import json
import math
import random
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

HIGHER_BETTER = {
    "recovered", "faults_effective", "mem_free", "hours", "bytes_per_s",
}
NEUTRAL = {
    "total", "n", "samples", "bytes", "uptime_s", "boot_count", "duration_s",
    "start_free", "end_free", "mem_alloc", "count",
    "iterations", "elapsed_s", "hours_planned",
}
BAD_BOOL = {
    "leak_detected": True,
    "device_restarted": True,
    "rebooted": True,
    "recovered": False,
    "fault_took_effect": False,
    "passed": False,
    "all_recovered": False,
    "all_faults_effective": False,
    "failed": True,
    "ok": False,
}
HARD_COUNTERS = {"errors", "reboots"}
FLAKY_CV = 0.25
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 42

_BUDGET_RE = re.compile(r"([A-Za-z0-9_.\-]+)\s*(<=|>=|=)?\s*(.*)")


def load_report(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} is not a non-empty report object")
    return data


def flatten(report):
    flat = {}

    def walk_list(items, prefix):
        if not items or not all(isinstance(item, dict) for item in items):
            return
        named = None
        for key in ("name", "fault", "label"):
            if all(key in item for item in items):
                named = key
                names = [str(item[key]) for item in items]
                if len(set(names)) == len(names):
                    for item in items:
                        walk(item, f"{prefix}.{item[key]}")
                    return
        if named is not None:
            for index, item in enumerate(items, 1):
                walk(item, f"{prefix}.{index}")

    def walk(node, prefix):
        if isinstance(node, dict):
            for key, value in node.items():
                name = str(key)
                if name.startswith("_"):
                    continue
                walk(value, f"{prefix}.{name}" if prefix else name)
        elif isinstance(node, list):
            walk_list(node, prefix)
        elif isinstance(node, bool):
            flat[prefix] = node
        elif isinstance(node, (int, float)):
            if math.isfinite(node):
                flat[prefix] = node

    walk(report, "")
    return flat


def firmware_version(report):
    device = report.get("device")
    if isinstance(device, dict):
        fw = device.get("fw")
        if isinstance(fw, str) and fw:
            return fw
    return None


def stamp_baseline(report, version, source):
    stamped = dict(report)
    stamped["_meta"] = {
        "espbench": version,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
    }
    return stamped


def _direction(leaf):
    if leaf in NEUTRAL:
        return "neutral"
    if leaf in HIGHER_BETTER:
        return "higher"
    return "lower"


def _repeat_distributions(report):
    repeat = report.get("repeat")
    if not isinstance(repeat, dict):
        return {}
    rows = repeat.get("metrics")
    if not isinstance(rows, list):
        return {}
    out = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("metric"), str) \
                and isinstance(row.get("values"), list) and row["values"]:
            out[row["metric"]] = row["values"]
    return out


def _coefficient_of_variation(values):
    if len(values) < 3:
        return 0.0
    mean = statistics.fmean(values)
    spread = statistics.pstdev(values)
    if mean == 0:
        return 0.0 if spread == 0 else float("inf")
    return spread / abs(mean)


def _bootstrap_ci(before_values, after_values):
    rng = random.Random(BOOTSTRAP_SEED)
    count_before = len(before_values)
    count_after = len(after_values)
    diffs = []
    for _ in range(BOOTSTRAP_SAMPLES):
        resampled_before = [before_values[rng.randrange(count_before)]
                            for _ in range(count_before)]
        resampled_after = [after_values[rng.randrange(count_after)]
                           for _ in range(count_after)]
        diffs.append(statistics.median(resampled_after)
                     - statistics.median(resampled_before))
    diffs.sort()
    low = diffs[int(0.025 * (BOOTSTRAP_SAMPLES - 1))]
    high = diffs[int(0.975 * (BOOTSTRAP_SAMPLES - 1))]
    return low, high


def _stat_verdict(leaf, direction, before_values, after_values,
                  worse_pct, tolerance):
    if leaf in HARD_COUNTERS or before_values is None or after_values is None:
        return None
    if len(before_values) < 3 or len(after_values) < 3:
        return None
    values = list(before_values) + list(after_values)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           for value in values):
        return None
    if (_coefficient_of_variation(before_values) > FLAKY_CV
            or _coefficient_of_variation(after_values) > FLAKY_CV):
        return "flaky"
    low, high = _bootstrap_ci(before_values, after_values)
    if direction == "lower":
        worse_significant = low > 0
        better_significant = high < 0
    else:
        worse_significant = high < 0
        better_significant = low > 0
    if worse_pct > tolerance:
        return "regression" if worse_significant else "ok"
    if worse_pct < 0:
        return "improved" if better_significant else "ok"
    return "ok"


def _compare_metric(path, before, after, tolerance, before_values=None,
                    after_values=None):
    leaf = path.rsplit(".", 1)[-1]
    if isinstance(before, bool) or isinstance(after, bool):
        if not (isinstance(before, bool) and isinstance(after, bool)):
            return "neutral", "-"
        if before == after:
            return "ok", "-"
        if leaf not in BAD_BOOL:
            return "neutral", "-"
        if after == BAD_BOOL[leaf]:
            return "regression", "worse"
        return "improved", "better"
    if after == before:
        return "ok", "0.0%"
    direction = _direction(leaf)
    if direction == "neutral":
        if before == 0:
            return "neutral", f"{after - before:+g}"
        delta_pct = (after - before) / abs(before) * 100.0
        return "neutral", f"{delta_pct:+.1f}%"
    if before == 0:
        delta = f"{after - before:+g}"
        worse = after > 0 if direction == "lower" else after < 0
        return ("regression" if worse else "improved"), delta
    delta_pct = (after - before) / abs(before) * 100.0
    delta = f"{delta_pct:+.1f}%"
    worse_pct = delta_pct if direction == "lower" else -delta_pct
    if worse_pct > tolerance:
        point = "regression"
    elif worse_pct < 0:
        point = "improved"
    else:
        point = "ok"
    stat = _stat_verdict(leaf, direction, before_values, after_values,
                         worse_pct, tolerance)
    if stat is not None:
        return stat, delta
    return point, delta


def compare_reports(before, after, tolerance=0.0):
    flat_before = flatten(before)
    flat_after = flatten(after)
    shared = [path for path in flat_before if path in flat_after]
    if not shared:
        raise ValueError("reports have no metrics in common")
    dist_before = _repeat_distributions(before)
    dist_after = _repeat_distributions(after)
    rows = []
    counts = {"regressions": 0, "improved": 0, "ok": 0, "neutral": 0,
              "flaky": 0, "new": 0, "missing": 0}
    for path in flat_before:
        if path in flat_after:
            verdict, delta = _compare_metric(path, flat_before[path],
                                             flat_after[path], tolerance,
                                             dist_before.get(path),
                                             dist_after.get(path))
            rows.append({"metric": path, "before": flat_before[path],
                         "after": flat_after[path], "delta": delta,
                         "verdict": verdict})
            counts["regressions" if verdict == "regression" else verdict] += 1
        else:
            rows.append({"metric": path, "before": flat_before[path], "after": "-",
                         "delta": "-", "verdict": "missing"})
            counts["missing"] += 1
    for path in flat_after:
        if path not in flat_before:
            rows.append({"metric": path, "before": "-", "after": flat_after[path],
                         "delta": "-", "verdict": "new"})
            counts["new"] += 1
    summary = {"checked": len(shared), **counts}
    return rows, summary


def parse_budget(spec):
    match = _BUDGET_RE.fullmatch(spec.strip())
    if not match:
        raise ValueError(
            f"invalid budget {spec!r}; use METRIC=LIMIT, METRIC<=LIMIT or METRIC>=LIMIT")
    path, operator, raw = match.group(1), match.group(2), match.group(3)
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        if operator not in (None, "="):
            raise ValueError(f"boolean budget {spec!r} cannot use {operator!r}")
        return path, "==", lowered == "true"
    try:
        limit = float(raw)
    except ValueError:
        raise ValueError(
            f"invalid budget value {raw.strip()!r} in {spec!r}; expected a number "
            "or true/false") from None
    if not math.isfinite(limit):
        raise ValueError(
            f"invalid budget value {raw.strip()!r} in {spec!r}; "
            "expected a finite number")
    if operator in (None, "="):
        return path, "<=", limit
    return path, operator, limit


def resolve_metric(flat, path):
    if path in flat:
        return path
    matches = [key for key in flat if key.endswith("." + path)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        near = [key for key in flat if path in key][:6]
        hint = f"; close metrics: {', '.join(near)}" if near else ""
        raise ValueError(f"unknown metric {path!r}{hint}")
    raise ValueError(
        f"ambiguous metric {path!r}; use a full path: {', '.join(matches[:6])}")


def load_budget_file(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} must be a non-empty JSON object of metric: limit")
    specs = []
    for key, value in data.items():
        if isinstance(value, bool):
            specs.append(f"{key}={str(value).lower()}")
        elif isinstance(value, (int, float)):
            specs.append(f"{key}={value}")
        elif isinstance(value, str):
            if not value.lstrip().startswith(("<=", ">=", "=")):
                raise ValueError(
                    f"budget {key!r}: string limits need an operator, e.g. \">=50\"")
            specs.append(f"{key}{value}")
        else:
            raise ValueError(
                f"budget {key!r}: limit must be a number, boolean or operator string")
    return specs


def check_budgets(report, specs):
    flat = flatten(report)
    if not flat:
        raise ValueError("report contains no metrics")
    rows = []
    failed = 0
    for spec in specs:
        path, operator, limit = parse_budget(spec)
        resolved = resolve_metric(flat, path)
        actual = flat[resolved]
        if isinstance(limit, bool):
            passed = isinstance(actual, bool) and actual == limit
        elif operator == "<=":
            passed = actual <= limit
        else:
            passed = actual >= limit
        if not passed:
            failed += 1
        display_limit = int(limit) if isinstance(limit, float) and limit.is_integer() \
            else limit
        rows.append({"budget": spec, "metric": resolved, "actual": actual,
                     "limit": display_limit,
                     "verdict": "pass" if passed else "FAIL"})
    summary = {"checked": len(rows), "passed": len(rows) - failed, "failed": failed}
    return rows, summary


def aggregate_runs(runs):
    if not runs:
        raise ValueError("aggregate_runs needs at least one run")
    if len(runs) == 1:
        return runs[0]
    flats = [flatten(run) for run in runs]
    usable = [(run, flat) for run, flat in zip(runs, flats) if run.get("suites")]
    if not usable:
        usable = list(zip(runs, flats))
    usable_flats = [flat for _, flat in usable]
    order = next((flat for flat in usable_flats if flat), flats[0])
    common = [path for path in order if all(path in flat for flat in usable_flats)]
    aggregated = {}
    distributions = {}
    for path in common:
        values = [flat[path] for flat in usable_flats]
        distributions[path] = values
        leaf = path.rsplit(".", 1)[-1]
        if all(isinstance(value, bool) for value in values):
            aggregated[path] = all(values)
        elif leaf in HARD_COUNTERS:
            aggregated[path] = max(values)
        else:
            aggregated[path] = statistics.median(values)

    def rebuild(node, prefix):
        if isinstance(node, dict):
            return {key: rebuild(value, f"{prefix}.{key}")
                    for key, value in node.items()}
        if isinstance(node, list):
            if node and all(isinstance(item, dict) for item in node):
                named = None
                for key in ("name", "fault", "label"):
                    if all(key in item for item in node):
                        named = key
                        names = [str(item[key]) for item in node]
                        if len(set(names)) == len(names):
                            return [rebuild(item, f"{prefix}.{item[key]}")
                                    for item in node]
                if named is not None:
                    return [rebuild(item, f"{prefix}.{index}")
                            for index, item in enumerate(node, 1)]
            return node
        if prefix in aggregated:
            return aggregated[prefix]
        return node

    base = usable[0][0]
    combined = {key: rebuild(value, key) for key, value in base.items()}
    device_error = next(
        (run["device"] for run in runs
         if isinstance(run.get("device"), dict) and "error" in run["device"]),
        None)
    if device_error is not None:
        combined["device"] = device_error
    if "log" not in combined:
        log_payload = next((run["log"] for run in runs if "log" in run), None)
        if log_payload is not None:
            combined["log"] = log_payload
    combined["repeat"] = {
        "count": len(runs),
        "metrics": [{"metric": path,
                     "median": statistics.median(distributions[path]),
                     "min": min(distributions[path]),
                     "max": max(distributions[path]),
                     "values": distributions[path]}
                    for path in common],
    }
    return combined


def _round_sig(value, digits=10):
    if value == 0:
        return 0.0
    return round(value, -int(math.floor(math.log10(abs(value)))) + digits - 1)


def derive_budgets(report, margin):
    if margin < 0:
        raise ValueError("margin must be >= 0")
    flat = flatten(report)
    budgets = {}
    for path in sorted(flat):
        value = flat[path]
        leaf = path.rsplit(".", 1)[-1]
        if leaf in NEUTRAL:
            continue
        if isinstance(value, bool):
            if leaf in BAD_BOOL and value != BAD_BOOL[leaf]:
                budgets[path] = value
            continue
        step = abs(value) * margin / 100.0
        if leaf in HIGHER_BETTER:
            budgets[path] = f">={_round_sig(value - step)}"
        else:
            budgets[path] = _round_sig(value + step)
    return budgets
