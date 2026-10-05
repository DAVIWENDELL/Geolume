"""Quadro de coordenadas e fontes no mapa.pdf: SRC completo, centroide em GMS e fonte da geometria."""

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsLayoutItemLabel, QgsLayoutItemShape

import pdf_verif
from geolume_worker.geometry import format_gms
from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha
from geolume_worker.processing import process

ZONA = (117, 210, 153, 205)  # x0, x1, y0, y1: abaixo do mapa, à direita da tabela
MARGEM_PT = 5 * pdf_verif.MM
FONTE = "Fonte da geometria: GeoJSON fornecido pelo usuário."
CENTROIDE = "Coordenadas apresentadas correspondem ao centroide da geometria."


def _summary(fixtures_dir, arquivo="gleba_rural_exemplo.geojson"):
    return process(load_input(fixtures_dir / arquivo))


def _rotulos(layout):
    return [item for item in layout.items() if isinstance(item, QgsLayoutItemLabel)]


def _caixa(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


def _quadro(layout):
    [quadro] = [r for r in _rotulos(layout) if r.currentText().startswith("SRC:")]
    return quadro


def _intersectam(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


@pytest.mark.parametrize(
    "arquivo, src",
    [("gleba_rural_exemplo.geojson", "SRC: SIRGAS 2000 / UTM fuso 23 Sul — EPSG:31983"),
     ("lote_boa_vista.geojson", "SRC: SIRGAS 2000 / UTM fuso 20 Norte — EPSG:31975")],
)
def test_quadro_tem_src_completo_centroide_e_fonte(qgis_app, fixtures_dir, arquivo, src):
    summary = _summary(fixtures_dir, arquivo)
    lon, lat = summary.centroide_geo
    centro = summary.geometry_utm.centroid().asPoint()
    _, layout = montar_mapa(summary)
    linhas = _quadro(layout).currentText().split("\n")
    assert linhas == [
        src,
        "Latitude/longitude: SIRGAS 2000 geográficas — EPSG:4674",
        f"Lat {format_gms(lat, 'lat')}   Long {format_gms(lon, 'lon')}",
        f"UTM: E {centro.x():.2f} m   N {centro.y():.2f} m",
        CENTROIDE,
        FONTE,
    ]


def test_hemisferios_no_pdf(qgis_app, fixtures_dir, tmp_path):
    sul = pdf_verif.texto(export_map_pdf(_summary(fixtures_dir), tmp_path / "sul.pdf"))
    norte = pdf_verif.texto(export_map_pdf(_summary(fixtures_dir, "lote_boa_vista.geojson"), tmp_path / "norte.pdf"))
    assert "Lat 15°" in sul and "\" S" in sul and "\" O" in sul
    assert "Lat 2°48'" in norte and "\" N" in norte and "\" O" in norte
    for texto in (sul, norte):
        assert FONTE in texto and CENTROIDE in texto and "EPSG:4674" in texto


def test_fonte_nao_se_apresenta_como_oficial(qgis_app, fixtures_dir):
    _, layout = montar_mapa(_summary(fixtures_dir))
    texto = _quadro(layout).currentText()
    for termo in ("IBGE", "ANA", "oficial", "cadastr", "INCRA", "SIGEF"):
        assert termo not in texto


@pytest.mark.parametrize("legenda", ["nenhuma", "lateral", "inferior"])
def test_quadro_na_zona_livre_sem_tocar_tabela_nem_legenda(qgis_app, fixtures_dir, tmp_path, legenda):
    summary = _summary(fixtures_dir)
    prancha = Prancha(legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    quadro = _caixa(_quadro(layout))
    assert ZONA[0] <= quadro[0] and quadro[2] <= ZONA[1], quadro
    assert ZONA[2] <= quadro[1] and quadro[3] <= ZONA[3], quadro
    outros = [r for r in _rotulos(layout) if r.currentText().startswith(("Vértice", "Legenda", "Limite"))]
    outros += [s for s in layout.items() if isinstance(s, QgsLayoutItemShape)]
    for item in outros:
        assert not _intersectam(quadro, _caixa(item)), (item, quadro)
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    assert pdf_verif.sobrepostas(pdf) == []
