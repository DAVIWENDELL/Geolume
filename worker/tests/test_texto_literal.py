"""Regressão: texto do GeoJSON (e do usuário) nunca é avaliado como expressão QGIS nos PDFs."""

import json
import subprocess

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsLayoutItemLabel, QgsPrintLayout, QgsProject

from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf
from geolume_worker.memorial import export_memorial_pdf
from geolume_worker.processing import process
from geolume_worker.texto import texto_literal

MALICIOSOS = [
    "[% 1+1 %]",
    "[% env('PATH') %]",
    "[%env('HOME')%]",
    "[[% 1+1 %]]",
    "[% '%]' %]",
    "a[%]b",
    "[%",
    "%]",
    "[",
    "colchetes [x] e 100% [% ok",
]


def _avaliado(texto: str) -> str:
    project = QgsProject()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    label = QgsLayoutItemLabel(layout)
    label.setText(texto)
    layout.addLayoutItem(label)
    return label.currentText()


@pytest.mark.parametrize("valor", MALICIOSOS)
def test_texto_literal_sai_igual_ao_valor(qgis_app, valor):
    assert _avaliado(texto_literal(valor)) == valor


def test_texto_sem_colchete_nao_muda():
    assert texto_literal("Sítio Boa Vista — 100% (lote 3)") == "Sítio Boa Vista — 100% (lote 3)"


def _geojson_malicioso(fixtures_dir, tmp_path):
    dados = json.loads((fixtures_dir / "lote_simples.geojson").read_text(encoding="utf-8"))
    feicao = dados["features"][0] if dados.get("type") == "FeatureCollection" else dados
    feicao["properties"] = {
        "nome_imovel": "Imóvel [% 1+1 %]",
        "proprietario": "[% env('PATH') %]",
        "municipio": "[% length(env('HOME')) %]",
        "uf": "DF",
        "matricula": "M-[% 2*3 %]",
    }
    caminho = tmp_path / "malicioso.geojson"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return process(load_input(caminho))


def _texto(pdf):
    return subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def _sem_espacos(texto: str) -> str:
    return "".join(texto.split())


@pytest.mark.parametrize("exportar", [export_map_pdf, export_memorial_pdf], ids=["mapa", "memorial"])
def test_pdf_mostra_expressoes_literalmente(qgis_app, fixtures_dir, tmp_path, exportar):
    summary = _geojson_malicioso(fixtures_dir, tmp_path)
    texto = _sem_espacos(_texto(exportar(summary, tmp_path / "saida.pdf")))
    for valor in ("Imóvel[%1+1%]", "[%env('PATH')%]", "M-[%2*3%]"):
        assert valor in texto
    assert "/usr/" not in texto  # nada do ambiente do worker
    assert "Imóvel2" not in texto and "M-6" not in texto


def test_uma_linha_preserva_zwj_e_zwnj_legitimos():
    from geolume_worker.texto import uma_linha

    familia = "Família \U0001f469‍\U0001f469‍\U0001f467"
    assert uma_linha(familia) == familia
    assert uma_linha("mi‌nha") == "mi‌nha"
    assert uma_linha("a‎‏؜‮b") == "ab"  # controles bidi continuam saindo
