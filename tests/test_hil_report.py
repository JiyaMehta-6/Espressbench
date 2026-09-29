import xml.etree.ElementTree as ET

import pytest

from espbench.hil import SUITES, run_soak, run_suites
from espbench.report import to_junit, to_markdown, write_reports
from espbench.simulation import FakeDevice


def test_run_suites_sim():
    results = run_suites(FakeDevice(seed=1), suites=("latency",),
                         params={"latency": {"n": 8, "warmup": 2}})
    assert results["device"]["simulated"] is True
    assert results["suites"]["latency"]["n"] == 8
    assert set(results["suites"]) == {"latency"}


def test_run_suites_unknown_suite():
    with pytest.raises(ValueError, match="unknown suite"):
        run_suites(FakeDevice(), suites=("power",))


def test_run_suites_captures_device_stats_failure():
    class _Broken:
        def stats(self):
            raise RuntimeError("device unreachable")

        def ping(self):
            return 1.0

    results = run_suites(_Broken(), suites=("latency",),
                         params={"latency": {"n": 2, "warmup": 0}})
    assert "error" in results["device"]
    assert results["suites"]["latency"]["n"] == 2


def test_run_suites_skips_suites_when_device_is_dead():
    class _Dead:
        def stats(self):
            raise RuntimeError("device unreachable")

        def ping(self):
            raise RuntimeError("device unreachable")

    results = run_suites(_Dead(), suites=("latency", "memory"))
    assert "error" in results["device"]
    assert results["suites"] == {}


def test_run_suites_validates_before_touching_device():
    class _Probe:
        def __init__(self):
            self.called = False

        def stats(self):
            self.called = True
            return {}

    device = _Probe()
    with pytest.raises(ValueError, match="unknown suite"):
        run_suites(device, suites=("nope",))
    assert device.called is False


def test_run_suites_rejects_empty_suite_list():
    with pytest.raises(ValueError, match="at least one suite"):
        run_suites(FakeDevice(), suites=())


def test_run_suites_fills_fw_from_version():
    results = run_suites(FakeDevice(seed=1), suites=("latency",),
                         params={"latency": {"n": 2, "warmup": 0}})
    assert results["device"]["fw"] == "sim-0.1.0"


def test_run_suites_survives_version_failure(monkeypatch):
    def boom(self):
        raise RuntimeError("no /version endpoint")

    monkeypatch.setattr(FakeDevice, "version", boom)
    results = run_suites(FakeDevice(seed=1), suites=("latency",),
                         params={"latency": {"n": 2, "warmup": 0}})
    assert "fw" not in results["device"]
    assert results["suites"]["latency"]["n"] == 2


def test_run_suites_keeps_existing_fw():
    device = FakeDevice(seed=1)
    device.stats = lambda: {"fw": "9.9", "uptime_s": 1}

    def boom():
        raise RuntimeError("must not be called")

    device.version = boom
    results = run_suites(device, suites=("latency",),
                         params={"latency": {"n": 2, "warmup": 0}})
    assert results["device"]["fw"] == "9.9"


def test_run_suites_attaches_log_on_failure():
    device = FakeDevice(seed=1, error_payloads=[b""])
    results = run_suites(device, suites=("fuzz",))
    assert results["suites"]["fuzz"]["passed"] is False
    assert results["log"]["log"] == ["sim boot"]


def test_run_suites_healthy_run_has_no_log():
    results = run_suites(FakeDevice(seed=1), suites=("latency",),
                         params={"latency": {"n": 2, "warmup": 0}})
    assert "log" not in results


def test_run_suites_log_failure_is_harmless():
    class _NoLog:
        def stats(self):
            return {"uptime_s": 1, "boot_count": 1}

        def ping(self):
            return 1.0

        def echo(self, payload):
            return 500

    results = run_suites(_NoLog(), suites=("fuzz",))
    assert results["suites"]["fuzz"]["passed"] is False
    assert "log" not in results


def test_run_soak_single_pass_is_healthy():
    report = run_soak(FakeDevice(seed=1), hours=0, interval=0)
    assert report["iterations"] == 1
    assert report["failures"] == 0
    assert report["events"] == []
    assert report["passed"] is True
    assert report["events_dropped"] == 0


def test_run_soak_repeats_until_deadline():
    report = run_soak(FakeDevice(seed=1), hours=0.00001, interval=0)
    assert report["iterations"] >= 2
    assert report["elapsed_s"] >= 0


def test_run_soak_counts_errors_and_reboots():
    class _Flaky:
        def __init__(self):
            self.calls = 0

        def stats(self):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("transient drop")
            return {"boot_count": 1 if self.calls < 3 else 2}

        def ping(self):
            return 1.0

    report = run_soak(_Flaky(), hours=0.00001, interval=0)
    assert report["iterations"] >= 3
    assert report["errors"] == 1
    assert report["reboots"] == 1
    assert report["failures"] == 2
    assert report["passed"] is False
    kinds = [event["kind"] for event in report["events"]]
    assert "stats_error" in kinds
    assert "reboot" in kinds


def test_run_soak_with_suites_counts_suite_failures():
    device = FakeDevice(seed=1, error_payloads=[b""])
    report = run_soak(device, hours=0, interval=0, suites=("fuzz",),
                      params={"fuzz": {"settle": 0.0}})
    assert report["iterations"] == 1
    assert report["failures"] == 1
    assert report["passed"] is False
    assert report["events"][0]["kind"] == "suite_failure"
    assert "fuzz" in report["events"][0]["detail"]


def test_run_soak_validates_inputs():
    with pytest.raises(ValueError, match="hours"):
        run_soak(FakeDevice(), hours=-1)
    with pytest.raises(ValueError, match="interval"):
        run_soak(FakeDevice(), interval=-1)
    with pytest.raises(ValueError, match="unknown suite"):
        run_soak(FakeDevice(), suites=("nope",))


def test_run_soak_event_cap():
    class _Dead:
        def stats(self):
            raise RuntimeError("down")

        def ping(self):
            raise RuntimeError("down")

    report = run_soak(_Dead(), hours=0.00001, interval=0)
    assert report["failures"] == report["iterations"]
    assert len(report["events"]) <= 50
    assert report["events_dropped"] == report["iterations"] * 2 \
        - len(report["events"])


def test_all_suites_registered():
    assert set(SUITES) == {"latency", "memory", "fuzz"}


def test_markdown_sections():
    text = to_markdown({"Device": {"simulated": True},
                        "Suites": {"latency": {"n": 8, "p95": 12.5}},
                        "List": [1, 2]})
    assert text.startswith("# ESP32 Eval Bench Report")
    assert "| p95 |" in text or "p95" in text
    assert "- 1" in text


def test_junit_reports_failures():
    results = {"suites": {"memory": {"leak_detected": True},
                          "latency": {"n": 5}}}
    xml = to_junit(results)
    assert 'failures="1"' in xml
    assert "<failure" in xml
    assert "memory" in xml


def test_junit_clean_run():
    results = {"suites": {"latency": {"n": 5}}}
    xml = to_junit(results)
    assert 'failures="0"' in xml


def test_junit_valid_xml_when_message_contains_quotes():
    results = {"suites": {"fuzz": {"passed": False, "results": [
        {"name": "long_string", "ok": False, "bytes": 8,
         "status": 'error: timeout "reading"', "error": "boom"}]}}}
    xml = to_junit(results)
    root = ET.fromstring(xml)
    assert root.get("failures") == "1"
    assert root.find("testcase/failure") is not None


def test_junit_marks_latency_errors_as_failure():
    xml = to_junit({"suites": {"latency": {"n": 5, "errors": 3, "mean": 1.0}}})
    assert 'failures="1"' in xml


def test_junit_includes_device_error_case():
    xml = to_junit({"device": {"error": "unreachable"}, "suites": {"latency": {"n": 5}}})
    assert 'failures="1"' in xml
    assert "unreachable" in xml


def test_junit_marks_unrecovered_chaos_flat_payload():
    xml = to_junit({"chaos": {"total": 2, "recovered": 1, "all_recovered": False}})
    assert 'failures="1"' in xml


def test_junit_matches_run_exit_code_contract():
    clean = to_junit({"device": {"uptime_s": 1},
                      "suites": {"latency": {"n": 5, "errors": 0}}})
    assert 'failures="0"' in clean


def test_write_reports(tmp_path):
    results = {"suites": {"latency": {"n": 5}}}
    paths = write_reports(results, tmp_path)
    for key in ("json", "markdown", "junit"):
        assert key in paths
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "junit.xml").exists()


def test_suite_failed_flags_device_restarted():
    from espbench.hil import suite_failed

    assert suite_failed("memory", {"device_restarted": True,
                                   "leak_detected": False}) is True
    assert suite_failed("memory", {"device_restarted": False,
                                   "leak_detected": False}) is False


def test_run_suites_rejects_unknown_param():
    with pytest.raises(ValueError, match="unknown parameter"):
        run_suites(FakeDevice(seed=1), suites=("latency",),
                   params={"latency": {"bogus": 1}})


def test_run_suites_rejects_non_object_params():
    with pytest.raises(ValueError, match="must be an object"):
        run_suites(FakeDevice(seed=1), suites=("latency",),
                   params={"latency": "junk"})


def test_run_suites_reports_progress():
    seen = []
    run_suites(FakeDevice(seed=1), suites=("latency", "fuzz"),
               params={"latency": {"n": 3, "warmup": 1}}, progress=seen.append)
    assert seen == ["suite latency (1/2)", "suite fuzz (2/2)"]


def test_run_soak_emits_progress():
    seen = []
    report = run_soak(FakeDevice(seed=1), hours=0, interval=0,
                      progress=seen.append)
    assert report["iterations"] == 1
    assert seen and "iteration 1" in seen[0]


def test_soak_counts_reboot_when_boot_count_drops():
    class _Reflash:
        def __init__(self):
            self.calls = 0

        def stats(self):
            self.calls += 1
            return {"boot_count": 3 if self.calls < 2 else 1,
                    "uptime_s": 1, "mem_free": 100}

        @staticmethod
        def ping():
            return 1.0

    report = run_soak(_Reflash(), hours=0.0002, interval=0)
    assert report["reboots"] == 1
    assert report["passed"] is False
