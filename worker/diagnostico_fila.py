"""Diagnóstico de jobs queued com task_id: só lê banco e Redis e imprime JSON. Nunca altera nem recupera.

O broker só confirma presença: a mensagem está na fila (`celery` e as filas de prioridade) ou reservada
por um worker (`unacked`). Não localizar a mensagem não prova abandono; na dúvida, `nao_verificavel`.

Uso (no contêiner api): python3 diagnostico_fila.py [--job-id ID ...] [--tenant T] [--agora ISO]
"""

import argparse
import base64
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import redis
from psycopg2.extras import RealDictCursor

from diagnostico_jobs import _agora, _conexao_leitura
from jobs_presos import motivo_job_preso

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
# Fila padrão do Celery e as listas de prioridade do kombu (priority_steps 0, 3, 6, 9; separador \x06\x16).
FILAS = ("celery", *(f"celery\x06\x16{p}" for p in (3, 6, 9)))
RESERVAS = "unacked"  # mensagens entregues a um worker e ainda não confirmadas
# Formato textual de UUID em minúsculas, sem conferir a versão. O Celery gera str(uuid4()), que passa;
# qualquer outro formato não veio da API.
TASK_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Estados gravados no backend enquanto a task ainda pode estar rodando.
EM_EXECUCAO = {"STARTED", "RETRY", "RECEIVED"}
CATEGORIAS = ("fila_confirmada", "nao_aplicavel", "nao_encontrado", "nao_verificavel", "queued_sem_task_id",
              "task_id_invalido", "task_nao_localizada")
# Só o necessário para decidir: sem owner_id, prancha, erro nem caminhos.
_COLUNAS = "id, status, task_id, tenant_id, created_at"
_ERROS_REDIS = (redis.RedisError, OSError)


def ler_jobs(job_ids: list[str] | None = None, tenant: str | None = None) -> list[dict[str, object]]:
    """Sem job_ids, só queued. Leitura global em sessão somente leitura: fica fora de db.py, que a API importa."""
    filtros, params = [], []
    if job_ids is None:
        filtros.append("status = 'queued'")
    else:
        filtros.append("id = ANY(%s)")
        params.append(list(job_ids))
    if tenant is not None:
        filtros.append("tenant_id = %s")
        params.append(tenant)
    with _conexao_leitura() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT {_COLUNAS} FROM jobs WHERE {' AND '.join(filtros)}", tuple(params))
        return [dict(row) for row in cur.fetchall()]


def _cliente(url: str):
    return redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def _task_id_da_mensagem(bruto: bytes) -> str | None:
    """Id da task na mensagem do kombu (protocolo 2: headers.id; 1: id no corpo). None = ilegível."""
    try:
        envelope = json.loads(bruto)
        if isinstance(envelope, list):  # reserva: [mensagem, exchange, routing_key]
            envelope = envelope[0]
        task_id = (envelope.get("headers") or {}).get("id") or (envelope.get("properties") or {}).get("correlation_id")
        if not task_id and (envelope.get("properties") or {}).get("body_encoding") == "base64":
            task_id = json.loads(base64.b64decode(envelope["body"])).get("id")
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        return None
    return task_id if isinstance(task_id, str) and task_id else None


def ler_broker() -> dict[str, object] | None:
    """Fila e reservas num único MULTI/EXEC (fotografia consistente), só com LRANGE e HVALS. None = indisponível."""
    try:
        with _cliente(BROKER_URL).pipeline(transaction=True) as pipe:
            for chave in FILAS:
                pipe.lrange(chave, 0, -1)
            pipe.hvals(RESERVAS)
            *filas, reservas = pipe.execute()
    except _ERROS_REDIS:
        return None
    onde, ilegiveis = {}, 0
    for lugar, mensagens in [("reservada", reservas), *(("fila", m) for m in filas)]:
        for bruto in mensagens:
            task_id = _task_id_da_mensagem(bruto)
            if task_id is None:
                ilegiveis += 1
            else:
                onde.setdefault(task_id, lugar)
    return {"onde": onde, "ilegiveis": ilegiveis}


def ler_resultados(task_ids: list[str]) -> dict[str, str | None] | None:
    """Só o status gravado no backend (nunca resultado nem traceback). None = indisponível; "?" = ilegível."""
    if not task_ids:
        return {}
    try:
        with _cliente(RESULT_BACKEND).pipeline(transaction=False) as pipe:
            for task_id in task_ids:
                pipe.get(f"celery-task-meta-{task_id}")
            brutos = pipe.execute()
    except _ERROS_REDIS:
        return None
    estados = {}
    for task_id, bruto in zip(task_ids, brutos):
        try:
            estados[task_id] = None if bruto is None else str(json.loads(bruto)["status"])
        except (ValueError, TypeError, KeyError):
            estados[task_id] = "?"
    return estados


def _idade(job: dict, agora: datetime) -> int | None:
    criado = job.get("created_at")
    if not isinstance(criado, datetime) or criado.tzinfo is None:
        return None
    return int((agora - criado).total_seconds())


def _base(job: dict, agora: datetime, categoria: str, motivo: str, broker: dict | None = None) -> dict[str, object]:
    return {"id": job["id"], "status": job.get("status"), "tenant_id": job.get("tenant_id"), "categoria": categoria,
            "motivo": motivo, "idade_segundos": _idade(job, agora), "broker": broker}


def _verificavel(job: dict) -> bool:
    task_id = job.get("task_id")
    return job.get("status") == "queued" and isinstance(task_id, str) and bool(TASK_ID.fullmatch(task_id))


def classificar(job: dict, agora: datetime, broker: dict | None, resultados: dict | None) -> dict[str, object]:
    status, task_id = job.get("status"), job.get("task_id")
    if status != "queued":
        return _base(job, agora, "nao_aplicavel", f"status_{status}")
    if not task_id:
        return _base(job, agora, "queued_sem_task_id", motivo_job_preso(job, agora) or "dentro_do_limite")
    if not _verificavel(job):
        return _base(job, agora, "task_id_invalido", "formato_inesperado")
    if broker is None:
        return _base(job, agora, "nao_verificavel", "broker_indisponivel")
    lugar = broker["onde"].get(task_id)
    if lugar:
        motivo = "na_fila" if lugar == "fila" else "reservada_pelo_worker"
        return _base(job, agora, "fila_confirmada", motivo, {"onde": lugar, "estado_resultado": None})
    if broker["ilegiveis"]:
        return _base(job, agora, "nao_verificavel", "mensagem_ilegivel_no_broker")
    if resultados is None:
        return _base(job, agora, "nao_verificavel", "resultado_indisponivel")
    estado = resultados.get(task_id)
    if estado == "?":
        return _base(job, agora, "nao_verificavel", "resultado_ilegivel")
    if estado in EM_EXECUCAO:
        return _base(job, agora, "nao_verificavel", "task_em_execucao_no_backend")
    return _base(job, agora, "task_nao_localizada", "ausente_da_fila_e_das_reservas",
                 {"onde": None, "estado_resultado": estado})


def relatorio(agora: datetime, job_ids: list[str] | None = None, tenant: str | None = None) -> dict[str, object]:
    """Banco, depois broker, depois banco de novo: job que mudou no meio vira nao_verificavel."""
    if agora.tzinfo is None:
        raise ValueError("agora sem fuso horário")
    lidos = {str(job["id"]): job for job in ler_jobs(job_ids, tenant)}
    a_verificar = sorted(i for i, job in lidos.items() if _verificavel(job))
    broker = ler_broker() if a_verificar else None
    ausentes = [] if broker is None else [
        lidos[i]["task_id"] for i in a_verificar if lidos[i]["task_id"] not in broker["onde"]]
    resultados = ler_resultados(sorted(ausentes)) if broker is not None else None
    jobs = {i: classificar(job, agora, broker, resultados) for i, job in lidos.items()}
    afirmados = [i for i in a_verificar if jobs[i]["categoria"] in ("fila_confirmada", "task_nao_localizada")]
    if afirmados:
        relidos = {str(job["id"]): job for job in ler_jobs(afirmados, tenant)}
        for i in afirmados:
            agora_job = relidos.get(i)
            if (agora_job is None or agora_job.get("status") != "queued"
                    or agora_job.get("task_id") != lidos[i]["task_id"]):
                jobs[i] = _base(lidos[i], agora, "nao_verificavel", "estado_mudou_durante_a_consulta")
    ids = sorted(set(job_ids) if job_ids is not None else lidos)
    saida = [jobs.get(i) or {"id": i, "categoria": "nao_encontrado"} for i in ids]
    contagem = Counter(job["categoria"] for job in saida)
    return {
        "agora": agora.isoformat(),
        "somente_leitura": True,
        "tenant": tenant,
        "broker_disponivel": None if not a_verificar else broker is not None,
        "mensagens_ilegiveis": broker["ilegiveis"] if broker else None,
        "resumo": {categoria: contagem[categoria] for categoria in CATEGORIAS},
        "total": len(saida),
        "jobs": saida,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico somente leitura de jobs queued no broker.")
    parser.add_argument("--job-id", action="append", dest="job_ids", help="restringe a este job (repetível)")
    parser.add_argument("--tenant", help="só jobs deste tenant")
    parser.add_argument("--agora", type=_agora, default=None, help="instante do diagnóstico, ISO com fuso")
    args = parser.parse_args(argv)
    rel = relatorio(args.agora or datetime.now(timezone.utc), args.job_ids, args.tenant)
    sys.stdout.write(json.dumps(rel, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
