"""Escala numérica no mapa.pdf: derivada do mapa QGIS, legível e junto da escala gráfica."""

import json
import re

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsLayoutItemLabel, QgsLayoutItemMap, QgsLayoutItemScaleBar, QgsLayoutItemShape

import pdf_verif
import poligonos
from geolume_worker.geometry import denominador_legivel
from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha
from geolume_worker.processing import process

ESCALA = re.compile(r"^Escala numérica: 1:(\d{1,3}(?:\.\d{3})*)$")
COLUNA_DIREITA = (218, 287)
MARGEM_PT = 5 * pdf_verif.MM


def _gleba_grande(destino):
    """Quadrado de ~2° (~4,7 milhões de ha) dentro do fuso 23S: escala na casa do milhão."""
    anel = [[-47.5, -16.0], [-45.5, -16.0], [-45.5, -14.0], [-47.5, -14.0], [-47.5, -16.0]]
    destino.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [anel]}}]}), encoding="utf-8")
    return destino


def _summary(fixtures_dir, tmp_path, caso):
    if caso == "grande":
        return process(load_input(_gleba_grande(tmp_path / "grande.geojson")))
    if caso == "30v":
        return process(load_input(poligonos.gerar(tmp_path / "p30.geojson", 30)))
    return process(load_input(fixtures_dir / f"{caso}.geojson"))


def _itens(layout, tipo):
    return [item for item in layout.items() if isinstance(item, tipo)]


def _rotulos(layout):
    return _itens(layout, QgsLayoutItemLabel)


def _caixa(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


def _intersectam(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _escala_numerica(layout):
    [rotulo] = [r for r in _rotulos(layout) if r.currentText().startswith("Escala numérica")]
    return rotulo


def _denominador(rotulo):
    return int(ESCALA.match(rotulo.currentText()).group(1).replace(".", ""))


CASOS = ["lote_minusculo", "lote_simples", "gleba_rural_exemplo", "30v", "grande"]


@pytest.mark.parametrize("caso", CASOS)
def test_escala_numerica_e_a_escala_real_do_mapa(qgis_app, fixtures_dir, tmp_path, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    _, layout = montar_mapa(summary)
    [mapa] = _itens(layout, QgsLayoutItemMap)
    denominador = _denominador(_escala_numerica(layout))
    # Mesma escala do QGIS e conferida de forma independente: largura do terreno (m) / largura no papel (m).
    assert mapa.scale() == pytest.approx(denominador, rel=1e-9)
    assert mapa.extent().width() / (mapa.rect().width() / 1000) == pytest.approx(denominador, rel=1e-6)
    assert denominador_legivel(denominador) == denominador  # já é um valor legível
    # Arredondar para cima só afasta: o polígono inteiro continua dentro do mapa.
    assert mapa.extent().contains(summary.geometry_utm.boundingBox())
    # A escala gráfica segue o mesmo mapa.
    [barra] = _itens(layout, QgsLayoutItemScaleBar)
    assert barra.linkedMap() is mapa


def test_escalas_pequena_e_grande_nas_ordens_esperadas(qgis_app, fixtures_dir, tmp_path):
    def denominador(caso):
        projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, caso))
        valor = _denominador(_escala_numerica(layout))
        del layout, projeto
        return valor

    minusculo, gleba, grande = denominador("lote_minusculo"), denominador("gleba_rural_exemplo"), denominador("grande")
    assert minusculo < 1_000
    assert 1_000 <= gleba < 100_000
    assert grande >= 1_000_000
    assert minusculo < gleba < grande


@pytest.mark.parametrize("caso", ["gleba_rural_exemplo", "grande"])
def test_pdf_mostra_escala_numerica_com_ponto_de_milhar(qgis_app, fixtures_dir, tmp_path, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    projeto, layout = montar_mapa(summary)
    esperado = _escala_numerica(layout).currentText()
    del layout, projeto
    texto = " ".join(pdf_verif.texto(export_map_pdf(summary, tmp_path / "m.pdf")).split())
    assert esperado in texto
    assert re.search(r"Escala numérica: 1:\d{1,3}(\.\d{3})+", texto)


@pytest.mark.parametrize("caso", ["gleba_rural_exemplo", "30v", "grande"])
@pytest.mark.parametrize("legenda", ["nenhuma", "lateral", "inferior"])
def test_escala_numerica_junto_da_barra_sem_sobrepor(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    prancha = Prancha(legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    numerica = _caixa(_escala_numerica(layout))
    [barra] = [_caixa(b) for b in _itens(layout, QgsLayoutItemScaleBar)]
    # Na coluna direita, logo acima da barra (até 5 mm).
    assert COLUNA_DIREITA[0] <= numerica[0] and numerica[2] <= COLUNA_DIREITA[1], numerica
    assert numerica[3] <= barra[1] and barra[1] - numerica[3] <= 5, (numerica, barra)
    outros = [_caixa(r) for r in _rotulos(layout) if r is not _escala_numerica(layout)]
    outros += [_caixa(i) for i in layout.items()
               if isinstance(i, (QgsLayoutItemScaleBar, QgsLayoutItemShape, QgsLayoutItemMap))
               and not i.id().startswith("Moldura")]
    for caixa in outros:
        assert not _intersectam(numerica, caixa), (numerica, caixa)
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    # Nenhuma palavra da escala numérica encosta em outra (a barra de área grande é tratada à parte).
    sobrepostas = pdf_verif.sobrepostas(pdf)
    assert not [par for par in sobrepostas if {"Escala", "numérica:"} & {par[0].texto, par[1].texto}], sobrepostas
    if caso != "grande":
        assert sobrepostas == []


def test_barra_grafica_de_area_grande_sem_rotulos_sobrepostos(qgis_app, fixtures_dir, tmp_path):
    pdf = export_map_pdf(_summary(fixtures_dir, tmp_path, "grande"), tmp_path / "m.pdf")
    assert pdf_verif.sobrepostas(pdf) == []
    assert [p.texto for p in _rotulos_da_barra(pdf)][-1] == "km"


# ---- Escala gráfica: metros ou quilômetros, rótulos separados e legíveis --------

# Lado do quadrado em graus: de ~55 m (1:540) a ~220 km (1:2.200.000), passando pelas faixas em que
# os rótulos em metros se fundiam ("1,2001,600" em 1:32.000) ou se sobrepunham.
LADOS = [0.0005, 0.002, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.3, 0.8, 2.0]


def _quadrado(destino, lado):
    x0, y0 = -45.0 - lado / 2, -15.0 - lado / 2  # fuso 23S
    anel = [[x0, y0], [x0 + lado, y0], [x0 + lado, y0 + lado], [x0, y0 + lado], [x0, y0]]
    destino.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [anel]}}]}), encoding="utf-8")
    return destino


def _rotulos_da_barra(pdf):
    """Palavras da faixa de rótulos da barra (coluna direita, logo abaixo da escala numérica)."""
    return [p for p in pdf_verif.palavras(pdf)
            if p.x0 >= 216 * pdf_verif.MM and 154 * pdf_verif.MM <= p.y0 <= 162 * pdf_verif.MM]


def _numero_pt(valor):
    """Número como o leitor brasileiro espera: vírgula decimal, ponto de milhar, sem zeros à direita."""
    texto = f"{round(valor, 6):,.6f}".rstrip("0").rstrip(".")
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


@pytest.mark.parametrize("lado", LADOS)
def test_barra_grafica_em_metros_ou_km_com_rotulos_separados(qgis_app, tmp_path, lado):
    summary = process(load_input(_quadrado(tmp_path / "q.geojson", lado)))
    projeto, layout = montar_mapa(summary)
    [mapa] = _itens(layout, QgsLayoutItemMap)
    [barra] = _itens(layout, QgsLayoutItemScaleBar)
    assert barra.linkedMap() is mapa
    segmento, unidade, segmentos = barra.unitsPerSegment(), barra.unitLabel(), barra.numberOfSegments()
    total_m = segmento * segmentos * (1000 if unidade == "km" else 1)
    # Metros enquanto nenhum rótulo chega a 4 dígitos; a partir de 1 000 m, quilômetros.
    assert unidade == ("km" if total_m >= 1000 else "m"), (unidade, total_m)
    esperados = [_numero_pt(segmento * i) for i in range(segmentos + 1)] + [unidade]
    denominador = round(mapa.scale())
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf")
    rotulos = _rotulos_da_barra(pdf)
    # Cada rótulo é uma palavra própria: rótulos encostados viram uma palavra só no PDF.
    assert [p.texto for p in rotulos] == esperados
    assert pdf_verif.sobrepostas(pdf) == []
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    # A barra mede o que diz: distância entre os rótulos "0" e o último, no papel, vezes a escala.
    centro = [(p.x0 + p.x1) / 2 for p in rotulos[:segmentos + 1]]
    no_papel_mm = (centro[-1] - centro[0]) / pdf_verif.MM
    assert no_papel_mm * denominador / 1000 == pytest.approx(total_m, rel=0.02)
