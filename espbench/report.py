import json
import math
from pathlib import Path
from xml.sax.saxutils import quoteattr

from espbench.hil import suite_failed


def _escape(text):
    return (text.replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r\n", " ").replace("\n", " ").replace("\r", " "))


def _cell(value):
    if value is None:
        return "_none_"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, list):
        return f"{len(value)} items"
    if isinstance(value, dict):
        cell = "; ".join(f"{k}={_cell(v)}" for k, v in value.items())[:160]
        if len(cell) == 160 and cell.endswith("\\") and not cell.endswith("\\\\"):
            cell = cell[:-1]
        return cell
    if isinstance(value, str):
        return _escape(value)
    return str(value)


def _heading(text):
    text = str(text).replace("_", " ")
    return text[:1].upper() + text[1:]


def _table(rows):
    if not rows:
        return ["_none_"]
    headers = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    lines = ["| " + " | ".join(_cell(h) for h in headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(h, "")) for h in headers) + " |")
    return lines


def _render(payload, level):
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items()
                   if not str(key).startswith("_")}
        if payload and all(isinstance(v, dict) for v in payload.values()):
            rows = [{"name": name,
                     **{key: value for key, value in values.items()
                        if not str(key).startswith("_")}}
                    for name, values in payload.items()]
            headers = []
            for row in rows:
                for key in row:
                    if key not in headers:
                        headers.append(key)
            sparse = (len(rows) >= 2
                      and max(len(row) for row in rows) < 0.7 * len(headers))
            if sparse:
                lines = []
                for name, values in payload.items():
                    lines.append("#" * min(level + 1, 6) + " " + _escape(str(name)))
                    lines.append("")
                    body = _render(values, level + 1)
                    while body and not body[-1]:
                        body.pop()
                    lines.extend(body)
                    lines.append("")
                return lines
            return _table(rows)
        if not payload:
            return ["_none_"]
        scalars = [(key, value) for key, value in payload.items()
                   if not isinstance(value, (dict, list)) or not value]
        containers = [(key, value) for key, value in payload.items()
                      if isinstance(value, (dict, list)) and value]
        lines = []
        if scalars:
            lines.append("| field | value |")
            lines.append("|---|---|")
            lines.extend(f"| {_cell(key)} | {_cell(value)} |"
                         for key, value in scalars)
            lines.append("")
        for key, value in containers:
            lines.append("#" * min(level + 1, 6) + " " + _heading(key))
            lines.append("")
            body = _render(value, level + 1)
            while body and not body[-1]:
                body.pop()
            lines.extend(body)
            lines.append("")
        return lines
    if isinstance(payload, list):
        if payload and all(isinstance(v, dict) for v in payload):
            return _table([dict(row) for row in payload])
        return [f"- {_cell(v)}" for v in payload] or ["_none_"]
    return [_cell(payload)]


def to_markdown(results, title="Espressbench Report"):
    lines = [f"# {title}", ""]
    for section, payload in results.items():
        lines.append(f"## {_heading(section)}")
        lines.append("")
        body = _render(payload, 2)
        while body and not body[-1]:
            body.pop()
        lines.extend(body)
        lines.append("")
    return "\n".join(lines)


def report_sections(results):
    suites = results.get("suites", {})
    sections = {"Device": results.get("device", {}), "Suites": {}}
    for name, payload in suites.items():
        sections["Suites"][name] = {k: v for k, v in payload.items()
                                    if not isinstance(v, (list, dict))}
    if "fuzz" in suites and suites["fuzz"].get("results"):
        sections["Fuzz cases"] = {row["name"]: {k: v for k, v in row.items() if k != "name"}
                                  for row in suites["fuzz"]["results"]}
    if isinstance(results.get("log"), dict):
        sections["Device log"] = results["log"]
    return sections


def chaos_sections(report):
    seen = {}
    fault_rows = {}
    for row in report.get("results", []):
        name = row["fault"]
        seen[name] = seen.get(name, 0) + 1
        key = name if seen[name] == 1 else f"{name} #{seen[name]}"
        fault_rows[key] = {k: v for k, v in row.items() if k != "fault"}
    return {"Chaos summary": {k: v for k, v in report.items() if k != "results"},
            "Faults": fault_rows}


def soak_sections(report):
    return {"Soak summary": {k: v for k, v in report.items() if k != "events"},
            "Events": report.get("events", [])}


def _collect_cases(results):
    cases = []
    device = results.get("device")
    if isinstance(device, dict) and "error" in device:
        cases.append(("device", "stats", False, str(device["error"])[:300]))
    suites = results.get("suites", results)
    for suite_name, payload in suites.items():
        if suite_name == "device":
            continue
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            for item in payload["results"]:
                name = item.get("name") or item.get("fault") or "case"
                ok = bool(item.get("ok", item.get("recovered", True)))
                message = "" if ok else json.dumps(
                    {k: v for k, v in item.items() if k not in ("name", "ok")},
                    default=str)[:300]
                cases.append((suite_name, str(name), ok, message))
        elif isinstance(payload, dict):
            ok = not suite_failed(suite_name, payload)
            message = "" if ok else json.dumps(payload, default=str)[:300]
            cases.append((suite_name, "summary", ok, message))
        else:
            cases.append((suite_name, "summary", True, ""))
    return cases


def to_junit(results):
    cases = _collect_cases(results)
    failures = sum(1 for _, _, ok, _ in cases if not ok)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="espbench" tests="{len(cases)}" failures="{failures}">',
    ]
    for suite, name, ok, message in cases:
        lines.append(f"  <testcase classname={quoteattr('espbench.' + suite)}"
                     f" name={quoteattr(name)}>")
        if not ok:
            lines.append(f"    <failure message={quoteattr(message)}/>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines)


def _json_safe(node):
    if isinstance(node, dict):
        return {key: _json_safe(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_json_safe(value) for value in node]
    if isinstance(node, float) and not math.isfinite(node):
        return None
    return node


def write_reports(results, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    json_path = out / "report.json"
    json_path.write_text(
        json.dumps(_json_safe(results), indent=2, default=str, allow_nan=False),
        encoding="utf-8")
    paths["json"] = str(json_path)
    md_path = out / "report.md"
    md_path.write_text(to_markdown(results), encoding="utf-8")
    paths["markdown"] = str(md_path)
    junit_path = out / "junit.xml"
    junit_path.write_text(to_junit(results), encoding="utf-8")
    paths["junit"] = str(junit_path)
    return paths
