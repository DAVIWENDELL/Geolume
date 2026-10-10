"""UPDATE condicional contra o PostgreSQL real (contêiner api): GEOLUME_DB_INTEGRATION=1.

Roda num schema temporário próprio (itest_*), com uma cópia vazia da estrutura de jobs, apagado
no fim: nunca lê nem escreve em public.jobs.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


# ---- Diagnóstico somente leitura ---------------------------------------------------


def _tabela():
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT md5(string_agg(t::text, ',' ORDER BY id)), count(*) FROM jobs t")
        return cur.fetchone()


def test_diagnostico_classifica_sem_alterar_o_banco(jobs_isolados, tmp_path):
    import diagnostico_jobs

    (tmp_path / "inputs").mkdir()
    perdido = _inserir(status="queued", task_id=None, started_at=None, created_at=AGORA - timedelta(hours=1))
    com_tarefa = _inserir(status="queued", started_at=None, created_at=AGORA - timedelta(hours=1))
    expirado = _inserir()
    concluido = _inserir(status="completed")
    antes = _tabela()

    rel = diagnostico_jobs.relatorio(AGORA, tmp_path)

    assert _tabela() == antes
    categorias = {j["id"]: j["categoria"] for j in rel["jobs"]}
    assert categorias == {perdido: "candidato_recuperacao", com_tarefa: "nao_expirado",
                          expirado: "candidato_recuperacao"}  # sem filtro: só queued e started
    rel = diagnostico_jobs.relatorio(AGORA, tmp_path, job_ids=[concluido, "nada"])
    assert [j["categoria"] for j in sorted(rel["jobs"], key=lambda j: j["id"] == "nada")] == [
        "nao_recuperavel", "nao_encontrado"]
    assert _tabela() == antes


def test_diagnostico_por_tenant_nao_le_outro_tenant(jobs_isolados, tmp_path):
    import diagnostico_jobs

    nosso, alheio = _inserir(tenant_id="demo"), _inserir(tenant_id="outro")
    rel = diagnostico_jobs.relatorio(AGORA, tmp_path, tenant="demo")
    assert [j["id"] for j in rel["jobs"]] == [nosso]
    rel = diagnostico_jobs.relatorio(AGORA, tmp_path, job_ids=[alheio], tenant="demo")
    assert rel["jobs"] == [{"id": alheio, "categoria": "nao_encontrado"}]


def test_sessao_do_diagnostico_recusa_escrita(jobs_isolados):
    import diagnostico_jobs

    job_id = _inserir(status="queued", started_at=None)
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        with diagnostico_jobs._conexao_leitura() as conn, conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", (job_id,))
    assert recuperacao.ler_job(job_id)["status"] == "queued"


# ---- CLI manual de recuperação -------------------------------------------------------


def _cli(tmp_path, *args):
    import contextlib
    import io
    import json

    import recuperar_job

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        codigo = recuperar_job.main(["--saida", str(tmp_path), *args])
    return codigo, json.loads(buffer.getvalue())


def test_cli_sem_apply_nao_altera_o_banco(jobs_isolados, tmp_path):
    job_id = _inserir(status="queued", task_id=None, started_at=None)
    antes = _tabela()
    codigo, r = _cli(tmp_path, "--job-id", job_id)
    assert (codigo, r["modo"]) == (0, "diagnostico")
    assert r["diagnostico"]["jobs"][0]["categoria"] == "candidato_recuperacao"
    assert _tabela() == antes


def test_cli_confirmacao_errada_nao_altera_o_banco(jobs_isolados, tmp_path):
    job_id = _inserir(status="queued", task_id=None, started_at=None)
    antes = _tabela()
    codigo, r = _cli(tmp_path, "--apply", "--job-id", job_id, "--confirm-job-id", "outro")
    assert (codigo, r["resultado"]) == (2, "confirmacao_invalida")
    assert _tabela() == antes


def test_cli_recupera_so_o_job_pedido(jobs_isolados, tmp_path):
    alvo = _inserir(status="queued", task_id=None, started_at=None)
    vizinho = _inserir(status="queued", task_id=None, started_at=None)
    antes = recuperacao.ler_job(vizinho)
    codigo, r = _cli(tmp_path, "--apply", "--job-id", alvo, "--confirm-job-id", alvo)
    assert (codigo, r["resultado"]) == (0, "marcado_failed")
    job = recuperacao.ler_job(alvo)
    assert job["status"] == "failed" and job["erro"].startswith("job_expirado: ")
    assert recuperacao.ler_job(vizinho) == antes


def test_cli_tenant_incorreto_nao_altera(jobs_isolados, tmp_path):
    job_id = _inserir(status="queued", task_id=None, started_at=None, tenant_id="outro")
    antes = _tabela()
    codigo, r = _cli(tmp_path, "--apply", "--job-id", job_id, "--confirm-job-id", job_id, "--tenant", "demo")
    assert (codigo, r["resultado"]) == (1, "nao_encontrado")
    assert _tabela() == antes


def test_cli_aplicacoes_simultaneas_so_uma_marca(jobs_isolados, tmp_path):
    import recuperar_job

    for _ in range(3):
        job_id = _inserir()
        args = ["--saida", str(tmp_path), "--apply", "--job-id", job_id, "--confirm-job-id", job_id]
        resultados = _corrida(*[lambda: recuperar_job.executar(recuperar_job._parser().parse_args(args))[1]
                                ["resultado"]] * 4)
        assert resultados.count("marcado_failed") == 1
        assert set(resultados) <= {"marcado_failed", "outro_processo", "nao_expirado"}
        assert recuperacao.ler_job(job_id)["status"] == "failed"


def test_plano_de_retencao_classifica_sem_alterar_o_banco(jobs_isolados, tmp_path):
    import retencao

    (tmp_path / "inputs").mkdir()
    entrada = tmp_path / "inputs" / "x-lote.geojson"
    falho = _inserir(status="failed", completed_at=AGORA - timedelta(days=31))
    entrada = entrada.with_name(f"{falho}-lote.geojson")
    entrada.write_text("{}")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET input_path = %s WHERE id = %s", (str(entrada), falho))
    recente = _inserir(status="failed", completed_at=AGORA - timedelta(days=1))
    concluido = _inserir(status="completed", completed_at=AGORA - timedelta(days=200))
    rodando = _inserir()
    na_fila = _inserir(status="queued", started_at=None)
    antes = _tabela()

    plano = retencao.plano(AGORA, tmp_path)

    assert _tabela() == antes
    assert entrada.is_file()
    decisoes = {j["id"]: j["decisao"] for j in plano["jobs"]}
    assert decisoes == {falho: "candidato_limpeza", recente: "manter", concluido: "candidato_retencao",
                        rodando: "manter", na_fila: "manter"}
    assert plano["arquivos_candidatos"] == 1


def test_plano_de_retencao_por_tenant_nao_le_outro_tenant(jobs_isolados, tmp_path):
    import retencao

    nosso = _inserir(status="failed", completed_at=AGORA - timedelta(days=31), tenant_id="demo")
    alheio = _inserir(status="failed", completed_at=AGORA - timedelta(days=31), tenant_id="outro")
    assert [j["id"] for j in retencao.plano(AGORA, tmp_path, tenant="demo")["jobs"]] == [nosso]
    plano = retencao.plano(AGORA, tmp_path, job_ids=[alheio], tenant="demo")
    assert plano["jobs"] == [{"id": alheio, "decisao": "nao_encontrado"}]


def test_sessao_do_plano_de_retencao_recusa_escrita(jobs_isolados):
    import retencao

    job_id = _inserir(status="failed", completed_at=AGORA - timedelta(days=31))
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        with retencao._conexao_leitura() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_plano_de_retencao_consulta_mista_soma_o_total(jobs_isolados, tmp_path):
    import retencao

    nosso = _inserir(status="failed", completed_at=AGORA - timedelta(days=31), tenant_id="demo")
    alheio = _inserir(status="failed", completed_at=AGORA - timedelta(days=31), tenant_id="outro")
    antes = _tabela()
    plano = retencao.plano(AGORA, tmp_path, job_ids=[nosso, "inexistente", alheio, nosso], tenant="demo")
    assert _tabela() == antes
    assert {j["id"]: j["decisao"] for j in plano["jobs"]} == {
        nosso: "candidato_limpeza", "inexistente": "nao_encontrado", alheio: "nao_encontrado"}
    assert sum(plano["resumo"].values()) == plano["total"] == len(plano["jobs"]) == 3


# ---- Diagnóstico da fila: Redis real numa DB isolada (15), nunca a do broker real ----------------

@pytest.fixture
def redis_isolado(monkeypatch):
    """(app Celery, cliente redis-py, chaves criadas), na DB 15 travada e conferida (tests/broker_isolado.py).

    O teste registra cada chave antes de criá-la; no fim só elas são apagadas, pelo nome. Nada é varrido para
    apagar. DB ocupada, suja ou com sobra não registrada falha o teste (nunca skip): a integração não some calada.
    """
    from contextlib import ExitStack

    import redis

    import diagnostico_fila
    from broker_isolado import BrokerNaoIsolado, app_isolado, cliente_isolado, db_exclusiva, isolar_diagnostico

    real = redis.Redis.from_url(diagnostico_fila.BROKER_URL)  # só leitura: conferir que nada chegou ao real
    broker_real = {k: real.dump(k) for k in real.scan_iter()}
    cliente, _ = cliente_isolado()
    uso = db_exclusiva(cliente)
    try:
        criadas = uso.__enter__()
    except BrokerNaoIsolado as erro:
        pytest.fail(f"Redis isolado inutilizável, nada foi publicado nem apagado: {erro}")
    with ExitStack() as pilha:
        pilha.push(uso)  # limpa e confere sobras ainda com a trava, mesmo se algo abaixo falhar
        app, _ = app_isolado(monkeypatch)  # falha aqui, antes de qualquer publicação, se não for a DB 15
        isolar_diagnostico(monkeypatch, diagnostico_fila)
        yield app, cliente, criadas
    assert {k: real.dump(k) for k in real.scan_iter()} == broker_real


def test_diagnostico_da_fila_com_broker_e_banco_reais(jobs_isolados, redis_isolado):
    import json

    import diagnostico_fila

    app, redis_isolado, criadas = redis_isolado
    na_fila, reservado, terminado, ausente = (str(uuid.uuid4()) for _ in range(4))
    criadas.update({"celery", "_kombu.binding.celery"})  # registradas antes de publicar
    for task_id in (na_fila, reservado):
        app.send_task("geolume.process_job", args=["/nada", "x"], task_id=task_id)
    # Simula a reserva do worker: a mensagem real sai da fila e vai para `unacked`, como no kombu.
    mensagem = next(m for m in redis_isolado.lrange("celery", 0, -1) if reservado in m.decode())
    redis_isolado.lrem("celery", 1, mensagem)
    criadas.add("unacked")  # registrada antes de criar
    redis_isolado.hset("unacked", "tag-1", json.dumps([json.loads(mensagem), "", "celery"]))
    criadas.add(f"celery-task-meta-{terminado}")
    app.backend.store_result(terminado, None, "FAILURE")
    ids = {nome: _inserir(status="queued", task_id=task_id, started_at=None, created_at=AGORA - timedelta(hours=1),
                          tenant_id="demo")
           for nome, task_id in [("na_fila", na_fila), ("reservado", reservado), ("terminado", terminado),
                                 ("ausente", ausente), ("invalido", "t-1"), ("sem_tarefa", None)]}
    ids["outro_tenant"] = _inserir(status="queued", task_id=na_fila, started_at=None, tenant_id="outro")
    antes_banco, antes_redis = _tabela(), {k: redis_isolado.dump(k) for k in redis_isolado.scan_iter()}

    rel = diagnostico_fila.relatorio(AGORA, job_ids=[*ids.values(), "inexistente"], tenant="demo")

    assert _tabela() == antes_banco
    assert {k: redis_isolado.dump(k) for k in redis_isolado.scan_iter()} == antes_redis
    por_id = {j["id"]: (j["categoria"], j.get("motivo")) for j in rel["jobs"]}
    assert por_id == {
        ids["na_fila"]: ("fila_confirmada", "na_fila"),
        ids["reservado"]: ("fila_confirmada", "reservada_pelo_worker"),
        ids["terminado"]: ("task_nao_localizada", "ausente_da_fila_e_das_reservas"),
        ids["ausente"]: ("task_nao_localizada", "ausente_da_fila_e_das_reservas"),
        ids["invalido"]: ("task_id_invalido", "formato_inesperado"),
        ids["sem_tarefa"]: ("queued_sem_task_id", "enfileiramento_perdido"),
        ids["outro_tenant"]: ("nao_encontrado", None),
        "inexistente": ("nao_encontrado", None),
    }
    terminado_job = next(j for j in rel["jobs"] if j["id"] == ids["terminado"])
    assert terminado_job["broker"]["estado_resultado"] == "FAILURE"
    assert sum(rel["resumo"].values()) == rel["total"] == 8


def test_diagnostico_da_fila_com_redis_fora_do_ar(jobs_isolados, monkeypatch):
    import diagnostico_fila

    monkeypatch.setattr(diagnostico_fila, "BROKER_URL", "redis://127.0.0.1:1/15")
    monkeypatch.setattr(diagnostico_fila, "RESULT_BACKEND", "redis://127.0.0.1:1/15")
    job_id = _inserir(status="queued", started_at=None, task_id=str(uuid.uuid4()))
    antes = _tabela()
    rel = diagnostico_fila.relatorio(AGORA, job_ids=[job_id])
    assert _tabela() == antes
    assert rel["broker_disponivel"] is False
    assert (rel["jobs"][0]["categoria"], rel["jobs"][0]["motivo"]) == ("nao_verificavel", "broker_indisponivel")


def test_db_isolada_com_chave_preexistente_falha_sem_apagar_nada():
    """Redis real: chave gravada antes do uso exclusivo. A trava é adquirida (a mensagem "não está vazia" só sai
    depois dela; trava ocupada daria "em uso" e falharia o match), a DB é recusada e a chave fica intacta.
    """
    from broker_isolado import TRAVA, BrokerNaoIsolado, cliente_isolado, db_exclusiva

    cliente, _ = cliente_isolado()
    externa = f"itest-externa-{uuid.uuid4().hex}"
    assert cliente.set(externa, b"preexistente", nx=True)
    try:
        with pytest.raises(BrokerNaoIsolado, match="não está vazia"):
            with db_exclusiva(cliente):
                pytest.fail("não pode entrar com a DB ocupada")
        assert cliente.get(externa) == b"preexistente"
        assert not cliente.exists(TRAVA)  # liberada por quem a adquiriu
    finally:
        cliente.delete(externa)  # nome único criado por este teste, apagado pelo nome


def test_trava_perdida_no_meio_falha_sem_apagar_nada():
    """Redis real: a validade da trava vence no meio do uso e outro processo trava a DB. Sem posse, nem a chave
    registrada é apagada (a DB já não é exclusiva) e o uso falha; a trava do outro fica intacta.
    """
    import time

    from broker_isolado import TRAVA, VALIDADE_TRAVA, BrokerNaoIsolado, cliente_isolado, db_exclusiva

    cliente, _ = cliente_isolado()
    outro, _ = cliente_isolado()  # como outro processo: conexão própria
    outra_trava = outro.lock(TRAVA, timeout=VALIDADE_TRAVA, blocking=False)
    propria = f"itest-propria-{uuid.uuid4().hex}"
    try:
        with pytest.raises(BrokerNaoIsolado, match="validade"):
            with db_exclusiva(cliente) as criadas:
                criadas.add(propria)
                cliente.set(propria, b"do teste")
                outro.pexpire(TRAVA, 1)  # a validade vence agora, sem esperar 120 s
                while outro.exists(TRAVA):
                    time.sleep(0.01)
                assert outra_trava.acquire(blocking=False)
        assert cliente.get(propria) == b"do teste"
        assert outra_trava.owned()
    finally:
        if outra_trava.owned():
            outra_trava.release()  # só com o token do outro processo simulado
        cliente.delete(propria)  # nome único criado por este teste, apagado pelo nome


def test_db_isolada_preserva_chave_que_o_teste_nao_criou():
    """Redis real: outro cliente, com conexão própria e sem acesso a `criadas`, grava durante o uso exclusivo.

    Só a chave registrada sai; a do outro cliente fica e o uso falha. DB ocupada ou suja na entrada dá outra
    mensagem e também falha: nunca vira skip.
    """
    from broker_isolado import BrokerNaoIsolado, cliente_isolado, db_exclusiva

    cliente, _ = cliente_isolado()
    outro, _ = cliente_isolado()  # como outro processo: conexão própria, nunca registra em `criadas`
    assert outro.connection_pool is not cliente.connection_pool
    externa = f"itest-externa-{uuid.uuid4().hex}"
    try:
        with pytest.raises(BrokerNaoIsolado, match="1 chave"):
            with db_exclusiva(cliente) as criadas:
                criadas.add("celery")
                cliente.rpush("celery", b"do teste")
                outro.set(externa, b"de outro processo")
                assert criadas == {"celery"}
        assert outro.get(externa) == b"de outro processo"
        assert not cliente.exists("celery")
    finally:
        cliente.delete(externa)  # nome único criado por este teste, apagado pelo nome


# ---- Isolamento por tenant: SQL real (JOB_ACCESS) num schema itest_*, rotas HTTP da API --------------

T1, T2 = "itest-t1", "itest-t2"
DONO = {"user_id": "u-dono", "email": "dono@geolume.test", "tenant_id": T1, "role": "member"}
COLEGA = {"user_id": "u-colega", "email": "colega@geolume.test", "tenant_id": T1, "role": "member"}
ADMIN_T1 = {"user_id": "u-adm1", "email": "adm1@geolume.test", "tenant_id": T1, "role": "admin"}
MEMBRO_T2 = {"user_id": "u-dono", "email": "outro@geolume.test", "tenant_id": T2, "role": "member"}  # mesmo id
ADMIN_T2 = {"user_id": "u-adm2", "email": "adm2@geolume.test", "tenant_id": T2, "role": "admin"}
RECURSOS = ("", "/files/mapa", "/files/memorial", "/files/resultado", "/input", "/logo")


@pytest.fixture
def api_real(jobs_isolados, monkeypatch, tmp_path):
    """Rotas da API chamadas direto (como em test_api_authz) com o usuário já resolvido, sobre o SQL real de
    get_job_for/list_jobs_for no schema itest_*. Login e sessão têm testes próprios; httpx não está na imagem."""
    import importlib
    import sys

    monkeypatch.setattr(db, "init_db", lambda *args, **kwargs: None)
    for nome in ("api", "celery_app"):
        sys.modules.pop(nome, None)
    api = importlib.import_module("api")
    saida = tmp_path / "saida"
    (saida / "inputs").mkdir(parents=True)
    monkeypatch.setattr(api, "OUTPUT_DIR", saida)
    monkeypatch.setattr(api, "INPUTS_DIR", saida / "inputs")
    yield SimpleNamespace(api=api, saida=saida)
    for nome in ("api", "celery_app"):
        sys.modules.pop(nome, None)


def _chamar(api_real, user, task_id, recurso):
    """(status, texto) de GET /jobs/{task_id}{recurso}; corpo em streaming lido e descritor fechado."""
    import asyncio

    from fastapi import HTTPException

    api = api_real.api
    rotas = {"": lambda: api.job_status(task_id, user), "/input": lambda: api.job_input(task_id, user),
             "/logo": lambda: api.job_logo(task_id, user),
             **{f"/files/{tipo}": (lambda t=tipo: api.download_job_file(task_id, t, user))
                for tipo in ("mapa", "memorial", "resultado")}}
    try:
        resposta = rotas[recurso]()
    except HTTPException as exc:
        return exc.status_code, json.dumps(exc.detail)
    if isinstance(resposta, dict):
        return 200, json.dumps(resposta, default=str)

    async def ler():
        return b"".join([parte async for parte in resposta.body_iterator])

    try:
        corpo = asyncio.run(ler())
    finally:
        if resposta.background:
            asyncio.run(resposta.background())
    return resposta.status_code, f"{len(corpo)} bytes"


def _job_do_tenant(api_real, tenant, owner_id):
    """Job completed com os 3 artefatos íntegros, GeoJSON e logo, gravado no schema itest_* pelo INSERT real."""
    from geolume_worker.prancha import caminho_logo
    from psycopg2.extras import Json

    import artefatos

    job_id, task_id = uuid.uuid4().hex, str(uuid.uuid4())
    pasta = artefatos.gravar(api_real.saida / job_id, job_id)
    entrada = api_real.saida / "inputs" / f"{job_id}-lote.geojson"
    entrada.write_text('{"type": "FeatureCollection", "features": []}')
    logo = caminho_logo(api_real.saida, job_id)
    logo.parent.mkdir(exist_ok=True)
    logo.write_bytes(b"\x89PNG\r\n\x1a\n")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO jobs (id, task_id, status, input_filename, created_at, completed_at, input_path, owner_id,
                                 tenant_id, prancha, mapa_path, memorial_path, resultado_path)
               VALUES (%s, %s, 'completed', 'lote.geojson', %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (job_id, task_id, AGORA - timedelta(hours=1), AGORA, str(entrada), owner_id, tenant, Json({"logo": True}),
             str(pasta / "mapa.pdf"), str(pasta / "memorial.pdf"), str(pasta / "resultado.json")))
    return task_id


def _status(api_real, user, task_id):
    return {rec or "/jobs/{id}": _chamar(api_real, user, task_id, rec)[0] for rec in RECURSOS}


def test_dono_e_admin_do_tenant_acessam_os_6_recursos(api_real):
    task_id = _job_do_tenant(api_real, T1, DONO["user_id"])
    for user in (DONO, ADMIN_T1):
        assert set(_status(api_real, user, task_id).values()) == {200}, user["email"]


@pytest.mark.parametrize("user", [COLEGA, MEMBRO_T2, ADMIN_T2],
                         ids=["colega-do-mesmo-tenant", "outro-tenant-mesmo-user-id", "admin-de-outro-tenant"])
def test_quem_nao_e_dono_nem_admin_do_tenant_recebe_404_nos_6_recursos(api_real, user):
    task_id = _job_do_tenant(api_real, T1, DONO["user_id"])
    for rec in RECURSOS:
        status, texto = _chamar(api_real, user, task_id, rec)
        assert status == 404, rec
        assert "itest" not in texto and DONO["user_id"] not in texto  # nem tenant nem dono
    # Mesma resposta de um id que não existe: não revela que o job existe em outro tenant.
    assert _chamar(api_real, user, task_id, "") == _chamar(api_real, user, str(uuid.uuid4()), "")


def test_listagem_so_mostra_o_proprio_tenant(api_real):
    do_t1 = _job_do_tenant(api_real, T1, DONO["user_id"])
    do_t2 = _job_do_tenant(api_real, T2, "u-alguem-t2")

    def listados(user):
        return {j["task_id"] for j in api_real.api.jobs(50, user)["jobs"]}

    assert listados(DONO) == {do_t1}
    assert listados(ADMIN_T1) == {do_t1}
    assert listados(ADMIN_T2) == {do_t2}
    assert listados(MEMBRO_T2) == set()  # mesmo user_id do dono, outro tenant
    assert listados(COLEGA) == set()


def test_isolamento_por_tenant_nao_toca_em_jobs_reais(api_real, jobs_isolados):
    _job_do_tenant(api_real, T1, DONO["user_id"])
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_schema(), count(*) FROM jobs")
        schema, total = cur.fetchone()
    assert schema == jobs_isolados and schema.startswith("itest_") and total == 1


def _failed_com_geojson(api_real, tenant, owner_id, entrada=None):
    """Job failed por entrada inválida, com o GeoJSON preservado (retenção de 30 dias), no schema itest_*.
    `entrada` grava outro input_path (cruzado) em vez do GeoJSON do próprio job."""
    job_id, task_id = uuid.uuid4().hex, str(uuid.uuid4())
    if entrada is None:
        entrada = api_real.saida / "inputs" / f"{job_id}-autointersecao.geojson"
        entrada.write_text('{"type": "FeatureCollection", "features": []}')
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO jobs (id, task_id, status, input_filename, created_at, completed_at, input_path, owner_id,
                                 tenant_id, erro)
               VALUES (%s, %s, 'failed', 'autointersecao.geojson', %s, %s, %s, %s, %s, %s)""",
            (job_id, task_id, AGORA - timedelta(hours=1), AGORA, str(entrada), owner_id, tenant,
             "geometria_invalida: O polígono é inválido (ex.: autointerseção)."))
    return task_id


def test_geojson_de_job_failed_e_servido_ao_dono_e_404_para_outro_tenant(api_real):
    task_id = _failed_com_geojson(api_real, T1, DONO["user_id"])
    for user in (DONO, ADMIN_T1):
        assert _chamar(api_real, user, task_id, "/input")[0] == 200, user["email"]
        status, texto = _chamar(api_real, user, task_id, "")
        publico = json.loads(texto)
        assert (status, publico["status"], publico["arquivos"]) == (200, "failed", {})
        assert publico["erro"] == "geometria_invalida: O polígono é inválido (ex.: autointerseção)."
        assert "/" not in publico["erro"] and "Traceback" not in texto and str(api_real.saida) not in texto
    for user in (COLEGA, MEMBRO_T2, ADMIN_T2):
        assert _chamar(api_real, user, task_id, "/input")[0] == 404, user["email"]


def _input_path(task_id):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT input_path FROM jobs WHERE task_id = %s", (task_id,))
        return cur.fetchone()[0]


@pytest.mark.parametrize("vitima, atacante", [((T2, "u-alguem-t2"), DONO), ((T2, DONO["user_id"]), DONO),
                                              ((T1, COLEGA["user_id"]), DONO)],
                         ids=["outro-tenant", "mesmo-user-id-outro-tenant", "outro-dono-mesmo-tenant"])
def test_input_path_cruzado_para_o_geojson_de_outro_job_da_404(api_real, vitima, atacante):
    """O job do atacante é dele (passa no filtro do SQL), mas o input_path aponta para o GeoJSON de outro job."""
    do_outro = _failed_com_geojson(api_real, *vitima)
    alheio = _input_path(do_outro)
    cruzado = _failed_com_geojson(api_real, atacante["tenant_id"], atacante["user_id"], entrada=alheio)
    for user in (atacante, ADMIN_T1):
        status, texto = _chamar(api_real, user, cruzado, "/input")
        assert status == 404, user["email"]
        assert str(api_real.saida) not in texto and "Traceback" not in texto
    # O dono de verdade continua recebendo o próprio GeoJSON.
    dono_real = {T1: ADMIN_T1, T2: ADMIN_T2}[vitima[0]]
    assert _chamar(api_real, dono_real, do_outro, "/input")[0] == 200
