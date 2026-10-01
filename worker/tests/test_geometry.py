import pytest

from geolume_worker.errors import InvalidInputError
from geolume_worker.geometry import (
    format_dms,
    grid_azimuth_deg,
    utm_epsg_for,
    vertex_table,
)


def test_epsg_brasilia():
    assert utm_epsg_for(-47.9, -15.8) == 31983


def test_epsg_boa_vista_hemisferio_norte():
    assert utm_epsg_for(-60.7, 2.8) == 31975


def test_epsg_fora_da_cobertura():
    with pytest.raises(InvalidInputError) as exc:
        utm_epsg_for(10.0, 50.0)
    assert exc.value.codigo == "fora_da_cobertura"


@pytest.mark.parametrize(
    "destino, esperado",
    [((0, 10), 0), ((10, 0), 90), ((0, -10), 180), ((-10, 0), 270), ((10, 10), 45)],
)
def test_azimutes_cardeais(destino, esperado):
    assert grid_azimuth_deg(0, 0, *destino) == pytest.approx(esperado)


@pytest.mark.parametrize(
    "graus, texto",
    [
        (45.0, "45°00'00\""),
        (123.5125, "123°30'45\""),
        (29.99999999, "30°00'00\""),
        (359.9999999, "0°00'00\""),
    ],
)
def test_format_dms(graus, texto):
    assert format_dms(graus) == texto


def test_vertex_table_quadrado():
    vertices = vertex_table([(0, 0), (0, 10), (10, 10), (10, 0)])
    assert [v.id for v in vertices] == ["V1", "V2", "V3", "V4"]
    assert vertices[0].azimute == "0°00'00\""
    assert vertices[0].distancia_m == 10.0
    assert vertices[3].azimute == "270°00'00\""


def test_vertex_table_remove_duplicados_consecutivos():
    vertices = vertex_table([(0, 0), (0, 10), (0, 10), (10, 10), (10, 0), (0, 0)])
    assert len(vertices) == 4
    assert all(v.distancia_m > 0 for v in vertices)
