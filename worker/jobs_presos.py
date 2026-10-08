"""Regra de job preso. Sem banco nem fila: só funções puras sobre o registro do job.

Só responde com critério verificável no próprio registro; na dúvida, None (o job segue como está).
"""

from datetime import datetime, timedelta

# A API grava o task_id logo depois do delay: sem ele depois disso, o job nunca chegou ao Celery.
LIMITE_SEM_TAREFA = timedelta(minutes=10)
# Um job leva segundos; meia hora em started é processo morto, não job lento.
LIMITE_EXECUCAO = timedelta(minutes=30)


def _atraso(desde: object, agora: datetime) -> timedelta | None:
    if not isinstance(desde, datetime):
        return None
    if desde.tzinfo is None:
        raise ValueError("data do job sem fuso horário")
    return agora - desde


def motivo_job_preso(job: dict, agora: datetime) -> str | None:
    """"enfileiramento_perdido", "execucao_expirada" ou None.

    queued com task_id não é decidido aqui: a mensagem pode estar no Redis esperando o Celery
    voltar. started sem started_at (jobs antigos) também não: não há início registrado.
    """
    if agora.tzinfo is None:
        raise ValueError("agora sem fuso horário")
    status = job.get("status")
    if status == "queued" and not job.get("task_id"):
        atraso = _atraso(job.get("created_at"), agora)
        if atraso is not None and atraso > LIMITE_SEM_TAREFA:
            return "enfileiramento_perdido"
    if status == "started":
        atraso = _atraso(job.get("started_at"), agora)
        if atraso is not None and atraso > LIMITE_EXECUCAO:
            return "execucao_expirada"
    return None
