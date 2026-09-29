import pytest

from espbench.device import DeviceError
from espbench.gate import flatten
from espbench.latency import _percentile, run
from espbench.memory import run as memory_run
from espbench.report import to_markdown
from espbench.simulation import FakeDevice


class _DeadDevice:
    def ping(self):
        raise DeviceError("timeout")

    def stats(self):
        raise DeviceError("timeout")


class _SeriesDevice:
    def __init__(self, series):
        self.series = list(series)

    def stats(self):
        return self.series.pop(0)


def test_percentile_edges():
    assert _percentile([7.0], 50) == 7.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert _percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0


def test_latency_run_reports_percentiles():
    result = run(FakeDevice(latency_ms=10.0, jitter_ms=1.0, seed=3), n=40, warmup=5)
    assert result["n"] == 40
    assert result["errors"] == 0
    assert result["unit"] == "ms"
    assert 0 < result["p50"] <= result["p95"] <= result["p99"] <= result["max"]
    assert result["mean"] > 0


def test_latency_run_all_errors_fails():
    result = run(_DeadDevice(), n=5, warmup=1)
    assert result["failed"] is True
    assert result["errors"] == 6
    assert result["n"] == 0


def test_memory_detects_leak_from_trend():
    series = [
        {"uptime_s": i, "mem_free": 100_000 - 5_000 * i, "mem_alloc": 90_000}
        for i in range(6)
    ]
    result = memory_run(_SeriesDevice(series), samples=6, interval=0, drop_threshold=1000)
    assert result["leak_detected"] is True
    assert result["bytes_per_s"] < 0
    assert result["drop_bytes"] == 25_000


def test_memory_stable_device_no_leak():
    series = [
        {"uptime_s": i, "mem_free": 100_000, "mem_alloc": 90_000} for i in range(5)
    ]
    result = memory_run(_SeriesDevice(series), samples=5, interval=0, drop_threshold=1000)
    assert result["leak_detected"] is False
    assert result["drop_bytes"] == 0


def test_memory_flags_restart():
    series = [
        {"uptime_s": 30, "mem_free": 90_000},
        {"uptime_s": 31, "mem_free": 90_000},
        {"uptime_s": 1, "mem_free": 100_000},
        {"uptime_s": 2, "mem_free": 100_000},
    ]
    result = memory_run(_SeriesDevice(series), samples=4, interval=0)
    assert result["device_restarted"] is True
    assert result["leak_detected"] is False


def test_memory_too_few_samples_fails():
    result = memory_run(_DeadDevice(), samples=3, interval=0)
    assert result["failed"] is True
    assert result["samples"] == 0
    assert result["errors"] == 3


def test_memory_null_mem_free_counts_as_error():
    class _NullDevice:
        def stats(self):
            return {"uptime_s": 1, "mem_free": None}

    result = memory_run(_NullDevice(), samples=3, interval=0)
    assert result["failed"] is True
    assert result["samples"] == 0
    assert result["errors"] == 3


@pytest.mark.parametrize("n", [1, 7])
def test_latency_small_n(n):
    result = run(FakeDevice(seed=1), n=n, warmup=0)
    assert result["n"] == n


def test_latency_keeps_raw_samples():
    result = run(FakeDevice(latency_ms=10.0, jitter_ms=1.0, seed=3),
                 n=12, warmup=0)
    assert len(result["_samples"]) == 12
    assert all(isinstance(value, float) for value in result["_samples"])


def test_latency_samples_hidden_from_markdown_and_gate():
    result = run(FakeDevice(seed=3), n=6, warmup=0)
    markdown = to_markdown({"Suites": {"latency": result}})
    assert "_samples" not in markdown
    assert "Samples" not in markdown
    assert "suites.latency._samples" not in flatten(
        {"suites": {"latency": result}})
