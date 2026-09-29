import random
import socket
import socketserver
import threading
import time

from espbench.device import Device
from espbench.simulation import EchoServer

MODES = ("normal", "refuse", "delay", "corrupt", "cut")


class FaultController:
    def __init__(self):
        self._lock = threading.Lock()
        self.mode = "normal"
        self.delay_ms = 200
        self.corrupt_rate = 0.5

    def set(self, mode, delay_ms=200, corrupt_rate=0.5):
        if mode not in MODES:
            raise ValueError(f"unknown fault mode {mode!r}; expected one of {MODES}")
        if delay_ms < 0:
            raise ValueError("delay_ms must be non-negative")
        if not 0.0 <= float(corrupt_rate) <= 1.0:
            raise ValueError("corrupt_rate must be between 0 and 1")
        with self._lock:
            self.mode = mode
            self.delay_ms = int(delay_ms)
            self.corrupt_rate = float(corrupt_rate)

    def snapshot(self):
        with self._lock:
            return self.mode, self.delay_ms, self.corrupt_rate


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        controller = self.server.controller
        mode, _, _ = controller.snapshot()
        if mode == "refuse":
            return
        try:
            upstream = socket.create_connection(self.server.upstream, timeout=5)
        except OSError:
            return
        client = self.request

        def pump(source, destination, outbound):
            try:
                while True:
                    data = source.recv(4096)
                    if not data:
                        break
                    active, delay_ms, corrupt_rate = controller.snapshot()
                    if outbound and active == "delay":
                        time.sleep(delay_ms / 1000.0)
                    if outbound and active == "corrupt" and random.random() < corrupt_rate:
                        head = bytes(b ^ 0x55 for b in data[:min(8, len(data))])
                        data = head + data[8:]
                    if outbound and active == "cut":
                        break
                    destination.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        threads = [
            threading.Thread(target=pump, args=(client, upstream, True), daemon=True),
            threading.Thread(target=pump, args=(upstream, client, False), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        upstream.close()


class _ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FaultProxy:
    def __init__(self, upstream_host, upstream_port, listen_port=0, listen_host="127.0.0.1"):
        self.controller = FaultController()
        self.server = _ProxyServer((listen_host, listen_port), _Handler)
        self.server.controller = self.controller
        self.server.upstream = (upstream_host, int(upstream_port))
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self._thread = None

    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._thread is not None:
            self.server.shutdown()
            self._thread.join(timeout=5)
        self.server.server_close()
        self.controller.set("normal")

    def set_fault(self, mode, **kwargs):
        self.controller.set(mode, **kwargs)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def run_chaos(proxy, probe, faults=("refuse", "delay", "corrupt", "cut"),
              duration=1.0, recovery_timeout=5.0, interval=0.1,
              delay_ms=200, corrupt_rate=0.5):
    if duration < 0:
        raise ValueError("duration must be non-negative")
    if recovery_timeout < 0:
        raise ValueError("recovery_timeout must be non-negative")
    if interval < 0:
        raise ValueError("interval must be non-negative")
    if delay_ms < 0:
        raise ValueError("delay_ms must be non-negative")
    if not 0.0 <= float(corrupt_rate) <= 1.0:
        raise ValueError("corrupt_rate must be between 0 and 1")
    results = []
    for mode in faults:
        entry = {"fault": mode}
        proxy.controller.set(mode, delay_ms=delay_ms, corrupt_rate=corrupt_rate)
        checks = []
        probe_ms = []
        deadline = time.time() + duration
        while time.time() < deadline:
            start_probe = time.perf_counter()
            try:
                checks.append(bool(probe()))
            except Exception:
                checks.append(False)
            probe_ms.append((time.perf_counter() - start_probe) * 1000)
            time.sleep(min(interval, max(0.0, deadline - time.time())))
        healthy = all(checks) if checks else None
        entry["healthy_under_fault"] = healthy
        entry["probe_ms_max"] = round(max(probe_ms), 2) if probe_ms else None
        if mode == "delay":
            entry["fault_took_effect"] = bool(probe_ms and max(probe_ms) >= delay_ms)
        else:
            entry["fault_took_effect"] = healthy is False
        proxy.controller.set("normal")
        start = time.time()
        recovered = False
        while time.time() - start < recovery_timeout:
            try:
                if probe():
                    recovered = True
                    break
            except Exception:
                pass
            time.sleep(interval)
        entry["recovered"] = recovered
        entry["recovery_time_s"] = round(time.time() - start, 3) if recovered else None
        results.append(entry)
    times = [e["recovery_time_s"] for e in results if e["recovery_time_s"] is not None]
    recovered = sum(1 for e in results if e["recovered"])
    effective = sum(1 for e in results if e["fault_took_effect"])
    return {
        "total": len(results),
        "recovered": recovered,
        "all_recovered": recovered == len(results),
        "faults_effective": effective,
        "all_faults_effective": effective == len(results) and bool(results),
        "worst_recovery_s": max(times) if times else None,
        "results": results,
    }


def _parse_duration(text):
    lowered = text.strip().lower()
    if lowered.endswith("ms"):
        value = float(lowered[:-2]) / 1000.0
    elif lowered.endswith("s"):
        value = float(lowered[:-1])
    elif lowered.endswith("m"):
        value = float(lowered[:-1]) * 60.0
    else:
        value = float(lowered)
    if value <= 0:
        raise ValueError(f"schedule duration {text!r} must be positive")
    return value


def parse_schedule(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("schedule must contain at least one phase; use "
                         'e.g. "refuse:2s,normal:1s,corrupt:500ms"')
    phases = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(
                f"schedule phase {chunk!r} needs a duration; use MODE:DURATION "
                'e.g. "refuse:2s" (modes: ' + ", ".join(MODES) + ")")
        mode, _, duration = chunk.partition(":")
        mode = mode.strip().lower()
        if mode not in MODES:
            raise ValueError(f"unknown fault mode {mode!r}; expected one of {MODES}")
        phases.append((mode, _parse_duration(duration)))
    if not phases:
        raise ValueError("schedule must contain at least one phase; use "
                         'e.g. "refuse:2s,normal:1s,corrupt:500ms"')
    return phases


def run_schedule(proxy, probe, phases, recovery_timeout=5.0, interval=0.1,
                 delay_ms=200, corrupt_rate=0.5):
    if recovery_timeout < 0:
        raise ValueError("recovery_timeout must be non-negative")
    if interval < 0:
        raise ValueError("interval must be non-negative")
    if delay_ms < 0:
        raise ValueError("delay_ms must be non-negative")
    if not 0.0 <= float(corrupt_rate) <= 1.0:
        raise ValueError("corrupt_rate must be between 0 and 1")
    if not phases:
        raise ValueError("schedule must contain at least one phase")
    results = []
    after_fault = False
    for mode, seconds in phases:
        proxy.controller.set(mode, delay_ms=delay_ms, corrupt_rate=corrupt_rate)
        checks = []
        probe_ms = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            start_probe = time.perf_counter()
            try:
                checks.append(bool(probe()))
            except Exception:
                checks.append(False)
            probe_ms.append((time.perf_counter() - start_probe) * 1000)
            time.sleep(min(interval, max(0.0, deadline - time.time())))
        if mode != "normal":
            healthy = all(checks) if checks else None
            entry = {"fault": mode, "duration_s": round(seconds, 3),
                     "healthy_under_fault": healthy,
                     "probe_ms_max": round(max(probe_ms), 2) if probe_ms else None}
            if mode == "delay":
                entry["fault_took_effect"] = bool(
                    probe_ms and max(probe_ms) >= delay_ms)
            else:
                entry["fault_took_effect"] = healthy is False
            entry["recovered"] = None
            entry["recovery_time_s"] = None
            results.append(entry)
            after_fault = True
        elif after_fault and results and results[-1]["recovered"] is None:
            entry = results[-1]
            start = time.time()
            window = min(seconds, recovery_timeout)
            while time.time() - start < window:
                try:
                    if probe():
                        entry["recovered"] = True
                        entry["recovery_time_s"] = round(time.time() - start, 3)
                        break
                except Exception:
                    pass
                time.sleep(interval)
            after_fault = False
    for entry in results:
        if entry["recovered"] is not None:
            continue
        proxy.controller.set("normal", delay_ms=delay_ms, corrupt_rate=corrupt_rate)
        start = time.time()
        while time.time() - start < recovery_timeout:
            try:
                if probe():
                    entry["recovered"] = True
                    entry["recovery_time_s"] = round(time.time() - start, 3)
                    break
            except Exception:
                pass
            time.sleep(interval)
        if entry["recovered"] is None:
            entry["recovered"] = False
    proxy.controller.set("normal")
    times = [e["recovery_time_s"] for e in results if e["recovery_time_s"] is not None]
    recovered = sum(1 for e in results if e["recovered"])
    effective = sum(1 for e in results if e["fault_took_effect"])
    return {
        "total": len(results),
        "recovered": recovered,
        "all_recovered": recovered == len(results),
        "faults_effective": effective,
        "all_faults_effective": effective == len(results) and bool(results),
        "worst_recovery_s": max(times) if times else None,
        "schedule": [f"{mode}:{round(seconds, 3):g}s" for mode, seconds in phases],
        "results": results,
    }


def echo_probe(host, port):
    def probe():
        try:
            with socket.create_connection((host, port), timeout=2) as sock:
                sock.sendall(b"ping")
                return sock.recv(64) == b"ping"
        except OSError:
            return False

    return probe


def run_target(host=None, port=80, sim=False, faults=(), schedule=None,
               duration=1.0, recovery_timeout=5.0, delay_ms=200,
               corrupt_rate=1.0):
    faults = list(faults)
    phases = parse_schedule(schedule) if schedule else None
    if phases is None and not faults:
        raise ValueError("--faults must name at least one fault mode")
    if not sim and not host:
        raise ValueError("--host is required (or pass --sim)")

    def dispatch(proxy, probe):
        if phases is not None:
            return run_schedule(proxy, probe, phases,
                                recovery_timeout=recovery_timeout,
                                delay_ms=delay_ms, corrupt_rate=corrupt_rate)
        return run_chaos(proxy, probe, faults=faults, duration=duration,
                         recovery_timeout=recovery_timeout,
                         delay_ms=delay_ms, corrupt_rate=corrupt_rate)

    if sim:
        server = EchoServer().start()
        try:
            with FaultProxy(server.host, server.port) as proxy:
                return dispatch(proxy, echo_probe(proxy.host, proxy.port))
        finally:
            server.stop()
    probe_timeout = max(5.0, delay_ms / 1000.0 + 2.0)
    with FaultProxy(host, port) as proxy:
        probe_device = Device(proxy.host, port=proxy.port, timeout=probe_timeout)

        def probe():
            return probe_device.ping()

        return dispatch(proxy, probe)
