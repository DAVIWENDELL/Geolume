"""Moldura cartográfica externa da prancha: linha dupla fora da área útil, sem tocar no conteúdo nem sair da página."""

import io
import json
import subprocess

import pytest

pytest.importorskip("qgis.core")

from PIL import Image
from qgis.core import (
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsNetworkAccessManager,
)

import pdf_verif
from geolume_worker.input_loader import load_input
from geolume_worker.layout import MAPA_ALTURA, MAPA_LARGURA, ROSA_DOS_VENTOS, export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha, normalizar_logo
from geolume_worker.processing import process

MM = pdf_verif.MM
PAGINA = (297, 210)
MARGEM = 5  # área útil: nenhum conteúdo antes de 5 mm da borda
LEGENDAS = ("nenhuma", "lateral", "inferior")
CASOS = ("lote_minusculo", "gleba_rural_exemplo", "grande")


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


def _logo(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 100), (20, 90, 160)).save(buffer, "PNG")
    destino = tmp_path / "logo.png"
    normalizar_logo(buffer.getvalue(), destino)
    return destino


def _molduras(layout):
    return sorted((s for s in layout.items() if isinstance(s, QgsLayoutItemShape) and s.id().startswith("Moldura")),
                  key=lambda s: s.id())


def _caixa(item):
    """Retângulo ocupado na página (mm), incluindo rótulos da grade e espessura do contorno."""
    r = item.mapRectToScene(item.boundingRect())
    return r.left(), r.top(), r.right(), r.bottom()


def _retangulo(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


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


# ---- Layout --------------------------------------------------------------------------


def test_moldura_dupla_externa_sem_preenchimento(qgis_app, fixtures_dir, tmp_path):
    projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, "gleba_rural_exemplo"))
    externa, interna = _molduras(layout)
    assert (externa.id(), interna.id()) == ("Moldura externa", "Moldura interna")
    for moldura in (externa, interna):
        assert moldura.shapeType() == QgsLayoutItemShape.Shape.Rectangle
        assert moldura.symbol().color().alpha() == 0  # só contorno: não cobre nada
    espessura = lambda m: m.symbol().symbolLayer(0).strokeWidth()  # noqa: E731
    assert espessura(externa) > espessura(interna) > 0  # traço externo forte, interno fino
    ex, inn = _retangulo(externa), _retangulo(interna)
    assert ex[0] < inn[0] and ex[1] < inn[1] and ex[2] > inn[2] and ex[3] > inn[3]
    # A moldura do próprio mapa continua, com o mesmo tamanho útil.
    [mapa] = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
    assert mapa.frameEnabled()
    assert (mapa.sizeWithUnits().width(), mapa.sizeWithUnits().height()) == (MAPA_LARGURA, MAPA_ALTURA)
    del layout, projeto


@pytest.mark.parametrize("caso", CASOS)
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_moldura_cerca_todo_conteudo_sem_tocar_e_dentro_da_pagina(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda)
    projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, caso), prancha=prancha, logo=_logo(tmp_path))
    externa, interna = _molduras(layout)
    for moldura in (externa, interna):
        x0, y0, x1, y1 = _caixa(moldura)
        assert 0 < x0 and 0 < y0 and x1 < PAGINA[0] and y1 < PAGINA[1], (moldura.id(), x0, y0, x1, y1)
    lim = _caixa(interna)
    conteudo = [i for i in layout.items()
                if hasattr(i, "positionWithUnits") and type(i).__name__ != "QgsLayoutItemPage"
                and not (isinstance(i, QgsLayoutItemShape) and i.id().startswith("Moldura"))]
    tipos = {type(i).__name__ for i in conteudo}
    for tipo in ("QgsLayoutItemMap", "QgsLayoutItemScaleBar", "QgsLayoutItemPicture", "QgsLayoutItemLabel"):
        assert tipo in tipos
    for item in conteudo:
        # O boundingRect do mapa reserva a extensão máxima teórica da grade (até fora da página); os rótulos
        # reais da grade são conferidos no PDF (fora_da_margem). Aqui vale a moldura do mapa.
        x0, y0, x1, y1 = _retangulo(item) if isinstance(item, QgsLayoutItemMap) else _caixa(item)
        assert lim[0] + 0.5 <= x0 and x1 <= lim[2] - 0.5, (type(item).__name__, (x0, x1), lim)
        assert lim[1] + 0.5 <= y0 and y1 <= lim[3] - 0.5, (type(item).__name__, (y0, y1), lim)
    del layout, projeto


# ---- PDF -----------------------------------------------------------------------------


def _linhas_escuras(imagem, eixo, posicao_mm, dpi):
    """Fração de pixels escuros numa faixa de ±0,3 mm em torno de x ou y = posicao_mm."""
    px = dpi / 25.4
    largura, altura = imagem.size
    faixa = range(round((posicao_mm - 0.3) * px), round((posicao_mm + 0.3) * px) + 1)
    if eixo == "x":
        amostras = [(x, y) for y in range(round(10 * px), round(200 * px), 3) for x in faixa]
        total = len(range(round(10 * px), round(200 * px), 3))
    else:
        amostras = [(x, y) for x in range(round(10 * px), round(287 * px), 3) for y in faixa]
        total = len(range(round(10 * px), round(287 * px), 3))
    escuros = {(c if eixo == "y" else r) for (c, r) in amostras if sum(imagem.getpixel((c, r))) < 3 * 110}
    return len(escuros) / total


@pytest.mark.parametrize("caso", CASOS)
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_pdf_com_moldura_limpo_e_elementos_preservados(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    externa, interna = (_retangulo(m) for m in _molduras(layout))
    assert len([i for i in layout.items() if isinstance(i, QgsLayoutItemScaleBar)]) == 1
    assert [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)][0].grids().size() == 1
    assert [f for f in layout.items() if isinstance(f, QgsLayoutItemPicture)
            and f.picturePath() == str(ROSA_DOS_VENTOS)]
    rotulos = {r.currentText() for r in layout.items() if isinstance(r, QgsLayoutItemLabel)}
    assert {"N", "S", "L", "O"} <= rotulos
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM * MM) == []
    assert pdf_verif.sobrepostas(pdf) == []
    texto = pdf_verif.texto(pdf)
    for trecho in ("Escala numérica: 1:", "Tabela de vértices", "Fonte da geometria", "SIRGAS 2000"):
        assert trecho in texto

    dpi = 100
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", str(pdf), str(tmp_path / "r")], check=True)
    with Image.open(tmp_path / "r.png") as bruta:
        imagem = bruta.convert("RGB")
    # As quatro linhas de cada moldura aparecem de ponta a ponta no PDF renderizado.
    for x0, y0, x1, y1 in (externa, interna):
        for eixo, posicao in (("x", x0), ("x", x1), ("y", y0), ("y", y1)):
            assert _linhas_escuras(imagem, eixo, posicao, dpi) > 0.95, (eixo, posicao)


def test_moldura_gerada_sem_rede(qgis_app, fixtures_dir, tmp_path, sem_rede):
    for caso in ("gleba_rural_exemplo", "lote_boa_vista"):
        summary = _summary(fixtures_dir, tmp_path, caso)
        pdf = export_map_pdf(summary, tmp_path / f"{caso}.pdf")
        assert pdf_verif.paginas(pdf) == 1
        projeto, layout = montar_mapa(summary)
        assert len(_molduras(layout)) == 2
        del layout, projeto
