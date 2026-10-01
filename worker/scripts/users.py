"""Administração de usuários do GeoLume.

Uso (dentro do contêiner api):
  python3 scripts/users.py create --email <email> --role admin|member [--password-stdin]
  python3 scripts/users.py reset-password --email <email> [--password-stdin]
  python3 scripts/users.py disable --email <email>

A senha nunca é argumento: vem do getpass (digitada duas vezes) ou da primeira
linha do stdin com --password-stdin. Só o hash scrypt é gravado.
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

import db  # noqa: E402
from auth import MIN_PASSWORD, hash_password, normalize_email  # noqa: E402

EXIT_SENHA = 2
EXIT_EXISTE = 3
EXIT_NAO_EXISTE = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="users.py", description="Usuários do GeoLume")
    comandos = parser.add_subparsers(dest="comando", required=True)
    criar = comandos.add_parser("create", help="cria um usuário")
    criar.add_argument("--email", required=True)
    criar.add_argument("--role", required=True, choices=("admin", "member"))
    criar.add_argument("--password-stdin", action="store_true")
    trocar = comandos.add_parser("reset-password", help="troca a senha e encerra as sessões")
    trocar.add_argument("--email", required=True)
    trocar.add_argument("--password-stdin", action="store_true")
    desativar = comandos.add_parser("disable", help="desativa o usuário e encerra as sessões")
    desativar.add_argument("--email", required=True)
    return parser


def _ler_senha(usar_stdin: bool, stdin) -> str | None:
    if usar_stdin:
        return stdin.readline().rstrip("\r\n")
    senha = getpass.getpass("Senha: ")
    if getpass.getpass("Repita a senha: ") != senha:
        print("As senhas não coincidem.", file=sys.stderr)
        return None
    return senha


def _senha_valida(args, stdin) -> str | None:
    senha = _ler_senha(args.password_stdin, stdin)
    if senha is None:
        return None
    if len(senha) < MIN_PASSWORD:
        print(f"A senha precisa ter pelo menos {MIN_PASSWORD} caracteres.", file=sys.stderr)
        return None
    return senha


def main(argv: list[str], stdin=sys.stdin) -> int:
    args = _parser().parse_args(argv)
    email = normalize_email(args.email)
    db.init_db(retries=5)
    usuario = db.get_user_by_email(email)

    if args.comando == "create":
        if usuario:
            print(f"Já existe usuário com o e-mail {email}.", file=sys.stderr)
            return EXIT_EXISTE
        senha = _senha_valida(args, stdin)
        if senha is None:
            return EXIT_SENHA
        try:
            db.create_user(email, hash_password(senha), args.role)
        except psycopg2.IntegrityError:  # criado por outro processo entre a consulta e o insert
            print(f"Já existe usuário com o e-mail {email}.", file=sys.stderr)
            return EXIT_EXISTE
        print(f"Usuário criado: {email} ({args.role})")
        return 0

    if not usuario:
        print(f"Usuário não encontrado: {email}", file=sys.stderr)
        return EXIT_NAO_EXISTE

    if args.comando == "reset-password":
        senha = _senha_valida(args, stdin)
        if senha is None:
            return EXIT_SENHA
        db.set_password(usuario["id"], hash_password(senha))
        print(f"Senha trocada e sessões encerradas: {email}")
        return 0

    db.set_active(usuario["id"], False)
    print(f"Usuário desativado e sessões encerradas: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
