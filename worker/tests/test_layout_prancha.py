"""Prancha personalizada no mapa.pdf: título, responsável, logo, cores e legenda lateral."""

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
)

from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf, montar_mapa
from geolume_worker.prancha import Prancha, normalizar_logo
from geolume_worker.processing import process

TITULO = "GeoLume — Mapa de Localização"
PAGINA = (297, 210)
LOGO_X = (237, 287)
TOPO_DO_MAPA = 25


@pytest.fixture
def summary(fixtures_dir):
    return process(load_input(fixtures_dir / "gleba_rural_exemplo.geojson"))


def _texto(pdf, *opcoes):
    return subprocess.run(["pdftotext", *opcoes, str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def _junto(texto):
    return "".join(texto.split())


def _imagens(pdf):
    saida = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True, check=True).stdout
    return len(saida.splitlines()) - 2  # cabeçalho de duas linhas


def _itens(layout, tipo):
    return [item for item in layout.items() if isinstance(item, tipo) and not item.id().startswith("Moldura")]


def _rotulos(layout):
    return {item.currentText(): item for item in _itens(layout, QgsLayoutItemLabel)}


def _caixa(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


def _logo(tmp_path, tamanho=(400, 100)):
    import io
    buffer = io.BytesIO()
    Image.new("RGB", tamanho, (20, 90, 160)).save(buffer, "PNG")
    destino = tmp_path / "logo.png"
    normalizar_logo(buffer.getvalue(), destino)
    return destino


# ---- Sem personalização: a prancha de hoje ------------------------------------


def test_sem_prancha_a_saida_e_a_de_hoje(qgis_app, summary, tmp_path):
    hoje = export_map_pdf(summary, tmp_path / "hoje.pdf")
    padrao = export_map_pdf(summary, tmp_path / "padrao.pdf", prancha=Prancha())
    assert _texto(hoje, "-layout") == _texto(padrao, "-layout")
    texto = _texto(padrao)
    assert TITULO in texto
    assert "Responsável" not in texto and "Legenda" not in texto
    assert _imagens(padrao) == 0


def test_projeto_vazio_mantem_o_titulo_atual(qgis_app, summary):
    _, layout = montar_mapa(summary, prancha=Prancha(responsavel="Eng. Ana Souza"))
    assert TITULO in _rotulos(layout)


# ---- Textos --------------------------------------------------------------------


def test_titulo_com_nome_do_projeto(qgis_app, summary, tmp_path):
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=Prancha(projeto="Loteamento Sol"))
    texto = _texto(pdf)
    assert "Loteamento Sol — Mapa de Localização" in texto
    assert TITULO not in texto


def test_responsavel_tecnico_aparece(qgis_app, summary, tmp_path):
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=Prancha(responsavel="Eng. Ana Souza — CREA 123"))
    assert "Responsável técnico: Eng. Ana Souza — CREA 123" in _texto(pdf)


def test_textos_da_prancha_sao_literais(qgis_app, summary, tmp_path):
    prancha = Prancha(projeto="[% env('PATH') %]", responsavel="[% 1+1 %]")
    _, layout = montar_mapa(summary, prancha=prancha)
    rotulos = _rotulos(layout)
    assert "[% env('PATH') %] — Mapa de Localização" in rotulos
    assert "Responsável técnico: [% 1+1 %]" in rotulos
    texto = _junto(_texto(export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)))
    assert "[%env('PATH')%]—MapadeLocalização" in texto
    assert "Responsáveltécnico:[%1+1%]" in texto
    assert "/usr/" not in texto


@pytest.mark.parametrize("valor", ["Loteamento " * 9, "W" * 100, "ç" * 100], ids=["palavras", "sem-espaco", "acentos"])
def test_textos_de_100_caracteres_cabem_no_cabecalho(qgis_app, summary, tmp_path, valor):
    valor = valor.strip()
    prancha = Prancha(projeto=valor, responsavel=valor)
    _, layout = montar_mapa(summary, prancha=prancha, logo=_logo(tmp_path))
    rotulos = _rotulos(layout)
    for texto in (f"{valor} — Mapa de Localização", f"Responsável técnico: {valor}"):
        x0, y0, x1, y1 = _caixa(rotulos[texto])
        assert x0 >= 10 and x1 <= LOGO_X[0], (texto, x1)  # não invade a logo
        assert y1 <= TOPO_DO_MAPA, (texto, y1)  # não invade o mapa
    # Nada cortado: o PDF tem o texto inteiro.
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert _junto(_texto(pdf)).count(_junto(valor)) == 2


# ---- Cores ---------------------------------------------------------------------


def _simbolo(project):
    camada = next(iter(project.mapLayers().values()))
    return camada.renderer().symbol()


def test_cores_padrao_sao_as_de_hoje(qgis_app, summary):
    project, _ = montar_mapa(summary)
    simbolo = _simbolo(project)
    assert simbolo.color().name() == "#ffc800" and simbolo.color().alpha() == 89
    assert simbolo.symbolLayer(0).strokeColor().name() == "#c80000"


def test_cores_da_prancha_no_simbolo(qgis_app, summary):
    project, _ = montar_mapa(summary, prancha=Prancha(cor_contorno="#1A2B3C", cor_preenchimento="#00FF7F"))
    simbolo = _simbolo(project)
    assert simbolo.color().name() == "#00ff7f" and simbolo.color().alpha() == 89
    assert simbolo.symbolLayer(0).strokeColor().name() == "#1a2b3c"


def _pixels(pdf, tmp_path):
    subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", str(pdf), str(tmp_path / "raster")], check=True)
    with Image.open(tmp_path / "raster.png") as imagem:
        return imagem.convert("RGB").getdata()


def _hex(cor):
    return tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))


def _tem(pixels, alvo, tolerancia=4):
    return any(all(abs(p - a) <= tolerancia for p, a in zip(pixel, alvo)) for pixel in pixels)


@pytest.mark.parametrize("contorno,preenchimento", [("#C80000", "#FFC800"), ("#1A2B3C", "#00FF7F")])
def test_pdf_renderizado_usa_as_cores_da_prancha(qgis_app, summary, tmp_path, contorno, preenchimento):
    prancha = Prancha(cor_contorno=contorno, cor_preenchimento=preenchimento)
    pixels = _pixels(export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha), tmp_path)
    alfa = 89 / 255  # alfa8(0.35)
    sobre_branco = tuple(round(255 * (1 - alfa) + c * alfa) for c in _hex(preenchimento))
    assert _tem(pixels, _hex(contorno))
    assert _tem(pixels, sobre_branco)


# ---- Legenda lateral -----------------------------------------------------------


def test_legenda_lateral_abaixo_da_escala_com_as_cores_da_prancha(qgis_app, summary, tmp_path):
    prancha = Prancha(cor_contorno="#1A2B3C", cor_preenchimento="#00FF7F", legenda="lateral")
    _, layout = montar_mapa(summary, prancha=prancha)
    rotulos = _rotulos(layout)
    assert {"Legenda", "Limite do imóvel"} <= set(rotulos)
    [amostra] = _itens(layout, QgsLayoutItemShape)
    simbolo = amostra.symbol()
    assert simbolo.color().name() == "#00ff7f" and simbolo.color().alpha() == 89
    assert simbolo.symbolLayer(0).strokeColor().name() == "#1a2b3c"
    [escala] = _itens(layout, QgsLayoutItemScaleBar)
    fim_da_escala = _caixa(escala)[3]
    for item in (amostra, rotulos["Legenda"], rotulos["Limite do imóvel"]):
        x0, y0, x1, y1 = _caixa(item)
        assert x0 >= 218 and x1 <= PAGINA[0] - 5 and y0 >= fim_da_escala and y1 <= PAGINA[1] - 5
    assert "Limite do imóvel" in _texto(export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha))


def test_sem_legenda_nao_desenha_amostra(qgis_app, summary):
    _, layout = montar_mapa(summary, prancha=Prancha(legenda="nenhuma"))
    assert not _itens(layout, QgsLayoutItemShape)
    assert "Legenda" not in _rotulos(layout)


# ---- Logo ----------------------------------------------------------------------


@pytest.mark.parametrize("tamanho", [(400, 100), (100, 400), (300, 300)])
def test_logo_no_canto_superior_direito_sem_distorcer(qgis_app, summary, tmp_path, tamanho):
    logo = _logo(tmp_path, tamanho)
    _, layout = montar_mapa(summary, prancha=Prancha(logo=True), logo=logo)
    figuras = [f for f in _itens(layout, QgsLayoutItemPicture) if f.picturePath() == str(logo)]
    assert len(figuras) == 1
    figura = figuras[0]
    assert figura.resizeMode() == QgsLayoutItemPicture.ResizeMode.Zoom  # mantém a proporção
    x0, y0, x1, y1 = _caixa(figura)
    assert LOGO_X[0] <= x0 and x1 <= LOGO_X[1] and y0 >= 5 and y1 <= TOPO_DO_MAPA - 2
    [mapa] = _itens(layout, QgsLayoutItemMap)
    assert y1 <= _caixa(mapa)[1]
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=Prancha(logo=True), logo=logo)
    assert _imagens(pdf) == 1


# ---- Estilo e alfa do preenchimento ------------------------------------------------

from qgis.core import QgsNetworkAccessManager, QgsRasterLayer, QgsUnitTypes  # noqa: E402

import pdf_verif  # noqa: E402
from geolume_worker.camadas import ESTILOS, alfa8, estilo_por_id  # noqa: E402
from geolume_worker.prancha import validar_prancha  # noqa: E402

A4_PAISAGEM_PT = (841.89, 595.28)
MARGEM_PT = 5 * pdf_verif.MM


def _assert_simbolo(simbolo, prancha):
    camada = simbolo.symbolLayer(0)
    estilo = estilo_por_id(prancha.estilo)
    assert simbolo.color().name() == prancha.cor_preenchimento.lower()
    assert simbolo.color().alpha() == alfa8(prancha.alfa_preenchimento)
    assert camada.strokeColor().name() == prancha.cor_contorno.lower()
    assert camada.strokeColor().alpha() == 255  # contorno sempre opaco
    assert camada.strokeWidth() == pytest.approx(estilo.espessura_mm)
    assert camada.strokeWidthUnit() == QgsUnitTypes.RenderUnit.RenderMillimeters


@pytest.mark.parametrize("estilo", ESTILOS, ids=lambda e: e.id)
def test_simbolo_do_estilo(qgis_app, summary, estilo):
    prancha = Prancha(estilo=estilo.id, cor_contorno=estilo.contorno, cor_preenchimento=estilo.preenchimento)
    project, _ = montar_mapa(summary, prancha=prancha)
    _assert_simbolo(_simbolo(project), prancha)


@pytest.mark.parametrize("alfa,esperado", [(0, 0), (0.35, 89), (0.6, 153), (1, 255)])
def test_alfa_do_preenchimento_no_simbolo(qgis_app, summary, alfa, esperado):
    project, _ = montar_mapa(summary, prancha=Prancha(alfa_preenchimento=alfa))
    simbolo = _simbolo(project)
    assert simbolo.color().alpha() == esperado
    assert simbolo.symbolLayer(0).strokeColor().alpha() == 255


def test_estilo_tecnico_com_cor_personalizada(qgis_app, summary):
    prancha = validar_prancha({"estilo": "tecnico", "cor_contorno": "#112233"})
    project, _ = montar_mapa(summary, prancha=prancha)
    camada = _simbolo(project).symbolLayer(0)
    assert camada.strokeColor().name() == "#112233"  # a cor escolhida vence a do estilo
    assert camada.strokeWidth() == pytest.approx(0.35)  # a espessura vem do estilo


@pytest.mark.parametrize("gravada", [None, {}, {"projeto": None, "responsavel": None, "cor_contorno": "#C80000",
                                                "cor_preenchimento": "#FFC800", "legenda": "lateral", "logo": False}],
                         ids=["sem-prancha", "vazia", "antiga"])
def test_job_antigo_usa_estilo_e_alfa_padrao(qgis_app, summary, gravada):
    prancha = validar_prancha(gravada)
    project, _ = montar_mapa(summary, prancha=prancha)
    simbolo = _simbolo(project)
    assert simbolo.color().alpha() == 89
    assert simbolo.symbolLayer(0).strokeWidth() == pytest.approx(0.6)
    assert simbolo.symbolLayer(0).strokeColor().name() == "#c80000"


def test_sem_prancha_e_o_mesmo_simbolo_da_prancha_padrao(qgis_app, summary):
    sem, com = montar_mapa(summary), montar_mapa(summary, prancha=Prancha())  # projetos vivos: donos do símbolo
    a, b = _simbolo(sem[0]), _simbolo(com[0])
    assert (a.color(), a.symbolLayer(0).strokeWidth()) == (b.color(), b.symbolLayer(0).strokeWidth())


@pytest.mark.parametrize("legenda", ["lateral", "inferior"])
@pytest.mark.parametrize("estilo", ESTILOS, ids=lambda e: e.id)
def test_legenda_amostra_igual_ao_simbolo_exportado(qgis_app, summary, legenda, estilo):
    prancha = Prancha(estilo=estilo.id, cor_contorno=estilo.contorno, cor_preenchimento=estilo.preenchimento,
                      alfa_preenchimento=0.6, legenda=legenda)
    project, layout = montar_mapa(summary, prancha=prancha)
    [amostra] = _itens(layout, QgsLayoutItemShape)  # uma entrada: a única camada exportada
    _assert_simbolo(amostra.symbol(), prancha)
    _assert_simbolo(_simbolo(project), prancha)


def test_projeto_so_com_a_camada_vetorial_em_memoria(qgis_app, summary, tmp_path):
    project, layout = montar_mapa(summary, prancha=Prancha(estilo="pb", alfa_preenchimento=1, legenda="lateral"))
    camadas = list(project.mapLayers().values())
    assert len(camadas) == 1
    assert camadas[0].providerType() == "memory"
    assert not any(isinstance(c, QgsRasterLayer) for c in camadas)
    [mapa] = _itens(layout, QgsLayoutItemMap)
    assert [c.id() for c in mapa.layers()] == [camadas[0].id()]


def test_pdf_sem_nenhuma_requisicao_de_rede(qgis_app, summary, tmp_path, monkeypatch):
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
        for estilo in ESTILOS:
            prancha = Prancha(estilo=estilo.id, alfa_preenchimento=0.6, legenda="inferior")
            pdf = export_map_pdf(summary, tmp_path / f"{estilo.id}.pdf", prancha=prancha)
            assert pdf_verif.paginas(pdf) == 1
    finally:
        gerenciador.requestAboutToBeCreated.disconnect(requisicoes.append)
    assert requisicoes == []


def _tamanho_pagina(pdf):
    import re
    saida = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    largura, altura = map(float, re.search(r"^Page size:\s+([\d.]+) x ([\d.]+) pts", saida, re.M).groups())
    return largura, altura


@pytest.mark.parametrize("alfa", [0, 0.35, 1])
def test_pdf_com_alfa_0_035_e_1(qgis_app, summary, tmp_path, alfa):
    contorno, preenchimento = "#1A2B3C", "#00FF7F"
    prancha = Prancha(cor_contorno=contorno, cor_preenchimento=preenchimento, alfa_preenchimento=alfa,
                      estilo="tecnico", legenda="lateral")
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    assert pdf_verif.paginas(pdf) == 1
    assert _tamanho_pagina(pdf) == pytest.approx(A4_PAISAGEM_PT, abs=0.5)
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    assert pdf_verif.sobrepostas(pdf) == []
    texto = pdf_verif.texto(pdf)
    for trecho in ("Tabela de vértices", "Vértice", "Limite do imóvel", "SIRGAS 2000"):
        assert trecho in texto
    pixels = _pixels(pdf, tmp_path)
    assert _tem(pixels, _hex(contorno))  # contorno visível mesmo com preenchimento invisível
    fracao = alfa8(alfa) / 255
    sobre_branco = tuple(round(255 * (1 - fracao) + c * fracao) for c in _hex(preenchimento))
    if alfa == 0:
        assert not _tem(pixels, tuple(round(255 * 0.6 + c * 0.4) for c in _hex(preenchimento)), tolerancia=10)
    else:
        assert _tem(pixels, sobre_branco)


def test_pdf_tem_norte_e_escala_em_todos_os_estilos(qgis_app, summary, tmp_path):
    from qgis.core import QgsLayoutItemScaleBar
    for estilo in ESTILOS:
        _, layout = montar_mapa(summary, prancha=Prancha(estilo=estilo.id, legenda="lateral"))
        setas = [f for f in _itens(layout, QgsLayoutItemPicture) if f.picturePath().endswith("rosa_dos_ventos.svg")]
        assert len(setas) == 1 and len(_itens(layout, QgsLayoutItemScaleBar)) == 1
