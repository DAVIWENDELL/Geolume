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
