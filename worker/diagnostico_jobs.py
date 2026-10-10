"""Diagnóstico manual de jobs presos: só lê banco e disco e imprime JSON. Nunca altera nem recupera.

Uso (no contêiner api): python3 diagnostico_jobs.py [--job-id ID ...] [--tenant T] [--agora ISO] [--saida DIR]
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from psycopg2.extras import RealDictCursor

import db
from geolume_worker.prancha import JOB_ID, caminho_logo
from jobs_presos import LIMITE_EXECUCAO, LIMITE_SEM_TAREFA, motivo_job_preso
from integridade import ARTEFATOS, conferir_artefatos

CATEGORIAS = ("artefatos_presentes", "candidato_recuperacao", "nao_encontrado", "nao_expirado", "nao_recuperavel")
# Só o necessário para decidir: sem owner_id, prancha, erro nem caminhos na saída.
_COLUNAS = "id, status, task_id, tenant_id, created_at, started_at, input_path"
_MARCO = {"queued": ("created_at", LIMITE_SEM_TAREFA), "started": ("started_at", LIMITE_EXECUCAO)}


def _conexao_leitura():
    conn = db.connect()
    conn.set_session(readonly=True)
    return conn


def ler_jobs(job_ids: list[str] | None = None, tenant: str | None = None) -> list[dict[str, object]]:
    """Sem job_ids, só queued e started. Leitura global: fica fora de db.py, que a API importa."""
    filtros, params = [], []
    if job_ids is None:
        filtros.append("status IN ('queued', 'started')")
    else:
        filtros.append("id = ANY(%s)")
        params.append(list(job_ids))
    if tenant is not None:
        filtros.append("tenant_id = %s")
        params.append(tenant)
    with _conexao_leitura() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT {_COLUNAS} FROM jobs WHERE {' AND '.join(filtros)}", tuple(params))
        return [dict(row) for row in cur.fetchall()]


def _geojson_do_job(job: dict, output_dir: Path) -> bool:
    """Mesma regra da limpeza da recuperação: só inputs/{job_id}-* conta como upload do job."""
    raw = job.get("input_path")
    if not isinstance(raw, str) or not raw:
        return False
    entrada = Path(raw)
    inputs = (Path(output_dir) / "inputs").resolve()
    return (entrada.parent.resolve() == inputs and entrada.name.startswith(f"{job['id']}-")
            and entrada.is_file())


def _artefatos(output_dir: Path, job_id: str) -> dict[str, object]:
    """"completos" só com os 3 íntegros (integridade.py); os 3 presentes mas algum inválido é "invalidos"."""
    presentes = {nome: (Path(output_dir) / job_id / nome).is_file() for nome in ARTEFATOS}
    conferencia = conferir_artefatos(output_dir, job_id)
    quantos = sum(presentes.values())
    if conferencia["ok"]:
        estado = "completos"
    elif quantos == len(ARTEFATOS):
        estado = "invalidos"
    else:
        estado = "parciais" if quantos else "ausentes"
    return {"estado": estado, **presentes, "motivos": conferencia["motivos"]}


def _classificar(job: dict, motivo: str | None, artefatos: dict) -> tuple[str, str]:
    status = job.get("status")
    if status not in _MARCO:
        return "nao_recuperavel", f"status_{status}"
    if motivo == "execucao_expirada" and artefatos["estado"] == "completos":
        return "artefatos_presentes", motivo  # revisão manual, como na recuperação
    if motivo is not None:
        return "candidato_recuperacao", motivo
    if status == "queued" and job.get("task_id"):
        return "nao_expirado", "queued_com_task_id"
    if status == "started" and job.get("started_at") is None:
        return "nao_expirado", "started_sem_started_at"
    return "nao_expirado", "dentro_do_limite"


def diagnosticar(job_id: str, job: dict | None, agora: datetime, output_dir: Path) -> dict[str, object]:
    if agora.tzinfo is None:
        raise ValueError("agora sem fuso horário")
    if job is None:
        return {"id": job_id, "categoria": "nao_encontrado"}
    motivo = motivo_job_preso(job, agora)
    artefatos = _artefatos(output_dir, job_id)
    categoria, razao = _classificar(job, motivo, artefatos)
    marco, limite = _MARCO.get(job.get("status"), (None, None))
    desde = job.get(marco) if marco else None
    if not isinstance(desde, datetime):
        marco = None
    return {
        "id": job_id,
        "status": job.get("status"),
        "tenant_id": job.get("tenant_id"),
        "tem_task_id": bool(job.get("task_id")),
        "categoria": categoria,
        "motivo": razao,
        "marco": marco,
        "idade_segundos": int((agora - desde).total_seconds()) if marco else None,
        "limite_segundos": limite.total_seconds() if limite else None,
        "artefatos": artefatos,
        "uploads": {
            "geojson": _geojson_do_job(job, output_dir),
            "logo": bool(JOB_ID.fullmatch(job_id)) and caminho_logo(output_dir, job_id).is_file(),
        },
    }


def relatorio(agora: datetime, output_dir: Path, job_ids: list[str] | None = None,
              tenant: str | None = None) -> dict[str, object]:
    lidos = {str(job["id"]): job for job in ler_jobs(job_ids, tenant)}
    ids = sorted(set(job_ids) if job_ids is not None else lidos)
    jobs = [diagnosticar(job_id, lidos.get(job_id), agora, output_dir) for job_id in ids]
    contagem = Counter(job["categoria"] for job in jobs)
    return {
        "agora": agora.isoformat(),
        "somente_leitura": True,
        "tenant": tenant,
        "resumo": {categoria: contagem[categoria] for categoria in CATEGORIAS},
        "jobs": jobs,
    }


def _agora(valor: str) -> datetime:
    data = datetime.fromisoformat(valor)
    if data.tzinfo is None:
        raise argparse.ArgumentTypeError("--agora precisa de fuso horário (ex.: 2026-10-08T12:00:00+00:00)")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico somente leitura de jobs presos (não altera nada).")
    parser.add_argument("--job-id", action="append", dest="job_ids", help="restringe a este job (repetível)")
    parser.add_argument("--tenant", help="só jobs deste tenant")
    parser.add_argument("--agora", type=_agora, default=None, help="instante da decisão, ISO com fuso")
    parser.add_argument("--saida", type=Path, default=Path("/saida"), help="diretório de saída dos jobs")
    args = parser.parse_args(argv)
    agora = args.agora or datetime.now(timezone.utc)
    rel = relatorio(agora, args.saida, args.job_ids, args.tenant)
    sys.stdout.write(json.dumps(rel, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
