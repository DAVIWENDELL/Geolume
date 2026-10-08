"""Recuperação de um job expirado. Sem scheduler nem endpoint: quem chama escolhe o job.

Só marca failed com critério verificável (`motivo_job_preso`) e se ganhar o UPDATE condicional;
só quem ganhou limpa os uploads. Nunca apaga artefatos nem declara completed.
"""

import logging
from datetime import datetime
from pathlib import Path

from psycopg2.extras import RealDictCursor

import db
from geolume_worker.prancha import JOB_ID, caminho_logo
from jobs_presos import LIMITE_EXECUCAO, LIMITE_SEM_TAREFA, motivo_job_preso

logger = logging.getLogger(__name__)

ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")
# Não é código de validação: o cliente continua vendo a mensagem genérica de falha.
ERROS = {
    "enfileiramento_perdido": "job_expirado: O job não chegou à fila de processamento.",
    "execucao_expirada": "job_expirado: O processamento não terminou dentro do tempo limite.",
}


def ler_job(job_id: str) -> dict[str, object] | None:
    """Leitura pelo id sem filtro de usuário. Fica aqui, e não em db.py, para a API nunca ter consulta global."""
    with db.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _artefatos_completos(output_dir: Path, job_id: str) -> bool:
    return all((Path(output_dir) / job_id / nome).is_file() for nome in ARTEFATOS)


def _limpar_uploads(job: dict, output_dir: Path) -> None:
    """Só o GeoJSON `inputs/{job_id}-*` e a logo do próprio job; erro de disco não desfaz a falha."""
    job_id = str(job["id"])
    alvos = []
    raw = job.get("input_path")
    if isinstance(raw, str) and raw:
        entrada = Path(raw)
        inputs = (Path(output_dir) / "inputs").resolve()
        if entrada.parent.resolve() == inputs and entrada.name.startswith(f"{job_id}-"):
            alvos.append(entrada)
    if JOB_ID.fullmatch(job_id):
        alvos.append(caminho_logo(output_dir, job_id))
    for alvo in alvos:
        try:
            alvo.unlink(missing_ok=True)
        except OSError:
            logger.warning("upload do job %s não foi removido", job_id)


def recuperar_job_expirado(job_id: str, agora: datetime, output_dir: Path) -> str:
    """"nao_encontrado", "nao_expirado", "artefatos_presentes", "outro_processo" ou "marcado_failed"."""
    if agora.tzinfo is None:
        raise ValueError("agora sem fuso horário")
    job = ler_job(job_id)
    if job is None:
        return "nao_encontrado"
    motivo = motivo_job_preso(job, agora)
    if motivo is None:
        return "nao_expirado"
    if motivo == "execucao_expirada":
        if _artefatos_completos(output_dir, job_id):
            return "artefatos_presentes"  # revisão manual: completed só com integridade confirmada
        marco, limite = job["started_at"], LIMITE_EXECUCAO
    else:
        marco, limite = job["created_at"], LIMITE_SEM_TAREFA
    if not db.marcar_job_expirado(job_id, job["status"], marco, agora - limite, ERROS[motivo]):
        return "outro_processo"
    _limpar_uploads(job, output_dir)
    return "marcado_failed"
