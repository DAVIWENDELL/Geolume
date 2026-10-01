"""Celery app e task assíncrona do worker GeoLume."""

import os
from contextlib import ExitStack
from pathlib import Path

from celery import Celery

from geolume_worker.job import run_job
from geolume_worker.qgis_session import qgis_session
from db import init_db, update_job

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
OUTPUT_DIR = Path(os.getenv("GEOLUME_OUTPUT_DIR", "/saida"))

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
    update_job(job_id, "started")
    try:
        _ensure_process_qgis_session()
        with qgis_session():
            resultado = run_job(Path(input_path), OUTPUT_DIR, job_id=job_id, prancha=prancha)
    except Exception as exc:
        update_job(job_id, "failed", erro=str(exc))
        raise
    update_job(
        job_id,
        "completed",
        mapa_path=str(resultado.pdf_path),
        memorial_path=str(resultado.memorial_path),
        resultado_path=str(resultado.json_path),
    )
    return {
        "status": "completed",
        "job_id": resultado.job_id,
        "mapa": str(resultado.pdf_path),
        "memorial": str(resultado.memorial_path),
        "resultado": str(resultado.json_path),
        "fases_ms": resultado.phases_ms,
    }
