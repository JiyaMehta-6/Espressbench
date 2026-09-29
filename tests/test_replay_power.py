import json

import pytest

from espbench.device import DeviceError
from espbench.fuzz import PAYLOADS
from espbench.fuzz import run as fuzz_run
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
    ReplayError,
    diff_steps,
    fixtures_differ,
    load_fixture,
    load_steps,
    save_steps,
)
from espbench.simulation import FakeDevice

CSV = "time_ms,mA\n0,100\n1000,150\n2000,200\n3000,150\n4000,100\n"
MARKERS = "time_ms,label\n0,wifi\n2000,sensor\n3000,sleep\n"


def test_parse_and_summarize():
    rows = parse(CSV)
    assert len(rows) == 5
    stats = summarize(rows)
    assert stats["samples"] == 5
    assert stats["peak_ma"] == 200
    assert stats["mean_ma"] == 140
    assert stats["duration_s"] == 4.0


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse("not,a,log\nheader,rows\n")


def test_energy_and_battery_life():
    rows = parse(CSV)
    energy = energy_mah(rows)
    assert energy > 0
    hours = battery_life_hours(rows, 1000)
    assert hours == pytest.approx(1000 / 140, abs=0.01)
    assert battery_life_hours([(0, 0), (1000, 0)], 1000) is None


def test_attribute_windows():
    rows = parse(CSV)
    markers = parse_markers(MARKERS)
    attributed = attribute(rows, markers)
    assert [row["label"] for row in attributed] == ["wifi", "sensor", "sleep"]
    assert all(row["mean_ma"] > 0 for row in attributed)
    assert attributed[0]["duration_s"] == 2.0


def test_record_replay_roundtrip(tmp_path):
    device = FakeDevice(seed=7)
    recorder = RecordingDevice(device)
    recorded = fuzz_run(recorder)
    fixture = tmp_path / "session.json"
    save_steps(recorder.steps, fixture)
    steps = load_steps(fixture)
    assert steps == json.loads(fixture.read_text())["steps"]
    replayed = fuzz_run(ReplayDevice(steps))
    assert replayed == recorded
    assert replayed["total"] == len(PAYLOADS)


def test_replay_detects_payload_mismatch():
    device = ReplayDevice([{"op": "echo", "payload": "aa", "status": 200}])
    with pytest.raises(ReplayError):
        device.echo(b"bb")


def test_replay_detects_exhausted_fixture():
    device = ReplayDevice([])
    with pytest.raises(ReplayError):
        device.stats()


def test_replay_surfaces_recorded_error():
    device = ReplayDevice([{"op": "ping", "error": "timeout"}])
    with pytest.raises(DeviceError):
        device.ping()


def test_load_steps_rejects_unknown_version(tmp_path):
    fixture = tmp_path / "bad.json"
    fixture.write_text(json.dumps({"version": 99, "steps": []}))
    with pytest.raises(ReplayError):
        load_steps(fixture)


def test_load_steps_rejects_non_object_payload(tmp_path):
    fixture = tmp_path / "array.json"
    fixture.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ReplayError):
        load_steps(fixture)


def test_load_steps_rejects_missing_steps(tmp_path):
    fixture = tmp_path / "nosteps.json"
    fixture.write_text(json.dumps({"version": 1}))
    with pytest.raises(ReplayError):
        load_steps(fixture)


def test_parse_sorts_unsorted_rows():
    rows = parse("time_ms,mA\n1000,100\n0,50\n500,75\n")
    assert [stamp for stamp, _ in rows] == [0, 500, 1000]
    assert summarize(rows)["duration_s"] == 1.0
    assert energy_mah(rows) > 0


def test_recording_device_forwards_control_ops():
    calls = []

    class _Inner:
        def mark(self, label):
            calls.append(("mark", label))
            return True

        def set_sensor(self, mode):
            calls.append(("sensor", mode))
            return True

        def restart(self):
            calls.append(("restart",))
            return True

    recorder = RecordingDevice(_Inner())
    recorder.mark("boot")
    recorder.set_sensor("hang")
    recorder.restart()
    assert calls == [("mark", "boot"), ("sensor", "hang"), ("restart",)]
    assert [step["op"] for step in recorder.steps] == ["mark", "set_sensor", "restart"]


def test_replay_control_op_surfaces_recorded_error():
    device = ReplayDevice([{"op": "mark", "label": "x", "error": "down"}])
    with pytest.raises(DeviceError):
        device.mark("x")


def test_battery_life_rejects_non_positive_capacity():
    rows = parse(CSV)
    with pytest.raises(ValueError, match="capacity"):
        battery_life_hours(rows, 0)
    with pytest.raises(ValueError, match="capacity"):
        battery_life_hours(rows, -100)


def test_load_steps_rejects_malformed_step(tmp_path):
    fixture = tmp_path / "step.json"
    fixture.write_text(json.dumps({"version": 1, "steps": [42]}))
    with pytest.raises(ReplayError, match=r"steps\[0\]"):
        load_steps(fixture)


def test_load_steps_rejects_step_without_op(tmp_path):
    fixture = tmp_path / "noop.json"
    fixture.write_text(json.dumps({"version": 1, "steps": [{"label": "x"}]}))
    with pytest.raises(ReplayError, match=r"steps\[0\]"):
        load_steps(fixture)


def test_recording_device_records_control_op_status():
    class _Inner:
        def mark(self, label):
            return False

        def set_sensor(self, mode):
            return True

        def restart(self):
            return True

    recorder = RecordingDevice(_Inner())
    assert recorder.mark("boot") is False
    assert recorder.steps == [{"op": "mark", "label": "boot", "status": False}]
    replay = ReplayDevice(recorder.steps)
    assert replay.mark("boot") is False


def test_replay_rejects_step_missing_result():
    device = ReplayDevice([{"op": "stats"}])
    with pytest.raises(ReplayError, match="needs a result"):
        device.stats()


def test_replay_rejects_step_missing_status():
    device = ReplayDevice([{"op": "echo", "payload": "6161"}])
    with pytest.raises(ReplayError, match="needs a status"):
        device.echo(b"aa")


def test_record_and_replay_version_and_log():
    class _Inner:
        def version(self):
            return "1.0"

        def log(self):
            return {"fw": "1.0", "log": ["BOOT boot_count=1"]}

    recorder = RecordingDevice(_Inner())
    assert recorder.version() == "1.0"
    assert recorder.log()["log"] == ["BOOT boot_count=1"]
    assert [step["op"] for step in recorder.steps] == ["version", "log"]
    replay = ReplayDevice(recorder.steps)
    assert replay.version() == "1.0"
    assert replay.log() == {"fw": "1.0", "log": ["BOOT boot_count=1"]}


def test_replay_version_errors():
    with pytest.raises(DeviceError, match="404"):
        ReplayDevice([{"op": "version", "error": "404"}]).version()
    with pytest.raises(ReplayError, match="string result"):
        ReplayDevice([{"op": "version", "result": 5}]).version()
    with pytest.raises(ReplayError, match="result object"):
        ReplayDevice([{"op": "log", "result": [1]}]).log()


def test_diff_steps_identical_fixtures():
    steps = [{"op": "echo", "payload": "aa", "status": 200},
             {"op": "stats", "result": {"sensor": "ok", "mem_free": 1},
              "latency_ms": 5.0}]
    rows, summary = diff_steps(steps, json.loads(json.dumps(steps)))
    assert rows == []
    assert summary["matched"] == 2
    assert fixtures_differ(summary) is False


def test_diff_steps_ignores_volatile_fields():
    before = [{"op": "stats", "result": {"sensor": "ok", "mem_free": 100,
                                         "fw": "0.1.0"}, "latency_ms": 1.0},
              {"op": "ping", "result": 2.5}]
    after = [{"op": "stats", "result": {"sensor": "ok", "mem_free": 900,
                                        "fw": "0.2.0"}, "latency_ms": 9.9},
             {"op": "ping", "result": 7.7}]
    rows, summary = diff_steps(before, after)
    assert rows == []
    assert not fixtures_differ(summary)


def test_diff_steps_flags_changed_echo_status():
    before = [{"op": "echo", "payload": "aa", "status": 200}]
    after = [{"op": "echo", "payload": "aa", "status": 500}]
    rows, summary = diff_steps(before, after)
    assert summary["changed"] == 1
    assert rows[0]["before"] == "echo payload=aa status=200"
    assert rows[0]["after"] == "echo payload=aa status=500"
    assert fixtures_differ(summary)


def test_diff_steps_reports_error_and_length_differences():
    before = [{"op": "echo", "payload": "aa", "error": "timeout"},
              {"op": "ping", "result": 1.0}]
    after = [{"op": "echo", "payload": "aa", "error": "reset"}]
    rows, summary = diff_steps(before, after)
    assert summary["changed"] == 1
    assert summary["only_before"] == 1
    assert rows[0]["before"] == "echo error=timeout"
    assert rows[1]["verdict"] == "only_before"
    rows, summary = diff_steps(after, before)
    assert summary["only_after"] == 1
    rows, summary = diff_steps([{"op": "ping", "result": 1.0}],
                               [{"op": "echo", "payload": "aa", "status": 200}])
    assert summary["changed"] == 1
    assert rows[0]["op"] == "ping"


def test_diff_steps_truncates_long_rows():
    payload = "ab" * 500
    rows, _ = diff_steps([{"op": "echo", "payload": payload, "status": 200}],
                         [{"op": "echo", "payload": payload, "status": 500}])
    assert len(rows[0]["before"]) <= 61
    assert rows[0]["before"].endswith("...")


def test_save_steps_run_metadata(tmp_path):
    fixture = tmp_path / "f.json"
    save_steps([{"op": "ping", "result": 1.0}], fixture,
               run={"suites": ["fuzz"], "params": {"fuzz": {"settle": 0.0}}})
    payload = load_fixture(fixture)
    assert payload["run"] == {"suites": ["fuzz"], "params": {"fuzz": {"settle": 0.0}}}
    assert payload["steps"] == [{"op": "ping", "result": 1.0}]
    assert load_steps(fixture) == payload["steps"]


def test_save_steps_without_metadata(tmp_path):
    fixture = tmp_path / "f.json"
    save_steps([], fixture)
    payload = load_fixture(fixture)
    assert "run" not in payload
    assert payload["steps"] == []


def test_load_fixture_rejects_bad_run_metadata(tmp_path):
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps({"version": 1, "steps": [], "run": "x"}))
    with pytest.raises(ReplayError, match="run metadata"):
        load_fixture(fixture)
