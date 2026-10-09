"""Diagnóstico de jobs queued com task_id: só lê banco e Redis; ausência de consulta nunca é prova de abandono."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("psycopg2")
pytest.importorskip("redis")

import diagnostico_fila  # noqa: E402

AGORA = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
T1 = "0f6c2a1e-3b4d-4c5e-8f90-123456789abc"
T2 = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
T3 = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"


def _job(job_id="j1", status="queued", task_id=T1, tenant="demo", criado_ha=timedelta(hours=2)):
    return {"id": job_id, "status": status, "task_id": task_id, "tenant_id": tenant, "owner_id": "u-secreto",
            "created_at": AGORA - criado_ha, "input_path": "/saida/inputs/segredo.geojson", "erro": "segredo"}


def _msg(task_id):
    return json.dumps({"body": "W1tdLCB7fSwge31d", "content-type": "application/json",
                       "headers": {"lang": "py", "task": "geolume.process_job", "id": task_id},
                       "properties": {"correlation_id": task_id, "delivery_tag": "d-" + task_id[:4]}}).encode()


def _unacked(task_id):
    return json.dumps([json.loads(_msg(task_id)), "", "celery"]).encode()


class _Pipe:
    def __init__(self, dados, log, nome):
        self.dados, self.log, self.nome, self.fila = dados, log, nome, []

    def _cmd(self, cmd, *args):
        self.log.append((self.nome, cmd, args))
        self.fila.append((cmd, args))
        return self

    def lrange(self, chave, inicio, fim):
        return self._cmd("lrange", chave, inicio, fim)

    def hvals(self, chave):
        return self._cmd("hvals", chave)

    def get(self, chave):
        return self._cmd("get", chave)

    def execute(self):
        self.log.append((self.nome, "execute", ()))
        saida = []
        for cmd, args in self.fila:
            valor = self.dados.get(args[0])
            saida.append(valor if valor is not None else ([] if cmd != "get" else None))
        return saida

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Redis:
    def __init__(self, dados, log, nome, erro=None):
        self.dados, self.log, self.nome, self.erro = dados, log, nome, erro

    def pipeline(self, transaction=True):
        if self.erro:
            raise self.erro
        self.log.append((self.nome, "pipeline", (transaction,)))
        return _Pipe(self.dados, self.log, self.nome)

    def __getattr__(self, nome):  # qualquer outro comando é proibido
        raise AssertionError(f"comando Redis não permitido: {nome}")


@pytest.fixture
def broker(monkeypatch):
    estado = {"broker": {}, "resultado": {}, "erro": None, "log": []}

    def cliente(url):
        nome = "resultado" if url == diagnostico_fila.RESULT_BACKEND else "broker"
        return _Redis(estado[nome], estado["log"], nome, estado["erro"])

    monkeypatch.setattr(diagnostico_fila, "_cliente", cliente)
    return estado


def _ler(jobs):
    def ler(job_ids=None, tenant=None):
        return [j for j in jobs if (j["status"] == "queued" if job_ids is None else j["id"] in job_ids)
                and (tenant is None or j["tenant_id"] == tenant)]
    return ler


def _rel(monkeypatch, jobs, **kw):
    monkeypatch.setattr(diagnostico_fila, "ler_jobs", _ler(jobs))
    return diagnostico_fila.relatorio(AGORA, **kw)


def _cat(rel, job_id="j1"):
    (job,) = [j for j in rel["jobs"] if j["id"] == job_id]
    return job["categoria"], job.get("motivo")


# ---- Classificação ----------------------------------------------------------------------


def test_task_na_fila_e_fila_confirmada(broker, monkeypatch):
    broker["broker"]["celery"] = [_msg(T2), _msg(T1)]
    rel = _rel(monkeypatch, [_job()])
    assert _cat(rel) == ("fila_confirmada", "na_fila")
    assert rel["jobs"][0]["broker"] == {"onde": "fila", "estado_resultado": None}


def test_task_em_fila_de_prioridade_e_fila_confirmada(broker, monkeypatch):
    broker["broker"]["celery\x06\x169"] = [_msg(T1)]
    assert _cat(_rel(monkeypatch, [_job()])) == ("fila_confirmada", "na_fila")


def test_task_reservada_pelo_worker_e_fila_confirmada(broker, monkeypatch):
    broker["broker"]["unacked"] = [_unacked(T1)]
    rel = _rel(monkeypatch, [_job()])
    assert _cat(rel) == ("fila_confirmada", "reservada_pelo_worker")
    assert rel["jobs"][0]["broker"]["onde"] == "reservada"


def test_task_ausente_e_task_nao_localizada(broker, monkeypatch):
    broker["broker"]["celery"] = [_msg(T2)]
    rel = _rel(monkeypatch, [_job()])
    assert _cat(rel) == ("task_nao_localizada", "ausente_da_fila_e_das_reservas")
    assert rel["jobs"][0]["broker"] == {"onde": None, "estado_resultado": None}


def test_task_ausente_com_resultado_terminado_informa_o_estado(broker, monkeypatch):
    broker["resultado"][f"celery-task-meta-{T1}"] = json.dumps(
        {"status": "FAILURE", "result": {"exc_message": ["segredo"]}, "traceback": "segredo"}).encode()
    rel = _rel(monkeypatch, [_job()])
    assert _cat(rel) == ("task_nao_localizada", "ausente_da_fila_e_das_reservas")
    assert rel["jobs"][0]["broker"]["estado_resultado"] == "FAILURE"
    assert "segredo" not in json.dumps(rel, default=str)


@pytest.mark.parametrize("estado", ["STARTED", "RETRY", "RECEIVED"])
def test_task_em_execucao_no_backend_nao_e_verificavel(broker, monkeypatch, estado):
    broker["resultado"][f"celery-task-meta-{T1}"] = json.dumps({"status": estado}).encode()
    assert _cat(_rel(monkeypatch, [_job()])) == ("nao_verificavel", "task_em_execucao_no_backend")


def test_resultado_ilegivel_nao_e_verificavel(broker, monkeypatch):
    broker["resultado"][f"celery-task-meta-{T1}"] = b"\xff nao json"
    assert _cat(_rel(monkeypatch, [_job()])) == ("nao_verificavel", "resultado_ilegivel")


@pytest.mark.parametrize("task_id", ["t-1", "abc", "0F6C2A1E-3B4D-4C5E-8F90-123456789ABC ", "x" * 36,
                                     "0f6c2a1e3b4d4c5e8f90123456789abc", "../" + T1])
def test_task_id_fora_do_formato_e_invalido(broker, monkeypatch, task_id):
    broker["broker"]["celery"] = [_msg(task_id)]
    assert _cat(_rel(monkeypatch, [_job(task_id=task_id)])) == ("task_id_invalido", "formato_inesperado")


def test_task_id_invalido_nao_consulta_o_resultado(broker, monkeypatch):
    _rel(monkeypatch, [_job(task_id="t-1")])
    assert not [c for c in broker["log"] if c[0] == "resultado" and c[1] == "get"]


@pytest.mark.parametrize("task_id", [None, ""])
def test_queued_sem_task_id(broker, monkeypatch, task_id):
    rel = _rel(monkeypatch, [_job(task_id=task_id)])
    assert _cat(rel) == ("queued_sem_task_id", "enfileiramento_perdido")
    rel = _rel(monkeypatch, [_job(task_id=task_id, criado_ha=timedelta(minutes=5))])
    assert _cat(rel) == ("queued_sem_task_id", "dentro_do_limite")


def test_redis_indisponivel_mantem_protegido(broker, monkeypatch):
    import redis

    broker["erro"] = redis.ConnectionError("sem broker")
    rel = _rel(monkeypatch, [_job(), _job("j2", task_id=None), _job("j3", task_id="t-1")])
    assert rel["broker_disponivel"] is False
    assert _cat(rel, "j1") == ("nao_verificavel", "broker_indisponivel")
    assert _cat(rel, "j2")[0] == "queued_sem_task_id"  # não depende do broker
    assert _cat(rel, "j3")[0] == "task_id_invalido"
    assert "sem broker" not in json.dumps(rel, default=str)


def test_erro_de_socket_tambem_e_broker_indisponivel(broker, monkeypatch):
    broker["erro"] = OSError("rede")
    assert _cat(_rel(monkeypatch, [_job()])) == ("nao_verificavel", "broker_indisponivel")


def test_backend_de_resultado_indisponivel_nao_vira_ausencia(broker, monkeypatch):
    import redis

    def cliente(url):
        if url == diagnostico_fila.RESULT_BACKEND:
            return _Redis({}, broker["log"], "resultado", redis.TimeoutError("lento"))
        return _Redis(broker["broker"], broker["log"], "broker")

    monkeypatch.setattr(diagnostico_fila, "_cliente", cliente)
    assert _cat(_rel(monkeypatch, [_job()])) == ("nao_verificavel", "resultado_indisponivel")


def test_mensagem_ilegivel_no_broker_impede_concluir_ausencia(broker, monkeypatch):
    broker["broker"]["celery"] = [b"nao e json", _msg(T2)]
    rel = _rel(monkeypatch, [_job()])
    assert _cat(rel) == ("nao_verificavel", "mensagem_ilegivel_no_broker")
    assert rel["mensagens_ilegiveis"] == 1


def test_mensagem_ilegivel_nao_impede_confirmar_presenca(broker, monkeypatch):
    broker["broker"]["celery"] = [b"nao e json", _msg(T1)]
    assert _cat(_rel(monkeypatch, [_job()])) == ("fila_confirmada", "na_fila")


def test_mensagem_protocolo_1_com_id_no_corpo(broker, monkeypatch):
    import base64

    corpo = base64.b64encode(json.dumps({"id": T1, "task": "geolume.process_job"}).encode()).decode()
    broker["broker"]["celery"] = [json.dumps({"body": corpo, "properties": {"body_encoding": "base64"},
                                              "headers": {}}).encode()]
    assert _cat(_rel(monkeypatch, [_job()])) == ("fila_confirmada", "na_fila")


def test_job_que_nao_esta_queued_nao_se_aplica(broker, monkeypatch):
    rel = _rel(monkeypatch, [_job(status="completed")], job_ids=["j1"])
    assert _cat(rel) == ("nao_aplicavel", "status_completed")


def test_job_inexistente(broker, monkeypatch):
    rel = _rel(monkeypatch, [_job()], job_ids=["nada"])
    assert rel["jobs"] == [{"id": "nada", "categoria": "nao_encontrado"}]


def test_job_de_outro_tenant_e_nao_encontrado_e_nao_consulta_o_broker(broker, monkeypatch):
    broker["broker"]["celery"] = [_msg(T1)]
    rel = _rel(monkeypatch, [_job(tenant="outro")], job_ids=["j1"], tenant="demo")
    assert rel["jobs"] == [{"id": "j1", "categoria": "nao_encontrado"}]
    assert not [c for c in broker["log"] if c[1] == "get"]


def test_consulta_mista_resumo_soma_o_total(broker, monkeypatch):
    broker["broker"]["celery"] = [_msg(T1)]
    jobs = [_job("a"), _job("b", task_id=T2), _job("c", task_id=None), _job("d", task_id="t-1"),
            _job("e", status="failed"), _job("f", tenant="outro", task_id=T3)]
    rel = _rel(monkeypatch, jobs, job_ids=["a", "b", "c", "d", "e", "f", "nada", "a"], tenant="demo")
    assert {j["id"]: j["categoria"] for j in rel["jobs"]} == {
        "a": "fila_confirmada", "b": "task_nao_localizada", "c": "queued_sem_task_id", "d": "task_id_invalido",
        "e": "nao_aplicavel", "f": "nao_encontrado", "nada": "nao_encontrado"}
    assert sum(rel["resumo"].values()) == rel["total"] == len(rel["jobs"]) == 7
    assert list(rel["resumo"]) == list(diagnostico_fila.CATEGORIAS)


def test_sem_job_ids_le_so_queued(broker, monkeypatch):
    rel = _rel(monkeypatch, [_job("a"), _job("b", status="started"), _job("c", status="completed")])
    assert [j["id"] for j in rel["jobs"]] == ["a"]


def test_job_que_mudou_durante_a_consulta_nao_e_verificavel(broker, monkeypatch):
    leituras = iter([[_job()], [_job(status="started")]])
    monkeypatch.setattr(diagnostico_fila, "ler_jobs", lambda job_ids=None, tenant=None: next(leituras))
    rel = diagnostico_fila.relatorio(AGORA)
    assert _cat(rel) == ("nao_verificavel", "estado_mudou_durante_a_consulta")


def test_task_id_trocado_durante_a_consulta_nao_e_verificavel(broker, monkeypatch):
    leituras = iter([[_job()], [_job(task_id=T2)]])
    monkeypatch.setattr(diagnostico_fila, "ler_jobs", lambda job_ids=None, tenant=None: next(leituras))
    assert _cat(diagnostico_fila.relatorio(AGORA)) == ("nao_verificavel", "estado_mudou_durante_a_consulta")


def test_idade_com_fuso(broker, monkeypatch):
    job = _job()
    job["created_at"] = datetime(2026, 10, 8, 6, 0, tzinfo=timezone(timedelta(hours=-3)))  # 09:00 UTC
    rel = _rel(monkeypatch, [job])
    assert rel["jobs"][0]["idade_segundos"] == 3 * 3600


def test_agora_sem_fuso_e_recusado(broker, monkeypatch):
    monkeypatch.setattr(diagnostico_fila, "ler_jobs", _ler([]))
    with pytest.raises(ValueError):
        diagnostico_fila.relatorio(datetime(2026, 10, 8, 12, 0))


# ---- Saída -------------------------------------------------------------------------------


def test_saida_deterministica_e_sem_dados_internos(broker, monkeypatch):
    broker["broker"]["celery"] = [_msg(T1)]
    jobs = [_job("b", task_id=T2), _job("a"), _job("c", task_id=None)]
    r1 = _rel(monkeypatch, jobs)
    r2 = _rel(monkeypatch, list(reversed(jobs)))
    assert json.dumps(r1, sort_keys=True, default=str) == json.dumps(r2, sort_keys=True, default=str)
    assert [j["id"] for j in r1["jobs"]] == ["a", "b", "c"]
    assert r1["somente_leitura"] is True and r1["broker_disponivel"] is True
    texto = json.dumps(r1, default=str)
    for proibido in ("u-secreto", "owner_id", "segredo", "/saida", T1, T2, "redis://"):
        assert proibido not in texto


# ---- Somente leitura -------------------------------------------------------------------


def test_so_comandos_de_leitura_no_redis(broker, monkeypatch):
    broker["broker"]["celery"] = [_msg(T2)]
    _rel(monkeypatch, [_job(), _job("j2", task_id=T2)])
    comandos = {c[1] for c in broker["log"]}
    assert comandos <= {"pipeline", "lrange", "hvals", "get", "execute"}
    assert ("broker", "pipeline", (True,)) in broker["log"]  # fila e reservas no mesmo MULTI/EXEC
    chaves_broker = {c[2][0] for c in broker["log"] if c[0] == "broker" and c[1] in ("lrange", "hvals")}
    assert chaves_broker == {"celery", "celery\x06\x163", "celery\x06\x166", "celery\x06\x169", "unacked"}
    assert {c[2][0] for c in broker["log"] if c[1] == "get"} == {f"celery-task-meta-{T1}"}


def test_modulo_nao_tem_escrita_nem_limpeza():
    fonte = Path(diagnostico_fila.__file__).read_text(encoding="utf-8")
    for proibido in ("unlink", "rmtree", "os.remove", "UPDATE ", "DELETE ", "INSERT ", ".delete(", ".set(",
                     "lpop", "rpop", "hdel", "flush", "expire", "publish", "control", "inspect", "marcar_job",
                     "recuperar_job_expirado", "falhar"):
        assert proibido not in fonte


def test_nao_chama_recuperacao_nem_escrita_no_banco(broker, monkeypatch):
    import db
    import recuperacao

    def proibido(*a, **k):
        raise AssertionError("diagnóstico tentou escrever")

    for nome in ("marcar_job_expirado", "falhar_job", "falhar_enfileiramento", "concluir_job", "iniciar_job",
                 "update_job", "set_task_id"):
        monkeypatch.setattr(db, nome, proibido)
    monkeypatch.setattr(recuperacao, "recuperar_job_expirado", proibido)
    rel = _rel(monkeypatch, [_job(), _job("j2", task_id=None, criado_ha=timedelta(days=9))])
    assert rel["total"] == 2


# ---- Leitura no banco -------------------------------------------------------------------


class _Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return []


class _Conn(_Cursor):
    def set_session(self, **kwargs):
        self.log.append(("set_session", kwargs))

    def cursor(self, **kwargs):
        return _Cursor(self.log)


@pytest.fixture
def consultas(monkeypatch):
    import db

    log = []
    monkeypatch.setattr(db, "connect", lambda: _Conn(log))
    return log


def test_ler_jobs_somente_leitura_e_so_queued(consultas):
    diagnostico_fila.ler_jobs()
    assert consultas[0] == ("set_session", {"readonly": True})
    ((sql, params),) = consultas[1:]
    assert sql.startswith("SELECT ") and "status = 'queued'" in sql and params == ()
    assert "owner_id" not in sql and "erro" not in sql and "input_path" not in sql


def test_ler_jobs_filtra_por_id_e_tenant(consultas):
    diagnostico_fila.ler_jobs(["a", "b"], tenant="demo")
    ((sql, params),) = consultas[1:]
    assert "id = ANY(%s)" in sql and "tenant_id = %s" in sql and "status = 'queued'" not in sql
    assert params == (["a", "b"], "demo")


# ---- CLI ---------------------------------------------------------------------------------


def test_cli_imprime_json(broker, monkeypatch, capsys):
    monkeypatch.setattr(diagnostico_fila, "ler_jobs", _ler([_job()]))
    assert diagnostico_fila.main(["--agora", "2026-10-08T12:00:00+00:00"]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["somente_leitura"] is True and saida["resumo"]["task_nao_localizada"] == 1


def test_cli_recusa_agora_sem_fuso_e_nao_tem_apply():
    with pytest.raises(SystemExit):
        diagnostico_fila.main(["--agora", "2026-10-08T12:00:00"])
    with pytest.raises(SystemExit):
        diagnostico_fila.main(["--apply"])


def test_api_e_celery_nao_importam_o_diagnostico():
    pasta = Path(diagnostico_fila.__file__).parent
    for arquivo in ("api.py", "celery_app.py"):
        assert "diagnostico_fila" not in (pasta / arquivo).read_text(encoding="utf-8")
