"""Benchmark do worker: inicializa o QGIS uma vez e executa N jobs quentes.

Uso: python3 scripts/benchmark.py --input <geojson> --runs 20 --output /saida
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

META_MS = 60_000


def summarize(values: list[float]) -> dict[str, float]:
    ordenados = sorted(values)
    p95 = ordenados[math.ceil(0.95 * len(ordenados)) - 1]  # nearest-rank
    return {"min": ordenados[0], "mediana": statistics.median(ordenados), "p95": p95, "max": ordenados[-1]}


def _mem_total_mb() -> float:
    for linha in Path("/proc/meminfo").read_text().splitlines():
        if linha.startswith("MemTotal:"):
            return round(int(linha.split()[1]) / 1024, 1)
    return 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    inicio = time.perf_counter()
    from qgis.core import Qgis

    from geolume_worker.job import run_job
    from geolume_worker.metrics import cgroup_memory_peak_mb, peak_rss_mb
    from geolume_worker.qgis_session import qgis_session

    with qgis_session():
        init_ms = round((time.perf_counter() - inicio) * 1000, 1)
        resultados = [run_job(args.input, args.output / "jobs") for _ in range(args.runs)]

        fases = {fase: summarize([r.phases_ms[fase] for r in resultados]) for fase in resultados[0].phases_ms}
        relatorio = {
            "versao_qgis": Qgis.version(),
            "cpus": os.cpu_count(),
            "mem_total_mb": _mem_total_mb(),
            "runs": args.runs,
            "inicializacao_qgis_ms": init_ms,
            "fases_ms": fases,
            "rss_por_job_mb": [r.rss_after_mb for r in resultados],
            "pico_rss_mb": round(peak_rss_mb(), 1),
            "cgroup_pico_mb": cgroup_memory_peak_mb(),
            "meta_60s_atingida": fases["total_job"]["p95"] + init_ms < META_MS,
        }

    args.output.mkdir(parents=True, exist_ok=True)
    destino = args.output / f"benchmark-{datetime.now():%Y%m%d-%H%M%S}.json"
    destino.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"QGIS {relatorio['versao_qgis']} | {relatorio['cpus']} CPUs | {relatorio['mem_total_mb']} MB RAM")
    print(f"inicializacao_qgis: {init_ms} ms")
    print(f"{'fase':<16}{'min':>10}{'mediana':>10}{'p95':>10}{'max':>10}  (ms)")
    for fase, r in fases.items():
        print(f"{fase:<16}{r['min']:>10}{r['mediana']:>10}{r['p95']:>10}{r['max']:>10}")
    print(f"pico RSS processo: {relatorio['pico_rss_mb']} MB | pico cgroup: {relatorio['cgroup_pico_mb']} MB")
    print(f"meta 60 s (init + p95 total_job): {'ATINGIDA' if relatorio['meta_60s_atingida'] else 'NAO atingida'}")
    print(f"relatorio: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
