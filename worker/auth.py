"""Senhas (scrypt da stdlib) e regras de sessão. Sem banco: só funções puras."""

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

IDLE_TIMEOUT = timedelta(hours=2)
MAX_AGE = timedelta(hours=12)
TOUCH_EVERY = timedelta(minutes=1)
MAX_FAILED_LOGINS = 5
LOCK_FOR = timedelta(minutes=15)
MIN_PASSWORD = 12

# Parâmetros OWASP para scrypt (n=2^15, r=8, p=3). Ficam gravados em cada hash,
# então endurecer depois não invalida as senhas existentes.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 3
SCRYPT_MAXMEM = 64 * 1024 * 1024
DKLEN = 32


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=SCRYPT_MAXMEM)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derivado = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(derivado)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        esquema, n, r, p, salt, esperado = encoded.split("$")
        if esquema != "scrypt":
            return False
        salt_raw = base64.b64decode(salt, validate=True)
        esperado_raw = base64.b64decode(esperado, validate=True)
        derivado = _scrypt(password, salt_raw, int(n), int(r), int(p))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(derivado, esperado_raw)


# Calculado na carga do módulo: se fosse na primeira chamada, o primeiro e-mail
# inexistente custaria dois scrypt e denunciaria a diferença.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    """Mesmo custo de uma verificação real, para e-mail inexistente não responder mais rápido."""
    verify_password(password, _DUMMY_HASH)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def session_is_valid(*, last_seen_at: datetime, expires_at: datetime, now: datetime) -> bool:
    return now < expires_at and now - last_seen_at < IDLE_TIMEOUT
