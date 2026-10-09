"""Guarda dos testes que publicam no Redis: só a DB isolada, conferida antes de qualquer publicação. Não conecta."""

import pytest

pytest.importorskip("celery")

import broker_isolado  # noqa: E402

REAL = "redis://redis:6379/1"
RESULTADO_REAL = "redis://redis:6379/0"
VARIAVEIS = ("CELERY_BROKER_URL", "CELERY_BROKER_READ_URL", "CELERY_BROKER_WRITE_URL", "CELERY_RESULT_BACKEND")


@pytest.fixture
def ambiente_do_api(monkeypatch):
    """Como no contêiner api (e pior): todas as variáveis que o Celery prefere ao código apontam para o real."""
    for nome in VARIAVEIS:
        monkeypatch.setenv(nome, RESULTADO_REAL if nome == "CELERY_RESULT_BACKEND" else REAL)
    monkeypatch.setenv("CELERY_CONFIG_MODULE", "config_real")


def test_url_isolada_e_a_db_15():
    assert broker_isolado.URL_ISOLADA == "redis://redis:6379/15"


@pytest.mark.parametrize("url, mascarada", [
    ("redis://:segredo@redis:6379/15", "redis://:***@redis:6379/15"),
    ("redis://usuario:segredo@redis:6379/1", "redis://usuario:***@redis:6379/1"),
    ("redis://redis:6379/15", "redis://redis:6379/15"),
])
def test_mascara_credenciais(url, mascarada):
    assert broker_isolado.mascarar(url) == mascarada


@pytest.mark.parametrize("url", [
    "redis://redis:6379/15", "redis://:segredo@redis:6379/15",
])
def test_aceita_so_a_db_isolada(url):
    broker_isolado.exigir_isolada(url, "broker")


@pytest.mark.parametrize("url", [
    REAL, RESULTADO_REAL, "redis://redis:6379", "redis://redis:6379/", "redis://outro:6379/15",
    "redis://redis:6380/15", "amqp://redis:6379/15", "redis://redis:6379/15?db=1", "redis://redis:6379/150",
    "", None,
])
def test_recusa_qualquer_outra_url(url):
    with pytest.raises(broker_isolado.BrokerNaoIsolado):
        broker_isolado.exigir_isolada(url, "broker")


def test_erro_nao_expoe_senha():
    with pytest.raises(broker_isolado.BrokerNaoIsolado) as erro:
        broker_isolado.exigir_isolada("redis://:segredo@redis:6379/1", "escrita")
    assert "segredo" not in str(erro.value) and "***" in str(erro.value) and "escrita" in str(erro.value)


def test_app_isolado_vence_o_ambiente_do_api(monkeypatch, ambiente_do_api):
    app, urls = broker_isolado.app_isolado(monkeypatch)
    assert urls == {"escrita": broker_isolado.URL_ISOLADA, "leitura": broker_isolado.URL_ISOLADA,
                    "resultado": broker_isolado.URL_ISOLADA}
    with app.connection_for_write() as escrita, app.connection_for_read() as leitura:
        for conexao in (escrita, leitura):
            assert (conexao.hostname, conexao.port, conexao.virtual_host) == ("redis", 6379, "15")
    assert app.backend.connparams["db"] == 15


def test_app_isolado_sobrescreve_todas_as_variaveis_celery(monkeypatch, ambiente_do_api):
    import os

    broker_isolado.app_isolado(monkeypatch)
    for nome in VARIAVEIS:
        assert os.environ[nome] == broker_isolado.URL_ISOLADA
    assert "CELERY_CONFIG_MODULE" not in os.environ


def test_variaveis_voltam_ao_fim_do_teste(ambiente_do_api):
    import os

    with pytest.MonkeyPatch.context() as mp:
        broker_isolado.app_isolado(mp)
    assert os.environ["CELERY_BROKER_URL"] == REAL and os.environ["CELERY_CONFIG_MODULE"] == "config_real"


@pytest.mark.parametrize("campo", ["broker_write_url", "broker_read_url", "result_backend"])
def test_conferir_app_recusa_qualquer_url_efetiva_real(monkeypatch, campo):
    from celery import Celery

    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
    conf = {"broker_url": broker_isolado.URL_ISOLADA, "result_backend": broker_isolado.URL_ISOLADA,
            "broker_read_url": broker_isolado.URL_ISOLADA, "broker_write_url": broker_isolado.URL_ISOLADA}
    conf[campo] = "redis://:segredo@redis:6379/1"
    app = Celery("itest", set_as_current=False)
    app.conf.update(conf)
    with pytest.raises(broker_isolado.BrokerNaoIsolado) as erro:
        broker_isolado.conferir_app(app)
    assert "segredo" not in str(erro.value)


def test_falha_antes_de_publicar(monkeypatch, ambiente_do_api):
    """Se a conferência falhar, app_isolado não devolve app nenhum: não há com o que publicar."""
    monkeypatch.setattr(broker_isolado, "URL_ISOLADA", REAL)  # alvo errado de propósito
    publicou = []
    monkeypatch.setattr("kombu.Producer.publish", lambda *a, **k: publicou.append(a))
    with pytest.raises(broker_isolado.BrokerNaoIsolado):
        broker_isolado.app_isolado(monkeypatch)
    assert publicou == []


def test_cliente_redis_isolado_confere_a_db(monkeypatch):
    cliente, url = broker_isolado.cliente_isolado()
    kwargs = cliente.connection_pool.connection_kwargs
    assert (kwargs["host"], kwargs["port"], kwargs["db"]) == ("redis", 6379, 15)
    assert url == broker_isolado.URL_ISOLADA


def test_cliente_redis_recusa_url_real(monkeypatch):
    monkeypatch.setattr(broker_isolado, "URL_ISOLADA", REAL)
    with pytest.raises(broker_isolado.BrokerNaoIsolado):
        broker_isolado.cliente_isolado()


def test_diagnostico_apontado_para_a_db_isolada(monkeypatch):
    import diagnostico_fila

    urls = broker_isolado.isolar_diagnostico(monkeypatch, diagnostico_fila)
    assert diagnostico_fila.BROKER_URL == diagnostico_fila.RESULT_BACKEND == broker_isolado.URL_ISOLADA
    assert urls == {"leitura_broker": broker_isolado.URL_ISOLADA, "leitura_resultado": broker_isolado.URL_ISOLADA}


# ---- Uso exclusivo da DB isolada: apaga só as chaves registradas pelo teste -----------------------


class _Trava:
    """Como a do redis-py: token próprio; reacquire e release só com a posse (senão LockNotOwnedError)."""

    def __init__(self, redis_falso, nome, timeout):
        self.r, self.nome, self.timeout = redis_falso, nome, timeout
        self.token = f"token-{len(redis_falso.travas)}".encode()
        self.r.travas.append(self)

    def acquire(self, blocking=True):
        assert blocking is False
        if self.nome in self.r.dados:
            return False
        self.r.dados[self.nome] = self.token
        return True

    def _conferir(self, operacao):
        from redis.exceptions import LockNotOwnedError

        self.r.ordem.append(operacao)
        if operacao in self.r.falhas:
            raise self.r.falhas[operacao]
        if self.r.dados.get(self.nome) != self.token:  # venceu, ou outro processo travou depois
            raise LockNotOwnedError(f"trava não é mais nossa ({operacao})")

    def reacquire(self):
        self._conferir("reacquire")
        return True

    def release(self):
        self._conferir("release")
        del self.r.dados[self.nome]


class _RedisFalso:
    """Só o que a guarda pode usar; SCAN, KEYS e FLUSH quebram o teste. `falhas` injeta erro por operação."""

    def __init__(self, **dados):
        self.dados, self.apagadas, self.travas, self.ordem, self.falhas = dict(dados), [], [], [], {}

    def lock(self, nome, timeout=None, blocking=True):
        return _Trava(self, nome, timeout)

    def dbsize(self):
        self.ordem.append("dbsize")
        if "dbsize" in self.falhas:
            raise self.falhas["dbsize"]
        return len(self.dados)

    def exists(self, *chaves):
        return sum(chave in self.dados for chave in chaves)

    def delete(self, *chaves):
        self.ordem.append("delete")
        if "delete" in self.falhas:
            raise self.falhas["delete"]
        self.apagadas.extend(chaves)
        for chave in chaves:
            self.dados.pop(chave, None)

    def __getattr__(self, nome):
        raise AssertionError(f"comando não permitido na limpeza: {nome}")


def test_chave_nao_registrada_e_preservada_e_falha_ainda_com_a_trava():
    r = _RedisFalso()
    with pytest.raises(broker_isolado.BrokerNaoIsolado, match="não registrada") as erro:
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            r.dados["celery"] = b"x"
            r.dados["externa"] = b"de outro processo"  # não registrada
    assert r.dados == {"externa": b"de outro processo"}
    assert r.apagadas == ["celery"]
    assert "externa" not in str(erro.value)  # só a contagem, sem nomes


def test_sobras_conferidas_antes_de_liberar_a_trava():
    r = _RedisFalso()
    contagens = []
    original = r.dbsize
    r.dbsize = lambda: contagens.append(broker_isolado.TRAVA in r.dados) or original()
    with pytest.raises(broker_isolado.BrokerNaoIsolado):
        with broker_isolado.db_exclusiva(r):
            r.dados["externa"] = b"y"
    assert contagens == [True, True]  # na entrada e na saída, sempre com a trava ainda no Redis


def test_db_limpa_no_fim_passa_e_libera_a_trava():
    r = _RedisFalso()
    with broker_isolado.db_exclusiva(r) as criadas:
        criadas.add("celery")
        r.dados["celery"] = b"x"
    assert r.dados == {}


def test_db_com_chave_preexistente_aborta_sem_apagar_nada():
    r = _RedisFalso(preexistente=b"alheia")
    with pytest.raises(broker_isolado.BrokerNaoIsolado):
        with broker_isolado.db_exclusiva(r):
            pytest.fail("não pode entrar com a DB ocupada")
    assert r.dados == {"preexistente": b"alheia"} and r.apagadas == []


def test_trava_ocupada_aborta_sem_apagar_nada():
    r = _RedisFalso(**{broker_isolado.TRAVA: b"de outro teste"})
    with pytest.raises(broker_isolado.BrokerNaoIsolado):
        with broker_isolado.db_exclusiva(r):
            pytest.fail("não pode entrar sem a trava")
    assert r.dados == {broker_isolado.TRAVA: b"de outro teste"} and r.apagadas == []


def test_falha_no_teste_ainda_apaga_so_as_registradas_e_libera_a_trava():
    r = _RedisFalso()
    with pytest.raises(RuntimeError):
        with broker_isolado.db_exclusiva(r) as criadas:
            r.dados.update({"celery": b"x", "externa": b"y"})
            criadas.add("celery")
            raise RuntimeError("teste quebrou")
    assert r.dados == {"externa": b"y"}


def test_sobra_na_falha_do_teste_vira_nota_sem_esconder_o_erro_original():
    r = _RedisFalso()
    with pytest.raises(RuntimeError, match="teste quebrou") as erro:
        with broker_isolado.db_exclusiva(r):
            r.dados["externa"] = b"y"
            raise RuntimeError("teste quebrou")
    assert any("não registrada" in nota for nota in getattr(erro.value, "__notes__", []))
    assert r.dados == {"externa": b"y"}


def _vencer_e_outro_processo_travar(r):
    """A validade vence no meio do teste e outro processo trava a DB e grava nela."""
    del r.dados[broker_isolado.TRAVA]
    assert r.lock(broker_isolado.TRAVA, timeout=broker_isolado.VALIDADE_TRAVA).acquire(blocking=False)
    r.dados["celery"] = b"do outro processo"


def test_trava_vencida_nao_esconde_a_falha_original():
    r = _RedisFalso()
    with pytest.raises(RuntimeError, match="teste quebrou") as erro:
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            _vencer_e_outro_processo_travar(r)
            raise RuntimeError("teste quebrou")
    assert any("validade" in nota for nota in getattr(erro.value, "__notes__", []))
    assert r.apagadas == [] and r.dados["celery"] == b"do outro processo"  # sem posse, nada é apagado
    assert r.dados[broker_isolado.TRAVA] == r.travas[1].token  # a trava do outro fica


def test_trava_vencida_num_teste_que_passou_falha_sem_apagar_nada():
    r = _RedisFalso()
    with pytest.raises(broker_isolado.BrokerNaoIsolado, match="validade"):
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            _vencer_e_outro_processo_travar(r)
    assert r.apagadas == [] and r.dados["celery"] == b"do outro processo"
    assert r.dados[broker_isolado.TRAVA] == r.travas[1].token


def test_trava_vencida_sem_ninguem_no_lugar_tambem_nao_apaga():
    r = _RedisFalso()
    with pytest.raises(broker_isolado.BrokerNaoIsolado, match="validade"):
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            r.dados["celery"] = b"x"
            del r.dados[broker_isolado.TRAVA]
    assert r.apagadas == [] and r.dados == {"celery": b"x"}


def test_posse_confirmada_e_renovada_antes_de_apagar():
    r = _RedisFalso()
    with broker_isolado.db_exclusiva(r) as criadas:
        criadas.add("celery")
        r.dados["celery"] = b"x"
    assert r.ordem == ["dbsize", "reacquire", "delete", "dbsize", "release"]


@pytest.mark.parametrize("operacao", ["reacquire", "release"])
def test_erro_de_redis_na_trava_num_teste_que_passou_vira_broker_nao_isolado(operacao):
    from redis.exceptions import ConnectionError

    r = _RedisFalso()
    with pytest.raises(broker_isolado.BrokerNaoIsolado, match="ConnectionError"):
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            r.dados["celery"] = b"x"
            r.falhas[operacao] = ConnectionError("redis caiu")


@pytest.mark.parametrize("operacao", ["reacquire", "delete", "dbsize", "release"])
def test_erro_de_redis_no_encerramento_vira_nota_e_preserva_a_falha_original(operacao):
    from redis.exceptions import ConnectionError

    r = _RedisFalso()
    with pytest.raises(RuntimeError, match="teste quebrou") as erro:
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            r.dados["celery"] = b"x"
            r.falhas[operacao] = ConnectionError("redis caiu")
            raise RuntimeError("teste quebrou")
    assert any("ConnectionError" in nota for nota in getattr(erro.value, "__notes__", []))


def test_erro_ao_apagar_ainda_libera_a_trava():
    from redis.exceptions import ConnectionError

    r = _RedisFalso()
    with pytest.raises(broker_isolado.BrokerNaoIsolado, match="ConnectionError"):
        with broker_isolado.db_exclusiva(r) as criadas:
            criadas.add("celery")
            r.falhas["delete"] = ConnectionError("redis caiu")
    assert broker_isolado.TRAVA not in r.dados


def test_erro_ao_liberar_na_entrada_nao_esconde_o_erro_original():
    from redis.exceptions import ConnectionError

    r = _RedisFalso()
    r.falhas.update(dbsize=RuntimeError("dbsize quebrou"), release=ConnectionError("redis caiu"))
    with pytest.raises(RuntimeError, match="dbsize quebrou") as erro:
        with broker_isolado.db_exclusiva(r):
            pytest.fail("não pode entrar")
    assert any("ConnectionError" in nota for nota in getattr(erro.value, "__notes__", []))


def test_erro_ao_liberar_com_db_suja_mantem_a_recusa():
    from redis.exceptions import ConnectionError

    r = _RedisFalso(preexistente=b"alheia")
    r.falhas["release"] = ConnectionError("redis caiu")
    with pytest.raises(broker_isolado.BrokerNaoIsolado, match="não está vazia") as erro:
        with broker_isolado.db_exclusiva(r):
            pytest.fail("não pode entrar")
    assert any("ConnectionError" in nota for nota in getattr(erro.value, "__notes__", []))
    assert r.dados["preexistente"] == b"alheia" and r.apagadas == []


def test_trava_tem_validade_para_nao_ficar_presa():
    travas = []
    r = _RedisFalso()
    original = r.lock
    r.lock = lambda nome, timeout=None, blocking=True: travas.append(timeout) or original(nome, timeout, blocking)
    with broker_isolado.db_exclusiva(r):
        pass
    assert travas and travas[0] and travas[0] <= 300


def test_fonte_da_guarda_nao_usa_varredura_nem_flush():
    from pathlib import Path

    fonte = Path(broker_isolado.__file__).read_text(encoding="utf-8")
    for proibido in ("scan", "keys(", "flush"):
        assert proibido not in fonte.lower()


# Guarda por lista de permitidos: proibir forma por forma deixa passar alias, pipeline, execute_command e eval.
# Toda varredura tem de ser um retrato só de leitura ({k: r.dump(k) for k in r.scan_iter()}), sem nome que a
# guarde para depois; toda exclusão no Redis, uma destas linhas, que apagam pelo nome.
_APAGAR_PERMITIDO = {"cliente.delete(*sorted(criadas))", "cliente.delete(externa)", "cliente.delete(propria)"}
_RETRATO = r"\{(\w+): (\w+)\.dump\(\1\) for \1 in \2\.scan_iter\(\)\}"
_PROIBIDO = (r"flushdb|flushall|execute_command|\beval(sha)?\b|register_script|\bunlink\b|\.keys\b|randomkey"
             r"|swapdb|getdel")


def _limpeza_ampla(fonte: str) -> list[str]:
    import re

    problemas = [m.group(0) for m in re.finditer(_PROIBIDO, fonte, re.IGNORECASE)]
    problemas += [linha.strip() for linha in re.sub(_RETRATO, "", fonte).splitlines()
                  if re.search(r"scan", linha, re.IGNORECASE)]
    for linha in fonte.splitlines():
        codigo = linha.split("#")[0].strip()
        if re.search(r"\.delete\b|[\"']delete[\"']", codigo, re.IGNORECASE) and codigo not in _APAGAR_PERMITIDO:
            problemas.append(codigo)
    return problemas


@pytest.mark.parametrize("arquivo", ["broker_isolado.py", "test_recuperacao_integracao.py"])
def test_fonte_nao_tem_limpeza_ampla(arquivo):
    from pathlib import Path

    fonte = (Path(broker_isolado.__file__).parent / arquivo).read_text(encoding="utf-8")
    assert _limpeza_ampla(fonte) == []


@pytest.mark.parametrize("trecho", [
    "r.flushdb()", "r.FLUSHALL()", "r.delete(*r.keys())", "r.delete(*r.scan_iter())", "r.unlink(k)",
    "for k in r.scan_iter():\n    r.delete(k)\n", "for k in r.scan_iter(match='x*'):\n    if k:\n        r.delete(k)\n",
    'r.execute_command("FLUSHDB")', 'r.execute_command("FLUSHALL")', 'r.execute_command("DEL", "celery")',
    'with r.pipeline() as pipe:\n    pipe.delete("celery")\n    pipe.execute()\n',
    'r.pipeline().delete(k).execute()',
    "r.eval(\"return redis.call('del', unpack(KEYS))\", 1, 'celery')",
    "r.register_script(\"return redis.call('flushdb')\")()",
    "chaves = list(r.scan_iter())\nr.delete(*chaves)\n",  # varredura guardada num nome
    "chaves = [k for k in r.scan_iter(match='celery*')]\n",
    "varrer = r.scan_iter\nfor k in varrer():\n    pass\n",
    "_, chaves = r.scan(0)\n",
    "apagar = r.delete\napagar('celery')\n",  # alias do comando
    "getattr(r, 'delete')('celery')",
    "r.delete('celery')",  # pelo nome, mas fora da lista permitida
])
def test_guarda_reconhece_limpeza_ampla(trecho):
    assert _limpeza_ampla(trecho)


@pytest.mark.parametrize("trecho", [
    "antes = {k: r.dump(k) for k in r.scan_iter()}",
    "cliente.delete(*sorted(criadas))",
    "cliente.delete(externa)  # pelo nome",
    'cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))',  # SQL no schema itest_*, não é o Redis
])
def test_guarda_aceita_retrato_e_exclusao_pelo_nome(trecho):
    assert _limpeza_ampla(trecho) == []
