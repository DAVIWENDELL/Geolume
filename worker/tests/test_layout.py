import subprocess

import pytest

pytest.importorskip("qgis.core")

from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf
from geolume_worker.processing import process


def _pdf(fixtures_dir, tmp_path, arquivo="lote_simples.geojson"):
    summary = process(load_input(fixtures_dir / arquivo))
    return export_map_pdf(summary, tmp_path / "mapa.pdf")


def _texto(pdf):
    return subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def test_pdf_gerado(qgis_app, fixtures_dir, tmp_path):
    pdf = _pdf(fixtures_dir, tmp_path)
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert pdf.stat().st_size > 1024


def test_pdf_contem_texto(qgis_app, fixtures_dir, tmp_path):
    texto = _texto(_pdf(fixtures_dir, tmp_path))
    assert "GeoLume" in texto
    assert "31983" in texto


def test_pdf_contem_dados_do_imovel(qgis_app, fixtures_dir, tmp_path):
    texto = _texto(_pdf(fixtures_dir, tmp_path, "gleba_rural_exemplo.geojson"))
    assert "Sítio Boa Vista" in texto
    assert "Brasília" in texto
    assert "EXEMPLO-0001" in texto
    assert "Tabela de vértices" in texto
    assert "V1" in texto
    assert "Azimute" in texto


def test_pdf_uma_pagina(qgis_app, fixtures_dir, tmp_path):
    info = subprocess.run(["pdfinfo", str(_pdf(fixtures_dir, tmp_path))], capture_output=True, text=True, check=True)
    paginas = [linha.split()[-1] for linha in info.stdout.splitlines() if linha.startswith("Pages:")]
    assert paginas == ["1"]


def test_pdf_poligono_minusculo(qgis_app, fixtures_dir, tmp_path):
    assert "GeoLume" in _texto(_pdf(fixtures_dir, tmp_path, "lote_minusculo.geojson"))
