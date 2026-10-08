"""Regra pura de job preso: só decide com critério verificável no próprio registro."""

from datetime import datetime, timedelta, timezone

import pytest

from jobs_presos import LIMITE_EXECUCAO, LIMITE_SEM_TAREFA, motivo_job_preso

AGORA = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _job(status, *, criado_ha=timedelta(hours=1), task_id="t-1", iniciado_ha=None, **extra):
    job = {"id": "j-1", "status": status, "task_id": task_id, "created_at": AGORA - criado_ha, "started_at": None}
    if iniciado_ha is not None:
        job["started_at"] = AGORA - iniciado_ha
    return {**job, **extra}


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_job_terminado_nunca_esta_preso(status):
    antigo = _job(status, criado_ha=timedelta(days=30), task_id=None, iniciado_ha=timedelta(days=30))
    assert motivo_job_preso(antigo, AGORA) is None


# ---- queued -----------------------------------------------------------------


def test_queued_sem_tarefa_alem_do_limite_perdeu_o_enfileiramento():
    # A API grava o task_id logo depois do delay; sem ele, o job nunca chegou ao Celery.
    job = _job("queued", task_id=None, criado_ha=LIMITE_SEM_TAREFA + timedelta(seconds=1))
    assert motivo_job_preso(job, AGORA) == "enfileiramento_perdido"


def test_queued_sem_tarefa_dentro_do_limite_ainda_pode_estar_sendo_enfileirado():
    assert motivo_job_preso(_job("queued", task_id=None, criado_ha=LIMITE_SEM_TAREFA), AGORA) is None


def test_queued_com_tarefa_nao_e_decidido_pelo_banco_mesmo_muito_antigo():
    # A mensagem pode estar no Redis esperando o Celery voltar (já houve job concluído 19 h depois).
    assert motivo_job_preso(_job("queued", criado_ha=timedelta(days=10)), AGORA) is None


def test_task_id_vazio_conta_como_sem_tarefa():
    job = _job("queued", task_id="", criado_ha=LIMITE_SEM_TAREFA + timedelta(minutes=1))
    assert motivo_job_preso(job, AGORA) == "enfileiramento_perdido"


# ---- started ----------------------------------------------------------------


def test_started_alem_do_limite_de_execucao_expirou():
    job = _job("started", criado_ha=timedelta(days=1), iniciado_ha=LIMITE_EXECUCAO + timedelta(seconds=1))
    assert motivo_job_preso(job, AGORA) == "execucao_expirada"


def test_started_dentro_do_limite_continua_ativo():
    assert motivo_job_preso(_job("started", iniciado_ha=LIMITE_EXECUCAO), AGORA) is None


def test_started_conta_desde_o_inicio_e_nao_desde_a_criacao():
    # Ficou 19 h na fila e começou agora: não está preso.
    job = _job("started", criado_ha=timedelta(hours=19), iniciado_ha=timedelta(seconds=5))
    assert motivo_job_preso(job, AGORA) is None


def test_started_sem_started_at_nao_e_decidido():
    # Jobs anteriores à coluna: sem início registrado, não há critério verificável.
    assert motivo_job_preso(_job("started", criado_ha=timedelta(days=10)), AGORA) is None


# ---- Relógio e dados inesperados --------------------------------------------


def test_data_no_futuro_nao_conta_como_atraso():
    job = _job("started", iniciado_ha=-timedelta(hours=2))
    assert motivo_job_preso(job, AGORA) is None
    assert motivo_job_preso(_job("queued", task_id=None, criado_ha=-timedelta(hours=2)), AGORA) is None


def test_agora_sem_fuso_e_recusado():
    with pytest.raises(ValueError):
        motivo_job_preso(_job("started", iniciado_ha=timedelta(hours=2)), datetime(2026, 10, 8, 12, 0))


def test_data_do_job_sem_fuso_e_recusada():
    job = _job("started", started_at=datetime(2026, 10, 8, 10, 0))
    with pytest.raises(ValueError):
        motivo_job_preso(job, AGORA)


@pytest.mark.parametrize("status", ["desconhecido", None, ""])
def test_status_desconhecido_nao_e_decidido(status):
    assert motivo_job_preso(_job(status, task_id=None, criado_ha=timedelta(days=10)), AGORA) is None


def test_regra_nao_altera_o_registro():
    job = _job("started", iniciado_ha=LIMITE_EXECUCAO * 2)
    copia = dict(job)
    motivo_job_preso(job, AGORA)
    assert job == copia


def test_limites_sao_folgados_para_o_processamento_atual():
    # Jobs reais levam segundos; o limite não pode pegar um job lento, porém vivo.
    assert LIMITE_EXECUCAO >= timedelta(minutes=30)
    assert LIMITE_SEM_TAREFA >= timedelta(minutes=5)
