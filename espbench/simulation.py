import random
import socketserver
import time

from espbench.device import DeviceError


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(4096)
            if not data:
                break
            try:
                self.request.sendall(data)
            except OSError:
                break


class EchoServer:
    def __init__(self, host="127.0.0.1", port=0):
        self.server = socketserver.ThreadingTCPServer((host, port), _EchoHandler)
        self.server.daemon_threads = True
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self._thread = None

    def start(self):
        if self._thread is not None:
            return self
        import threading

        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._thread is not None:
            self.server.shutdown()
            self._thread.join(timeout=5)
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


class FakeDevice:
    def __init__(self, latency_ms=10.0, jitter_ms=2.0, leak_bytes_per_s=0.0,
                 error_payloads=(), reboot_payloads=(), seed=0):
        self.latency_ms = float(latency_ms)
        self.jitter_ms = float(jitter_ms)
        self.leak_bytes_per_s = float(leak_bytes_per_s)
        self.error_payloads = {self._norm(p) for p in error_payloads}
        self.reboot_payloads = {self._norm(p) for p in reboot_payloads}
        self.rng = random.Random(seed)
        self.boot_count = 1
        self.start = time.time()
        self.mem_free_start = 120_000
        self.restarts = 0
        self.sensor_mode = "ok"
        self.clock_offset = 0

    @staticmethod
    def _norm(payload):
        return payload.encode() if isinstance(payload, str) else payload

    def _uptime(self):
        return time.time() - self.start + self.clock_offset

    def stats(self):
        uptime = self._uptime()
        return {
            "uptime_s": int(uptime),
            "boot_count": self.boot_count,
            "mem_free": int(self.mem_free_start - self.leak_bytes_per_s * uptime),
            "mem_alloc": 90_000,
            "sensor": self.sensor_mode,
            "simulated": True,
        }

    def version(self):
        return "sim-0.1.0"

    def log(self):
        return {"fw": "sim-0.1.0", "log": ["sim boot"]}

    def ping(self):
        return max(0.1, self.latency_ms + self.rng.gauss(0, self.jitter_ms))

    def echo(self, payload):
        data = self._norm(payload)
        if data in self.reboot_payloads:
            self._reboot()
            raise DeviceError("connection reset: device rebooted")
        if data in self.error_payloads:
            return 500
        if self.sensor_mode == "hang":
            raise DeviceError("timeout: sensor hang simulated")
        return 200

    def mark(self, label):
        return True

    def set_sensor(self, mode):
        self.sensor_mode = mode
        return True

    def restart(self):
        self._reboot()
        return True

    def _reboot(self):
        self.boot_count += 1
        self.start = time.time()
        self.restarts += 1
