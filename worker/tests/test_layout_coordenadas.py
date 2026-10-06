"""Quadro de coordenadas e fontes no mapa.pdf: SRC completo, centroide em GMS e fonte da geometria."""

import pytest

pytest.importorskip("qgis.core")

from qgis.core import (
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsNetworkAccessManager,
    QgsUnitTypes,
)

import pdf_verif
from geolume_worker.geometry import format_gms
from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha
from geolume_worker.processing import process

ZONA = (117, 210, 153, 205)  # x0, x1, y0, y1: abaixo do mapa, à direita da tabela
BASE_DO_QUADRO = 202  # folga de 3 mm acima do limite inferior (205)
FONTE_MINIMA_PT = 8
# Altura da caixa de palavra no poppler ≈ 1,17 × corpo da DejaVu Sans: 7 pt → 8,18 pt; 8 pt → 9,35 pt.
ALTURA_MINIMA_PALAVRA_PT = 9.2
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
        "Coordenadas apresentadas correspondem",
        "ao centroide da geometria.",
        FONTE,
    ]


def _palavras_do_quadro(pdf):
    """Palavras do PDF dentro da caixa do quadro (zona livre abaixo do mapa, à direita da tabela)."""
    return [p for p in pdf_verif.palavras(pdf)
            if p.x0 >= ZONA[0] * pdf_verif.MM and p.x1 <= ZONA[1] * pdf_verif.MM and p.y0 >= 170 * pdf_verif.MM]


def test_corpo_do_quadro_com_fonte_minima_de_8_pt(qgis_app, fixtures_dir, tmp_path):
    summary = _summary(fixtures_dir)
    _, layout = montar_mapa(summary)
    formato = _quadro(layout).textFormat()
    assert formato.sizeUnit() == QgsUnitTypes.RenderUnit.RenderPoints
    assert formato.size() >= FONTE_MINIMA_PT
    palavras = _palavras_do_quadro(export_map_pdf(summary, tmp_path / "m.pdf"))
    assert {"SRC:", "Fonte", "centroide"} <= {p.texto for p in palavras}
    for palavra in palavras:
        assert palavra.y1 - palavra.y0 >= ALTURA_MINIMA_PALAVRA_PT, palavra


def test_hemisferios_no_pdf(qgis_app, fixtures_dir, tmp_path):
    sul = pdf_verif.texto(export_map_pdf(_summary(fixtures_dir), tmp_path / "sul.pdf"))
    norte = pdf_verif.texto(export_map_pdf(_summary(fixtures_dir, "lote_boa_vista.geojson"), tmp_path / "norte.pdf"))
    assert "Lat 15°" in sul and "\" S" in sul and "\" O" in sul
    assert "Lat 2°48'" in norte and "\" N" in norte and "\" O" in norte
    for texto in (sul, norte):
        assert FONTE in texto and "EPSG:4674" in texto
        assert CENTROIDE in " ".join(texto.split())


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
    assert ZONA[2] <= quadro[1] and quadro[3] <= BASE_DO_QUADRO, quadro
    outros = [r for r in _rotulos(layout) if r.currentText().startswith(("Tabela", "Vértice", "Exibidos", "Legenda",
                                                                          "Limite"))]
    outros += [i for i in layout.items() if isinstance(i, (QgsLayoutItemShape, QgsLayoutItemScaleBar, QgsLayoutItemMap))
               and not i.id().startswith("Moldura")]
    assert len(outros) >= 4
    for item in outros:
        assert not _intersectam(quadro, _caixa(item)), (item, quadro)
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    assert pdf_verif.sobrepostas(pdf) == []


@pytest.mark.parametrize("n", [6, 30])
@pytest.mark.parametrize("legenda", ["nenhuma", "lateral", "inferior"])
def test_quadro_sem_sobreposicao_com_tabela_longa(qgis_app, tmp_path, legenda, n):
    import poligonos

    summary = process(load_input(poligonos.gerar(tmp_path / f"p{n}.geojson", n)))
    projeto, layout = montar_mapa(summary, prancha=Prancha(legenda=legenda))
    quadro = _caixa(_quadro(layout))
    for item in _rotulos(layout):
        if item.currentText().startswith(("Tabela", "Vértice", "Exibidos", "Legenda", "Limite")):
            assert not _intersectam(quadro, _caixa(item)), (item.currentText()[:20], quadro)
    del layout, projeto
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=Prancha(legenda=legenda))
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    assert pdf_verif.sobrepostas(pdf) == []


def test_quadro_gerado_sem_rede(qgis_app, fixtures_dir, tmp_path, monkeypatch):
    import socket

    requisicoes = []
    gerenciador = QgsNetworkAccessManager.instance()
    gerenciador.requestAboutToBeCreated.connect(requisicoes.append)

    def sem_rede(*args, **kwargs):
        raise AssertionError("o PDF tentou acessar a rede")

    monkeypatch.setattr(socket.socket, "connect", sem_rede)
    monkeypatch.setattr(socket.socket, "connect_ex", sem_rede)
    monkeypatch.setattr(socket, "create_connection", sem_rede)
    try:
        for legenda in ("nenhuma", "lateral", "inferior"):
            # process() também roda aqui: a conversão para EPSG:4674 não pode buscar grade remota.
            summary = _summary(fixtures_dir)
            pdf = export_map_pdf(summary, tmp_path / f"{legenda}.pdf", prancha=Prancha(legenda=legenda))
            assert FONTE in pdf_verif.texto(pdf)
    finally:
        gerenciador.requestAboutToBeCreated.disconnect(requisicoes.append)
    assert requisicoes == []
