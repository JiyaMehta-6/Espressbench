import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from espbench.gui import MainWindow, PowerTab, RunWorker, SuitesTab  # noqa: E402


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
    assert "ESP32 Eval Bench Report" in text
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
