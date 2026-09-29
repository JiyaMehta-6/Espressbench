import json

from espbench.cli import main
from espbench.insight import hints
from espbench.svg import histogram_svg


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_hints_clean_report_is_empty():
    assert hints({}) == []
    assert hints([]) == []
    assert hints({"suites": {"latency": {"n": 10, "errors": 0,
                                         "p50": 10.0, "p95": 12.0}}}) == []


def test_hints_latency_errors_and_tail():
    got = hints({"suites": {"latency": {"n": 10, "errors": 3,
                                        "p50": 10.0, "p95": 40.0}}})
    assert any("dropped 3 pings" in line for line in got)
    assert any("4x p50" in line for line in got)
    assert any("40 ms" in line for line in got)


def test_hints_memory_fuzz_and_generic_failure():
    got = hints({"suites": {
        "memory": {"leak_detected": True, "failed": True},
        "fuzz": {"reboots": 1, "errors": 0, "passed": False},
        "custom": {"failed": True},
    }})
    assert any("memory leak" in line for line in got)
    assert any("rebooted 1x" in line for line in got)
    assert any("suite custom failed" in line for line in got)
    assert not any("suite memory failed" in line for line in got)
    assert not any("suite fuzz failed" in line for line in got)


def test_hints_fuzz_errors_without_reboots():
    got = hints({"suites": {"fuzz": {"errors": 2, "reboots": 0,
                                     "passed": False}}})
    assert any("2 fuzz payloads" in line for line in got)


def test_hints_memory_restart():
    got = hints({"suites": {"memory": {"leak_detected": False,
                                       "device_restarted": True}}})
    assert any("restarted" in line for line in got)


def test_hints_chaos_report():
    got = hints({"chaos": {"total": 4, "recovered": 3, "all_recovered": False,
                           "all_faults_effective": False}})
    assert any("1 of 4 faults" in line for line in got)
    assert any("no effect" in line for line in got)
    clean = hints({"chaos": {"total": 2, "recovered": 2, "all_recovered": True,
                             "all_faults_effective": True}})
    assert clean == []


def test_hints_soak_report():
    got = hints({"soak": {"failures": 2, "errors": 1, "reboots": 1}})
    assert any("rebooted 1x during soak" in line for line in got)
    assert any("1 device errors" in line for line in got)
    assert not any("iterations failed" in line for line in got)
    suite_only = hints({"soak": {"failures": 2, "errors": 0, "reboots": 0}})
    assert any("2 soak iterations failed" in line for line in suite_only)


def test_hints_device_error_and_sensor():
    error = hints({"device": {"error": "connection refused"}})
    assert any("unreachable" in line for line in error)
    sensor = hints({"device": {"sensor": "stuck"}})
    assert any("sensor" in line for line in sensor)


def test_histogram_svg_renders_title_and_bars():
    svg = histogram_svg([1, 2, 2, 3, 3, 3, 4], title="latency <ms>", unit="ms")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "latency &lt;ms&gt;" in svg
    assert svg.count("<rect") == 4
    assert 'fill="#2f6fed"' in svg
    assert "ms (n=7)" in svg


def test_histogram_svg_empty_state():
    svg = histogram_svg([], title="nothing")
    assert "no data" in svg
    assert "<rect" not in svg


def test_histogram_svg_single_and_constant_values():
    single = histogram_svg([5.0], title="one")
    assert single.count("<rect") == 1
    flat = histogram_svg([2, 2, 2], title="flat")
    assert flat.count("<rect") == 1


def test_histogram_svg_skips_non_finite_values():
    svg = histogram_svg([1, "x", None, float("nan"), True, 2], title="t")
    assert "n=2" in svg


def test_chart_writes_svg_for_latency_report(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(["run", "--sim", "--suites", "latency", "--latency-n", "6",
                 "--out", str(out)]) == 0
    capsys.readouterr()
    target = tmp_path / "latency.svg"
    assert main(["chart", str(out / "report.json"), "--out", str(target),
                 "--title", "sim latency", "--unit", "ms"]) == 0
    svg = target.read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "sim latency" in svg
    assert "wrote" in capsys.readouterr().out


def test_chart_default_out_is_next_to_report(tmp_path):
    out = tmp_path / "run"
    assert main(["run", "--sim", "--suites", "latency", "--latency-n", "4",
                 "--out", str(out)]) == 0
    assert main(["chart", str(out / "report.json")]) == 0
    assert (out / "report.svg").exists()


def test_chart_falls_back_to_repeat_values(tmp_path):
    report = _write(tmp_path, "r.json", {
        "repeat": {"count": 3, "metrics": [
            {"metric": "suites.latency.p95", "values": [10.0, 11.0, 12.0]}]}})
    target = tmp_path / "r.svg"
    assert main(["chart", str(report), "--out", str(target)]) == 0
    assert "repeat suites.latency.p95 values" in target.read_text(encoding="utf-8")


def test_chart_without_samples_exits_two(tmp_path, capsys):
    report = _write(tmp_path, "r.json", {"suites": {"fuzz": {"total": 12}}})
    assert main(["chart", str(report)]) == 2
    assert "no sample values" in capsys.readouterr().err


def test_insight_prints_hints(tmp_path, capsys):
    report = _write(tmp_path, "r.json",
                    {"suites": {"memory": {"leak_detected": True}}})
    assert main(["insight", str(report)]) == 0
    out = capsys.readouterr().out
    assert "Insights" in out
    assert "memory leak" in out


def test_insight_clean_report(tmp_path, capsys):
    report = _write(tmp_path, "r.json",
                    {"suites": {"latency": {"n": 5, "errors": 0}}})
    assert main(["insight", str(report)]) == 0
    assert "no hints" in capsys.readouterr().out


def test_insight_bad_input_exits_two(tmp_path, capsys):
    assert main(["insight", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert main(["insight", str(bad)]) == 2


def test_run_dead_host_prints_insights(capsys):
    code = main(["run", "--host", "127.0.0.1", "--port", "1",
                 "--suites", "latency", "--latency-n", "2"])
    assert code == 1
    out = capsys.readouterr().out
    assert "Insights" in out
    assert "unreachable" in out


def test_hint_flags_zero_p50_with_tail():
    got = hints({"suites": {"latency": {"p50": 0, "p95": 40.0, "errors": 0}}})
    assert any("tail latency" in line for line in got)
    assert hints({"suites": {"latency": {"p50": 0, "p95": 0}}}) == []
