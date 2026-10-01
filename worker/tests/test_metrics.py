import sys
import time

import pytest

from geolume_worker.metrics import (
    PhaseTimer,
    cgroup_memory_peak_mb,
    current_rss_mb,
    peak_rss_mb,
)

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="usa /proc e resource")


def test_phase_timer_registra_fase():
    timer = PhaseTimer()
    with timer.phase("a"):
        time.sleep(0.05)
    assert timer.phases_ms["a"] >= 45


def test_phase_timer_ordem_e_soma():
    timer = PhaseTimer()
    with timer.phase("a"):
        time.sleep(0.02)
    with timer.phase("b"):
        pass
    primeiro_a = timer.phases_ms["a"]
    with timer.phase("a"):
        time.sleep(0.02)
    assert list(timer.phases_ms) == ["a", "b"]
    assert timer.phases_ms["a"] > primeiro_a


def test_phase_timer_registra_mesmo_com_excecao():
    timer = PhaseTimer()
    with pytest.raises(ValueError):
        with timer.phase("x"):
            raise ValueError("falha")
    assert "x" in timer.phases_ms


@linux_only
def test_rss_positivo():
    assert current_rss_mb() > 0
    assert peak_rss_mb() > 0
    assert peak_rss_mb() >= current_rss_mb() * 0.9


@linux_only
def test_cgroup_peak_tipo():
    valor = cgroup_memory_peak_mb()
    assert valor is None or valor > 0
