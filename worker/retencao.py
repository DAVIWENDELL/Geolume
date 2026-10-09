"""Plano de retenção de arquivos (dry-run): só lê banco e disco e imprime JSON. Nunca remove nada.

Diz, por job, o que seria mantido e o que poderia ser limpo. Não existe modo que aplique o plano:
a exclusão de verdade exige uma política aprovada e uma fatia própria.

Uso (no contêiner api): python3 retencao.py [--job-id ID ...] [--tenant T] [--agora ISO] [--saida DIR]
"""

import argparse
import json
import os
import stat
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg2.extras import RealDictCursor

from diagnostico_jobs import _conexao_leitura
from geolume_worker.prancha import JOB_ID, caminho_logo
from recuperacao import ARTEFATOS

# Contados a partir de completed_at (gravado em toda conclusão e toda falha). Provisórios até decisão
# de produto: o histórico do cliente mostra os downloads e a entrada do job concluído.
RETENCAO_COMPLETED = timedelta(days=90)  # depois disso, só candidato a retenção (nada é limpo nesta fatia)
RETENCAO_FAILED = timedelta(days=30)  # depois disso, os uploads do job falho podem ser limpos

DECISOES = ("candidato_limpeza", "candidato_retencao", "manter", "nao_encontrado")
ARQUIVOS = ("geojson", "logo", *ARTEFATOS)
# Só o necessário para decidir: sem owner_id, prancha, erro nem caminhos na saída.
_COLUNAS = "id, status, tenant_id, input_filename, input_path, completed_at"
_LIMITE = {"completed": RETENCAO_COMPLETED, "failed": RETENCAO_FAILED}
# Ação de cada arquivo presente e verificável, por motivo do job.
_ACAO_UPLOAD = {
    "completed_recente": ("manter", None),
    "completed_expirado": ("candidato_retencao", "completed_expirado"),
    "failed_recente": ("manter", "diagnostico"),
    "failed_expirado": ("candidato_limpeza", "upload_de_job_falho"),
}
_ACAO_ARTEFATO = {
    "completed_recente": ("manter", "download"),
    "completed_expirado": ("candidato_retencao", "completed_expirado"),
    "failed_recente": ("manter", "artefato_parcial"),
    "failed_expirado": ("manter", "artefato_parcial"),  # preservados para diagnóstico
}
_MOTIVO_UPLOAD_RECENTE = {"geojson": "entrada_do_historico", "logo": "logo_do_mapa"}


def ler_jobs(job_ids: list[str] | None = None, tenant: str | None = None) -> list[dict[str, object]]:
    """Leitura global em sessão somente leitura: fica fora de db.py, que a API importa."""
    filtros, params = [], []
    if job_ids is not None:
        filtros.append("id = ANY(%s)")
        params.append(list(job_ids))
    if tenant is not None:
        filtros.append("tenant_id = %s")
        params.append(tenant)
    onde = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    with _conexao_leitura() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT {_COLUNAS} FROM jobs{onde}", tuple(params))
        return [dict(row) for row in cur.fetchall()]


def _pasta_bloqueada(pasta: Path) -> str | None:
    """Pasta que é link, ou que não dá para conferir, bloqueia tudo que está nela (is_symlink engole o erro)."""
    try:
        info = os.lstat(pasta)
    except FileNotFoundError:
        return None
    except OSError:
        return "nao_verificavel"
    return "link_recusado" if stat.S_ISLNK(info.st_mode) else None


def _raiz_bloqueada(output_dir: Path) -> str | None:
    """output_dir e cada pasta acima dele, sem normalizar `..`: qualquer link ou lstat falho bloqueia o job inteiro."""
    caminho = Path(output_dir).absolute()
    for pasta in (*reversed(caminho.parents), caminho):
        if bloqueio := _pasta_bloqueada(pasta):
            return bloqueio
    return None


def _arquivo(caminho: Path, pasta: Path) -> tuple[bool | None, str | None]:
    """(presente, bloqueio) sem seguir links; presente None = não verificável. Link, não regular e hard link bloqueiam."""
    bloqueio = _pasta_bloqueada(pasta)
    if bloqueio:
        return (os.path.lexists(caminho) if bloqueio == "link_recusado" else None), bloqueio
    try:
        info = os.lstat(caminho)
    except FileNotFoundError:
        return False, None
    except OSError:
        return None, "nao_verificavel"
    if stat.S_ISLNK(info.st_mode):
        return True, "link_recusado"
    if not stat.S_ISREG(info.st_mode):
        return True, "nao_e_arquivo_regular"
    if info.st_nlink != 1:
        return True, "hard_link_recusado"
    return True, None


def _geojson(job: dict, output_dir: Path) -> tuple[bool | None, str | None]:
    """Só inputs/{job_id}-{input_filename}.geojson direto na pasta real de entradas conta como do job."""
    raw, nome = job.get("input_path"), job.get("input_filename")
    if not isinstance(raw, str) or not raw:
        return False, None
    entrada, inputs = Path(raw), Path(output_dir) / "inputs"
    bloqueio = _pasta_bloqueada(inputs)
    if bloqueio:
        return (os.path.lexists(entrada) if bloqueio == "link_recusado" else None), bloqueio
    esperado = f"{job['id']}-{nome}" if isinstance(nome, str) else None
    try:
        dentro = entrada.parent.resolve() == inputs.resolve()
    except (OSError, RuntimeError):
        dentro = False
    if not dentro or entrada.name != esperado or entrada.suffix.lower() != ".geojson":
        return os.path.lexists(entrada), "fora_da_pasta_permitida"
    return _arquivo(inputs / entrada.name, inputs)


def _idade(job: dict, agora: datetime) -> tuple[str, timedelta | None]:
    """Motivo do job e idade desde completed_at; na dúvida (sem data, sem fuso, futura), manter."""
    status = job.get("status")
    if status in ("queued", "started"):
        return f"{status}_protegido", None
    if status not in _LIMITE:
        return "status_desconhecido", None
    desde = job.get("completed_at")
    if not isinstance(desde, datetime):
        return "sem_marco", None
    if desde.tzinfo is None:
        return "data_sem_fuso", None
    idade = agora - desde
    if idade < timedelta(0):
        return "data_futura", idade
    return f"{status}_{'expirado' if idade > _LIMITE[status] else 'recente'}", idade


def planejar_job(job: dict, agora: datetime, output_dir: Path) -> dict[str, object]:
    if agora.tzinfo is None:
        raise ValueError("agora sem fuso horário")
    job_id = job.get("id")
    motivo, idade = _idade(job, agora)
    decisao = {"completed_expirado": "candidato_retencao", "failed_expirado": "candidato_limpeza"}.get(motivo, "manter")
    arquivos = {}
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        arquivos = {nome: {"presente": None, "acao": "manter", "motivo": "job_id_invalido"} for nome in ARQUIVOS}
    elif raiz := _raiz_bloqueada(output_dir):
        arquivos = {nome: {"presente": None, "acao": "manter", "motivo": raiz} for nome in ARQUIVOS}
        if raiz == "link_recusado":
            caminhos = {"geojson": job.get("input_path"), "logo": caminho_logo(output_dir, job_id),
                        **{nome: Path(output_dir) / job_id / nome for nome in ARTEFATOS}}
            for nome, caminho in caminhos.items():
                arquivos[nome]["presente"] = bool(caminho) and os.path.lexists(caminho)
    else:
        pasta = Path(output_dir) / job_id
        logo = caminho_logo(output_dir, job_id)
        estados = {"geojson": _geojson(job, output_dir), "logo": _arquivo(logo, logo.parent)}
        estados.update({nome: _arquivo(pasta / nome, pasta) for nome in ARTEFATOS})
        for nome, (presente, bloqueio) in estados.items():
            tabela = _ACAO_ARTEFATO if nome in ARTEFATOS else _ACAO_UPLOAD
            acao, razao = tabela.get(motivo, ("manter", motivo))
            if nome in _MOTIVO_UPLOAD_RECENTE and razao is None:
                razao = _MOTIVO_UPLOAD_RECENTE[nome]
            if not presente:
                acao, razao = "manter", bloqueio or "ausente"
            elif bloqueio:
                acao, razao = "manter", bloqueio
            arquivos[nome] = {"presente": presente, "acao": acao, "motivo": razao}
    marcado = idade is not None
    return {
        "id": job_id,
        "status": job.get("status"),
        "tenant_id": job.get("tenant_id"),
        "decisao": decisao,
        "motivo": motivo,
        "marco": "completed_at" if marcado else None,
        "idade_segundos": int(idade.total_seconds()) if marcado else None,
        "limite_segundos": _LIMITE[job["status"]].total_seconds() if marcado else None,
        "arquivos": arquivos,
        "arquivos_candidatos": [n for n in ARQUIVOS if arquivos[n]["acao"] == "candidato_limpeza"],
    }


def plano(agora: datetime, output_dir: Path, job_ids: list[str] | None = None,
          tenant: str | None = None) -> dict[str, object]:
    lidos = {str(job["id"]): job for job in ler_jobs(job_ids, tenant)}
    ids = sorted(set(job_ids) if job_ids is not None else lidos)
    jobs = [planejar_job(lidos[i], agora, output_dir) if i in lidos else {"id": i, "decisao": "nao_encontrado"}
            for i in ids]
    contagem = Counter(job["decisao"] for job in jobs)
    return {
        "agora": agora.isoformat(),
        "somente_leitura": True,
        "tenant": tenant,
        "periodos_dias": {"completed": RETENCAO_COMPLETED.days, "failed": RETENCAO_FAILED.days},
        "resumo": {decisao: contagem[decisao] for decisao in DECISOES},  # soma = total
        "total": len(jobs),
        "arquivos_candidatos": sum(len(job.get("arquivos_candidatos", ())) for job in jobs),
        "jobs": jobs,
    }


def _agora(valor: str) -> datetime:
    data = datetime.fromisoformat(valor)
    if data.tzinfo is None:
        raise argparse.ArgumentTypeError("--agora precisa de fuso horário (ex.: 2026-10-08T12:00:00+00:00)")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plano de retenção somente leitura (não remove nada).")
    parser.add_argument("--job-id", action="append", dest="job_ids", help="restringe a este job (repetível)")
    parser.add_argument("--tenant", help="só jobs deste tenant")
    parser.add_argument("--agora", type=_agora, default=None, help="instante da decisão, ISO com fuso")
    parser.add_argument("--saida", type=Path, default=Path("/saida"), help="diretório de saída dos jobs")
    args = parser.parse_args(argv)
    agora = args.agora or datetime.now(timezone.utc)
    rel = plano(agora, args.saida, args.job_ids, args.tenant)
    sys.stdout.write(json.dumps(rel, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
