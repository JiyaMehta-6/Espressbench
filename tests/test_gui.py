import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from espbench.gui import (  # noqa: E402
    ChaosTab,
    MainWindow,
    PowerTab,
    ReportsTab,
    RunWorker,
    SoakTab,
    SuitesTab,
    _save_device_settings,
)


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def suites_tab(app):
    return SuitesTab()


def test_mainwindow_builds(app):
    window = MainWindow()
    assert isinstance(window.suites_tab, SuitesTab)
    assert isinstance(window.power_tab, PowerTab)
    window.close()


def test_suites_tab_defaults(suites_tab):
    assert set(suites_tab.selected_suites()) == {"latency", "memory", "fuzz"}
    params = suites_tab.params()
    assert params["latency"]["n"] == 100
    assert params["memory"]["samples"] == 25


def test_worker_emits_results():
    captured = []
    worker = RunWorker("", 80, True, ["latency"],
                       {"latency": {"n": 6, "warmup": 1}})
    worker.finished.connect(captured.append)
    worker.run()
    assert captured and captured[0]["suites"]["latency"]["n"] == 6


def test_worker_reports_failure():
    captured = []
    worker = RunWorker("", 80, True, ["bogus"], {})
    worker.failed.connect(captured.append)
    worker.run()
    assert captured and "unknown suite" in captured[0]


def test_suites_tab_end_to_end(app, suites_tab):
    suites_tab.sim.setChecked(True)
    suites_tab.suite_boxes["memory"].setChecked(False)
    suites_tab.latency_n.setValue(5)
    suites_tab.on_run()
    deadline = time.time() + 30
    while not suites_tab.run_btn.isEnabled() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert suites_tab.run_btn.isEnabled()
    text = suites_tab.output.toPlainText()
    assert "Espressbench Report" in text
    assert suites_tab.export_btn.isEnabled()


def test_power_tab_analyse(app, tmp_path):
    tab = PowerTab()
    csv_path = tmp_path / "power.csv"
    csv_path.write_text("time_ms,mA\n0,100\n1000,200\n2000,150\n")
    tab.csv_path.setText(str(csv_path))
    tab.on_analyze()
    text = tab.output.toPlainText()
    assert "Power summary" in text
    assert "Battery projection" in text
    assert tab.export_btn.isEnabled()


def test_power_tab_export_disabled_when_reanalysis_fails(app, tmp_path, monkeypatch):
    tab = PowerTab()
    good = tmp_path / "power.csv"
    good.write_text("time_ms,mA\n0,100\n1000,200\n")
    tab.csv_path.setText(str(good))
    tab.on_analyze()
    assert tab.export_btn.isEnabled()

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: warnings.append(args[2]))
    bad = tmp_path / "bad.csv"
    bad.write_text("noise,data\nfoo,bar\n")
    tab.csv_path.setText(str(bad))
    tab.on_analyze()
    assert warnings and "no numeric" in warnings[0]
    assert not tab.export_btn.isEnabled()
    assert not tab.report_text


def test_export_disabled_while_run_is_active(app, suites_tab, monkeypatch):
    class SlowWorker(RunWorker):
        def run(self):
            time.sleep(0.3)
            super().run()

    monkeypatch.setattr("espbench.gui.RunWorker", SlowWorker)
    suites_tab.sim.setChecked(True)
    suites_tab.latency_n.setValue(5)
    suites_tab.on_run()
    assert not suites_tab.run_btn.isEnabled()
    assert not suites_tab.export_btn.isEnabled()
    deadline = time.time() + 30
    while not suites_tab.run_btn.isEnabled() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert suites_tab.export_btn.isEnabled()


def test_close_event_blocked_while_thread_running(app, monkeypatch):
    window = MainWindow()
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args, **kwargs: messages.append(args[2]))

    class StuckThread:
        @staticmethod
        def isRunning():
            return True

        @staticmethod
        def quit():
            pass

        @staticmethod
        def wait(timeout):
            return False

    window.suites_tab._thread = StuckThread()
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False
    assert messages and "still active" in messages[0]
    window.suites_tab._thread = None
    window.close()


def test_close_event_accepts_when_idle(app):
    window = MainWindow()
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is True
    window.close()


def test_close_event_quits_running_thread(app):
    window = MainWindow()
    thread = QThread()
    thread.start()
    time.sleep(0.05)
    window.suites_tab._thread = thread
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is True
    assert thread.wait(2000)
    window.suites_tab._thread = None
    window.close()


def test_probe_sim_reports_ready(suites_tab):
    suites_tab.sim.setChecked(True)
    suites_tab.on_probe()
    assert "simulator ready" in suites_tab.status.text()


def test_probe_dead_host_reports_unreachable(suites_tab):
    suites_tab.host.setText("127.0.0.1")
    suites_tab.port.setValue(9)
    suites_tab.sim.setChecked(False)
    suites_tab.on_probe()
    assert "unreachable" in suites_tab.status.text()
    assert suites_tab.probe_btn.isEnabled()


def test_suites_run_shows_progress_and_chart(app, suites_tab):
    suites_tab.sim.setChecked(True)
    suites_tab.suite_boxes["memory"].setChecked(False)
    suites_tab.suite_boxes["fuzz"].setChecked(False)
    suites_tab.latency_n.setValue(5)
    suites_tab.on_run()
    assert not suites_tab.probe_btn.isEnabled()
    deadline = time.time() + 30
    while not suites_tab.run_btn.isEnabled() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert suites_tab.progress.value() == 100
    assert "completed" in suites_tab.status.text()
    text = suites_tab.output.toPlainText()
    assert "Espressbench Report" in text
    assert "Suites" in text
    if suites_tab.chart is not None:
        assert not suites_tab.chart.isHidden()


def test_soak_tab_quick_run(app):
    tab = SoakTab()
    tab.sim.setChecked(True)
    tab.hours.setValue(0.0)
    tab.interval.setValue(0.0)
    tab.on_run()
    deadline = time.time() + 30
    while not tab.run_btn.isEnabled() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    text = tab.output.toPlainText()
    assert "Espressbench Soak" in text
    assert "Soak summary" in text
    assert tab.export_btn.isEnabled()
    assert "passed" in tab.status.text()
    assert "1 iterations" in tab.status.text()


def test_soak_tab_rejects_missing_device(app, monkeypatch):
    tab = SoakTab()
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args, **kwargs: messages.append(args[2]))
    tab.sim.setChecked(False)
    tab.host.setText("")
    tab.on_run()
    assert messages and "device host" in messages[0]
    assert tab.run_btn.isEnabled()


def test_chaos_tab_quick_run(app):
    tab = ChaosTab()
    tab.sim.setChecked(True)
    for mode in ("delay", "corrupt", "cut"):
        tab.fault_boxes[mode].setChecked(False)
    tab.duration.setValue(0.05)
    tab.recovery.setValue(1.0)
    tab.on_run()
    deadline = time.time() + 30
    while not tab.run_btn.isEnabled() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    text = tab.output.toPlainText()
    assert "Chaos summary" in text
    assert tab.export_btn.isEnabled()
    assert "recovered" in tab.status.text()


def test_mainwindow_has_all_tabs_and_menu(app):
    window = MainWindow()
    assert isinstance(window.soak_tab, SoakTab)
    assert isinstance(window.chaos_tab, ChaosTab)
    assert isinstance(window.power_tab, PowerTab)
    actions = [a.text() for a in window.menuBar().actions()]
    assert "&File" in actions
    assert "&Help" in actions
    window.close()


def test_close_event_blocked_while_soak_running(app, monkeypatch):
    window = MainWindow()
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args, **kwargs: messages.append(args[2]))

    class StuckThread:
        @staticmethod
        def isRunning():
            return True

        @staticmethod
        def quit():
            pass

        @staticmethod
        def wait(timeout):
            return False

    window.soak_tab._thread = StuckThread()
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False
    assert messages and "still active" in messages[0]
    window.soak_tab._thread = None
    window.close()


def test_device_settings_roundtrip(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    path = tmp_path / "settings.ini"
    monkeypatch.setattr("espbench.gui._settings",
                        lambda: QSettings(str(path), QSettings.IniFormat))
    tab = SuitesTab()
    tab.host.setText("10.0.0.7")
    tab.port.setValue(8080)
    tab.sim.setChecked(True)
    _save_device_settings(tab.host, tab.port, tab.sim, "suites")
    fresh = SuitesTab()
    assert fresh.host.text() == "10.0.0.7"
    assert fresh.port.value() == 8080
    assert fresh.sim.isChecked()


def test_reports_tab_load_compare_check(app, tmp_path):
    report = {"device": {"fw": "sim-0.1.0"},
              "suites": {"latency": {"n": 5, "p50": 10.0, "p95": 12.0,
                                     "errors": 0}}}
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"suites.latency.p95": 100}),
                       encoding="utf-8")
    tab = ReportsTab()
    tab.report_path.setText(str(report_file))
    tab.on_load()
    text = tab.output.toPlainText()
    assert "Espressbench Report" in text
    assert "Suites" in text
    assert "loaded report.json" in tab.status.text()
    tab.on_set_baseline()
    assert (tmp_path / "baseline.json").exists()
    tab.on_compare()
    text = tab.output.toPlainText()
    assert "Comparison" in text
    assert "0 regression" in tab.status.text()
    tab.on_check()
    text = tab.output.toPlainText()
    assert "Budgets" in text
    assert "1/1 passed" in tab.status.text()


def test_reports_tab_requires_report_for_actions(app, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args, **kwargs: messages.append(args[2]))
    tab = ReportsTab()
    tab.on_load()
    tab.on_set_baseline()
    tab.on_compare()
    tab.on_check()
    assert messages and "report.json" in messages[0]


def test_reports_tab_missing_baseline_hint(app, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *args, **kwargs: messages.append(args[2]))
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps(
        {"suites": {"latency": {"p95": 10.0}}}), encoding="utf-8")
    tab = ReportsTab()
    tab.report_path.setText(str(report_file))
    tab.on_load()
    tab.on_compare()
    assert messages and "baseline" in messages[0]


def test_suites_tab_repeat_aggregates(app, suites_tab):
    suites_tab.sim.setChecked(True)
    suites_tab.suite_boxes["memory"].setChecked(False)
    suites_tab.suite_boxes["fuzz"].setChecked(False)
    suites_tab.latency_n.setValue(5)
    suites_tab.repeat_runs.setValue(2)
    suites_tab.on_run()
    deadline = time.time() + 30
    while not suites_tab.run_btn.isEnabled() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    text = suites_tab.output.toPlainText()
    assert "Espressbench Report" in text
    assert "## Repeat" in text


def test_progress_lines_stream_into_output(suites_tab):
    suites_tab.output.setPlainText("")
    suites_tab._on_progress("suite latency (1/1)")
    assert "suite latency (1/1)" in suites_tab.status.text()
    assert "suite latency (1/1)" in suites_tab.output.toPlainText()


def test_mainwindow_reports_tab(app):
    window = MainWindow()
    assert isinstance(window.reports_tab, ReportsTab)
    assert window.tabs.count() == 5
    window.close()
