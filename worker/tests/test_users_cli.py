"""CLI de usuários: senha só por getpass ou stdin, nunca em argumento nem na saída."""

import importlib.util
import io
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "users.py"
SENHA = "uma-senha-bem-longa"


@pytest.fixture
def cli(monkeypatch):
    pytest.importorskip("psycopg2")
    spec = importlib.util.spec_from_file_location("users_cli", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    banco = {"users": {}, "senhas": {}, "ativos": {}, "sessoes_apagadas": []}

    def create_user(email, password_hash, role, tenant_id="demo"):
        banco["users"][email] = {"id": f"id-{email}", "email": email, "role": role, "password_hash": password_hash}
        return f"id-{email}"

    def set_password(user_id, password_hash):
        banco["senhas"][user_id] = password_hash
        banco["sessoes_apagadas"].append(user_id)

    def set_active(user_id, active):
        banco["ativos"][user_id] = active
        banco["sessoes_apagadas"].append(user_id)

    monkeypatch.setattr(modulo.db, "init_db", lambda *a, **kw: None)
    monkeypatch.setattr(modulo.db, "create_user", create_user)
    monkeypatch.setattr(modulo.db, "get_user_by_email", lambda email: banco["users"].get(email))
    monkeypatch.setattr(modulo.db, "set_password", set_password)
    monkeypatch.setattr(modulo.db, "set_active", set_active)
    modulo.banco = banco
    return modulo


def rodar(cli, *args, stdin=f"{SENHA}\n"):
    return cli.main(list(args), stdin=io.StringIO(stdin))


def test_create_grava_hash_e_nunca_a_senha(cli, capsys):
    import auth

    assert rodar(cli, "create", "--email", " Ana@Geolume.TEST ", "--role", "admin", "--password-stdin") == 0
    usuario = cli.banco["users"]["ana@geolume.test"]
    assert usuario["role"] == "admin"
    assert SENHA not in usuario["password_hash"]
    assert auth.verify_password(SENHA, usuario["password_hash"])
    saida = capsys.readouterr()
    assert SENHA not in saida.out + saida.err
    assert "ana@geolume.test" in saida.out


def test_create_por_getpass_pede_duas_vezes(cli, monkeypatch):
    respostas = iter([SENHA, SENHA])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(respostas))
    assert rodar(cli, "create", "--email", "bia@geolume.test", "--role", "member") == 0
    assert "bia@geolume.test" in cli.banco["users"]


def test_getpass_divergente_exit_2(cli, monkeypatch):
    respostas = iter([SENHA, SENHA + "x"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(respostas))
    assert rodar(cli, "create", "--email", "bia@geolume.test", "--role", "member") == 2
    assert cli.banco["users"] == {}


def test_create_email_existente_exit_3(cli):
    assert rodar(cli, "create", "--email", "ana@geolume.test", "--role", "member", "--password-stdin") == 0
    assert rodar(cli, "create", "--email", "ANA@geolume.test", "--role", "member", "--password-stdin") == 3


def test_senha_curta_exit_2(cli):
    assert rodar(cli, "create", "--email", "ana@geolume.test", "--role", "member", "--password-stdin",
                 stdin="curta-11ch\n") == 2
    assert cli.banco["users"] == {}


def test_reset_troca_hash_e_apaga_sessoes(cli, capsys):
    import auth

    rodar(cli, "create", "--email", "ana@geolume.test", "--role", "member", "--password-stdin")
    nova = "outra-senha-bem-longa"
    assert rodar(cli, "reset-password", "--email", "ana@geolume.test", "--password-stdin", stdin=f"{nova}\r\n") == 0
    assert auth.verify_password(nova, cli.banco["senhas"]["id-ana@geolume.test"])
    assert cli.banco["sessoes_apagadas"] == ["id-ana@geolume.test"]
    assert nova not in "".join(capsys.readouterr())
    assert rodar(cli, "reset-password", "--email", "ninguem@geolume.test", "--password-stdin") == 4


def test_disable(cli):
    rodar(cli, "create", "--email", "ana@geolume.test", "--role", "member", "--password-stdin")
    assert rodar(cli, "disable", "--email", "ana@geolume.test") == 0
    assert cli.banco["ativos"] == {"id-ana@geolume.test": False}
    assert cli.banco["sessoes_apagadas"] == ["id-ana@geolume.test"]
    assert rodar(cli, "disable", "--email", "ninguem@geolume.test") == 4


def test_nao_aceita_senha_como_argumento(cli):
    with pytest.raises(SystemExit) as exc:
        rodar(cli, "create", "--email", "ana@geolume.test", "--role", "member", "--password", SENHA)
    assert exc.value.code == 2
    assert cli.banco["users"] == {}


def test_papel_invalido_recusado(cli):
    with pytest.raises(SystemExit):
        rodar(cli, "create", "--email", "ana@geolume.test", "--role", "root", "--password-stdin")
