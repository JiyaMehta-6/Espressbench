import json
import xml.etree.ElementTree as ET

from espbench.report import to_junit, to_markdown


def test_markdown_escapes_pipes_and_newlines_in_values():
    text = to_markdown({"S": {"g": {"note": "a|b\nc"}}})
    assert "a\\|b c" in text
    assert "| g |" in text


def test_markdown_escapes_row_names():
    text = to_markdown({"S": {"a|b": {"v": 1}}})
    assert "| a\\|b | 1 |" in text


def test_markdown_bullet_values_are_single_line():
    text = to_markdown({"S": {"note": "line1\nline2"}})
    assert "line1 line2" in text
    assert "\nline2" not in text


def test_markdown_renders_none_as_none_placeholder():
    text = to_markdown({"Power": {"hours": None, "rows": {"a": {"v": None}}}})
    assert "| hours | _none_ |" in text
    assert "| a | _none_ |" in text
    assert "None" not in text


def test_junit_failure_message_survives_quotes_and_parses():
    results = {"suites": {"memory": {"leak_detected": True,
                                     "error": 'bad "state"'}}}
    root = ET.fromstring(to_junit(results))
    assert root.get("failures") == "1"
    payload = json.loads(root.find("testcase/failure").get("message"))
    assert payload["error"] == 'bad "state"'
    assert payload["leak_detected"] is True


def test_write_reports_produces_strict_json_for_non_finite(tmp_path):
    from espbench.report import write_reports

    write_reports({"suites": {"latency": {"p95": float("nan"), "n": 5}}}, tmp_path)
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["suites"]["latency"]["p95"] is None
    assert data["suites"]["latency"]["n"] == 5


def test_write_reports_maps_infinity_to_null(tmp_path):
    from espbench.report import write_reports

    write_reports({"suites": {"latency": {"p95": float("inf"), "n": 5}}}, tmp_path)
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["suites"]["latency"]["p95"] is None
    assert data["suites"]["latency"]["n"] == 5
