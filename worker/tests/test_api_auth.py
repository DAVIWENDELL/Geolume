"""Login, logout, sessão, CSRF e /health, chamando as funções da API diretamente."""

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

AGORA = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SENHA = "senha-de-teste-longa"


@pytest.fixture
def api(monkeypatch):
    pytest.importorskip("qgis.core")
    pytest.importorskip("fastapi")
    import db

    monkeypatch.setattr(db, "init_db", lambda *args, **kwargs: None)
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)
    module = importlib.import_module("api")
    monkeypatch.setattr(module, "_now", lambda: AGORA)
    yield module
    for name in ("api", "celery_app"):
        sys.modules.pop(name, None)


@pytest.fixture
def banco(api, monkeypatch):
    """Usuários e sessões em memória no lugar das funções do db usadas pela API."""
    import auth

    estado = {
        "users": {
            "ana@geolume.test": {"id": "u1", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member",
                                 "active": True, "password_hash": auth.hash_password(SENHA)},
        },
        "sessions": {},
        "claims": [],
        "resets": [],
        "touches": [],
        "bloqueado": False,
    }
    por_id = lambda uid: next(u for u in estado["users"].values() if u["id"] == uid)  # noqa: E731

    def claim(uid):
        estado["claims"].append(uid)
        return not estado["bloqueado"]

    def create_session(digest, uid, now):
        estado["sessions"][digest] = {"user_id": uid, "last_seen_at": now, "expires_at": now + auth.MAX_AGE}

    def get_session_user(digest):
        s = estado["sessions"].get(digest)
        if not s:
            return None
        u = por_id(s["user_id"])
        return {**s, "email": u["email"], "tenant_id": u["tenant_id"], "role": u["role"], "active": u["active"]}

    monkeypatch.setattr(api, "get_user_by_email", lambda email: estado["users"].get(email))
    monkeypatch.setattr(api, "claim_login_attempt", claim)
    monkeypatch.setattr(api, "reset_login_failures", lambda uid: estado["resets"].append(uid))
    monkeypatch.setattr(api, "create_session", create_session)
    monkeypatch.setattr(api, "get_session_user", get_session_user)
    monkeypatch.setattr(api, "touch_session", lambda digest, now: estado["touches"].append((digest, now)))
    monkeypatch.setattr(api, "delete_session", lambda digest: estado["sessions"].pop(digest, None))
    return estado


def req(cookie=None, headers=None, method="GET"):
    from starlette.requests import Request

    h = [(b"host", b"localhost:8000")]
    if cookie:
        h.append((b"cookie", f"geolume_session={cookie}".encode()))
    for chave, valor in (headers or {}).items():
        h.append((chave.lower().encode(), valor.encode()))
    return Request({"type": "http", "method": method, "scheme": "http", "server": ("localhost", 8000),
                    "path": "/", "root_path": "", "query_string": b"", "headers": h})


def status_de(chamada):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        chamada()
    return exc.value.status_code, exc.value.detail


def entrar(api, email="ana@geolume.test", senha=SENHA):
    from fastapi import Response

    resposta = Response()
    corpo = api.login(api.Credenciais(email=email, password=senha), resposta)
    cookie = resposta.headers["set-cookie"]
    token = cookie.split(";")[0].split("=", 1)[1]
    return corpo, cookie, token


# ---- /health ------------------------------------------------------------


def test_health_publico_sem_detalhes_internos(api, monkeypatch):
    def proibido(*a, **kw):
        raise AssertionError("health não pode consultar banco nem sessão")

    monkeypatch.setattr(api, "get_session_user", proibido)
    assert api.health() == {"status": "ok", "service": "geolume-worker"}
    rota = next(r for r in api.app.routes if getattr(r, "path", None) == "/health")
    assert rota.dependant.dependencies == []


# ---- login --------------------------------------------------------------


def test_login_define_cookie_httponly_secure_lax_12h(api, banco):
    corpo, cookie, token = entrar(api)
    assert corpo == {"email": "ana@geolume.test", "role": "member"}
    atributos = cookie.lower()
    assert cookie.startswith("geolume_session=")
    for parte in ("httponly", "secure", "samesite=lax", "path=/", f"max-age={12 * 3600}"):
        assert parte in atributos
    assert "domain" not in atributos
    import auth

    assert auth.hash_token(token) in banco["sessions"]
    assert token not in banco["sessions"]  # o banco guarda só o hash


def test_login_gera_token_novo_a_cada_vez(api, banco):
    assert entrar(api)[2] != entrar(api)[2]


def test_login_normaliza_email(api, banco):
    assert entrar(api, email="  ANA@Geolume.TEST ")[0]["email"] == "ana@geolume.test"


def test_login_erro_generico_igual(api, banco, monkeypatch):
    queimadas = []
    import auth

    real = auth.burn_password_check
    monkeypatch.setattr(api, "burn_password_check", lambda s: (queimadas.append(s), real(s)))
    esperado = (401, "E-mail ou senha inválidos")

    assert status_de(lambda: entrar(api, email="ninguem@geolume.test")) == esperado
    assert queimadas == [SENHA]  # e-mail inexistente paga o mesmo custo
    assert status_de(lambda: entrar(api, senha="senha-errada-longa")) == esperado

    banco["bloqueado"] = True
    assert status_de(lambda: entrar(api)) == esperado  # senha certa, mas bloqueado
    banco["bloqueado"] = False

    banco["users"]["ana@geolume.test"]["active"] = False
    assert status_de(lambda: entrar(api)) == esperado
    assert banco["sessions"] == {}


def test_tentativa_e_contada_antes_da_verificacao(api, banco):
    status_de(lambda: entrar(api, senha="senha-errada-longa"))
    assert banco["claims"] == ["u1"]
    assert banco["resets"] == []


def test_sucesso_zera_falhas(api, banco):
    entrar(api)
    assert banco["resets"] == ["u1"]


# ---- sessão -------------------------------------------------------------


def test_current_user_401_sem_cookie(api, banco):
    assert status_de(lambda: api.current_user(req())) == (401, "Autenticação necessária")
    assert status_de(lambda: api.current_user(req(cookie="inventado"))) == (401, "Autenticação necessária")


def test_current_user_devolve_usuario(api, banco):
    _, _, token = entrar(api)
    assert api.current_user(req(cookie=token)) == {
        "user_id": "u1", "email": "ana@geolume.test", "tenant_id": "demo", "role": "member"}


def test_current_user_sessao_expirada_apaga_linha(api, banco, monkeypatch):
    _, _, token = entrar(api)
    monkeypatch.setattr(api, "_now", lambda: AGORA + timedelta(hours=2))
    assert status_de(lambda: api.current_user(req(cookie=token)))[0] == 401
    assert banco["sessions"] == {}


def test_current_user_expira_em_12h_mesmo_em_uso(api, banco, monkeypatch):
    _, _, token = entrar(api)
    for horas in range(1, 12):
        monkeypatch.setattr(api, "_now", lambda h=horas: AGORA + timedelta(hours=h))
        api.current_user(req(cookie=token))
        banco["sessions"][next(iter(banco["sessions"]))]["last_seen_at"] = AGORA + timedelta(hours=horas)
    monkeypatch.setattr(api, "_now", lambda: AGORA + timedelta(hours=12))
    assert status_de(lambda: api.current_user(req(cookie=token)))[0] == 401


def test_current_user_usuario_inativo(api, banco):
    _, _, token = entrar(api)
    banco["users"]["ana@geolume.test"]["active"] = False
    assert status_de(lambda: api.current_user(req(cookie=token)))[0] == 401


def test_current_user_toca_last_seen_so_apos_1min(api, banco, monkeypatch):
    _, _, token = entrar(api)
    monkeypatch.setattr(api, "_now", lambda: AGORA + timedelta(seconds=59))
    api.current_user(req(cookie=token))
    assert banco["touches"] == []
    monkeypatch.setattr(api, "_now", lambda: AGORA + timedelta(minutes=1))
    api.current_user(req(cookie=token))
    assert len(banco["touches"]) == 1


def test_logout_apaga_sessao_e_cookie(api, banco):
    _, _, token = entrar(api)
    request = req(cookie=token, method="POST")
    resposta = api.logout(request, api.current_user(request))
    assert resposta.status_code == 204
    assert banco["sessions"] == {}
    cookie = resposta.headers["set-cookie"].lower()
    assert cookie.startswith("geolume_session=") and "max-age=0" in cookie


def test_me(api, banco):
    _, _, token = entrar(api)
    assert api.me(api.current_user(req(cookie=token))) == {"email": "ana@geolume.test", "role": "member"}


def test_require_admin_403_para_membro(api):
    membro = {"user_id": "u1", "email": "a", "tenant_id": "demo", "role": "member"}
    admin = {**membro, "role": "admin"}
    assert status_de(lambda: api.require_admin(membro)) == (403, "Acesso restrito ao administrador")
    assert api.require_admin(admin) == admin


# ---- CSRF ---------------------------------------------------------------


def test_post_sem_header_csrf_403(api):
    assert status_de(lambda: api.check_csrf(req(method="POST"))) == (403, "Requisição recusada")
    assert status_de(lambda: api.check_csrf(req(method="POST", headers={"X-GeoLume-CSRF": "0"})))[0] == 403


def test_post_origin_diferente_403(api):
    ruim = {"X-GeoLume-CSRF": "1", "Origin": "http://evil.test"}
    assert status_de(lambda: api.check_csrf(req(method="POST", headers=ruim)))[0] == 403


def test_post_com_header_e_mesma_origem_passa(api):
    assert api.check_csrf(req(method="POST", headers={"X-GeoLume-CSRF": "1", "Origin": "http://localhost:8000"})) is None
    assert api.check_csrf(req(method="POST", headers={"X-GeoLume-CSRF": "1"})) is None
