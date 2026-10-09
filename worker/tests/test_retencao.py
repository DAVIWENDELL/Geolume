"""Plano de retenção (dry-run): só lê banco e disco e diz o que seria mantido ou poderia ser limpo."""

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("psycopg2")

import retencao  # noqa: E402

AGORA = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")
ARQUIVOS = ("geojson", "logo", *ARTEFATOS)


@pytest.fixture
def saida(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "logos").mkdir()
    return tmp_path


def _job(saida, job_id="j1", status="completed", terminado_ha=timedelta(days=1), geojson=True, logo=False,
         artefatos=(), tenant="demo", nome="lote.geojson"):
    entrada = saida / "inputs" / f"{job_id}-{nome}"
    if geojson:
        entrada.write_text("{}")
    if logo:
        (saida / "logos" / f"{job_id}.png").write_bytes(b"png")
    if artefatos:
        (saida / job_id).mkdir(exist_ok=True)
        for artefato in artefatos:
            (saida / job_id / artefato).write_bytes(b"ok")
    terminado = status in ("completed", "failed")
    return {"id": job_id, "status": status, "task_id": "t-1", "tenant_id": tenant, "owner_id": "u-secreto",
            "input_filename": nome, "created_at": AGORA - terminado_ha - timedelta(minutes=1),
            "started_at": None, "completed_at": AGORA - terminado_ha if terminado else None,
            "input_path": str(entrada), "erro": "segredo", "prancha": {"projeto": "X"}}


def _plano(job, saida):
    return retencao.planejar_job(job, AGORA, saida)


def _acoes(plano):
    return {nome: plano["arquivos"][nome]["acao"] for nome in ARQUIVOS}


def _arvore(raiz):
    return sorted((str(p.relative_to(raiz)), p.lstat().st_size, p.lstat().st_mtime_ns, p.lstat().st_nlink)
                  for p in Path(raiz).rglob("*"))


# ---- Períodos ------------------------------------------------------------------------


def test_periodos_documentados():
    assert retencao.RETENCAO_COMPLETED == timedelta(days=90)
    assert retencao.RETENCAO_FAILED == timedelta(days=30)


# ---- Estados -------------------------------------------------------------------------


def test_completed_recente_mantem_downloads_e_historico(saida):
    p = _plano(_job(saida, logo=True, artefatos=ARTEFATOS), saida)
    assert (p["decisao"], p["motivo"]) == ("manter", "completed_recente")
    assert set(_acoes(p).values()) == {"manter"}
    assert p["arquivos"]["geojson"] == {"presente": True, "acao": "manter", "motivo": "entrada_do_historico"}
    assert p["arquivos"]["logo"]["motivo"] == "logo_do_mapa"
    assert p["arquivos"]["mapa.pdf"]["motivo"] == "download"


def test_completed_expirado_so_e_candidato_a_retencao(saida):
    p = _plano(_job(saida, terminado_ha=timedelta(days=91), logo=True, artefatos=ARTEFATOS), saida)
    assert (p["decisao"], p["motivo"]) == ("candidato_retencao", "completed_expirado")
    assert set(_acoes(p).values()) == {"candidato_retencao"}  # nada é candidato à limpeza nesta fatia


def test_completed_no_limite_exato_ainda_e_recente(saida):
    p = _plano(_job(saida, terminado_ha=retencao.RETENCAO_COMPLETED), saida)
    assert p["decisao"] == "manter"


def test_failed_no_limite_exato_ainda_e_recente(saida):
    p = _plano(_job(saida, status="failed", terminado_ha=retencao.RETENCAO_FAILED), saida)
    assert (p["decisao"], p["motivo"], p["arquivos_candidatos"]) == ("manter", "failed_recente", [])
    p = _plano(_job(saida, status="failed", terminado_ha=retencao.RETENCAO_FAILED + timedelta(seconds=1)), saida)
    assert p["decisao"] == "candidato_limpeza"


def test_failed_recente_mantem_diagnostico(saida):
    p = _plano(_job(saida, status="failed", terminado_ha=timedelta(days=29), logo=True), saida)
    assert (p["decisao"], p["motivo"]) == ("manter", "failed_recente")
    assert set(_acoes(p).values()) == {"manter"}


def test_failed_expirado_tem_uploads_como_candidatos(saida):
    p = _plano(_job(saida, status="failed", terminado_ha=timedelta(days=31), logo=True), saida)
    assert (p["decisao"], p["motivo"]) == ("candidato_limpeza", "failed_expirado")
    assert p["arquivos"]["geojson"] == {"presente": True, "acao": "candidato_limpeza", "motivo": "upload_de_job_falho"}
    assert p["arquivos"]["logo"]["acao"] == "candidato_limpeza"
    assert p["arquivos_candidatos"] == ["geojson", "logo"]


def test_failed_expirado_preserva_artefatos_parciais(saida):
    p = _plano(_job(saida, status="failed", terminado_ha=timedelta(days=31), artefatos=("mapa.pdf",)), saida)
    assert p["arquivos"]["mapa.pdf"] == {"presente": True, "acao": "manter", "motivo": "artefato_parcial"}
    assert p["arquivos"]["memorial.pdf"]["presente"] is False
    assert p["arquivos_candidatos"] == ["geojson"]


def test_failed_expirado_com_os_tres_artefatos_nao_apaga_pdf(saida):
    p = _plano(_job(saida, status="failed", terminado_ha=timedelta(days=31), artefatos=ARTEFATOS), saida)
    assert {p["arquivos"][a]["acao"] for a in ARTEFATOS} == {"manter"}


@pytest.mark.parametrize("status", ["queued", "started"])
def test_queued_e_started_sao_sempre_mantidos(saida, status):
    job = _job(saida, status=status, terminado_ha=timedelta(days=400), logo=True, artefatos=("mapa.pdf",))
    job["task_id"] = "t-1"
    job["created_at"] = AGORA - timedelta(days=400)
    job["started_at"] = AGORA - timedelta(days=400) if status == "started" else None
    p = _plano(job, saida)
    assert (p["decisao"], p["motivo"]) == ("manter", f"{status}_protegido")
    assert set(_acoes(p).values()) == {"manter"}
    assert p["arquivos_candidatos"] == []


def test_queued_sem_task_id_tambem_e_mantido(saida):
    job = _job(saida, status="queued")
    job["task_id"] = None
    assert _plano(job, saida)["decisao"] == "manter"


def test_started_sem_task_id_tambem_e_mantido(saida):
    job = _job(saida, status="started", logo=True)
    job["task_id"] = None
    job["started_at"] = AGORA - timedelta(days=400)
    p = _plano(job, saida)
    assert (p["decisao"], p["motivo"], p["arquivos_candidatos"]) == ("manter", "started_protegido", [])


def test_status_desconhecido_e_mantido(saida):
    p = _plano(_job(saida, status="cancelado", terminado_ha=timedelta(days=400)), saida)
    assert (p["decisao"], p["motivo"]) == ("manter", "status_desconhecido")


# ---- Tempo ---------------------------------------------------------------------------


def test_idade_calculada_com_fuso(saida):
    job = _job(saida, status="failed")
    job["completed_at"] = datetime(2026, 9, 7, 9, 0, tzinfo=timezone(timedelta(hours=-3)))  # 12:00 UTC
    p = _plano(job, saida)
    assert (p["marco"], p["idade_segundos"]) == ("completed_at", 31 * 86400)
    assert p["limite_segundos"] == retencao.RETENCAO_FAILED.total_seconds()
    assert p["decisao"] == "candidato_limpeza"


def test_data_futura_nao_expira(saida):
    job = _job(saida, status="failed")
    job["completed_at"] = AGORA + timedelta(days=400)
    p = _plano(job, saida)
    assert (p["decisao"], p["motivo"]) == ("manter", "data_futura")
    assert p["arquivos_candidatos"] == []


def test_sem_completed_at_e_mantido(saida):
    job = _job(saida, status="failed", terminado_ha=timedelta(days=400))
    job["completed_at"] = None
    assert _plano(job, saida)["motivo"] == "sem_marco"


def test_data_sem_fuso_e_mantida(saida):
    job = _job(saida, status="failed")
    job["completed_at"] = datetime(2020, 1, 1)
    assert _plano(job, saida)["motivo"] == "data_sem_fuso"


def test_agora_sem_fuso_e_recusado(saida):
    with pytest.raises(ValueError):
        retencao.planejar_job(_job(saida), datetime(2026, 10, 8, 12, 0), saida)


# ---- Caminhos e links ----------------------------------------------------------------


def _expirado(saida, **kw):
    return _job(saida, status="failed", terminado_ha=timedelta(days=31), **kw)


def test_job_sem_arquivos(saida):
    p = _plano(_expirado(saida, geojson=False), saida)
    assert {n: p["arquivos"][n]["presente"] for n in ARQUIVOS} == dict.fromkeys(ARQUIVOS, False)
    assert p["decisao"] == "candidato_limpeza" and p["arquivos_candidatos"] == []


def test_input_path_fora_de_inputs_nao_e_candidato(saida, tmp_path_factory):
    fora = tmp_path_factory.mktemp("fora") / "j1-lote.geojson"
    fora.write_text("{}")
    job = _expirado(saida, geojson=False)
    job["input_path"] = str(fora)
    p = _plano(job, saida)
    assert p["arquivos"]["geojson"] == {"presente": True, "acao": "manter", "motivo": "fora_da_pasta_permitida"}


def test_input_path_com_ponto_ponto_nao_e_candidato(saida):
    (saida / "j1-lote.geojson").write_text("{}")
    job = _expirado(saida, geojson=False)
    job["input_path"] = str(saida / "inputs" / ".." / "j1-lote.geojson")
    assert _plano(job, saida)["arquivos"]["geojson"]["acao"] == "manter"


def test_upload_de_outro_job_nunca_e_candidato(saida):
    """O caminho no banco aponta para a entrada de outro job (outro dono): não é do job."""
    _job(saida, job_id="outro")
    job = _expirado(saida, geojson=False)
    job["input_path"] = str(saida / "inputs" / "outro-lote.geojson")
    p = _plano(job, saida)
    assert p["arquivos"]["geojson"]["motivo"] == "fora_da_pasta_permitida"
    assert p["arquivos_candidatos"] == []


def test_nome_diferente_do_registrado_nao_e_candidato(saida):
    job = _expirado(saida)
    job["input_filename"] = "outro.geojson"
    assert _plano(job, saida)["arquivos"]["geojson"]["acao"] == "manter"


def test_job_id_invalido_nao_tem_candidatos(saida):
    job = _expirado(saida, job_id="j1")
    job["id"] = "../j1"
    p = _plano(job, saida)
    assert p["arquivos_candidatos"] == []
    assert {p["arquivos"][n]["motivo"] for n in ARQUIVOS} == {"job_id_invalido"}


@pytest.fixture
def links_suportados(tmp_path):
    try:
        os.symlink(tmp_path / "x", tmp_path / "teste-link")
    except (OSError, NotImplementedError):
        pytest.skip("sistema sem links simbólicos")
    (tmp_path / "teste-link").unlink()


def test_geojson_link_simbolico_nao_e_candidato(saida, links_suportados, tmp_path_factory):
    alvo = tmp_path_factory.mktemp("alvo") / "importante.geojson"
    alvo.write_text("{}")
    job = _expirado(saida, geojson=False)
    os.symlink(alvo, job["input_path"])
    p = _plano(job, saida)
    assert p["arquivos"]["geojson"] == {"presente": True, "acao": "manter", "motivo": "link_recusado"}


def test_logo_link_simbolico_nao_e_candidata(saida, links_suportados, tmp_path_factory):
    alvo = tmp_path_factory.mktemp("alvo") / "importante.png"
    alvo.write_bytes(b"png")
    os.symlink(alvo, saida / "logos" / "j1.png")
    p = _plano(_expirado(saida), saida)
    assert p["arquivos"]["logo"] == {"presente": True, "acao": "manter", "motivo": "link_recusado"}


def test_pasta_inputs_link_simbolico_nao_e_candidata(tmp_path, links_suportados, tmp_path_factory):
    real = tmp_path_factory.mktemp("inputs_real")
    os.symlink(real, tmp_path / "inputs", target_is_directory=True)
    (tmp_path / "logos").mkdir()
    job = _expirado(tmp_path)
    p = _plano(job, tmp_path)
    assert p["arquivos"]["geojson"]["acao"] == "manter"
    assert p["arquivos"]["geojson"]["motivo"] in ("link_recusado", "fora_da_pasta_permitida")


def test_pasta_logos_link_simbolico_nao_e_candidata(tmp_path, links_suportados, tmp_path_factory):
    real = tmp_path_factory.mktemp("logos_real")
    (real / "j1.png").write_bytes(b"png")
    (tmp_path / "inputs").mkdir()
    os.symlink(real, tmp_path / "logos", target_is_directory=True)
    p = _plano(_expirado(tmp_path), tmp_path)
    assert p["arquivos"]["logo"] == {"presente": True, "acao": "manter", "motivo": "link_recusado"}
    assert p["arquivos_candidatos"] == ["geojson"]


def _saida_real(raiz):
    """Saída completa (inputs, logos, job failed expirado com GeoJSON, logo e mapa parcial) fora do caminho do plano."""
    (raiz / "inputs").mkdir()
    (raiz / "logos").mkdir()
    return _expirado(raiz, logo=True, artefatos=("mapa.pdf",))


def _tudo_mantido(p, motivo, presente=True):
    assert p["arquivos_candidatos"] == []
    assert {n: p["arquivos"][n] for n in ("geojson", "logo", "mapa.pdf")} == dict.fromkeys(
        ("geojson", "logo", "mapa.pdf"), {"presente": presente, "acao": "manter", "motivo": motivo})


def test_output_dir_link_simbolico_nao_tem_candidatos(tmp_path, links_suportados, tmp_path_factory, monkeypatch):
    real = tmp_path_factory.mktemp("saida_real")
    job = _saida_real(real)
    link = tmp_path / "saida"
    os.symlink(real, link, target_is_directory=True)
    job["input_path"] = str(link / "inputs" / "j1-lote.geojson")
    antes = _arvore(real)
    monkeypatch.setattr(retencao, "ler_jobs", _ler([job]))
    p = retencao.plano(AGORA, link)
    _tudo_mantido(p["jobs"][0], "link_recusado")
    assert p["arquivos_candidatos"] == 0
    assert _arvore(real) == antes  # o caminho externo não foi tocado


def test_ancestral_do_output_dir_link_simbolico_nao_tem_candidatos(tmp_path, links_suportados, tmp_path_factory):
    real = tmp_path_factory.mktemp("volume")
    (real / "saida").mkdir()
    job = _saida_real(real / "saida")
    os.symlink(real, tmp_path / "volume", target_is_directory=True)
    saida = tmp_path / "volume" / "saida"
    job["input_path"] = str(saida / "inputs" / "j1-lote.geojson")
    _tudo_mantido(retencao.planejar_job(job, AGORA, saida), "link_recusado")


def test_output_dir_relativo_com_ancestral_link_nao_tem_candidatos(tmp_path, links_suportados, tmp_path_factory,
                                                                   monkeypatch):
    real = tmp_path_factory.mktemp("volume_rel")
    (real / "saida").mkdir()
    job = _saida_real(real / "saida")
    os.symlink(real, tmp_path / "volume", target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    job["input_path"] = str(Path("volume") / "saida" / "inputs" / "j1-lote.geojson")
    _tudo_mantido(retencao.planejar_job(job, AGORA, Path("volume") / "saida"), "link_recusado")


def test_lstat_negado_num_ancestral_nao_tem_candidatos(saida, monkeypatch):
    job = _expirado(saida, logo=True, artefatos=("mapa.pdf",))
    _lstat_negado(monkeypatch, saida.parent)
    _tudo_mantido(_plano(job, saida), "nao_verificavel", presente=None)


def test_output_dir_real_continua_com_candidatos(saida):
    assert _plano(_expirado(saida, logo=True), saida)["arquivos_candidatos"] == ["geojson", "logo"]


def test_pasta_do_job_link_simbolico_nao_e_seguida(saida, links_suportados, tmp_path_factory):
    real = tmp_path_factory.mktemp("pasta_real")
    (real / "mapa.pdf").write_bytes(b"ok")
    os.symlink(real, saida / "j1", target_is_directory=True)
    p = _plano(_expirado(saida), saida)
    assert p["arquivos"]["mapa.pdf"] == {"presente": True, "acao": "manter", "motivo": "link_recusado"}


def test_geojson_com_hard_link_nao_e_candidato(saida, tmp_path_factory):
    job = _expirado(saida)
    try:
        os.link(job["input_path"], tmp_path_factory.mktemp("outro") / "copia.geojson")
    except OSError:
        pytest.skip("sistema sem hard links")
    p = _plano(job, saida)
    assert p["arquivos"]["geojson"] == {"presente": True, "acao": "manter", "motivo": "hard_link_recusado"}


def test_logo_com_hard_link_nao_e_candidata(saida, tmp_path_factory):
    job = _expirado(saida, logo=True)
    try:
        os.link(saida / "logos" / "j1.png", tmp_path_factory.mktemp("outro") / "copia.png")
    except OSError:
        pytest.skip("sistema sem hard links")
    assert _plano(job, saida)["arquivos"]["logo"]["motivo"] == "hard_link_recusado"


def test_artefato_com_hard_link_nao_e_candidato_a_retencao(saida, tmp_path_factory):
    job = _job(saida, terminado_ha=timedelta(days=91), artefatos=ARTEFATOS)
    try:
        os.link(saida / "j1" / "mapa.pdf", tmp_path_factory.mktemp("outro") / "copia.pdf")
    except OSError:
        pytest.skip("sistema sem hard links")
    p = _plano(job, saida)
    assert p["arquivos"]["mapa.pdf"] == {"presente": True, "acao": "manter", "motivo": "hard_link_recusado"}
    assert p["arquivos"]["memorial.pdf"]["acao"] == "candidato_retencao"


def _lstat_negado(monkeypatch, negado):
    original = os.lstat

    def lstat(caminho, *a, **k):
        if Path(caminho) == negado:
            raise PermissionError(13, "Permission denied")
        return original(caminho, *a, **k)

    monkeypatch.setattr(os, "lstat", lstat)


def test_lstat_negado_no_arquivo_nao_e_candidato(saida, monkeypatch):
    job = _expirado(saida, logo=True)
    _lstat_negado(monkeypatch, Path(job["input_path"]))
    p = _plano(job, saida)
    assert p["arquivos"]["geojson"] == {"presente": None, "acao": "manter", "motivo": "nao_verificavel"}
    assert p["arquivos_candidatos"] == ["logo"]


@pytest.mark.parametrize("pasta", ["inputs", "logos", "j1"])
def test_lstat_negado_na_pasta_nao_derruba_o_plano(saida, monkeypatch, pasta):
    job = _expirado(saida, logo=True, artefatos=("mapa.pdf",))
    _lstat_negado(monkeypatch, saida / pasta)
    p = _plano(job, saida)
    afetado = {"inputs": "geojson", "logo": "logo", "logos": "logo", "j1": "mapa.pdf"}[pasta]
    assert p["arquivos"][afetado] == {"presente": None, "acao": "manter", "motivo": "nao_verificavel"}
    assert afetado not in p["arquivos_candidatos"]


def test_geojson_que_e_pasta_nao_e_candidato(saida):
    job = _expirado(saida, geojson=False)
    Path(job["input_path"]).mkdir()
    assert _plano(job, saida)["arquivos"]["geojson"]["motivo"] == "nao_e_arquivo_regular"


# ---- Saída e determinismo ------------------------------------------------------------


def test_saida_sem_caminhos_dono_nem_erro(saida):
    p = _plano(_expirado(saida, logo=True), saida)
    texto = json.dumps(p, default=str)
    for proibido in (str(saida), "u-secreto", "owner_id", "segredo", "prancha", "inputs/"):
        assert proibido not in texto


def _ler(jobs):
    return lambda job_ids=None, tenant=None: [
        j for j in jobs if (job_ids is None or j["id"] in job_ids) and (tenant is None or j["tenant_id"] == tenant)]


def test_plano_deterministico_e_ordenado(saida, monkeypatch):
    jobs = [_expirado(saida, job_id="b"), _job(saida, job_id="a"), _job(saida, job_id="c", status="started")]
    monkeypatch.setattr(retencao, "ler_jobs", _ler(jobs))
    p1 = retencao.plano(AGORA, saida)
    monkeypatch.setattr(retencao, "ler_jobs", _ler(list(reversed(jobs))))
    p2 = retencao.plano(AGORA, saida)
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
    assert [j["id"] for j in p1["jobs"]] == ["a", "b", "c"]
    assert p1["somente_leitura"] is True
    assert p1["resumo"] == {"candidato_limpeza": 1, "candidato_retencao": 0, "manter": 2, "nao_encontrado": 0}
    assert p1["total"] == 3
    assert p1["arquivos_candidatos"] == 1
    assert p1["periodos_dias"] == {"completed": 90, "failed": 30}


def test_plano_por_tenant_nao_inclui_outro_tenant(saida, monkeypatch):
    jobs = [_expirado(saida, job_id="nosso"), _expirado(saida, job_id="alheio", tenant="outro")]
    monkeypatch.setattr(retencao, "ler_jobs", _ler(jobs))
    p = retencao.plano(AGORA, saida, tenant="demo")
    assert [j["id"] for j in p["jobs"]] == ["nosso"]
    p = retencao.plano(AGORA, saida, job_ids=["alheio"], tenant="demo")
    assert p["jobs"] == [{"id": "alheio", "decisao": "nao_encontrado"}]
    assert p["arquivos_candidatos"] == 0
    assert p["resumo"]["nao_encontrado"] == 1 and p["total"] == 1


def test_consulta_mista_resumo_soma_o_total(saida, monkeypatch):
    jobs = [_expirado(saida, job_id="nosso"), _expirado(saida, job_id="alheio", tenant="outro")]
    monkeypatch.setattr(retencao, "ler_jobs", _ler(jobs))
    p = retencao.plano(AGORA, saida, job_ids=["nosso", "nada", "alheio"], tenant="demo")
    assert {j["id"]: j["decisao"] for j in p["jobs"]} == {
        "nosso": "candidato_limpeza", "nada": "nao_encontrado", "alheio": "nao_encontrado"}
    assert p["resumo"] == {"candidato_limpeza": 1, "candidato_retencao": 0, "manter": 0, "nao_encontrado": 2}
    assert sum(p["resumo"].values()) == p["total"] == len(p["jobs"]) == 3
    assert p["arquivos_candidatos"] == 1  # só o GeoJSON do job do tenant consultado


def test_ids_duplicados_aparecem_uma_vez(saida, monkeypatch):
    monkeypatch.setattr(retencao, "ler_jobs", _ler([_expirado(saida, job_id="a")]))
    p = retencao.plano(AGORA, saida, job_ids=["a", "nada", "a", "nada"])
    assert [j["id"] for j in p["jobs"]] == ["a", "nada"]
    assert sum(p["resumo"].values()) == p["total"] == 2
    assert p["arquivos_candidatos"] == 1


def test_dois_owners_no_mesmo_tenant_cada_um_com_seus_arquivos(saida, monkeypatch):
    a = _expirado(saida, job_id="a", logo=True)
    b = _expirado(saida, job_id="b")
    b["owner_id"] = "u-outro"
    monkeypatch.setattr(retencao, "ler_jobs", _ler([a, b]))
    p = retencao.plano(AGORA, saida, tenant="demo")
    assert {j["id"]: j["arquivos_candidatos"] for j in p["jobs"]} == {"a": ["geojson", "logo"], "b": ["geojson"]}
    texto = json.dumps(p, default=str)
    assert "u-secreto" not in texto and "u-outro" not in texto


# ---- Somente leitura -----------------------------------------------------------------


def _bloquear_escrita(monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("o plano de retenção tentou escrever ou apagar")

    for alvo, nome in [(os, "unlink"), (os, "remove"), (os, "rmdir"), (os, "rename"), (os, "replace"),
                       (os, "mkdir"), (os, "makedirs"), (os, "link"), (os, "symlink"), (os, "truncate"),
                       (shutil, "rmtree"), (shutil, "move"), (Path, "unlink"), (Path, "rmdir"),
                       (Path, "write_text"), (Path, "write_bytes"), (Path, "touch"), (Path, "mkdir"),
                       (Path, "rename"), (Path, "replace")]:
        monkeypatch.setattr(alvo, nome, proibido)
    abrir = os.open

    def abrir_leitura(caminho, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            proibido()
        return abrir(caminho, flags, *a, **k)

    monkeypatch.setattr(os, "open", abrir_leitura)


def test_dry_run_nao_escreve_nem_apaga(saida, monkeypatch):
    jobs = [_expirado(saida, job_id="f", logo=True, artefatos=("mapa.pdf",)),
            _job(saida, job_id="c", terminado_ha=timedelta(days=200), logo=True, artefatos=ARTEFATOS),
            _job(saida, job_id="s", status="started")]
    monkeypatch.setattr(retencao, "ler_jobs", _ler(jobs))
    antes = _arvore(saida)
    p = retencao.plano(AGORA, saida)
    assert _arvore(saida) == antes
    assert p["arquivos_candidatos"] == 2


def test_dry_run_com_disco_bloqueado_para_escrita(saida, monkeypatch):
    jobs = [_expirado(saida, job_id="f", logo=True, artefatos=("mapa.pdf",))]
    monkeypatch.setattr(retencao, "ler_jobs", _ler(jobs))
    _bloquear_escrita(monkeypatch)
    p = retencao.plano(AGORA, saida)
    assert p["jobs"][0]["arquivos_candidatos"] == ["geojson", "logo"]


def test_modulo_nao_tem_funcao_de_exclusao():
    fonte = Path(retencao.__file__).read_text(encoding="utf-8")
    for proibido in ("unlink", "rmtree", "os.remove", "rmdir", "UPDATE ", "DELETE ", "INSERT "):
        assert proibido not in fonte


# ---- Leitura no banco ----------------------------------------------------------------


class _Cursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return []


class _Conn(_Cursor):
    def set_session(self, **kwargs):
        self.log.append(("set_session", kwargs))

    def cursor(self, **kwargs):
        return _Cursor(self.log)


@pytest.fixture
def consultas(monkeypatch):
    import db

    log = []
    monkeypatch.setattr(db, "connect", lambda: _Conn(log))
    return log


def test_ler_jobs_usa_sessao_somente_leitura_e_so_select(consultas):
    retencao.ler_jobs()
    assert consultas[0] == ("set_session", {"readonly": True})
    ((sql, params),) = consultas[1:]
    assert sql.startswith("SELECT ") and params == ()
    assert "owner_id" not in sql and "prancha" not in sql and "erro" not in sql


def test_ler_jobs_filtra_por_id_e_tenant(consultas):
    retencao.ler_jobs(["a", "b"], tenant="demo")
    ((sql, params),) = consultas[1:]
    assert "id = ANY(%s)" in sql and "tenant_id = %s" in sql
    assert params == (["a", "b"], "demo")


# ---- CLI -----------------------------------------------------------------------------


def test_cli_imprime_json_e_nao_tem_apply(saida, monkeypatch, capsys):
    monkeypatch.setattr(retencao, "ler_jobs", _ler([_expirado(saida)]))
    assert retencao.main(["--saida", str(saida), "--agora", "2026-10-08T12:00:00+00:00"]) == 0
    saida_json = json.loads(capsys.readouterr().out)
    assert saida_json["somente_leitura"] is True and saida_json["arquivos_candidatos"] == 1
    with pytest.raises(SystemExit):
        retencao.main(["--apply"])


def test_cli_recusa_agora_sem_fuso(saida):
    with pytest.raises(SystemExit):
        retencao.main(["--saida", str(saida), "--agora", "2026-10-08T12:00:00"])


def test_api_nao_importa_a_retencao():
    fonte = (Path(retencao.__file__).parent / "api.py").read_text(encoding="utf-8")
    assert "retencao" not in fonte
