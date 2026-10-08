"""Migração e gravação de input_path sem banco real (conexão falsa)."""

import pytest

psycopg2 = pytest.importorskip("psycopg2")
import db  # noqa: E402


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, **kwargs):
        return FakeCursor(self.log)


@pytest.fixture
def executed(monkeypatch):
    log = []
    monkeypatch.setattr(db, "connect", lambda: FakeConn(log))
    return log


def test_init_db_adiciona_e_preenche_input_path_dentro_do_lock(executed):
    db.init_db(retries=1)
    sqls = [sql for sql, _ in executed]

    assert sqls[0] == "SELECT pg_advisory_lock(731942)"
    assert sqls[-1] == "SELECT pg_advisory_unlock(731942)"
    corpo = " ".join(sqls[1:-1])
    assert "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS input_path TEXT" in corpo

    backfill = [(sql, params) for sql, params in executed if sql.startswith("UPDATE jobs SET input_path")]
    assert len(backfill) == 1
    sql, params = backfill[0]
    # Idempotente: só toca linhas ainda sem caminho, pela convenção do upload assíncrono.
    assert "WHERE input_path IS NULL" in sql
    assert "id || '-' || input_filename" in sql
    assert params == ("/saida/inputs/",)


DONO = {"owner_id": "u1", "tenant_id": "demo"}
MEMBRO = {"user_id": "u1", "tenant_id": "demo", "role": "member"}


def test_create_job_grava_input_path(executed):
    db.create_job("abc", "lote.geojson", input_path="/saida/inputs/abc-lote.geojson", **DONO)
    sql, params = executed[-1]
    assert sql.startswith("INSERT INTO jobs (id, task_id, status, input_filename, created_at, input_path, owner_id, tenant_id, prancha)")
    assert params[0] == "abc"
    assert params[3] == "lote.geojson"
    assert params[5] == "/saida/inputs/abc-lote.geojson"


def test_create_job_sem_input_path_continua_compativel(executed):
    db.create_job("abc", "lote.geojson", **DONO)
    assert executed[-1][1][5] is None


def test_create_job_grava_dono_e_tenant(executed):
    db.create_job("abc", "lote.geojson", **DONO)
    assert executed[-1][1][6:8] == ("u1", "demo")


def test_create_job_sem_prancha_grava_nulo(executed):
    db.create_job("abc", "lote.geojson", **DONO)
    assert executed[-1][1][8] is None


def test_create_job_grava_prancha_como_jsonb(executed):
    from psycopg2.extras import Json

    prancha = {"projeto": "Loteamento Sol", "logo": True}
    db.create_job("abc", "lote.geojson", prancha=prancha, **DONO)
    valor = executed[-1][1][8]
    assert isinstance(valor, Json) and valor.adapted == prancha


def test_create_job_exige_dono_e_tenant():
    with pytest.raises(TypeError):
        db.create_job("abc", "lote.geojson")


def corpo_da_migracao(executed):
    sqls = [sql for sql, _ in executed]
    assert sqls[0] == "SELECT pg_advisory_lock(731942)"
    assert sqls[-1] == "SELECT pg_advisory_unlock(731942)"
    return sqls[1:-1]


def test_schema_cria_tabelas_de_auth_idempotente(executed):
    db.init_db(retries=1)
    corpo = " ".join(corpo_da_migracao(executed))
    for trecho in (
        "CREATE TABLE IF NOT EXISTS tenants",
        "INSERT INTO tenants (id, nome) VALUES ('demo', 'GeoLume (demo)') ON CONFLICT DO NOTHING",
        "CREATE TABLE IF NOT EXISTS users",
        "email TEXT NOT NULL UNIQUE",
        "role TEXT NOT NULL CHECK (role IN ('admin', 'member'))",
        "CREATE TABLE IF NOT EXISTS sessions",
        "token_hash TEXT PRIMARY KEY",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT REFERENCES tenants(id)",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS owner_id TEXT REFERENCES users(id)",
        "CREATE INDEX IF NOT EXISTS jobs_tenant_owner_idx",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS prancha JSONB",
    ):
        assert trecho in corpo
    comandos = [c.strip().upper() for c in corpo.split(";") if c.strip()]
    assert not any(c.startswith(("DROP", "DELETE", "TRUNCATE")) for c in comandos)  # só aditiva


def test_backfill_de_tenant_so_onde_nulo_e_dentro_do_lock(executed):
    db.init_db(retries=1)
    corpo = corpo_da_migracao(executed)
    assert "UPDATE jobs SET tenant_id = 'demo' WHERE tenant_id IS NULL" in corpo
    assert not any("owner_id =" in sql for sql in corpo)  # legados ficam sem dono


def test_get_job_for_filtra_por_tenant_e_dono(executed):
    db.get_job_for("t1", MEMBRO)
    sql, params = executed[-1]
    assert "WHERE task_id = %s AND tenant_id = %s AND (%s = 'admin' OR owner_id = %s)" in sql
    assert params == ("t1", "demo", "member", "u1")


def test_list_jobs_for_usa_o_mesmo_filtro(executed):
    db.list_jobs_for(MEMBRO, limit=500)
    sql, params = executed[-1]
    assert "WHERE tenant_id = %s AND (%s = 'admin' OR owner_id = %s)" in sql
    assert "ORDER BY created_at DESC LIMIT %s" in sql
    assert params == ("demo", "member", "u1", 100)


def test_claim_login_attempt_e_um_unico_update_atomico(executed):
    db.claim_login_attempt("u1")
    (sql, params), = executed
    assert sql.startswith("UPDATE users SET failed_logins = CASE WHEN failed_logins + 1 >= %s THEN 0")
    assert "WHERE id = %s AND (locked_until IS NULL OR locked_until <= now()) RETURNING id" in sql
    assert params[0] == 5 and params[-1] == "u1"


def test_reset_login_failures_zera_contador_e_bloqueio(executed):
    db.reset_login_failures("u1")
    sql, params = executed[-1]
    assert sql == "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = %s"
    assert params == ("u1",)


def test_set_password_e_set_active_apagam_sessoes(executed):
    db.set_password("u1", "scrypt$...")
    db.set_active("u1", False)
    sqls = [sql for sql, _ in executed]
    assert sqls[0].startswith("UPDATE users SET password_hash = %s")
    assert sqls[1] == "DELETE FROM sessions WHERE user_id = %s"
    assert sqls[2].startswith("UPDATE users SET active = %s")
    assert sqls[3] == "DELETE FROM sessions WHERE user_id = %s"


def test_create_session_expira_em_12h_e_limpa_vencidas(executed):
    from datetime import datetime, timedelta, timezone

    agora = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    db.create_session("h1", "u1", agora)
    (limpeza, p1), (insert, p2) = executed
    assert limpeza == "DELETE FROM sessions WHERE expires_at <= %s OR last_seen_at <= %s"
    assert p1 == (agora, agora - timedelta(hours=2))
    assert insert.startswith("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)")
    assert p2 == ("h1", "u1", agora, agora, agora + timedelta(hours=12))


def test_get_session_user_junta_usuario(executed):
    db.get_session_user("h1")
    sql, params = executed[-1]
    assert "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = %s" in sql
    for coluna in ("s.user_id", "s.last_seen_at", "s.expires_at", "u.email", "u.tenant_id", "u.role", "u.active"):
        assert coluna in sql
    assert params == ("h1",)


# ---- started_at: início real da execução (regra de job preso) ----------------


def test_init_db_adiciona_started_at_sem_preencher_jobs_antigos(executed):
    db.init_db(retries=1)
    corpo = " ".join(sql for sql, _ in executed)
    assert "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ" in corpo
    assert not [sql for sql, _ in executed if "SET started_at" in sql]  # legado fica nulo, não inventa início


def test_update_job_started_grava_started_at(executed):
    db.update_job("j-1", "started")
    sql, params = executed[-1]
    assert sql.startswith("UPDATE jobs SET ") and sql.endswith(" WHERE id = %s")
    campos = dict(zip([c.split(" = ")[0] for c in sql[len("UPDATE jobs SET "):-len(" WHERE id = %s")].split(", ")],
                      params))
    assert campos["status"] == "started"
    assert campos["started_at"].tzinfo is not None
    assert "completed_at" not in campos
    assert params[-1] == "j-1"


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_update_job_terminal_nao_mexe_em_started_at(executed, status):
    db.update_job("j-1", status)
    sql, _ = executed[-1]
    assert "started_at" not in sql
    assert "completed_at = %s" in sql


# ---- Recuperação de job expirado: UPDATE condicional ---------------------------


class CursorComLinha(FakeCursor):
    def __init__(self, log, linha):
        super().__init__(log)
        self.linha = linha

    def fetchone(self):
        return self.linha


def _conexao_com_linha(monkeypatch, linha):
    log = []

    class Conn(FakeConn):
        def cursor(self, **kwargs):
            return CursorComLinha(self.log, linha)

    monkeypatch.setattr(db, "connect", lambda: Conn(log))
    return log


def test_marcar_started_expirado_so_se_status_e_started_at_forem_os_lidos(monkeypatch):
    from datetime import datetime, timezone

    log = _conexao_com_linha(monkeypatch, ("j-1",))
    inicio = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
    limite = datetime(2026, 10, 8, 11, 30, tzinfo=timezone.utc)
    assert db.marcar_job_expirado("j-1", "started", inicio, limite, "job_expirado: x") is True
    sql, params = log[-1]
    assert sql.startswith("UPDATE jobs SET status = 'failed', erro = %s, completed_at = %s WHERE id = %s")
    assert "AND status = 'started' AND started_at = %s AND started_at < %s" in sql
    assert sql.endswith("RETURNING id")
    assert params[0] == "job_expirado: x" and params[1].tzinfo is not None
    assert params[2:] == ("j-1", inicio, limite)


def test_marcar_queued_expirado_exige_created_at_lido_e_ainda_sem_tarefa(monkeypatch):
    from datetime import datetime, timezone

    log = _conexao_com_linha(monkeypatch, ("j-1",))
    criado = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    limite = datetime(2026, 10, 8, 11, 50, tzinfo=timezone.utc)
    assert db.marcar_job_expirado("j-1", "queued", criado, limite, "job_expirado: y") is True
    sql, params = log[-1]
    assert "AND status = 'queued' AND created_at = %s AND created_at < %s" in sql
    assert "AND (task_id IS NULL OR task_id = '')" in sql
    assert params[2:] == ("j-1", criado, limite)


def test_marcar_job_expirado_sem_linha_atualizada_e_false(executed):
    from datetime import datetime, timezone

    agora = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    assert db.marcar_job_expirado("j-1", "started", agora, agora, "job_expirado: x") is False


@pytest.mark.parametrize("status", ["completed", "failed", "desconhecido"])
def test_marcar_job_expirado_recusa_status_que_nao_expira(executed, status):
    from datetime import datetime, timezone

    agora = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        db.marcar_job_expirado("j-1", status, agora, agora, "job_expirado: x")
    assert executed == []


# ---- Conclusão condicional: só started vira completed --------------------------


def test_concluir_job_so_promove_quem_ainda_esta_started(monkeypatch):
    log = _conexao_com_linha(monkeypatch, ("j-1",))
    assert db.concluir_job("j-1", "/saida/j-1/mapa.pdf", "/saida/j-1/memorial.pdf", "/saida/j-1/resultado.json")
    sql, params = log[-1]
    assert sql == ("UPDATE jobs SET status = 'completed', mapa_path = %s, memorial_path = %s, resultado_path = %s, "
                   "completed_at = %s WHERE id = %s AND status = 'started' RETURNING id")
    assert params[:3] == ("/saida/j-1/mapa.pdf", "/saida/j-1/memorial.pdf", "/saida/j-1/resultado.json")
    assert params[3].tzinfo is not None and params[4] == "j-1"


def test_concluir_job_recusado_pelo_banco_e_false(executed):
    assert db.concluir_job("j-1", "m", "me", "r") is False
    (sql, _), = executed
    assert "WHERE id = %s AND status = 'started'" in sql  # nunca um UPDATE sem filtro de job e status
