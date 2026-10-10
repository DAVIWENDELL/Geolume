"""GET /jobs/{task_id}/input: só devolve o GeoJSON salvo em inputs/ para o job."""

import importlib
import os
import sys

import pytest

FIXTURE = "lote_simples.geojson"
USUARIO = {"user_id": "u1", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member"}


@pytest.fixture
def api_module(monkeypatch, tmp_path):
    pytest.importorskip("qgis.core")
    pytest.importorskip("fastapi")
    import db

    monkeypatch.setattr(db, "init_db", lambda *args, **kwargs: None)
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)
    module = importlib.import_module("api")
    inputs = tmp_path / "saida" / "inputs"
    inputs.mkdir(parents=True)
    monkeypatch.setattr(module, "INPUTS_DIR", inputs)
    yield module
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)


@pytest.fixture
def jobs(monkeypatch, api_module):
    registros = {}
    monkeypatch.setattr(api_module, "get_job_for", lambda task_id, user: registros.get(task_id))
    return registros


def registro(input_path, status="completed"):
    return {"id": "job1", "status": status, "input_filename": FIXTURE, "input_path": input_path and str(input_path)}


def partes(resposta) -> list[bytes]:
    """Consome o streaming como o servidor faria ao enviar (o Starlette o expõe como assíncrono)."""
    import asyncio

    async def coletar():
        return [parte async for parte in resposta.body_iterator]

    return asyncio.run(coletar())


def body(resposta) -> bytes:
    return b"".join(partes(resposta))


def assert_404(api_module, task_id):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        api_module.job_input(task_id, USUARIO)
    assert exc.value.status_code == 404
    return exc.value.detail


@pytest.mark.parametrize("status", ["completed", "failed", "queued", "started"])
def test_devolve_geojson_salvo_em_qualquer_status(api_module, jobs, fixtures_dir, status):
    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    arquivo.write_bytes((fixtures_dir / FIXTURE).read_bytes())
    jobs["t1"] = registro(arquivo, status)

    resposta = api_module.job_input("t1", USUARIO)

    assert resposta.media_type == "application/geo+json"
    assert body(resposta) == arquivo.read_bytes()


def test_envia_em_streaming_sem_carregar_o_arquivo_inteiro(api_module, jobs):
    """Sem limite de upload na API: ler tudo em memória derrubaria o contêiner (2 GB)."""
    from fastapi.responses import StreamingResponse

    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    conteudo = b'{"type":"FeatureCollection","features":[]}' + b" " * (3 * 1024 * 1024)
    arquivo.write_bytes(conteudo)
    jobs["t1"] = registro(arquivo)

    resposta = api_module.job_input("t1", USUARIO)

    assert isinstance(resposta, StreamingResponse)
    enviadas = partes(resposta)
    assert len(enviadas) > 1 and max(map(len, enviadas)) <= 1024 * 1024
    assert b"".join(enviadas) == conteudo


def test_troca_por_link_depois_da_validacao_nao_vaza_arquivo_externo(api_module, jobs, fixtures_dir, tmp_path):
    """TOCTOU: o conteúdo sai do descritor validado, não de uma reabertura posterior do caminho."""
    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    original = (fixtures_dir / FIXTURE).read_bytes()
    arquivo.write_bytes(original)
    segredo = tmp_path / "segredo.geojson"
    segredo.write_text('{"segredo": true}')
    jobs["t1"] = registro(arquivo)

    resposta = api_module.job_input("t1", USUARIO)
    arquivo.unlink()
    arquivo.symlink_to(segredo)

    assert body(resposta) == original


def test_nao_segue_link_trocado_antes_de_abrir(api_module, jobs, monkeypatch, tmp_path):
    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    arquivo.write_text("{}")
    segredo = tmp_path / "segredo.geojson"
    segredo.write_text('{"segredo": true}')
    jobs["t1"] = registro(arquivo)
    validar = api_module._safe_input_path

    def validar_e_trocar(raw):
        caminho = validar(raw)
        arquivo.unlink()
        arquivo.symlink_to(segredo)
        return caminho

    monkeypatch.setattr(api_module, "_safe_input_path", validar_e_trocar)
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_job_inexistente(api_module, jobs):
    assert assert_404(api_module, "nao-existe") == "Job não encontrado"


def test_job_sem_input_path_registrado(api_module, jobs):
    jobs["t1"] = registro(None)
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_arquivo_ausente(api_module, jobs):
    jobs["t1"] = registro(api_module.INPUTS_DIR / "job1-sumiu.geojson")
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_bloqueia_path_traversal(api_module, jobs):
    fora = api_module.INPUTS_DIR.parent / "segredo.geojson"
    fora.write_text("{}")
    jobs["t1"] = registro(f"{api_module.INPUTS_DIR}/../segredo.geojson")
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_bloqueia_caminho_absoluto_fora_de_inputs(api_module, jobs, fixtures_dir):
    jobs["t1"] = registro(fixtures_dir / FIXTURE)  # arquivo real, mas fora de inputs/
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"
    jobs["t2"] = registro("/etc/passwd")
    assert assert_404(api_module, "t2") == "Arquivo original não disponível"


def test_bloqueia_link_simbolico_para_fora(api_module, jobs, fixtures_dir):
    link = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    link.symlink_to(fixtures_dir / FIXTURE)
    jobs["t1"] = registro(link)
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_bloqueia_subpasta_e_diretorio(api_module, jobs):
    sub = api_module.INPUTS_DIR / "sub"
    sub.mkdir()
    (sub / "x.geojson").write_text("{}")
    jobs["t1"] = registro(sub / "x.geojson")
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"
    jobs["t2"] = registro(api_module.INPUTS_DIR)
    assert assert_404(api_module, "t2") == "Arquivo original não disponível"


def test_bloqueia_extensao_diferente(api_module, jobs):
    outro = api_module.INPUTS_DIR / "job1-notas.txt"
    outro.write_text("{}")
    jobs["t1"] = registro(outro)
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_upload_assincrono_grava_input_path(api_module, monkeypatch, tmp_path):
    import asyncio
    import io
    from types import SimpleNamespace

    from fastapi import UploadFile

    criados = []
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path / "saida")
    monkeypatch.setattr(api_module, "create_db_job", lambda *a, **kw: criados.append((a, kw)))
    monkeypatch.setattr(api_module, "set_task_id", lambda *a: None)
    monkeypatch.setattr(api_module.process_job, "delay", lambda *a: SimpleNamespace(id="task-x"))

    upload = UploadFile(file=io.BytesIO(b"{}"), filename="../lote.geojson")
    resposta = asyncio.run(api_module.enqueue_job(upload, USUARIO))

    (args, kwargs), = criados
    esperado = tmp_path / "saida" / "inputs" / f"{resposta['job_id']}-lote.geojson"
    assert args[:2] == (resposta["job_id"], "lote.geojson")
    assert kwargs["input_path"] == str(esperado)
    assert esperado.is_file()


def test_nao_serve_hard_link_trocado_antes_de_abrir(api_module, jobs, monkeypatch, tmp_path):
    """Troca por outro arquivo regular (hard link para fora) entre validar e abrir."""
    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    arquivo.write_text("{}")
    segredo = tmp_path / "segredo.geojson"
    segredo.write_text('{"segredo": true}')
    jobs["t1"] = registro(arquivo)
    validar = api_module._safe_input_path

    def validar_e_trocar(raw):
        caminho = validar(raw)
        arquivo.unlink()
        os.link(segredo, arquivo)
        return caminho

    monkeypatch.setattr(api_module, "_safe_input_path", validar_e_trocar)
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_nao_serve_se_a_pasta_inputs_for_trocada_antes_de_abrir(api_module, jobs, monkeypatch, tmp_path):
    """O descritor aberto é validado pelo caminho real, não pelo nome usado para abrir."""
    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    arquivo.write_text("{}")
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / arquivo.name).write_text('{"segredo": true}')
    jobs["t1"] = registro(arquivo)
    validar = api_module._safe_input_path
    inputs = api_module.INPUTS_DIR

    def validar_e_trocar(raw):
        caminho = validar(raw)
        inputs.rename(inputs.with_name("inputs-antigo"))
        inputs.symlink_to(fora, target_is_directory=True)
        return caminho

    monkeypatch.setattr(api_module, "_safe_input_path", validar_e_trocar)
    assert assert_404(api_module, "t1") == "Arquivo original não disponível"


def test_sem_content_length_fixo_o_corpo_segue_o_descritor(api_module, jobs, fixtures_dir):
    """Content-Length do início divergiria do corpo se o arquivo mudasse durante o envio."""
    arquivo = api_module.INPUTS_DIR / f"job1-{FIXTURE}"
    arquivo.write_bytes((fixtures_dir / FIXTURE).read_bytes())
    jobs["t1"] = registro(arquivo)

    resposta = api_module.job_input("t1", USUARIO)

    assert "content-length" not in resposta.headers


@pytest.mark.parametrize("conteudo, esperado", [
    (b"", "json_invalido: O arquivo está vazio."),
    (b'{"type": "FeatureCollection",\n "features": [', "json_invalido: O arquivo não é um JSON válido (erro na linha 2, coluna 15)."),
    ('{"nome": "Sítio"}'.encode("latin-1"), "json_invalido: O arquivo precisa estar em UTF-8."),
])
def test_erro_de_json_chega_ao_cliente_com_codigo_e_causa_clara(api_module, qgis_app, tmp_path, conteudo, esperado):
    from geolume_worker.errors import InvalidInputError
    from geolume_worker.input_loader import load_input

    entrada = tmp_path / "entrada.geojson"
    entrada.write_bytes(conteudo)
    with pytest.raises(InvalidInputError) as exc:
        load_input(entrada)
    # O worker grava str(exc) no job; a API só repassa erros de validação com código conhecido.
    assert api_module._erro_publico(str(exc.value)) == esperado
    assert str(tmp_path) not in esperado


@pytest.mark.parametrize("erro", [
    "coordenada_invalida: Longitude 200° no vértice 1 fora do intervalo de -180° a 180°.",
    "crs_nao_suportado: Sistema de coordenadas declarado no GeoJSON não reconhecido. "
    "Use EPSG:4326 (WGS 84) ou EPSG:4674 (SIRGAS 2000).",
    "fora_da_cobertura: O vértice 2 (longitude -20°, latitude -15.8°) está fora da cobertura SIRGAS 2000 / UTM "
    "aceita: fusos 17N a 22N e 18S a 25S.",
])
def test_erro_de_coordenada_chega_ao_cliente_como_veio(api_module, erro):
    assert api_module._erro_publico(erro) == erro


def test_erro_bruto_de_geometria_nula_nao_chega_ao_cliente(api_module):
    assert api_module._erro_publico("Null geometry cannot be converted to point.") == "Falha no processamento do job."


def test_artefato_invalido_e_interno_e_o_cliente_ve_a_mensagem_generica(api_module):
    erro = "artefato_invalido: mapa.pdf=sem_cabecalho_pdf, resultado.json=json_invalido"
    assert "artefato_invalido" not in api_module.CODIGOS_DE_VALIDACAO
    assert api_module._erro_publico(erro) == "Falha no processamento do job."
