"""UPDATE condicional contra o PostgreSQL real (contêiner api): GEOLUME_DB_INTEGRATION=1.

Roda num schema temporário próprio (itest_*), com uma cópia vazia da estrutura de jobs, apagado
no fim: nunca lê nem escreve em public.jobs.
"""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if os.environ.get("GEOLUME_DB_INTEGRATION") != "1":
    pytest.skip("só com GEOLUME_DB_INTEGRATION=1 e banco real", allow_module_level=True)

import psycopg2  # noqa: E402

import db  # noqa: E402
import recuperacao  # noqa: E402

AGORA = datetime.now(timezone.utc)
INICIO = AGORA - timedelta(hours=1)
LIMITE = AGORA - timedelta(minutes=30)
ERRO = "job_expirado: teste"


@pytest.fixture
def jobs_isolados(monkeypatch):
    schema = f"itest_{uuid.uuid4().hex[:12]}"
    db.init_db(retries=5)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"CREATE TABLE {schema}.jobs (LIKE public.jobs INCLUDING DEFAULTS)")
    monkeypatch.setattr(db, "connect", lambda: psycopg2.connect(
        db.DATABASE_URL, connect_timeout=5, options=f"-c search_path={schema}"))
    yield schema
    monkeypatch.undo()
    assert schema.startswith("itest_")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA {schema} CASCADE")


def _inserir(status="started", task_id="t-1", started_at=INICIO, created_at=None, **extra):
    job_id = uuid.uuid4().hex
    campos = {"id": job_id, "task_id": task_id, "status": status, "input_filename": "lote.geojson",
              "created_at": created_at or AGORA - timedelta(hours=2), "started_at": started_at, **extra}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO jobs ({', '.join(campos)}) VALUES ({', '.join(['%s'] * len(campos))})",
                    tuple(campos.values()))
    return job_id


def test_o_schema_isolado_nao_e_o_public(jobs_isolados):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_schema()")
        assert cur.fetchone()[0] == jobs_isolados
        cur.execute("SELECT count(*) FROM jobs")
        assert cur.fetchone()[0] == 0


def test_primeiro_adquire_e_o_segundo_nao(jobs_isolados):
    job_id = _inserir()
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is True
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is False
    job = recuperacao.ler_job(job_id)
    assert job["status"] == "failed" and job["erro"] == ERRO and job["completed_at"] is not None


def test_processos_simultaneos_so_um_adquire(jobs_isolados):
    job_id = _inserir()
    barreira = threading.Barrier(8)
    resultados = []

    def tentar():
        barreira.wait()
        resultados.append(db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO))

    threads = [threading.Thread(target=tentar) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resultados.count(True) == 1


def test_status_alterado_depois_da_leitura_nao_e_sobrescrito(jobs_isolados):
    job_id = _inserir()
    db.update_job(job_id, "completed", mapa_path="/saida/x/mapa.pdf")  # o Celery terminou nesse meio-tempo
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is False
    job = recuperacao.ler_job(job_id)
    assert job["status"] == "completed" and job["erro"] is None and job["mapa_path"] == "/saida/x/mapa.pdf"


def test_started_at_alterado_depois_da_leitura_nao_e_sobrescrito(jobs_isolados):
    job_id = _inserir()
    db.update_job(job_id, "started")  # reentregue: novo início
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is False
    assert recuperacao.ler_job(job_id)["status"] == "started"


def test_job_que_deixou_de_estar_expirado_nao_e_marcado(jobs_isolados):
    recente = AGORA - timedelta(minutes=5)
    job_id = _inserir(started_at=recente)
    assert db.marcar_job_expirado(job_id, "started", recente, LIMITE, ERRO) is False
    assert recuperacao.ler_job(job_id)["status"] == "started"


def test_queued_que_ganhou_tarefa_nao_e_marcado(jobs_isolados):
    criado = AGORA - timedelta(hours=1)
    job_id = _inserir(status="queued", task_id=None, started_at=None, created_at=criado)
    db.set_task_id(job_id, "t-tardia")
    assert db.marcar_job_expirado(job_id, "queued", criado, AGORA - timedelta(minutes=10), ERRO) is False
    assert recuperacao.ler_job(job_id)["status"] == "queued"


def test_queued_sem_tarefa_expirado_e_marcado(jobs_isolados):
    criado = AGORA - timedelta(hours=1)
    job_id = _inserir(status="queued", task_id=None, started_at=None, created_at=criado)
    assert db.marcar_job_expirado(job_id, "queued", criado, AGORA - timedelta(minutes=10), ERRO) is True
    assert recuperacao.ler_job(job_id)["status"] == "failed"


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_job_terminado_nunca_e_recuperado(jobs_isolados, status):
    job_id = _inserir(status=status, erro="anterior" if status == "failed" else None)
    antes = recuperacao.ler_job(job_id)
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is False
    assert recuperacao.ler_job(job_id) == antes


def test_recuperacao_completa_contra_o_banco(jobs_isolados, tmp_path):
    (tmp_path / "inputs").mkdir()
    job_id = _inserir()
    entrada = tmp_path / "inputs" / f"{job_id}-lote.geojson"
    entrada.write_text("{}")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET input_path = %s WHERE id = %s", (str(entrada), job_id))

    assert recuperacao.recuperar_job_expirado(job_id, AGORA, tmp_path) == "marcado_failed"
    assert recuperacao.recuperar_job_expirado(job_id, AGORA, tmp_path) == "nao_expirado"
    assert recuperacao.ler_job(job_id)["status"] == "failed"
    assert not entrada.exists()


# ---- Conclusão condicional do Celery ------------------------------------------------

CAMINHOS = ("/saida/x/mapa.pdf", "/saida/x/memorial.pdf", "/saida/x/resultado.json")


def test_started_e_concluido(jobs_isolados):
    job_id = _inserir()
    assert db.concluir_job(job_id, *CAMINHOS) is True
    job = recuperacao.ler_job(job_id)
    assert job["status"] == "completed" and job["completed_at"] is not None
    assert (job["mapa_path"], job["memorial_path"], job["resultado_path"]) == CAMINHOS


def test_celery_atrasado_nao_ressuscita_job_marcado_failed(jobs_isolados):
    job_id = _inserir()
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is True  # recuperação venceu
    antes = recuperacao.ler_job(job_id)
    assert db.concluir_job(job_id, *CAMINHOS) is False
    assert recuperacao.ler_job(job_id) == antes  # continua failed, sem caminhos


def test_concluir_duas_vezes_e_idempotente(jobs_isolados):
    job_id = _inserir()
    assert db.concluir_job(job_id, *CAMINHOS) is True
    antes = recuperacao.ler_job(job_id)
    assert db.concluir_job(job_id, "/outro/mapa.pdf", "/outro/memorial.pdf", "/outro/resultado.json") is False
    assert recuperacao.ler_job(job_id) == antes


@pytest.mark.parametrize("status", ["queued", "inesperado"])
def test_estado_inesperado_nao_e_promovido(jobs_isolados, status):
    job_id = _inserir(status=status, started_at=None)
    antes = recuperacao.ler_job(job_id)
    assert db.concluir_job(job_id, *CAMINHOS) is False
    assert recuperacao.ler_job(job_id) == antes


def test_conclusao_so_afeta_o_proprio_job(jobs_isolados):
    alvo, vizinho = _inserir(), _inserir()
    antes = recuperacao.ler_job(vizinho)
    assert db.concluir_job(alvo, *CAMINHOS) is True
    assert recuperacao.ler_job(vizinho) == antes


def test_recuperacao_e_conclusao_simultaneas_so_uma_vence(jobs_isolados):
    for _ in range(5):
        job_id = _inserir()
        barreira = threading.Barrier(2)
        resultados = {}

        def recuperar():
            barreira.wait()
            resultados["recuperou"] = db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO)

        def concluir():
            barreira.wait()
            resultados["concluiu"] = db.concluir_job(job_id, *CAMINHOS)

        threads = [threading.Thread(target=recuperar), threading.Thread(target=concluir)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(resultados.values()) == [False, True]
        status = recuperacao.ler_job(job_id)["status"]
        assert status == ("failed" if resultados["recuperou"] else "completed")


# ---- started e failed condicionais do Celery -------------------------------------

ERRO_CELERY = "sem_feicoes: O GeoJSON não contém feições."


def test_queued_e_iniciado(jobs_isolados):
    job_id = _inserir(status="queued", started_at=None)
    assert db.iniciar_job(job_id) is True
    job = recuperacao.ler_job(job_id)
    assert job["status"] == "started" and job["started_at"] is not None


def test_started_e_falhado(jobs_isolados):
    job_id = _inserir()
    assert db.falhar_job(job_id, ERRO_CELERY) is True
    job = recuperacao.ler_job(job_id)
    assert job["status"] == "failed" and job["erro"] == ERRO_CELERY and job["completed_at"] is not None


def test_tarefa_atrasada_nao_ressuscita_job_recuperado(jobs_isolados):
    criado = AGORA - timedelta(hours=1)
    job_id = _inserir(status="queued", task_id=None, started_at=None, created_at=criado)
    assert db.marcar_job_expirado(job_id, "queued", criado, AGORA - timedelta(minutes=10), ERRO) is True
    antes = recuperacao.ler_job(job_id)
    assert db.iniciar_job(job_id) is False
    assert recuperacao.ler_job(job_id) == antes  # continua failed, com job_expirado e sem started_at


def test_falha_tardia_preserva_job_expirado(jobs_isolados):
    job_id = _inserir()
    assert db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO) is True
    antes = recuperacao.ler_job(job_id)
    assert db.falhar_job(job_id, ERRO_CELERY) is False
    assert recuperacao.ler_job(job_id) == antes and antes["erro"] == ERRO  # a primeira causa fica


def test_segunda_falha_nao_troca_a_causa(jobs_isolados):
    job_id = _inserir()
    assert db.falhar_job(job_id, ERRO_CELERY) is True
    antes = recuperacao.ler_job(job_id)
    assert db.falhar_job(job_id, "outra: causa") is False
    assert recuperacao.ler_job(job_id) == antes


@pytest.mark.parametrize("status", ["failed", "completed"])
def test_job_terminado_nao_e_iniciado_nem_falhado(jobs_isolados, status):
    job_id = _inserir(status=status, erro="anterior" if status == "failed" else None)
    antes = recuperacao.ler_job(job_id)
    assert db.iniciar_job(job_id) is False
    assert db.falhar_job(job_id, ERRO_CELERY) is False
    assert recuperacao.ler_job(job_id) == antes


def test_job_ja_started_nao_e_reiniciado(jobs_isolados):
    job_id = _inserir()  # mensagem reentregue: não reabre o início nem roda de novo
    antes = recuperacao.ler_job(job_id)
    assert db.iniciar_job(job_id) is False
    assert recuperacao.ler_job(job_id) == antes


def test_job_queued_nao_e_falhado_pelo_celery(jobs_isolados):
    # Banco caiu ao marcar started: o failed seguinte não pula de queued para failed.
    job_id = _inserir(status="queued", started_at=None)
    antes = recuperacao.ler_job(job_id)
    assert db.falhar_job(job_id, ERRO_CELERY) is False
    assert recuperacao.ler_job(job_id) == antes


def test_inicio_e_falha_so_afetam_o_proprio_job(jobs_isolados):
    alvo, vizinho_queued, vizinho_started = (_inserir(status="queued", started_at=None),
                                             _inserir(status="queued", started_at=None), _inserir())
    antes = [recuperacao.ler_job(v) for v in (vizinho_queued, vizinho_started)]
    assert db.iniciar_job(alvo) is True
    assert db.falhar_job(alvo, ERRO_CELERY) is True
    assert [recuperacao.ler_job(v) for v in (vizinho_queued, vizinho_started)] == antes


def _corrida(*funcoes):
    barreira = threading.Barrier(len(funcoes))
    resultados = [None] * len(funcoes)

    def rodar(i, funcao):
        barreira.wait()
        resultados[i] = funcao()

    threads = [threading.Thread(target=rodar, args=(i, f)) for i, f in enumerate(funcoes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return resultados


def test_entregas_simultaneas_so_uma_inicia(jobs_isolados):
    job_id = _inserir(status="queued", started_at=None)
    assert _corrida(*[lambda: db.iniciar_job(job_id)] * 8).count(True) == 1


def test_recuperacao_e_inicio_simultaneos_so_um_vence(jobs_isolados):
    criado = AGORA - timedelta(hours=1)
    for _ in range(5):
        job_id = _inserir(status="queued", task_id=None, started_at=None, created_at=criado)
        recuperou, iniciou = _corrida(
            lambda: db.marcar_job_expirado(job_id, "queued", criado, AGORA - timedelta(minutes=10), ERRO),
            lambda: db.iniciar_job(job_id))
        assert sorted([recuperou, iniciou]) == [False, True]
        assert recuperacao.ler_job(job_id)["status"] == ("failed" if recuperou else "started")


def test_recuperacao_e_falha_simultaneas_preservam_a_causa_de_quem_venceu(jobs_isolados):
    for _ in range(5):
        job_id = _inserir()
        recuperou, falhou = _corrida(lambda: db.marcar_job_expirado(job_id, "started", INICIO, LIMITE, ERRO),
                                     lambda: db.falhar_job(job_id, ERRO_CELERY))
        assert sorted([recuperou, falhou]) == [False, True]
        job = recuperacao.ler_job(job_id)
        assert job["status"] == "failed" and job["erro"] == (ERRO if recuperou else ERRO_CELERY)


# ---- failed da API ao falhar o enfileiramento --------------------------------------

ERRO_FILA = "Falha ao enfileirar o job."


def test_falha_ao_enfileirar_com_job_ainda_queued(jobs_isolados):
    job_id = _inserir(status="queued", task_id=None, started_at=None)
    assert db.falhar_enfileiramento(job_id, ERRO_FILA) is True
    job = recuperacao.ler_job(job_id)
    assert job["status"] == "failed" and job["erro"] == ERRO_FILA and job["completed_at"] is not None


@pytest.mark.parametrize("status", ["started", "completed", "failed"])
def test_falha_ao_enfileirar_nao_sobrescreve_quem_ja_decidiu(jobs_isolados, status):
    extra = {"erro": ERRO} if status == "failed" else {"mapa_path": CAMINHOS[0]} if status == "completed" else {}
    job_id = _inserir(status=status, **extra)
    antes = recuperacao.ler_job(job_id)
    assert db.falhar_enfileiramento(job_id, ERRO_FILA) is False
    assert recuperacao.ler_job(job_id) == antes


def test_falha_ao_enfileirar_so_afeta_o_proprio_job(jobs_isolados):
    alvo = _inserir(status="queued", task_id=None, started_at=None)
    vizinho = _inserir(status="queued", task_id=None, started_at=None)
    antes = recuperacao.ler_job(vizinho)
    assert db.falhar_enfileiramento(alvo, ERRO_FILA) is True
    assert recuperacao.ler_job(vizinho) == antes


def test_api_e_celery_simultaneos_so_um_vence(jobs_isolados):
    # delay() já entregou a mensagem e set_task_id falhou: a API tenta failed enquanto o Celery tenta started.
    for _ in range(5):
        job_id = _inserir(status="queued", task_id=None, started_at=None)
        falhou, iniciou = _corrida(lambda: db.falhar_enfileiramento(job_id, ERRO_FILA),
                                   lambda: db.iniciar_job(job_id))
        assert sorted([falhou, iniciou]) == [False, True]
        job = recuperacao.ler_job(job_id)
        assert (job["status"], job["erro"]) == (("failed", ERRO_FILA) if falhou else ("started", None))


def test_falhas_simultaneas_ao_enfileirar_so_uma_grava(jobs_isolados):
    job_id = _inserir(status="queued", task_id=None, started_at=None)
    assert _corrida(*[lambda: db.falhar_enfileiramento(job_id, ERRO_FILA)] * 8).count(True) == 1
