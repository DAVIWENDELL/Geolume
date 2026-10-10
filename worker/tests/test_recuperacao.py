"""Recuperação de um job expirado: decide pela regra, confere artefatos e só limpa quem ganhou o UPDATE."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg2")

import recuperacao  # noqa: E402
from jobs_presos import LIMITE_EXECUCAO, LIMITE_SEM_TAREFA  # noqa: E402

AGORA = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
JOB = "a1b2c3"
ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")


@pytest.fixture
def saida(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "logos").mkdir()
    return tmp_path


@pytest.fixture
def banco(monkeypatch):
    """Banco falso: um registro por id e o UPDATE condicional decidido pelo teste."""
    estado = {"jobs": {}, "ganha": True, "marcados": []}

    def marcar(job_id, status, marco, expirado_antes, erro):
        estado["marcados"].append((job_id, status, marco, expirado_antes, erro))
        return estado["ganha"]

    monkeypatch.setattr(recuperacao, "ler_job", lambda job_id: estado["jobs"].get(job_id))
    monkeypatch.setattr(recuperacao.db, "marcar_job_expirado", marcar)
    return estado


def _started(saida, iniciado_ha=LIMITE_EXECUCAO + timedelta(minutes=1)):
    entrada = saida / "inputs" / f"{JOB}-lote.geojson"
    entrada.write_text("{}")
    return {"id": JOB, "status": "started", "task_id": "t-1", "created_at": AGORA - timedelta(hours=2),
            "started_at": AGORA - iniciado_ha, "input_path": str(entrada)}


def _uploads(saida):
    entrada = saida / "inputs" / f"{JOB}-lote.geojson"
    logo = saida / "logos" / f"{JOB}.png"
    logo.write_bytes(b"png")
    return entrada, logo


def _gerar(saida, *nomes):
    """Artefatos válidos (passam em integridade.conferir_artefatos)."""
    import artefatos

    pasta = saida / JOB
    pasta.mkdir(exist_ok=True)
    if nomes:
        artefatos.gravar(pasta, JOB, *nomes)
    return pasta


# ---- started expirado ----------------------------------------------------------


def test_started_expirado_sem_artefatos_vira_falha_e_limpa_os_uploads(banco, saida):
    banco["jobs"][JOB] = job = _started(saida)
    entrada, logo = _uploads(saida)

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"

    ((job_id, status, marco, expirado_antes, erro),) = banco["marcados"]
    assert (job_id, status, marco, expirado_antes) == (JOB, "started", job["started_at"], AGORA - LIMITE_EXECUCAO)
    assert erro.startswith("job_expirado: ")
    assert not entrada.exists() and not logo.exists()


def test_started_expirado_com_os_tres_artefatos_nao_muda_nem_apaga_nada(banco, saida):
    banco["jobs"][JOB] = _started(saida)
    entrada, logo = _uploads(saida)
    pasta = _gerar(saida, *ARTEFATOS)

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "artefatos_presentes"

    assert banco["marcados"] == []  # nem failed nem completed automático
    assert sorted(p.name for p in pasta.iterdir()) == sorted(ARTEFATOS)
    assert entrada.exists() and logo.exists()


@pytest.mark.parametrize("presentes", [("mapa.pdf",), ("mapa.pdf", "memorial.pdf")])
def test_started_expirado_com_artefatos_parciais_vira_falha_sem_apagar_os_parciais(banco, saida, presentes):
    banco["jobs"][JOB] = _started(saida)
    pasta = _gerar(saida, *presentes)

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert sorted(p.name for p in pasta.iterdir()) == sorted(presentes)


@pytest.mark.parametrize("estrago", ["zerados", "json_de_outro_job", "pdf_truncado", "link"])
def test_tres_artefatos_invalidos_nao_contam_como_presentes(banco, saida, estrago):
    """Corrompidos não seguram o job em revisão manual: valem como parciais (failed só pelo UPDATE condicional,
    pedido à mão), e nenhum artefato é apagado nem o job é promovido."""
    import json

    import artefatos

    banco["jobs"][JOB] = _started(saida)
    pasta = _gerar(saida, *ARTEFATOS)
    if estrago == "zerados":
        for nome in ARTEFATOS:
            (pasta / nome).write_bytes(b"\x00" * 1024)
    elif estrago == "json_de_outro_job":
        (pasta / "resultado.json").write_text(json.dumps(artefatos.resultado("outro")), encoding="utf-8")
    elif estrago == "pdf_truncado":
        (pasta / "memorial.pdf").write_bytes(artefatos.PDF[:-7])
    else:
        outro = artefatos.gravar(saida / "outro-lugar", JOB)
        (pasta / "mapa.pdf").unlink()
        (pasta / "mapa.pdf").symlink_to(outro / "mapa.pdf")
    antes = sorted((p.name, p.lstat().st_size) for p in pasta.iterdir())

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"

    ((_, status, *_),) = banco["marcados"]
    assert status == "started"  # só failed condicional; completed nunca
    assert sorted((p.name, p.lstat().st_size) for p in pasta.iterdir()) == antes


def _zerar(pasta):
    for nome in ARTEFATOS:
        (pasta / nome).write_bytes(b"\x00" * 1024)
    return {nome: (pasta / nome).read_bytes() for nome in ARTEFATOS}


def test_artefatos_invalidos_viram_failed_limpam_so_o_geojson_e_ficam_para_diagnostico(banco, saida):
    """Decisão operacional: started expirado com os 3 artefatos presentes e inválidos vira failed (job_expirado)
    pelo UPDATE condicional; sai só o GeoJSON do próprio job; a logo e os 3 artefatos ficam byte a byte para
    diagnóstico; nada vira completed."""
    banco["jobs"][JOB] = job = _started(saida)
    entrada, logo = _uploads(saida)
    vizinho = saida / "inputs" / f"{JOB}x-lote.geojson"  # prefixo parecido, outro job
    vizinho.write_text("{}")
    (saida / "logos" / f"{JOB}x.png").write_bytes(b"png")
    conteudo = _zerar(_gerar(saida, *ARTEFATOS))

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"

    ((job_id, status, marco, expirado_antes, erro),) = banco["marcados"]
    assert (job_id, status, marco, expirado_antes) == (JOB, "started", job["started_at"], AGORA - LIMITE_EXECUCAO)
    assert erro == recuperacao.ERROS["execucao_expirada"]
    assert not entrada.exists()
    assert logo.read_bytes() == b"png"  # a logo fica com os artefatos
    assert vizinho.exists() and (saida / "logos" / f"{JOB}x.png").exists()
    assert {nome: (saida / JOB / nome).read_bytes() for nome in ARTEFATOS} == conteudo


@pytest.mark.parametrize("estrago", ["json_de_outro_job", "pdf_truncado", "link"])
def test_outros_artefatos_invalidos_tambem_mantem_a_logo(banco, saida, estrago):
    import json

    import artefatos

    banco["jobs"][JOB] = _started(saida)
    entrada, logo = _uploads(saida)
    pasta = _gerar(saida, *ARTEFATOS)
    if estrago == "json_de_outro_job":
        (pasta / "resultado.json").write_text(json.dumps(artefatos.resultado("outro")), encoding="utf-8")
    elif estrago == "pdf_truncado":
        (pasta / "memorial.pdf").write_bytes(artefatos.PDF[:-7])
    else:
        outro = artefatos.gravar(saida / "outro-lugar", JOB)
        (pasta / "mapa.pdf").unlink()
        (pasta / "mapa.pdf").symlink_to(outro / "mapa.pdf")

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert not entrada.exists() and logo.exists()


@pytest.mark.parametrize("presentes", [(), ("mapa.pdf",), ("mapa.pdf", "memorial.pdf")])
def test_artefatos_ausentes_ou_parciais_continuam_limpando_geojson_e_logo(banco, saida, presentes):
    """Fora do caso inválido nada muda: sem os 3 artefatos, saem o GeoJSON e a logo do job."""
    banco["jobs"][JOB] = _started(saida)
    entrada, logo = _uploads(saida)
    _gerar(saida, *presentes)

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert not entrada.exists() and not logo.exists()


def test_parcial_com_invalido_continua_parcial_e_limpa_a_logo(banco, saida):
    banco["jobs"][JOB] = _started(saida)
    entrada, logo = _uploads(saida)
    pasta = _gerar(saida, "mapa.pdf", "memorial.pdf")
    (pasta / "mapa.pdf").write_bytes(b"\x00" * 1024)  # 2 de 3, um deles zerado: parcial

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert not entrada.exists() and not logo.exists()


def test_queued_sem_tarefa_continua_limpando_geojson_e_logo(banco, saida):
    entrada = saida / "inputs" / f"{JOB}-lote.geojson"
    entrada.write_text("{}")
    banco["jobs"][JOB] = {"id": JOB, "status": "queued", "task_id": None, "created_at": AGORA - timedelta(hours=2),
                          "started_at": None, "input_path": str(entrada)}
    _, logo = _uploads(saida)

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert not entrada.exists() and not logo.exists()


def test_artefatos_invalidos_com_corrida_perdida_nao_apagam_nada(banco, saida):
    banco["jobs"][JOB] = _started(saida)
    banco["ganha"] = False  # outro processo decidiu o job entre a leitura e o UPDATE
    entrada, logo = _uploads(saida)
    conteudo = _zerar(_gerar(saida, *ARTEFATOS))

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "outro_processo"

    assert entrada.exists() and logo.exists()
    assert {nome: (saida / JOB / nome).read_bytes() for nome in ARTEFATOS} == conteudo


def test_recuperacao_nunca_promove_a_completed():
    from pathlib import Path

    fonte = Path(recuperacao.__file__).read_text(encoding="utf-8")
    assert "concluir_job" not in fonte and "'completed'" not in fonte and '"completed"' not in fonte


def test_recuperacao_usa_a_conferencia_de_integridade(banco, saida, monkeypatch):
    banco["jobs"][JOB] = _started(saida)
    vistos = []
    monkeypatch.setattr(recuperacao, "conferir_artefatos",
                        lambda saida_, job_id: vistos.append((saida_, job_id)) or {"ok": True, "motivos": {}})
    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "artefatos_presentes"
    assert vistos == [(saida, JOB)] and banco["marcados"] == []


def test_artefato_que_e_pasta_nao_conta_como_gerado(banco, saida):
    banco["jobs"][JOB] = _started(saida)
    pasta = _gerar(saida, "mapa.pdf", "memorial.pdf")
    (pasta / "resultado.json").mkdir()
    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"


def test_quem_perde_a_corrida_nao_limpa_nada(banco, saida):
    banco["jobs"][JOB] = _started(saida)
    banco["ganha"] = False
    entrada, logo = _uploads(saida)

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "outro_processo"
    assert entrada.exists() and logo.exists()


# ---- queued sem tarefa ---------------------------------------------------------


def test_queued_sem_tarefa_expirado_vira_falha_e_limpa_os_uploads(banco, saida):
    entrada, logo = _uploads(saida)
    entrada.write_text("{}")
    criado = AGORA - LIMITE_SEM_TAREFA - timedelta(minutes=1)
    banco["jobs"][JOB] = {"id": JOB, "status": "queued", "task_id": None, "created_at": criado,
                          "started_at": None, "input_path": str(entrada)}

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    ((_, status, marco, expirado_antes, _),) = banco["marcados"]
    assert (status, marco, expirado_antes) == ("queued", criado, AGORA - LIMITE_SEM_TAREFA)
    assert not entrada.exists() and not logo.exists()


# ---- Nada a fazer ----------------------------------------------------------------


@pytest.mark.parametrize("registro", [
    {"status": "completed", "task_id": "t-1", "started_at": AGORA - timedelta(days=9)},
    {"status": "failed", "task_id": None, "started_at": None},
    {"status": "queued", "task_id": "t-1", "started_at": None},  # pode estar no Redis
    {"status": "started", "task_id": "t-1", "started_at": None},  # job antigo, sem início
    {"status": "started", "task_id": "t-1", "started_at": AGORA - timedelta(minutes=5)},  # ainda rodando
], ids=["completed", "failed", "queued-com-tarefa", "started-sem-inicio", "started-recente"])
def test_job_nao_expirado_nao_e_tocado(banco, saida, registro):
    entrada, logo = _uploads(saida)
    entrada.write_text("{}")
    banco["jobs"][JOB] = {"id": JOB, "created_at": AGORA - timedelta(days=9), "input_path": str(entrada), **registro}

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "nao_expirado"
    assert banco["marcados"] == []
    assert entrada.exists() and logo.exists()


def test_job_inexistente(banco, saida):
    assert recuperacao.recuperar_job_expirado("nada", AGORA, saida) == "nao_encontrado"
    assert banco["marcados"] == []


# ---- Limpeza só do que é do job ----------------------------------------------------


def test_nao_apaga_input_path_fora_de_inputs(banco, saida, tmp_path_factory):
    fora = tmp_path_factory.mktemp("fora") / f"{JOB}-lote.geojson"
    fora.write_text("{}")
    banco["jobs"][JOB] = {**_started(saida), "input_path": str(fora)}

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert fora.exists()


def test_nao_apaga_upload_de_outro_job_com_mesmo_prefixo(banco, saida):
    # "abc" não pode levar junto "abcbf373…-lote.geojson".
    outro = saida / "inputs" / f"{JOB}ff-lote.geojson"
    outro.write_text("{}")
    banco["jobs"][JOB] = {**_started(saida), "input_path": str(outro)}

    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert outro.exists()


def test_input_path_ausente_no_disco_nao_impede_a_falha(banco, saida):
    banco["jobs"][JOB] = job = _started(saida)
    (saida / "inputs" / f"{JOB}-lote.geojson").unlink()
    assert recuperacao.recuperar_job_expirado(JOB, AGORA, saida) == "marcado_failed"
    assert job["input_path"]


def test_agora_sem_fuso_e_recusado(banco, saida):
    banco["jobs"][JOB] = _started(saida)
    with pytest.raises(ValueError):
        recuperacao.recuperar_job_expirado(JOB, datetime(2026, 10, 8, 12, 0), saida)
    assert banco["marcados"] == []


# ---- Leitura sem filtro de usuário fica fora do alcance da API --------------------


def test_ler_job_consulta_pelo_id(monkeypatch):
    import db

    consultas = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            consultas.append((sql, params))

        def fetchone(self):
            return {"id": "j-1", "status": "started"}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self, **kwargs):
            return Cursor()

    monkeypatch.setattr(db, "connect", lambda: Conn())
    assert recuperacao.ler_job("j-1") == {"id": "j-1", "status": "started"}
    assert consultas == [("SELECT * FROM jobs WHERE id = %s", ("j-1",))]


def test_api_nao_importa_a_recuperacao():
    from pathlib import Path

    fonte = (Path(recuperacao.__file__).parent / "api.py").read_text(encoding="utf-8")
    assert "recuperacao" not in fonte and "ler_job" not in fonte
