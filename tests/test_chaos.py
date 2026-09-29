import socket

import pytest

from espbench.chaos import (
    FaultController,
    FaultProxy,
    parse_schedule,
    run_chaos,
    run_schedule,
)
from espbench.cli import main
from espbench.simulation import EchoServer


def _echo_probe(host, port):
    def probe():
        try:
            with socket.create_connection((host, port), timeout=1) as sock:
                sock.sendall(b"ping")
                return sock.recv(64) == b"ping"
        except OSError:
            return False

    return probe


def test_controller_rejects_unknown_mode():
    controller = FaultController()
    with pytest.raises(ValueError):
        controller.set("meteor")
    controller.set("delay", delay_ms=50, corrupt_rate=0.2)
    assert controller.snapshot() == ("delay", 50, 0.2)


def test_controller_rejects_out_of_range_values():
    controller = FaultController()
    with pytest.raises(ValueError, match="delay_ms"):
        controller.set("delay", delay_ms=-5)
    with pytest.raises(ValueError, match="corrupt_rate"):
        controller.set("corrupt", corrupt_rate=2.0)
    with pytest.raises(ValueError, match="corrupt_rate"):
        controller.set("corrupt", corrupt_rate=-0.1)


@pytest.mark.parametrize("kwargs, message", [
    ({"duration": -1}, "duration"),
    ({"recovery_timeout": -1}, "recovery_timeout"),
    ({"interval": -1}, "interval"),
    ({"delay_ms": -1}, "delay_ms"),
    ({"corrupt_rate": 1.5}, "corrupt_rate"),
    ({"corrupt_rate": -0.1}, "corrupt_rate"),
])
def test_run_chaos_validates_before_touching_proxy(kwargs, message):
    with pytest.raises(ValueError, match=message):
        run_chaos(None, None, faults=("refuse",), **kwargs)


def test_proxy_passes_traffic_in_normal_mode(echo_server):
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        assert probe() is True


def test_proxy_refuse_fails_then_recovers(echo_server):
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        proxy.set_fault("refuse")
        assert probe() is False
        proxy.set_fault("normal")
        assert probe() is True


def test_run_chaos_all_faults_recover(echo_server):
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        report = run_chaos(proxy, probe,
                           faults=("refuse", "delay", "corrupt", "cut"),
                           duration=0.25, recovery_timeout=2.0, interval=0.05,
                           delay_ms=100, corrupt_rate=1.0)
    assert report["total"] == 4
    assert report["all_recovered"] is True
    assert report["recovered"] == 4
    by_fault = {row["fault"]: row for row in report["results"]}
    assert by_fault["refuse"]["fault_took_effect"] is True
    assert by_fault["refuse"]["healthy_under_fault"] is False
    assert by_fault["delay"]["fault_took_effect"] is True
    assert by_fault["delay"]["healthy_under_fault"] is True
    assert by_fault["corrupt"]["fault_took_effect"] is True
    assert by_fault["cut"]["fault_took_effect"] is True
    assert all(row["recovery_time_s"] is not None for row in report["results"])
    assert report["faults_effective"] == 4
    assert report["all_faults_effective"] is True


def test_run_chaos_reports_ineffective_fault_when_duration_zero(echo_server):
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        report = run_chaos(proxy, probe, faults=("refuse",),
                           duration=0.0, recovery_timeout=1.0, interval=0.05)
    assert report["all_recovered"] is True
    assert report["all_faults_effective"] is False
    assert report["faults_effective"] == 0


def test_run_chaos_single_fault_is_fast(echo_server):
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        report = run_chaos(proxy, probe, faults=("refuse",),
                           duration=0.2, recovery_timeout=1.0, interval=0.05)
    assert report["total"] == 1
    assert report["all_recovered"] is True


def test_proxy_stop_without_start_does_not_hang():
    proxy = FaultProxy("127.0.0.1", 9)
    proxy.stop()


def test_echo_server_stop_without_start_does_not_hang():
    server = EchoServer()
    server.stop()


def test_parse_schedule_units_and_modes():
    assert parse_schedule("refuse:2s,normal:1s") == [("refuse", 2.0),
                                                     ("normal", 1.0)]
    assert parse_schedule("cut:500ms") == [("cut", 0.5)]
    assert parse_schedule("delay:1m") == [("delay", 60.0)]
    assert parse_schedule("refuse:0.25") == [("refuse", 0.25)]
    assert parse_schedule(" REFUSE:2S , normal:1s ") == [("refuse", 2.0),
                                                         ("normal", 1.0)]


def test_parse_schedule_rejects_bad_input():
    with pytest.raises(ValueError, match="at least one phase"):
        parse_schedule("")
    with pytest.raises(ValueError, match="at least one phase"):
        parse_schedule(" , ")
    with pytest.raises(ValueError, match="needs a duration"):
        parse_schedule("refuse")
    with pytest.raises(ValueError, match="unknown fault mode"):
        parse_schedule("meteor:1s")
    with pytest.raises(ValueError, match="positive"):
        parse_schedule("refuse:0")
    with pytest.raises(ValueError):
        parse_schedule("refuse:abc")


@pytest.mark.parametrize("kwargs, message", [
    ({"recovery_timeout": -1}, "recovery_timeout"),
    ({"interval": -1}, "interval"),
    ({"delay_ms": -1}, "delay_ms"),
    ({"corrupt_rate": 1.5}, "corrupt_rate"),
])
def test_run_schedule_validates_before_touching_proxy(kwargs, message):
    with pytest.raises(ValueError, match=message):
        run_schedule(None, None, [("refuse", 1.0)], **kwargs)


def test_run_schedule_rejects_empty_phases():
    with pytest.raises(ValueError, match="at least one phase"):
        run_schedule(None, None, [])


def test_run_schedule_phases_measure_recovery(echo_server):
    phases = parse_schedule("refuse:0.15,normal:0.15,cut:0.15,normal:0.15")
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        report = run_schedule(proxy, probe, phases, recovery_timeout=2.0,
                              interval=0.05, delay_ms=100, corrupt_rate=1.0)
    assert report["total"] == 2
    assert report["all_recovered"] is True
    assert report["all_faults_effective"] is True
    assert report["schedule"] == ["refuse:0.15s", "normal:0.15s",
                                  "cut:0.15s", "normal:0.15s"]
    by_fault = {row["fault"]: row for row in report["results"]}
    assert by_fault["refuse"]["healthy_under_fault"] is False
    assert by_fault["refuse"]["recovery_time_s"] is not None
    assert by_fault["cut"]["recovered"] is True
    assert all(row["duration_s"] > 0 for row in report["results"])


def test_run_schedule_delay_phase_records_latency(echo_server):
    phases = parse_schedule("delay:0.2,normal:0.1")
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        report = run_schedule(proxy, probe, phases, recovery_timeout=2.0,
                              interval=0.05, delay_ms=100, corrupt_rate=1.0)
    entry = report["results"][0]
    assert entry["fault_took_effect"] is True
    assert entry["healthy_under_fault"] is True
    assert entry["recovered"] is True


def test_run_schedule_trailing_fault_gets_final_recovery(echo_server):
    phases = parse_schedule("refuse:0.15")
    with FaultProxy(echo_server.host, echo_server.port) as proxy:
        probe = _echo_probe(proxy.host, proxy.port)
        report = run_schedule(proxy, probe, phases, recovery_timeout=2.0,
                              interval=0.05)
    entry = report["results"][0]
    assert entry["recovered"] is True
    assert entry["recovery_time_s"] is not None
    assert report["all_recovered"] is True


def test_chaos_schedule_via_cli(capsys):
    code = main(["chaos", "--sim", "--schedule", "refuse:0.1,normal:0.1",
                 "--recovery-timeout", "2.0"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Schedule" in out
    assert "refuse:0.1s" in out
    assert "all_recovered" in out


def test_chaos_schedule_invalid_via_cli(capsys):
    code = main(["chaos", "--sim", "--schedule", "meteor:1s"])
    assert code == 2
    assert "unknown fault mode" in capsys.readouterr().err
