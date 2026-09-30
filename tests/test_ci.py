import json

import pytest

from espbench.cli import main


def _report(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_completions_bash(capsys):
    assert main(["--completions", "bash"]) == 0
    out = capsys.readouterr().out
    assert "complete -F _espbench espbench" in out
    assert "run soak chaos replay-record" in out
    assert "--fixture" in out
    assert "--completions" in out


def test_completions_zsh(capsys):
    assert main(["--completions", "zsh"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("#compdef espbench")
    assert "_describe 'command' commands" in out
    assert "run:run evaluation suites" in out
    assert "--schedule" in out


def test_completions_fish(capsys):
    assert main(["--completions", "fish"]) == 0
    out = capsys.readouterr().out
    assert "complete -c espbench -f" in out
    assert "__fish_seen_subcommand_from run" in out
    assert "-l fixture" in out
    assert "-d 'device IP'" in out


def test_completions_invalid_shell():
    with pytest.raises(SystemExit) as exc:
        main(["--completions", "tcsh"])
    assert exc.value.code == 2


def test_badge_renders_metric_to_stdout(tmp_path, capsys):
    report = _report(tmp_path, "r.json",
                     {"suites": {"latency": {"p95": 24.5, "n": 100}}})
    assert main(["badge", str(report), "--metric", "p95"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("<svg")
    assert out.rstrip().endswith("</svg>")
    assert ">p95<" in out
    assert ">24.5<" in out
    assert 'fill="#007ec6"' in out


def test_badge_bool_gets_pass_fail_colors(tmp_path, capsys):
    good = _report(tmp_path, "good.json", {"suites": {"fuzz": {"passed": True}}})
    assert main(["badge", str(good), "--metric", "passed"]) == 0
    out = capsys.readouterr().out
    assert ">pass<" in out
    assert 'fill="#4c1"' in out
    bad = _report(tmp_path, "bad.json", {"suites": {"fuzz": {"passed": False}}})
    assert main(["badge", str(bad), "--metric", "passed"]) == 0
    out = capsys.readouterr().out
    assert ">fail<" in out
    assert 'fill="#e05d44"' in out


def test_badge_out_file_with_custom_color(tmp_path):
    report = _report(tmp_path, "r.json", {"suites": {"latency": {"p95": 3.0}}})
    target = tmp_path / "badge.svg"
    assert main(["badge", str(report), "--metric", "suites.latency.p95",
                 "--label", "p95 ms", "--color", "red", "--out", str(target)]) == 0
    svg = target.read_text(encoding="utf-8")
    assert ">p95 ms<" in svg
    assert 'fill="#e05d44"' in svg


def test_badge_bad_inputs_exit_two(tmp_path, capsys):
    report = _report(tmp_path, "r.json",
                     {"a": {"p95": 1.0}, "b": {"p95": 2.0}})
    assert main(["badge", str(report), "--metric", "nope"]) == 2
    assert "unknown metric" in capsys.readouterr().err
    assert main(["badge", str(report), "--metric", "p95"]) == 2
    assert "ambiguous metric" in capsys.readouterr().err
    assert main(["badge", str(report), "--metric", "a.p95",
                 "--color", "chartreuse"]) == 2
    assert "unknown badge color" in capsys.readouterr().err
    assert main(["badge", str(tmp_path / "missing.json"), "--metric", "p95"]) == 2
    assert "error:" in capsys.readouterr().err


def test_init_creates_workflow(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    workflow = tmp_path / ".github" / "workflows" / "espbench.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "name: espbench" in text
    assert "python -m pytest -q" in text
    assert "python -m ruff check ." in text
    assert "espbench run --sim" in text
    assert "upload-artifact" in text
    out = capsys.readouterr().out
    assert "Next steps" in out
    assert "Created" in out


def test_init_refuses_existing_workflow(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["init", str(tmp_path)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert main(["init", str(tmp_path), "--force"]) == 0


def test_init_defaults_to_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / ".github" / "workflows" / "espbench.yml").exists()


def test_run_appends_github_step_summary(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    summary.write_text("## ci\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = main(["run", "--sim", "--suites", "latency", "--latency-n", "3"])
    assert code == 0
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("## ci\n")
    assert "Espressbench Report" in text
    assert text.endswith("\n\n")


def test_step_summary_swallows_os_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))
    code = main(["run", "--sim", "--suites", "latency", "--latency-n", "2"])
    assert code == 0


def test_insight_appends_step_summary(tmp_path, monkeypatch, capsys):
    report = _report(tmp_path, "r.json",
                     {"suites": {"memory": {"leak_detected": True}}})
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["insight", str(report)]) == 0
    text = summary.read_text(encoding="utf-8")
    assert "Insights" in text
    assert "memory leak" in text


def test_step_summary_unset_is_a_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out_dir = tmp_path / "run"
    assert main(["run", "--sim", "--suites", "latency", "--latency-n", "2",
                 "--out", str(out_dir)]) == 0
    assert not (tmp_path / "summary.md").exists()
