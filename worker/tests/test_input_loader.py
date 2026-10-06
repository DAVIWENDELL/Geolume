import json
import math

import pytest

pytest.importorskip("qgis.core")

from geolume_worker.errors import InvalidInputError
from geolume_worker.input_loader import MAX_BYTES, MAX_VERTICES, load_input


def test_carrega_lote_simples(qgis_app, fixtures_dir):
    loaded = load_input(fixtures_dir / "lote_simples.geojson")
    assert loaded.source_crs in {"EPSG:4326", "EPSG:4674"}
    assert not loaded.geometry.isMultipart()
    anel = loaded.geometry.asPolygon()[0]
    assert len(anel) - 1 == 4


@pytest.mark.parametrize(
    "arquivo, codigo",
    [
        ("inexistente.geojson", "arquivo_nao_encontrado"),
        ("invalido.json", "json_invalido"),
        ("vazio.geojson", "sem_feicoes"),
        ("multiplas_feicoes.geojson", "multiplas_feicoes"),
        ("linha.geojson", "geometria_nao_poligonal"),
        ("com_furo.geojson", "poligono_com_furos"),
        ("autointersecao.geojson", "geometria_invalida"),
        ("crs_utm.geojson", "crs_nao_suportado"),
    ],
)
def test_erros_de_entrada(qgis_app, fixtures_dir, arquivo, codigo):
    with pytest.raises(InvalidInputError) as exc:
        load_input(fixtures_dir / arquivo)
    assert exc.value.codigo == codigo


def test_arquivo_muito_grande(qgis_app, tmp_path):
    grande = tmp_path / "grande.geojson"
    grande.write_bytes(b" " * (MAX_BYTES + 1))
    with pytest.raises(InvalidInputError) as exc:
        load_input(grande)
    assert exc.value.codigo == "arquivo_muito_grande"


def test_excesso_de_vertices(qgis_app, tmp_path):
    n = MAX_VERTICES + 1
    anel = [
        [-47.9 + 0.01 * math.cos(2 * math.pi * i / n), -15.8 + 0.01 * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]
    anel.append(anel[0])
    dados = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [anel]}}],
    }
    arquivo = tmp_path / "muitos.geojson"
    arquivo.write_text(json.dumps(dados), encoding="utf-8")
    with pytest.raises(InvalidInputError) as exc:
        load_input(arquivo)
    assert exc.value.codigo == "excesso_de_vertices"


# ---- Mensagem clara para JSON vazio ou malformado (código json_invalido preservado) --------------

_TEXTO_BRUTO = ("Expecting", "char ", "codec", "line ", "column ", "Traceback", "/", "\\")


def _erro_json(tmp_path, conteudo):
    arquivo = tmp_path / "entrada.geojson"
    arquivo.write_text(conteudo, encoding="utf-8")
    with pytest.raises(InvalidInputError) as exc:
        load_input(arquivo)
    assert exc.value.codigo == "json_invalido"
    for trecho in _TEXTO_BRUTO:  # nada da exceção do Python nem de caminho do servidor
        assert trecho not in exc.value.mensagem, exc.value.mensagem
    assert str(exc.value) == f"json_invalido: {exc.value.mensagem}"  # formato "codigo: mensagem" que a API repassa
    return exc.value.mensagem


def test_arquivo_vazio_diz_que_esta_vazio(qgis_app, tmp_path):
    assert _erro_json(tmp_path, "") == "O arquivo está vazio."


def test_json_malformado_aponta_linha_e_coluna_sem_texto_bruto(qgis_app, tmp_path):
    mensagem = _erro_json(tmp_path, '{"type": "FeatureCollection",\n "features": [')
    assert mensagem == "O arquivo não é um JSON válido (erro na linha 2, coluna 15)."


def test_json_valido_continua_carregando(qgis_app, fixtures_dir):
    assert load_input(fixtures_dir / "lote_simples.geojson").source_crs in {"EPSG:4326", "EPSG:4674"}


def test_arquivo_so_com_espacos_tambem_e_vazio(qgis_app, tmp_path):
    assert _erro_json(tmp_path, "  \n\t\r\n ") == "O arquivo está vazio."


def test_arquivo_fora_de_utf8_diz_a_codificacao(qgis_app, tmp_path):
    arquivo = tmp_path / "latin1.geojson"
    arquivo.write_bytes('{"type": "FeatureCollection", "nome": "Sítio"}'.encode("latin-1"))
    with pytest.raises(InvalidInputError) as exc:
        load_input(arquivo)
    assert exc.value.codigo == "json_invalido"
    assert exc.value.mensagem == "O arquivo precisa estar em UTF-8."


def test_geojson_valido_com_bom_utf8_e_aceito(qgis_app, fixtures_dir, tmp_path):
    arquivo = tmp_path / "com_bom.geojson"
    arquivo.write_bytes(b"\xef\xbb\xbf" + (fixtures_dir / "lote_simples.geojson").read_bytes())
    loaded = load_input(arquivo)
    assert len(loaded.geometry.asPolygon()[0]) - 1 == 4
