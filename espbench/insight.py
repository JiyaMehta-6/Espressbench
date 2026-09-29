from espbench.hil import suite_failed


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def hints(report):
    items = []
    if not isinstance(report, dict):
        return items
    suites = report.get("suites")
    suites = suites if isinstance(suites, dict) else {}
    device = report.get("device")
    if isinstance(device, dict):
        error = device.get("error")
        if isinstance(error, str) and error:
            items.append(f"warn: device unreachable ({error})")
        elif device.get("sensor") not in (None, "ok"):
            items.append(f"warn: on-device sensor reports {device['sensor']}")
    mentioned = set()
    latency = suites.get("latency")
    if isinstance(latency, dict):
        errors = _number(latency.get("errors"))
        if errors is not None and errors > 0:
            items.append(f"warn: latency dropped {int(errors)} pings")
            mentioned.add("latency")
        p50 = _number(latency.get("p50"))
        p95 = _number(latency.get("p95"))
        if p50 and p95 is not None and p95 >= 3 * p50:
            items.append(
                f"warn: tail latency - p95 {p95:g} ms is "
                f"{round(p95 / p50, 1):g}x p50 {p50:g} ms")
    memory = suites.get("memory")
    if isinstance(memory, dict):
        if memory.get("leak_detected"):
            items.append("warn: memory leak - mem_free trends down over the run")
            mentioned.add("memory")
        elif memory.get("device_restarted"):
            items.append("warn: device restarted during the memory suite")
            mentioned.add("memory")
    fuzz = suites.get("fuzz")
    if isinstance(fuzz, dict):
        reboots = _number(fuzz.get("reboots"))
        errors = _number(fuzz.get("errors"))
        if reboots is not None and reboots > 0:
            items.append(
                f"warn: device rebooted {int(reboots)}x under hostile payloads")
            mentioned.add("fuzz")
        elif errors is not None and errors > 0:
            items.append(f"warn: {int(errors)} fuzz payloads returned errors")
            mentioned.add("fuzz")
    for name, payload in suites.items():
        if name in mentioned or not isinstance(payload, dict):
            continue
        if suite_failed(name, payload):
            items.append(f"warn: suite {name} failed")
            mentioned.add(name)
    chaos = report.get("chaos")
    if isinstance(chaos, dict):
        if chaos.get("all_recovered") is False:
            total = chaos.get("total") or 0
            recovered = chaos.get("recovered") or 0
            items.append(
                f"warn: {total - recovered} of {total} faults left the device "
                "unrecovered")
        if chaos.get("all_faults_effective") is False:
            items.append("info: at least one fault had no effect - "
                         "verify the proxy sits in the device path")
    soak = report.get("soak")
    if isinstance(soak, dict):
        reboots = _number(soak.get("reboots"))
        errors = _number(soak.get("errors"))
        failures = _number(soak.get("failures"))
        if reboots is not None and reboots > 0:
            items.append(f"warn: device rebooted {int(reboots)}x during soak")
        if errors is not None and errors > 0:
            items.append(f"warn: soak hit {int(errors)} device errors")
        if failures and failures > 0 and not reboots and not errors:
            items.append(f"warn: {int(failures)} soak iterations failed")
    return items
