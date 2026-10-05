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
