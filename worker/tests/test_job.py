import json
import subprocess
import sys

import pytest

pytest.importorskip("qgis.core")

from geolume_worker.errors import InvalidInputError
from geolume_worker.job import FASES, run_job


def test_run_job_gera_artefatos(qgis_app, fixtures_dir, tmp_path):
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path)
    assert result.pdf_path == tmp_path / result.job_id / "mapa.pdf"
    assert result.pdf_path.is_file()
    dados = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert dados["versao_esquema"] == 1
    assert dados["crs_saida"] == "EPSG:31983"
    assert dados["entrada"] == "lote_simples.geojson"
    assert len(dados["vertices"]) == 4
    assert set(dados["vertices"][0]) == {"id", "e", "n", "azimute", "distancia_m"}
    assert list(result.phases_ms) == list(FASES)
    assert dados["metricas"]["fases_ms"] == result.phases_ms
    assert result.peak_rss_mb > 0


def test_run_job_job_id_explicito(qgis_app, fixtures_dir, tmp_path):
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc")
    assert result.job_id == "abc"
    assert (tmp_path / "abc" / "resultado.json").is_file()


def test_run_job_cria_output_dir(qgis_app, fixtures_dir, tmp_path):
    destino = tmp_path / "nao" / "existe"
    assert run_job(fixtures_dir / "lote_simples.geojson", destino).pdf_path.is_file()


def test_run_job_entrada_invalida_nao_deixa_pasta(qgis_app, fixtures_dir, tmp_path):
    with pytest.raises(InvalidInputError):
        run_job(fixtures_dir / "linha.geojson", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_dois_jobs_na_mesma_sessao(qgis_app, fixtures_dir, tmp_path):
    a = run_job(fixtures_dir / "lote_simples.geojson", tmp_path)
    b = run_job(fixtures_dir / "lote_boa_vista.geojson", tmp_path)
    assert a.job_id != b.job_id
    assert json.loads(b.json_path.read_text(encoding="utf-8"))["crs_saida"] == "EPSG:31975"


def test_run_job_sem_sessao(fixtures_dir, tmp_path):
    codigo = (
        "from pathlib import Path; from geolume_worker.job import run_job; "
        f"run_job(Path(r'{fixtures_dir / 'lote_simples.geojson'}'), Path(r'{tmp_path}'))"
    )
    proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "sessao QGIS nao inicializada" in proc.stderr


# ---- Prancha personalizada ---------------------------------------------------


def _pdf_texto(pdf):
    return subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def _pdf_imagens(pdf):
    """Imagens raster além da marca do GeoLume (sempre presente: imagem + máscara alfa)."""
    saida = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True, check=True).stdout
    return len(saida.splitlines()) - 2 - 2


def _gravar_logo(tmp_path, job_id):
    import io

    from PIL import Image

    from geolume_worker.prancha import caminho_logo, normalizar_logo

    buffer = io.BytesIO()
    Image.new("RGB", (300, 100), (20, 90, 160)).save(buffer, "PNG")
    destino = caminho_logo(tmp_path, job_id)
    destino.parent.mkdir(parents=True, exist_ok=True)
    normalizar_logo(buffer.getvalue(), destino)
    return destino


def test_run_job_sem_prancha_mantem_o_mapa_de_hoje(qgis_app, fixtures_dir, tmp_path):
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path, prancha=None)
    texto = _pdf_texto(result.pdf_path)
    assert "GeoLume — Mapa de Localização" in texto and "Responsável" not in texto


def test_run_job_aplica_a_prancha_so_no_mapa(qgis_app, fixtures_dir, tmp_path):
    prancha = {"projeto": "Loteamento Sol", "responsavel": "Eng. Ana", "legenda": "lateral"}
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha=prancha)
    mapa = _pdf_texto(result.pdf_path)
    assert "Loteamento Sol — Mapa de Localização" in mapa
    assert "Responsável técnico: Eng. Ana" in mapa and "Limite do imóvel" in mapa
    memorial = _pdf_texto(result.memorial_path)
    assert "Loteamento Sol" not in memorial and "Eng. Ana" not in memorial  # memorial inalterado


def test_run_job_revalida_a_prancha(qgis_app, fixtures_dir, tmp_path):
    with pytest.raises(InvalidInputError) as erro:
        run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"cor_contorno": "red"})
    assert erro.value.codigo == "cor_invalida"
    assert not (tmp_path / "abc").exists()


def test_run_job_usa_a_logo_do_job(qgis_app, fixtures_dir, tmp_path):
    _gravar_logo(tmp_path, "abc")
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"logo": True})
    assert _pdf_imagens(result.pdf_path) == 1


def test_run_job_ignora_logo_quando_a_prancha_nao_pede(qgis_app, fixtures_dir, tmp_path):
    _gravar_logo(tmp_path, "abc")
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"projeto": "X"})
    assert _pdf_imagens(result.pdf_path) == 0


def test_run_job_logo_ausente_falha_sem_pdf(qgis_app, fixtures_dir, tmp_path):
    with pytest.raises(InvalidInputError) as erro:
        run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"logo": True})
    assert erro.value.codigo == "logo_ausente"
    assert not (tmp_path / "abc").exists()


@pytest.mark.parametrize("adulteracao", ["svg", "link", "grande"])
def test_run_job_recusa_logo_adulterada(qgis_app, fixtures_dir, tmp_path, adulteracao):
    from PIL import Image

    from geolume_worker.prancha import caminho_logo

    destino = caminho_logo(tmp_path, "abc")
    destino.parent.mkdir(parents=True)
    if adulteracao == "svg":
        destino.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    elif adulteracao == "link":
        destino.symlink_to(_gravar_logo(tmp_path, "outro"))
    else:
        Image.new("RGB", (1500, 10)).save(destino, "PNG")
    with pytest.raises(InvalidInputError) as erro:
        run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"logo": True})
    assert erro.value.codigo == "logo_invalida"
    assert not (tmp_path / "abc").exists()


def test_run_job_com_legenda_inferior(qgis_app, fixtures_dir, tmp_path):
    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"legenda": "inferior"})
    assert "Limite do imóvel" in _pdf_texto(result.pdf_path)


def test_run_job_recusa_legenda_desconhecida(qgis_app, fixtures_dir, tmp_path):
    with pytest.raises(InvalidInputError) as erro:
        run_job(fixtures_dir / "lote_simples.geojson", tmp_path, job_id="abc", prancha={"legenda": "topo"})
    assert erro.value.codigo == "legenda_invalida"
    assert not (tmp_path / "abc").exists()


def test_estilo_e_alfa_nao_mudam_o_memorial(qgis_app, fixtures_dir, tmp_path):
    entrada = fixtures_dir / "lote_simples.geojson"
    padrao = run_job(entrada, tmp_path, job_id="padrao")
    estilizado = run_job(entrada, tmp_path, job_id="estilo",
                         prancha={"estilo": "pb", "alfa_preenchimento": 0, "legenda": "lateral"})

    def texto(pdf):
        return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True,
                              check=True).stdout

    assert texto(estilizado.memorial_path) == texto(padrao.memorial_path)
    assert "Legenda" not in texto(estilizado.memorial_path)
