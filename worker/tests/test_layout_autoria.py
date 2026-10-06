"""Identificação de autoria no rodapé do mapa.pdf: marca local do GeoLume e "Gerado pelo GeoLume", sem selo técnico."""

import io
import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from PIL import Image
from qgis.core import QgsLayoutItemLabel, QgsLayoutItemMap, QgsLayoutItemPicture, QgsNetworkAccessManager

import pdf_verif
from geolume_worker.input_loader import load_input
from geolume_worker.layout import MARCA_GEOLUME, TEXTO_AUTORIA, export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha, normalizar_logo
from geolume_worker.processing import process

MM = pdf_verif.MM
PAGINA = (297, 210)
LIMITE_INFERIOR = 205
COLUNA_DIREITA = (218, 287)
LEGENDAS = ("nenhuma", "lateral", "inferior")
CASOS = ("lote_minusculo", "gleba_rural_exemplo", "grande")
ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"
LARANJA_DA_MARCA = (210, 119, 26)  # pino de localização da marca


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


def _logo_cliente(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 100), (20, 90, 160)).save(buffer, "PNG")
    destino = tmp_path / "logo.png"
    normalizar_logo(buffer.getvalue(), destino)
    return destino


def _caixa(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


def _intersectam(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _autoria(layout):
    marcas = [f for f in layout.items() if isinstance(f, QgsLayoutItemPicture) and f.picturePath() == str(MARCA_GEOLUME)]
    textos = [r for r in layout.items() if isinstance(r, QgsLayoutItemLabel) and r.currentText() == TEXTO_AUTORIA]
    assert len(marcas) == 1 and len(textos) == 1
    return marcas[0], textos[0]


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


# ---- Recurso local e texto -----------------------------------------------------------


def test_marca_e_o_arquivo_oficial_versionado_e_o_texto_e_so_autoria():
    assert MARCA_GEOLUME == ASSETS / "geolume-marca-transparente.png"
    assert MARCA_GEOLUME.is_file()
    with Image.open(MARCA_GEOLUME) as imagem:
        assert imagem.format == "PNG" and imagem.mode == "RGBA"
    assert TEXTO_AUTORIA == "Gerado pelo GeoLume"
    proibidos = ("selo", "crea", "certific", "aprova", "garant", "técnico", "oficial")
    assert not any(p in TEXTO_AUTORIA.lower() for p in proibidos)


# ---- Layout --------------------------------------------------------------------------


@pytest.mark.parametrize("com_logo", [False, True], ids=["sem-logo", "logo-cliente"])
@pytest.mark.parametrize("caso", CASOS)
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_autoria_no_rodape_sem_sobrepor_nada(qgis_app, fixtures_dir, tmp_path, legenda, caso, com_logo):
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda, logo=com_logo)
    logo = _logo_cliente(tmp_path) if com_logo else None
    projeto, layout = montar_mapa(_summary(fixtures_dir, tmp_path, caso), prancha=prancha, logo=logo)
    marca, texto = _autoria(layout)
    caixa_marca, caixa_texto = _caixa(marca), _caixa(texto)
    [mapa] = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
    fim_mapa = _caixa(mapa)[3]
    quadro = _caixa(next(r for r in layout.items()
                         if isinstance(r, QgsLayoutItemLabel) and r.currentText().startswith("SRC:")))
    for caixa in (caixa_marca, caixa_texto):
        assert COLUNA_DIREITA[0] <= caixa[0] and caixa[2] <= COLUNA_DIREITA[1], caixa
        assert caixa[1] >= fim_mapa + 30 and caixa[3] <= LIMITE_INFERIOR, caixa  # rodapé, dentro da área útil
        assert caixa[0] >= quadro[2] + 5  # ao lado do quadro de fontes, sem encostar
        assert caixa[1] < quadro[3] and caixa[3] > quadro[1]  # na mesma faixa do quadro de fontes
    assert caixa_texto[0] >= caixa_marca[2]  # texto à direita da marca
    centro = lambda c: (c[1] + c[3]) / 2  # noqa: E731
    assert centro(caixa_texto) == pytest.approx(centro(caixa_marca), abs=0.5)
    assert 6 <= marca.sizeWithUnits().height() <= 10  # discreta
    proprios = {id(marca), id(texto)}
    outros = [(type(i).__name__, _caixa(i)) for i in layout.items()
              if hasattr(i, "positionWithUnits") and id(i) not in proprios
              and type(i).__name__ != "QgsLayoutItemPage" and not i.id().startswith("Moldura")]
    for caixa in (caixa_marca, caixa_texto):
        for nome, outra in outros:  # mapa, título, logo, rosa, resumo, escalas, legenda, tabela, quadro
            assert not _intersectam(caixa, outra), (nome, caixa, outra)
    # A logo do cliente segue no canto superior direito, independente da marca do GeoLume.
    clientes = [f for f in layout.items() if isinstance(f, QgsLayoutItemPicture) and logo and f.picturePath() == str(logo)]
    assert len(clientes) == (1 if com_logo else 0)
    if com_logo:
        x0, y0, x1, y1 = _caixa(clientes[0])
        assert y1 <= _caixa(mapa)[1] and x0 >= 237
    del layout, projeto


# ---- PDF -----------------------------------------------------------------------------


@pytest.mark.parametrize("caso", CASOS)
@pytest.mark.parametrize("legenda", LEGENDAS)
def test_pdf_mostra_gerado_pelo_geolume_dentro_da_pagina(qgis_app, fixtures_dir, tmp_path, legenda, caso):
    summary = _summary(fixtures_dir, tmp_path, caso)
    prancha = Prancha(projeto="Loteamento Sol", responsavel="Eng. Ana Souza", legenda=legenda)
    projeto, layout = montar_mapa(summary, prancha=prancha)
    marca, texto = _autoria(layout)
    x0, y0, x1, y1 = _caixa(marca)
    caixa_texto = _caixa(texto)
    del layout, projeto, marca, texto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, 5 * MM) == []
    assert pdf_verif.sobrepostas(pdf) == []
    conteudo = pdf_verif.texto(pdf)
    assert conteudo.count(TEXTO_AUTORIA) == 1
    for proibido in ("Selo", "CREA", "Certific", "Aprovado", "Garant"):
        assert proibido not in conteudo
    # As palavras da linha de autoria saem no PDF dentro do quadro do rótulo (e nada mais cai ali).
    palavras = [p for p in pdf_verif.palavras(pdf)
                if caixa_texto[0] - 0.5 <= p.x0 / MM and p.x1 / MM <= caixa_texto[2] + 0.5
                and caixa_texto[1] - 0.5 <= p.y0 / MM and p.y1 / MM <= caixa_texto[3] + 0.5]
    assert [p.texto for p in palavras] == ["Gerado", "pelo", "GeoLume"]
    # A marca foi de fato desenhada: o laranja do pino aparece dentro do quadro da marca.
    dpi = 200
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", str(pdf), str(tmp_path / "r")], check=True)
    px = dpi / 25.4
    with Image.open(tmp_path / "r.png") as bruta:
        recorte = bruta.convert("RGB").crop((round(x0 * px), round(y0 * px), round(x1 * px), round(y1 * px)))
    laranjas = sum(all(abs(c - a) <= 40 for c, a in zip(pixel, LARANJA_DA_MARCA)) for pixel in recorte.getdata())
    assert laranjas > 20


def test_autoria_gerada_sem_rede(qgis_app, fixtures_dir, tmp_path, sem_rede):
    for caso in ("gleba_rural_exemplo", "lote_boa_vista"):
        pdf = export_map_pdf(_summary(fixtures_dir, tmp_path, caso), tmp_path / f"{caso}.pdf")
        assert TEXTO_AUTORIA in pdf_verif.texto(pdf)
