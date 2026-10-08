import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def celery_module(monkeypatch):
    pytest.importorskip("qgis.core")
    pytest.importorskip("celery")
    import db

    monkeypatch.setattr(db, "init_db", lambda: None)
    sys.modules.pop("celery_app", None)
    module = importlib.import_module("celery_app")
    monkeypatch.setattr(module, "update_job", lambda *args, **kwargs: None)
    chamadas = []

    def run_job(entrada, saida, job_id, prancha=None):
        chamadas.append({"entrada": entrada, "job_id": job_id, "prancha": prancha})
        return SimpleNamespace(
            job_id=job_id,
            pdf_path=Path("/tmp/mapa.pdf"),
            memorial_path=Path("/tmp/memorial.pdf"),
            json_path=Path("/tmp/resultado.json"),
            phases_ms={},
        )

    monkeypatch.setattr(module, "run_job", run_job)
    module.chamadas = chamadas
    yield module
    sys.modules.pop("celery_app", None)


def test_sessao_qgis_sobrevive_entre_tarefas(celery_module):
    """exitQgis + initQgis no mesmo processo derruba o worker (SIGSEGV)."""
    from geolume_worker.qgis_session import is_qgis_initialized

    celery_module.process_job.run("/tmp/a.geojson", "job-a")
    assert is_qgis_initialized()

    celery_module.process_job.run("/tmp/b.geojson", "job-b")
    assert is_qgis_initialized()


def test_task_enfileirada_antes_da_prancha_continua_valida(celery_module):
    """Mensagens antigas na fila só têm (input_path, job_id)."""
    celery_module.process_job.run("/tmp/a.geojson", "job-a")
    assert celery_module.chamadas[-1]["prancha"] is None


def test_task_repassa_a_prancha_ao_job(celery_module):
    prancha = {"projeto": "Loteamento Sol", "logo": True}
    celery_module.process_job.run("/tmp/a.geojson", "job-a", prancha)
    assert celery_module.chamadas[-1]["prancha"] == prancha


def test_task_remove_uploads_quando_o_job_falha(celery_module, monkeypatch, tmp_path):
    from geolume_worker.errors import InvalidInputError


    entrada = tmp_path / "inputs" / "job-a-lote.geojson"
    entrada.parent.mkdir()
    entrada.write_text("{}")
    logo = tmp_path / "logos" / "job-a.png"
    logo.parent.mkdir()
    logo.write_bytes(b"logo")
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)

    def falhar(*args, **kwargs):
        raise InvalidInputError("json_invalido", "entrada inválida")

    monkeypatch.setattr(celery_module, "run_job", falhar)

    with pytest.raises(InvalidInputError):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert not entrada.exists()
    assert not logo.exists()
