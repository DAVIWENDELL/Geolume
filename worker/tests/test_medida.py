"""Medição de texto pelo rótulo QGIS real: base para nada sair da página no mapa e no memorial."""

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsPrintLayout, QgsProject

from geolume_worker.medida import cortar_para_caber, linhas_que_cabem, medir, quebrar_por_largura

LONGO_COM_ESPACOS = ("Fazenda Santa Maria do Rio Grande " * 20)[:500]
LONGO_SEM_ESPACOS = "W" * 500


@pytest.fixture
def layout(qgis_app):
    projeto = QgsProject()
    layout = QgsPrintLayout(projeto)
    layout.initializeDefaults()
    yield layout


def test_medir_cresce_com_o_texto_e_com_a_fonte(layout):
    curto, alto = medir(layout, "abc", 10)
    assert curto > 0 and alto > 0
    assert medir(layout, "abcabc", 10)[0] > curto
    assert medir(layout, "abc", 20)[0] > curto
    assert medir(layout, "a\nb", 10)[1] > alto


@pytest.mark.parametrize("texto", [LONGO_COM_ESPACOS, LONGO_SEM_ESPACOS], ids=["com-espacos", "sem-espacos"])
def test_quebrar_por_largura_nenhuma_linha_passa(layout, texto):
    linhas = quebrar_por_largura(layout, texto, 10, 174)
    assert len(linhas) > 1
    for linha in linhas:
        assert medir(layout, linha, 10)[0] <= 174, linha
    assert "".join("".join(linhas).split()) == "".join(texto.split())  # nada perdido


def test_quebrar_texto_curto_fica_intacto(layout):
    assert quebrar_por_largura(layout, "Imóvel: Sítio Boa Vista", 10, 174) == ["Imóvel: Sítio Boa Vista"]


def test_quebrar_preserva_paragrafos(layout):
    assert quebrar_por_largura(layout, "Um.\n\nDois.", 10, 174) == ["Um.", "", "Dois."]


def test_cortar_texto_curto_fica_intacto(layout):
    assert cortar_para_caber(layout, "UF: DF", 8, 69) == "UF: DF"


@pytest.mark.parametrize("texto", [LONGO_COM_ESPACOS, LONGO_SEM_ESPACOS], ids=["com-espacos", "sem-espacos"])
def test_cortar_texto_longo_termina_em_reticencias_e_cabe(layout, texto):
    cortado = cortar_para_caber(layout, texto, 8, 69)
    assert cortado.endswith("…")
    assert texto.startswith(cortado[:-1])
    assert medir(layout, cortado, 8)[0] <= 69
    # O maior prefixo possível: um caractere a mais já não cabe.
    assert medir(layout, texto[:len(cortado)] + "…", 8)[0] > 69


def test_cortar_expressao_qgis_continua_literal(layout):
    texto = "[% 1+1 %]" * 60
    cortado = cortar_para_caber(layout, texto, 8, 69)
    assert cortado.startswith("[% 1+1 %]") and cortado.endswith("…")


def test_linhas_que_cabem_e_o_maior_k(layout):
    cabecalho = ["Vértice        E (m)          N (m)        Azimute       Dist. (m)"]
    linhas = [f"V{i:<7} {100000.0:>12.2f} {8000000.0:>12.2f} {'45°00′00″':>14} {10.0:>10.2f}" for i in range(1, 60)]
    k = linhas_que_cabem(layout, cabecalho, linhas, 7, 45)
    assert 0 < k < len(linhas)
    assert medir(layout, "\n".join(cabecalho + linhas[:k]), 7)[1] <= 45
    assert medir(layout, "\n".join(cabecalho + linhas[:k + 1]), 7)[1] > 45
    assert linhas_que_cabem(layout, cabecalho, linhas[:3], 7, 45) == 3
