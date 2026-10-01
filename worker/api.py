"""API HTTP da PoC do worker GeoLume.

`POST /jobs` mantém o fluxo síncrono de validação. `POST /jobs/async` usa
Redis/Celery e retorna imediatamente com um task_id.
"""

import json
import logging
import os
import stat
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from celery_app import process_job
from auth import (
    MAX_AGE,
    TOUCH_EVERY,
    burn_password_check,
    hash_token,
    new_session_token,
    normalize_email,
    session_is_valid,
    verify_password,
)
from db import (
    claim_login_attempt,
    create_job as create_db_job,
    create_session,
    delete_session,
    get_job_for,
    get_session_user,
    get_user_by_email,
    init_db,
    list_jobs_for,
    reset_login_failures,
    set_task_id,
    touch_session,
    update_job,
)
from geolume_worker.camadas import catalogo_publico
from geolume_worker.errors import InvalidInputError
from geolume_worker.input_loader import MAX_BYTES as MAX_UPLOAD_BYTES
from geolume_worker.job import run_job
from geolume_worker.prancha import JOB_ID, LOGO_MAX_BYTES, caminho_logo, normalizar_logo, validar_prancha
from geolume_worker.qgis_session import qgis_session

OUTPUT_DIR = Path("/saida")
INPUTS_DIR = OUTPUT_DIR / "inputs"
INPUT_CHUNK_BYTES = 256 * 1024

MULTIPART_FOLGA = 64 * 1024  # cabeçalhos e delimitadores do multipart em volta do arquivo
UPLOAD_GRANDE = f"Arquivo maior que {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
ENVIO_MAX_BYTES = 12 * 1024 * 1024  # /jobs/async: GeoJSON (10 MB) + logo (2 MB) + campos, no total
LOGO_GRANDE = f"Logo maior que {LOGO_MAX_BYTES // (1024 * 1024)} MB."
# Limite do corpo inteiro por rota de upload, checado antes do multipart.
UPLOAD_LIMITES = {
    "/jobs": (MAX_UPLOAD_BYTES + MULTIPART_FOLGA, UPLOAD_GRANDE),
    "/jobs/async": (ENVIO_MAX_BYTES, f"Envio maior que {ENVIO_MAX_BYTES // (1024 * 1024)} MB."),
}

# Códigos de InvalidInputError: só essas mensagens chegam ao cliente como vieram.
CODIGOS_DE_VALIDACAO = frozenset({
    "arquivo_muito_grande", "arquivo_nao_encontrado", "crs_nao_suportado", "excesso_de_vertices",
    "fora_da_cobertura", "geometria_invalida", "geometria_nao_poligonal", "json_invalido",
    "multiplas_feicoes", "multiplas_partes", "poligono_com_furos", "sem_feicoes",
    # Prancha (revalidada no worker)
    "alfa_invalido", "cor_invalida", "estilo_invalido", "legenda_invalida", "logo_ausente", "logo_dimensoes",
    "logo_grande", "logo_invalida",
    "projeto_invalido", "responsavel_invalido",
})
ERRO_DO_JOB = "Falha no processamento do job."

logger = logging.getLogger("geolume.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    with qgis_session():
        yield


# Sem /docs, /redoc e /openapi.json: o esquema da API não fica público.
app = FastAPI(
    title="GeoLume Worker API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class _CorpoGrande(Exception):
    pass


async def _responder(send, status: int, detalhe: str) -> None:
    """Resposta JSON que fecha a conexão: o corpo do upload fica sem ler."""
    corpo = json.dumps({"detail": detalhe}, ensure_ascii=False, separators=(",", ":")).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode()),
                    (b"connection", b"close")],
    })
    await send({"type": "http.response.body", "body": corpo})


async def _sessao_do_upload(scope) -> bool:
    try:
        await run_in_threadpool(current_user, Request(scope))
    except HTTPException:
        return False
    return True


class UploadLimit:
    """Sessão e limite do corpo dos uploads antes do multipart.

    O Starlette lê o corpo inteiro para um arquivo temporário antes de chamar o endpoint,
    então as duas checagens precisam estar aqui: sem sessão válida é 401 sem ler nada;
    Content-Length acima do limite é recusado sem ler nada, e sem Content-Length (ou com
    um valor falso) a leitura para no primeiro bloco que passa.
    """

    def __init__(self, app, limites: dict[str, tuple[int, str]]):
        self.app = app
        self.limites = limites

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in self.limites:
            await self.app(scope, receive, send)
            return
        limite, grande = self.limites[scope["path"]]
        if not await _sessao_do_upload(scope):
            await _responder(send, 401, NAO_AUTENTICADO)
            return
        declarado = dict(scope["headers"]).get(b"content-length", b"")
        if declarado.isdigit() and int(declarado) > limite:
            await _responder(send, 413, grande)
            return

        lidos = 0
        estourou = False

        async def receive_contado():
            nonlocal lidos, estourou
            mensagem = await receive()
            if mensagem["type"] == "http.request":
                lidos += len(mensagem.get("body", b""))
                if lidos > limite:
                    estourou = True
                    raise _CorpoGrande()
            return mensagem

        async def send_se_dentro(mensagem):
            # O FastAPI transforma a interrupção em 400; a resposta que vale é o 413.
            if not estourou:
                await send(mensagem)

        try:
            await self.app(scope, receive_contado, send_se_dentro)
        except Exception:
            if not estourou:
                raise
        if estourou:
            await _responder(send, 413, grande)


app.add_middleware(UploadLimit, limites=UPLOAD_LIMITES)


@app.get("/health")
def health() -> dict[str, str]:
    # Público e fixo: não consulta banco, Redis nem Celery, e não expõe detalhes internos.
    return {"status": "ok", "service": "geolume-worker"}


# ---- Autenticação -------------------------------------------------------

SESSION_COOKIE = "geolume_session"
NAO_AUTENTICADO = "Autenticação necessária"
CSRF_HEADER = "x-geolume-csrf"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def check_csrf(request: Request) -> None:
    """POST só com o header próprio (formulário de outro site não consegue enviá-lo)
    e, se o navegador mandar Origin, ela precisa ser a do próprio servidor."""
    recusada = HTTPException(status_code=403, detail="Requisição recusada")
    if request.headers.get(CSRF_HEADER) != "1":
        raise recusada
    origem = request.headers.get("origin")
    if origem is not None and origem != f"{request.url.scheme}://{request.url.netloc}":
        raise recusada


def current_user(request: Request) -> dict[str, str]:
    nao_autenticado = HTTPException(status_code=401, detail=NAO_AUTENTICADO)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise nao_autenticado
    digest = hash_token(token)
    sessao = get_session_user(digest)
    if not sessao:
        raise nao_autenticado
    agora = _now()
    valida = session_is_valid(last_seen_at=sessao["last_seen_at"], expires_at=sessao["expires_at"], now=agora)
    if not valida or not sessao["active"]:
        delete_session(digest)
        raise nao_autenticado
    if agora - sessao["last_seen_at"] >= TOUCH_EVERY:
        touch_session(digest, agora)
    return {
        "user_id": sessao["user_id"],
        "email": sessao["email"],
        "tenant_id": sessao["tenant_id"],
        "role": sessao["role"],
    }


def require_admin(user: dict = Depends(current_user)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")
    return user


class Credenciais(BaseModel):
    email: str
    password: str


@app.post("/auth/login", dependencies=[Depends(check_csrf)])
def login(credenciais: Credenciais, response: Response) -> dict[str, str]:
    invalido = HTTPException(status_code=401, detail="E-mail ou senha inválidos")
    usuario = get_user_by_email(normalize_email(credenciais.email))
    if not usuario:
        burn_password_check(credenciais.password)
        raise invalido
    # A tentativa é contada antes de verificar a senha: tentativas simultâneas não furam o limite.
    if not claim_login_attempt(usuario["id"]):
        burn_password_check(credenciais.password)
        raise invalido
    if not verify_password(credenciais.password, usuario["password_hash"]) or not usuario["active"]:
        raise invalido
    reset_login_failures(usuario["id"])
    token, digest = new_session_token()
    create_session(digest, usuario["id"], _now())
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(MAX_AGE.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"email": usuario["email"], "role": usuario["role"]}


@app.post("/auth/logout", status_code=204, dependencies=[Depends(check_csrf)])
def logout(request: Request, user: dict = Depends(current_user)) -> Response:
    delete_session(hash_token(request.cookies[SESSION_COOKIE]))
    resposta = Response(status_code=204)
    resposta.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return resposta


@app.get("/auth/me")
def me(user: dict = Depends(current_user)) -> dict[str, str]:
    return {"email": user["email"], "role": user["role"]}


# ---- Camadas ------------------------------------------------------------


@app.get("/camadas")
def camadas(user: dict = Depends(current_user)) -> JSONResponse:
    # Igual para todo usuário (nada de tenant), mas só com sessão; cache só no navegador.
    return JSONResponse(catalogo_publico(), headers={"Cache-Control": "private, max-age=300"})


# ---- Jobs ---------------------------------------------------------------


def _erro_publico(erro: str | None) -> str | None:
    """Erro de validação conhecido sai como veio; o resto (exceção, caminho) vira texto genérico."""
    if erro is None:
        return None
    codigo, separador, _ = erro.partition(": ")
    return erro if separador and codigo in CODIGOS_DE_VALIDACAO else ERRO_DO_JOB


ARQUIVOS_DO_JOB = {
    "mapa": ("mapa_path", "mapa.pdf"),
    "memorial": ("memorial_path", "memorial.pdf"),
    "resultado": ("resultado_path", "resultado.json"),
}
MIME_DO_ARQUIVO = {"mapa.pdf": "application/pdf", "memorial.pdf": "application/pdf", "resultado.json": "application/json"}


def _prancha_publica(registro: dict, task_id: str | None) -> dict[str, object] | None:
    """Prancha do job revalidada; a logo vira URL da API (ou nulo), nunca caminho."""
    if registro.get("prancha") is None:
        return None
    try:
        prancha = validar_prancha(registro["prancha"]).como_dict()
    except Exception:  # noqa: BLE001 — registro inválido não derruba a listagem
        logger.warning("prancha inválida no job %s", registro.get("id"))
        return None
    prancha["logo"] = f"/jobs/{quote(task_id, safe='')}/logo" if prancha["logo"] and task_id else None
    return prancha


def _job_publico(registro: dict) -> dict[str, object]:
    """O que o cliente vê de um job: sem caminhos do servidor, dono nem tenant."""
    task_id = registro.get("task_id")
    concluido = registro.get("status") == "completed" and task_id
    return {
        "job_id": registro.get("id"),
        "task_id": task_id,
        "status": registro.get("status"),
        "input_filename": registro.get("input_filename"),
        "created_at": registro.get("created_at"),
        "completed_at": registro.get("completed_at"),
        "erro": _erro_publico(registro.get("erro")),
        "arquivos": {tipo: f"/jobs/{quote(task_id, safe='')}/files/{tipo}" for tipo in ARQUIVOS_DO_JOB}
        if concluido else {},
        "prancha": _prancha_publica(registro, task_id),
    }


def _salvar_upload(origem, destino: Path) -> None:
    """Copia em blocos e para no primeiro byte acima do limite; nunca deixa arquivo parcial."""
    total = 0
    try:
        with destino.open("wb") as saida:
            while bloco := origem.read(INPUT_CHUNK_BYTES):
                total += len(bloco)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=UPLOAD_GRANDE)
                saida.write(bloco)
    except BaseException:
        destino.unlink(missing_ok=True)
        raise


def _job_or_404(task_id: str, user: dict) -> dict[str, object]:
    """Inexistente e alheio respondem igual, para não revelar que o job existe."""
    registro = get_job_for(task_id, user)
    if not registro:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return registro


@app.post("/jobs", dependencies=[Depends(check_csrf)])
async def create_job(file: UploadFile = File(...), user: dict = Depends(require_admin)) -> dict[str, object]:
    nome = Path(file.filename or "entrada.geojson").name
    if not nome.lower().endswith(".geojson"):
        raise HTTPException(status_code=400, detail="A PoC aceita somente arquivos .geojson")

    job_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="geolume-api-") as temp_dir:
        entrada = Path(temp_dir) / nome
        _salvar_upload(file.file, entrada)
        try:
            resultado = run_job(entrada, OUTPUT_DIR, job_id=job_id)
        except InvalidInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("job síncrono %s falhou", job_id)
            raise HTTPException(status_code=422, detail="Não foi possível processar o arquivo.") from exc

    # Os arquivos do job síncrono não têm rota de download; os caminhos ficam só no servidor.
    return {
        "status": "ok",
        "job_id": resultado.job_id,
        "input_filename": nome,
        "fases_ms": resultado.phases_ms,
    }


def _ler_logo(logo: UploadFile | None) -> bytes | None:
    """Conteúdo da logo enviada; campo vazio (sem arquivo escolhido) conta como ausente."""
    if logo is None:
        return None
    conteudo = logo.file.read(LOGO_MAX_BYTES + 1)
    if len(conteudo) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail=LOGO_GRANDE)
    if not conteudo and not logo.filename:
        return None
    return conteudo


@app.post("/jobs/async", status_code=202, dependencies=[Depends(check_csrf)])
async def enqueue_job(
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
    logo: Annotated[UploadFile | None, File()] = None,
    projeto: Annotated[str | None, Form()] = None,
    responsavel: Annotated[str | None, Form()] = None,
    cor_contorno: Annotated[str | None, Form()] = None,
    cor_preenchimento: Annotated[str | None, Form()] = None,
    legenda: Annotated[str | None, Form()] = None,
    estilo: Annotated[str | None, Form()] = None,
    alfa_preenchimento: Annotated[str | None, Form()] = None,
) -> dict[str, str]:
    nome = Path(file.filename or "entrada.geojson").name
    if not nome.lower().endswith(".geojson"):
        raise HTTPException(status_code=400, detail="A PoC aceita somente arquivos .geojson")

    # Prancha validada aqui (e de novo no worker): opção inválida não enfileira nada.
    conteudo_logo = _ler_logo(logo)
    try:
        prancha = validar_prancha({
            "projeto": projeto, "responsavel": responsavel, "cor_contorno": cor_contorno,
            "cor_preenchimento": cor_preenchimento, "legenda": legenda, "logo": conteudo_logo is not None,
            "estilo": estilo, "alfa_preenchimento": alfa_preenchimento,
        })
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    destino_logo = caminho_logo(OUTPUT_DIR, job_id)  # nome vem só do job_id, nunca do enviado
    if conteudo_logo is not None:
        destino_logo.parent.mkdir(parents=True, exist_ok=True)
        try:
            await run_in_threadpool(normalizar_logo, conteudo_logo, destino_logo)
        except InvalidInputError as exc:
            status = 413 if exc.codigo == "logo_grande" else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    entrada = OUTPUT_DIR / "inputs" / f"{job_id}-{nome}"
    entrada.parent.mkdir(parents=True, exist_ok=True)
    try:
        _salvar_upload(file.file, entrada)
    except BaseException:
        destino_logo.unlink(missing_ok=True)
        raise
    opcoes = None if prancha.padrao else prancha.como_dict()
    registrado = False
    try:
        create_db_job(job_id, nome, input_path=str(entrada), owner_id=user["user_id"], tenant_id=user["tenant_id"],
                      prancha=opcoes)
        registrado = True
        # Sem prancha, a mensagem é a mesma de antes (input_path, job_id).
        task = process_job.delay(str(entrada), job_id, *([opcoes] if opcoes else []))
        set_task_id(job_id, task.id)
    except Exception as exc:
        # Sem banco ou sem fila: nada de arquivo órfão nem job "na fila" que nunca roda.
        logger.exception("job %s não foi enfileirado", job_id)
        entrada.unlink(missing_ok=True)
        destino_logo.unlink(missing_ok=True)
        if registrado:
            try:
                update_job(job_id, "failed", erro="Falha ao enfileirar o job.")
            except Exception:
                logger.exception("job %s ficou sem marcar a falha", job_id)
        raise HTTPException(status_code=503, detail="Não foi possível enfileirar o job. Tente novamente.") from exc
    return {"status": "queued", "job_id": job_id, "task_id": task.id}


@app.get("/jobs")
def jobs(limit: int = 20, user: dict = Depends(current_user)) -> dict[str, object]:
    return {"jobs": [_job_publico(registro) for registro in list_jobs_for(user, limit)]}


@app.get("/jobs/{task_id}")
def job_status(task_id: str, user: dict = Depends(current_user)) -> dict[str, object]:
    # Só o banco responde: sem fallback ao Celery, que não sabe quem é o dono do job.
    return _job_publico({"task_id": task_id, **_job_or_404(task_id, user)})


@app.get("/jobs/{task_id}/files/{kind}")
def download_job_file(task_id: str, kind: str, user: dict = Depends(current_user)) -> StreamingResponse:
    registro = _job_or_404(task_id, user)
    if registro["status"] != "completed":
        raise HTTPException(status_code=404, detail="Job ainda não concluído")
    if kind not in ARQUIVOS_DO_JOB:
        raise HTTPException(status_code=404, detail="Arquivo desconhecido")
    campo, nome = ARQUIVOS_DO_JOB[kind]
    caminho = _safe_job_file(registro, campo, nome)
    arquivo = None if caminho is None else _open_validated(caminho)
    if arquivo is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    def partes():
        with arquivo:
            while parte := arquivo.read(INPUT_CHUNK_BYTES):
                yield parte

    # Envia do descritor validado: o caminho não é reaberto (troca por link depois da validação).
    return StreamingResponse(
        partes(),
        media_type=MIME_DO_ARQUIVO[nome],
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        background=BackgroundTask(arquivo.close),
    )


def _safe_job_file(registro: dict, campo: str, nome: str) -> Path | None:
    """Caminho do banco só vale se for `nome` direto na pasta do próprio job: id sem separador
    nem `..`, pasta real (não link) e caminho resolvido igual ao esperado."""
    raw = registro.get(campo)
    job_id = registro.get("id")
    if not isinstance(raw, str) or not raw or not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        return None
    try:
        base = OUTPUT_DIR.resolve(strict=True)
        pasta = base / job_id
        caminho = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if pasta.is_symlink() or not pasta.is_dir() or caminho != pasta / nome:
        return None
    return caminho


@app.get("/jobs/{task_id}/logo")
def job_logo(task_id: str, user: dict = Depends(current_user)) -> StreamingResponse:
    registro = _job_or_404(task_id, user)
    indisponivel = HTTPException(status_code=404, detail="Logo não disponível")
    prancha = _prancha_publica(registro, task_id)
    if not prancha or not prancha["logo"]:
        raise indisponivel
    try:
        # Só o PNG direto em logos/, com o nome derivado do id do job (links recusados em _open_validated).
        caminho = caminho_logo(OUTPUT_DIR, str(registro["id"]))
        caminho = caminho.parent.resolve(strict=True) / caminho.name
    except (OSError, RuntimeError, ValueError):
        raise indisponivel from None
    arquivo = _open_validated(caminho)
    if arquivo is None:
        raise indisponivel

    def partes():
        with arquivo:
            while parte := arquivo.read(INPUT_CHUNK_BYTES):
                yield parte

    return StreamingResponse(
        partes(),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(arquivo.close),
    )


def _safe_input_path(raw: object) -> Path | None:
    """Caminho do banco só vale se, resolvido (links incluídos), for um .geojson direto em inputs/."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        base = INPUTS_DIR.resolve(strict=True)
        caminho = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if caminho.parent != base or caminho.suffix.lower() != ".geojson" or not caminho.is_file():
        return None
    return caminho


@app.get("/jobs/{task_id}/input")
def job_input(task_id: str, user: dict = Depends(current_user)) -> StreamingResponse:
    registro = _job_or_404(task_id, user)
    indisponivel = HTTPException(status_code=404, detail="Arquivo original não disponível")
    caminho = _safe_input_path(registro.get("input_path"))
    if caminho is None:
        raise indisponivel
    arquivo = _open_validated(caminho)
    if arquivo is None:
        raise indisponivel

    def partes():
        with arquivo:
            while parte := arquivo.read(INPUT_CHUNK_BYTES):
                yield parte

    # Sem Content-Length: o corpo é o que o descritor entregar (envio em chunks).
    return StreamingResponse(
        partes(),
        media_type="application/geo+json",
        background=BackgroundTask(arquivo.close),  # fecha mesmo se o cliente desistir antes
    )


def _open_validated(caminho: Path):
    """Abre uma única vez e valida o próprio descritor, não o nome: o que foi aberto precisa
    ser o arquivo validado (caminho real igual), regular e sem outros hard links. Assim a
    troca entre validar e abrir (link, hard link, pasta trocada) não expõe outro arquivo, e o
    envio lê desse descritor em vez de reabrir o caminho como o FileResponse faria."""
    try:
        fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    arquivo = os.fdopen(fd, "rb")
    try:
        info = os.fstat(fd)
        real = Path(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        real, info = None, None
    if info is None or real != caminho or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        arquivo.close()
        return None
    return arquivo


app.mount("/", StaticFiles(directory="/app/web", html=True), name="web")

