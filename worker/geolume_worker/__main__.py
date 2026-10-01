"""CLI: python3 -m geolume_worker run --input <geojson> --output <dir> [--job-id <id>]

Códigos de saída: 0 sucesso, 2 entrada inválida, 1 erro inesperado.
"""

import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path

from geolume_worker.errors import InvalidInputError
from geolume_worker.metrics import PhaseTimer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geolume_worker")
    sub = parser.add_subparsers(dest="comando", required=True)
    run = sub.add_parser("run", help="gera mapa.pdf e resultado.json para um GeoJSON")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--job-id")
    args = parser.parse_args(argv)

    timer = PhaseTimer()
    with ExitStack() as stack:
        with timer.phase("inicializacao_qgis"):
            from geolume_worker.job import run_job
            from geolume_worker.qgis_session import qgis_session

            stack.enter_context(qgis_session())
        try:
            result = run_job(args.input, args.output, job_id=args.job_id)
        except InvalidInputError as exc:
            erro = {"status": "erro", "codigo": exc.codigo, "mensagem": exc.mensagem}
            print(json.dumps(erro, ensure_ascii=False), file=sys.stderr)
            return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "job_id": result.job_id,
                "pdf": str(result.pdf_path),
                "json": str(result.json_path),
                "memorial": str(result.memorial_path),
                "fases_ms": {**timer.phases_ms, **result.phases_ms},
                "pico_rss_mb": result.peak_rss_mb,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
