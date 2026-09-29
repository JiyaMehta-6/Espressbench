import time

from espbench.device import DeviceError

PAYLOADS = [
    ("empty", b""),
    ("long_string", b"A" * 8192),
    ("format_specifiers", b"%s%s%n%x"),
    ("null_bytes", b"\x00" * 256),
    ("broken_json", b'{"broken":'),
    ("sql_injection", b"' OR 1=1 --"),
    ("ansi_escape", b"\x1b[31mred\x1b[0m"),
    ("crlf_injection", b"value\r\nX-Injected: 1"),
    ("utf8_edge", "\u2603".encode() * 100),
    ("deep_nesting", b"[" * 200 + b"]" * 200),
    ("negative_length", b"Content-Length: -1"),
    ("huge_numeric", b"9" * 500),
]


def run(device, extra=(), settle=0.0):
    cases = [(name, payload) for name, payload in PAYLOADS]
    cases.extend((f"custom_{i}", p) for i, p in enumerate(extra))
    results = []
    errors = 0
    reboots = 0
    for name, payload in cases:
        entry = {"name": name, "bytes": len(payload)}
        try:
            before = device.stats()
        except DeviceError:
            before = None
        try:
            entry["status"] = device.echo(payload)
        except DeviceError as exc:
            entry["status"] = f"error: {exc}"
            errors += 1
        else:
            if entry["status"] != 200:
                errors += 1
        if settle:
            time.sleep(settle)
        try:
            after = device.stats()
            rebooted = bool(before and after["boot_count"] != before["boot_count"])
        except DeviceError:
            rebooted = True
        if rebooted:
            reboots += 1
        entry["rebooted"] = rebooted
        entry["ok"] = bool(entry["status"] == 200 and not rebooted)
        results.append(entry)
    return {
        "total": len(results),
        "errors": errors,
        "reboots": reboots,
        "passed": errors == 0 and reboots == 0,
        "results": results,
    }
