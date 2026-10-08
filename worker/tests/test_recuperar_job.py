"""CLI manual de recuperação: diagnóstico por padrão; escrita só com --apply, um --job-id e confirmação."""

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg2")

import db  # noqa: E402
import diagnostico_jobs  # noqa: E402
import recuperacao  # noqa: E402
import recuperar_job  # noqa: E402

AGORA = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")


@pytest.fixture
def saida(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "logos").mkdir()
    return tmp_path


@pytest.fixture
def banco(monkeypatch):
    """Banco falso compartilhado pela leitura do diagnóstico, pela recuperação e pelo UPDATE condicional."""
    estado = {"jobs": {}, "marcados": []}

    def ler_jobs(job_ids=None, tenant=None):
        jobs = [j for j in estado["jobs"].values() if job_ids is None or j["id"] in job_ids]
        return [dict(j) for j in jobs if tenant is None or j["tenant_id"] == tenant]

    def marcar(job_id, status, marco, expirado_antes, erro):
        job = estado["jobs"][job_id]
        if job["status"] != status:
            return False
        estado["marcados"].append(job_id)
        job.update(status="failed", erro=erro)
        return True

    monkeypatch.setattr(diagnostico_jobs, "ler_jobs", ler_jobs)
    monkeypatch.setattr(recuperacao, "ler_job", lambda job_id: dict(estado["jobs"][job_id])
                        if job_id in estado["jobs"] else None)
    monkeypatch.setattr(db, "marcar_job_expirado", marcar)
    monkeypatch.setattr(recuperar_job, "_agora", lambda: AGORA)
    return estado


def _job(banco, saida, job_id="j1", status="queued", task_id=None, iniciado_ha=None, tenant="demo", logo=True):
    entrada = saida / "inputs" / f"{job_id}-lote.geojson"
    entrada.write_text("{}")
    if logo:
        (saida / "logos" / f"{job_id}.png").write_bytes(b"png")
    banco["jobs"][job_id] = {
        "id": job_id, "status": status, "task_id": task_id, "tenant_id": tenant, "erro": None,
        "created_at": AGORA - timedelta(hours=3),
        "started_at": None if iniciado_ha is None else AGORA - iniciado_ha, "input_path": str(entrada)}
    return entrada


def _gerar(saida, job_id, *nomes):
    (saida / job_id).mkdir(exist_ok=True)
    for nome in nomes:
        (saida / job_id / nome).write_bytes(b"ok")


def _rodar(capsys, saida, *args):
    codigo = recuperar_job.main(["--saida", str(saida), *args])
    return codigo, json.loads(capsys.readouterr().out)


def _foto(raiz):
    return sorted((str(p.relative_to(raiz)), p.stat().st_size, p.stat().st_mtime_ns) for p in raiz.rglob("*"))


def _aplicar(job_id, confirmacao=None):
    return ["--apply", "--job-id", job_id, "--confirm-job-id", job_id if confirmacao is None else confirmacao]


# ---- Padrão: só diagnóstico ----------------------------------------------------------


def test_sem_apply_so_diagnostica(banco, saida, capsys):
    _job(banco, saida)
    antes = _foto(saida)
    codigo, saida_json = _rodar(capsys, saida, "--job-id", "j1")
    assert codigo == 0
    assert saida_json["modo"] == "diagnostico" and saida_json["somente_leitura"] is True
    assert saida_json["diagnostico"]["jobs"][0]["categoria"] == "candidato_recuperacao"
    assert banco["marcados"] == [] and _foto(saida) == antes


def test_confirmacao_sem_apply_e_argumento_invalido(banco, saida, capsys):
    _job(banco, saida)
    codigo, r = _rodar(capsys, saida, "--job-id", "j1", "--confirm-job-id", "j1")
    assert (codigo, r["resultado"]) == (2, "argumento_invalido")
    assert banco["marcados"] == []


# ---- --apply: argumentos e confirmação ------------------------------------------------


@pytest.mark.parametrize("args", [
    ["--apply"],  # sem job: nunca recuperação global
    ["--apply", "--confirm-job-id", "j1"],
    ["--apply", "--job-id", "j1", "--job-id", "j2", "--confirm-job-id", "j1"],  # um job por vez
    ["--apply", "--job-id", "j1", "--confirm-job-id", "j1", "--agora", "2030-01-01T00:00:00+00:00"],
], ids=["sem-job", "so-confirmacao", "dois-jobs", "agora-forjado"])
def test_apply_com_argumentos_invalidos_nao_escreve(banco, saida, capsys, args):
    _job(banco, saida)
    _job(banco, saida, "j2")
    antes = _foto(saida)
    codigo, r = _rodar(capsys, saida, *args)
    assert (codigo, r["resultado"]) == (2, "argumento_invalido")
    assert banco["marcados"] == [] and _foto(saida) == antes


@pytest.mark.parametrize("confirmacao", [None, "j2", "J1", " j1"], ids=["ausente", "outro", "maiuscula", "espaco"])
def test_apply_sem_confirmacao_exata_nao_escreve(banco, saida, capsys, confirmacao):
    _job(banco, saida)
    antes = _foto(saida)
    args = ["--apply", "--job-id", "j1"] + ([] if confirmacao is None else ["--confirm-job-id", confirmacao])
    codigo, r = _rodar(capsys, saida, *args)
    assert (codigo, r["resultado"]) == (2, "confirmacao_invalida")
    assert banco["marcados"] == [] and _foto(saida) == antes
    assert banco["jobs"]["j1"]["status"] == "queued"


# ---- --apply: decisões ---------------------------------------------------------------------


def test_queued_sem_task_id_expirado_e_recuperado_e_limpa_so_os_uploads_dele(banco, saida, capsys):
    entrada = _job(banco, saida)
    vizinho = _job(banco, saida, "j1x")  # mesmo prefixo, outro job
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"))
    assert (codigo, r["resultado"], r["job_id"]) == (0, "marcado_failed", "j1")
    assert r["antes"]["categoria"] == "candidato_recuperacao"
    assert r["depois"]["status"] == "failed" and r["depois"]["categoria"] == "nao_recuperavel"
    assert banco["marcados"] == ["j1"]
    assert not entrada.exists() and not (saida / "logos" / "j1.png").exists()
    assert vizinho.exists() and (saida / "logos" / "j1x.png").exists()


def test_started_expirado_sem_artefatos_e_recuperado_preservando_parciais(banco, saida, capsys):
    _job(banco, saida, status="started", task_id="t-1", iniciado_ha=timedelta(hours=2))
    _gerar(saida, "j1", "mapa.pdf")
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"))
    assert (codigo, r["resultado"]) == (0, "marcado_failed")
    assert (saida / "j1" / "mapa.pdf").exists()  # PDF nunca é apagado


def test_started_expirado_com_os_tres_artefatos_nao_muda(banco, saida, capsys):
    entrada = _job(banco, saida, status="started", task_id="t-1", iniciado_ha=timedelta(hours=2))
    _gerar(saida, "j1", *ARTEFATOS)
    antes = _foto(saida)
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"))
    assert (codigo, r["resultado"]) == (1, "artefatos_presentes")
    assert banco["marcados"] == [] and _foto(saida) == antes and entrada.exists()


@pytest.mark.parametrize("registro", [
    {"status": "completed", "task_id": "t-1"},
    {"status": "failed", "task_id": None},
    {"status": "queued", "task_id": "t-1"},
    {"status": "started", "task_id": "t-1"},  # sem started_at
], ids=["completed", "failed", "queued-com-task-id", "started-sem-started_at"])
def test_nao_recupera_quem_a_regra_nao_decide(banco, saida, capsys, registro):
    _job(banco, saida)
    banco["jobs"]["j1"].update(registro)
    antes = _foto(saida)
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"))
    assert (codigo, r["resultado"]) == (1, "nao_expirado")
    assert banco["marcados"] == [] and _foto(saida) == antes


def test_job_inexistente(banco, saida, capsys):
    codigo, r = _rodar(capsys, saida, *_aplicar("nada"))
    assert (codigo, r["resultado"], r["antes"]) == (1, "nao_encontrado", {"id": "nada", "categoria": "nao_encontrado"})


def test_tenant_incorreto_e_nao_encontrado_sem_escrita(banco, saida, capsys):
    _job(banco, saida, tenant="outro")
    antes = _foto(saida)
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"), "--tenant", "demo")
    assert (codigo, r["resultado"]) == (1, "nao_encontrado")
    assert banco["marcados"] == [] and _foto(saida) == antes


def test_tenant_correto_recupera(banco, saida, capsys):
    _job(banco, saida, tenant="demo")
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"), "--tenant", "demo")
    assert (codigo, r["resultado"]) == (0, "marcado_failed")


def test_outro_processo_venceu_o_update(banco, saida, capsys, monkeypatch):
    entrada = _job(banco, saida)
    monkeypatch.setattr(db, "marcar_job_expirado", lambda *a: False)
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"))
    assert (codigo, r["resultado"]) == (1, "outro_processo")
    assert entrada.exists()  # quem perde não limpa


def test_falha_no_diagnostico_depois_de_marcar_nao_esconde_a_recuperacao(banco, saida, capsys, monkeypatch):
    entrada = _job(banco, saida)
    _gerar(saida, "j1", "mapa.pdf")
    pdf = _foto(saida / "j1")
    leituras = []
    ler_jobs = diagnostico_jobs.ler_jobs

    def ler_jobs_que_cai_depois(job_ids=None, tenant=None):
        leituras.append(job_ids)
        if len(leituras) > 1:
            raise RuntimeError("conexão perdida em /segredo/interno")
        return ler_jobs(job_ids, tenant)

    monkeypatch.setattr(diagnostico_jobs, "ler_jobs", ler_jobs_que_cai_depois)
    codigo, r = _rodar(capsys, saida, *_aplicar("j1"))
    assert (codigo, r["modo"], r["resultado"], r["depois"]) == (0, "aplicar", "marcado_failed", None)
    assert r["erro_diagnostico"] and "/segredo" not in r["erro_diagnostico"]
    assert "Traceback" not in r["erro_diagnostico"]
    assert banco["marcados"] == ["j1"]  # um único UPDATE; nada é tentado de novo
    assert not entrada.exists() and _foto(saida / "j1") == pdf


def test_saida_json_deterministica(banco, saida, capsys):
    _job(banco, saida, status="completed", task_id="t-1")
    _, primeira = _rodar(capsys, saida, *_aplicar("j1"))
    recuperar_job.main(["--saida", str(saida), *_aplicar("j1")])
    texto = capsys.readouterr().out
    assert json.loads(texto) == primeira
    assert texto == json.dumps(primeira, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_api_nao_importa_o_cli():
    from pathlib import Path

    fonte = (Path(recuperar_job.__file__).parent / "api.py").read_text(encoding="utf-8")
    assert "recuperar_job" not in fonte.replace("recuperar_job_expirado", "")
