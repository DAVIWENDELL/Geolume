"""Integridade semântica dos 3 artefatos antes de um job virar completed. Só lê o disco."""

import json
import os

import pytest

import artefatos
import integridade

JOB = "job-a"
ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")


@pytest.fixture
def pasta(tmp_path):
    return artefatos.gravar(tmp_path / JOB, JOB)


def _conferir(tmp_path, job_id=JOB):
    return integridade.conferir_artefatos(tmp_path, job_id)


def _json(pasta, **mudancas):
    dados = {**artefatos.resultado(JOB), **mudancas}
    (pasta / "resultado.json").write_text(json.dumps(dados), encoding="utf-8")


def test_tres_artefatos_validos(tmp_path, pasta):
    assert _conferir(tmp_path) == {"ok": True, "motivos": {}}


def test_artefatos_e_os_do_run_job():
    assert integridade.ARTEFATOS == ARTEFATOS


# ---- Presença e tipo de arquivo ------------------------------------------------------


@pytest.mark.parametrize("nome", ARTEFATOS)
def test_cada_artefato_ausente(tmp_path, pasta, nome):
    (pasta / nome).unlink()
    assert _conferir(tmp_path) == {"ok": False, "motivos": {nome: "ausente"}}


def test_pasta_do_job_ausente(tmp_path):
    assert _conferir(tmp_path) == {"ok": False, "motivos": {"pasta": "ausente"}}


@pytest.mark.parametrize("nome", ARTEFATOS)
def test_cada_artefato_vazio(tmp_path, pasta, nome):
    (pasta / nome).write_bytes(b"")
    assert _conferir(tmp_path) == {"ok": False, "motivos": {nome: "vazio"}}


@pytest.mark.parametrize("nome, motivo", [
    ("mapa.pdf", "sem_cabecalho_pdf"), ("memorial.pdf", "sem_cabecalho_pdf"), ("resultado.json", "json_invalido")])
def test_cada_artefato_preenchido_com_nul(tmp_path, pasta, nome, motivo):
    """Caso real de 2026-10-01: tamanho plausível, conteúdo só com bytes zero."""
    tamanho = (pasta / nome).stat().st_size
    (pasta / nome).write_bytes(b"\x00" * tamanho)
    assert _conferir(tmp_path) == {"ok": False, "motivos": {nome: motivo}}


def test_tres_artefatos_zerados(tmp_path, pasta):
    for nome in ARTEFATOS:
        (pasta / nome).write_bytes(b"\x00" * 1024)
    assert _conferir(tmp_path)["motivos"] == {
        "mapa.pdf": "sem_cabecalho_pdf", "memorial.pdf": "sem_cabecalho_pdf", "resultado.json": "json_invalido"}


@pytest.mark.parametrize("nome", ["mapa.pdf", "memorial.pdf"])
def test_pdf_sem_cabecalho(tmp_path, pasta, nome):
    (pasta / nome).write_bytes(b"<html>%%EOF\n")
    assert _conferir(tmp_path)["motivos"] == {nome: "sem_cabecalho_pdf"}


@pytest.mark.parametrize("nome", ["mapa.pdf", "memorial.pdf"])
def test_pdf_truncado_sem_eof(tmp_path, pasta, nome):
    (pasta / nome).write_bytes(artefatos.PDF[:-7])
    assert _conferir(tmp_path)["motivos"] == {nome: "pdf_truncado"}


def test_eof_so_vale_nos_ultimos_1024_bytes(tmp_path, pasta):
    (pasta / "mapa.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n" + b"x" * 1024)
    assert _conferir(tmp_path)["motivos"] == {"mapa.pdf": "pdf_truncado"}


def test_pdf_grande_com_eof_no_fim_e_valido(tmp_path, pasta):
    (pasta / "mapa.pdf").write_bytes(b"%PDF-1.7\n" + b"x" * 100_000 + b"\n%%EOF\n")
    assert _conferir(tmp_path)["ok"] is True


def test_acima_do_teto_e_recusado(tmp_path, pasta, monkeypatch):
    monkeypatch.setattr(integridade, "TAMANHO_MAXIMO", len(artefatos.PDF) - 1)
    assert _conferir(tmp_path)["motivos"]["mapa.pdf"] == "grande_demais"


@pytest.mark.parametrize("nome", ARTEFATOS)
def test_symlink_recusado_mesmo_para_artefato_valido(tmp_path, pasta, nome):
    outro = artefatos.gravar(tmp_path / "outro", JOB)
    (pasta / nome).unlink()
    (pasta / nome).symlink_to(outro / nome)
    assert _conferir(tmp_path)["motivos"] == {nome: "link_recusado"}


@pytest.mark.parametrize("nome", ARTEFATOS)
def test_hard_link_recusado(tmp_path, pasta, nome):
    os.link(pasta / nome, tmp_path / f"copia-{nome}")
    assert _conferir(tmp_path)["motivos"] == {nome: "hard_link_recusado"}


def test_artefato_que_e_pasta(tmp_path, pasta):
    (pasta / "resultado.json").unlink()
    (pasta / "resultado.json").mkdir()
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "nao_e_arquivo_regular"}


def test_pasta_do_job_que_e_link_e_recusada(tmp_path):
    real = artefatos.gravar(tmp_path / "real", JOB)
    (tmp_path / JOB).symlink_to(real)
    assert _conferir(tmp_path) == {"ok": False, "motivos": {"pasta": "link_recusado"}}


def test_pasta_do_job_que_e_arquivo(tmp_path):
    (tmp_path / JOB).write_bytes(b"x")
    assert _conferir(tmp_path) == {"ok": False, "motivos": {"pasta": "nao_e_pasta"}}


@pytest.mark.parametrize("job_id", ["", "..", "a/b", "../job-a", "a b", "x" * 65, None, 7])
def test_job_id_fora_do_formato_nao_toca_no_disco(tmp_path, job_id):
    assert _conferir(tmp_path, job_id) == {"ok": False, "motivos": {"job_id": "job_id_invalido"}}


# ---- resultado.json -------------------------------------------------------------------


@pytest.mark.parametrize("conteudo", [b"{", b"\xff\xfe{}", b"{} lixo", b'{"a": NaN}', b'{"area_ha": Infinity}'])
def test_json_invalido(tmp_path, pasta, conteudo):
    (pasta / "resultado.json").write_bytes(conteudo)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "json_invalido"}


@pytest.mark.parametrize("valor", [[], "texto", 1, None])
def test_json_que_nao_e_objeto(tmp_path, pasta, valor):
    (pasta / "resultado.json").write_text(json.dumps(valor), encoding="utf-8")
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "json_nao_objeto"}


@pytest.mark.parametrize("job_id", ["outro-job", "", None, 1])
def test_job_id_de_outro_job(tmp_path, pasta, job_id):
    _json(pasta, job_id=job_id)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "job_id_divergente"}


@pytest.mark.parametrize("versao", [2, 0, "1", 1.0, True, None])
def test_versao_do_esquema_diferente(tmp_path, pasta, versao):
    _json(pasta, versao_esquema=versao)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "versao_esquema_invalida"}


@pytest.mark.parametrize("valor", ["mapa.pdf", "../memorial.pdf", None])
def test_pdf_memorial_diferente(tmp_path, pasta, valor):
    _json(pasta, pdf_memorial=valor)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "pdf_memorial_invalido"}


@pytest.mark.parametrize("campo", ["entrada", "propriedades", "crs_saida", "area_ha", "perimetro_m", "vertices",
                                   "metricas", "versao_esquema", "job_id", "pdf_memorial"])
def test_campo_obrigatorio_ausente(tmp_path, pasta, campo):
    dados = artefatos.resultado(JOB)
    del dados[campo]
    (pasta / "resultado.json").write_text(json.dumps(dados), encoding="utf-8")
    assert _conferir(tmp_path)["ok"] is False
    assert list(_conferir(tmp_path)["motivos"]) == ["resultado.json"]


VERTICE = artefatos.resultado(JOB)["vertices"][0]


@pytest.mark.parametrize("campo, valor", [
    ("entrada", 1), ("entrada", ""), ("propriedades", []), ("crs_saida", "31983"), ("crs_saida", 31983),
    ("area_ha", "0.01"), ("area_ha", True), ("area_ha", -0.0001), ("area_ha", -1.0),
    ("perimetro_m", None), ("perimetro_m", -0.01), ("perimetro_m", False),
    ("vertices", []), ("vertices", {}), ("vertices", [VERTICE, VERTICE]), ("vertices", [VERTICE, VERTICE, "V3"]),
    ("vertices", [VERTICE, VERTICE, {**VERTICE, "e": "500000"}]), ("vertices", [VERTICE, VERTICE, {"id": "V3"}]),
    ("metricas", None),
])
def test_campo_tecnico_com_tipo_errado(tmp_path, pasta, campo, valor):
    _json(pasta, **{campo: valor})
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": f"campo_invalido:{campo}"}


def test_numeros_inteiros_valem(tmp_path, pasta):
    _json(pasta, area_ha=2, perimetro_m=40)
    assert _conferir(tmp_path)["ok"] is True


# ---- Métricas e vértices completos, como o run_job grava ------------------------------

METRICAS = artefatos.resultado(JOB)["metricas"]
RSS = ("pico_rss_mb", "rss_antes_mb", "rss_depois_mb")
FASES = ("carregar", "processar", "renderizar_pdf", "total_job")
# Fora do JSON padrão: o parser recusa NaN/Infinity, mas 1e999 vira infinito ao ler; por isso o marcador.
INFINITO = "__infinito__"


def _gravar(pasta, dados):
    texto = json.dumps(dados).replace(f'"{INFINITO}"', "1e999")
    (pasta / "resultado.json").write_text(texto, encoding="utf-8")


def _com_metricas(pasta, **mudancas):
    metricas = {**json.loads(json.dumps(METRICAS)), **mudancas}
    _gravar(pasta, {**artefatos.resultado(JOB), "metricas": metricas})


def _sem_metrica(pasta, nome):
    metricas = json.loads(json.dumps(METRICAS))
    if nome in metricas:
        del metricas[nome]
    else:
        del metricas["fases_ms"][nome]
    _gravar(pasta, {**artefatos.resultado(JOB), "metricas": metricas})


def _com_vertice(pasta, **mudancas):
    dados = artefatos.resultado(JOB)
    dados["vertices"][1] = {**dados["vertices"][1], **mudancas}
    _gravar(pasta, dados)


def test_json_completo_do_run_job_e_aprovado(tmp_path, pasta):
    assert set(METRICAS) == {"fases_ms", *RSS} and set(METRICAS["fases_ms"]) == set(FASES)
    assert _conferir(tmp_path) == {"ok": True, "motivos": {}}


@pytest.mark.parametrize("nome", ["fases_ms", *RSS, *FASES])
def test_cada_metrica_ausente(tmp_path, pasta, nome):
    _sem_metrica(pasta, nome)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:metricas"}


@pytest.mark.parametrize("valor", [None, True, False, "245.7", INFINITO, -1.0, [], {}])
@pytest.mark.parametrize("nome", RSS)
def test_cada_metrica_de_memoria_com_tipo_ou_valor_errado(tmp_path, pasta, nome, valor):
    _com_metricas(pasta, **{nome: valor})
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:metricas"}


@pytest.mark.parametrize("valor", [None, True, False, "9.5", INFINITO, -0.1, []])
@pytest.mark.parametrize("fase", FASES)
def test_cada_fase_com_tipo_ou_valor_errado(tmp_path, pasta, fase, valor):
    _com_metricas(pasta, fases_ms={**METRICAS["fases_ms"], fase: valor})
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:metricas"}


@pytest.mark.parametrize("fases", [None, [], "x", {**METRICAS["fases_ms"], "extra": 1.0}])
def test_fases_ms_que_nao_e_o_mapa_das_quatro_fases(tmp_path, pasta, fases):
    _com_metricas(pasta, fases_ms=fases)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:metricas"}


@pytest.mark.parametrize("campo", ["area_ha", "perimetro_m"])
@pytest.mark.parametrize("zero", [0, 0.0])
def test_area_e_perimetro_zero_valem_porque_o_run_job_arredonda(tmp_path, pasta, campo, zero):
    """processing.py: round(area / 10000, 4) e round(length, 2). Gerado de verdade pelo QGIS: quadrado de 0,4 m
    dá area_ha 0.0; quadrado de 1 mm dá também perimetro_m 0.0."""
    _json(pasta, **{campo: zero})
    assert _conferir(tmp_path) == {"ok": True, "motivos": {}}


@pytest.mark.parametrize("zero", [0, 0.0])
def test_distancia_zero_vale_porque_o_run_job_arredonda(tmp_path, pasta, zero):
    """geometry.vertex_table: vértices distintos ao milímetro, distância round(..., 2); a 3 mm dá 0.0."""
    _com_vertice(pasta, distancia_m=zero)
    assert _conferir(tmp_path) == {"ok": True, "motivos": {}}


def test_poligono_milimetrico_inteiro_em_zero_vale(tmp_path, pasta):
    """Forma exata do resultado.json de um quadrado de 1 mm gerado pelo QGIS (area, perímetro e lados 0.0)."""
    dados = artefatos.resultado(JOB)
    dados.update(area_ha=0.0, perimetro_m=0.0)
    for vertice in dados["vertices"]:
        vertice["distancia_m"] = 0.0
    dados["metricas"]["fases_ms"]["processar"] = 0.0
    _gravar(pasta, dados)
    assert _conferir(tmp_path) == {"ok": True, "motivos": {}}


@pytest.mark.parametrize("nome", RSS)
def test_memoria_zero_continua_recusada(tmp_path, pasta, nome):
    """RSS de um processo vivo nunca é 0: sem evidência de zero, a regra não relaxa."""
    _com_metricas(pasta, **{nome: 0.0})
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:metricas"}


def test_fase_zero_vale_porque_o_run_job_arredonda_para_0_1_ms(tmp_path, pasta):
    _com_metricas(pasta, fases_ms={**METRICAS["fases_ms"], "processar": 0.0})
    assert _conferir(tmp_path)["ok"] is True


@pytest.mark.parametrize("campo, valor", [
    ("azimute", ""), ("azimute", None), ("azimute", 90), ("azimute", True),
    ("distancia_m", -0.01), ("distancia_m", -10.0), ("distancia_m", INFINITO),
    ("distancia_m", None), ("distancia_m", True), ("distancia_m", "10.0"),
    ("e", True), ("e", None), ("e", INFINITO), ("n", "8000000"), ("id", ""), ("id", 1),
])
def test_vertice_com_campo_errado(tmp_path, pasta, campo, valor):
    _com_vertice(pasta, **{campo: valor})
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:vertices"}


@pytest.mark.parametrize("campo", ["id", "e", "n", "azimute", "distancia_m"])
def test_vertice_sem_campo(tmp_path, pasta, campo):
    dados = artefatos.resultado(JOB)
    del dados["vertices"][2][campo]
    _gravar(pasta, dados)
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "campo_invalido:vertices"}


@pytest.mark.parametrize("texto", ['"distancia_m": NaN', '"distancia_m": Infinity', '"pico_rss_mb": -Infinity'])
def test_nan_e_infinito_literais_sao_json_invalido(tmp_path, pasta, texto):
    nome, _ = texto.split(": ")
    conteudo = json.dumps(artefatos.resultado(JOB))
    conteudo = conteudo.replace(f'{nome}: 10.0', texto, 1).replace(f'{nome}: 245.7', texto, 1)
    assert texto in conteudo
    (pasta / "resultado.json").write_text(conteudo, encoding="utf-8")
    assert _conferir(tmp_path)["motivos"] == {"resultado.json": "json_invalido"}


# ---- Saída e leitura ------------------------------------------------------------------


def test_saida_sem_caminho_conteudo_nem_traceback(tmp_path, pasta):
    for nome in ARTEFATOS:
        (pasta / nome).write_bytes(b"\x00SEGREDO-do-cliente\x00" * 10)
    (pasta / "resultado.json").write_text(json.dumps({**artefatos.resultado("SEGREDO"), "x": "SEGREDO"}))
    texto = json.dumps(_conferir(tmp_path))
    for proibido in (str(tmp_path), "SEGREDO", "Traceback", "Error", "/"):
        assert proibido not in texto
    assert set(json.loads(texto)) == {"ok", "motivos"}


def test_erro_de_leitura_vira_motivo_e_nao_excecao(tmp_path, pasta, monkeypatch):
    real = os.open

    def negar(caminho, *args, **kwargs):
        if str(caminho).endswith("memorial.pdf"):
            raise PermissionError(13, "negado", str(caminho))
        return real(caminho, *args, **kwargs)

    monkeypatch.setattr(integridade.os, "open", negar)
    assert _conferir(tmp_path) == {"ok": False, "motivos": {"memorial.pdf": "ilegivel"}}


def test_somente_leitura(tmp_path, pasta, monkeypatch):
    antes = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in pasta.iterdir())
    for nome in ("unlink", "remove", "rename", "replace", "rmdir", "truncate"):
        monkeypatch.setattr(integridade.os, nome, lambda *a, **k: pytest.fail("escrita"), raising=False)
    assert _conferir(tmp_path)["ok"] is True
    assert sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in pasta.iterdir()) == antes


def test_fonte_nao_escreve():
    from pathlib import Path

    fonte = Path(integridade.__file__).read_text(encoding="utf-8")
    for proibido in ("O_WRONLY", "O_RDWR", "O_CREAT", "unlink", "rmtree", "write", ".replace(", "rename"):
        assert proibido not in fonte, proibido


# ---- Valores numéricos extremos ou falsos: sempre motivo, nunca exceção -------------------

GIGANTE = "1" + "0" * 400  # inteiro JSON válido, grande demais para float: math.isfinite levanta OverflowError
CASOS_NUMERICOS = {"int_gigante": GIGANTE, "negativo_gigante": "-" + GIGANTE, "float_infinito": "1e999",
                   "float_menos_infinito": "-1e999", "nan": "NaN", "string_numerica": '"10.0"', "bool": "true",
                   "null": "null"}
MOTIVO_DO_CAMPO = {"pico_rss_mb": "campo_invalido:metricas", "processar": "campo_invalido:metricas",
                   "e": "campo_invalido:vertices", "distancia_m": "campo_invalido:vertices",
                   "area_ha": "campo_invalido:area_ha", "perimetro_m": "campo_invalido:perimetro_m"}
MARCADOR = "__valor__"


def _json_com_literal(pasta, campo, literal):
    """Grava o resultado.json do job com `literal` (texto JSON cru) no lugar do valor de `campo`."""
    dados = artefatos.resultado(JOB)
    if campo in dados["metricas"]:
        dados["metricas"][campo] = MARCADOR
    elif campo in dados["metricas"]["fases_ms"]:
        dados["metricas"]["fases_ms"][campo] = MARCADOR
    elif campo in dados["vertices"][0]:
        dados["vertices"][0][campo] = MARCADOR
    else:
        dados[campo] = MARCADOR
    (pasta / "resultado.json").write_text(json.dumps(dados).replace(f'"{MARCADOR}"', literal), encoding="utf-8")


@pytest.mark.parametrize("campo", ["pico_rss_mb", "e", "distancia_m"])
def test_inteiro_gigante_e_recusado_sem_overflow(tmp_path, pasta, campo):
    _json_com_literal(pasta, campo, GIGANTE)
    json.loads((pasta / "resultado.json").read_text(encoding="utf-8"))  # JSON válido: o problema é só o valor
    assert _conferir(tmp_path) == {"ok": False, "motivos": {"resultado.json": MOTIVO_DO_CAMPO[campo]}}


@pytest.mark.parametrize("caso", CASOS_NUMERICOS)
@pytest.mark.parametrize("campo", MOTIVO_DO_CAMPO)
def test_nenhum_numero_invalido_escapa_como_excecao(tmp_path, pasta, campo, caso):
    _json_com_literal(pasta, campo, CASOS_NUMERICOS[caso])
    resultado = _conferir(tmp_path)  # nunca levanta
    esperado = "json_invalido" if caso == "nan" else MOTIVO_DO_CAMPO[campo]
    assert resultado == {"ok": False, "motivos": {"resultado.json": esperado}}


def test_inteiro_com_digitos_demais_para_o_python_e_json_invalido(tmp_path, pasta):
    """Acima do limite de dígitos do int do Python, o próprio parser recusa: vira json_invalido, não exceção."""
    _json_com_literal(pasta, "pico_rss_mb", "1" * 5000)
    assert _conferir(tmp_path) == {"ok": False, "motivos": {"resultado.json": "json_invalido"}}
