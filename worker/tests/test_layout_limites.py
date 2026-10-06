"""mapa.pdf sem clipping: uma página, nada fora da margem de 5 mm, nada sobreposto."""

import re
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsLayoutItemLabel

import pdf_verif
import poligonos
from geolume_worker.input_loader import load_input
from geolume_worker.layout import MARCA_GEOLUME, TEXTO_AUTORIA, export_map_pdf, montar_mapa
from geolume_worker.processing import process

MARGEM_PT = 5 * pdf_verif.MM
LIMITE_INFERIOR = 205
COLUNA_DIREITA_FIM = 287
AVISO = re.compile(r"Exibidos (\d+) de (\d+) vértices\. Demais vértices no memorial descritivo\.")
REFERENCIA = Path(__file__).parent / "fixtures" / "mapa_padrao_6v.txt"
PREFIXOS_PROPRIEDADES = ("Imóvel:", "Proprietário:", "Município:", "UF:", "Matrícula:", "Tipo:")


def _summary(tmp_path, n, propriedades=None):
    return process(load_input(poligonos.gerar(tmp_path / f"p{n}.geojson", n, propriedades)))


def _rotulos(layout):
    return [item for item in layout.items() if isinstance(item, QgsLayoutItemLabel)]


def _caixa(item):
    pos, tam = item.positionWithUnits(), item.sizeWithUnits()
    return pos.x(), pos.y(), pos.x() + tam.width(), pos.y() + tam.height()


def _rotulo(layout, inicio):
    encontrados = [r for r in _rotulos(layout) if r.currentText().startswith(inicio)]
    return encontrados[0] if encontrados else None


def _palavras(pdf):
    return {p.texto for p in pdf_verif.palavras(pdf)}


def _assert_pdf_limpo(pdf):
    assert pdf_verif.paginas(pdf) == 1
    assert pdf_verif.fora_da_margem(pdf, MARGEM_PT) == []
    assert pdf_verif.sobrepostas(pdf) == []


# ---- Tabela de vértices limitada à área segura --------------------------------


@pytest.mark.parametrize("n", [14, 16, 30])
def test_tabela_com_muitos_vertices_cabe_na_pagina(qgis_app, tmp_path, n):
    summary = _summary(tmp_path, n)
    _, layout = montar_mapa(summary)
    assert _caixa(_rotulo(layout, "Vértice"))[3] <= LIMITE_INFERIOR
    aviso = _rotulo(layout, "Exibidos")
    assert aviso is not None
    assert _caixa(aviso)[3] <= LIMITE_INFERIOR

    pdf = export_map_pdf(summary, tmp_path / "m.pdf")
    _assert_pdf_limpo(pdf)
    exibidos, total = map(int, AVISO.search(" ".join(pdf_verif.texto(pdf).split())).groups())
    assert total == n and 1 <= exibidos < n
    palavras = _palavras(pdf)
    assert {f"V{i}" for i in range(1, exibidos + 1)} <= palavras
    assert f"V{exibidos + 1}" not in palavras


def test_seis_vertices_sem_aviso(qgis_app, fixtures_dir, tmp_path):
    summary = process(load_input(fixtures_dir / "gleba_rural_exemplo.geojson"))
    pdf = export_map_pdf(summary, tmp_path / "m.pdf")
    assert "Exibidos" not in pdf_verif.texto(pdf)
    assert pdf_verif.texto(pdf, "-layout") == REFERENCIA.read_text(encoding="utf-8")
    _assert_pdf_limpo(pdf)


def test_limite_exato_da_tabela(qgis_app, tmp_path):
    sem_aviso = []
    for n in range(10, 17):
        projeto, layout = montar_mapa(_summary(tmp_path, n))  # o projeto precisa viver enquanto o layout é lido
        if _rotulo(layout, "Exibidos") is None:
            sem_aviso.append(n)
        del layout, projeto
    assert sem_aviso, "nenhum tamanho sem aviso entre 10 e 16"
    maior = max(sem_aviso)
    assert sem_aviso == list(range(10, maior + 1))  # monotônico: a partir de um ponto sempre há aviso
    projeto, layout = montar_mapa(_summary(tmp_path, maior))
    assert _caixa(_rotulo(layout, "Vértice"))[3] <= LIMITE_INFERIOR
    assert f"V{maior}" in _rotulo(layout, "Vértice").currentText()
    projeto, layout = montar_mapa(_summary(tmp_path, maior + 1))
    assert _rotulo(layout, "Exibidos") is not None
    assert _caixa(_rotulo(layout, "Exibidos"))[3] <= LIMITE_INFERIOR


# ---- Propriedades longas cortadas no mapa (inteiras no memorial) ---------------


LONGOS = {
    "com_espacos": ("Fazenda Santa Maria do Rio Grande " * 20)[:500],
    "sem_espacos": "W" * 500,
    "expressao": "[% env('PATH') %]" * 30,
}


@pytest.mark.parametrize("caso", LONGOS)
def test_propriedade_longa_cortada(qgis_app, tmp_path, caso):
    valor = LONGOS[caso]
    summary = _summary(tmp_path, 6, {"nome_imovel": valor, "proprietario": valor, "municipio": "Brasília", "uf": "DF"})
    _, layout = montar_mapa(summary)
    propriedades = [r for r in _rotulos(layout) if r.currentText().startswith(PREFIXOS_PROPRIEDADES)]
    assert len(propriedades) == 4
    for rotulo in propriedades:
        assert _caixa(rotulo)[2] <= COLUNA_DIREITA_FIM, rotulo.currentText()
    assert _rotulo(layout, "Imóvel:").currentText().endswith("…")
    assert _rotulo(layout, "UF:").currentText() == "UF: DF"

    pdf = export_map_pdf(summary, tmp_path / "m.pdf")
    _assert_pdf_limpo(pdf)
    if caso == "expressao":
        junto = "".join(pdf_verif.texto(pdf).split())
        assert "[%env('PATH')%]" in junto
        assert "/usr/" not in junto


CONTROLES = {
    "quebras": "Fazenda\n" * 40,
    "cr_tab": "Fazenda\r\tBoa\r\n\x0bVista\x0c" * 10,
    "bidi": "Fazenda \u202eairavaF\u202c \u2066Boa\u2069 Vista",
}


@pytest.mark.parametrize("caso", CONTROLES)
def test_propriedade_com_controles_fica_em_uma_linha(qgis_app, tmp_path, caso):
    """Valor do GeoJSON com quebra de linha, controle ou bidi não empurra os outros rótulos."""
    valor = CONTROLES[caso]
    summary = _summary(tmp_path, 6, {"nome_imovel": valor, "proprietario": valor, "municipio": valor, "uf": "DF"})
    _, layout = montar_mapa(summary)
    propriedades = [r for r in _rotulos(layout) if r.currentText().startswith(PREFIXOS_PROPRIEDADES)]
    assert len(propriedades) == 4
    altura_uma_linha = _caixa(_rotulo(layout, "UF:"))[3] - _caixa(_rotulo(layout, "UF:"))[1]
    for rotulo in propriedades:
        texto = rotulo.currentText()
        assert not any(unicodedata.category(c) in ("Cc", "Cf") for c in texto), repr(texto)
        x0, y0, x1, y1 = _caixa(rotulo)
        assert y1 - y0 <= altura_uma_linha + 0.01, repr(texto)
        assert x1 <= COLUNA_DIREITA_FIM
    caixas = sorted(_caixa(r) for r in propriedades)
    for a, b in zip(caixas, caixas[1:]):
        assert a[3] <= b[1], (a, b)
    _assert_pdf_limpo(export_map_pdf(summary, tmp_path / "m.pdf"))


# ---- Legenda inferior e matriz de combinações ----------------------------------

import io  # noqa: E402
import itertools  # noqa: E402
import subprocess  # noqa: E402

from PIL import Image  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
)

from geolume_worker.prancha import Prancha, normalizar_logo  # noqa: E402

FAIXA_INFERIOR = (117, 210, 153, 205)
CORES = ("#1A2B3C", "#00FF7F")


def _logo(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 100), (20, 90, 160)).save(buffer, "PNG")
    destino = tmp_path / "logo.png"
    normalizar_logo(buffer.getvalue(), destino)
    return destino


def _itens(layout, tipo):
    return [item for item in layout.items() if isinstance(item, tipo) and not item.id().startswith("Moldura")]


def _uniao(caixas):
    caixas = list(caixas)
    if not caixas:
        return None
    return (min(c[0] for c in caixas), min(c[1] for c in caixas), max(c[2] for c in caixas), max(c[3] for c in caixas))


def _grupos(layout, logo):
    rotulos = _rotulos(layout)

    def com(*inicios):
        return _uniao(_caixa(r) for r in rotulos if r.currentText().startswith(inicios))

    figuras = _itens(layout, QgsLayoutItemPicture)
    grupos = {
        "cabecalho": _uniao(_caixa(r) for r in rotulos
                            if "Mapa de Localização" in r.currentText() or r.currentText().startswith("Responsável")),
        "logo": _uniao(_caixa(f) for f in figuras if logo and f.picturePath() == str(logo)),
        "norte": _uniao(_caixa(f) for f in figuras
                        if not (logo and f.picturePath() == str(logo)) and f.picturePath() != str(MARCA_GEOLUME)),
        "autoria": _uniao([_caixa(f) for f in figuras if f.picturePath() == str(MARCA_GEOLUME)]
                          + [_caixa(r) for r in rotulos if r.currentText() == TEXTO_AUTORIA]),
        "mapa": _uniao(_caixa(m) for m in _itens(layout, QgsLayoutItemMap)),
        "propriedades": com(*PREFIXOS_PROPRIEDADES),
        "resumo": com("SIRGAS"),
        "coordenadas": com("SRC:"),
        "escala": _uniao(_caixa(e) for e in _itens(layout, QgsLayoutItemScaleBar)),
        "escala_numerica": com("Escala numérica"),
        "tabela": com("Tabela de vértices", "Vértice ", "Exibidos"),
        "legenda": _uniao([_caixa(r) for r in rotulos if r.currentText() in ("Legenda", "Limite do imóvel")]
                          + [_caixa(s) for s in _itens(layout, QgsLayoutItemShape)]),
    }
    return {nome: caixa for nome, caixa in grupos.items() if caixa is not None}


def _intersectam(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def test_legenda_inferior_na_faixa_inferior(qgis_app, tmp_path):
    summary = _summary(tmp_path, 30)
    prancha = Prancha(cor_contorno=CORES[0], cor_preenchimento=CORES[1], legenda="inferior")
    projeto, layout = montar_mapa(summary, prancha=prancha)
    [amostra] = _itens(layout, QgsLayoutItemShape)
    rotulos = {r.currentText(): r for r in _rotulos(layout)}
    fim_tabela = _caixa(rotulos[next(t for t in rotulos if t.startswith("Vértice "))])[2]
    fim_mapa = _caixa(_itens(layout, QgsLayoutItemMap)[0])[3]
    for item in (amostra, rotulos["Legenda"], rotulos["Limite do imóvel"]):
        x0, y0, x1, y1 = _caixa(item)
        assert FAIXA_INFERIOR[0] <= x0 and x1 <= FAIXA_INFERIOR[1], (x0, x1)
        assert FAIXA_INFERIOR[2] <= y0 and y1 <= FAIXA_INFERIOR[3], (y0, y1)
        assert x0 >= fim_tabela + 5 and y0 >= fim_mapa
    simbolo = amostra.symbol()
    assert simbolo.color().name() == "#00ff7f" and simbolo.symbolLayer(0).strokeColor().name() == "#1a2b3c"

    # No raster, a cor do contorno está dentro do retângulo da amostra.
    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha)
    subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", str(pdf), str(tmp_path / "r")], check=True)
    x0, y0, x1, y1 = (round(v * 150 / 25.4) for v in _caixa(amostra))
    with Image.open(tmp_path / "r.png") as imagem:
        recorte = imagem.convert("RGB").crop((x0 - 2, y0 - 2, x1 + 2, y1 + 2)).getdata()
    alvo = (0x1A, 0x2B, 0x3C)
    assert any(all(abs(p - a) <= 6 for p, a in zip(pixel, alvo)) for pixel in recorte)
    assert "Limite do imóvel" in pdf_verif.texto(pdf)


MATRIZ = list(itertools.product(("nenhuma", "lateral", "inferior"), (False, True), ("curtos", "longos"),
                                (6, 14, 30), ("normais", "longas")))


@pytest.mark.parametrize("indice", range(len(MATRIZ)),
                         ids=["-".join(map(str, (l, "logo" if g else "sem-logo", t, f"{n}v", p)))
                              for l, g, t, n, p in MATRIZ])
def test_matriz_sem_sobreposicao(qgis_app, tmp_path, indice):
    legenda, com_logo, textos, n, props = MATRIZ[indice]
    texto = ("Loteamento " * 10)[:100] if textos == "longos" else None
    cores = dict(cor_contorno=CORES[0], cor_preenchimento=CORES[1]) if indice % 2 else {}
    prancha = Prancha(projeto=texto or "Sol", responsavel=texto, legenda=legenda, logo=com_logo, **cores)
    logo = _logo(tmp_path) if com_logo else None
    valor = LONGOS["com_espacos"] if props == "longas" else "Sítio Boa Vista"
    summary = _summary(tmp_path, n, {"nome_imovel": valor, "proprietario": valor, "municipio": "Brasília",
                                     "uf": "DF", "matricula": "M-1", "tipo": "Gleba rural"})

    projeto, layout = montar_mapa(summary, prancha=prancha, logo=logo)
    grupos = _grupos(layout, logo)
    assert {"cabecalho", "norte", "mapa", "propriedades", "resumo", "coordenadas", "escala", "escala_numerica",
            "tabela"} <= set(grupos)
    assert ("legenda" in grupos) == (legenda != "nenhuma")
    assert ("logo" in grupos) == com_logo
    for nome, (x0, y0, x1, y1) in grupos.items():
        assert x0 >= 5 and y0 >= 5 and x1 <= 292 and y1 <= 205, (nome, (x0, y0, x1, y1))
    for (a, ca), (b, cb) in itertools.combinations(grupos.items(), 2):
        assert not _intersectam(ca, cb), (a, ca, b, cb)
    del layout, projeto

    pdf = export_map_pdf(summary, tmp_path / "m.pdf", prancha=prancha, logo=logo)
    _assert_pdf_limpo(pdf)
    conteudo = pdf_verif.texto(pdf)
    assert ("Limite do imóvel" in conteudo) == (legenda != "nenhuma")
    assert "Tabela de vértices" in conteudo


def test_propriedade_nula_ou_so_com_controles_e_omitida_no_mapa():
    from qgis.PyQt.QtCore import QVariant

    from geolume_worker.layout import _metadata_lines

    linhas = _metadata_lines({"nome_imovel": "X", "uf": QVariant(), "matricula": "‮‬", "tipo": " 	 "})
    assert linhas == ["Imóvel: X"]
