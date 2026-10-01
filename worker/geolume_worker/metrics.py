"""Medição de tempo por fase e de memória (as funções de memória exigem Linux)."""

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_CGROUP_PEAK = Path("/sys/fs/cgroup/memory.peak")


class PhaseTimer:
    def __init__(self) -> None:
        self._ms: dict[str, float] = {}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        inicio = time.perf_counter()
        try:
            yield
        finally:
            self._ms[name] = self._ms.get(name, 0.0) + (time.perf_counter() - inicio) * 1000

    @property
    def phases_ms(self) -> dict[str, float]:
        return {nome: round(ms, 1) for nome, ms in self._ms.items()}


def peak_rss_mb() -> float:
    import resource

    # Linux informa ru_maxrss em KB; é o pico da vida inteira do processo.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def current_rss_mb() -> float:
    for linha in Path("/proc/self/status").read_text().splitlines():
        if linha.startswith("VmRSS:"):
            return int(linha.split()[1]) / 1024
    raise RuntimeError("VmRSS ausente em /proc/self/status")


def cgroup_memory_peak_mb() -> float | None:
    if not _CGROUP_PEAK.exists():
        return None
    return int(_CGROUP_PEAK.read_text().strip()) / (1024 * 1024)
