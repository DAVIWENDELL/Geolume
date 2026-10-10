"""Celery app e task assíncrona do worker GeoLume."""

import logging
import os
from contextlib import ExitStack
from pathlib import Path

from celery import Celery

from geolume_worker.job import run_job
from geolume_worker.prancha import caminho_logo
from geolume_worker.qgis_session import qgis_session
from db import concluir_job, falhar_job, iniciar_job, init_db
from integridade import conferir_artefatos

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
OUTPUT_DIR = Path(os.getenv("GEOLUME_OUTPUT_DIR", "/saida"))

logger = logging.getLogger(__name__)

celery = Celery("geolume", broker=BROKER_URL, backend=RESULT_BACKEND)
celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)
init_db()

# Uma sessão QGIS por processo do worker: encerrar e reabrir o QGIS no mesmo
# processo causa SIGSEGV na tarefa seguinte. Fica aberta até o processo sair.
_process_session = ExitStack()
_process_session_open = False


def _ensure_process_qgis_session() -> None:
    global _process_session_open
    if not _process_session_open:
        _process_session.enter_context(qgis_session())
        _process_session_open = True


@celery.task(name="geolume.process_job")
def process_job(input_path: str, job_id: str, prancha: dict | None = None) -> dict[str, object]:
    # prancha é opcional: tarefas enfileiradas antes dela chegam só com (input_path, job_id).
    try:
        # Dentro do try: banco fora aqui também limpa os uploads (o failed seguinte é recusado: job não está started).
        if not iniciar_job(job_id):
            # Job já decidido (ex.: recuperação o marcou failed) ou já iniciado: não processa nem apaga nada.
            logger.warning("job %s não estava em queued; início recusado, nada processado", job_id)
            return {"status": "inicio_recusado", "job_id": job_id}
        _ensure_process_qgis_session()
        with qgis_session():
            resultado = run_job(Path(input_path), OUTPUT_DIR, job_id=job_id, prancha=prancha)
    except Exception as exc:
        try:
            if not falhar_job(job_id, str(exc)):
                # A primeira causa gravada (ex.: job_expirado) vale; esta falha fica só no log.
                logger.warning("job %s não estava em started; falha recusada, causa anterior mantida", job_id)
        finally:
            Path(input_path).unlink(missing_ok=True)
            caminho_logo(OUTPUT_DIR, job_id).unlink(missing_ok=True)
        raise
    # Fora do try: banco fora daqui em diante não marca failed nem apaga nada (fica started, com os 3 artefatos).
    integridade = conferir_artefatos(OUTPUT_DIR, job_id)
    if not integridade["ok"]:
        # Arquivo gerado mas ilegível (ex.: PDF zerado): nunca completed. Código interno, a API mostra a
        # mensagem genérica. Artefatos e uploads ficam para diagnóstico.
        motivos = ", ".join(f"{nome}={motivo}" for nome, motivo in sorted(integridade["motivos"].items()))
        if not falhar_job(job_id, f"artefato_invalido: {motivos}"):
            logger.warning("job %s com artefato inválido não estava em started; causa anterior mantida", job_id)
        logger.warning("job %s com artefato inválido (%s); não concluído, artefatos mantidos", job_id, motivos)
        return {"status": "artefato_invalido", "job_id": job_id}
    if not concluir_job(job_id, str(resultado.pdf_path), str(resultado.memorial_path), str(resultado.json_path)):
        # Outro processo já decidiu o job (ex.: a recuperação o marcou failed): o banco é o status oficial.
        logger.warning("job %s não estava em started; conclusão recusada e artefatos mantidos", job_id)
        return {"status": "conclusao_recusada", "job_id": job_id}
    return {
        "status": "completed",
        "job_id": resultado.job_id,
        "mapa": str(resultado.pdf_path),
        "memorial": str(resultado.memorial_path),
        "resultado": str(resultado.json_path),
        "fases_ms": resultado.phases_ms,
    }
