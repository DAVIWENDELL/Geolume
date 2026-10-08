"""CLI manual de recuperação de um job expirado. Sem --apply, só diagnostica (somente leitura).

Uso (no contêiner api):
  python3 recuperar_job.py [--job-id ID ...] [--tenant T] [--agora ISO]       diagnóstico
  python3 recuperar_job.py --apply --job-id ID --confirm-job-id ID [--tenant T] recupera um job

A recuperação é a de `recuperacao.recuperar_job_expirado`: relê o job, aplica a regra e só marca
failed pelo UPDATE condicional; só quem venceu limpa os uploads do próprio job. Nunca apaga PDFs.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import diagnostico_jobs
import recuperacao

# 0: diagnóstico ou job recuperado; 1: recuperação recusada pela regra/banco; 2: pedido inválido.
_RECUSADO, _INVALIDO = 1, 2


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnóstico de jobs presos e recuperação manual de um job.")
    parser.add_argument("--job-id", action="append", dest="job_ids", help="job a diagnosticar (repetível)")
    parser.add_argument("--tenant", help="só jobs deste tenant")
    parser.add_argument("--agora", type=diagnostico_jobs._agora, default=None,
                        help="instante do diagnóstico, ISO com fuso (não vale com --apply)")
    parser.add_argument("--saida", type=Path, default=Path("/saida"), help="diretório de saída dos jobs")
    parser.add_argument("--apply", action="store_true", help="recupera o job (exige um --job-id e confirmação)")
    parser.add_argument("--confirm-job-id", help="repita o id do job para confirmar a recuperação")
    return parser


def _recusa(modo: str, resultado: str, detalhe: str) -> tuple[int, dict[str, object]]:
    return _INVALIDO, {"modo": modo, "resultado": resultado, "detalhe": detalhe}


def _diagnostico_de(job_id: str, agora: datetime, args: argparse.Namespace) -> dict[str, object]:
    return diagnostico_jobs.relatorio(agora, args.saida, [job_id], args.tenant)["jobs"][0]


def executar(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.apply:
        if args.confirm_job_id is not None:
            return _recusa("diagnostico", "argumento_invalido", "--confirm-job-id só vale com --apply")
        rel = diagnostico_jobs.relatorio(args.agora or _agora(), args.saida, args.job_ids, args.tenant)
        return 0, {"modo": "diagnostico", "somente_leitura": True, "diagnostico": rel}

    if not args.job_ids or len(args.job_ids) != 1:
        return _recusa("aplicar", "argumento_invalido", "--apply exige exatamente um --job-id")
    if args.agora is not None:
        # A recuperação decide pelo relógio real: um --agora no futuro forjaria a expiração.
        return _recusa("aplicar", "argumento_invalido", "--agora não vale com --apply")
    (job_id,) = args.job_ids
    if args.confirm_job_id != job_id:
        return _recusa("aplicar", "confirmacao_invalida", "--confirm-job-id precisa repetir o --job-id")

    agora = _agora()
    antes = _diagnostico_de(job_id, agora, args)
    if antes["categoria"] == "nao_encontrado":
        resultado = "nao_encontrado"  # inexistente ou de outro tenant: a recuperação nem é chamada
    else:
        resultado = recuperacao.recuperar_job_expirado(job_id, agora, args.saida)
    saida: dict[str, object] = {"modo": "aplicar", "job_id": job_id, "tenant": args.tenant,
                                "resultado": resultado, "antes": antes}
    if resultado == "nao_encontrado":
        saida["depois"] = antes
    elif resultado == "marcado_failed":
        # A recuperação já gravou: uma falha na releitura não pode esconder isso nem repetir a escrita.
        try:
            saida["depois"] = _diagnostico_de(job_id, agora, args)
        except Exception as exc:  # noqa: BLE001 - só o tipo vai para a saída, sem mensagem nem traceback
            saida["depois"] = None
            saida["erro_diagnostico"] = f"falha ao ler o job depois da recuperação ({type(exc).__name__})"
    else:
        saida["depois"] = _diagnostico_de(job_id, agora, args)
    return (0 if resultado == "marcado_failed" else _RECUSADO), saida


def main(argv: list[str] | None = None) -> int:
    codigo, saida = executar(_parser().parse_args(argv))
    sys.stdout.write(json.dumps(saida, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
