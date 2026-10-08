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


# ---- CRS e coordenadas: limites geográficos e cobertura SIRGAS 2000 / UTM ------------------------

_USE_CRS = "Use EPSG:4326 (WGS 84) ou EPSG:4674 (SIRGAS 2000)."


def _geojson(tmp_path, anel, crs=None):
    dados = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [anel]}}],
    }
    if crs is not None:
        dados["crs"] = crs
    arquivo = tmp_path / "entrada.geojson"
    arquivo.write_text(json.dumps(dados), encoding="utf-8")
    return arquivo


def _quadrado(lon, lat, lado=0.01):
    return [[lon, lat], [lon + lado, lat], [lon + lado, lat + lado], [lon, lat + lado], [lon, lat]]


def _erro_entrada(arquivo):
    with pytest.raises(InvalidInputError) as exc:
        load_input(arquivo)
    for trecho in ("Traceback", "Null geometry", "Error", str(arquivo.parent), "/app", "/saida", "\\"):
        assert trecho not in exc.value.mensagem, exc.value.mensagem  # nada de exceção bruta nem caminho
    return exc.value


_CRS_NAO_RECONHECIDOS = [
    {"type": "name", "properties": {"name": "banana"}},
    {"type": "link", "properties": {"href": "http://exemplo.test/crs", "type": "proj4"}},
    {"type": "name", "properties": {}},
    "EPSG:4326",
]


@pytest.mark.parametrize("crs", _CRS_NAO_RECONHECIDOS)
def test_crs_declarado_nao_reconhecido_e_rejeitado(qgis_app, tmp_path, crs):
    # O OGR cai em EPSG:4326 sem avisar quando não reconhece o CRS declarado.
    erro = _erro_entrada(_geojson(tmp_path, _quadrado(-47.9, -15.8), crs))
    assert erro.codigo == "crs_nao_suportado"
    assert erro.mensagem == f"Sistema de coordenadas declarado no GeoJSON não reconhecido. {_USE_CRS}"


@pytest.mark.parametrize("crs", _CRS_NAO_RECONHECIDOS)
def test_crs_nao_reconhecido_e_rejeitado_antes_do_ogr(qgis_app, tmp_path, monkeypatch, crs):
    # O OGR tenta baixar o href de um CRS "link": a entrada não pode chegar a ele.
    import geolume_worker.input_loader as input_loader

    def sem_ogr(*args, **kwargs):
        raise AssertionError("o OGR não deveria abrir esta entrada")

    monkeypatch.setattr(input_loader, "QgsVectorLayer", sem_ogr)
    assert _erro_entrada(_geojson(tmp_path, _quadrado(-47.9, -15.8), crs)).codigo == "crs_nao_suportado"


def test_crs_epsg_inexistente_diz_qual_foi_declarado(qgis_app, tmp_path):
    crs = {"type": "name", "properties": {"name": "EPSG:999999"}}
    erro = _erro_entrada(_geojson(tmp_path, _quadrado(-47.9, -15.8), crs))
    assert erro.codigo == "crs_nao_suportado"
    assert erro.mensagem == f"Sistema de coordenadas EPSG:999999 não suportado. {_USE_CRS}"


def test_crs_conhecido_nao_suportado_diz_qual_e_quais_aceitos(qgis_app, tmp_path):
    crs = {"type": "name", "properties": {"name": "EPSG:3857"}}
    erro = _erro_entrada(_geojson(tmp_path, _quadrado(-5_330_000, -1_780_000, 100), crs))
    assert erro.codigo == "crs_nao_suportado"
    assert erro.mensagem == f"Sistema de coordenadas EPSG:3857 não suportado. {_USE_CRS}"


@pytest.mark.parametrize("nome, esperado", [
    ("EPSG:4674", "EPSG:4674"),
    ("urn:ogc:def:crs:EPSG::4674", "EPSG:4674"),
    ("urn:ogc:def:crs:EPSG::4326", "EPSG:4326"),
    ("urn:ogc:def:crs:OGC:1.3:CRS84", "EPSG:4326"),
])
def test_crs_aceito_declarado_continua_carregando(qgis_app, tmp_path, nome, esperado):
    arquivo = _geojson(tmp_path, _quadrado(-47.9, -15.8), {"type": "name", "properties": {"name": nome}})
    assert load_input(arquivo).source_crs == esperado


def test_latitude_fora_do_intervalo_e_rejeitada_na_carga(qgis_app, tmp_path):
    erro = _erro_entrada(_geojson(tmp_path, [[-80.0, 95.0], [-79.99, 95.0], [-79.99, 95.01], [-80.0, 95.0]]))
    assert erro.codigo == "coordenada_invalida"
    assert erro.mensagem.startswith("Latitude 95° no vértice 1 fora do intervalo de -90° a 90°.")


def test_longitude_fora_do_intervalo_e_rejeitada_na_carga(qgis_app, tmp_path):
    erro = _erro_entrada(_geojson(tmp_path, _quadrado(200.0, -15.0)))
    assert erro.codigo == "coordenada_invalida"
    assert erro.mensagem == "Longitude 200° no vértice 1 fora do intervalo de -180° a 180°."


def test_vertice_fora_da_cobertura_e_rejeitado_na_carga(qgis_app, tmp_path):
    anel = [[-47.9, -15.8], [-20.0, -15.8], [-20.0, -15.7], [-47.9, -15.7], [-47.9, -15.8]]
    erro = _erro_entrada(_geojson(tmp_path, anel))
    assert erro.codigo == "fora_da_cobertura"
    assert erro.mensagem.startswith("O vértice 2 (longitude -20°, latitude -15.8°) está fora da cobertura")


def test_europa_e_rejeitada_na_carga(qgis_app, fixtures_dir):
    assert _erro_entrada(fixtures_dir / "europa.geojson").codigo == "fora_da_cobertura"
