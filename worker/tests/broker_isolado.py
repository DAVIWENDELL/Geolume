"""Redis isolado para testes que publicam: só a DB 15, conferida antes de qualquer publicação.

O Celery prefere CELERY_BROKER_URL, CELERY_BROKER_READ_URL, CELERY_BROKER_WRITE_URL e
CELERY_RESULT_BACKEND do ambiente aos argumentos do código; no contêiner api, CELERY_BROKER_URL aponta
para o broker real (DB 1) e CELERY_RESULT_BACKEND para o backend real (DB 0). Por isso o app de teste só
sai daqui depois de conferir as URLs efetivas.
"""

import os
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

URL_ISOLADA = "redis://redis:6379/15"
# Destino exigido, fixo e separado de URL_ISOLADA: trocar a URL por engano não troca a conferência.
_DESTINO_ISOLADO = ("redis", "redis", 6379, "/15", "")
# O Celery usa nomes fixos (celery, unacked, celery-task-meta-*): sem prefixo possível, o uso da DB 15
# é exclusivo por trava e só as chaves que o teste registrou são apagadas.
TRAVA = "geolume-itest:trava-db15"
VALIDADE_TRAVA = 120  # segundos; a trava some sozinha se o teste morrer
_VARIAVEIS = ("CELERY_BROKER_URL", "CELERY_BROKER_READ_URL", "CELERY_BROKER_WRITE_URL", "CELERY_RESULT_BACKEND")


class BrokerNaoIsolado(RuntimeError):
    pass


def mascarar(url: str) -> str:
    partes = urlsplit(url)
    if partes.password is None:
        return url
    usuario = partes.username or ""
    host = partes.hostname + (f":{partes.port}" if partes.port else "")
    return urlunsplit(partes._replace(netloc=f"{usuario}:***@{host}"))


def _destino(url: str) -> tuple[str, str | None, int | None, str, str]:
    partes = urlsplit(url)
    return partes.scheme, partes.hostname, partes.port, partes.path, partes.query


def exigir_isolada(url: object, uso: str) -> None:
    """Mesmo esquema, host, porta e DB da URL isolada, sem query que troque a DB; senão, BrokerNaoIsolado."""
    if not isinstance(url, str) or not url or _destino(url) != _DESTINO_ISOLADO:
        texto = mascarar(url) if isinstance(url, str) else repr(url)
        raise BrokerNaoIsolado(f"URL de {uso} não é a DB isolada {URL_ISOLADA}: {texto}")


def conferir_app(app) -> dict[str, str]:
    """URLs efetivas (as que o Celery vai usar de fato), conferidas e mascaradas para registro."""
    efetivas = {"escrita": app.conf.broker_write_url, "leitura": app.conf.broker_read_url,
                "resultado": app.conf.result_backend}
    for uso, url in efetivas.items():
        exigir_isolada(url, uso)
    return {uso: mascarar(url) for uso, url in efetivas.items()}


def app_isolado(monkeypatch):
    """(app, urls mascaradas). Sobrescreve as variáveis do Celery, remove as demais CELERY_* e confere antes de devolver."""
    from celery import Celery

    for nome in [n for n in os.environ if n.startswith("CELERY_") and n not in _VARIAVEIS]:
        monkeypatch.delenv(nome)
    for nome in _VARIAVEIS:
        monkeypatch.setenv(nome, URL_ISOLADA)
    app = Celery("itest", broker=URL_ISOLADA, backend=URL_ISOLADA, set_as_current=False)
    app.conf.update(broker_read_url=URL_ISOLADA, broker_write_url=URL_ISOLADA)
    urls = conferir_app(app)
    print(f"broker de teste: {urls}")  # registro sem credenciais (pytest -s ou na falha)
    return app, urls


def cliente_isolado():
    """Cliente redis-py direto (para simular a reserva do worker), só depois de conferir a URL."""
    import redis

    exigir_isolada(URL_ISOLADA, "cliente de teste")
    cliente = redis.Redis.from_url(URL_ISOLADA)
    kwargs = cliente.connection_pool.connection_kwargs
    if (kwargs.get("host"), kwargs.get("port"), kwargs.get("db")) != ("redis", 6379, 15):
        raise BrokerNaoIsolado("cliente de teste não está na DB 15")
    return cliente, mascarar(URL_ISOLADA)


def isolar_diagnostico(monkeypatch, modulo) -> dict[str, str]:
    """Aponta as leituras do diagnóstico para a DB isolada e confere."""
    monkeypatch.setattr(modulo, "BROKER_URL", URL_ISOLADA)
    monkeypatch.setattr(modulo, "RESULT_BACKEND", URL_ISOLADA)
    urls = {"leitura_broker": modulo.BROKER_URL, "leitura_resultado": modulo.RESULT_BACKEND}
    for uso, url in urls.items():
        exigir_isolada(url, uso)
    return {uso: mascarar(url) for uso, url in urls.items()}


def _vencida() -> str:
    return f"a validade da trava ({VALIDADE_TRAVA} s) venceu: a DB pode não ser mais exclusiva; nada foi apagado"


def _liberar(trava) -> list[str]:
    """Libera e devolve os problemas em vez de levantar: um erro aqui nunca esconde outro."""
    from redis.exceptions import LockNotOwnedError

    try:
        trava.release()  # o redis-py só apaga a trava se o token ainda for o nosso
    except LockNotOwnedError:
        return [_vencida()]
    except Exception as erro:
        return [f"liberação da trava falhou ({type(erro).__name__}: {erro}); ela expira em {VALIDADE_TRAVA} s"]
    return []


def _encerrar(cliente, trava, criadas: set[str]) -> list[str]:
    """Confere e renova a posse antes de apagar; sem posse, não apaga nada. Devolve os problemas, nunca levanta.

    Com a posse renovada, apaga só as registradas e conta as sobras ainda com a trava; depois libera.
    """
    from redis.exceptions import LockNotOwnedError

    try:
        trava.reacquire()  # atômico no redis-py: só renova se o token ainda for o nosso
    except LockNotOwnedError:
        return [_vencida()]  # outro processo pode estar usando a DB: não apaga nem tenta liberar
    except Exception as erro:
        return [f"posse da trava não conferida ({type(erro).__name__}: {erro}); nada foi apagado", *_liberar(trava)]
    problemas = []
    try:
        if criadas:
            cliente.delete(*sorted(criadas))
        sobras = cliente.dbsize() - cliente.exists(TRAVA)
        if sobras > 0:  # só a contagem: nomes de chaves alheias não vão para o relatório
            problemas.append(f"{sobras} chave(s) não registrada(s) ficaram na DB isolada e não foram apagadas")
    except Exception as erro:
        problemas.append(f"limpeza das chaves registradas falhou ({type(erro).__name__}: {erro})")
    return problemas + _liberar(trava)


@contextmanager
def db_exclusiva(cliente):
    """Trava a DB isolada e entrega um set para registrar as chaves antes de criá-las; no fim apaga só essas.

    Trava ocupada ou DB com qualquer chave além da trava: BrokerNaoIsolado, sem apagar nada. No fim, a posse é
    conferida e renovada antes de apagar; trava vencida (perdida para outro processo ou não) não apaga nada.
    Sobra não registrada, trava vencida ou erro do Redis ao limpar ou liberar: BrokerNaoIsolado se o teste
    passou; se já tinha falhado, nota no erro original, que é sempre o que sobe.
    """
    trava = cliente.lock(TRAVA, timeout=VALIDADE_TRAVA, blocking=False)
    if not trava.acquire(blocking=False):
        raise BrokerNaoIsolado("DB isolada em uso por outro teste; nada foi publicado nem apagado")
    try:
        vazia = cliente.dbsize() == 1  # só a trava
    except BaseException as erro:
        for problema in _liberar(trava):
            erro.add_note(problema)
        raise
    if not vazia:
        recusa = BrokerNaoIsolado("DB isolada não está vazia; nada foi publicado nem apagado")
        for problema in _liberar(trava):
            recusa.add_note(problema)
        raise recusa
    criadas: set[str] = set()
    try:
        yield criadas
    except BaseException as erro:
        for problema in _encerrar(cliente, trava, criadas):
            erro.add_note(problema)
        raise
    problemas = _encerrar(cliente, trava, criadas)
    if problemas:
        raise BrokerNaoIsolado("; ".join(problemas))
