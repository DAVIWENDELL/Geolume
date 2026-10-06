"""Rosa dos ventos da prancha: SVG local versionado, norte destacado para cima, N/S/L/O, sem sobreposição nem rede."""

import json
import re
import subprocess

import pytest

pytest.importorskip("qgis.core")

from PIL import Image
from qgis.core import (
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsNetworkAccessManager,
)

import pdf_verif
from geolume_worker.input_loader import load_input
from geolume_worker.layout import ROSA_DOS_VENTOS, export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha
from geolume_worker.processing import process

MM = pdf_verif.MM
MARGEM = 5
PAGINA = (297, 210)
COLUNA_DIREITA = (218, 287)
LEGENDAS = ("nenhuma", "lateral", "inferior")
CASOS = ("gleba_rural_exemplo", "lote_boa_vista", "lote_minusculo", "grande")
CARDEAIS = ("N", "S", "L", "O")
GMS = re.compile(r"^\d{1,3}°\d{2}'\d{2}")


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


def _centro(caixa):
    return (caixa[0] + caixa[2]) / 2, (caixa[1] + caixa[3]) / 2


def _rosa(layout):
    rosas = [f for f in _itens(layout, QgsLayoutItemPicture) if f.picturePath() == str(ROSA_DOS_VENTOS)]
    assert len(rosas) == 1
    return rosas[0]


def _cardeais_no_pdf(pdf, caixa_rosa, folga=8):
    """Palavras N/S/L/O isoladas em volta da rosa (caixa em mm), em mm."""
    x0, y0, x1, y1 = caixa_rosa
    achadas = {}
    for p in pdf_verif.palavras(pdf):
        caixa = (p.x0 / MM, p.y0 / MM, p.x1 / MM, p.y1 / MM)
        if p.texto in CARDEAIS and x0 - folga <= caixa[0] and caixa[2] <= x1 + folga \
                and y0 - folga <= caixa[1] and caixa[3] <= y1 + folga:
            assert p.texto not in achadas, p.texto
            achadas[p.texto] = caixa
    return achadas


def _raster(pdf, tmp_path, dpi=150):
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", str(pdf), str(tmp_path / "raster")], check=True)
    return Image.open(tmp_path / "raster.png").convert("RGB")


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


# ---- Recurso local -------------------------------------------------------------------


def test_rosa_e_svg_local_versionado_sem_recurso_externo():
    assert ROSA_DOS_VENTOS.is_file()
    assert ROSA_DOS_VENTOS.suffix == ".svg"
    assert "geolume_worker" in ROSA_DOS_VENTOS.parts  # dentro do pacote, copiado na imagem Docker
    svg = ROSA_DOS_VENTOS.read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg") or svg.lstrip().startswith("<?xml")
    svg = svg.replace('xmlns="http://www.w3.org/2000/svg"', "", 1)  # identificador de namespace, não é acesso à rede
    for proibido in ("http:", "https:", "href", "<image", "url(", "@import", "<text", "base64"):
        assert proibido not in svg, proibido  # sem rede, sem imagem embutida, sem fonte do sistema
    assert 'id="norte"' in svg


# ---- Layout --------------------------------------------------------------------------


def test_rosa_substitui_seta_e_fica_vinculada_ao_mapa_norte_para_cima(qgis_app, fixtures_dir, tmp_path):
    projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, "gleba_rural_exemplo"))
    [mapa] = _itens(layout, QgsLayoutItemMap)
    rosa = _rosa(layout)
    assert not [f for f in _itens(layout, QgsLayoutItemPicture) if "NorthArrow" in f.picturePath()]
    assert rosa.linkedMap() is mapa
    assert mapa.mapRotation() == 0 and rosa.pictureRotation() == 0  # mapa norte para cima, rosa sem giro
    largura, altura = rosa.sizeWithUnits().width(), rosa.sizeWithUnits().height()
    assert largura == pytest.approx(altura)  # rosa circular, sem distorção
    assert largura >= 20
    del layout, projeto


@pytest.mark.parametrize("caso", CASOS)
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_rosa_e_cardeais_sem_sobrepor_nada_dentro_da_coluna(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda)
    projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, caso), prancha=prancha)
    rosa = _rosa(layout)
    caixa_rosa = _caixa(rosa)
    rotulos = {r.currentText(): r for r in _itens(layout, QgsLayoutItemLabel)}
    cardeais = {letra: _caixa(rotulos[letra]) for letra in CARDEAIS}
    conjunto = [caixa_rosa, *cardeais.values()]
    for caixa in conjunto:
        assert COLUNA_DIREITA[0] <= caixa[0] and caixa[2] <= COLUNA_DIREITA[1], caixa
        assert MARGEM <= caixa[1] and caixa[3] <= PAGINA[1] - MARGEM, caixa
    proprios = {id(rosa), *(id(rotulos[letra]) for letra in CARDEAIS)}
    outros = [(type(i).__name__, _caixa(i)) for i in layout.items()
              if hasattr(i, "positionWithUnits") and id(i) not in proprios
              and type(i).__name__ != "QgsLayoutItemPage"]
    assert any(nome == "QgsLayoutItemMap" for nome, _ in outros)
    for caixa in conjunto:
        for nome, outra in outros:  # mapa, título, logo, resumo, escalas, legenda, tabela, quadro
            assert not _intersectam(caixa, outra), (nome, caixa, outra)
    for a in conjunto[1:]:
        assert not _intersectam(a, caixa_rosa) or a is caixa_rosa
    # Orientação: N acima, S abaixo, O à esquerda, L à direita, alinhados ao centro da rosa.
    cx, cy = _centro(caixa_rosa)
    assert cardeais["N"][3] <= caixa_rosa[1] + 0.5 and cardeais["S"][1] >= caixa_rosa[3] - 0.5
    assert cardeais["O"][2] <= caixa_rosa[0] + 0.5 and cardeais["L"][0] >= caixa_rosa[2] - 0.5
    for letra in ("N", "S"):
        assert _centro(cardeais[letra])[0] == pytest.approx(cx, abs=0.5)
    for letra in ("L", "O"):
        assert _centro(cardeais[letra])[1] == pytest.approx(cy, abs=0.5)
    del layout, projeto


# ---- PDF -----------------------------------------------------------------------------


@pytest.mark.parametrize("caso", CASOS)
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_pdf_com_rosa_limpo_e_demais_elementos_preservados(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    caixa_rosa = _caixa(_rosa(layout))
    assert len(_itens(layout, QgsLayoutItemScaleBar)) == 1
    assert _itens(layout, QgsLayoutItemMap)[0].grids().size() == 1
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM * MM) == []
    assert pdf_verif.sobrepostas(pdf) == []
    cardeais = _cardeais_no_pdf(pdf, caixa_rosa)
    assert set(cardeais) == set(CARDEAIS)
    cx, cy = _centro(caixa_rosa)
    assert _centro(cardeais["N"])[1] < cy < _centro(cardeais["S"])[1]
    assert _centro(cardeais["O"])[0] < cx < _centro(cardeais["L"])[0]
    texto = pdf_verif.texto(pdf)
    for trecho in ("Escala numérica: 1:", "Tabela de vértices", "Fonte da geometria", "SIRGAS 2000"):
        assert trecho in texto
    assert any(GMS.match(p.texto) for p in pdf_verif.palavras(pdf))  # rótulos da grade DMS continuam


@pytest.mark.parametrize("legenda", LEGENDAS)
def test_rosa_desenhada_com_norte_destacado_para_cima(qgis_app, fixtures_dir, tmp_path, legenda):
    summary = _summary(fixtures_dir, tmp_path, "gleba_rural_exemplo")
    prancha = Prancha(legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    x0, y0, x1, y1 = _caixa(_rosa(layout))
    del layout, projeto
    imagem = _raster(export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha), tmp_path)
    px = 150 / 25.4
    recorte = imagem.crop((round(x0 * px), round(y0 * px), round(x1 * px), round(y1 * px)))
    largura, altura = recorte.size
    escuros, vermelhos = [], []
    for indice, (r, g, b) in enumerate(recorte.getdata()):
        ponto = divmod(indice, largura)[::-1]
        if r < 90 and g < 90 and b < 90:
            escuros.append(ponto)
        elif r > 140 and g < 90 and b < 90:
            vermelhos.append(ponto)
    assert len(escuros) > 200  # a rosa foi de fato desenhada no PDF
    assert {y < altura / 2 for _, y in escuros} == {True, False}  # pontas e marcações dos dois lados
    assert len(vermelhos) > 50
    assert all(y < altura / 2 for _, y in vermelhos)  # o norte destacado aponta para cima
    assert sum(x for x, _ in vermelhos) / len(vermelhos) == pytest.approx(largura / 2, abs=largura * 0.08)


def test_rosa_gerada_sem_rede(qgis_app, fixtures_dir, tmp_path, sem_rede):
    for caso in ("gleba_rural_exemplo", "lote_boa_vista"):
        summary = _summary(fixtures_dir, tmp_path, caso)
        projeto, layout = montar_mapa(summary)
        caixa_rosa = _caixa(_rosa(layout))
        del layout, projeto
        pdf = export_map_pdf(summary, tmp_path / f"{caso}.pdf")
        assert set(_cardeais_no_pdf(pdf, caixa_rosa)) == set(CARDEAIS)
