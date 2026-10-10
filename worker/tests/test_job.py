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


def test_artefatos_reais_do_qgis_passam_na_integridade(qgis_app, fixtures_dir, tmp_path):
    import integridade

    result = run_job(fixtures_dir / "lote_simples.geojson", tmp_path / "saida", job_id="real",
                     prancha={"projeto": "Loteamento Sol", "legenda": "lateral"})
    assert integridade.conferir_artefatos(tmp_path / "saida", "real") == {"ok": True, "motivos": {}}
    assert sorted(p.name for p in result.pdf_path.parent.iterdir()) == ["mapa.pdf", "memorial.pdf", "resultado.json"]


def test_copia_estragada_de_um_job_real_e_recusada_e_o_original_continua_valido(qgis_app, fixtures_dir, tmp_path):
    """Destrutivo só na cópia: o caso real de 2026-10-01 (mesmo tamanho, só bytes zero)."""
    import shutil

    import integridade

    run_job(fixtures_dir / "lote_simples.geojson", tmp_path / "saida", job_id="real")
    copia = tmp_path / "copia"
    shutil.copytree(tmp_path / "saida" / "real", copia / "real")
    for nome in ("mapa.pdf", "memorial.pdf", "resultado.json"):
        tamanho = (copia / "real" / nome).stat().st_size
        (copia / "real" / nome).write_bytes(b"\x00" * tamanho)
    assert integridade.conferir_artefatos(copia, "real") == {"ok": False, "motivos": {
        "mapa.pdf": "sem_cabecalho_pdf", "memorial.pdf": "sem_cabecalho_pdf", "resultado.json": "json_invalido"}}
    assert integridade.conferir_artefatos(tmp_path / "saida", "real")["ok"] is True


def _poligono_metrico(tmp_path, nome, pontos_m):
    """GeoJSON perto de Brasília com os vértices dados em metros relativos (aproximação local)."""
    lon, lat = -47.9, -15.8
    anel = [[lon + x / 107_000, lat + y / 111_000] for x, y in pontos_m]
    entrada = tmp_path / "entradas" / f"{nome}.geojson"
    entrada.parent.mkdir(exist_ok=True)
    entrada.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [anel + anel[:1]]}}]}), encoding="utf-8")
    return entrada


@pytest.mark.parametrize("nome, pontos_m, zeros", [
    ("quadrado_0_4m", [(0, 0), (0.4, 0), (0.4, 0.4), (0, 0.4)], {"area_ha"}),
    ("vertices_a_3mm", [(0, 0), (10, 0), (10.003, 0), (10, 10), (0, 10)], {"distancia_m"}),
    ("quadrado_1mm", [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)], {"area_ha", "perimetro_m", "distancia_m"}),
])
def test_zeros_do_arredondamento_do_produtor_passam_na_integridade(qgis_app, tmp_path, nome, pontos_m, zeros):
    """Entrada válida, valores arredondados a 0.0 pelo próprio run_job: o job tem de poder concluir."""
    import integridade

    run_job(_poligono_metrico(tmp_path, nome, pontos_m), tmp_path / "saida", job_id=nome)
    dados = json.loads((tmp_path / "saida" / nome / "resultado.json").read_text(encoding="utf-8"))
    vistos = {campo for campo in ("area_ha", "perimetro_m") if dados[campo] == 0.0}
    vistos |= {"distancia_m"} if any(v["distancia_m"] == 0.0 for v in dados["vertices"]) else set()
    assert vistos == zeros  # o caso exercita mesmo o zero que diz exercitar
    assert integridade.conferir_artefatos(tmp_path / "saida", nome) == {"ok": True, "motivos": {}}


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


def test_run_job_arquivo_vazio_falha_com_causa_clara_sem_pasta(qgis_app, tmp_path):
    entrada = tmp_path / "entrada" / "vazio.geojson"
    entrada.parent.mkdir()
    entrada.write_bytes(b"")
    saida = tmp_path / "saida"
    with pytest.raises(InvalidInputError) as exc:
        run_job(entrada, saida, job_id="vazio")
    assert str(exc.value) == "json_invalido: O arquivo está vazio."
    assert not (saida / "vazio").exists()  # nenhum mapa.pdf, memorial.pdf ou resultado.json parcial


@pytest.mark.parametrize("anel, crs, codigo", [
    ([[-80.0, 95.0], [-79.99, 95.0], [-79.99, 95.01], [-80.0, 95.0]], None, "coordenada_invalida"),
    ([[200.0, -15.0], [200.01, -15.0], [200.01, -14.99], [200.0, -15.0]], None, "coordenada_invalida"),
    ([[-47.9, -15.8], [-47.89, -15.8], [-47.89, -15.79], [-47.9, -15.8]],
     {"type": "name", "properties": {"name": "EPSG:999999"}}, "crs_nao_suportado"),
    ([[-47.9, -15.8], [-20.0, -15.8], [-20.0, -15.7], [-47.9, -15.7], [-47.9, -15.8]], None, "fora_da_cobertura"),
])
def test_run_job_coordenada_invalida_falha_sem_artefatos(qgis_app, tmp_path, anel, crs, codigo):
    dados = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [anel]}}],
    }
    if crs:
        dados["crs"] = crs
    entrada = tmp_path / "entrada" / "coord.geojson"
    entrada.parent.mkdir()
    entrada.write_text(json.dumps(dados), encoding="utf-8")
    saida = tmp_path / "saida"
    with pytest.raises(InvalidInputError) as exc:
        run_job(entrada, saida, job_id="coord")
    assert exc.value.codigo == codigo
    assert str(exc.value) == f"{codigo}: {exc.value.mensagem}"
    assert not (saida / "coord").exists()  # nenhum mapa.pdf, memorial.pdf ou resultado.json parcial
