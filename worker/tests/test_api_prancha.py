"""Prancha no envio assíncrono: campos, logo, limites, sobras, resposta pública e rota da logo."""

import importlib
import io
import json
import os
import struct
import sys
import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from test_api_upload_limit import PEDACO, SESSAO, chamar

ANA = {"user_id": "u-ana", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member"}
BIA = {"user_id": "u-bia", "email": "bia@geolume.test", "tenant_id": "demo", "role": "member"}
ADMIN = {"user_id": "u-adm", "email": "adm@geolume.test", "tenant_id": "demo", "role": "admin"}
OUTRO_TENANT = {"user_id": "u-out", "email": "out@x.test", "tenant_id": "outro", "role": "admin"}
MB = 1024 * 1024
GEOJSON = b'{"type": "FeatureCollection", "features": []}'


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("qgis.core")
    pytest.importorskip("fastapi")
    import db

    monkeypatch.setattr(db, "init_db", lambda *args, **kwargs: None)
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)
    module = importlib.import_module("api")
    saida = tmp_path / "saida"
    (saida / "inputs").mkdir(parents=True)
    monkeypatch.setattr(module, "OUTPUT_DIR", saida)
    monkeypatch.setattr(module, "INPUTS_DIR", saida / "inputs")

    efeitos = SimpleNamespace(jobs=[], filas=[], saida=saida)
    monkeypatch.setattr(module, "create_db_job", lambda *a, **k: efeitos.jobs.append((a, k)))
    monkeypatch.setattr(module, "set_task_id", lambda *a: None)
    monkeypatch.setattr(module, "falhar_enfileiramento", lambda *a, **k: True, raising=False)

    def delay(*args):
        efeitos.filas.append(args)
        return SimpleNamespace(id="task-x")

    monkeypatch.setattr(module.process_job, "delay", delay)
    agora = datetime.now(timezone.utc)
    sessoes = {module.hash_token(SESSAO): {**ANA, "active": True, "last_seen_at": agora,
                                           "expires_at": agora + timedelta(hours=1)}}
    monkeypatch.setattr(module, "get_session_user", sessoes.get)
    monkeypatch.setattr(module, "touch_session", lambda *a: None)
    module.efeitos = efeitos
    yield module
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)


def multipart(campos: dict[str, str] | None = None, arquivos: dict[str, tuple[str, bytes]] | None = None):
    limite = "geolumeLimite7MA4YWxk"
    corpo = b""
    for nome, valor in (campos or {}).items():
        corpo += (f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n').encode()
    for nome, (arquivo, conteudo) in (arquivos or {"file": ("lote.geojson", GEOJSON)}).items():
        corpo += (f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"; filename="{arquivo}"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n").encode() + conteudo + b"\r\n"
    return corpo + f"--{limite}--\r\n".encode(), f"multipart/form-data; boundary={limite}"


def enviar(api, campos=None, logo=None, geojson=GEOJSON, **opcoes):
    arquivos = {"file": ("lote.geojson", geojson)}
    if logo is not None:
        arquivos["logo"] = logo
    return chamar(api, "/jobs/async", *multipart(campos, arquivos), **opcoes)


def png(tamanho=(300, 100), modo="RGB"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(modo, tamanho, (20, 90, 160, 255)[: len(modo)]).save(buffer, "PNG")
    return buffer.getvalue()


def png_ruido(lado):
    from PIL import Image

    buffer = io.BytesIO()
    Image.frombytes("RGB", (lado, lado), os.urandom(lado * lado * 3)).save(buffer, "PNG", compress_level=0)
    return buffer.getvalue()


def png_bomba():
    def bloco(tipo, dados):
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados))

    ihdr = struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", ihdr) + bloco(b"IDAT", zlib.compress(b"\x00" * 64)) + bloco(b"IEND", b"")


def arquivos_em(api, pasta):
    caminho = api.efeitos.saida / pasta
    return sorted(p.name for p in caminho.iterdir()) if caminho.exists() else []


def sem_sobras(api):
    assert arquivos_em(api, "inputs") == []
    assert arquivos_em(api, "logos") == []
    assert api.efeitos.jobs == [] and api.efeitos.filas == []


# ---- Envio sem personalização: fluxo de hoje -------------------------------------


def test_sem_campos_o_job_e_o_de_hoje(api):
    status, corpo, _ = enviar(api)
    assert status == 202, corpo
    (_, kwargs), = api.efeitos.jobs
    assert kwargs["prancha"] is None
    (fila,) = api.efeitos.filas
    assert len(fila) == 2  # mesma mensagem de antes: (input_path, job_id)
    assert arquivos_em(api, "logos") == []


def test_campos_vazios_contam_como_ausentes(api):
    campos = {"projeto": "  ", "responsavel": "", "cor_contorno": "", "cor_preenchimento": "", "legenda": ""}
    status, corpo, _ = enviar(api, campos, logo=("", b""))
    assert status == 202, corpo
    assert api.efeitos.jobs[0][1]["prancha"] is None
    assert len(api.efeitos.filas[0]) == 2


# ---- Campos --------------------------------------------------------------------


def test_campos_validados_e_normalizados_vao_ao_banco_e_a_fila(api):
    campos = {"projeto": "  Loteamento Sol ", "responsavel": "Eng. Ana", "cor_contorno": "#1a2b3c",
              "cor_preenchimento": "#00ff7f", "legenda": "lateral"}
    status, corpo, _ = enviar(api, campos)
    assert status == 202, corpo
    esperado = {"projeto": "Loteamento Sol", "responsavel": "Eng. Ana", "cor_contorno": "#1A2B3C",
                "cor_preenchimento": "#00FF7F", "legenda": "lateral", "logo": False,
                "estilo": "padrao", "alfa_preenchimento": 0.35}
    assert api.efeitos.jobs[0][1]["prancha"] == esperado
    entrada, job_id, prancha = api.efeitos.filas[0]
    assert prancha == esperado and job_id == corpo["job_id"]


def test_legenda_inferior_vai_ao_banco_e_a_fila(api):
    status, corpo, _ = enviar(api, {"legenda": "inferior"})
    assert status == 202, corpo
    assert api.efeitos.jobs[0][1]["prancha"]["legenda"] == "inferior"
    assert api.efeitos.filas[0][2]["legenda"] == "inferior"


def test_legenda_adulterada_tem_mensagem_fixa(api):
    status, corpo, _ = enviar(api, {"legenda": "<script>topo</script>"}, logo=("logo.png", png()))
    assert status == 422
    assert corpo["detail"] == "legenda_invalida: Legenda deve ser 'nenhuma', 'lateral' ou 'inferior'."
    sem_sobras(api)


def test_expressao_qgis_nos_campos_e_aceita_como_texto(api):
    status, _, _ = enviar(api, {"projeto": "[% env('PATH') %]"})
    assert status == 202
    assert api.efeitos.jobs[0][1]["prancha"]["projeto"] == "[% env('PATH') %]"


@pytest.mark.parametrize("campos,codigo", [
    ({"projeto": "a" * 101}, "projeto_invalido"),
    ({"responsavel": "Ana\nSouza"}, "responsavel_invalido"),
    ({"cor_contorno": "red"}, "cor_invalida"),
    ({"cor_preenchimento": "#FFF"}, "cor_invalida"),
    ({"legenda": "superior"}, "legenda_invalida"),
    ({"legenda": "topo"}, "legenda_invalida"),
    ({"legenda": "Inferior"}, "legenda_invalida"),
])
def test_campo_invalido_recusa_com_422_sem_sobras(api, campos, codigo):
    status, corpo, _ = enviar(api, campos, logo=("logo.png", png()))
    assert status == 422
    assert corpo["detail"].startswith(f"{codigo}: ")
    sem_sobras(api)


# ---- Logo ----------------------------------------------------------------------


def test_logo_normalizada_salva_so_pelo_job_id(api):
    from PIL import Image

    status, corpo, _ = enviar(api, {"projeto": "X"}, logo=("../../etc/passwd.png", png((3000, 1000))))
    assert status == 202, corpo
    assert arquivos_em(api, "logos") == [f"{corpo['job_id']}.png"]
    with Image.open(api.efeitos.saida / "logos" / f"{corpo['job_id']}.png") as imagem:
        assert (imagem.format, imagem.size) == ("PNG", (1000, 333))
    assert api.efeitos.jobs[0][1]["prancha"]["logo"] is True
    assert api.efeitos.filas[0][2]["logo"] is True
    assert not (api.efeitos.saida.parent / "etc").exists()


def test_so_logo_ja_personaliza(api):
    status, _, _ = enviar(api, logo=("marca.jpg", png()))
    assert status == 202
    assert api.efeitos.jobs[0][1]["prancha"]["logo"] is True


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.mark.parametrize("logo,codigo", [
    (("logo.svg", SVG), "logo_invalida"),
    (("logo.png", SVG), "logo_invalida"),  # extensão mente
    (("logo.png", b"\x89PNG\r\n\x1a\n" + b"lixo" * 50), "logo_invalida"),
    (("logo.gif", b"GIF89a" + b"\x00" * 50), "logo_invalida"),
    (("logo.png", png_bomba()), "logo_dimensoes"),
], ids=["svg", "svg-como-png", "png-falso", "gif", "bomba"])
def test_logo_invalida_recusa_com_422_sem_sobras(api, logo, codigo):
    status, corpo, _ = enviar(api, {"projeto": "X"}, logo=logo)
    assert status == 422
    assert corpo["detail"].startswith(f"{codigo}: ")
    sem_sobras(api)


def test_logo_acima_de_2_mb_recusa_com_413_sem_sobras(api):
    status, corpo, _ = enviar(api, logo=("logo.png", png() + b"\x00" * (2 * MB)))
    assert (status, corpo) == (413, {"detail": "Logo maior que 2 MB."})
    sem_sobras(api)


def test_geojson_acima_de_10_mb_nao_deixa_a_logo(api):
    status, corpo, _ = enviar(api, logo=("logo.png", png()), geojson=b" " * (10 * MB + 1))
    assert (status, corpo) == (413, {"detail": "Arquivo maior que 10 MB."})
    sem_sobras(api)


def test_geojson_de_10_mb_e_logo_perto_de_2_mb_cabem_nos_12_mb(api):
    logo = png_ruido(800)
    assert 1.8 * MB < len(logo) <= 2 * MB
    status, corpo, _ = enviar(api, logo=("logo.png", logo), geojson=b" " * (10 * MB))
    assert status == 202, corpo
    assert arquivos_em(api, "logos") == [f"{corpo['job_id']}.png"]


def test_envio_acima_de_12_mb_recusa_sem_ler(api):
    status, corpo, lidos = enviar(api, logo=("logo.png", b"\x00" * (3 * MB)), geojson=b" " * (10 * MB))
    assert (status, corpo, lidos) == (413, {"detail": "Envio maior que 12 MB."}, 0)
    sem_sobras(api)


def test_envio_acima_de_12_mb_sem_content_length_para_de_ler(api):
    status, corpo, lidos = enviar(api, logo=("logo.png", b"\x00" * (3 * MB)), geojson=b" " * (10 * MB),
                                  content_length=False)
    assert (status, corpo) == (413, {"detail": "Envio maior que 12 MB."})
    assert lidos <= 12 * MB + PEDACO
    sem_sobras(api)


def test_falha_ao_enfileirar_apaga_logo_e_entrada(api, monkeypatch):
    def cai(*args):
        raise ConnectionError("redis fora")

    monkeypatch.setattr(api.process_job, "delay", cai)
    status, _, _ = enviar(api, {"projeto": "X"}, logo=("logo.png", png()))
    assert status == 503
    assert arquivos_em(api, "inputs") == [] and arquivos_em(api, "logos") == []


# ---- Resposta pública e rota da logo ---------------------------------------------


@pytest.fixture
def jobs(api, monkeypatch):
    registros = {}

    def pode(job, user):
        return job["tenant_id"] == user["tenant_id"] and (user["role"] == "admin" or job["owner_id"] == user["user_id"])

    monkeypatch.setattr(api, "get_job_for", lambda t, u: dict(registros[t]) if t in registros and pode(registros[t], u) else None)
    monkeypatch.setattr(api, "list_jobs_for", lambda u, limit=20: [dict(j) for j in registros.values() if pode(j, u)])
    logos = api.efeitos.saida / "logos"
    logos.mkdir()

    def novo(task_id, prancha, *, owner="u-ana", logo=True):
        registros[task_id] = {"id": f"job-{task_id}", "task_id": task_id, "status": "queued", "erro": None,
                              "input_filename": "lote.geojson", "created_at": None, "completed_at": None,
                              "owner_id": owner, "tenant_id": "demo", "prancha": prancha}
        if logo:
            (logos / f"job-{task_id}.png").write_bytes(png())

    novo("t-antigo", None, logo=False)
    novo("t-logo", {"projeto": "Loteamento Sol", "responsavel": None, "cor_contorno": "#1A2B3C",
                    "cor_preenchimento": "#00FF7F", "legenda": "lateral", "logo": True})
    novo("t-sem-logo", {"projeto": "X", "responsavel": None, "cor_contorno": "#C80000",
                        "cor_preenchimento": "#FFC800", "legenda": "nenhuma", "logo": False})
    return SimpleNamespace(registros=registros, logos=logos, novo=novo)


def test_job_antigo_tem_prancha_nula(api, jobs):
    assert api.job_status("t-antigo", ANA)["prancha"] is None


def test_prancha_publica_com_url_da_logo_sem_caminhos(api, jobs):
    prancha = api.job_status("t-logo", ANA)["prancha"]
    assert prancha == {"projeto": "Loteamento Sol", "responsavel": None, "cor_contorno": "#1A2B3C",
                       "cor_preenchimento": "#00FF7F", "legenda": "lateral", "logo": "/jobs/t-logo/logo",
                       "estilo": "padrao", "alfa_preenchimento": 0.35}
    assert api.job_status("t-sem-logo", ANA)["prancha"]["logo"] is None
    texto = json.dumps(api.jobs(20, ANA), default=str)
    assert "/saida" not in texto and "u-ana" not in texto and "demo" not in texto


def test_prancha_com_legenda_inferior_e_devolvida(api, jobs):
    jobs.novo("t-inferior", {"projeto": None, "responsavel": None, "cor_contorno": "#C80000",
                             "cor_preenchimento": "#FFC800", "legenda": "inferior", "logo": False}, logo=False)
    assert api.job_status("t-inferior", ANA)["prancha"]["legenda"] == "inferior"


def test_prancha_corrompida_no_banco_vira_nula(api, jobs):
    jobs.novo("t-ruim", {"cor_contorno": "javascript:alert(1)", "logo": "/etc/passwd"}, logo=False)
    assert api.job_status("t-ruim", ANA)["prancha"] is None


def _corpo(resposta):
    async def ler():
        return b"".join([parte async for parte in resposta.body_iterator])

    import asyncio

    return asyncio.run(ler())


def test_dono_e_admin_baixam_a_logo(api, jobs):
    for user in (ANA, ADMIN):
        resposta = api.job_logo("t-logo", user)
        assert resposta.media_type == "image/png"
        assert _corpo(resposta) == (jobs.logos / "job-t-logo.png").read_bytes()


def _erro(chamada):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        chamada()
    return exc.value.status_code, exc.value.detail


@pytest.mark.parametrize("user", [BIA, OUTRO_TENANT], ids=["outro-membro", "outro-tenant"])
def test_logo_alheia_responde_404_como_job_inexistente(api, jobs, user):
    assert _erro(lambda: api.job_logo("t-logo", user)) == (404, "Job não encontrado")
    assert _erro(lambda: api.job_logo("nao-existe", user)) == (404, "Job não encontrado")


def test_job_sem_logo_responde_404(api, jobs):
    for task_id in ("t-antigo", "t-sem-logo"):
        assert _erro(lambda: api.job_logo(task_id, ANA)) == (404, "Logo não disponível")


def test_logo_apagada_ou_trocada_por_link_responde_404(api, jobs, tmp_path):
    (jobs.logos / "job-t-logo.png").unlink()
    assert _erro(lambda: api.job_logo("t-logo", ANA)) == (404, "Logo não disponível")
    segredo = tmp_path / "segredo.png"
    segredo.write_bytes(png())
    (jobs.logos / "job-t-logo.png").symlink_to(segredo)
    assert _erro(lambda: api.job_logo("t-logo", ANA)) == (404, "Logo não disponível")


def test_rota_da_logo_exige_usuario(api):
    rota = next(r for r in api.app.routes if getattr(r, "path", "") == "/jobs/{task_id}/logo")
    assert api.current_user in {dep.call for dep in rota.dependant.dependencies}


# ---- Estilo e alfa do preenchimento ----------------------------------------------

MENSAGEM_ALFA = "alfa_invalido: Opacidade do preenchimento deve estar entre 0 e 1."
MENSAGEM_ESTILO = "estilo_invalido: Estilo do polígono inválido."


def saida_vazia(api):
    """Nada gravado em /saida além das pastas vazias."""
    assert [p for p in api.efeitos.saida.rglob("*") if not p.is_dir()] == []


@pytest.mark.parametrize("valor", ["-0.1", "2", "abc", "0,5", "NaN", "inf", "1e-1", "<b>0.5</b>"])
def test_post_alfa_invalido_422(api, valor):
    status, corpo, _ = enviar(api, {"alfa_preenchimento": valor}, logo=("logo.png", png()))
    assert (status, corpo) == (422, {"detail": MENSAGEM_ALFA})  # fixa: nunca ecoa o valor
    sem_sobras(api)
    saida_vazia(api)


@pytest.mark.parametrize("valor", ["urbano", "Tecnico", " pb", "<script>x</script>"])
def test_post_estilo_invalido_422(api, valor):
    status, corpo, _ = enviar(api, {"estilo": valor}, logo=("logo.png", png()))
    assert (status, corpo) == (422, {"detail": MENSAGEM_ESTILO})
    sem_sobras(api)
    saida_vazia(api)


def test_post_estilo_e_alfa_gravados_e_devolvidos(api, jobs):
    status, corpo, _ = enviar(api, {"estilo": "tecnico", "alfa_preenchimento": "0.6", "cor_contorno": "#112233"})
    assert status == 202, corpo
    gravada = api.efeitos.jobs[0][1]["prancha"]
    assert (gravada["estilo"], gravada["alfa_preenchimento"], gravada["cor_contorno"]) == ("tecnico", 0.6, "#112233")
    assert api.efeitos.filas[0][2] == gravada
    jobs.novo("t-tecnico", gravada, logo=False)
    publica = api.job_status("t-tecnico", ANA)["prancha"]
    assert (publica["estilo"], publica["alfa_preenchimento"]) == ("tecnico", 0.6)


@pytest.mark.parametrize("campos", [{"alfa_preenchimento": "0"}, {"alfa_preenchimento": "1"}, {"estilo": "pb"}])
def test_post_so_estilo_ou_alfa_ja_personaliza(api, campos):
    status, corpo, _ = enviar(api, campos)
    assert status == 202, corpo
    assert api.efeitos.jobs[0][1]["prancha"] is not None
    assert len(api.efeitos.filas[0]) == 3


@pytest.mark.parametrize("campos", [{}, {"estilo": "", "alfa_preenchimento": ""},
                                    {"estilo": "padrao", "alfa_preenchimento": "0.35"}])
def test_post_sem_campos_novos_prancha_nula(api, campos):
    status, corpo, _ = enviar(api, campos)
    assert status == 202, corpo
    assert api.efeitos.jobs[0][1]["prancha"] is None
    assert len(api.efeitos.filas[0]) == 2  # mesma mensagem de antes


def test_job_antigo_devolve_estilo_padrao(api, jobs):
    for task_id in ("t-logo", "t-sem-logo"):  # gravados antes do estilo existir
        prancha = api.job_status(task_id, ANA)["prancha"]
        assert (prancha["estilo"], prancha["alfa_preenchimento"]) == ("padrao", 0.35)
    assert api.job_status("t-antigo", ANA)["prancha"] is None
    listados = {j["task_id"]: j["prancha"] for j in api.jobs(20, ANA)["jobs"]}
    assert listados["t-sem-logo"]["estilo"] == "padrao"


# ---- Catálogo de camadas ---------------------------------------------------------


def obter(api, caminho, cookie=SESSAO):
    """GET pela aplicação ASGI inteira. Devolve (status, headers, json)."""
    import asyncio

    headers = [(b"host", b"localhost:8000")]
    if cookie is not None:
        headers.append((b"cookie", f"geolume_session={cookie}".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": caminho, "raw_path": caminho.encode(), "query_string": b"",
        "root_path": "", "headers": headers, "client": ("127.0.0.1", 50000), "server": ("localhost", 8000),
    }
    mensagens = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensagem):
        mensagens.append(mensagem)

    asyncio.run(api.app(scope, receive, send))
    inicio = next(m for m in mensagens if m["type"] == "http.response.start")
    corpo = b"".join(m.get("body", b"") for m in mensagens if m["type"] == "http.response.body")
    cabecalhos = {k.decode().lower(): v.decode() for k, v in inicio["headers"]}
    return inicio["status"], cabecalhos, json.loads(corpo) if corpo else None


@pytest.mark.parametrize("cookie", [None, "", "tok-expirado-ou-inexistente"], ids=["sem-cookie", "vazio", "invalido"])
def test_camadas_exige_sessao(api, cookie):
    status, _, corpo = obter(api, "/camadas", cookie=cookie)
    assert (status, corpo) == (401, {"detail": "Autenticação necessária"})


def test_camadas_devolve_catalogo(api):
    from geolume_worker.camadas import catalogo_publico

    status, cabecalhos, corpo = obter(api, "/camadas")
    assert status == 200
    assert corpo == json.loads(json.dumps(catalogo_publico()))
    assert cabecalhos["cache-control"] == "private, max-age=300"
    assert cabecalhos["content-type"].startswith("application/json")


def test_camadas_sem_url_nas_indisponiveis(api):
    _, _, corpo = obter(api, "/camadas")
    for camada in corpo["camadas"]:
        if camada["situacao"] != "disponivel":
            assert camada["url"] is None, camada["id"]
    assert [c["id"] for c in corpo["camadas"] if c["exporta_pdf"]] == ["poligono"]


def test_camadas_sem_dados_internos(api):
    status, _, corpo = obter(api, "/camadas")
    assert status == 200
    texto = json.dumps(corpo)
    for proibido in ("/saida", "/app", "u-ana", "demo", "ana@geolume.test", "owner_id", "tenant_id", "user_id"):
        assert proibido not in texto
