"""Integridade semântica dos 3 artefatos de um job, conferida antes de ele virar completed. Só lê o disco.

Caso real que motivou (2026-10-01): jobs completed com mapa.pdf, memorial.pdf e resultado.json de tamanho
plausível e conteúdo só com bytes zero. Existir não basta: o PDF precisa de cabeçalho e fim, e o resultado.json
precisa ser o deste job, no esquema que o run_job grava. A saída só traz códigos: nada de caminho nem conteúdo.
"""

import json
import math
import os
import stat
from pathlib import Path

from geolume_worker.prancha import JOB_ID

ARTEFATOS = ("mapa.pdf", "memorial.pdf", "resultado.json")
TAMANHO_MAXIMO = 50 * 1024 * 1024  # bem acima de um mapa com logo (~20 KB hoje); acima disso não é nosso
FIM_DO_PDF = 1024  # %%EOF tem de estar nos últimos 1024 bytes, como os leitores de PDF exigem
VERSAO_ESQUEMA = 1  # a de geolume_worker.job; repetida aqui para não importar o QGIS
FASES = ("carregar", "processar", "renderizar_pdf", "total_job")  # geolume_worker.job.FASES, idem
RSS = ("pico_rss_mb", "rss_antes_mb", "rss_depois_mb")


def _numero(valor: object) -> bool:
    """Número finito de verdade: bool, string numérica, null, NaN e infinito (1e999 lido) ficam de fora.

    Inteiro grande demais para float (ex.: 10**400, JSON válido) também: o run_job nunca grava nada perto disso.
    """
    if type(valor) not in (int, float):
        return False
    try:
        return math.isfinite(valor)
    except OverflowError:
        return False


def _numero_positivo(valor: object) -> bool:
    return _numero(valor) and valor > 0


def _nao_negativo(valor: object) -> bool:
    """Para o que o run_job arredonda e pode dar 0.0 em entrada válida (verificado gerando pelo QGIS):
    area_ha (4 casas), perimetro_m e distancia_m (2 casas) e as fases (0,1 ms)."""
    return _numero(valor) and valor >= 0


def _texto(valor: object) -> bool:
    return isinstance(valor, str) and bool(valor)


def _vertice(valor: object) -> bool:
    return (isinstance(valor, dict) and _texto(valor.get("id")) and _numero(valor.get("e"))
            and _numero(valor.get("n")) and _texto(valor.get("azimute")) and _nao_negativo(valor.get("distancia_m")))


def _metricas(valor: object) -> bool:
    """As 4 chaves do run_job: fases_ms com as 4 fases (>= 0) e RSS > 0 (processo vivo nunca tem 0)."""
    if not isinstance(valor, dict) or set(valor) != {"fases_ms", *RSS}:
        return False
    fases = valor["fases_ms"]
    return (isinstance(fases, dict) and set(fases) == set(FASES)
            and all(_nao_negativo(fases[fase]) for fase in FASES)
            and all(_numero_positivo(valor[chave]) for chave in RSS))


# Campos técnicos do resultado.json (job.py), conferidos em ordem; o primeiro que falhar vira o motivo.
_CAMPOS = (
    ("entrada", _texto),
    ("propriedades", lambda v: isinstance(v, dict)),
    ("crs_saida", lambda v: isinstance(v, str) and v.startswith("EPSG:") and v[5:].isdigit()),
    ("area_ha", _nao_negativo),
    ("perimetro_m", _nao_negativo),
    ("vertices", lambda v: isinstance(v, list) and len(v) >= 3 and all(map(_vertice, v))),
    ("metricas", _metricas),
)


def _recusar_constante(nome: str) -> None:
    raise ValueError(nome)  # NaN e Infinity não são JSON e o run_job nunca os grava


def _conferir_json(conteudo: bytes, job_id: str) -> str | None:
    try:
        dados = json.loads(conteudo.decode("utf-8"), parse_constant=_recusar_constante)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return "json_invalido"
    if not isinstance(dados, dict):
        return "json_nao_objeto"
    if dados.get("job_id") != job_id:
        return "job_id_divergente"
    versao = dados.get("versao_esquema")
    if type(versao) is not int or versao != VERSAO_ESQUEMA:
        return "versao_esquema_invalida"
    if dados.get("pdf_memorial") != "memorial.pdf":
        return "pdf_memorial_invalido"
    for campo, valido in _CAMPOS:
        if campo not in dados or not valido(dados[campo]):
            return f"campo_invalido:{campo}"
    return None


def _conferir_arquivo(caminho: Path, nome: str, job_id: str) -> str | None:
    """Abre uma vez sem seguir link e confere o próprio descritor; None = válido."""
    try:
        fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "ausente"
    except OSError:
        # O_NOFOLLOW num link dá ELOOP; o lstat separa link de outro erro sem seguir nada.
        try:
            return "link_recusado" if stat.S_ISLNK(os.lstat(caminho).st_mode) else "ilegivel"
        except OSError:
            return "ilegivel"
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        return "ilegivel"
    if not stat.S_ISREG(info.st_mode):  # antes do fdopen, que recusa pasta
        os.close(fd)
        return "nao_e_arquivo_regular"
    with os.fdopen(fd, "rb") as arquivo:
        if info.st_nlink != 1:
            return "hard_link_recusado"
        if info.st_size == 0:
            return "vazio"
        if info.st_size > TAMANHO_MAXIMO:
            return "grande_demais"
        try:
            if nome == "resultado.json":
                return _conferir_json(arquivo.read(TAMANHO_MAXIMO + 1), job_id)
            if not arquivo.read(5).startswith(b"%PDF-"):
                return "sem_cabecalho_pdf"
            arquivo.seek(max(0, info.st_size - FIM_DO_PDF))
            return None if b"%%EOF" in arquivo.read(FIM_DO_PDF) else "pdf_truncado"
        except OSError:
            return "ilegivel"


def conferir_artefatos(output_dir: Path, job_id: object) -> dict[str, object]:
    """{"ok": bool, "motivos": {nome: código}}; ok só com os 3 artefatos válidos. Nunca levanta por causa do disco.

    `motivos` traz só o que falhou: "job_id" (fora do formato, o disco nem é lido), "pasta" (a do job ausente,
    link ou não pasta) ou cada artefato. Diretamente em output_dir/job_id/, sem seguir link.
    """
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        return {"ok": False, "motivos": {"job_id": "job_id_invalido"}}
    pasta = Path(output_dir) / job_id
    try:
        modo = os.lstat(pasta).st_mode
    except FileNotFoundError:
        return {"ok": False, "motivos": {"pasta": "ausente"}}
    except OSError:
        return {"ok": False, "motivos": {"pasta": "ilegivel"}}
    if stat.S_ISLNK(modo):
        return {"ok": False, "motivos": {"pasta": "link_recusado"}}
    if not stat.S_ISDIR(modo):
        return {"ok": False, "motivos": {"pasta": "nao_e_pasta"}}
    motivos = {}
    for nome in ARTEFATOS:
        motivo = _conferir_arquivo(pasta / nome, nome, job_id)
        if motivo is not None:
            motivos[nome] = motivo
    return {"ok": not motivos, "motivos": motivos}
