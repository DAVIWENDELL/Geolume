"""Autorização nos endpoints de jobs: dono, administrador, legado sem dono e job alheio."""

import asyncio
import importlib
import io
import sys
from types import SimpleNamespace

import pytest

ANA = {"user_id": "u-ana", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member"}
BIA = {"user_id": "u-bia", "email": "bia@geolume.test", "tenant_id": "demo", "role": "member"}
ADMIN = {"user_id": "u-adm", "email": "adm@geolume.test", "tenant_id": "demo", "role": "admin"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("qgis.core")
    pytest.importorskip("fastapi")
    import db

    monkeypatch.setattr(db, "init_db", lambda *args, **kwargs: None)
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)
    module = importlib.import_module("api")
    inputs = tmp_path / "saida" / "inputs"
    inputs.mkdir(parents=True)
    monkeypatch.setattr(module, "OUTPUT_DIR", tmp_path / "saida")
    monkeypatch.setattr(module, "INPUTS_DIR", inputs)
    yield module
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)


@pytest.fixture
def jobs(api, monkeypatch, tmp_path):
    """Banco em memória com a mesma regra do filtro SQL (tenant e administrador ou dono)."""
    registros = {}
    consultas = []

    def pode(job, user):
        return job["tenant_id"] == user["tenant_id"] and (user["role"] == "admin" or job["owner_id"] == user["user_id"])

    def get_job_for(task_id, user):
        consultas.append(user)
        job = registros.get(task_id)
        return dict(job) if job and pode(job, user) else None

    def list_jobs_for(user, limit=20):
        consultas.append(user)
        return [dict(j) for j in registros.values() if pode(j, user)][:limit]

    monkeypatch.setattr(api, "get_job_for", get_job_for)
    monkeypatch.setattr(api, "list_jobs_for", list_jobs_for)

    def novo(task_id, owner_id):
        pasta = tmp_path / "saida" / task_id
        pasta.mkdir()
        caminhos = {}
        for campo, nome in (("mapa_path", "mapa.pdf"), ("memorial_path", "memorial.pdf"), ("resultado_path", "resultado.json")):
            (pasta / nome).write_text("x")
            caminhos[campo] = str(pasta / nome)
        entrada = api.INPUTS_DIR / f"{task_id}-lote.geojson"
        entrada.write_text("{}")
        registros[task_id] = {"id": task_id, "task_id": task_id, "status": "completed", "erro": None,
                              "input_filename": "lote.geojson", "input_path": str(entrada),
                              "owner_id": owner_id, "tenant_id": "demo", **caminhos}

    novo("t-ana", ANA["user_id"])
    novo("t-legado", None)
    return SimpleNamespace(registros=registros, consultas=consultas)


def recursos(api, task_id, user):
    return {
        "status": lambda: api.job_status(task_id, user),
        "mapa": lambda: api.download_job_file(task_id, "mapa", user),
        "memorial": lambda: api.download_job_file(task_id, "memorial", user),
        "resultado": lambda: api.download_job_file(task_id, "resultado", user),
        "input": lambda: api.job_input(task_id, user),
    }


def erro(chamada):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        chamada()
    return exc.value.status_code, exc.value.detail


def dependencias(rota):
    return {dep.call for dep in rota.dependant.dependencies}


# ---- Guardas de rota ----------------------------------------------------


def test_toda_rota_de_jobs_exige_usuario(api):
    rotas = [r for r in api.app.routes if getattr(r, "path", "").startswith(("/jobs", "/auth/logout", "/auth/me"))]
    assert len(rotas) >= 8
    for rota in rotas:
        assert dependencias(rota) & {api.current_user, api.require_admin}, rota.path


def test_camadas_exige_usuario(api):
    rota = next(r for r in api.app.routes if getattr(r, "path", "") == "/camadas")
    assert api.current_user in dependencias(rota)
    assert rota.methods == {"GET"}


def test_camadas_mesma_resposta_para_qualquer_usuario(api):
    import json

    corpos = [api.camadas(user).body for user in (ANA, BIA, ADMIN)]
    assert corpos[0] == corpos[1] == corpos[2]
    texto = corpos[0].decode()
    assert "u-ana" not in texto and "demo" not in texto and "tenant_id" not in texto
    assert set(json.loads(texto)) == {"camadas", "estilos", "alfa_padrao"}


def test_post_exige_csrf(api):
    posts = [r for r in api.app.routes if "POST" in getattr(r, "methods", set())]
    assert {r.path for r in posts} >= {"/jobs", "/jobs/async", "/auth/login", "/auth/logout"}
    for rota in posts:
        assert api.check_csrf in dependencias(rota), rota.path


def test_sync_so_admin(api):
    rota = next(r for r in api.app.routes if r.path == "/jobs" and "POST" in r.methods)
    assert api.require_admin in dependencias(rota)


# ---- Acesso aos 5 recursos ----------------------------------------------


def test_membro_recebe_404_nos_5_recursos_de_job_alheio(api, jobs):
    for nome, chamada in recursos(api, "t-ana", BIA).items():
        assert erro(chamada) == (404, "Job não encontrado"), nome
    for nome, chamada in recursos(api, "nao-existe", BIA).items():
        assert erro(chamada) == (404, "Job não encontrado"), nome  # mesma resposta: não revela existência
    assert all(u == BIA for u in jobs.consultas)


def test_dono_acessa_os_5_recursos(api, jobs):
    for nome, chamada in recursos(api, "t-ana", ANA).items():
        assert chamada() is not None, nome
    assert api.job_status("t-ana", ANA)["status"] == "completed"


def test_membro_nao_acessa_legado(api, jobs):
    for nome, chamada in recursos(api, "t-legado", ANA).items():
        assert erro(chamada) == (404, "Job não encontrado"), nome
    assert [j["task_id"] for j in api.jobs(20, ANA)["jobs"]] == ["t-ana"]


def test_admin_acessa_legado_e_job_de_membro(api, jobs):
    for task_id in ("t-ana", "t-legado"):
        for nome, chamada in recursos(api, task_id, ADMIN).items():
            assert chamada() is not None, (task_id, nome)
    assert {j["task_id"] for j in api.jobs(20, ADMIN)["jobs"]} == {"t-ana", "t-legado"}


def test_listagem_passa_o_usuario_para_list_jobs_for(api, jobs):
    assert api.jobs(20, BIA) == {"jobs": []}
    assert jobs.consultas == [BIA]


def test_job_fora_do_banco_404_sem_consultar_celery(api, jobs):
    assert not hasattr(api, "AsyncResult")
    import db

    assert not hasattr(db, "get_job") and not hasattr(db, "list_jobs")  # sem consulta global
    assert erro(lambda: api.job_status("so-no-celery", ADMIN)) == (404, "Job não encontrado")


def test_job_incompleto_continua_sem_download(api, jobs):
    jobs.registros["t-ana"]["status"] = "started"
    assert erro(lambda: api.download_job_file("t-ana", "mapa", ANA)) == (404, "Job ainda não concluído")


# ---- Criação ------------------------------------------------------------


def test_async_grava_dono_e_tenant(api, monkeypatch):
    from fastapi import UploadFile

    criados = []
    monkeypatch.setattr(api, "create_db_job", lambda *a, **kw: criados.append((a, kw)))
    monkeypatch.setattr(api, "set_task_id", lambda *a: None)
    monkeypatch.setattr(api.process_job, "delay", lambda *a: SimpleNamespace(id="task-x"))

    asyncio.run(api.enqueue_job(UploadFile(file=io.BytesIO(b"{}"), filename="lote.geojson"), BIA))

    (_, kwargs), = criados
    assert kwargs["owner_id"] == "u-bia"
    assert kwargs["tenant_id"] == "demo"


# ---- Download: só o arquivo do próprio job ------------------------------


def test_download_recusa_caminho_do_banco_fora_da_pasta_do_job(api, jobs, tmp_path):
    """O caminho gravado no banco não basta: tem de ser o arquivo esperado na pasta do próprio job."""
    jobs.registros["t-bia"] = {**jobs.registros["t-legado"], "id": "t-bia", "task_id": "t-bia", "owner_id": BIA["user_id"]}
    alheio = tmp_path / "saida" / "t-legado" / "mapa.pdf"
    fora = tmp_path / "segredo.pdf"
    fora.write_text("segredo")
    for campo, destino in (("mapa_path", alheio), ("memorial_path", fora), ("resultado_path", alheio.parent / "mapa.pdf")):
        jobs.registros["t-ana"][campo] = str(destino)
    for kind in ("mapa", "memorial", "resultado"):
        assert erro(lambda: api.download_job_file("t-ana", kind, ANA)) == (404, "Arquivo não encontrado"), kind


def test_download_recusa_link_na_pasta_do_job(api, jobs, tmp_path):
    segredo = tmp_path / "segredo.pdf"
    segredo.write_text("segredo")
    mapa = tmp_path / "saida" / "t-ana" / "mapa.pdf"
    mapa.unlink()
    mapa.symlink_to(segredo)
    assert erro(lambda: api.download_job_file("t-ana", "mapa", ANA)) == (404, "Arquivo não encontrado")


def test_download_do_proprio_job_continua(api, jobs):
    for kind, nome, tipo in (("mapa", "mapa.pdf", "application/pdf"), ("memorial", "memorial.pdf", "application/pdf"),
                             ("resultado", "resultado.json", "application/json")):
        (api.OUTPUT_DIR / "t-ana" / nome).write_text(f"conteudo de {nome}")
        resposta = api.download_job_file("t-ana", kind, ANA)
        assert resposta.media_type == tipo
        assert resposta.headers["content-disposition"] == f'attachment; filename="{nome}"'
        assert corpo(resposta) == f"conteudo de {nome}".encode()


def corpo(resposta):
    async def ler():
        return b"".join([parte async for parte in resposta.body_iterator])

    try:
        return asyncio.run(ler())
    finally:
        if resposta.background:
            asyncio.run(resposta.background())


def test_download_recusa_hard_link_no_caminho_esperado(api, jobs, tmp_path):
    import os

    segredo = tmp_path / "segredo.pdf"
    segredo.write_text("segredo")
    mapa = tmp_path / "saida" / "t-ana" / "mapa.pdf"
    mapa.unlink()
    os.link(segredo, mapa)
    assert erro(lambda: api.download_job_file("t-ana", "mapa", ANA)) == (404, "Arquivo não encontrado")


def test_download_nao_reabre_caminho_trocado_depois_da_validacao(api, jobs, tmp_path):
    segredo = tmp_path / "segredo.pdf"
    segredo.write_text("segredo")
    mapa = tmp_path / "saida" / "t-ana" / "mapa.pdf"
    resposta = api.download_job_file("t-ana", "mapa", ANA)
    mapa.unlink()
    mapa.symlink_to(segredo)
    assert corpo(resposta) == b"x"  # o que foi validado, não o novo alvo


@pytest.mark.parametrize("job_id", ["../saida/t-legado", "a/../t-legado", "a/b", "", "."])
def test_download_recusa_id_com_separador_ou_dotdot(api, jobs, tmp_path, job_id):
    (tmp_path / "saida" / "a" / "b").mkdir(parents=True)
    alvo = tmp_path / "saida" / "t-legado" / "mapa.pdf"
    jobs.registros["t-ana"].update(id=job_id, mapa_path=str(alvo))
    assert erro(lambda: api.download_job_file("t-ana", "mapa", ANA)) == (404, "Arquivo não encontrado")


def test_download_recusa_pasta_do_job_como_symlink_para_outro_job(api, jobs, tmp_path):
    import shutil

    pasta = tmp_path / "saida" / "t-ana"
    shutil.rmtree(pasta)
    pasta.symlink_to(tmp_path / "saida" / "t-legado", target_is_directory=True)
    jobs.registros["t-ana"]["mapa_path"] = str(tmp_path / "saida" / "t-legado" / "mapa.pdf")
    assert erro(lambda: api.download_job_file("t-ana", "mapa", ANA)) == (404, "Arquivo não encontrado")
