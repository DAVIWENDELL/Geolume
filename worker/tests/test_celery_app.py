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

    def incondicional(*args, **kwargs):
        raise AssertionError("a task só grava status por UPDATE condicional")

    monkeypatch.setattr(module, "update_job", incondicional, raising=False)
    monkeypatch.setattr(module, "iniciar_job", lambda *args, **kwargs: True, raising=False)
    monkeypatch.setattr(module, "falhar_job", lambda *args, **kwargs: True, raising=False)
    monkeypatch.setattr(module, "concluir_job", lambda *args, **kwargs: True, raising=False)
    # run_job falso não grava nada: por padrão a integridade aprova; os testes dela usam arquivos de verdade.
    monkeypatch.setattr(module, "conferir_artefatos", lambda *args: {"ok": True, "motivos": {}}, raising=False)
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


@pytest.mark.parametrize("erro", [
    "validacao", "qgis",
], ids=["falha-de-validacao", "falha-no-qgis"])
def test_job_failed_mantem_o_geojson_e_remove_so_a_logo(celery_module, monkeypatch, tmp_path, erro):
    """O GeoJSON de job failed fica (retenção: 30 dias), para o preview mostrar o motivo sobre a geometria."""
    from geolume_worker.errors import InvalidInputError

    entrada = tmp_path / "inputs" / "job-a-lote.geojson"
    entrada.parent.mkdir()
    entrada.write_text("{}")
    logo = tmp_path / "logos" / "job-a.png"
    logo.parent.mkdir()
    logo.write_bytes(b"logo")
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)

    def falhar(*args, **kwargs):
        if erro == "validacao":
            raise InvalidInputError("json_invalido", "entrada inválida")
        raise RuntimeError("Falha ao exportar PDF")

    monkeypatch.setattr(celery_module, "run_job", falhar)

    with pytest.raises((InvalidInputError, RuntimeError)):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert entrada.read_text() == "{}"
    assert not logo.exists()


# ---- Status gravados no banco: só transições condicionais -----------------------


def _registrar_status(celery_module, monkeypatch, inicia=True, falha=True, conclui=True):
    """Registra as transições pedidas ao banco; cada uma devolve se o UPDATE condicional venceu."""
    registros = []

    def iniciar_job(job_id):
        registros.append((job_id, "started"))
        return inicia

    def falhar_job(job_id, erro):
        registros.append((job_id, "failed", erro))
        return falha

    def concluir_job(job_id, *caminhos):
        registros.append((job_id, "completed", caminhos))
        return conclui

    monkeypatch.setattr(celery_module, "iniciar_job", iniciar_job)
    monkeypatch.setattr(celery_module, "falhar_job", falhar_job)
    monkeypatch.setattr(celery_module, "concluir_job", concluir_job)
    return registros


def _uploads(tmp_path):
    entrada = tmp_path / "inputs" / "job-a-lote.geojson"
    entrada.parent.mkdir()
    entrada.write_text("{}")
    logo = tmp_path / "logos" / "job-a.png"
    logo.parent.mkdir()
    logo.write_bytes(b"logo")
    return entrada, logo


def _sem_feicoes(*args, **kwargs):
    from geolume_worker.errors import InvalidInputError

    raise InvalidInputError("sem_feicoes", "O GeoJSON não contém feições.")


def test_task_inicia_e_conclui_condicionalmente_com_os_tres_artefatos(celery_module, monkeypatch):
    registros = _registrar_status(celery_module, monkeypatch)
    resposta = celery_module.process_job.run("/tmp/a.geojson", "job-a")
    assert registros == [
        ("job-a", "started"),
        ("job-a", "completed", ("/tmp/mapa.pdf", "/tmp/memorial.pdf", "/tmp/resultado.json")),
    ]
    assert resposta["status"] == "completed"


def test_task_inicia_e_depois_falha_com_o_erro(celery_module, monkeypatch, tmp_path):
    from geolume_worker.errors import InvalidInputError

    registros = _registrar_status(celery_module, monkeypatch)
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(celery_module, "run_job", _sem_feicoes)
    with pytest.raises(InvalidInputError):
        celery_module.process_job.run(str(tmp_path / "a.geojson"), "job-a")
    assert registros == [
        ("job-a", "started"),
        ("job-a", "failed", "sem_feicoes: O GeoJSON não contém feições."),
    ]


def test_banco_fora_ao_marcar_started_nao_deixa_upload_nem_processa(celery_module, monkeypatch, tmp_path):
    entrada, logo = _uploads(tmp_path)
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    registros = _registrar_status(celery_module, monkeypatch, falha=False)  # job ainda queued: failed recusado

    def banco_fora(job_id):
        registros.append((job_id, "started"))
        raise ConnectionError("banco indisponível")

    monkeypatch.setattr(celery_module, "iniciar_job", banco_fora)
    with pytest.raises(ConnectionError):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert [r[1] for r in registros] == ["started", "failed"]  # failed só pelo UPDATE condicional
    assert celery_module.chamadas == []  # não processa um job que não conseguiu marcar
    assert not entrada.exists()
    assert not logo.exists()


# ---- Tarefa atrasada: job já decidido por outro processo --------------------------


def test_tarefa_atrasada_nao_ressuscita_job_ja_decidido(celery_module, monkeypatch, tmp_path, caplog):
    # Ex.: a recuperação marcou failed (job_expirado) antes de a mensagem chegar ao worker.
    entrada, logo = _uploads(tmp_path)
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    registros = _registrar_status(celery_module, monkeypatch, inicia=False)

    with caplog.at_level("WARNING"):
        resposta = celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert resposta == {"status": "inicio_recusado", "job_id": "job-a"}
    assert registros == [("job-a", "started")]  # nem failed nem completed por cima
    assert celery_module.chamadas == []  # não processa
    assert "job-a" in caplog.text and "início" in caplog.text
    assert entrada.exists() and logo.exists()  # nada apagado


def test_falha_tardia_nao_sobrescreve_a_primeira_causa(celery_module, monkeypatch, tmp_path, caplog):
    # A recuperação já gravou failed com job_expirado: o UPDATE condicional recusa o segundo erro.
    from geolume_worker.errors import InvalidInputError

    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    registros = _registrar_status(celery_module, monkeypatch, falha=False)
    parcial = tmp_path / "job-a" / "mapa.pdf"

    def falhar(*args, **kwargs):
        parcial.parent.mkdir()
        parcial.write_bytes(b"pdf")
        _sem_feicoes()

    monkeypatch.setattr(celery_module, "run_job", falhar)
    with caplog.at_level("WARNING"), pytest.raises(InvalidInputError):
        celery_module.process_job.run(str(tmp_path / "a.geojson"), "job-a")

    assert [r[1] for r in registros] == ["started", "failed"]
    assert "job-a" in caplog.text and "falha" in caplog.text
    assert parcial.exists()  # artefatos não são apagados


def test_banco_fora_ao_marcar_failed_propaga_sem_outra_transicao(celery_module, monkeypatch, tmp_path):
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    registros = _registrar_status(celery_module, monkeypatch)

    def banco_fora(job_id, erro):
        registros.append((job_id, "failed", erro))
        raise ConnectionError("banco indisponível")

    monkeypatch.setattr(celery_module, "falhar_job", banco_fora)
    monkeypatch.setattr(celery_module, "run_job", _sem_feicoes)
    with pytest.raises(ConnectionError):
        celery_module.process_job.run(str(tmp_path / "a.geojson"), "job-a")
    assert [r[1] for r in registros] == ["started", "failed"]  # nada de completed


@pytest.mark.parametrize("falha", ["recusada", "banco_fora"])
def test_geojson_fica_quando_o_failed_e_recusado_ou_o_banco_cai(celery_module, monkeypatch, tmp_path, falha):
    """Job já decidido por outro processo ou banco fora ao marcar failed: o worker não apaga o GeoJSON."""
    entrada, logo = _uploads(tmp_path)
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    registros = _registrar_status(celery_module, monkeypatch, falha=False)
    if falha == "banco_fora":
        def banco_fora(job_id, erro):
            raise ConnectionError("banco indisponível")

        monkeypatch.setattr(celery_module, "falhar_job", banco_fora)
    monkeypatch.setattr(celery_module, "run_job", _sem_feicoes)

    with pytest.raises(Exception):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert registros[0] == ("job-a", "started")
    assert entrada.exists()
    assert not logo.exists()


# ---- Conclusão tardia: o banco decide, nada é apagado ------------------------------


def _job_gerado(celery_module, monkeypatch, tmp_path, estragar=None):
    """run_job no disco (3 artefatos válidos, ou estragados por `estragar`), uploads do job presentes e a
    conferência de integridade real."""
    import artefatos
    import integridade

    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(celery_module, "conferir_artefatos", integridade.conferir_artefatos)
    entrada, logo = _uploads(tmp_path)
    pasta = tmp_path / "job-a"

    def run_job(entrada_, saida, job_id, prancha=None):
        artefatos.gravar(pasta, job_id)
        if estragar:
            estragar(pasta)
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
    registros = _registrar_status(celery_module, monkeypatch, conclui=False)

    with caplog.at_level("WARNING"):
        resposta = celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert resposta == {"status": "conclusao_recusada", "job_id": "job-a"}  # sem caminhos, sem "completed"
    assert [r[1] for r in registros] == ["started", "completed"]  # nem failed por cima do estado oficial
    assert "job-a" in caplog.text and "conclus" in caplog.text
    _intactos(entrada, logo, pasta)


def test_banco_fora_ao_concluir_propaga_sem_marcar_failed_nem_apagar(celery_module, monkeypatch, tmp_path):
    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path)
    registros = _registrar_status(celery_module, monkeypatch)

    def banco_fora(*args):
        raise ConnectionError("banco indisponível")

    monkeypatch.setattr(celery_module, "concluir_job", banco_fora)
    with pytest.raises(ConnectionError):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    # Fica started com os 3 artefatos: a recuperação o trata como "artefatos_presentes" (revisão manual).
    assert registros == [("job-a", "started")]
    _intactos(entrada, logo, pasta)


# ---- Integridade dos artefatos antes do completed ---------------------------------


def _zerar(*nomes):
    def estragar(pasta):
        for nome in nomes:
            (pasta / nome).write_bytes(b"\x00" * 1024)
    return estragar


def test_artefatos_validos_concluem(celery_module, monkeypatch, tmp_path):
    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path)
    registros = _registrar_status(celery_module, monkeypatch)
    resposta = celery_module.process_job.run(str(entrada), "job-a", {"logo": True})
    assert [r[1] for r in registros] == ["started", "completed"]
    assert resposta["status"] == "completed"


@pytest.mark.parametrize("estragar, motivo", [
    (_zerar("mapa.pdf", "memorial.pdf", "resultado.json"), "mapa.pdf=sem_cabecalho_pdf"),
    (_zerar("resultado.json"), "resultado.json=json_invalido"),
    (lambda pasta: (pasta / "memorial.pdf").unlink(), "memorial.pdf=ausente"),
    (lambda pasta: (pasta / "mapa.pdf").write_bytes(b""), "mapa.pdf=vazio"),
])
def test_artefato_invalido_marca_failed_condicional_e_nao_conclui(celery_module, monkeypatch, tmp_path, caplog,
                                                                   estragar, motivo):
    gerados = []

    def estragar_e_fotografar(pasta):
        estragar(pasta)
        gerados.extend(sorted((p.name, p.stat().st_size) for p in pasta.iterdir()))

    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path, estragar_e_fotografar)
    registros = _registrar_status(celery_module, monkeypatch)

    with caplog.at_level("WARNING"):
        resposta = celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert [r[1] for r in registros] == ["started", "failed"]  # nunca completed
    erro = registros[1][2]
    assert erro.startswith("artefato_invalido: ") and motivo in erro
    assert str(tmp_path) not in erro
    assert resposta == {"status": "artefato_invalido", "job_id": "job-a"}  # sem caminhos
    assert sorted((p.name, p.stat().st_size) for p in pasta.iterdir()) == gerados  # artefatos para diagnóstico
    assert entrada.exists() and logo.exists()  # uploads também ficam
    assert "job-a" in caplog.text and "artefato" in caplog.text


def test_conferencia_usa_a_saida_e_o_id_do_job(celery_module, monkeypatch, tmp_path):
    monkeypatch.setattr(celery_module, "OUTPUT_DIR", tmp_path)
    vistos = []
    monkeypatch.setattr(celery_module, "conferir_artefatos",
                        lambda saida, job_id: vistos.append((saida, job_id)) or {"ok": True, "motivos": {}})
    celery_module.process_job.run("/tmp/a.geojson", "job-a")
    assert vistos == [(tmp_path, "job-a")]


def test_conferencia_acontece_depois_do_run_job_e_antes_do_completed(celery_module, monkeypatch):
    ordem = []
    run_job = celery_module.run_job
    monkeypatch.setattr(celery_module, "run_job", lambda *a, **k: ordem.append("run_job") or run_job(*a, **k))
    monkeypatch.setattr(celery_module, "conferir_artefatos",
                        lambda *a: ordem.append("conferir") or {"ok": True, "motivos": {}})
    monkeypatch.setattr(celery_module, "concluir_job", lambda *a: ordem.append("concluir") or True)
    celery_module.process_job.run("/tmp/a.geojson", "job-a")
    assert ordem == ["run_job", "conferir", "concluir"]


def test_artefato_invalido_com_job_ja_decidido_nao_sobrescreve(celery_module, monkeypatch, tmp_path, caplog):
    # Ex.: a recuperação já marcou failed (job_expirado) enquanto o worker gerava os arquivos.
    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path, _zerar("resultado.json"))
    registros = _registrar_status(celery_module, monkeypatch, falha=False)

    with caplog.at_level("WARNING"):
        resposta = celery_module.process_job.run(str(entrada), "job-a", {"logo": True})

    assert [r[1] for r in registros] == ["started", "failed"]  # tentou só o UPDATE condicional, sem completed
    assert resposta == {"status": "artefato_invalido", "job_id": "job-a"}
    assert "job-a" in caplog.text and "causa anterior" in caplog.text
    _intactos(entrada, logo, pasta)


def test_banco_fora_ao_marcar_artefato_invalido_propaga_sem_concluir_nem_apagar(celery_module, monkeypatch,
                                                                                 tmp_path):
    entrada, logo, pasta = _job_gerado(celery_module, monkeypatch, tmp_path, _zerar("mapa.pdf"))
    registros = _registrar_status(celery_module, monkeypatch)

    def banco_fora(job_id, erro):
        registros.append((job_id, "failed", erro))
        raise ConnectionError("banco indisponível")

    monkeypatch.setattr(celery_module, "falhar_job", banco_fora)
    with pytest.raises(ConnectionError):
        celery_module.process_job.run(str(entrada), "job-a", {"logo": True})
    assert [r[1] for r in registros] == ["started", "failed"]  # fica started; a recuperação decide depois
    _intactos(entrada, logo, pasta)
