import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import espbench
from espbench.cli import main
from espbench.replay import load_steps


def test_run_sim(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["run", "--sim", "--suites", "latency,fuzz",
                 "--latency-n", "8", "--out", str(out)])
    assert code == 0
    captured = capsys.readouterr().out
    assert "ESP32 Eval Bench Report" in captured
    assert (out / "report.json").exists()
    assert (out / "junit.xml").exists()
    payload = json.loads((out / "report.json").read_text())
    assert payload["suites"]["latency"]["n"] == 8
    assert payload["suites"]["fuzz"]["passed"] is True


def test_run_sim_memory(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["run", "--sim", "--suites", "memory",
                 "--memory-samples", "3", "--memory-interval", "0",
                 "--out", str(out)])
    assert code == 0
    payload = json.loads((out / "report.json").read_text())
    assert payload["suites"]["memory"]["samples"] == 3


def test_replay_record_then_run(tmp_path, capsys):
    fixture = tmp_path / "session.json"
    code = main(["replay-record", "--sim", "--out", str(fixture)])
    assert code == 0
    assert fixture.exists()
    assert "Recorded fuzz" in capsys.readouterr().out
    code = main(["replay-run", "--fixture", str(fixture)])
    assert code == 0
    assert "Replayed fuzz" in capsys.readouterr().out


def test_chaos_sim(capsys):
    code = main(["chaos", "--sim", "--faults", "refuse,cut",
                 "--duration", "0.2", "--recovery-timeout", "2.0"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Chaos summary" in out
    assert "all_recovered" in out
    assert "all_faults_effective" in out


def test_chaos_ineffective_fault_exits_nonzero(capsys):
    code = main(["chaos", "--sim", "--faults", "refuse",
                 "--duration", "0", "--recovery-timeout", "1.0"])
    assert code == 1
    assert "all_faults_effective" in capsys.readouterr().out


def test_power_command(tmp_path, capsys):
    csv_path = tmp_path / "power.csv"
    csv_path.write_text("time_ms,mA\n0,100\n1000,200\n2000,150\n")
    out_md = tmp_path / "power.md"
    code = main(["power", "--csv", str(csv_path), "--capacity", "1200",
                 "--out", str(out_md)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Power summary" in out
    assert out_md.exists()
    assert "Battery projection" in out_md.read_text()


def test_power_with_markers(tmp_path, capsys):
    csv_path = tmp_path / "power.csv"
    csv_path.write_text("time_ms,mA\n0,100\n1000,200\n2000,150\n")
    markers = tmp_path / "markers.csv"
    markers.write_text("time_ms,label\n0,wifi\n1000,sensor\n")
    code = main(["power", "--csv", str(csv_path), "--markers", str(markers)])
    assert code == 0
    assert "wifi" in capsys.readouterr().out


def test_run_unreachable_device_exits_nonzero(capsys):
    code = main(["run", "--host", "127.0.0.1", "--port", "1",
                 "--suites", "latency,memory,fuzz"])
    assert code == 1
    assert "error" in capsys.readouterr().out


def test_replay_missing_fixture_returns_error_code(tmp_path, capsys):
    code = main(["replay-run", "--fixture", str(tmp_path / "nope.json")])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_missing_host_returns_error_code(capsys):
    code = main(["run", "--suites", "latency"])
    assert code == 2
    assert "--host is required" in capsys.readouterr().err


def test_chaos_missing_host_returns_error_code(capsys):
    code = main(["chaos"])
    assert code == 2
    assert "--host is required" in capsys.readouterr().err


def test_chaos_empty_faults_returns_error_code(capsys):
    code = main(["chaos", "--sim", "--faults", " , "])
    assert code == 2
    assert "at least one fault" in capsys.readouterr().err


def test_power_missing_csv_returns_error_code(tmp_path, capsys):
    code = main(["power", "--csv", str(tmp_path / "nope.csv")])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"espbench {espbench.__version__}" in capsys.readouterr().out


def test_pyproject_version_matches_package():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    assert match and match.group(1) == espbench.__version__


def test_module_entrypoint_runs_version():
    proc = subprocess.run([sys.executable, "-m", "espbench", "--version"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert f"espbench {espbench.__version__}" in proc.stdout


def test_run_rejects_empty_suites(capsys):
    code = main(["run", "--sim", "--suites", " , "])
    assert code == 2
    assert "at least one suite" in capsys.readouterr().err


def test_replay_invalid_fixture_returns_error_code(tmp_path, capsys):
    fixture = tmp_path / "bad.json"
    fixture.write_text(json.dumps({"version": 9, "steps": []}), encoding="utf-8")
    code = main(["replay-run", "--fixture", str(fixture)])
    assert code == 2
    assert "unsupported fixture version" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["run", "--sim", "--suites", "latency", "--port", "0"],
    ["run", "--sim", "--suites", "latency", "--port", "70000"],
    ["run", "--sim", "--suites", "latency", "--port", "not-a-port"],
    ["run", "--sim", "--suites", "latency", "--latency-n", "0"],
    ["run", "--sim", "--suites", "memory", "--memory-samples", "0"],
    ["run", "--sim", "--suites", "memory", "--memory-interval", "-1"],
    ["run", "--sim", "--suites", "fuzz", "--settle", "-0.5"],
    ["run", "--sim", "--suites", "latency", "--repeat", "0"],
    ["chaos", "--sim", "--duration", "-1"],
    ["chaos", "--sim", "--recovery-timeout", "-1"],
    ["chaos", "--sim", "--delay-ms", "-1"],
    ["chaos", "--sim", "--corrupt-rate", "1.5"],
    ["chaos", "--sim", "--corrupt-rate", "-0.1"],
    ["replay-record", "--sim", "--out", "unused.json", "--settle", "-1"],
    ["power", "--csv", "missing.csv", "--capacity", "-5"],
    ["baseline", "report.json", "--auto", "--margin", "-1"],
])
def test_out_of_range_flags_exit_two(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_power_zero_capacity_is_accepted_and_skips_projection(tmp_path, capsys):
    csv_path = tmp_path / "power.csv"
    csv_path.write_text("time_ms,mA\n0,100\n1000,200\n")
    code = main(["power", "--csv", str(csv_path), "--capacity", "0"])
    assert code == 0
    assert "Battery projection" not in capsys.readouterr().out


def test_run_bundle_writes_replayable_fixture(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["run", "--sim", "--suites", "latency", "--latency-n", "5",
                 "--out", str(out), "--bundle"])
    assert code == 0
    steps = load_steps(out / "session.json")
    assert steps[0]["op"] == "stats"
    assert any(step["op"] == "version" for step in steps)
    assert "wrote fixture" in capsys.readouterr().out


def test_run_bundle_custom_path(tmp_path):
    fixture = tmp_path / "custom.json"
    code = main(["run", "--sim", "--suites", "fuzz",
                 "--bundle", str(fixture)])
    assert code == 0
    assert load_steps(fixture)


def test_run_bundle_default_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main(["run", "--sim", "--suites", "latency", "--latency-n", "3",
                 "--bundle"])
    assert code == 0
    assert (tmp_path / "session.json").exists()
    capsys.readouterr()


def _fw_report(fw, p95):
    return {"device": {"fw": fw, "uptime_s": 5},
            "suites": {"latency": {"n": 10, "p95": p95, "unit": "ms"}}}


def test_compare_firmware_mismatch_gated(tmp_path, capsys):
    before = _write_json(tmp_path, "before.json", _fw_report("0.1.0", 20.0))
    after = _write_json(tmp_path, "after.json", _fw_report("0.2.0", 20.0))
    assert main(["compare", str(before), str(after)]) == 2
    err = capsys.readouterr().err
    assert "firmware mismatch" in err
    assert "--allow-mismatch" in err
    assert main(["compare", str(before), str(after), "--allow-mismatch"]) == 0
    out = capsys.readouterr().out
    assert "Firmware" in out
    assert "0.1.0" in out and "0.2.0" in out
    same = _write_json(tmp_path, "same.json", _fw_report("0.1.0", 20.0))
    assert main(["compare", str(before), str(same)]) == 0
    assert "Firmware" not in capsys.readouterr().out


def test_compare_without_fw_versions_skips_gate(tmp_path, capsys):
    before = _write_json(tmp_path, "before.json",
                         {"suites": {"latency": {"n": 5, "p95": 20.0}}})
    after = _write_json(tmp_path, "after.json",
                        {"suites": {"latency": {"n": 5, "p95": 20.0}}})
    assert main(["compare", str(before), str(after)]) == 0
    capsys.readouterr()


def _write_json(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_diff_command_exit_codes(tmp_path, capsys):
    before = _write_json(tmp_path, "a.json",
                         {"version": 1, "steps": [
                             {"op": "echo", "payload": "aa", "status": 200}]})
    after = _write_json(tmp_path, "b.json",
                        {"version": 1, "steps": [
                            {"op": "echo", "payload": "aa", "status": 500}]})
    assert main(["diff", str(before), str(after)]) == 1
    out = capsys.readouterr().out
    assert "changed" in out
    assert "status=500" in out
    assert main(["diff", str(before), str(before)]) == 0
    assert "_none_" in capsys.readouterr().out
    assert main(["diff", str(before), str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err
    bad = _write_json(tmp_path, "bad.json", {"version": 9, "steps": []})
    assert main(["diff", str(before), str(bad)]) == 2
    assert "unsupported fixture version" in capsys.readouterr().err


def test_soak_sim_single_pass(tmp_path, capsys):
    out = tmp_path / "soak"
    code = main(["soak", "--sim", "--hours", "0", "--out", str(out)])
    assert code == 0
    assert "Soak summary" in capsys.readouterr().out
    payload = json.loads((out / "report.json").read_text())
    assert payload["soak"]["iterations"] == 1
    assert payload["soak"]["passed"] is True
    assert payload["soak"]["events"] == []


def test_soak_sim_with_suites(capsys):
    code = main(["soak", "--sim", "--hours", "0", "--suites", "latency",
                 "--latency-n", "3", "--interval", "0"])
    assert code == 0
    assert "Soak summary" in capsys.readouterr().out


def test_soak_unknown_suite_exits_two(capsys):
    code = main(["soak", "--sim", "--hours", "0", "--suites", "nope"])
    assert code == 2
    assert "unknown suite" in capsys.readouterr().err


def test_soak_bad_hours_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["soak", "--sim", "--hours", "-1"])
    assert exc.value.code == 2


def test_run_dead_host_exits_one_fast(capsys):
    code = main(["run", "--host", "127.0.0.1", "--port", "1",
                 "--suites", "latency", "--latency-n", "2"])
    assert code == 1
    out = capsys.readouterr().out
    assert "error" in out


def test_run_fixture_replays_bundle_with_meta(tmp_path, capsys):
    rec = tmp_path / "rec"
    assert main(["run", "--sim", "--suites", "latency,fuzz", "--latency-n", "5",
                 "--out", str(rec), "--bundle"]) == 0
    capsys.readouterr()
    replay_out = tmp_path / "replay"
    assert main(["run", "--fixture", str(rec / "session.json"),
                 "--out", str(replay_out)]) == 0
    original = json.loads((rec / "report.json").read_text())
    replayed = json.loads((replay_out / "report.json").read_text())
    assert replayed["suites"]["latency"]["n"] == original["suites"]["latency"]["n"]
    assert replayed["suites"]["latency"]["n"] == 5
    assert abs(replayed["suites"]["latency"]["p95"]
               - original["suites"]["latency"]["p95"]) < 0.01
    assert replayed["suites"]["fuzz"]["passed"] is True
    assert replayed["device"]["fw"] == "sim-0.1.0"
    assert "Device log" not in capsys.readouterr().out


def test_run_fixture_meta_overrides_cli_defaults(tmp_path):
    rec = tmp_path / "rec"
    assert main(["run", "--sim", "--suites", "fuzz", "--out", str(rec),
                 "--bundle"]) == 0
    assert main(["run", "--fixture", str(rec / "session.json")]) == 0


def test_run_fixture_without_meta_uses_cli_suites(tmp_path):
    rec = tmp_path / "rec"
    assert main(["run", "--sim", "--suites", "fuzz", "--out", str(rec),
                 "--bundle"]) == 0
    payload = json.loads((rec / "session.json").read_text())
    del payload["run"]
    (rec / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    assert main(["run", "--fixture", str(rec / "session.json"),
                 "--suites", "fuzz"]) == 0


def test_run_fixture_rejects_device_flags(tmp_path, capsys):
    assert main(["run", "--fixture", "x.json", "--sim"]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert main(["run", "--fixture", str(tmp_path / "missing.json")]) == 2
    assert "error:" in capsys.readouterr().err


def test_run_bundle_with_repeat_merges_recordings(tmp_path):
    out = tmp_path / "out"
    code = main(["run", "--sim", "--suites", "latency", "--latency-n", "3",
                 "--repeat", "2", "--out", str(out), "--bundle"])
    assert code == 0
    steps = load_steps(out / "session.json")
    assert sum(1 for step in steps if step["op"] == "stats") == 2
    assert sum(1 for step in steps if step["op"] == "version") == 2
    assert sum(1 for step in steps if step["op"] == "ping") == 2 * (3 + 10)


def test_doctor_environment_ok(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "ESP32 Eval Bench Doctor" in out
    assert "| espbench |" in out


def test_doctor_sim_is_ok(capsys):
    assert main(["doctor", "--sim"]) == 0
    assert "simulator selected" in capsys.readouterr().out


def test_doctor_dead_host_exits_one():
    assert main(["doctor", "--host", "127.0.0.1", "--port", "9"]) == 1


def test_gui_command_delegates_to_gui_main(monkeypatch):
    pytest.importorskip("PySide6")
    import espbench.gui as gui_module

    monkeypatch.setattr(gui_module, "main", lambda argv=None: 0)
    assert main(["gui"]) == 0


def test_run_reports_progress_on_stderr(capsys):
    assert main(["run", "--sim", "--suites", "latency",
                 "--latency-n", "3"]) == 0
    captured = capsys.readouterr()
    assert "run: suite latency (1/1)" in captured.err


def test_soak_reports_progress_on_stderr(capsys):
    assert main(["soak", "--sim", "--hours", "0"]) == 0
    captured = capsys.readouterr()
    assert "soak: iteration 1" in captured.err


def test_insight_strict_fails_on_warnings(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(
        {"suites": {"memory": {"leak_detected": True}}}), encoding="utf-8")
    assert main(["insight", str(report)]) == 0
    assert main(["insight", str(report), "--strict"]) == 1


def test_insight_strict_passes_on_clean_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(
        {"suites": {"memory": {"leak_detected": False}}}), encoding="utf-8")
    assert main(["insight", str(report), "--strict"]) == 0


def test_check_cli_budget_overrides_budgets_file(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(
        {"suites": {"latency": {"p95": 10.0}}}), encoding="utf-8")
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"latency.p95": 5}), encoding="utf-8")
    code = main(["check", str(report), "--budgets", str(budgets),
                 "--budget", "p95=100"])
    out = capsys.readouterr().out
    assert code == 0
    assert "| checked | 1 |" in out


def test_run_fixture_unknown_param_exits_two(tmp_path):
    from espbench.replay import save_steps

    fixture = tmp_path / "session.json"
    save_steps([{"op": "stats", "payload": {"boot_count": 1}}], fixture,
               run={"suites": ["latency"],
                    "params": {"latency": {"bogus": 1}}})
    assert main(["run", "--fixture", str(fixture)]) == 2


def test_run_fixture_unknown_op_exits_two(tmp_path, capsys):
    from espbench.replay import save_steps

    fixture = tmp_path / "session.json"
    save_steps([{"op": "teleport", "when": "now"}], fixture,
               run={"suites": ["latency"], "params": {}})
    assert main(["run", "--fixture", str(fixture)]) == 2
    assert "unknown op 'teleport'" in capsys.readouterr().err


def test_run_fixture_unused_steps_exits_one(tmp_path, capsys):
    rec = tmp_path / "rec"
    assert main(["run", "--sim", "--suites", "fuzz", "--out", str(rec),
                 "--bundle"]) == 0
    payload = json.loads((rec / "session.json").read_text(encoding="utf-8"))
    payload["steps"].append({"op": "ping", "result": 1.0})
    (rec / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    assert main(["run", "--fixture", str(rec / "session.json")]) == 1
    assert "unused steps" in capsys.readouterr().err


def test_compare_without_baseline_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(
        {"suites": {"latency": {"p95": 10.0}}}), encoding="utf-8")
    assert main(["compare", str(report)]) == 2
    assert "espbench baseline" in capsys.readouterr().err


def test_out_directory_gets_default_names(tmp_path, capsys):
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"suites.latency.p95": 100}),
                       encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps(
        {"suites": {"latency": {"n": 5, "p50": 1.0, "p95": 2.0}}}),
        encoding="utf-8")
    assert main(["check", str(report), "--budgets", str(budgets),
                 "--out", str(tmp_path)]) == 0
    assert (tmp_path / "check.md").exists()
    csv = tmp_path / "power.csv"
    csv.write_text("time_ms,mA\n0,100\n1000,200\n", encoding="utf-8")
    assert main(["power", "--csv", str(csv), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "power_report.md").exists()
    assert main(["badge", str(report), "--metric", "suites.latency.p95",
                 "--out", str(tmp_path)]) == 0
    assert (tmp_path / "badge.svg").exists()
    assert "wrote" in capsys.readouterr().out


def test_timeout_flag_accepted_and_bounded(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["run", "--sim", "--suites", "latency", "--latency-n", "5",
                 "--timeout", "2", "--out", str(out)]) == 0
    with pytest.raises(SystemExit) as exc:
        main(["run", "--sim", "--timeout", "0"])
    assert exc.value.code == 2


def test_insight_small_sample_is_info_only(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(
        {"suites": {"latency": {"n": 5, "errors": 0,
                                "p50": 10.0, "p95": 12.0}}}),
        encoding="utf-8")
    assert main(["insight", str(report)]) == 0
    out = capsys.readouterr().out
    assert "info: latency percentiles come from only 5 samples" in out
    assert main(["insight", str(report), "--strict"]) == 0


def test_chaos_progress_on_stderr(capsys):
    code = main(["chaos", "--sim", "--faults", "refuse", "--duration", "0.05",
                 "--recovery-timeout", "0.3"])
    err = capsys.readouterr().err
    assert code in (0, 1)
    assert "chaos: fault refuse (1/1)" in err
    assert "refuse recovered" in err
