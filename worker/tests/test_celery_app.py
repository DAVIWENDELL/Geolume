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
    monkeypatch.setattr(module, "concluir_job", lambda *args, **kwargs: True, raising=False)
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


# ---- Status gravados no banco ---------------------------------------------


def _registrar_status(celery_module, monkeypatch):
    registros = []
    monkeypatch.setattr(celery_module, "update_job", lambda job_id, status, **campos: registros.append(
        (job_id, status, campos)))
    return registros


def test_task_marca_started_e_conclui_condicionalmente_com_os_tres_artefatos(celery_module, monkeypatch):
    registros = _registrar_status(celery_module, monkeypatch)
    conclusoes = []
    monkeypatch.setattr(celery_module, "concluir_job", lambda *args: conclusoes.append(args) or True,
                        raising=False)
    resposta = celery_module.process_job.run("/tmp/a.geojson", "job-a")
    assert registros == [("job-a", "started", {})]  # completed só pelo UPDATE condicional
    assert conclusoes == [("job-a", "/tmp/mapa.pdf", "/tmp/memorial.pdf", "/tmp/resultado.json")]
    assert resposta["status"] == "completed"


def test_task_marca_started_e_depois_failed_com_o_erro(celery_module, monkeypatch, tmp_path):
    from geolume_worker.errors import InvalidInputError

    registros = _registrar_status(celery_module, monkeypatch)
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)

    def falhar(*args, **kwargs):
        raise InvalidInputError("sem_feicoes", "O GeoJSON não contém feições.")

    monkeypatch.setattr(celery_module, "run_job", falhar)
    with pytest.raises(InvalidInputError):
        celery_module.process_job.run(str(tmp_path / "a.geojson"), "job-a")
    assert registros == [
        ("job-a", "started", {}),
        ("job-a", "failed", {"erro": "sem_feicoes: O GeoJSON não contém feições."}),
    ]


def test_banco_fora_ao_marcar_started_nao_deixa_upload_nem_job_na_fila(celery_module, monkeypatch, tmp_path):
    entrada = tmp_path / "inputs" / "job-a-lote.geojson"
    entrada.parent.mkdir()
    entrada.write_text("{}")
    logo = tmp_path / "logos" / "job-a.png"
    logo.parent.mkdir()
    logo.write_bytes(b"logo")
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    registros = []

    def update_job(job_id, status, **campos):
        registros.append(status)
        if status == "started":
            raise ConnectionError("banco indisponível")

    monkeypatch.setattr(celery_module, "update_job", update_job)
    with pytest.raises(ConnectionError):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert registros == ["started", "failed"]  # tenta não deixar o job "queued" para sempre
    assert celery_module.chamadas == []  # não processa um job que não conseguiu marcar
    assert not entrada.exists()
    assert not logo.exists()


# ---- Conclusão tardia: o banco decide, nada é apagado ------------------------------


def _job_gerado(celery_module, monkeypatch, tmp_path):
    """run_job de verdade no disco (3 arquivos) e uploads do job presentes."""
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    entrada = tmp_path / "inputs" / "job-a-lote.geojson"
    entrada.parent.mkdir()
    entrada.write_text("{}")
    logo = tmp_path / "logos" / "job-a.png"
    logo.parent.mkdir()
    logo.write_bytes(b"logo")
    pasta = tmp_path / "job-a"

    def run_job(entrada_, saida, job_id, prancha=None):
        pasta.mkdir()
        for nome in ("mapa.pdf", "memorial.pdf", "resultado.json"):
            (pasta / nome).write_bytes(b"ok")
        return SimpleNamespace(job_id=job_id, pdf_path=pasta / "mapa.pdf", memorial_path=pasta / "memorial.pdf",
                               json_path=pasta / "resultado.json", phases_ms={})

    monkeypatch.setattr(celery_module, "run_job", run_job)
    return entrada, logo, pasta


def _intactos(entrada, logo, pasta):
    assert sorted(p.name for p in pasta.iterdir()) == ["mapa.pdf", "memorial.pdf", "resultado.json"]
    assert entrada.exists() and logo.exists()


def test_conclusao_recusada_pelo_banco_nao_finge_sucesso_nem_apaga_nada(celery_module, monkeypatch, tmp_path,
                                                                         caplog):
    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path)
    registros = _registrar_status(celery_module, monkeypatch)
    monkeypatch.setattr(celery_module, "concluir_job", lambda *args: False, raising=False)

    with caplog.at_level("WARNING"):
        resposta = celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert resposta == {"status": "conclusao_recusada", "job_id": "job-a"}  # sem caminhos, sem "completed"
    assert registros == [("job-a", "started", {})]  # nem failed por cima do estado oficial
    assert "job-a" in caplog.text and "conclus" in caplog.text
    _intactos(entrada, logo, pasta)


def test_banco_fora_ao_concluir_propaga_sem_marcar_failed_nem_apagar(celery_module, monkeypatch, tmp_path):
    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path)
    registros = _registrar_status(celery_module, monkeypatch)

    def banco_fora(*args):
        raise ConnectionError("banco indisponível")

    monkeypatch.setattr(celery_module, "concluir_job", banco_fora, raising=False)
    with pytest.raises(ConnectionError):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    # Fica started com os 3 artefatos: a recuperação o trata como "artefatos_presentes" (revisão manual).
    assert registros == [("job-a", "started", {})]
    _intactos(entrada, logo, pasta)
