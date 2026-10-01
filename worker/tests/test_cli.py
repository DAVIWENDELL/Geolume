import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "geolume_worker", *args], capture_output=True, text=True)


def test_cli_sucesso(fixtures_dir, tmp_path):
    proc = _cli("run", "--input", str(fixtures_dir / "lote_simples.geojson"), "--output", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    saida = json.loads(proc.stdout.strip().splitlines()[-1])
    assert saida["status"] == "ok"
    assert "inicializacao_qgis" in saida["fases_ms"]
    assert Path(saida["pdf"]).is_file()


def test_cli_entrada_invalida(fixtures_dir, tmp_path):
    proc = _cli("run", "--input", str(fixtures_dir / "linha.geojson"), "--output", str(tmp_path))
    assert proc.returncode == 2
    erro = json.loads(proc.stderr.strip().splitlines()[-1])
    assert erro == {"status": "erro", "codigo": "geometria_nao_poligonal", "mensagem": erro["mensagem"]}


def test_cli_sem_argumentos():
    assert _cli().returncode != 0
