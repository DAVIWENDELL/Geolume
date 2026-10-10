"""Diagnóstico de jobs presos: só lê banco e disco, nunca altera nem chama a recuperação."""

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg2")

import diagnostico_jobs  # noqa: E402
import recuperacao  # noqa: E402
from jobs_presos import LIMITE_EXECUCAO, LIMITE_SEM_TAREFA  # noqa: E402

AGORA = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")


@pytest.fixture
def saida(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "logos").mkdir()
    return tmp_path


def _job(saida, job_id="j1", status="queued", task_id=None, criado_ha=timedelta(hours=1), iniciado_ha=None,
         geojson=True, logo=False, tenant="demo"):
    entrada = saida / "inputs" / f"{job_id}-lote.geojson"
    if geojson:
        entrada.write_text("{}")
    if logo:
        (saida / "logos" / f"{job_id}.png").write_bytes(b"png")
    return {"id": job_id, "status": status, "task_id": task_id, "tenant_id": tenant, "owner_id": "u-secreto",
            "created_at": AGORA - criado_ha, "started_at": None if iniciado_ha is None else AGORA - iniciado_ha,
            "input_path": str(entrada), "erro": None, "prancha": {"projeto": "X"}}


def _gerar(saida, job_id, *nomes):
    """Artefatos válidos (passam em integridade.conferir_artefatos)."""
    import artefatos

    (saida / job_id).mkdir(exist_ok=True)
    if nomes:
        artefatos.gravar(saida / job_id, job_id, *nomes)


def _diag(job, saida, job_id="j1"):
    return diagnostico_jobs.diagnosticar(job_id, job, AGORA, saida)


# ---- Classificação ------------------------------------------------------------------


def test_queued_sem_task_id_expirado_e_candidato(saida):
    d = _diag(_job(saida, logo=True), saida)
    assert d["categoria"] == "candidato_recuperacao"
    assert d["motivo"] == "enfileiramento_perdido"
    assert (d["marco"], d["idade_segundos"]) == ("created_at", 3600)
    assert d["limite_segundos"] == LIMITE_SEM_TAREFA.total_seconds()
    assert d["uploads"] == {"geojson": True, "logo": True}


def test_queued_sem_task_id_dentro_do_limite_nao_expirou(saida):
    d = _diag(_job(saida, criado_ha=timedelta(minutes=5)), saida)
    assert (d["categoria"], d["motivo"], d["idade_segundos"]) == ("nao_expirado", "dentro_do_limite", 300)


def test_queued_com_task_id_nao_e_decidido(saida):
    d = _diag(_job(saida, task_id="t-1", criado_ha=timedelta(days=3)), saida)
    assert (d["categoria"], d["motivo"]) == ("nao_expirado", "queued_com_task_id")
    assert d["tem_task_id"] is True


def test_started_expirado_sem_artefatos_e_candidato(saida):
    d = _diag(_job(saida, status="started", task_id="t-1", iniciado_ha=timedelta(hours=2)), saida)
    assert (d["categoria"], d["motivo"]) == ("candidato_recuperacao", "execucao_expirada")
    assert (d["marco"], d["idade_segundos"]) == ("started_at", 7200)
    assert d["limite_segundos"] == LIMITE_EXECUCAO.total_seconds()
    assert d["artefatos"] == {"estado": "ausentes", **{nome: False for nome in ARTEFATOS},
                              "motivos": {"pasta": "ausente"}}


def test_started_expirado_com_os_tres_artefatos_e_revisao_manual(saida):
    _gerar(saida, "j1", *ARTEFATOS)
    d = _diag(_job(saida, status="started", task_id="t-1", iniciado_ha=timedelta(hours=2)), saida)
    assert (d["categoria"], d["motivo"]) == ("artefatos_presentes", "execucao_expirada")
    assert d["artefatos"] == {"estado": "completos", **{nome: True for nome in ARTEFATOS}, "motivos": {}}


def test_started_expirado_com_artefatos_parciais_e_candidato(saida):
    _gerar(saida, "j1", "mapa.pdf")
    d = _diag(_job(saida, status="started", task_id="t-1", iniciado_ha=timedelta(hours=2)), saida)
    assert d["categoria"] == "candidato_recuperacao"
    assert d["artefatos"] == {"estado": "parciais", "mapa.pdf": True, "memorial.pdf": False,
                              "resultado.json": False,
                              "motivos": {"memorial.pdf": "ausente", "resultado.json": "ausente"}}


@pytest.mark.parametrize("estrago, motivos", [
    ("zerados", {"mapa.pdf": "sem_cabecalho_pdf", "memorial.pdf": "sem_cabecalho_pdf",
                 "resultado.json": "json_invalido"}),
    ("vazio", {"memorial.pdf": "vazio"}),
    ("json_de_outro_job", {"resultado.json": "job_id_divergente"}),
])
def test_started_expirado_com_artefatos_invalidos_e_candidato_e_nao_revisao(saida, estrago, motivos):
    import artefatos

    _gerar(saida, "j1", *ARTEFATOS)
    pasta = saida / "j1"
    if estrago == "zerados":
        for nome in ARTEFATOS:
            (pasta / nome).write_bytes(b"\x00" * 1024)
    elif estrago == "vazio":
        (pasta / "memorial.pdf").write_bytes(b"")
    else:
        (pasta / "resultado.json").write_text(json.dumps(artefatos.resultado("outro")), encoding="utf-8")
    antes = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in pasta.iterdir())
    d = _diag(_job(saida, status="started", task_id="t-1", iniciado_ha=timedelta(hours=2)), saida)
    assert (d["categoria"], d["motivo"]) == ("candidato_recuperacao", "execucao_expirada")
    assert d["artefatos"] == {"estado": "invalidos", **{nome: True for nome in ARTEFATOS}, "motivos": motivos}
    assert sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in pasta.iterdir()) == antes
    assert str(saida) not in json.dumps(d, default=str)


def test_started_sem_started_at_nao_e_decidido(saida):
    d = _diag(_job(saida, status="started", task_id="t-1", criado_ha=timedelta(days=9)), saida)
    assert (d["categoria"], d["motivo"], d["marco"], d["idade_segundos"]) == (
        "nao_expirado", "started_sem_started_at", None, None)


def test_started_recente_nao_expirou(saida):
    d = _diag(_job(saida, status="started", task_id="t-1", iniciado_ha=timedelta(minutes=5)), saida)
    assert (d["categoria"], d["motivo"], d["idade_segundos"]) == ("nao_expirado", "dentro_do_limite", 300)


@pytest.mark.parametrize("status", ["completed", "failed", "inesperado"])
def test_terminado_ou_desconhecido_nunca_e_recuperavel(saida, status):
    _gerar(saida, "j1", *ARTEFATOS)
    d = _diag(_job(saida, status=status, task_id="t-1", criado_ha=timedelta(days=9),
                   iniciado_ha=timedelta(days=9)), saida)
    assert (d["categoria"], d["motivo"]) == ("nao_recuperavel", f"status_{status}")


def test_upload_ausente_e_relatado(saida):
    d = _diag(_job(saida, geojson=False), saida)
    assert d["uploads"] == {"geojson": False, "logo": False}
    assert d["categoria"] == "candidato_recuperacao"  # a ausência é informação, não muda a regra


def test_geojson_fora_de_inputs_nao_conta_como_do_job(saida, tmp_path_factory):
    fora = tmp_path_factory.mktemp("fora") / "j1-lote.geojson"
    fora.write_text("{}")
    d = _diag({**_job(saida, geojson=False), "input_path": str(fora)}, saida)
    assert d["uploads"]["geojson"] is False


def test_job_inexistente(saida):
    assert _diag(None, saida, "nada") == {"id": "nada", "categoria": "nao_encontrado"}


def test_saida_nao_expoe_caminhos_dono_nem_prancha(saida):
    texto = json.dumps(_diag(_job(saida, logo=True), saida))
    assert str(saida) not in texto and "u-secreto" not in texto and "projeto" not in texto
    assert "input_path" not in texto and "owner_id" not in texto


def test_agora_sem_fuso_e_recusado(saida):
    with pytest.raises(ValueError):
        diagnostico_jobs.diagnosticar("j1", _job(saida), datetime(2026, 10, 8, 12, 0), saida)


# ---- Relatório: filtros, ordem e somente leitura -----------------------------------------


@pytest.fixture
def banco(monkeypatch):
    """Banco falso: guarda o filtro pedido e devolve os registros que casam."""
    estado = {"jobs": {}, "pedidos": []}

    def ler_jobs(job_ids=None, tenant=None):
        estado["pedidos"].append((job_ids, tenant))
        jobs = estado["jobs"].values()
        if job_ids is not None:
            jobs = [j for j in jobs if j["id"] in job_ids]
        else:
            jobs = [j for j in jobs if j["status"] in ("queued", "started")]
        return [dict(j) for j in jobs if tenant is None or j["tenant_id"] == tenant]

    monkeypatch.setattr(diagnostico_jobs, "ler_jobs", ler_jobs)
    return estado


def test_relatorio_por_job_id_inclui_inexistente_e_ordena(banco, saida):
    banco["jobs"]["b"] = _job(saida, "b", status="completed")
    banco["jobs"]["a"] = _job(saida, "a")
    rel = diagnostico_jobs.relatorio(AGORA, saida, job_ids=["b", "zz", "a"])
    assert [j["id"] for j in rel["jobs"]] == ["a", "b", "zz"]
    assert [j["categoria"] for j in rel["jobs"]] == ["candidato_recuperacao", "nao_recuperavel", "nao_encontrado"]
    assert rel["resumo"] == {"artefatos_presentes": 0, "candidato_recuperacao": 1, "nao_encontrado": 1,
                             "nao_expirado": 0, "nao_recuperavel": 1}
    assert rel["agora"] == AGORA.isoformat() and rel["somente_leitura"] is True


def test_relatorio_sem_filtro_le_so_jobs_ativos(banco, saida):
    banco["jobs"]["a"] = _job(saida, "a")
    banco["jobs"]["b"] = _job(saida, "b", status="completed")
    rel = diagnostico_jobs.relatorio(AGORA, saida)
    assert banco["pedidos"] == [(None, None)]
    assert [j["id"] for j in rel["jobs"]] == ["a"]


def test_relatorio_por_tenant_nao_mostra_outro_tenant(banco, saida):
    banco["jobs"]["a"] = _job(saida, "a", tenant="demo")
    banco["jobs"]["b"] = _job(saida, "b", tenant="outro")
    rel = diagnostico_jobs.relatorio(AGORA, saida, job_ids=["a", "b"], tenant="demo")
    assert banco["pedidos"] == [(["a", "b"], "demo")]
    # O job de outro tenant aparece como não encontrado, sem nenhum dado dele.
    assert rel["jobs"][1] == {"id": "b", "categoria": "nao_encontrado"}


def _foto(raiz):
    return sorted((str(p.relative_to(raiz)), p.stat().st_size, p.stat().st_mtime_ns) for p in raiz.rglob("*"))


def test_relatorio_nao_altera_disco_nem_chama_recuperacao(banco, saida, monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("diagnóstico não altera nada")

    import db

    for nome in ("marcar_job_expirado", "iniciar_job", "falhar_job", "concluir_job", "falhar_enfileiramento",
                 "update_job"):
        monkeypatch.setattr(db, nome, proibido)
    monkeypatch.setattr(recuperacao, "recuperar_job_expirado", proibido)
    banco["jobs"]["a"] = _job(saida, "a", logo=True)
    banco["jobs"]["b"] = _job(saida, "b", status="started", task_id="t", iniciado_ha=timedelta(hours=2))
    _gerar(saida, "b", *ARTEFATOS)
    antes = _foto(saida)
    diagnostico_jobs.relatorio(AGORA, saida)
    assert _foto(saida) == antes


def test_main_imprime_json_deterministico(banco, saida, capsys):
    banco["jobs"]["a"] = _job(saida, "a")
    args = ["--saida", str(saida), "--agora", AGORA.isoformat(), "--job-id", "a"]
    assert diagnostico_jobs.main(args) == 0
    primeira = capsys.readouterr().out
    assert diagnostico_jobs.main(args) == 0
    assert capsys.readouterr().out == primeira
    rel = json.loads(primeira)
    assert rel["jobs"][0]["categoria"] == "candidato_recuperacao"
    assert primeira == json.dumps(rel, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_main_recusa_agora_sem_fuso(banco, saida):
    with pytest.raises(SystemExit):
        diagnostico_jobs.main(["--saida", str(saida), "--agora", "2026-10-08T12:00:00"])


# ---- Leitura no banco ------------------------------------------------------------------


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


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

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


def test_ler_jobs_usa_sessao_somente_leitura_e_so_select(consultas):
    diagnostico_jobs.ler_jobs()
    assert consultas[0] == ("set_session", {"readonly": True})
    ((sql, params),) = consultas[1:]
    assert sql.startswith("SELECT ") and "status IN ('queued', 'started')" in sql and params == ()
    for palavra in ("UPDATE", "DELETE", "INSERT"):
        assert palavra not in sql.upper()


def test_ler_jobs_filtra_por_id_e_tenant(consultas):
    diagnostico_jobs.ler_jobs(["a", "b"], tenant="demo")
    ((sql, params),) = consultas[1:]
    assert "id = ANY(%s)" in sql and "tenant_id = %s" in sql and "status IN" not in sql
    assert params == (["a", "b"], "demo")


def test_api_nao_importa_o_diagnostico():
    from pathlib import Path

    fonte = (Path(diagnostico_jobs.__file__).parent / "api.py").read_text(encoding="utf-8")
    assert "diagnostico_jobs" not in fonte
