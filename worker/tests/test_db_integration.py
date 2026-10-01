"""Contra o PostgreSQL real (contêiner api): GEOLUME_DB_INTEGRATION=1.

Usa um usuário temporário, apagado no fim; não toca jobs nem outros usuários.
"""

import os
import threading
import uuid

import pytest

if os.environ.get("GEOLUME_DB_INTEGRATION") != "1":
    pytest.skip("só com GEOLUME_DB_INTEGRATION=1 e banco real", allow_module_level=True)

import db  # noqa: E402


@pytest.fixture
def usuario_temporario():
    db.init_db(retries=5)
    email = f"itest-{uuid.uuid4().hex}@geolume.test"
    user_id = db.create_user(email, "scrypt$sem-login", "member")
    yield user_id
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def test_tentativas_simultaneas_nao_passam_do_limite(usuario_temporario):
    barreira = threading.Barrier(12)
    aceitas = []

    def tentar():
        barreira.wait()
        aceitas.append(db.claim_login_attempt(usuario_temporario))

    threads = [threading.Thread(target=tentar) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert aceitas.count(True) == 5
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT failed_logins, locked_until > now() FROM users WHERE id = %s", (usuario_temporario,))
        assert cur.fetchone() == (0, True)


def test_sucesso_libera_o_bloqueio(usuario_temporario):
    for _ in range(5):
        db.claim_login_attempt(usuario_temporario)
    assert not db.claim_login_attempt(usuario_temporario)
    db.reset_login_failures(usuario_temporario)
    assert db.claim_login_attempt(usuario_temporario)


def test_prancha_ida_e_volta_no_jsonb(usuario_temporario):
    user = {"user_id": usuario_temporario, "tenant_id": "demo", "role": "member"}
    prancha = {"projeto": "Loteamento Sol", "responsavel": None, "cor_contorno": "#123456",
               "cor_preenchimento": "#FFC800", "legenda": "lateral", "logo": True}
    com, sem = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        db.create_job(com, "a.geojson", owner_id=usuario_temporario, tenant_id="demo", task_id=com, prancha=prancha)
        db.create_job(sem, "b.geojson", owner_id=usuario_temporario, tenant_id="demo", task_id=sem)
        assert db.get_job_for(com, user)["prancha"] == prancha
        assert db.get_job_for(sem, user)["prancha"] is None
    finally:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE id IN (%s, %s)", (com, sem))
