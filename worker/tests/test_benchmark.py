import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "benchmark.py"


def _benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_summarize():
    resumo = _benchmark_module().summarize([10, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert resumo == {"min": 1, "mediana": 5.5, "p95": 10, "max": 10}


def test_benchmark_executa_e_rss_estavel(fixtures_dir, tmp_path):
    pytest.importorskip("qgis.core")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(fixtures_dir / "lote_simples.geojson"),
         "--runs", "5", "--output", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    [arquivo] = tmp_path.glob("benchmark-*.json")
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["runs"] == 5
    assert len(dados["rss_por_job_mb"]) == 5
    assert set(dados["fases_ms"]["total_job"]) == {"min", "mediana", "p95", "max"}
    assert isinstance(dados["meta_60s_atingida"], bool)
    # Sem vazamento relevante entre jobs quentes (o 1º job ainda aquece caches).
    assert dados["rss_por_job_mb"][-1] - dados["rss_por_job_mb"][1] < 50
