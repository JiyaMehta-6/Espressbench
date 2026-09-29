from espbench.device import DeviceError
from espbench.fuzz import PAYLOADS, run
from espbench.simulation import FakeDevice


def test_fuzz_clean_device_passes():
    result = run(FakeDevice(seed=2))
    assert result["total"] == len(PAYLOADS)
    assert result["passed"] is True
    assert result["errors"] == 0
    assert result["reboots"] == 0
    assert all(row["ok"] for row in result["results"])


def test_fuzz_flags_http_500():
    device = FakeDevice(error_payloads=[b"boom"])
    result = run(device, extra=[b"boom"])
    assert result["passed"] is False
    assert result["errors"] == 1
    bad = next(row for row in result["results"] if row["name"] == "custom_0")
    assert bad["status"] == 500
    assert bad["ok"] is False


def test_fuzz_detects_reboot_via_boot_count():
    device = FakeDevice(reboot_payloads=[b"crashme"])
    result = run(device, extra=[b"crashme"])
    assert result["passed"] is False
    assert result["reboots"] == 1
    row = next(r for r in result["results"] if r["name"] == "custom_0")
    assert row["rebooted"] is True
    assert row["ok"] is False
    assert device.boot_count == 2


def test_fuzz_reboot_counts_even_when_echo_raises():
    class _Rebooter:
        def __init__(self):
            self.boot = 1

        def stats(self):
            return {"boot_count": self.boot}

        def echo(self, payload):
            if payload == b"x":
                self.boot += 1
                raise DeviceError("connection reset")
            return 200

    result = run(_Rebooter(), extra=[b"x"])
    assert result["reboots"] == 1
    assert result["errors"] == 1
    assert result["passed"] is False


def test_fuzz_settle_zero_is_fast():
    result = run(FakeDevice(seed=4), settle=0.0)
    assert result["total"] == len(PAYLOADS)


def test_fuzz_missing_boot_count_is_conservative_not_a_crash():
    class _NoBoot:
        @staticmethod
        def stats():
            return {}

        @staticmethod
        def echo(payload):
            return 200

    result = run(_NoBoot(), extra=[b"x"])
    assert result["total"] == len(PAYLOADS) + 1
    assert result["reboots"] > 0
    assert result["passed"] is False


def test_fuzz_stats_recovered_after_blip_counts_as_reboot():
    class _Blip:
        def __init__(self):
            self.calls = 0

        def stats(self):
            self.calls += 1
            if self.calls == 1:
                raise DeviceError("blip")
            return {"boot_count": 1}

        @staticmethod
        def echo(payload):
            return 200

    result = run(_Blip(), extra=[b"x"])
    assert result["reboots"] >= 1
    assert result["passed"] is False


def test_fuzz_bytes_counts_encoded_size():
    result = run(FakeDevice(seed=5), extra=["\u2603"])
    row = next(r for r in result["results"] if r["name"] == "custom_0")
    assert row["bytes"] == 3
