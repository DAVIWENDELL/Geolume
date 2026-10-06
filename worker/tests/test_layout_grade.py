"""Grade de coordenadas na moldura do mapa: geográfica (EPSG:4674), rótulos GMS em português, sem rede."""

import json
import re

import pytest

pytest.importorskip("qgis.core")

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemScaleBar,
    QgsNetworkAccessManager,
    QgsPointXY,
    QgsProject,
)

import pdf_verif
from geolume_worker.geometry import INTERVALOS_GRADE_S
from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha
from geolume_worker.processing import process

MM = pdf_verif.MM
MARGEM_PT = 5 * MM
GMS = re.compile(r"^(\d{1,3})°(\d{2})'(\d{2})(?:\.(\d{1,2}))?\"$")
LEGENDAS = ("nenhuma", "lateral", "inferior")


def _quadrado(destino, lado, centro=(-45.0, -15.0)):
    x0, y0 = centro[0] - lado / 2, centro[1] - lado / 2
    anel = [[x0, y0], [x0 + lado, y0], [x0 + lado, y0 + lado], [x0, y0 + lado], [x0, y0]]
    destino.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [anel]}}]}), encoding="utf-8")
    return destino


def _summary(fixtures_dir, tmp_path, caso):
    if caso == "grande":
        return process(load_input(_quadrado(tmp_path / "g.geojson", 2.0)))
    return process(load_input(fixtures_dir / f"{caso}.geojson"))


def _itens(layout, tipo):
    return [item for item in layout.items() if isinstance(item, tipo)]


def _caixa(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


def _intersectam(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _extensao_geografica(mapa):
    """lon/lat mín./máx. dos cantos do mapa (UTM) levados a SIRGAS 2000 geográficas — cálculo independente."""
    para_geo = QgsCoordinateTransform(mapa.crs(), QgsCoordinateReferenceSystem("EPSG:4674"), QgsProject.instance())
    e = mapa.extent()
    cantos = [para_geo.transform(QgsPointXY(x, y)) for x in (e.xMinimum(), e.xMaximum())
              for y in (e.yMinimum(), e.yMaximum())]
    return (min(p.x() for p in cantos), max(p.x() for p in cantos),
            min(p.y() for p in cantos), max(p.y() for p in cantos))


def _graus(palavra, hemisferio):
    g, m, s, fracao = GMS.match(palavra).groups()
    assert int(m) < 60 and int(s) < 60, palavra  # nunca 60' nem 60"
    valor = int(g) + int(m) / 60 + float(f"{s}.{fracao or 0}") / 3600
    return -valor if hemisferio in ("S", "O") else valor


def _rotulos_da_grade(pdf, caixa_mapa):
    """Pares (palavra GMS, hemisfério) na faixa abaixo do mapa (longitude) e à esquerda dele (latitude)."""
    x0, y0, x1, y1 = (v * MM for v in caixa_mapa)
    palavras = pdf_verif.palavras(pdf)
    abaixo = sorted((p for p in palavras if y1 <= p.y0 and p.y1 <= y1 + 6 * MM and x0 - MM <= p.x0 <= x1),
                    key=lambda p: p.x0)
    esquerda = sorted((p for p in palavras if p.x1 <= x0 and y0 - 15 * MM <= p.y0 <= y1 + 15 * MM),
                      key=lambda p: -p.y1)  # texto vertical lido de baixo para cima
    lon = [(a, b) for a, b in zip(abaixo, abaixo[1:]) if GMS.match(a.texto) and b.texto in ("L", "O")]
    lat = [(a, b) for a, b in zip(esquerda, esquerda[1:]) if GMS.match(a.texto) and b.texto in ("N", "S")]
    return lon, lat, abaixo + esquerda


@pytest.fixture
def sem_rede(monkeypatch):
    import socket

    requisicoes = []
    gerenciador = QgsNetworkAccessManager.instance()
    gerenciador.requestAboutToBeCreated.connect(requisicoes.append)

    def bloqueia(*args, **kwargs):
        raise AssertionError("o PDF tentou acessar a rede")

    monkeypatch.setattr(socket.socket, "connect", bloqueia)
    monkeypatch.setattr(socket.socket, "connect_ex", bloqueia)
    monkeypatch.setattr(socket, "create_connection", bloqueia)
    yield
    gerenciador.requestAboutToBeCreated.disconnect(requisicoes.append)
    assert requisicoes == []


# ---- Configuração da grade no QGIS -------------------------------------------------


def test_grade_geografica_habilitada_na_moldura(qgis_app, fixtures_dir, tmp_path):
    summary = _summary(fixtures_dir, tmp_path, "gleba_rural_exemplo")
    _, layout = montar_mapa(summary)
    [mapa] = _itens(layout, QgsLayoutItemMap)
    assert mapa.crs().authid() == f"EPSG:{summary.epsg}"  # o mapa continua em UTM
    assert mapa.grids().size() == 1
    grade = mapa.grid()
    Lado, Modo = QgsLayoutItemMapGrid.BorderSide, QgsLayoutItemMapGrid.DisplayMode
    assert grade.enabled()
    assert grade.crs().authid() == "EPSG:4674"
    assert grade.style() == QgsLayoutItemMapGrid.GridStyle.Cross  # cruzetas discretas, não linhas cheias
    assert grade.annotationEnabled()
    assert grade.annotationFormat() == QgsLayoutItemMapGrid.AnnotationFormat.CustomFormat
    for lado in (Lado.Left, Lado.Bottom):
        assert grade.annotationPosition(lado) == QgsLayoutItemMapGrid.AnnotationPosition.OutsideMapFrame
    assert grade.annotationDisplay(Lado.Left) == Modo.LatitudeOnly
    assert grade.annotationDisplay(Lado.Bottom) == Modo.LongitudeOnly
    assert grade.annotationDisplay(Lado.Top) == Modo.HideAll and grade.annotationDisplay(Lado.Right) == Modo.HideAll
    assert grade.intervalX() == grade.intervalY()
    assert grade.intervalX() * 3600 == pytest.approx(min(INTERVALOS_GRADE_S, key=lambda i: abs(i - grade.intervalX()
                                                                                                * 3600)))
    assert grade.annotationTextFormat().size() >= 7


# ---- Rótulos GMS no PDF --------------------------------------------------------------


@pytest.mark.parametrize(
    "caso, hemisferio_lat",
    [("gleba_rural_exemplo", "S"), ("lote_boa_vista", "N"), ("lote_minusculo", "S"), ("grande", "S")],
)
def test_rotulos_gms_reais_dentro_da_extensao_do_mapa(qgis_app, fixtures_dir, tmp_path, caso, hemisferio_lat):
    summary = _summary(fixtures_dir, tmp_path, caso)
    projeto, layout = montar_mapa(summary)
    [mapa] = _itens(layout, QgsLayoutItemMap)
    caixa_mapa = _caixa(mapa)
    intervalo = mapa.grid().intervalX()
    lon_min, lon_max, lat_min, lat_max = _extensao_geografica(mapa)
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf")
    lon, lat, _ = _rotulos_da_grade(pdf, caixa_mapa)
    assert len(lon) >= 2 and len(lat) >= 1, (lon, lat)
    assert {h.texto for _, h in lon} == {"O"}
    assert {h.texto for _, h in lat} == {hemisferio_lat}
    for rotulos, minimo, maximo in ((lon, lon_min, lon_max), (lat, lat_min, lat_max)):
        valores = [_graus(p.texto, h.texto) for p, h in rotulos]
        assert len(set(valores)) == len(valores)
        for valor in valores:
            assert minimo <= valor <= maximo, (valor, minimo, maximo)  # coordenada real do mapa, não inventada
            assert valor / intervalo == pytest.approx(round(valor / intervalo), abs=1e-6)  # múltiplo do intervalo
    # Longitudes crescem para a direita; latitudes crescem para cima.
    assert [_graus(p.texto, h.texto) for p, h in lon] == sorted(_graus(p.texto, h.texto) for p, h in lon)


def test_intervalo_pequeno_e_grande(qgis_app, fixtures_dir, tmp_path):
    def intervalo_s(caso):
        projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, caso))
        valor = _itens(layout, QgsLayoutItemMap)[0].grid().intervalX() * 3600
        del layout, projeto
        return valor

    minusculo, gleba, grande = (intervalo_s(c) for c in ("lote_minusculo", "gleba_rural_exemplo", "grande"))
    assert minusculo < 1  # frações de segundo num lote de 1 m²
    assert 1 <= gleba <= 30
    assert grande >= 1800  # 30' ou mais em ~2°
    assert minusculo < gleba < grande


# ---- Página: legendas, sobreposição, margem, escala ----------------------------------


@pytest.mark.parametrize("caso", ["gleba_rural_exemplo", "lote_boa_vista", "grande"])
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_grade_sem_sobrepor_nada_nos_tres_layouts(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    [mapa] = _itens(layout, QgsLayoutItemMap)
    caixa_mapa = _caixa(mapa)
    outros = [_caixa(i) for i in layout.items()
              if hasattr(i, "positionWithUnits") and i is not mapa and type(i).__name__ != "QgsLayoutItemPage"
              and not i.id().startswith("Moldura")]
    assert any(r.currentText().startswith("Escala numérica") for r in _itens(layout, QgsLayoutItemLabel))
    assert len(_itens(layout, QgsLayoutItemScaleBar)) == 1
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    assert pdf_verif.sobrepostas(pdf) == []
    lon, lat, _ = _rotulos_da_grade(pdf, caixa_mapa)
    assert lon and lat
    for numero, hemisferio in lon + lat:
        for palavra in (numero, hemisferio):
            caixa = (palavra.x0 / MM, palavra.y0 / MM, palavra.x1 / MM, palavra.y1 / MM)
            assert not _intersectam(caixa, caixa_mapa), (palavra.texto, caixa)  # fora da moldura
            for outra in outros:  # título, tabela, escalas, quadro, legenda, seta…
                assert not _intersectam(caixa, outra), (palavra.texto, caixa, outra)
    texto = pdf_verif.texto(pdf)
    assert "Escala numérica: 1:" in texto and "Tabela de vértices" in texto and "Fonte da geometria" in texto


def test_grade_gerada_sem_rede(qgis_app, fixtures_dir, tmp_path, sem_rede):
    for caso in ("gleba_rural_exemplo", "lote_boa_vista"):
        summary = _summary(fixtures_dir, tmp_path, caso)  # process() e a grade transformam para EPSG:4674 aqui
        pdf = export_map_pdf(summary, tmp_path / f"{caso}.pdf")
        projeto, layout = montar_mapa(summary)
        caixa = _caixa(_itens(layout, QgsLayoutItemMap)[0])
        del layout, projeto
        lon, lat, _ = _rotulos_da_grade(pdf, caixa)
        assert lon and lat
