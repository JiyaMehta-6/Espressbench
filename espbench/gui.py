import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from espbench import __version__
from espbench.chaos import run_target
from espbench.hil import run_soak, run_suites
from espbench.insight import hints
from espbench.power import (
    attribute,
    battery_life_hours,
    energy_mah,
    parse,
    parse_markers,
    summarize,
)
from espbench.report import (
    chaos_sections,
    report_sections,
    soak_sections,
    to_markdown,
    write_reports,
)
from espbench.simulation import FakeDevice
from espbench.svg import histogram_svg

try:
    from PySide6.QtSvg import QSvgWidget
except ImportError:
    QSvgWidget = None

STYLE = """
QWidget { background: #1f2229; color: #e8eaed; font-size: 13px; }
QMainWindow { background: #1f2229; }
QGroupBox { border: 1px solid #3a3f4b; border-radius: 6px;
            margin-top: 10px; padding-top: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QPushButton { background: #2f6fed; border: none; border-radius: 5px;
              padding: 6px 14px; color: #ffffff; font-weight: 600; }
QPushButton:hover { background: #4580f5; }
QPushButton:disabled { background: #3a3f4b; color: #8a90a0; }
QPushButton[secondary="true"] { background: #3a3f4b; font-weight: 400; }
QPushButton[secondary="true"]:hover { background: #4a5060; }
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #15171c; border: 1px solid #3a3f4b; border-radius: 4px;
    padding: 3px 6px; selection-background-color: #2f6fed;
}
QPlainTextEdit { font-family: Consolas, "Courier New", monospace; }
QTabWidget::pane { border: 1px solid #3a3f4b; border-radius: 4px; }
QTabBar::tab { background: #15171c; padding: 6px 16px;
               border-top-left-radius: 5px; border-top-right-radius: 5px;
               margin-right: 2px; color: #b8bec9; }
QTabBar::tab:selected { background: #2f6fed; color: #ffffff; }
QMenuBar { background: #15171c; color: #e8eaed; }
QMenuBar::item:selected { background: #2f6fed; color: #ffffff; }
QMenu { background: #15171c; border: 1px solid #3a3f4b; }
QMenu::item:selected { background: #2f6fed; }
QStatusBar { background: #15171c; color: #9aa0a6; }
QProgressBar { border: 1px solid #3a3f4b; border-radius: 4px;
               text-align: center; background: #15171c; color: #e8eaed; }
QProgressBar::chunk { background: #2f6fed; }
QLabel { background: transparent; }
QScrollBar:vertical { background: #15171c; width: 10px; }
QScrollBar::handle:vertical { background: #3a3f4b; border-radius: 5px;
                              min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMessageBox { background: #1f2229; }
"""


class RunWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, host, port, sim, suites, params):
        super().__init__()
        self.host = host
        self.port = port
        self.sim = sim
        self.suites = suites
        self.params = params

    def run(self):
        try:
            device = FakeDevice(seed=1) if self.sim else _make_device(self.host, self.port)
            results = run_suites(device, suites=self.suites, params=self.params,
                                 progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(results)


class SoakWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, host, port, sim, hours, interval, suites, params):
        super().__init__()
        self.host = host
        self.port = port
        self.sim = sim
        self.hours = hours
        self.interval = interval
        self.suites = suites
        self.params = params

    def run(self):
        try:
            device = FakeDevice(seed=1) if self.sim else _make_device(self.host, self.port)
            report = run_soak(device, hours=self.hours, interval=self.interval,
                              suites=self.suites, params=self.params,
                              progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)


class ChaosWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, host, port, sim, faults, schedule, duration,
                 recovery_timeout, delay_ms):
        super().__init__()
        self.host = host
        self.port = port
        self.sim = sim
        self.faults = faults
        self.schedule = schedule
        self.duration = duration
        self.recovery_timeout = recovery_timeout
        self.delay_ms = delay_ms

    def run(self):
        try:
            report = run_target(host=self.host, port=self.port, sim=self.sim,
                                faults=self.faults, schedule=self.schedule,
                                duration=self.duration,
                                recovery_timeout=self.recovery_timeout,
                                delay_ms=self.delay_ms, corrupt_rate=1.0)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)


def _make_device(host, port):
    from espbench.device import Device

    return Device(host, port=port)


def _device_fields(form):
    host = QLineEdit("192.168.1.50")
    port = QSpinBox()
    port.setRange(1, 65535)
    port.setValue(80)
    sim = QCheckBox("use built-in simulator (no hardware)")
    form.addRow("Device host", host)
    form.addRow("Port", port)
    form.addRow("", sim)
    return host, port, sim


def _settings():
    return QSettings("espbench", "espbench")


def _load_device_settings(host, port, sim, prefix):
    settings = _settings()
    try:
        saved_host = settings.value(f"{prefix}/host", "")
        saved_port = settings.value(f"{prefix}/port", "")
        saved_sim = settings.value(f"{prefix}/sim", "")
    except Exception:
        return
    if saved_host:
        host.setText(str(saved_host))
    try:
        port.setValue(int(saved_port))
    except (TypeError, ValueError):
        pass
    if saved_sim in (True, "true", "1", 1):
        sim.setChecked(True)
    elif saved_sim in (False, "false", "0", 0):
        sim.setChecked(False)


def _save_device_settings(host, port, sim, prefix):
    settings = _settings()
    try:
        settings.setValue(f"{prefix}/host", host.text().strip())
        settings.setValue(f"{prefix}/port", port.value())
        settings.setValue(f"{prefix}/sim", sim.isChecked())
    except Exception:
        return


def _launch(tab, worker):
    tab._thread = QThread(tab)
    tab._worker = worker
    worker.moveToThread(tab._thread)
    tab._thread.started.connect(worker.run)
    worker.finished.connect(tab._on_finished)
    worker.failed.connect(tab._on_failed)
    worker.progress.connect(tab._on_progress)
    worker.finished.connect(tab._thread.quit)
    worker.failed.connect(tab._thread.quit)
    tab._thread.finished.connect(worker.deleteLater)
    tab._thread.finished.connect(tab._thread.deleteLater)
    tab._thread.finished.connect(tab._thread_finished)
    tab._thread.start()


def _begin_run(tab, started):
    tab.run_btn.setEnabled(False)
    tab.export_btn.setEnabled(False)
    tab.output.setPlainText("running...")
    tab.progress.setRange(0, 0)
    tab.status.setText("starting...")
    tab._started = started


def _latency_samples(results):
    suites = results.get("suites")
    suites = suites if isinstance(suites, dict) else {}
    latency = suites.get("latency")
    samples = latency.get("_samples") if isinstance(latency, dict) else None
    if isinstance(samples, list) and samples:
        return samples
    return None


class SuitesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = {}
        self._started = time.perf_counter()
        form = QFormLayout()
        self.host, self.port, self.sim = _device_fields(form)
        _load_device_settings(self.host, self.port, self.sim, "suites")

        self.suite_boxes = {}
        suites_box = QGroupBox("Suites")
        suites_layout = QHBoxLayout()
        for key, label in (("latency", "Latency"), ("memory", "Memory leak"),
                           ("fuzz", "Fuzz")):
            box = QCheckBox(label)
            box.setChecked(True)
            self.suite_boxes[key] = box
            suites_layout.addWidget(box)
        suites_box.setLayout(suites_layout)

        self.latency_n = QSpinBox()
        self.latency_n.setRange(5, 10000)
        self.latency_n.setValue(100)
        self.memory_samples = QSpinBox()
        self.memory_samples.setRange(2, 1000)
        self.memory_samples.setValue(25)
        self.memory_interval = QDoubleSpinBox()
        self.memory_interval.setRange(0.0, 60.0)
        self.memory_interval.setValue(1.0)
        self.settle = QDoubleSpinBox()
        self.settle.setRange(0.0, 10.0)
        params = QFormLayout()
        params.addRow("Latency samples", self.latency_n)
        params.addRow("Memory samples", self.memory_samples)
        params.addRow("Memory interval (s)", self.memory_interval)
        params.addRow("Fuzz settle (s)", self.settle)

        self.run_btn = QPushButton("Run")
        self.probe_btn = QPushButton("Test connection")
        self.probe_btn.setProperty("secondary", True)
        self.export_btn = QPushButton("Export reports")
        self.export_btn.setProperty("secondary", True)
        self.export_btn.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = QLabel("ready")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        if QSvgWidget is not None:
            self.chart = QSvgWidget()
            self.chart.setMinimumHeight(150)
            self.chart.hide()
        else:
            self.chart = None

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.probe_btn)
        buttons.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(suites_box)
        layout.addLayout(params)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Results"))
        layout.addWidget(self.output, stretch=1)
        if self.chart is not None:
            layout.addWidget(self.chart)

        self.run_btn.clicked.connect(self.on_run)
        self.probe_btn.clicked.connect(self.on_probe)
        self.export_btn.clicked.connect(self.on_export)
        self._thread = None
        self._worker = None

    def selected_suites(self):
        return [key for key, box in self.suite_boxes.items() if box.isChecked()]

    def params(self):
        return {
            "latency": {"n": self.latency_n.value()},
            "memory": {"samples": self.memory_samples.value(),
                       "interval": self.memory_interval.value()},
            "fuzz": {"settle": self.settle.value()},
        }

    def on_run(self):
        suites = self.selected_suites()
        if not suites:
            QMessageBox.information(self, "espbench", "select at least one suite")
            return
        if not self.sim.isChecked() and not self.host.text().strip():
            QMessageBox.information(self, "espbench",
                                    "enter a device host or enable the simulator")
            return
        _save_device_settings(self.host, self.port, self.sim, "suites")
        self.probe_btn.setEnabled(False)
        _begin_run(self, time.perf_counter())
        worker = RunWorker(self.host.text().strip(), self.port.value(),
                           self.sim.isChecked(), suites, self.params())
        _launch(self, worker)

    def on_probe(self):
        if self.sim.isChecked():
            self.status.setText("simulator ready - no hardware needed")
            return
        host = self.host.text().strip()
        if not host:
            QMessageBox.information(self, "espbench",
                                    "enter a device host or enable the simulator")
            return
        self.probe_btn.setEnabled(False)
        self.status.setText(f"probing {host}:{self.port.value()} ...")
        try:
            from espbench.device import Device

            device = Device(host, port=self.port.value(), timeout=3.0)
            stats = device.stats()
            fw = stats.get("fw") if isinstance(stats, dict) else None
            if not fw:
                try:
                    fw = device.version()
                except Exception:
                    fw = "?"
            ping = device.ping()
            self.status.setText(f"device online - fw {fw}, ping {ping:.1f} ms")
        except Exception as exc:
            self.status.setText(f"device unreachable - {str(exc)[:120]}")
        finally:
            self.probe_btn.setEnabled(True)
            _save_device_settings(self.host, self.port, self.sim, "suites")

    def _thread_finished(self):
        self._thread = None

    def _on_progress(self, message):
        self.status.setText(message)

    def _on_finished(self, results):
        self.results = results
        elapsed = time.perf_counter() - self._started
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.probe_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        sections = report_sections(results)
        lines = hints(results)
        if lines:
            sections["Insights"] = lines
        self.output.setPlainText(to_markdown(sections))
        device = results.get("device")
        failed = isinstance(device, dict) and "error" in device
        self.status.setText(f"{'failed' if failed else 'completed'} "
                            f"in {elapsed:.1f}s")
        samples = _latency_samples(results)
        if self.chart is not None:
            if samples:
                svg = histogram_svg(samples, title="Latency distribution",
                                    unit="ms")
                self.chart.load(svg.encode("utf-8"))
                self.chart.show()
            else:
                self.chart.hide()

    def _on_failed(self, message):
        self.run_btn.setEnabled(True)
        self.probe_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText("run failed")
        self.output.setPlainText(f"error: {message}")

    def on_export(self):
        if not self.results:
            return
        directory = QFileDialog.getExistingDirectory(self, "export reports")
        if not directory:
            return
        try:
            paths = write_reports(self.results, directory)
            samples = _latency_samples(self.results)
            if samples:
                chart_path = Path(directory) / "latency.svg"
                chart_path.write_text(
                    histogram_svg(samples, title="Latency distribution",
                                  unit="ms"),
                    encoding="utf-8")
                paths["chart"] = str(chart_path)
        except OSError as exc:
            QMessageBox.warning(self, "espbench", str(exc))
            return
        QMessageBox.information(self, "espbench",
                                "wrote " + ", ".join(paths.values()))


class SoakTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = {}
        self._started = time.perf_counter()
        form = QFormLayout()
        self.host, self.port, self.sim = _device_fields(form)
        _load_device_settings(self.host, self.port, self.sim, "soak")

        self.hours = QDoubleSpinBox()
        self.hours.setRange(0.0, 168.0)
        self.hours.setValue(1.0)
        self.hours.setSuffix(" h")
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.0, 3600.0)
        self.interval.setValue(60.0)
        self.interval.setSuffix(" s")
        timing = QFormLayout()
        timing.addRow("Soak length", self.hours)
        timing.addRow("Interval", self.interval)

        self.suite_boxes = {}
        suites_box = QGroupBox("Suites each iteration (empty = health check only)")
        suites_layout = QHBoxLayout()
        for key, label in (("latency", "Latency"), ("memory", "Memory leak"),
                           ("fuzz", "Fuzz")):
            box = QCheckBox(label)
            self.suite_boxes[key] = box
            suites_layout.addWidget(box)
        suites_box.setLayout(suites_layout)

        self.run_btn = QPushButton("Start soak")
        self.export_btn = QPushButton("Export reports")
        self.export_btn.setProperty("secondary", True)
        self.export_btn.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = QLabel("ready")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(timing)
        layout.addWidget(suites_box)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Results"))
        layout.addWidget(self.output, stretch=1)

        self.run_btn.clicked.connect(self.on_run)
        self.export_btn.clicked.connect(self.on_export)
        self._thread = None
        self._worker = None

    def selected_suites(self):
        return [key for key, box in self.suite_boxes.items() if box.isChecked()]

    def params(self):
        return {
            "latency": {"n": 100},
            "memory": {"samples": 25, "interval": 1.0},
            "fuzz": {"settle": 0.0},
        }

    def on_run(self):
        if not self.sim.isChecked() and not self.host.text().strip():
            QMessageBox.information(self, "espbench",
                                    "enter a device host or enable the simulator")
            return
        _save_device_settings(self.host, self.port, self.sim, "soak")
        _begin_run(self, time.perf_counter())
        worker = SoakWorker(self.host.text().strip(), self.port.value(),
                            self.sim.isChecked(), self.hours.value(),
                            self.interval.value(), self.selected_suites(),
                            self.params())
        _launch(self, worker)

    def _thread_finished(self):
        self._thread = None

    def _on_progress(self, message):
        self.status.setText(message)

    def _on_finished(self, report):
        self.results = report
        elapsed = time.perf_counter() - self._started
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        sections = soak_sections(report)
        lines = hints({"soak": report})
        if lines:
            sections["Insights"] = lines
        self.output.setPlainText(to_markdown(sections,
                                             title="ESP32 Eval Bench Soak"))
        verdict = "passed" if report.get("passed") else "FAILED"
        self.status.setText(
            f"soak {verdict} - {report.get('iterations', 0)} iterations "
            f"in {elapsed:.1f}s")

    def _on_failed(self, message):
        self.run_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText("soak failed")
        self.output.setPlainText(f"error: {message}")

    def on_export(self):
        if not self.results:
            return
        directory = QFileDialog.getExistingDirectory(self, "export reports")
        if not directory:
            return
        try:
            paths = write_reports({"soak": self.results}, directory)
        except OSError as exc:
            QMessageBox.warning(self, "espbench", str(exc))
            return
        QMessageBox.information(self, "espbench",
                                "wrote " + ", ".join(paths.values()))


class ChaosTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = {}
        self._started = time.perf_counter()
        form = QFormLayout()
        self.host, self.port, self.sim = _device_fields(form)
        _load_device_settings(self.host, self.port, self.sim, "chaos")

        self.fault_boxes = {}
        faults_box = QGroupBox("Fault modes")
        faults_layout = QHBoxLayout()
        for mode in ("refuse", "delay", "corrupt", "cut"):
            box = QCheckBox(mode)
            box.setChecked(True)
            self.fault_boxes[mode] = box
            faults_layout.addWidget(box)
        faults_box.setLayout(faults_layout)

        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.0, 60.0)
        self.duration.setValue(1.0)
        self.duration.setSuffix(" s")
        self.recovery = QDoubleSpinBox()
        self.recovery.setRange(0.0, 120.0)
        self.recovery.setValue(5.0)
        self.recovery.setSuffix(" s")
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(0, 10000)
        self.delay_ms.setValue(200)
        self.delay_ms.setSuffix(" ms")
        self.schedule = QLineEdit()
        self.schedule.setPlaceholderText('optional: "refuse:2s,normal:1s,cut:500ms"')
        tuning = QFormLayout()
        tuning.addRow("Fault duration", self.duration)
        tuning.addRow("Recovery timeout", self.recovery)
        tuning.addRow("Delay inject", self.delay_ms)
        tuning.addRow("Schedule", self.schedule)

        self.run_btn = QPushButton("Run chaos")
        self.export_btn = QPushButton("Export reports")
        self.export_btn.setProperty("secondary", True)
        self.export_btn.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = QLabel("ready")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(faults_box)
        layout.addLayout(tuning)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Results"))
        layout.addWidget(self.output, stretch=1)

        self.run_btn.clicked.connect(self.on_run)
        self.export_btn.clicked.connect(self.on_export)
        self._thread = None
        self._worker = None

    def selected_faults(self):
        return [mode for mode, box in self.fault_boxes.items() if box.isChecked()]

    def on_run(self):
        schedule = self.schedule.text().strip()
        if not schedule and not self.selected_faults():
            QMessageBox.information(self, "espbench",
                                    "select at least one fault mode or enter a schedule")
            return
        if not self.sim.isChecked() and not self.host.text().strip():
            QMessageBox.information(self, "espbench",
                                    "enter a device host or enable the simulator")
            return
        _save_device_settings(self.host, self.port, self.sim, "chaos")
        _begin_run(self, time.perf_counter())
        worker = ChaosWorker(self.host.text().strip(), self.port.value(),
                             self.sim.isChecked(), self.selected_faults(),
                             schedule or None, self.duration.value(),
                             self.recovery.value(), self.delay_ms.value())
        _launch(self, worker)

    def _thread_finished(self):
        self._thread = None

    def _on_progress(self, message):
        self.status.setText(message)

    def _on_finished(self, report):
        self.results = report
        elapsed = time.perf_counter() - self._started
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        sections = chaos_sections(report)
        lines = hints({"chaos": report})
        if lines:
            sections["Insights"] = lines
        self.output.setPlainText(to_markdown(sections))
        ok = report.get("all_recovered") and report.get("all_faults_effective")
        self.status.setText(
            f"chaos {'passed' if ok else 'FAILED'} - "
            f"{report.get('recovered', 0)}/{report.get('total', 0)} recovered "
            f"in {elapsed:.1f}s")

    def _on_failed(self, message):
        self.run_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText("chaos failed")
        self.output.setPlainText(f"error: {message}")

    def on_export(self):
        if not self.results:
            return
        directory = QFileDialog.getExistingDirectory(self, "export reports")
        if not directory:
            return
        try:
            paths = write_reports({"chaos": self.results}, directory)
        except OSError as exc:
            QMessageBox.warning(self, "espbench", str(exc))
            return
        QMessageBox.information(self, "espbench",
                                "wrote " + ", ".join(paths.values()))


class PowerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = None
        self.report_text = ""

        form = QFormLayout()
        self.csv_path = QLineEdit()
        csv_btn = QPushButton("Browse...")
        csv_btn.setProperty("secondary", True)
        csv_btn.clicked.connect(self._browse_csv)
        csv_row = QHBoxLayout()
        csv_row.addWidget(self.csv_path)
        csv_row.addWidget(csv_btn)
        self.markers_path = QLineEdit()
        markers_btn = QPushButton("Browse...")
        markers_btn.setProperty("secondary", True)
        markers_btn.clicked.connect(self._browse_markers)
        markers_row = QHBoxLayout()
        markers_row.addWidget(self.markers_path)
        markers_row.addWidget(markers_btn)
        self.capacity = QDoubleSpinBox()
        self.capacity.setRange(0.0, 100000.0)
        self.capacity.setValue(1200.0)
        form.addRow("Current log CSV", csv_row)
        form.addRow("Markers CSV", markers_row)
        form.addRow("Battery capacity (mAh)", self.capacity)

        self.analyze_btn = QPushButton("Analyse")
        self.export_btn = QPushButton("Export report")
        self.export_btn.setProperty("secondary", True)
        self.export_btn.setEnabled(False)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.analyze_btn)
        buttons.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Report"))
        layout.addWidget(self.output)

        self.analyze_btn.clicked.connect(self.on_analyze)
        self.export_btn.clicked.connect(self.on_export)

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "current log CSV", "",
                                              "CSV (*.csv);;All files (*)")
        if path:
            self.csv_path.setText(path)

    def _browse_markers(self):
        path, _ = QFileDialog.getOpenFileName(self, "markers CSV", "",
                                              "CSV (*.csv);;All files (*)")
        if path:
            self.markers_path.setText(path)

    def on_analyze(self):
        self.export_btn.setEnabled(False)
        self.report_text = ""
        path = self.csv_path.text().strip()
        if not path:
            QMessageBox.information(self, "espbench", "choose a current log CSV")
            return
        try:
            rows = parse(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.warning(self, "espbench", str(exc))
            return
        self.rows = rows
        sections = {"Power summary": summarize(rows),
                    "Energy": {"total_mah": round(energy_mah(rows), 6)}}
        markers_text = self.markers_path.text().strip()
        if markers_text:
            try:
                markers = parse_markers(Path(markers_text).read_text(encoding="utf-8"))
                sections["Operations"] = {row["label"]: row
                                          for row in attribute(rows, markers)}
            except Exception as exc:
                sections["Markers error"] = str(exc)
        capacity = self.capacity.value()
        if capacity > 0:
            sections["Battery projection"] = {
                "capacity_mah": capacity,
                "hours": battery_life_hours(rows, capacity),
            }
        self.report_text = to_markdown(sections, title="Power Report")
        self.output.setPlainText(self.report_text)
        self.export_btn.setEnabled(True)

    def on_export(self):
        if not self.report_text:
            return
        path, _ = QFileDialog.getSaveFileName(self, "export power report",
                                              "power_report.md", "Markdown (*.md)")
        if not path:
            return
        try:
            Path(path).write_text(self.report_text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "espbench", str(exc))
            return
        QMessageBox.information(self, "espbench", f"wrote {path}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32 Eval Bench")
        self.resize(980, 760)
        self.setStyleSheet(STYLE)
        tabs = QTabWidget()
        self.suites_tab = SuitesTab()
        self.soak_tab = SoakTab()
        self.chaos_tab = ChaosTab()
        self.power_tab = PowerTab()
        tabs.addTab(self.suites_tab, "Suites")
        tabs.addTab(self.soak_tab, "Soak")
        tabs.addTab(self.chaos_tab, "Chaos")
        tabs.addTab(self.power_tab, "Power")
        self.setCentralWidget(tabs)

        file_menu = self.menuBar().addMenu("&File")
        export_action = file_menu.addAction("Export reports...")
        export_action.triggered.connect(self.suites_tab.on_export)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)
        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("About espbench")
        about_action.triggered.connect(self._about)
        self.statusBar().showMessage(f"espbench {__version__} - ready")

    def _about(self):
        QMessageBox.about(self, "About espbench",
                          f"<b>espbench {__version__}</b><br>"
                          "Hardware-in-the-loop evaluation bench for "
                          "ESP32/ESP8266 firmware<br><br>"
                          "Run suites, soak, chaos and power analysis - "
                          "all 100% free and offline.")

    def closeEvent(self, event):
        for tab in (self.soak_tab, self.chaos_tab, self.suites_tab):
            thread = getattr(tab, "_thread", None)
            if thread is not None and thread.isRunning():
                thread.quit()
                if not thread.wait(1500):
                    QMessageBox.information(self, "espbench",
                                            "a run is still active; try again")
                    event.ignore()
                    return
        event.accept()


def main(argv=None):
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("ESP32 Eval Bench")
    app.setOrganizationName("espbench")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
