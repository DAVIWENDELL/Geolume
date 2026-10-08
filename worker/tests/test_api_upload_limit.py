"""Limite de 10 MB no servidor para POST /jobs e POST /jobs/async, sem confiar no navegador.

Os testes chamam o app ASGI inteiro (middleware, multipart do Starlette, dependências e
endpoint) e contam quantos bytes do corpo o servidor chegou a ler.
"""

import asyncio
import importlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ANA = {"user_id": "u-ana", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member"}
ADMIN = {"user_id": "u-adm", "email": "adm@geolume.test", "tenant_id": "demo", "role": "admin"}
MB = 1024 * 1024
LIMITE = 10 * MB
PEDACO = 256 * 1024
ROTAS = ["/jobs/async", "/jobs"]
# Corpo inteiro: /jobs só leva o GeoJSON; /jobs/async leva GeoJSON + logo (12 MB no total).
TOTAL = {"/jobs": LIMITE + 64 * 1024, "/jobs/async": 12 * MB}
CORPO_GRANDE = {"/jobs": "Arquivo maior que 10 MB.", "/jobs/async": "Envio maior que 12 MB."}
SESSAO = "tok-sessao-valida"


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

    efeitos = SimpleNamespace(jobs=[], processados=[], inputs=inputs)
    monkeypatch.setattr(module, "create_db_job", lambda *a, **k: efeitos.jobs.append((a, k)))
    monkeypatch.setattr(module, "set_task_id", lambda *a: None)
    monkeypatch.setattr(module.process_job, "delay", lambda *a: SimpleNamespace(id="task-x"))

    def run_job(entrada, saida, job_id):
        efeitos.processados.append(entrada.stat().st_size)
        return SimpleNamespace(job_id=job_id, pdf_path="m", memorial_path="n", json_path="r", phases_ms={})

    monkeypatch.setattr(module, "run_job", run_job)
    # Sessão válida de verdade: o limite de upload confere o cookie antes de ler o corpo.
    agora = datetime.now(timezone.utc)
    sessoes = {module.hash_token(SESSAO): {**ADMIN, "active": True, "last_seen_at": agora,
                                           "expires_at": agora + timedelta(hours=1)}}
    monkeypatch.setattr(module, "get_session_user", sessoes.get)
    monkeypatch.setattr(module, "touch_session", lambda *a: None)
    monkeypatch.setattr(module, "delete_session", lambda *a: None)
    # POST /jobs exige administrador; /jobs/async, qualquer usuário (a regra fica em require_admin).
    module.app.dependency_overrides[module.current_user] = lambda: ADMIN
    module.efeitos = efeitos
    yield module
    module.app.dependency_overrides.clear()
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)


def multipart(conteudo: bytes, nome: str = "lote.geojson") -> tuple[bytes, str]:
    limite = "geolumeLimite7MA4YWxk"
    corpo = (
        f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{nome}"\r\n'
        "Content-Type: application/geo+json\r\n\r\n"
    ).encode() + conteudo + f"\r\n--{limite}--\r\n".encode()
    return corpo, f"multipart/form-data; boundary={limite}"


def chamar(api, caminho, corpo, tipo, *, content_length=True, desconectar_apos=None, cookie=SESSAO):
    """Envia o corpo em pedaços de 256 KiB. Devolve (status, json, bytes lidos pelo servidor).

    content_length: True declara o tamanho real, False omite o header, um número declara esse valor.
    cookie: token da sessão (None manda o pedido sem cookie).
    """
    pedacos = [corpo[i:i + PEDACO] for i in range(0, len(corpo), PEDACO)]
    lidos = []
    mensagens = []
    headers = [(b"host", b"localhost:8000"), (b"content-type", tipo.encode()), (b"x-geolume-csrf", b"1")]
    if cookie is not None:
        headers.append((b"cookie", f"geolume_session={cookie}".encode()))
    if content_length is not False:
        declarado = len(corpo) if content_length is True else content_length
        headers.append((b"content-length", str(declarado).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": caminho, "raw_path": caminho.encode(), "query_string": b"",
        "root_path": "", "headers": headers, "client": ("127.0.0.1", 50000), "server": ("localhost", 8000),
    }

    async def receive():
        i = len(lidos)
        if i >= len(pedacos) or (desconectar_apos is not None and i >= desconectar_apos):
            return {"type": "http.disconnect"}
        lidos.append(len(pedacos[i]))
        return {"type": "http.request", "body": pedacos[i], "more_body": i < len(pedacos) - 1}

    async def send(mensagem):
        mensagens.append(mensagem)

    asyncio.run(api.app(scope, receive, send))
    inicio = [m for m in mensagens if m["type"] == "http.response.start"]
    status = inicio[0]["status"] if inicio else None
    resposta = b"".join(m.get("body", b"") for m in mensagens if m["type"] == "http.response.body")
    return status, (json.loads(resposta) if resposta else None), sum(lidos)


def sem_sobras(api):
    assert list(api.efeitos.inputs.iterdir()) == []
    assert api.efeitos.jobs == []
    assert api.efeitos.processados == []


# ---- No limite ------------------------------------------------------------


def test_async_aceita_arquivo_de_exatamente_10_mb(api):
    status, corpo, _ = chamar(api, "/jobs/async", *multipart(b" " * LIMITE))
    assert status == 202, corpo
    salvos = list(api.efeitos.inputs.iterdir())
    assert len(salvos) == 1 and salvos[0].stat().st_size == LIMITE
    assert len(api.efeitos.jobs) == 1


def test_sync_aceita_arquivo_de_exatamente_10_mb(api):
    status, corpo, _ = chamar(api, "/jobs", *multipart(b" " * LIMITE))
    assert status == 200, corpo
    assert api.efeitos.processados == [LIMITE]


# ---- Acima do limite ------------------------------------------------------


@pytest.mark.parametrize("rota", ROTAS)
def test_um_byte_acima_do_limite_recebe_413_sem_sobras(api, rota):
    status, corpo, _ = chamar(api, rota, *multipart(b" " * (LIMITE + 1)))
    assert status == 413
    assert corpo == {"detail": "Arquivo maior que 10 MB."}
    sem_sobras(api)


@pytest.mark.parametrize("rota", ROTAS)
def test_content_length_acima_do_limite_recusa_sem_ler_o_corpo(api, rota):
    status, corpo, lidos = chamar(api, rota, *multipart(b" " * (40 * MB)))
    assert status == 413
    assert corpo == {"detail": CORPO_GRANDE[rota]}
    assert lidos == 0
    sem_sobras(api)


@pytest.mark.parametrize("rota", ROTAS)
def test_sem_content_length_para_de_ler_logo_depois_do_limite(api, rota):
    status, corpo, lidos = chamar(api, rota, *multipart(b" " * (40 * MB)), content_length=False)
    assert status == 413
    assert corpo == {"detail": CORPO_GRANDE[rota]}
    assert lidos <= TOTAL[rota] + PEDACO  # não copia os 40 MB antes de recusar
    sem_sobras(api)


@pytest.mark.parametrize("rota", ROTAS)
def test_content_length_mentiroso_nao_fura_o_limite(api, rota):
    # Declara 1 KB e manda 40 MB: quem conta é o corpo que chega.
    status, _, lidos = chamar(api, rota, *multipart(b" " * (40 * MB)), content_length=1024)
    assert status in (400, 413)
    assert lidos <= TOTAL[rota] + PEDACO
    sem_sobras(api)


# ---- Upload interrompido --------------------------------------------------


@pytest.mark.parametrize("rota", ROTAS)
def test_upload_interrompido_nao_cria_job_nem_arquivo(api, rota):
    status, corpo, lidos = chamar(api, rota, *multipart(b" " * (5 * MB)), desconectar_apos=4)
    assert lidos == 4 * PEDACO
    assert status != 500
    sem_sobras(api)


def test_copia_interrompida_no_endpoint_apaga_o_arquivo_parcial(api):
    from fastapi import UploadFile

    class Quebra(io.RawIOBase):
        def __init__(self):
            self.vezes = 0

        def readable(self):
            return True

        def read(self, n=-1):
            self.vezes += 1
            if self.vezes > 1:
                raise OSError("conexão caiu no meio da cópia")
            return b" " * PEDACO

    with pytest.raises(OSError):
        asyncio.run(api.enqueue_job(UploadFile(file=Quebra(), filename="lote.geojson"), ANA))
    sem_sobras(api)


# ---- Mensagens de erro ----------------------------------------------------


def test_erro_inesperado_no_sync_nao_expoe_caminho_nem_excecao(api, monkeypatch):
    def quebra(entrada, saida, job_id):
        raise RuntimeError(f"falha ao abrir {saida}/{job_id}/mapa.qgz")

    monkeypatch.setattr(api, "run_job", quebra)
    status, corpo, _ = chamar(api, "/jobs", *multipart(b"{}"))
    assert status == 422
    assert corpo == {"detail": "Não foi possível processar o arquivo."}


def test_erro_de_validacao_no_sync_continua_explicando_o_problema(api, monkeypatch):
    from geolume_worker.errors import InvalidInputError

    def recusa(entrada, saida, job_id):
        raise InvalidInputError("poligono_com_furos", "O polígono tem furos.")

    monkeypatch.setattr(api, "run_job", recusa)
    status, corpo, _ = chamar(api, "/jobs", *multipart(b"{}"))
    assert status == 422
    assert corpo == {"detail": "poligono_com_furos: O polígono tem furos."}


@pytest.mark.parametrize(
    "gravado, exibido",
    [
        ("geometria_invalida: O polígono é inválido (ex.: autointerseção).",
         "geometria_invalida: O polígono é inválido (ex.: autointerseção)."),
        ("[Errno 2] No such file or directory: '/saida/inputs/abc-lote.geojson'", "Falha no processamento do job."),
        ("Traceback (most recent call last):\n  File \"/app/celery_app.py\"", "Falha no processamento do job."),
        ("codigo_desconhecido: /saida/x", "Falha no processamento do job."),
        (None, None),
    ],
)
def test_status_so_mostra_erro_de_validacao_conhecido(api, monkeypatch, gravado, exibido):
    registro = {"id": "j1", "status": "failed", "erro": gravado, "mapa_path": None,
                "memorial_path": None, "resultado_path": None}
    monkeypatch.setattr(api, "get_job_for", lambda task_id, user: registro)
    assert api.job_status("t1", ANA)["erro"] == exibido


# ---- Documentação da API ---------------------------------------------------


@pytest.mark.parametrize("caminho", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_documentacao_da_api_nao_e_publica(api, caminho):
    api.app.dependency_overrides.clear()
    mensagens = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(mensagem):
        mensagens.append(mensagem)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": caminho, "raw_path": caminho.encode(), "query_string": b"",
        "root_path": "", "headers": [(b"host", b"localhost:8000")], "client": ("127.0.0.1", 50000),
        "server": ("localhost", 8000),
    }
    asyncio.run(api.app(scope, receive, send))
    assert next(m for m in mensagens if m["type"] == "http.response.start")["status"] == 404
    assert api.app.openapi_url is None and api.app.docs_url is None and api.app.redoc_url is None


def test_listagem_tambem_so_mostra_erro_de_validacao_conhecido(api, monkeypatch):
    linhas = [{"id": "j1", "erro": "[Errno 2] No such file or directory: '/saida/x'"},
              {"id": "j2", "erro": "sem_feicoes: O arquivo não tem feições."},
              {"id": "j3", "erro": None}]
    monkeypatch.setattr(api, "list_jobs_for", lambda user, limit: [dict(l) for l in linhas])
    erros = [j["erro"] for j in api.jobs(20, ANA)["jobs"]]
    assert erros == ["Falha no processamento do job.", "sem_feicoes: O arquivo não tem feições.", None]


def test_lista_de_codigos_cobre_todo_erro_de_validacao_do_worker(api):
    import re
    from pathlib import Path

    fonte = "".join(p.read_text(encoding="utf-8") for p in Path(api.__file__).parent.glob("geolume_worker/*.py"))
    levantados = set(re.findall(r'InvalidInputError\(\s*"([a-z_]+)"', fonte))
    assert levantados and levantados <= api.CODIGOS_DE_VALIDACAO


# ---- Falha depois da cópia (revisão Codex) --------------------------------


def _falha_em(api, monkeypatch, etapa):
    alvo = api.process_job if etapa == "delay" else api

    def quebra(*a, **k):
        raise RuntimeError("redis://redis:6379 recusou a conexão em /app/celery_app.py")

    monkeypatch.setattr(alvo, etapa, quebra)


def _registrar_falhas(api, monkeypatch, venceu=True):
    """Registra o failed condicional pedido pela API; venceu = o job ainda estava queued."""
    falhas = []

    def falhar_enfileiramento(job_id, erro):
        falhas.append(erro)
        return venceu

    monkeypatch.setattr(api, "falhar_enfileiramento", falhar_enfileiramento, raising=False)
    return falhas


@pytest.mark.parametrize("etapa", ["create_db_job", "delay", "set_task_id"])
def test_falha_ao_registrar_ou_enfileirar_nao_deixa_arquivo_nem_job_pendente(api, monkeypatch, etapa):
    falhas = _registrar_falhas(api, monkeypatch)
    _falha_em(api, monkeypatch, etapa)
    status, corpo, _ = chamar(api, "/jobs/async", *multipart(b"{}"))
    assert status == 503
    assert corpo == {"detail": "Não foi possível enfileirar o job. Tente novamente."}
    assert list(api.efeitos.inputs.iterdir()) == []
    # Job já registrado não fica "na fila" para sempre: vira falha, só pelo UPDATE condicional.
    assert falhas == ([] if etapa == "create_db_job" else ["Falha ao enfileirar o job."])


@pytest.mark.parametrize("etapa", ["delay", "set_task_id"])
def test_job_ja_mudado_por_outro_processo_e_preservado(api, monkeypatch, caplog, etapa):
    # Ex.: a mensagem saiu e o Celery já marcou started (ou concluiu) antes de set_task_id falhar.
    falhas = _registrar_falhas(api, monkeypatch, venceu=False)
    _falha_em(api, monkeypatch, etapa)
    with caplog.at_level("WARNING"):
        status, corpo, _ = chamar(api, "/jobs/async", *multipart(b"{}"))
    assert (status, corpo) == (503, {"detail": "Não foi possível enfileirar o job. Tente novamente."})
    assert falhas == ["Falha ao enfileirar o job."]
    assert "não estava em queued" in caplog.text
    # O upload é do job que outro processo assumiu: não é apagado debaixo dele.
    assert [p.name.endswith("-lote.geojson") for p in api.efeitos.inputs.iterdir()] == [True]


def test_banco_fora_ao_marcar_failed_limpa_e_responde_503(api, monkeypatch, caplog):
    def banco_fora(job_id, erro):
        raise ConnectionError("banco indisponível")

    monkeypatch.setattr(api, "falhar_enfileiramento", banco_fora, raising=False)
    _falha_em(api, monkeypatch, "set_task_id")
    with caplog.at_level("ERROR"):
        status, corpo, _ = chamar(api, "/jobs/async", *multipart(b"{}"))
    assert (status, corpo) == (503, {"detail": "Não foi possível enfileirar o job. Tente novamente."})
    assert "ficou sem marcar a falha" in caplog.text
    # Job fica queued sem task_id: a regra de job preso o decide (enfileiramento_perdido).
    assert list(api.efeitos.inputs.iterdir()) == []


def test_api_nao_grava_status_sem_condicao(api):
    assert not hasattr(api, "update_job")


# ---- Upload sem sessão: 401 antes de ler o corpo ----------------------------


@pytest.mark.parametrize("rota", ROTAS)
@pytest.mark.parametrize("cookie", [None, "tok-inexistente"], ids=["sem-cookie", "cookie-invalido"])
@pytest.mark.parametrize("content_length", [True, False, 50 * MB], ids=["declarado", "sem-tamanho", "declarado-50mb"])
def test_upload_anonimo_recebe_401_sem_ler_o_corpo(api, rota, cookie, content_length):
    api.app.dependency_overrides.clear()  # a regra de verdade, sem usuário injetado
    status, corpo, lidos = chamar(api, rota, *multipart(b" " * (2 * MB)), content_length=content_length, cookie=cookie)
    assert status == 401
    assert corpo == {"detail": "Autenticação necessária"}
    assert lidos == 0
    sem_sobras(api)


@pytest.mark.parametrize("rota", ROTAS)
def test_sessao_expirada_no_upload_recebe_401_sem_ler_o_corpo(api, rota, monkeypatch):
    api.app.dependency_overrides.clear()
    antiga = datetime.now(timezone.utc) - timedelta(hours=13)
    apagadas = []
    monkeypatch.setattr(api, "get_session_user", lambda digest: {**ADMIN, "active": True, "last_seen_at": antiga,
                                                                   "expires_at": antiga + timedelta(hours=12)})
    monkeypatch.setattr(api, "delete_session", apagadas.append)
    status, corpo, lidos = chamar(api, rota, *multipart(b"{}"))
    assert (status, corpo, lidos) == (401, {"detail": "Autenticação necessária"}, 0)
    assert apagadas == [api.hash_token(SESSAO)]
    sem_sobras(api)


def test_upload_autenticado_sem_override_continua_funcionando(api):
    api.app.dependency_overrides.clear()
    status, corpo, _ = chamar(api, "/jobs/async", *multipart(b"{}"))
    assert status == 202, corpo
    assert len(api.efeitos.jobs) == 1


def test_upload_autenticado_continua_sujeito_ao_limite(api):
    api.app.dependency_overrides.clear()
    status, corpo, _ = chamar(api, "/jobs/async", *multipart(b" " * (LIMITE + 1)))
    assert (status, corpo) == (413, {"detail": "Arquivo maior que 10 MB."})
    sem_sobras(api)


@pytest.mark.parametrize("caminho, metodo", [("/health", "GET"), ("/auth/me", "GET"), ("/jobs", "GET")])
def test_outras_rotas_nao_passam_pela_checagem_do_upload(api, monkeypatch, caminho, metodo):
    """Só POST nas rotas de upload é barrado antes do corpo; o resto segue o caminho normal."""
    api.app.dependency_overrides.clear()
    consultas = []
    monkeypatch.setattr(api, "get_session_user", lambda digest: consultas.append(digest))
    monkeypatch.setattr(api, "list_jobs_for", lambda user, limit: [])
    mensagens = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensagem):
        mensagens.append(mensagem)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": metodo,
        "scheme": "http", "path": caminho, "raw_path": caminho.encode(), "query_string": b"",
        "root_path": "", "headers": [(b"host", b"localhost:8000")], "client": ("127.0.0.1", 50000),
        "server": ("localhost", 8000),
    }
    asyncio.run(api.app(scope, receive, send))
    status = next(m for m in mensagens if m["type"] == "http.response.start")["status"]
    assert status == (200 if caminho == "/health" else 401)
    assert consultas == []  # sem cookie, nem chega a consultar sessão
