"""Senha (scrypt) e validade de sessão, sem banco."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import auth

AGORA = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def test_hash_verifica_a_senha_certa_e_recusa_a_errada():
    codificado = auth.hash_password("senha-muito-longa")
    assert auth.verify_password("senha-muito-longa", codificado)
    assert not auth.verify_password("senha-muito-longaX", codificado)


def test_hash_nao_contem_a_senha_e_usa_salt_diferente():
    a = auth.hash_password("senha-muito-longa")
    b = auth.hash_password("senha-muito-longa")
    assert "senha-muito-longa" not in a
    assert a != b
    assert a.startswith("scrypt$32768$8$3$")


def test_verifica_com_parametros_gravados_no_hash():
    salt = b"s" * 16
    derivado = hashlib.scrypt(b"outra-senha-longa", salt=salt, n=16384, r=8, p=1, dklen=32)
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    codificado = f"scrypt$16384$8$1${b64(salt)}${b64(derivado)}"
    assert auth.verify_password("outra-senha-longa", codificado)


def test_hash_malformado_retorna_false():
    for ruim in ("", "texto", "scrypt$x$8$3$a$b", "bcrypt$1$2$3$a$b", "scrypt$16384$8$1$!!$!!"):
        assert not auth.verify_password("qualquer", ruim)


def test_token_tem_hash_sha256_e_nao_repete():
    token, digest = auth.new_session_token()
    outro, _ = auth.new_session_token()
    assert token != outro
    assert len(token) >= 43
    assert digest == hashlib.sha256(token.encode()).hexdigest() == auth.hash_token(token)


def test_sessao_valida_ate_2h_sem_uso():
    expira = AGORA + timedelta(hours=10)
    assert auth.session_is_valid(last_seen_at=AGORA - timedelta(hours=1, minutes=59), expires_at=expira, now=AGORA)
    assert not auth.session_is_valid(last_seen_at=AGORA - timedelta(hours=2), expires_at=expira, now=AGORA)


def test_sessao_expira_em_12h_mesmo_em_uso():
    assert not auth.session_is_valid(last_seen_at=AGORA, expires_at=AGORA, now=AGORA)
    assert auth.MAX_AGE == timedelta(hours=12)
    assert auth.IDLE_TIMEOUT == timedelta(hours=2)


def test_normaliza_email():
    assert auth.normalize_email("  Ana@X.COM ") == "ana@x.com"


def test_burn_password_check_nao_levanta():
    assert auth.burn_password_check("qualquer") is None


def test_email_inexistente_custa_uma_unica_derivacao_ja_na_primeira_tentativa(monkeypatch):
    # Revisão Codex: o hash fictício era criado na primeira chamada (2 scrypt contra 1 de usuário real).
    import importlib

    modulo = importlib.reload(auth)  # processo recém-iniciado
    chamadas = []
    original = modulo._scrypt
    monkeypatch.setattr(modulo, "_scrypt", lambda *a: chamadas.append(1) or original(*a))
    modulo.burn_password_check("qualquer")
    assert len(chamadas) == 1
