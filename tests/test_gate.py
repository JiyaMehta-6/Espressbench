import copy
import json

import pytest

from espbench import __version__
from espbench.cli import main
from espbench.gate import (
    aggregate_runs,
    check_budgets,
    compare_reports,
    derive_budgets,
    firmware_version,
    flatten,
    load_report,
    parse_budget,
    stamp_baseline,
)

REPORT = {
    "suites": {
        "latency": {"n": 100, "errors": 0, "mean": 10.0, "p95": 20.0, "unit": "ms"},
        "memory": {"samples": 25, "drop_bytes": 0, "bytes_per_s": 0.0,
                   "leak_detected": False, "device_restarted": False},
        "fuzz": {"total": 12, "errors": 0, "reboots": 0, "passed": True,
                 "results": [{"name": "empty", "bytes": 0, "status": 200,
                              "rebooted": False, "ok": True}]},
    },
    "device": {"uptime_s": 5, "mem_free": 90000, "sensor": "ok", "simulated": True},
}

CHAOS = {
    "chaos": {"total": 4, "recovered": 4, "all_recovered": True,
              "worst_recovery_s": 0.1,
              "results": [{"fault": "refuse", "healthy_under_fault": False,
                           "recovered": True, "recovery_time_s": 0.05}]}
}


def _variant(**changes):
    report = copy.deepcopy(REPORT)
    for path, value in changes.items():
        node = report
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return report


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_flatten_dotted_paths_and_types():
    flat = flatten(REPORT)
    assert flat["suites.latency.p95"] == 20.0
    assert flat["suites.fuzz.passed"] is True
    assert flat["suites.memory.leak_detected"] is False
    assert flat["device.uptime_s"] == 5
    assert "suites.latency.unit" not in flat
    assert "device.sensor" not in flat


def test_flatten_named_list_rows():
    flat = flatten(REPORT)
    assert flat["suites.fuzz.results.empty.status"] == 200
    assert flat["suites.fuzz.results.empty.ok"] is True
    chaos_flat = flatten(CHAOS)
    assert chaos_flat["chaos.results.refuse.recovery_time_s"] == 0.05
    assert chaos_flat["chaos.results.refuse.recovered"] is True


def test_flatten_skips_underscore_keys_unnamed_lists_and_nan():
    flat = flatten({"_meta": {"created": "x"}, "a": [1, 2],
                    "b": {"rows": [{"v": 1}]}, "c": float("nan"), "d": 1})
    assert flat == {"d": 1}


def test_compare_identical_reports_have_no_regressions():
    rows, summary = compare_reports(REPORT, copy.deepcopy(REPORT))
    assert summary["regressions"] == 0
    assert summary["checked"] == summary["ok"] + summary["neutral"]
    assert all(row["verdict"] in ("ok", "neutral") for row in rows)


def test_compare_flags_regression_beyond_tolerance():
    after = _variant(**{"suites.latency.p95": 26.0})
    _, summary = compare_reports(REPORT, after)
    assert summary["regressions"] == 1
    _, summary = compare_reports(REPORT, after, tolerance=50.0)
    assert summary["regressions"] == 0


def test_compare_marks_improvement():
    after = _variant(**{"suites.latency.p95": 15.0})
    rows, summary = compare_reports(REPORT, after)
    assert summary["improved"] == 1
    row = next(r for r in rows if r["metric"] == "suites.latency.p95")
    assert row["verdict"] == "improved"
    assert row["delta"] == "-25.0%"


def test_compare_bool_regression_and_improvement():
    after = _variant(**{"suites.fuzz.passed": False,
                        "suites.memory.leak_detected": True})
    rows, summary = compare_reports(REPORT, after)
    assert summary["regressions"] == 2
    better = _variant(**{"suites.memory.leak_detected": True})
    _, summary = compare_reports(better, REPORT)
    assert summary["improved"] >= 1


def test_compare_regression_from_zero_ignores_tolerance():
    before = _variant(**{"suites.latency.errors": 0})
    after = _variant(**{"suites.latency.errors": 2})
    _, summary = compare_reports(before, after, tolerance=100000.0)
    assert summary["regressions"] == 1


def test_compare_neutral_metric_never_fails():
    after = _variant(**{"suites.latency.n": 500})
    rows, summary = compare_reports(REPORT, after)
    assert summary["regressions"] == 0
    assert summary["neutral"] == 1
    row = next(r for r in rows if r["metric"] == "suites.latency.n")
    assert row["verdict"] == "neutral"


def test_compare_higher_is_better_metric():
    after = copy.deepcopy(CHAOS)
    after["chaos"]["recovered"] = 2
    _, summary = compare_reports(CHAOS, after)
    assert summary["regressions"] == 1


def test_compare_reports_new_and_missing_metrics():
    before = _variant()
    after = _variant(**{"suites.fuzz.duration_s": 3.0})
    del after["suites"]["memory"]
    rows, summary = compare_reports(before, after)
    assert summary["missing"] >= 1
    assert summary["new"] >= 1
    assert summary["regressions"] == 0
    assert any(row["verdict"] == "new" for row in rows)
    assert any(row["verdict"] == "missing" for row in rows)


def test_compare_rejects_disjoint_reports():
    with pytest.raises(ValueError, match="no metrics in common"):
        compare_reports({"a": {"x": 1}}, {"b": {"y": 1}})


def test_parse_budget_forms():
    assert parse_budget("p95=50") == ("p95", "<=", 50.0)
    assert parse_budget("p95<=50") == ("p95", "<=", 50.0)
    assert parse_budget("mem_free>=50000") == ("mem_free", ">=", 50000.0)
    assert parse_budget("leak_detected=false") == ("leak_detected", "==", False)
    with pytest.raises(ValueError, match="invalid budget"):
        parse_budget("=50")
    with pytest.raises(ValueError, match="invalid budget value"):
        parse_budget("p95=abc")
    with pytest.raises(ValueError, match="boolean budget"):
        parse_budget("passed>=false")


def test_check_budgets_pass_breach_and_suffix_resolution():
    rows, summary = check_budgets(REPORT, ["p95=50", "latency.errors=0",
                                           "mem_free>=50000"])
    assert summary == {"checked": 3, "passed": 3, "failed": 0}
    assert rows[0]["metric"] == "suites.latency.p95"
    _, summary = check_budgets(REPORT, ["suites.latency.p95=10"])
    assert summary["failed"] == 1


def test_check_budgets_bool_equality():
    _, summary = check_budgets(REPORT, ["leak_detected=false", "passed=true"])
    assert summary["passed"] == 2
    bad = _variant(**{"suites.memory.leak_detected": True})
    rows, summary = check_budgets(bad, ["leak_detected=false"])
    assert summary["failed"] == 1
    assert rows[0]["verdict"] == "FAIL"


def test_check_budgets_unknown_and_ambiguous_metrics():
    with pytest.raises(ValueError, match="unknown metric"):
        check_budgets(REPORT, ["p999=1"])
    with pytest.raises(ValueError, match="close metrics"):
        check_budgets(REPORT, ["latency=1"])
    ambiguous = {"a": {"p95": 1.0}, "b": {"p95": 2.0}}
    with pytest.raises(ValueError, match="ambiguous metric"):
        check_budgets(ambiguous, ["p95=1"])


def test_cli_compare_two_reports_exit_codes(tmp_path, capsys):
    good = _write(tmp_path, "before.json", REPORT)
    same = _write(tmp_path, "after.json", REPORT)
    assert main(["compare", str(good), str(same)]) == 0
    capsys.readouterr()
    worse = _write(tmp_path, "worse.json", _variant(**{"suites.latency.p95": 60.0}))
    assert main(["compare", str(good), str(worse)]) == 1
    out = capsys.readouterr().out
    assert "regression" in out
    assert "Summary" in out


def test_cli_compare_single_report_uses_baseline(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "baseline.json", REPORT)
    candidate = _write(tmp_path, "report.json", _variant(
        **{"suites.latency.p95": 40.0}))
    assert main(["compare", str(candidate)]) == 1
    assert "regression" in capsys.readouterr().out


def test_cli_compare_bad_input_exits_two(tmp_path, capsys):
    assert main(["compare", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err
    only = _write(tmp_path, "one.json", REPORT)
    other = _write(tmp_path, "two.json", {"chaos": {"total": 1}})
    assert main(["compare", str(only), str(other)]) == 2
    assert "no metrics in common" in capsys.readouterr().err
    assert main(["compare"]) == 2
    assert "compare takes" in capsys.readouterr().err


def test_cli_check_exit_codes(tmp_path, capsys):
    report = _write(tmp_path, "report.json", REPORT)
    assert main(["check", str(report), "--budget", "p95=50",
                 "--budget", "mem_free>=50000"]) == 0
    capsys.readouterr()
    assert main(["check", str(report), "--budget", "p95=10"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert main(["check", str(report)]) == 2
    assert "at least one" in capsys.readouterr().err
    assert main(["check", str(report), "--budget", "nonsense"]) == 2
    assert "invalid budget" in capsys.readouterr().err


def test_cli_check_budgets_file_merges_with_flags(tmp_path, capsys):
    report = _write(tmp_path, "report.json", REPORT)
    budgets = _write(tmp_path, "budgets.json",
                     {"latency.p95": 50, "device.mem_free": ">=50000",
                      "memory.leak_detected": False})
    assert main(["check", str(report), "--budgets", str(budgets),
                 "--budget", "latency.errors=0"]) == 0
    capsys.readouterr()
    assert main(["check", str(report), "--budgets", str(tmp_path / "nope.json")]) == 2
    empty = _write(tmp_path, "empty.json", {})
    assert main(["check", str(report), "--budgets", str(empty)]) == 2
    assert "must be a non-empty JSON object" in capsys.readouterr().err
    bad = _write(tmp_path, "bad.json", {"p95": [50]})
    assert main(["check", str(report), "--budgets", str(bad)]) == 2
    assert "limit must be a number" in capsys.readouterr().err
    bad_str = _write(tmp_path, "bad_str.json", {"p95": "50"})
    assert main(["check", str(report), "--budgets", str(bad_str)]) == 2
    assert "string limits need an operator" in capsys.readouterr().err


def test_cli_baseline_writes_stamp_and_ignores_meta(tmp_path, capsys):
    report = _write(tmp_path, "report.json", REPORT)
    out = tmp_path / "baseline.json"
    assert main(["baseline", str(report), "--out", str(out)]) == 0
    assert "# ESP32 Eval Bench Baseline" in capsys.readouterr().out
    stamped = json.loads(out.read_text(encoding="utf-8"))
    assert stamped["_meta"]["espbench"] == __version__
    assert stamped["_meta"]["source"] == str(report)
    assert flatten(stamped) == flatten(REPORT)


def test_cli_baseline_bad_input_exits_two(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert main(["baseline", str(broken)]) == 2
    empty = _write(tmp_path, "empty.json", {})
    assert main(["baseline", str(empty)]) == 2
    assert main(["baseline", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err


def test_stamp_baseline_fields():
    stamped = stamp_baseline(REPORT, "9.9.9", "src.json")
    assert stamped["_meta"]["espbench"] == "9.9.9"
    assert stamped["_meta"]["source"] == "src.json"
    assert stamped["_meta"]["created"].endswith("Z")


def test_load_report_rejects_arrays(tmp_path):
    path = tmp_path / "array.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a non-empty report object"):
        load_report(str(path))


def _with_repeat(report, distributions):
    payload = copy.deepcopy(report)
    payload["repeat"] = {
        "count": len(next(iter(distributions.values()))),
        "metrics": [{"metric": path, "values": values}
                    for path, values in distributions.items()],
    }
    return payload


def test_aggregate_runs_single_run_passthrough():
    run = _variant()
    assert aggregate_runs([run]) is run


def test_aggregate_runs_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        aggregate_runs([])


def test_aggregate_runs_median_bool_all_and_hard_max():
    runs = [_variant(**{"suites.latency.p95": v}) for v in (20.0, 30.0, 40.0)]
    runs[1]["suites"]["fuzz"]["passed"] = False
    runs[1]["suites"]["latency"]["errors"] = 3
    combined = aggregate_runs(runs)
    assert combined["suites"]["latency"]["p95"] == 30.0
    assert combined["suites"]["fuzz"]["passed"] is False
    assert combined["suites"]["latency"]["errors"] == 3
    assert combined["device"]["uptime_s"] == 5


def test_aggregate_runs_repeat_rows_and_flatten_consistency():
    runs = [_variant(**{"suites.latency.p95": v}) for v in (20.0, 30.0, 40.0)]
    combined = aggregate_runs(runs)
    assert combined["repeat"]["count"] == 3
    row = next(r for r in combined["repeat"]["metrics"]
               if r["metric"] == "suites.latency.p95")
    assert row["values"] == [20.0, 30.0, 40.0]
    assert row["median"] == 30.0
    assert row["min"] == 20.0
    assert row["max"] == 40.0
    flat = flatten(combined)
    assert flat["suites.latency.p95"] == 30.0
    assert flat["repeat.count"] == 3
    assert "suites.latency.unit" not in flat


def test_aggregate_runs_named_list_rows_use_all_runs():
    failing = [{"name": "empty", "bytes": 0, "status": 200,
                "rebooted": False, "ok": False}]
    runs = [_variant(), _variant(**{"suites.fuzz.results": failing})]
    combined = aggregate_runs(runs)
    item = combined["suites"]["fuzz"]["results"][0]
    assert item["ok"] is False
    assert item["status"] == 200
    flat = flatten(combined)
    assert flat["suites.fuzz.results.empty.ok"] is False


def test_aggregate_runs_device_error_survives():
    runs = [_variant(), _variant()]
    runs[1]["device"] = {"error": "boom"}
    combined = aggregate_runs(runs)
    assert combined["device"] == {"error": "boom"}
    assert combined["repeat"]["count"] == 2


def test_compare_statistical_regression_when_ci_excludes_zero():
    before = _with_repeat(_variant(), {"suites.latency.p95": [20.0, 20.0, 20.0]})
    after = _with_repeat(_variant(**{"suites.latency.p95": 30.0}),
                         {"suites.latency.p95": [30.0, 30.0, 30.0]})
    rows, summary = compare_reports(before, after)
    row = next(r for r in rows if r["metric"] == "suites.latency.p95")
    assert row["verdict"] == "regression"
    assert summary["regressions"] == 1


def test_compare_statistical_improvement_when_ci_entirely_better():
    before = _with_repeat(_variant(**{"suites.latency.p95": 30.0}),
                          {"suites.latency.p95": [30.0, 30.0, 30.0]})
    after = _with_repeat(_variant(**{"suites.latency.p95": 20.0}),
                         {"suites.latency.p95": [20.0, 20.0, 20.0]})
    rows, summary = compare_reports(before, after)
    row = next(r for r in rows if r["metric"] == "suites.latency.p95")
    assert row["verdict"] == "improved"
    assert summary["improved"] == 1


def test_compare_flaky_distribution_never_gates():
    before = _with_repeat(_variant(**{"suites.latency.p95": 50.0}),
                          {"suites.latency.p95": [10.0, 50.0, 90.0]})
    after = _with_repeat(_variant(**{"suites.latency.p95": 70.0}),
                         {"suites.latency.p95": [30.0, 70.0, 110.0]})
    rows, summary = compare_reports(before, after)
    row = next(r for r in rows if r["metric"] == "suites.latency.p95")
    assert row["verdict"] == "flaky"
    assert summary["regressions"] == 0
    assert summary["flaky"] == 1


def test_compare_point_regression_inside_ci_stays_ok():
    before = _with_repeat(_variant(**{"suites.latency.p95": 50.0}),
                          {"suites.latency.p95": [40.0, 50.0, 60.0]})
    after = _with_repeat(_variant(**{"suites.latency.p95": 58.0}),
                         {"suites.latency.p95": [52.0, 58.0, 64.0]})
    rows, summary = compare_reports(before, after)
    row = next(r for r in rows if r["metric"] == "suites.latency.p95")
    assert row["verdict"] == "ok"
    assert summary["regressions"] == 0


def test_compare_hard_counter_ignores_statistics():
    before = _with_repeat(_variant(**{"suites.latency.errors": 1}),
                          {"suites.latency.errors": [0, 0, 1]})
    after = _with_repeat(_variant(**{"suites.latency.errors": 3}),
                         {"suites.latency.errors": [0, 0, 3]})
    rows, summary = compare_reports(before, after)
    row = next(r for r in rows if r["metric"] == "suites.latency.errors")
    assert row["verdict"] == "regression"
    assert summary["regressions"] == 1


def test_compare_zero_before_ignores_distributions():
    before = _with_repeat(_variant(**{"suites.latency.errors": 0}),
                          {"suites.latency.errors": [0, 0, 0]})
    after = _with_repeat(_variant(**{"suites.latency.errors": 3}),
                         {"suites.latency.errors": [1, 2, 3]})
    _, summary = compare_reports(before, after, tolerance=100000.0)
    assert summary["regressions"] == 1


def test_firmware_version_reads_device_section():
    assert firmware_version(_variant(**{"device.fw": "1.0"})) == "1.0"
    assert firmware_version(REPORT) is None
    assert firmware_version({"device": {"error": "down"}}) is None
    assert firmware_version({"device": {"fw": ""}}) is None
    assert firmware_version({"suites": {}}) is None


def test_derive_budgets_shapes_and_skips():
    budgets = derive_budgets(REPORT, 20.0)
    assert budgets["suites.latency.p95"] == pytest.approx(24.0)
    assert budgets["suites.memory.leak_detected"] is False
    assert budgets["suites.fuzz.passed"] is True
    assert budgets["device.mem_free"] == ">=72000.0"
    assert budgets["suites.memory.bytes_per_s"] == ">=0.0"
    assert "suites.latency.n" not in budgets
    assert "suites.memory.samples" not in budgets
    assert "device.uptime_s" not in budgets
    assert "device.simulated" not in budgets
    with pytest.raises(ValueError, match="margin"):
        derive_budgets(REPORT, -1.0)


def test_cli_run_repeat_aggregates_and_writes_runs(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["run", "--sim", "--suites", "latency", "--latency-n", "5",
                 "--repeat", "3", "--out", str(out)]) == 0
    console = capsys.readouterr().out
    assert "## Repeat" not in console
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["repeat"]["count"] == 3
    row = next(r for r in payload["repeat"]["metrics"]
               if r["metric"] == "suites.latency.p95")
    assert len(row["values"]) == 3
    assert payload["suites"]["latency"]["p95"] == row["median"]
    assert (out / "runs" / "report-01.json").exists()
    assert (out / "runs" / "report-03.json").exists()
    per_run = json.loads((out / "runs" / "report-01.json").read_text(encoding="utf-8"))
    assert "repeat" not in per_run
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## Repeat" in md
    assert "### Metrics" in md


def test_cli_run_repeat_one_has_no_repeat_section(tmp_path):
    out = tmp_path / "out"
    assert main(["run", "--sim", "--suites", "latency", "--latency-n", "5",
                 "--out", str(out)]) == 0
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert "repeat" not in payload
    assert not (out / "runs").exists()


def test_cli_baseline_auto_derives_loadable_budgets(tmp_path, capsys):
    report = _write(tmp_path, "report.json", REPORT)
    budgets_path = tmp_path / "budgets.json"
    assert main(["baseline", str(report), "--auto", "--margin", "20",
                 "--out", str(budgets_path)]) == 0
    assert "Auto Budgets" in capsys.readouterr().out
    budgets = json.loads(budgets_path.read_text(encoding="utf-8"))
    assert budgets["suites.latency.p95"] == pytest.approx(24.0)
    assert budgets["suites.memory.leak_detected"] is False
    assert budgets["suites.fuzz.passed"] is True
    assert budgets["device.mem_free"] == ">=72000.0"
    assert "suites.latency.n" not in budgets
    assert "device.simulated" not in budgets
    assert main(["check", str(report), "--budgets", str(budgets_path)]) == 0
    capsys.readouterr()
    bad = _write(tmp_path, "bad.json", _variant(**{"suites.latency.p95": 90.0}))
    assert main(["check", str(bad), "--budgets", str(budgets_path)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_baseline_auto_default_out(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "report.json", REPORT)
    assert main(["baseline", "report.json", "--auto"]) == 0
    assert (tmp_path / "budgets.json").exists()
    capsys.readouterr()
    assert main(["baseline", "report.json"]) == 0
    assert (tmp_path / "baseline.json").exists()


def test_cli_baseline_auto_errors(tmp_path, capsys):
    empty = _write(tmp_path, "empty.json", {})
    assert main(["baseline", str(empty), "--auto"]) == 2
    neutral = _write(tmp_path, "neutral.json", {"a": {"n": 3}})
    assert main(["baseline", str(neutral), "--auto"]) == 2
    assert "no budgetable metrics" in capsys.readouterr().err


def test_soak_duration_metrics_compare_neutral():
    before = {"soak": {"iterations": 10, "elapsed_s": 60.0,
                       "hours_planned": 1.0, "passed": True}}
    after = {"soak": {"iterations": 11, "elapsed_s": 61.8,
                      "hours_planned": 1.0, "passed": True}}
    rows, summary = compare_reports(before, after)
    assert summary["regressions"] == 0
    verdicts = {row["metric"]: row["verdict"] for row in rows}
    assert verdicts["soak.iterations"] == "neutral"
    assert verdicts["soak.elapsed_s"] == "neutral"


def test_budget_rejects_non_finite_limits():
    with pytest.raises(ValueError, match="finite"):
        parse_budget("p95=inf")
    with pytest.raises(ValueError, match="finite"):
        parse_budget("p95=nan")
    with pytest.raises(ValueError, match="finite"):
        parse_budget("p95>=1e999")


def test_flatten_duplicate_names_fall_back_to_indexes():
    report = {"chaos": {"total": 2, "results": [
        {"fault": "refuse", "recovered": True},
        {"fault": "refuse", "recovered": False}]}}
    flat = flatten(report)
    assert flat["chaos.results.1.recovered"] is True
    assert flat["chaos.results.2.recovered"] is False
    assert flat["chaos.total"] == 2


def test_aggregate_ignores_suiteless_runs():
    dead = {"device": {"error": "down"}, "suites": {}}
    good = {"device": {"fw": "1.0"},
            "suites": {"latency": {"n": 10, "p95": 5.0, "errors": 0}}}
    combined = aggregate_runs([dead, good, good])
    assert combined["suites"]["latency"]["n"] == 10
    assert combined["suites"]["latency"]["p95"] == 5.0
    assert combined["device"]["error"] == "down"
    metrics = [row["metric"] for row in combined["repeat"]["metrics"]]
    assert "suites.latency.p95" in metrics
