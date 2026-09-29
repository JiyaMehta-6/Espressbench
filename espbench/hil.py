import time

from espbench.fuzz import run as fuzz_run
from espbench.latency import run as latency_run
from espbench.memory import run as memory_run

SUITES = {
    "latency": latency_run,
    "memory": memory_run,
    "fuzz": fuzz_run,
}

SOAK_EVENT_CAP = 50


def suite_failed(name, payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("failed") or payload.get("leak_detected"):
        return True
    if payload.get("passed") is False:
        return True
    if payload.get("all_recovered") is False:
        return True
    if payload.get("all_faults_effective") is False:
        return True
    if name == "latency" and payload.get("errors"):
        return True
    return False


def run_suites(device, suites=("latency", "memory", "fuzz"), params=None):
    params = params or {}
    suites = list(suites)
    unknown = [name for name in suites if name not in SUITES]
    if unknown:
        raise ValueError(f"unknown suite {unknown[0]!r}; expected one of {sorted(SUITES)}")
    if not suites:
        raise ValueError("at least one suite is required")
    results = {"suites": {}}
    stats_error = None
    try:
        results["device"] = device.stats()
    except Exception as exc:
        stats_error = str(exc)
        results["device"] = {"error": stats_error}
    device_payload = results["device"]
    if stats_error is None and isinstance(device_payload, dict) \
            and not device_payload.get("fw"):
        try:
            device_payload["fw"] = device.version()
        except Exception:
            pass
    if stats_error is not None:
        try:
            device.ping()
        except Exception:
            return results
    for name in suites:
        results["suites"][name] = SUITES[name](device, **params.get(name, {}))
    _attach_log(results, device)
    return results


def _attach_log(results, device):
    device_payload = results.get("device")
    failed = isinstance(device_payload, dict) and "error" in device_payload
    failed = failed or any(suite_failed(name, payload)
                           for name, payload in results.get("suites", {}).items())
    if not failed:
        return
    try:
        results["log"] = device.log()
    except Exception:
        pass


def _soak_event(events, iteration, kind, detail):
    if len(events) < SOAK_EVENT_CAP:
        events.append({"iteration": iteration, "kind": kind, "detail": str(detail)[:300]})
        return 0
    return 1


def run_soak(device, hours=0.0, interval=60.0, suites=None, params=None):
    if hours < 0:
        raise ValueError("hours must be non-negative")
    if interval < 0:
        raise ValueError("interval must be non-negative")
    suites = list(suites or ())
    params = params or {}
    if suites:
        unknown = [name for name in suites if name not in SUITES]
        if unknown:
            raise ValueError(
                f"unknown suite {unknown[0]!r}; expected one of {sorted(SUITES)}")
    started = time.time()
    deadline = started + hours * 3600.0
    iterations = 0
    failures = 0
    errors = 0
    reboots = 0
    dropped = 0
    events = []
    boot_count = None
    while True:
        iterations += 1
        iteration_failed = False
        stats = {}
        if suites:
            results = run_suites(device, suites=suites, params=params)
            stats = results.get("device") if isinstance(results.get("device"), dict) \
                else {}
            if "error" in stats:
                errors += 1
                iteration_failed = True
                dropped += _soak_event(events, iterations, "device_error",
                                       stats["error"])
            failed_names = [name for name, payload in results.get("suites", {}).items()
                            if suite_failed(name, payload)]
            if failed_names:
                iteration_failed = True
                dropped += _soak_event(events, iterations, "suite_failure",
                                       ", ".join(failed_names))
        else:
            try:
                stats = device.stats()
                if not isinstance(stats, dict):
                    stats = {}
            except Exception as exc:
                errors += 1
                iteration_failed = True
                stats = {}
                dropped += _soak_event(events, iterations, "stats_error", exc)
            try:
                device.ping()
            except Exception as exc:
                errors += 1
                iteration_failed = True
                dropped += _soak_event(events, iterations, "ping_error", exc)
        boot = stats.get("boot_count")
        if isinstance(boot, int):
            if boot_count is not None and boot > boot_count:
                reboots += 1
                iteration_failed = True
                dropped += _soak_event(events, iterations, "reboot",
                                       f"boot_count {boot_count} -> {boot}")
            boot_count = boot
        if iteration_failed:
            failures += 1
        if time.time() >= deadline:
            break
        time.sleep(min(interval, max(0.0, deadline - time.time())))
    passed = failures == 0 and reboots == 0
    return {
        "hours_planned": hours,
        "elapsed_s": round(time.time() - started, 3),
        "iterations": iterations,
        "failures": failures,
        "errors": errors,
        "reboots": reboots,
        "events_dropped": dropped,
        "passed": passed,
        "events": events,
    }
