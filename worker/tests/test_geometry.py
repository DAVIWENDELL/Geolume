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


# ---- Coordenadas geográficas em GMS e fuso UTM --------------------------------

from geolume_worker.geometry import format_gms, fuso_utm  # noqa: E402


@pytest.mark.parametrize(
    "valor, eixo, texto",
    [
        (-(15 + 47 / 60 + 12.34 / 3600), "lat", "15°47'12.34\" S"),
        (2.8, "lat", "2°48'00.00\" N"),
        (-47.5, "lon", "47°30'00.00\" O"),
        (12.25, "lon", "12°15'00.00\" L"),
        (0.0, "lat", "0°00'00.00\" N"),
        (0.0, "lon", "0°00'00.00\" L"),
        (-0.000000001, "lat", "0°00'00.00\" N"),  # arredonda a zero: sem "S" para o Equador
        # 59,995" sobe para o minuto seguinte; nunca "60.00"
        (-(15 + 47 / 60 + 59.995 / 3600), "lat", "15°48'00.00\" S"),
        (-(15 + 47 / 60 + 59.994 / 3600), "lat", "15°47'59.99\" S"),
        (-(47 + 59 / 60 + 59.996 / 3600), "lon", "48°00'00.00\" O"),
        (-(59 + 59 / 60 + 59.995 / 3600), "lon", "60°00'00.00\" O"),
    ],
)
def test_format_gms(valor, eixo, texto):
    assert format_gms(valor, eixo) == texto


def test_format_gms_nunca_60_segundos_nem_60_minutos():
    for centesimos in range(0, 6000 * 2):
        valor = -(15 + 47 / 60 + (centesimos / 100 + 0.005) / 3600)
        texto = format_gms(valor, "lat")
        minutos, segundos = texto.split("°")[1].split("'")[:2]
        assert int(minutos) < 60 and float(segundos.rstrip('" S')) < 60, texto


def test_format_gms_eixo_invalido():
    with pytest.raises(ValueError):
        format_gms(1.0, "z")


@pytest.mark.parametrize(
    "epsg, esperado",
    [(31983, (23, "Sul")), (31978, (18, "Sul")), (31985, (25, "Sul")),
     (31975, (20, "Norte")), (31972, (17, "Norte")), (31977, (22, "Norte"))],
)
def test_fuso_utm(epsg, esperado):
    assert fuso_utm(epsg) == esperado


def test_fuso_utm_fora_da_cobertura():
    with pytest.raises(ValueError):
        fuso_utm(4674)
