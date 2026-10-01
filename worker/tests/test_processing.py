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
