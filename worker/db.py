"""Persistência mínima dos jobs em PostgreSQL/PostGIS."""

import os
import time
import uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from auth import IDLE_TIMEOUT, LOCK_FOR, MAX_AGE, MAX_FAILED_LOGINS

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://geolume:geolume@postgis:5432/geolume",
)

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    status TEXT NOT NULL,
    input_filename TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    mapa_path TEXT,
    memorial_path TEXT,
    resultado_path TEXT,
    erro TEXT
);
CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs (task_id);
CREATE INDEX IF NOT EXISTS jobs_created_at_idx ON jobs (created_at DESC);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS input_path TEXT;

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO tenants (id, nome) VALUES ('demo', 'GeoLume (demo)') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    active BOOLEAN NOT NULL DEFAULT true,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT REFERENCES tenants(id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS owner_id TEXT REFERENCES users(id);
CREATE INDEX IF NOT EXISTS jobs_tenant_owner_idx ON jobs (tenant_id, owner_id, created_at DESC);
-- Opções da prancha (mapa.pdf); nulo = prancha padrão, inclusive nos jobs antigos.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS prancha JSONB;
-- Início real da execução (regra de job preso); nulo nos jobs antigos, sem backfill.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
"""

# Jobs anteriores à autenticação entram no tenant demo e ficam sem dono:
# só o administrador os vê.
BACKFILL_TENANT = "UPDATE jobs SET tenant_id = 'demo' WHERE tenant_id IS NULL"

# Filtro único de acesso a jobs: mesmo tenant e (administrador ou dono).
JOB_ACCESS = "tenant_id = %s AND (%s = 'admin' OR owner_id = %s)"

# Jobs anteriores à coluna: o upload assíncrono sempre salvou em
# /saida/inputs/{job_id}-{input_filename}. Só preenche o que ainda está vazio.
LEGACY_INPUTS_PREFIX = "/saida/inputs/"
BACKFILL_INPUT_PATH = """
UPDATE jobs SET input_path = %s || id || '-' || input_filename
WHERE input_path IS NULL
"""


def connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


def init_db(retries: int = 30) -> None:
    for tentativa in range(retries):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(731942)")
                try:
                    cur.execute(SCHEMA)
                    cur.execute(BACKFILL_INPUT_PATH, (LEGACY_INPUTS_PREFIX,))
                    cur.execute(BACKFILL_TENANT)
                finally:
                    cur.execute("SELECT pg_advisory_unlock(731942)")
            return
        except psycopg2.OperationalError:
            if tentativa == retries - 1:
                raise
            time.sleep(1)


def create_job(
    job_id: str,
    filename: str,
    *,
    owner_id: str,
    tenant_id: str,
    task_id: str | None = None,
    input_path: str | None = None,
    prancha: dict | None = None,
) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO jobs (id, task_id, status, input_filename, created_at, input_path, owner_id, tenant_id, prancha)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (job_id, task_id, "queued", filename, datetime.now(timezone.utc), input_path, owner_id, tenant_id,
             Json(prancha) if prancha is not None else None),
        )


def set_task_id(job_id: str, task_id: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET task_id = %s WHERE id = %s", (task_id, job_id))


def update_job(job_id: str, status: str, **fields: str | None) -> None:
    allowed = {"mapa_path", "memorial_path", "resultado_path", "erro", "completed_at"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates["status"] = status
    if status == "started":
        updates["started_at"] = datetime.now(timezone.utc)
    if status in {"completed", "failed"}:
        updates["completed_at"] = datetime.now(timezone.utc)
    assignments = ", ".join(f"{key} = %s" for key in updates)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE jobs SET {assignments} WHERE id = %s",
            (*updates.values(), job_id),
        )


def iniciar_job(job_id: str) -> bool:
    """started só a partir de queued: job já recuperado (failed), completed ou já started não é retomado."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = 'started', started_at = %s WHERE id = %s AND status = 'queued' RETURNING id",
            (datetime.now(timezone.utc), job_id),
        )
        return cur.fetchone() is not None


def falhar_job(job_id: str, erro: str) -> bool:
    """failed só a partir de started: a primeira causa gravada (ex.: job_expirado) não é sobrescrita."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs SET status = 'failed', erro = %s, completed_at = %s
               WHERE id = %s AND status = 'started' RETURNING id""",
            (erro, datetime.now(timezone.utc), job_id),
        )
        return cur.fetchone() is not None


def falhar_enfileiramento(job_id: str, erro: str) -> bool:
    """failed da API só a partir de queued: o Celery pode já ter iniciado ou concluído o job."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs SET status = 'failed', erro = %s, completed_at = %s
               WHERE id = %s AND status = 'queued' RETURNING id""",
            (erro, datetime.now(timezone.utc), job_id),
        )
        return cur.fetchone() is not None


def concluir_job(job_id: str, mapa_path: str, memorial_path: str, resultado_path: str) -> bool:
    """completed só a partir de started: um job já failed (recuperação) ou completed não é sobrescrito."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs SET status = 'completed', mapa_path = %s, memorial_path = %s, resultado_path = %s,
               completed_at = %s WHERE id = %s AND status = 'started' RETURNING id""",
            (mapa_path, memorial_path, resultado_path, datetime.now(timezone.utc), job_id),
        )
        return cur.fetchone() is not None


# Marco que a regra de job preso usou para cada status; queued só expira enquanto não tem tarefa.
_MARCO_EXPIRACAO = {
    "queued": "created_at = %s AND created_at < %s AND (task_id IS NULL OR task_id = '')",
    "started": "started_at = %s AND started_at < %s",
}


def marcar_job_expirado(job_id: str, status: str, marco: datetime, expirado_antes: datetime, erro: str) -> bool:
    """Marca failed só se o job ainda estiver como foi lido (status e marco) e ainda expirado.

    Um único UPDATE: o lock de linha serializa recuperadores simultâneos e só um recebe True.
    """
    if status not in _MARCO_EXPIRACAO:
        raise ValueError(f"status que não expira: {status}")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""UPDATE jobs SET status = 'failed', erro = %s, completed_at = %s
               WHERE id = %s AND status = '{status}' AND {_MARCO_EXPIRACAO[status]} RETURNING id""",
            (erro, datetime.now(timezone.utc), job_id, marco, expirado_antes),
        )
        return cur.fetchone() is not None


def _access(user: dict) -> tuple[str, str, str]:
    return (user["tenant_id"], user["role"], user["user_id"])


def get_job_for(task_id: str, user: dict) -> dict[str, object] | None:
    """Job só existe para quem pode vê-lo: inexistente e alheio dão o mesmo None."""
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM jobs WHERE task_id = %s AND {JOB_ACCESS}", (task_id, *_access(user)))
        row = cur.fetchone()
    return dict(row) if row else None


def list_jobs_for(user: dict, limit: int = 20) -> list[dict[str, object]]:
    limit = max(1, min(limit, 100))
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"SELECT * FROM jobs WHERE {JOB_ACCESS} ORDER BY created_at DESC LIMIT %s",
            (*_access(user), limit),
        )
        return [dict(row) for row in cur.fetchall()]


# ---- Usuários e sessões -------------------------------------------------


def create_user(email: str, password_hash: str, role: str, tenant_id: str = "demo") -> str:
    user_id = uuid.uuid4().hex
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) VALUES (%s, %s, %s, %s, %s)",
            (user_id, tenant_id, email, password_hash, role),
        )
    return user_id


def get_user_by_email(email: str) -> dict[str, object] | None:
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    return dict(row) if row else None


def set_password(user_id: str, password_hash: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE users SET password_hash = %s, password_changed_at = now(),
               failed_logins = 0, locked_until = NULL WHERE id = %s""",
            (password_hash, user_id),
        )
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def set_active(user_id: str, active: bool) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET active = %s WHERE id = %s", (active, user_id))
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def claim_login_attempt(user_id: str) -> bool:
    """Conta a tentativa antes de verificar a senha, num único UPDATE: o lock de linha
    serializa tentativas simultâneas, então nenhuma passa do limite. False = bloqueado."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE users SET
                 failed_logins = CASE WHEN failed_logins + 1 >= %s THEN 0 ELSE failed_logins + 1 END,
                 locked_until = CASE WHEN failed_logins + 1 >= %s THEN now() + %s ELSE NULL END
               WHERE id = %s AND (locked_until IS NULL OR locked_until <= now()) RETURNING id""",
            (MAX_FAILED_LOGINS, MAX_FAILED_LOGINS, LOCK_FOR, user_id),
        )
        return cur.fetchone() is not None


def reset_login_failures(user_id: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = %s", (user_id,))


def create_session(token_hash: str, user_id: str, now: datetime) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sessions WHERE expires_at <= %s OR last_seen_at <= %s",
            (now, now - IDLE_TIMEOUT),
        )
        cur.execute(
            """INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (token_hash, user_id, now, now, now + MAX_AGE),
        )


def get_session_user(token_hash: str) -> dict[str, object] | None:
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT s.user_id, s.last_seen_at, s.expires_at, u.email, u.tenant_id, u.role, u.active
               FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = %s""",
            (token_hash,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def touch_session(token_hash: str, now: datetime) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s", (now, token_hash))


def delete_session(token_hash: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
