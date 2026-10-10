"""run_job grava o resultado.json por arquivo temporário + fsync + os.replace e sincroniza os PDFs e as pastas
antes de devolver: o completed só é gravado depois disso. Exportação do QGIS trocada por stubs."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("qgis.core")

import artefatos  # noqa: E402
import integridade  # noqa: E402
from geolume_worker import job as job_mod  # noqa: E402
from geolume_worker.geometry import Vertex  # noqa: E402

JOB = "job-a"


@pytest.fixture
def stubs(monkeypatch):
    vertices = [Vertex(f"V{i}", 500000.0 + i, 8000000.0 + i, "90°00'00\"", 10.0) for i in range(1, 5)]
    resumo = SimpleNamespace(properties={}, epsg=31983, area_ha=0.01, perimetro_m=40.0, vertices=vertices)
    monkeypatch.setattr(job_mod, "is_qgis_initialized", lambda: True)
    monkeypatch.setattr(job_mod, "load_input", lambda entrada: object())
    monkeypatch.setattr(job_mod, "process", lambda carregado: resumo)

    def exportar(summary, destino, **kwargs):
        Path(destino).write_bytes(artefatos.PDF)

    monkeypatch.setattr(job_mod, "export_map_pdf", exportar)
    monkeypatch.setattr(job_mod, "export_memorial_pdf", exportar)


@pytest.fixture
def eventos(monkeypatch):
    """Registra fsync (com o caminho real do descritor) e os.replace, na ordem."""
    registro = []
    fsync, replace = os.fsync, os.replace

    def fsync_registrado(fd):
        registro.append(("fsync", os.readlink(f"/proc/self/fd/{fd}")))
        return fsync(fd)

    def replace_registrado(origem, destino):
        registro.append(("replace", str(origem), str(destino)))
        return replace(origem, destino)

    monkeypatch.setattr(job_mod.os, "fsync", fsync_registrado)
    monkeypatch.setattr(job_mod.os, "replace", replace_registrado)
    return registro


def _rodar(tmp_path):
    entrada = tmp_path / "lote.geojson"
    entrada.write_text("{}")
    return job_mod.run_job(entrada, tmp_path / "saida", job_id=JOB)


def test_integridade_confere_as_mesmas_fases_e_versao_do_run_job():
    assert integridade.FASES == job_mod.FASES and integridade.VERSAO_ESQUEMA == job_mod.VERSAO_ESQUEMA


def test_artefatos_do_run_job_passam_na_integridade(stubs, tmp_path):
    _rodar(tmp_path)
    assert integridade.conferir_artefatos(tmp_path / "saida", JOB) == {"ok": True, "motivos": {}}


def test_resultado_json_por_temporario_fsync_e_replace(stubs, eventos, tmp_path):
    resultado = _rodar(tmp_path)
    pasta = tmp_path / "saida" / JOB
    (replace,) = [e for e in eventos if e[0] == "replace"]
    _, origem, destino = replace
    assert destino == str(resultado.json_path)
    assert Path(origem).parent == pasta and Path(origem).name != "resultado.json"
    assert not Path(origem).exists()  # o temporário virou o resultado.json
    assert eventos.index(("fsync", origem)) < eventos.index(replace)  # dados no disco antes do nome final
    assert json.loads(resultado.json_path.read_text(encoding="utf-8"))["job_id"] == JOB


def test_pdfs_e_pastas_sincronizados_depois_do_replace(stubs, eventos, tmp_path):
    _rodar(tmp_path)
    pasta = tmp_path / "saida" / JOB
    sincronizados = [e[1] for e in eventos if e[0] == "fsync"]
    assert str(pasta / "mapa.pdf") in sincronizados and str(pasta / "memorial.pdf") in sincronizados
    posicao_replace = next(i for i, e in enumerate(eventos) if e[0] == "replace")
    depois = [e[1] for e in eventos[posicao_replace:] if e[0] == "fsync"]
    assert str(pasta) in depois  # a entrada resultado.json persistida na pasta do job
    assert str(tmp_path / "saida") in depois  # e a pasta do job na saída


def test_nenhum_temporario_sobra(stubs, tmp_path):
    _rodar(tmp_path)
    assert sorted(p.name for p in (tmp_path / "saida" / JOB).iterdir()) == [
        "mapa.pdf", "memorial.pdf", "resultado.json"]


@pytest.mark.parametrize("onde", ["escrever", "fsync", "replace"])
def test_erro_ao_gravar_o_json_nao_deixa_pdfs_parciais(stubs, tmp_path, monkeypatch, onde):
    if onde == "escrever":
        monkeypatch.setattr(job_mod.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    elif onde == "fsync":
        monkeypatch.setattr(job_mod.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disco cheio")))
    else:
        monkeypatch.setattr(job_mod.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disco cheio")))
    with pytest.raises(OSError, match="disco cheio"):
        _rodar(tmp_path)
    assert not (tmp_path / "saida" / JOB).exists()  # nem mapa.pdf, nem memorial.pdf, nem temporário


def test_erro_ao_sincronizar_a_pasta_tambem_descarta(stubs, tmp_path, monkeypatch):
    fsync = os.fsync

    def falha_na_pasta(fd):
        if os.path.isdir(os.readlink(f"/proc/self/fd/{fd}")):
            raise OSError("fsync da pasta")
        return fsync(fd)

    monkeypatch.setattr(job_mod.os, "fsync", falha_na_pasta)
    with pytest.raises(OSError, match="fsync da pasta"):
        _rodar(tmp_path)
    assert not (tmp_path / "saida" / JOB).exists()


def test_falha_antes_de_criar_a_pasta_nao_apaga_pasta_preexistente(stubs, tmp_path, monkeypatch):
    preexistente = tmp_path / "saida" / JOB
    preexistente.mkdir(parents=True)
    (preexistente / "outro.txt").write_text("x")

    def falha(entrada):
        raise ValueError("entrada")

    monkeypatch.setattr(job_mod, "load_input", falha)
    with pytest.raises(ValueError):
        _rodar(tmp_path)
    assert (preexistente / "outro.txt").exists()  # como antes: só o que veio depois do mkdir é descartado
