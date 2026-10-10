import json

import pytest

pytest.importorskip("qgis.core")

from geolume_worker.errors import InvalidInputError
from geolume_worker.input_loader import load_input
from geolume_worker.processing import process


def test_process_lote_simples(qgis_app, fixtures_dir):
    summary = process(load_input(fixtures_dir / "lote_simples.geojson"))
    assert summary.epsg == 31983
    assert summary.area_ha == pytest.approx(1.18, abs=0.02)
    assert summary.perimetro_m == pytest.approx(435, abs=3)
    assert len(summary.vertices) == 4
    assert summary.vertices[0].id == "V1"
    assert all(v.distancia_m > 0 for v in summary.vertices)


def test_process_hemisferio_norte(qgis_app, fixtures_dir):
    assert process(load_input(fixtures_dir / "lote_boa_vista.geojson")).epsg == 31975


def test_process_europa_rejeitado(qgis_app, fixtures_dir):
    with pytest.raises(InvalidInputError) as exc:
        process(load_input(fixtures_dir / "europa.geojson"))
    assert exc.value.codigo == "fora_da_cobertura"


def _centroide_independente(summary):
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem(f"EPSG:{summary.epsg}"),
                                       QgsCoordinateReferenceSystem("EPSG:4674"), QgsProject.instance())
    ponto = transform.transform(summary.geometry_utm.centroid().asPoint())
    return ponto.x(), ponto.y()


@pytest.mark.parametrize("arquivo, sinal_lat", [("gleba_rural_exemplo.geojson", -1), ("lote_boa_vista.geojson", 1)])
def test_centroide_geografico_sirgas2000(qgis_app, fixtures_dir, arquivo, sinal_lat):
    summary = process(load_input(fixtures_dir / arquivo))
    lon, lat = summary.centroide_geo
    assert (lon, lat) == pytest.approx(_centroide_independente(summary), abs=1e-9)
    assert lon < 0 and lat * sinal_lat > 0


def _poligono(tmp_path, deslocamentos):
    """Polígono em EPSG:4326 com vértices deslocados (em graus) de Brasília; 1e-9° ≈ 0,1 mm."""
    anel = [[-47.9 + dx, -15.8 + dy] for dx, dy in deslocamentos]
    anel.append(anel[0])
    caminho = tmp_path / "pequeno.geojson"
    caminho.write_text(json.dumps({"type": "Polygon", "coordinates": [anel]}), encoding="utf-8")
    return caminho


SUBMILIMETRICO = [(0, 0), (2e-9, 0), (0, 2e-9)]  # os 3 vértices no mesmo milímetro: sobra 1
DOIS_NO_MESMO_MM = [(0, 0), (2e-9, 0), (1e-5, 1e-5)]  # 2 vértices no mesmo milímetro e 1 a ~1,5 m: sobram 2


# Textos literais: singular com 1 vértice, plural com 2 (a gramática não pode regredir).
RESTA_1 = "resta 1 vértice; é necessário pelo menos 3."
RESTAM_2 = "restam 2 vértices; são necessários pelo menos 3."


@pytest.mark.parametrize("deslocamentos, final", [(SUBMILIMETRICO, RESTA_1), (DOIS_NO_MESMO_MM, RESTAM_2)],
                         ids=["colapsa-em-1", "colapsa-em-2"])
def test_poligono_que_colapsa_ao_milimetro_e_recusado(qgis_app, tmp_path, deslocamentos, final):
    """Passa no load_input (GEOS válido em graus), mas em UTM os vértices arredondados a 3 casas se repetem."""
    entrada = _poligono(tmp_path, deslocamentos)
    load_input(entrada)  # a entrada em si é aceita: a recusa é do processamento
    with pytest.raises(InvalidInputError) as exc:
        process(load_input(entrada))
    assert exc.value.codigo == "poligono_muito_pequeno"
    assert exc.value.mensagem == "O polígono é pequeno demais: após arredondar as coordenadas para milímetros, " + final


def test_poligono_de_1_cm_continua_com_4_vertices(qgis_app, tmp_path):
    summary = process(load_input(_poligono(tmp_path, [(0, 0), (9e-8, 0), (9e-8, 9e-8), (0, 9e-8)])))
    assert len(summary.vertices) == 4


def test_triangulo_com_3_vertices_distintos_depois_do_arredondamento_e_aceito(qgis_app, tmp_path):
    """Fronteira do critério: exatamente 3 vértices restantes valem (só menos de 3 é recusado)."""
    summary = process(load_input(_poligono(tmp_path, [(0, 0), (9e-8, 0), (0, 9e-8)])))
    assert len(summary.vertices) == 3
