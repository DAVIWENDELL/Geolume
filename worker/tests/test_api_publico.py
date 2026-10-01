"""Respostas públicas dos jobs: sem caminhos do servidor, sem owner_id/tenant_id, com URLs da API."""

import asyncio
import importlib
import io
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

ANA = {"user_id": "u-ana", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member"}
ADMIN = {"user_id": "u-adm", "email": "adm@geolume.test", "tenant_id": "demo", "role": "admin"}
CAMPOS = {"job_id", "task_id", "status", "input_filename", "created_at", "completed_at", "erro", "arquivos", "prancha"}
PROIBIDOS = ("input_path", "mapa_path", "memorial_path", "resultado_path", "owner_id", "tenant_id",
             "mapa", "memorial", "resultado", "id")
CRIADO = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("qgis.core")
    pytest.importorskip("fastapi")
    import db

    monkeypatch.setattr(db, "init_db", lambda *args, **kwargs: None)
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)
    module = importlib.import_module("api")
    monkeypatch.setattr(module, "OUTPUT_DIR", tmp_path / "saida")

    def registro(task_id, status, erro=None):
        pasta = f"/saida/{task_id}"
        return {"id": f"job-{task_id}", "task_id": task_id, "status": status, "erro": erro,
                "input_filename": "lote.geojson", "input_path": f"/saida/inputs/{task_id}-lote.geojson",
                "created_at": CRIADO, "completed_at": CRIADO if status == "completed" else None,
                "owner_id": "u-ana", "tenant_id": "demo", "mapa_path": f"{pasta}/mapa.pdf",
                "memorial_path": f"{pasta}/memorial.pdf", "resultado_path": f"{pasta}/resultado.json"}

    registros = {"t-ok": registro("t-ok", "completed"), "t-fila": registro("t-fila", "queued"),
                 "t-falha": registro("t-falha", "failed", "[Errno 2] /saida/inputs/x")}
    monkeypatch.setattr(module, "get_job_for", lambda task_id, user: dict(registros[task_id]) if task_id in registros else None)
    monkeypatch.setattr(module, "list_jobs_for", lambda user, limit=20: [dict(r) for r in registros.values()])
    yield module
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)


def sem_detalhes_internos(resposta):
    texto = json.dumps(resposta, default=str)
    assert "/saida" not in texto and "/app" not in texto and "/tmp" not in texto
    assert "u-ana" not in texto and "demo" not in texto


def test_listagem_so_tem_campos_publicos(api):
    jobs = api.jobs(20, ANA)["jobs"]
    assert len(jobs) == 3
    for job in jobs:
        assert set(job) == CAMPOS, job
        assert not any(campo in job for campo in PROIBIDOS)
    sem_detalhes_internos(jobs)


def test_status_so_tem_campos_publicos(api):
    for task_id in ("t-ok", "t-fila", "t-falha"):
        job = api.job_status(task_id, ANA)
        assert set(job) == CAMPOS, job
        sem_detalhes_internos(job)


def test_job_concluido_aponta_downloads_da_api(api):
    job = api.job_status("t-ok", ANA)
    assert job["job_id"] == "job-t-ok"
    assert job["task_id"] == "t-ok"
    assert job["status"] == "completed"
    assert job["input_filename"] == "lote.geojson"
    assert job["arquivos"] == {
        "mapa": "/jobs/t-ok/files/mapa",
        "memorial": "/jobs/t-ok/files/memorial",
        "resultado": "/jobs/t-ok/files/resultado",
    }


def test_job_nao_concluido_nao_tem_downloads(api):
    assert api.job_status("t-fila", ANA)["arquivos"] == {}
    falha = api.job_status("t-falha", ANA)
    assert falha["arquivos"] == {}
    assert falha["erro"] == "Falha no processamento do job."


def test_task_id_estranho_vai_codificado_na_url(api, monkeypatch):
    registro = api.get_job_for("t-ok", ANA)
    registro["task_id"] = "a/b?c"
    monkeypatch.setattr(api, "get_job_for", lambda task_id, user: dict(registro))
    assert api.job_status("a/b?c", ANA)["arquivos"]["mapa"] == "/jobs/a%2Fb%3Fc/files/mapa"


def test_sync_nao_devolve_caminhos_do_servidor(api, monkeypatch, tmp_path):
    from fastapi import UploadFile

    def run_job(entrada, saida, job_id):
        return SimpleNamespace(job_id=job_id, pdf_path=saida / job_id / "mapa.pdf",
                               memorial_path=saida / job_id / "memorial.pdf",
                               json_path=saida / job_id / "resultado.json", phases_ms={"total": 12.5})

    monkeypatch.setattr(api, "run_job", run_job)
    resposta = asyncio.run(api.create_job(UploadFile(file=io.BytesIO(b"{}"), filename="lote.geojson"), ADMIN))
    assert set(resposta) == {"status", "job_id", "input_filename", "fases_ms"}
    assert resposta["status"] == "ok"
    assert resposta["input_filename"] == "lote.geojson"
    assert resposta["fases_ms"] == {"total": 12.5}
    assert str(tmp_path) not in json.dumps(resposta)
    sem_detalhes_internos(resposta)


def test_async_so_devolve_identificadores(api, monkeypatch, tmp_path):
    from fastapi import UploadFile

    (tmp_path / "saida").mkdir()
    monkeypatch.setattr(api, "create_db_job", lambda *a, **k: None)
    monkeypatch.setattr(api, "set_task_id", lambda *a: None)
    monkeypatch.setattr(api.process_job, "delay", lambda *a: SimpleNamespace(id="task-x"))
    resposta = asyncio.run(api.enqueue_job(UploadFile(file=io.BytesIO(b"{}"), filename="lote.geojson"), ANA))
    assert set(resposta) == {"status", "job_id", "task_id"}
    assert resposta["status"] == "queued" and resposta["task_id"] == "task-x"
