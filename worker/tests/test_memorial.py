"""memorial.pdf: textos longos dentro das margens e todos os vértices, com paginação."""

import pytest

pytest.importorskip("qgis.core")

import pdf_verif
import poligonos
from geolume_worker.input_loader import load_input
from geolume_worker.memorial import export_memorial_pdf
from geolume_worker.processing import process

MM = pdf_verif.MM
# Área útil 18–192 × 15–260 mm; o rodapé fica em y = 265 e termina antes de 272 mm.
MARGENS = dict(esquerda_pt=17.5 * MM, direita_pt=17.5 * MM, topo_pt=14.5 * MM, base_pt=25 * MM)
LONGOS = {
    "com_espacos": ("Fazenda Santa Maria do Rio Grande " * 20)[:500].strip(),
    "sem_espacos": "W" * 500,
}


def _summary(tmp_path, n, propriedades=None):
    return process(load_input(poligonos.gerar(tmp_path / f"p{n}.geojson", n, propriedades)))


def _fora(pdf):
    return pdf_verif.fora_da_margem(pdf, 0, **MARGENS)


@pytest.mark.parametrize("caso", LONGOS)
def test_textos_longos_dentro_da_margem(qgis_app, tmp_path, caso):
    valor = LONGOS[caso]
    summary = _summary(tmp_path, 6, {"nome_imovel": valor, "proprietario": valor, "municipio": valor, "uf": "DF"})
    pdf = export_memorial_pdf(summary, tmp_path / "memorial.pdf")
    assert _fora(pdf) == []
    assert pdf_verif.sobrepostas(pdf) == []
    junto = "".join(pdf_verif.texto(pdf).split())
    # Texto completo no memorial: sem reticências, o valor inteiro (nome aparece na identificação e na descrição).
    assert junto.count("".join(valor.split())) >= 4
    assert "…" not in junto


CONTROLES = {
    "quebras": "Fazenda\n" * 40,
    "cr_tab": "Fazenda\r\tBoa\r\n\x0bVista\x0c" * 10,
    "bidi": "Fazenda \u202eairavaF\u202c \u2066Boa\u2069 Vista",
}


@pytest.mark.parametrize("caso", CONTROLES)
def test_propriedade_com_controles_dentro_da_margem(qgis_app, tmp_path, caso):
    valor = CONTROLES[caso]
    summary = _summary(tmp_path, 6, {"nome_imovel": valor, "proprietario": valor, "municipio": valor, "uf": "DF"})
    pdf = export_memorial_pdf(summary, tmp_path / "memorial.pdf")
    assert _fora(pdf) == []
    assert pdf_verif.sobrepostas(pdf) == []
    assert pdf_verif.paginas(pdf) == 1
    texto = pdf_verif.texto(pdf)
    assert not any(c in texto for c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
    assert "Fazenda" in texto and "Vértice" in texto


# ---- Paginação: todos os vértices, cabeçalho repetido, nada órfão ---------------

import re  # noqa: E402

from qgis.core import QgsLayoutItemLabel  # noqa: E402

from geolume_worker.memorial import montar_memorial  # noqa: E402

RODAPE = "Documento preliminar gerado automaticamente pelo Geolume."
TITULOS = ("Quadro de vértices", "Quadro de vértices (continuação)", "Descrição perimetral")
_LINHA_TABELA = re.compile(r"V\d+")


def _linhas_da_tabela(pdf):
    """Palavras Vn no início da linha (x ≈ 18 mm): as linhas do quadro, não a menção a V1 no texto."""
    return [p for p in pdf_verif.palavras(pdf) if _LINHA_TABELA.fullmatch(p.texto) and p.x0 < 19 * MM]


@pytest.mark.parametrize("n", [6, 14, 16, 30, 300])
def test_todos_os_vertices_no_memorial(qgis_app, tmp_path, n):
    pdf = export_memorial_pdf(_summary(tmp_path, n), tmp_path / "memorial.pdf")
    linhas = _linhas_da_tabela(pdf)
    assert [p.texto for p in linhas] == [f"V{i}" for i in range(1, n + 1)]  # todos, em ordem, uma vez

    palavras = pdf_verif.palavras(pdf)
    [descricao] = [p for p in palavras if p.texto == "perimetral"]
    ultima = linhas[-1]
    assert (descricao.pagina, descricao.y0) > (ultima.pagina, ultima.y1)

    paginas = pdf_verif.paginas(pdf)
    for pagina in {p.pagina for p in linhas}:
        assert any(p.pagina == pagina and p.texto == "Vértice" for p in palavras), pagina  # cabeçalho repetido
    for pagina in range(1, paginas + 1):
        texto_pagina = " ".join(pdf_verif.texto(pdf, "-f", str(pagina), "-l", str(pagina)).split())
        assert RODAPE in texto_pagina, pagina
    assert _fora(pdf) == []
    assert pdf_verif.sobrepostas(pdf) == []
    if n <= 14:
        assert paginas == 1
    if n == 300:
        assert paginas > 1


def test_fontes_nao_diminuem(qgis_app, tmp_path):
    projeto, layout = montar_memorial(_summary(tmp_path, 300))
    tamanhos = {round(item.textFormat().size(), 1) for item in layout.items() if isinstance(item, QgsLayoutItemLabel)}
    assert tamanhos == {16, 12, 10, 8}


def test_seis_vertices_mesmas_posicoes(qgis_app, fixtures_dir):
    projeto, layout = montar_memorial(process(load_input(fixtures_dir / "gleba_rural_exemplo.geojson")))
    rotulos = {item.currentText(): item for item in layout.items() if isinstance(item, QgsLayoutItemLabel)}
    assert rotulos["Quadro de vértices"].positionWithUnits().y() == pytest.approx(88)
    assert rotulos["Descrição perimetral"].positionWithUnits().y() == pytest.approx(145)
    assert layout.pageCollection().pageCount() == 1


def _por_pagina(layout):
    paginas = {}
    for item in layout.items():
        if isinstance(item, QgsLayoutItemLabel) and item.currentText() != RODAPE:
            paginas.setdefault(item.page(), []).append(item)
    return paginas


def test_titulo_de_secao_nunca_orfao(qgis_app, tmp_path):
    for n in range(40, 121, 4):
        projeto, layout = montar_memorial(_summary(tmp_path, n))
        for pagina, itens in _por_pagina(layout).items():
            ultimo = max(itens, key=lambda item: item.pagePositionWithUnits().y())
            assert ultimo.currentText() not in TITULOS, (n, pagina, ultimo.currentText())
        del layout, projeto


def test_descricao_longa_continua_na_proxima_pagina(qgis_app, tmp_path):
    nome = ("Fazenda Santa Maria do Rio Grande " * 90).strip()
    summary = _summary(tmp_path, 30, {"nome_imovel": nome, "municipio": "Brasília", "uf": "DF"})
    pdf = export_memorial_pdf(summary, tmp_path / "memorial.pdf")
    assert pdf_verif.paginas(pdf) >= 2
    assert _fora(pdf) == []
    assert pdf_verif.sobrepostas(pdf) == []
    junto = "".join(pdf_verif.texto(pdf).split())
    assert junto.count("".join(nome.split())) == 2  # identificação e descrição, inteiras
    assert "…" not in junto


def test_identificacao_trata_none_e_valor_esvaziado_como_nao_informado(qgis_app, tmp_path):
    summary = _summary(tmp_path, 6, {"proprietario": None, "matricula": "‮‬", "municipio": " \t ", "uf": None})
    _projeto, layout = montar_memorial(summary)
    rotulos = "\n".join(item.currentText() for item in layout.items() if isinstance(item, QgsLayoutItemLabel))
    assert "Proprietário: Não informado" in rotulos
    assert "Matrícula: Não informada" in rotulos
    assert "Município/UF: Não informado/Não informado" in rotulos
    assert "None" not in rotulos and "NULL" not in rotulos
