import argparse
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path

from espbench import __version__
from espbench.chaos import run_target
from espbench.completion import completion_script
from espbench.device import Device
from espbench.fuzz import run as fuzz_run
from espbench.gate import (
    aggregate_runs,
    check_budgets,
    compare_reports,
    derive_budgets,
    firmware_version,
    flatten,
    load_budget_file,
    load_report,
    parse_budget,
    resolve_metric,
    stamp_baseline,
)
from espbench.hil import run_soak, run_suites, suite_failed
from espbench.insight import hints
from espbench.power import (
    attribute,
    battery_life_hours,
    energy_mah,
    parse,
    parse_markers,
    summarize,
)
from espbench.replay import (
    RecordingDevice,
    ReplayDevice,
    diff_steps,
    fixtures_differ,
    load_fixture,
    load_steps,
    save_steps,
)
from espbench.report import (
    chaos_sections,
    report_sections,
    soak_sections,
    to_markdown,
    write_reports,
)
from espbench.simulation import FakeDevice
from espbench.svg import badge_svg, histogram_svg


def _in_range(kind, low, high=None):
    def parse(text):
        try:
            value = kind(text)
        except ValueError:
            article = "an" if kind.__name__[:1].lower() in "aeiou" else "a"
            raise argparse.ArgumentTypeError(
                f"expected {article} {kind.__name__}") from None
        if value < low or (high is not None and value > high):
            bounds = (f"between {low} and {high}" if high is not None
                      else f"at least {low}")
            raise argparse.ArgumentTypeError(f"must be {bounds}")
        return value

    return parse


def _device(args):
    if args.sim:
        return FakeDevice(seed=1)
    if not args.host:
        raise ValueError("--host is required (or pass --sim)")
    return Device(args.host, port=args.port, timeout=args.timeout)


def _add_device_args(parser, host_required=True):
    parser.add_argument("--host", required=host_required, help="device IP")
    parser.add_argument("--port", type=_in_range(int, 1, 65535), default=80)
    parser.add_argument("--sim", action="store_true", help="run against the built-in simulator")
    parser.add_argument("--timeout", type=_in_range(float, 0.1, 60.0), default=5.0,
                        help="HTTP timeout in seconds (default: 5)")


def _out_path(out, default_name):
    path = Path(out)
    if str(out).endswith(("/", "\\")) or (path.exists() and path.is_dir()):
        path = path / default_name
    return path


def _run_params(args):
    return {
        "latency": {"n": args.latency_n},
        "memory": {"samples": args.memory_samples, "interval": args.memory_interval},
        "fuzz": {"settle": args.settle},
    }


def _split_suites(text):
    return [s.strip() for s in text.split(",") if s.strip()]


def _append_summary(markdown):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip("\n") + "\n\n")
    except OSError:
        return


def cmd_run(args):
    if args.fixture and (args.sim or args.host):
        raise ValueError("--fixture cannot be combined with --host or --sim")
    replay_devices = []
    if args.fixture:
        payload = load_fixture(args.fixture)
        steps = payload["steps"]
        meta = payload.get("run")
        suites = list(meta["suites"]) if isinstance(meta, dict) \
            and isinstance(meta.get("suites"), list) and meta["suites"] \
            else _split_suites(args.suites)
        params = meta.get("params") if isinstance(meta, dict) \
            and isinstance(meta.get("params"), dict) else _run_params(args)

        def make_device():
            device = ReplayDevice(steps)
            replay_devices.append(device)
            return device
    else:
        base_device = _device(args)
        suites = _split_suites(args.suites)
        params = _run_params(args)

        def make_device():
            return base_device

    recorders = []

    def make_recorder(device):
        if args.bundle is not None:
            recorder = RecordingDevice(device)
            recorders.append(recorder)
            return recorder
        return device

    def progress(message):
        print(f"run: {message}", file=sys.stderr, flush=True)

    runs = []
    for index in range(args.repeat):
        if args.repeat > 1:
            progress(f"run {index + 1}/{args.repeat}")
        runs.append(run_suites(make_recorder(make_device()), suites=suites,
                               params=params, progress=progress))
    remaining = max((device.remaining for device in replay_devices), default=0)
    results = aggregate_runs(runs)
    sections = report_sections(results)
    if args.fixture:
        sections["Fixture"] = {"steps": len(steps), "remaining": remaining}
    lines = hints(results)
    if lines:
        sections["Insights"] = lines
    text = to_markdown(sections)
    print(text)
    _append_summary(text)
    if args.out:
        paths = write_reports(results, args.out)
        if len(runs) > 1:
            runs_dir = Path(args.out) / "runs"
            runs_dir.mkdir(exist_ok=True)
            for index, run in enumerate(runs, 1):
                (runs_dir / f"report-{index:02d}.json").write_text(
                    json.dumps(run, indent=2, default=str), encoding="utf-8")
        print("wrote " + ", ".join(paths.values()))
    if recorders:
        path = args.bundle or (
            str(Path(args.out) / "session.json") if args.out else "session.json")
        steps = [step for recorder in recorders for step in recorder.steps]
        save_steps(steps, path, run={"suites": suites, "params": params})
        print(f"wrote fixture {path} ({len(steps)} steps)")
    if remaining:
        print(f"run: fixture has {remaining} unused steps - the suites and "
              "params did not consume the whole recording",
              file=sys.stderr, flush=True)
    if remaining or any(_run_failed(run) for run in runs):
        return 1
    return 0


def _run_failed(results):
    device = results.get("device")
    if isinstance(device, dict) and "error" in device:
        return True
    return any(suite_failed(name, payload)
               for name, payload in results.get("suites", {}).items())


def cmd_chaos(args):
    faults = [s.strip() for s in args.faults.split(",") if s.strip()]
    described = args.schedule or ",".join(faults)
    print(f"chaos: {described} (duration {args.duration:g}s, "
          f"recovery {args.recovery_timeout:g}s)", file=sys.stderr, flush=True)
    report = run_target(host=args.host, port=args.port, sim=args.sim,
                        faults=faults, schedule=args.schedule,
                        duration=args.duration,
                        recovery_timeout=args.recovery_timeout,
                        delay_ms=args.delay_ms,
                        corrupt_rate=args.corrupt_rate,
                        timeout=args.timeout,
                        progress=lambda message: print(
                            f"chaos: {message}", file=sys.stderr, flush=True))
    sections = chaos_sections(report)
    lines = hints({"chaos": report})
    if lines:
        sections["Insights"] = lines
    text = to_markdown(sections)
    print(text)
    _append_summary(text)
    if args.out:
        paths = write_reports({"chaos": report}, args.out)
        print("wrote " + ", ".join(paths.values()))
    if not report["all_recovered"] or not report["all_faults_effective"]:
        return 1
    return 0


def cmd_soak(args):
    device = _device(args)
    suites = _split_suites(args.suites) if args.suites else []
    report = run_soak(
        device, hours=args.hours, interval=args.interval,
        suites=suites, params=_run_params(args),
        progress=lambda message: print(f"soak: {message}", file=sys.stderr,
                                       flush=True))
    sections = soak_sections(report)
    lines = hints({"soak": report})
    if lines:
        sections["Insights"] = lines
    text = to_markdown(sections, title="Espressbench Soak")
    print(text)
    _append_summary(text)
    if args.out:
        paths = write_reports({"soak": report}, args.out)
        print("wrote " + ", ".join(paths.values()))
    return 0 if report["passed"] else 1


def cmd_replay_record(args):
    device = _device(args)
    recorder = RecordingDevice(device)
    outcome = fuzz_run(recorder, settle=args.settle)
    out = str(_out_path(args.out, "session.json"))
    save_steps(recorder.steps, out)
    text = to_markdown({"Recorded fuzz": {k: v for k, v in outcome.items()
                                          if k != "results"},
                        "Fixture": {"steps": len(recorder.steps), "path": out}})
    print(text)
    _append_summary(text)
    return 0 if outcome["passed"] else 1


def cmd_replay_run(args):
    steps = load_steps(args.fixture)
    device = ReplayDevice(steps)
    outcome = fuzz_run(device)
    sections = {
        "Replayed fuzz": {k: v for k, v in outcome.items() if k != "results"},
        "Cases": {row["name"]: {k: v for k, v in row.items() if k != "name"}
                  for row in outcome["results"]},
        "Fixture": {"steps": len(steps), "consumed": device.index,
                    "remaining": device.remaining},
    }
    text = to_markdown(sections)
    print(text)
    _append_summary(text)
    return 0 if outcome["passed"] and device.remaining == 0 else 1


def cmd_power(args):
    rows = parse(Path(args.csv).read_text(encoding="utf-8"))
    sections = {"Power summary": summarize(rows),
                "Energy": {"total_mah": round(energy_mah(rows), 6)}}
    if args.markers:
        markers = parse_markers(Path(args.markers).read_text(encoding="utf-8"))
        attributed = attribute(rows, markers)
        sections["Operations"] = {row["label"]: row for row in attributed}
    if args.capacity:
        sections["Battery projection"] = {
            "capacity_mah": args.capacity,
            "hours": battery_life_hours(rows, args.capacity),
        }
    text = to_markdown(sections, title="Power Report")
    print(text)
    _append_summary(text)
    if args.out:
        out = _out_path(args.out, "power_report.md")
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")


def cmd_baseline(args):
    report = load_report(args.report)
    flat = flatten(report)
    if not flat:
        raise ValueError(f"{args.report} contains no comparable metrics")
    if args.auto:
        budgets = derive_budgets(report, args.margin)
        if not budgets:
            raise ValueError(f"{args.report} contains no budgetable metrics")
        out = args.out or "budgets.json"
        Path(out).write_text(json.dumps(budgets, indent=2) + "\n", encoding="utf-8")
        text = to_markdown({"Budgets": {"source": args.report, "out": out,
                                        "margin_pct": args.margin,
                                        "count": len(budgets)}},
                           title="Espressbench Auto Budgets")
        print(text)
        _append_summary(text)
        return 0
    out = args.out or ("budgets.json" if args.auto else "baseline.json")
    out = str(_out_path(out, "budgets.json" if args.auto else "baseline.json"))
    stamped = stamp_baseline(report, __version__, args.report)
    Path(out).write_text(json.dumps(stamped, indent=2), encoding="utf-8")
    headlines = {path: flat[path] for path in
                 ("suites.latency.p95", "suites.fuzz.passed",
                  "suites.memory.leak_detected", "chaos.worst_recovery_s")
                 if path in flat}
    text = to_markdown({"Baseline": {"source": args.report, "out": out,
                                     "metrics": len(flat),
                                     "headlines": headlines}},
                       title="Espressbench Baseline")
    print(text)
    _append_summary(text)
    return 0


def cmd_compare(args):
    if not args.reports or len(args.reports) > 2:
        raise ValueError("compare takes one report (vs ./baseline.json) "
                         "or two reports (before after)")
    if len(args.reports) == 1:
        before_path, after_path = "baseline.json", args.reports[0]
        if not Path(before_path).exists():
            raise ValueError("baseline.json not found - run "
                             "'espbench baseline report.json' first, or pass "
                             "two reports to compare")
    else:
        before_path, after_path = args.reports
    before = load_report(before_path)
    after = load_report(after_path)
    fw_before = firmware_version(before)
    fw_after = firmware_version(after)
    if fw_before and fw_after and fw_before != fw_after and not args.allow_mismatch:
        raise ValueError(
            f"firmware mismatch: {before_path} is {fw_before} but {after_path} is "
            f"{fw_after}; pass --allow-mismatch to compare across firmware versions")
    rows, summary = compare_reports(before, after, args.tolerance)
    sections = {"Comparison": rows, "Summary": summary}
    if fw_before != fw_after and fw_before and fw_after:
        sections = {"Firmware": {"before": fw_before, "after": fw_after,
                                 "note": "different firmware compared "
                                         "(--allow-mismatch)"},
                    **sections}
    text = to_markdown(sections, title="Espressbench Comparison")
    print(text)
    _append_summary(text)
    if args.out:
        out = _out_path(args.out, "comparison.md")
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    return 1 if summary["regressions"] else 0


def cmd_diff(args):
    before = load_steps(args.before)
    after = load_steps(args.after)
    rows, summary = diff_steps(before, after)
    text = to_markdown({"Differences": rows, "Summary": summary},
                       title="Espressbench Fixture Diff")
    print(text)
    _append_summary(text)
    if args.out:
        out = _out_path(args.out, "diff.md")
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    return 1 if fixtures_differ(summary) else 0


def cmd_check(args):
    report = load_report(args.report)
    flat = flatten(report)
    specs = []
    if args.budgets:
        specs.extend(load_budget_file(args.budgets))
    specs.extend(args.budget)
    if not specs:
        raise ValueError("at least one --budget SPEC or a --budgets file is required")
    merged = {}
    for spec in specs:
        merged[resolve_metric(flat, parse_budget(spec)[0])] = spec
    rows, summary = check_budgets(report, list(merged.values()))
    text = to_markdown({"Budgets": rows, "Summary": summary},
                       title="Espressbench Budget Check")
    print(text)
    _append_summary(text)
    if args.out:
        out = _out_path(args.out, "check.md")
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    return 1 if summary["failed"] else 0


def cmd_insight(args):
    report = load_report(args.report)
    lines = hints(report)
    sections = {"Insights": lines or ["no hints - the report looks clean"]}
    text = to_markdown(sections, title="Espressbench Insights")
    print(text)
    _append_summary(text)
    if args.strict and any(line.startswith("warn:") for line in lines):
        return 1
    return 0


def _sample_values(report):
    suites = report.get("suites")
    suites = suites if isinstance(suites, dict) else {}
    latency = suites.get("latency")
    if isinstance(latency, dict):
        samples = latency.get("_samples")
        if isinstance(samples, list) and samples:
            return samples, "latency samples"
    repeat = report.get("repeat")
    if isinstance(repeat, dict):
        rows = repeat.get("metrics")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                values = row.get("values")
                if isinstance(values, list) and values:
                    metric = row.get("metric")
                    return values, f"repeat {metric} values" if metric else "repeat values"
    raise ValueError("report contains no sample values; run a latency suite "
                     "or repeat a run with --repeat")


def cmd_chart(args):
    report = load_report(args.report)
    values, label = _sample_values(report)
    svg = histogram_svg(values, title=args.title or label, unit=args.unit)
    out = _out_path(args.out, "report.svg") \
        if args.out else Path(args.report).with_suffix(".svg")
    out.write_text(svg, encoding="utf-8")
    text = f"wrote {out} ({len(values)} samples)"
    print(text)
    _append_summary(text)
    return 0


def cmd_badge(args):
    report = load_report(args.report)
    flat = flatten(report)
    if not flat:
        raise ValueError(f"{args.report} contains no metrics")
    path = resolve_metric(flat, args.metric)
    value = flat[path]
    if isinstance(value, bool):
        text = "pass" if value else "fail"
        color = args.color or ("brightgreen" if value else "red")
    else:
        text = f"{value:g}" if isinstance(value, float) else str(value)
        color = args.color or "blue"
    label = args.label or path.rsplit(".", 1)[-1]
    svg = badge_svg(label, text, color)
    if args.out:
        out = _out_path(args.out, "badge.svg")
        out.write_text(svg, encoding="utf-8")
        line = f"wrote {out}"
        print(line)
        _append_summary(line)
    else:
        print(svg)
    return 0


WORKFLOW = """name: espbench

on:
  push:
  pull_request:
  workflow_dispatch:

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e .
      - run: python -m pip install pytest ruff
      - run: python -m ruff check .
      - run: python -m pytest -q

  sim-report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e .
      - run: espbench run --sim --out reports/
      - run: espbench insight reports/report.json
      - run: espbench badge reports/report.json --metric p95 --out reports/badge.svg
      - uses: actions/upload-artifact@v4
        with:
          name: espbench-report
          path: reports/
"""


def cmd_init(args):
    workflow = Path(args.directory) / ".github" / "workflows" / "espbench.yml"
    if workflow.exists() and not args.force:
        raise ValueError(f"{workflow} already exists (pass --force to overwrite)")
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(WORKFLOW, encoding="utf-8")
    sections = {
        "Created": {"workflow": str(workflow)},
        "Next steps": [
            "git add .github && git commit -m 'add espbench CI'",
            "push, then watch the tests and sim-report jobs run free on GitHub",
            "the sim-report job posts its report to the step summary automatically",
        ],
    }
    text = to_markdown(sections, title="Espressbench Init")
    print(text)
    _append_summary(text)
    return 0


def cmd_doctor(args):
    rows = []
    rows.append({"check": "espbench", "status": "ok", "detail": f"v{__version__}"})
    rows.append({"check": "python", "status": "ok",
                 "detail": platform.python_version() + " on " + platform.system()})
    if importlib.util.find_spec("PySide6") is None:
        rows.append({"check": "gui", "status": "warn",
                     "detail": "PySide6 not installed; espbench gui is unavailable"})
    else:
        from importlib import metadata

        try:
            version = metadata.version("PySide6")
        except Exception:
            version = "installed"
        rows.append({"check": "gui", "status": "ok", "detail": f"PySide6 {version}"})
    for name in ("baseline.json", "budgets.json", "report.json"):
        rows.append({"check": name, "status": "ok" if Path(name).exists() else "skip",
                     "detail": "present" if Path(name).exists() else "not found"})
    workflows = sorted(Path(".github/workflows").glob("*.yml")) \
        if Path(".github/workflows").is_dir() else []
    rows.append({"check": "ci workflow", "status": "ok" if workflows else "skip",
                 "detail": ", ".join(p.name for p in workflows) or "not found "
                           "(run espbench init)"})
    if args.sim:
        rows.append({"check": "device", "status": "ok", "detail": "simulator selected"})
    elif args.host:
        device = Device(args.host, port=args.port, timeout=args.timeout)
        try:
            stats = device.stats()
            fw = stats.get("fw") if isinstance(stats, dict) else None
            if not fw:
                try:
                    fw = device.version()
                except Exception:
                    fw = "?"
            ping = device.ping()
            rows.append({"check": "device", "status": "ok",
                         "detail": f"{args.host}:{args.port} fw={fw} "
                                   f"ping={ping:.1f} ms"})
        except Exception as exc:
            rows.append({"check": "device", "status": "fail", "detail": str(exc)})
    else:
        rows.append({"check": "device", "status": "skip",
                     "detail": "pass --host to probe a device (or --sim)"})
    text = to_markdown({"Checks": rows}, title="Espressbench Doctor")
    print(text)
    _append_summary(text)
    return 1 if any(row["status"] == "fail" for row in rows) else 0


def cmd_gui(args):
    from espbench import gui

    return gui.main([])


def build_parser():
    parser = argparse.ArgumentParser(
        prog="espbench",
        description="Espressbench - HIL evaluation bench for Espressif WiFi firmware")
    parser.add_argument("--version", action="version", version=f"espbench {__version__}")
    parser.add_argument("--completions", choices=("bash", "zsh", "fish"),
                        help="print a shell completion script and exit "
                             "(e.g. source <(espbench --completions bash))")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run evaluation suites against a device")
    _add_device_args(run, host_required=False)
    run.add_argument("--suites", default="latency,memory,fuzz",
                     help="comma-separated: latency,memory,fuzz")
    run.add_argument("--latency-n", type=_in_range(int, 1), default=100)
    run.add_argument("--memory-samples", type=_in_range(int, 1), default=25)
    run.add_argument("--memory-interval", type=_in_range(float, 0.0), default=1.0)
    run.add_argument("--settle", type=_in_range(float, 0.0), default=0.0)
    run.add_argument("--repeat", type=_in_range(int, 1), default=1,
                     help="repeat the suites N times and aggregate (default: 1)")
    run.add_argument("--bundle", nargs="?", const="", default=None, metavar="PATH",
                     help="record a replay fixture of the whole run "
                          "(default: session.json, or out/session.json)")
    run.add_argument("--fixture",
                     help="replay a recorded bundle instead of a live device "
                          "(suite/params metadata from the fixture wins)")
    run.add_argument("--out")
    run.set_defaults(func=cmd_run)

    soak = sub.add_parser("soak", help="endurance loop: repeat health checks for N hours")
    _add_device_args(soak, host_required=False)
    soak.add_argument("--hours", type=_in_range(float, 0.0), default=1.0,
                      help="how long to soak (default: 1; 0 = single pass)")
    soak.add_argument("--interval", type=_in_range(float, 0.0), default=60.0,
                      help="seconds between iterations (default: 60)")
    soak.add_argument("--suites",
                      help="comma-separated suites to run each iteration "
                           "(default: stats+ping health check only)")
    soak.add_argument("--latency-n", type=_in_range(int, 1), default=100)
    soak.add_argument("--memory-samples", type=_in_range(int, 1), default=25)
    soak.add_argument("--memory-interval", type=_in_range(float, 0.0), default=1.0)
    soak.add_argument("--settle", type=_in_range(float, 0.0), default=0.0)
    soak.add_argument("--out")
    soak.set_defaults(func=cmd_soak)

    chaos = sub.add_parser("chaos", help="inject connection faults and measure recovery")
    _add_device_args(chaos, host_required=False)
    chaos.add_argument("--faults", default="refuse,delay,corrupt,cut",
                       help="comma-separated fault modes")
    chaos.add_argument("--schedule",
                       help='timed phase schedule, e.g. "refuse:2s,normal:1s,'
                            'cut:500ms" (replaces --faults/--duration timing)')
    chaos.add_argument("--duration", type=_in_range(float, 0.0), default=1.0)
    chaos.add_argument("--recovery-timeout", type=_in_range(float, 0.0), default=5.0)
    chaos.add_argument("--delay-ms", type=_in_range(int, 0), default=200)
    chaos.add_argument("--corrupt-rate", type=_in_range(float, 0.0, 1.0), default=1.0,
                       help="probability of corrupting each probe (default: always)")
    chaos.add_argument("--out")
    chaos.set_defaults(func=cmd_chaos)

    record = sub.add_parser("replay-record", help="record a fuzz session to a fixture")
    _add_device_args(record, host_required=False)
    record.add_argument("--out", required=True)
    record.add_argument("--settle", type=_in_range(float, 0.0), default=0.0)
    record.set_defaults(func=cmd_replay_record)

    replay = sub.add_parser("replay-run", help="replay a fixture without hardware")
    replay.add_argument("--fixture", required=True)
    replay.set_defaults(func=cmd_replay_run)

    power = sub.add_parser("power", help="analyse a current-log CSV")
    power.add_argument("--csv", required=True, help="logger CSV: time_ms,mA")
    power.add_argument("--markers", help="marker CSV: time_ms,label")
    power.add_argument("--capacity", type=_in_range(float, 0.0),
                       help="battery capacity in mAh (0 or omitted = no projection)")
    power.add_argument("--out")
    power.set_defaults(func=cmd_power)

    baseline = sub.add_parser("baseline", help="freeze a known-good report as a reference")
    baseline.add_argument("report", nargs="?", default="report.json")
    baseline.add_argument("--out",
                          help="output path (default: baseline.json, or "
                               "budgets.json with --auto)")
    baseline.add_argument("--auto", action="store_true",
                          help="derive budgets from the report instead of a baseline")
    baseline.add_argument("--margin", type=_in_range(float, 0.0), default=20.0,
                          help="budget margin %% around each metric (default: 20)")
    baseline.set_defaults(func=cmd_baseline)

    compare = sub.add_parser("compare",
                             help="diff reports; exit 1 on regression beyond tolerance")
    compare.add_argument("reports", nargs="*", metavar="REPORT",
                         help="one report (vs ./baseline.json) or two (before after)")
    compare.add_argument("--tolerance", type=_in_range(float, 0.0), default=0.0,
                         help="allowed regression %% before flagging")
    compare.add_argument("--allow-mismatch", action="store_true",
                         help="compare reports recorded on different firmware")
    compare.add_argument("--out")
    compare.set_defaults(func=cmd_compare)

    diff = sub.add_parser("diff",
                          help="compare two replay fixtures; exit 1 on differences")
    diff.add_argument("before", metavar="BEFORE")
    diff.add_argument("after", metavar="AFTER")
    diff.add_argument("--out")
    diff.set_defaults(func=cmd_diff)

    check = sub.add_parser("check", help="assert metrics against budgets; exit 1 on breach")
    check.add_argument("report")
    check.add_argument("--budget", action="append", default=[], metavar="SPEC",
                       help="METRIC=LIMIT, METRIC<=LIMIT or METRIC>=LIMIT (repeatable)")
    check.add_argument("--budgets", help="JSON file of {metric: limit}")
    check.add_argument("--out")
    check.set_defaults(func=cmd_check)

    insight = sub.add_parser(
        "insight", help="print advisory hints for a report "
                        "(exit 1 with --strict when warnings exist)")
    insight.add_argument("report")
    insight.add_argument("--strict", action="store_true",
                         help="exit 1 when any warn-level hint is found")
    insight.set_defaults(func=cmd_insight)

    chart = sub.add_parser("chart", help="render a histogram SVG from report samples")
    chart.add_argument("report")
    chart.add_argument("--out", help="SVG path (default: report.svg next to the report)")
    chart.add_argument("--title", help="chart title (default: the sample source)")
    chart.add_argument("--unit", default="", help="x-axis unit label, e.g. ms")
    chart.set_defaults(func=cmd_chart)

    badge = sub.add_parser("badge", help="render a status badge SVG for one metric")
    badge.add_argument("report")
    badge.add_argument("--metric", required=True,
                       help="metric to show, e.g. p95 or suites.latency.p95")
    badge.add_argument("--label", help="badge label (default: the metric leaf)")
    badge.add_argument("--color",
                       help="shields color name or hex (default: blue, "
                            "pass/fail green/red)")
    badge.add_argument("--out", help="write the SVG here instead of stdout")
    badge.set_defaults(func=cmd_badge)

    init = sub.add_parser("init",
                          help="scaffold the GitHub Actions workflow for this project")
    init.add_argument("directory", nargs="?", default=".",
                      help="project root (default: current directory)")
    init.add_argument("--force", action="store_true",
                      help="overwrite an existing workflow")
    init.set_defaults(func=cmd_init)

    doctor = sub.add_parser("doctor",
                            help="check environment, local files and device reachability")
    _add_device_args(doctor, host_required=False)
    doctor.set_defaults(func=cmd_doctor)

    gui_parser = sub.add_parser("gui", help="launch the desktop GUI")
    gui_parser.set_defaults(func=cmd_gui)

    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "completions", None):
            print(completion_script(args.completions, parser))
            return 0
        if not getattr(args, "command", None):
            parser.error("the following arguments are required: command")
        code = args.func(args)
    except (ValueError, KeyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0 if code is None else code


if __name__ == "__main__":
    sys.exit(main())
