import json
import time

from espbench.device import DeviceError


class ReplayError(ValueError):
    pass


def _as_bytes(payload):
    return payload.encode() if isinstance(payload, str) else payload


class RecordingDevice:
    def __init__(self, inner):
        self.inner = inner
        self.steps = []

    def stats(self):
        start = time.perf_counter()
        try:
            result = self.inner.stats()
        except DeviceError as exc:
            self.steps.append({"op": "stats", "error": str(exc)})
            raise
        self.steps.append({"op": "stats", "result": dict(result),
                           "latency_ms": round((time.perf_counter() - start) * 1000, 3)})
        return result

    def version(self):
        try:
            value = self.inner.version()
        except DeviceError as exc:
            self.steps.append({"op": "version", "error": str(exc)})
            raise
        self.steps.append({"op": "version", "result": value})
        return value

    def log(self):
        try:
            result = self.inner.log()
        except DeviceError as exc:
            self.steps.append({"op": "log", "error": str(exc)})
            raise
        self.steps.append({"op": "log", "result": dict(result)})
        return result

    def ping(self):
        try:
            value = self.inner.ping()
        except DeviceError as exc:
            self.steps.append({"op": "ping", "error": str(exc)})
            raise
        self.steps.append({"op": "ping", "result": round(float(value), 3)})
        return value

    def echo(self, payload):
        data = _as_bytes(payload)
        try:
            status = self.inner.echo(data)
        except DeviceError as exc:
            self.steps.append({"op": "echo", "payload": data.hex(), "error": str(exc)})
            raise
        self.steps.append({"op": "echo", "payload": data.hex(), "status": status})
        return status

    def mark(self, label):
        try:
            status = self.inner.mark(label)
        except DeviceError as exc:
            self.steps.append({"op": "mark", "label": label, "error": str(exc)})
            raise
        self.steps.append({"op": "mark", "label": label, "status": status})
        return status

    def set_sensor(self, mode):
        try:
            status = self.inner.set_sensor(mode)
        except DeviceError as exc:
            self.steps.append({"op": "set_sensor", "mode": mode, "error": str(exc)})
            raise
        self.steps.append({"op": "set_sensor", "mode": mode, "status": status})
        return status

    def restart(self):
        try:
            status = self.inner.restart()
        except DeviceError as exc:
            self.steps.append({"op": "restart", "error": str(exc)})
            raise
        self.steps.append({"op": "restart", "status": status})
        return status


class ReplayDevice:
    def __init__(self, steps):
        self.steps = list(steps)
        self.index = 0

    def _next(self, op):
        if self.index >= len(self.steps):
            raise ReplayError(f"fixture exhausted while calling {op!r}")
        step = self.steps[self.index]
        if step["op"] != op:
            raise ReplayError(f"step {self.index}: expected {step['op']!r}, got {op!r}")
        self.index += 1
        return step

    def stats(self):
        step = self._next("stats")
        if "error" in step:
            raise DeviceError(step["error"])
        result = step.get("result")
        if not isinstance(result, dict):
            raise ReplayError(
                f"step {self.index - 1}: fixture step 'stats' needs a result object")
        return dict(result)

    def version(self):
        step = self._next("version")
        if "error" in step:
            raise DeviceError(step["error"])
        result = step.get("result")
        if not isinstance(result, str) or not result:
            raise ReplayError(
                f"step {self.index - 1}: fixture step 'version' needs a string result")
        return result

    def log(self):
        step = self._next("log")
        if "error" in step:
            raise DeviceError(step["error"])
        result = step.get("result")
        if not isinstance(result, dict):
            raise ReplayError(
                f"step {self.index - 1}: fixture step 'log' needs a result object")
        return dict(result)

    def ping(self):
        step = self._next("ping")
        if "error" in step:
            raise DeviceError(step["error"])
        result = step.get("result")
        if not isinstance(result, (int, float)):
            raise ReplayError(
                f"step {self.index - 1}: fixture step 'ping' needs a numeric result")
        return result

    def echo(self, payload):
        step = self._next("echo")
        expected = _as_bytes(payload).hex()
        if step.get("payload") != expected:
            raise ReplayError(
                f"step {self.index - 1}: payload mismatch "
                f"(fixture {step.get('payload')}, got {expected})"
            )
        if "error" in step:
            raise DeviceError(step["error"])
        if "status" not in step:
            raise ReplayError(f"step {self.index - 1}: fixture step 'echo' needs a status")
        return step["status"]

    def mark(self, label):
        step = self._next("mark")
        if "error" in step:
            raise DeviceError(step["error"])
        return step.get("status", True)

    def set_sensor(self, mode):
        step = self._next("set_sensor")
        if "error" in step:
            raise DeviceError(step["error"])
        return step.get("status", True)

    def restart(self):
        step = self._next("restart")
        if "error" in step:
            raise DeviceError(step["error"])
        return step.get("status", True)

    @property
    def remaining(self):
        return len(self.steps) - self.index


def save_steps(steps, path, run=None):
    payload = {"version": 1, "steps": steps}
    if run is not None:
        payload["run"] = run
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def load_fixture(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ReplayError("fixture must be a JSON object with version and steps")
    if payload.get("version") != 1:
        raise ReplayError(f"unsupported fixture version: {payload.get('version')}")
    if not isinstance(payload.get("steps"), list):
        raise ReplayError("fixture steps must be a list")
    for index, step in enumerate(payload["steps"]):
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            raise ReplayError(f"steps[{index}] must be an object with a string op field")
    run = payload.get("run")
    if run is not None and not isinstance(run, dict):
        raise ReplayError("fixture run metadata must be an object")
    return payload


def load_steps(path):
    return load_fixture(path)["steps"]


def _step_signature(step):
    op = step.get("op")
    if "error" in step:
        return (op, "error", str(step["error"]))
    if op == "echo":
        return (op, "echo", f"payload={step.get('payload')} status={step.get('status')}")
    if op in ("mark", "set_sensor", "restart"):
        subject = step.get("label") or step.get("mode") or "-"
        return (op, "control", f"{subject} status={step.get('status')}")
    if op == "stats":
        result = step.get("result")
        sensor = result.get("sensor") if isinstance(result, dict) else None
        return (op, "stats", f"sensor={sensor}")
    if op == "version":
        return (op, "version", str(step.get("result")))
    if op == "log":
        result = step.get("result")
        entries = result.get("log") if isinstance(result, dict) else None
        count = len(entries) if isinstance(entries, list) else 0
        return (op, "log", f"entries={count}")
    if op == "ping":
        return (op, "ping", "ok")
    stable = {key: value for key, value in step.items() if key != "latency_ms"}
    return (op, "raw", json.dumps(stable, sort_keys=True, default=str))


def _fmt_signature(signature):
    op, kind, detail = signature
    text = f"{op} error={detail}" if kind == "error" else f"{op} {detail}"
    return text if len(text) <= 60 else text[:57] + "..."


def diff_steps(before, after):
    signatures_before = [_step_signature(step) for step in before]
    signatures_after = [_step_signature(step) for step in after]
    rows = []
    matched = 0
    changed = 0
    shared = min(len(signatures_before), len(signatures_after))
    for index in range(shared):
        if signatures_before[index] == signatures_after[index]:
            matched += 1
        else:
            changed += 1
            rows.append({
                "step": index,
                "op": signatures_before[index][0],
                "before": _fmt_signature(signatures_before[index]),
                "after": _fmt_signature(signatures_after[index]),
                "verdict": "changed",
            })
    for index in range(shared, len(signatures_before)):
        rows.append({"step": index, "op": signatures_before[index][0],
                     "before": _fmt_signature(signatures_before[index]),
                     "after": "-", "verdict": "only_before"})
    for index in range(shared, len(signatures_after)):
        rows.append({"step": index, "op": signatures_after[index][0], "before": "-",
                     "after": _fmt_signature(signatures_after[index]),
                     "verdict": "only_after"})
    summary = {
        "steps_before": len(before),
        "steps_after": len(after),
        "matched": matched,
        "changed": changed,
        "only_before": len(before) - shared,
        "only_after": len(after) - shared,
    }
    return rows, summary


def fixtures_differ(summary):
    return bool(summary["changed"] or summary["only_before"]
                or summary["only_after"])
