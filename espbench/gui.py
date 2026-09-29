import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
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
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from espbench.hil import run_suites
from espbench.power import (
    attribute,
    battery_life_hours,
    energy_mah,
    parse,
    parse_markers,
    summarize,
)
from espbench.report import to_markdown, write_reports
from espbench.simulation import FakeDevice


class RunWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

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
            results = run_suites(device, suites=self.suites, params=self.params)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(results)


def _make_device(host, port):
    from espbench.device import Device

    return Device(host, port=port)


class SuitesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = {}
        form = QFormLayout()
        self.host = QLineEdit("192.168.1.50")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(80)
        self.sim = QCheckBox("use built-in simulator (no hardware)")
        form.addRow("Device host", self.host)
        form.addRow("Port", self.port)
        form.addRow("", self.sim)

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
        self.export_btn = QPushButton("Export reports")
        self.export_btn.setEnabled(False)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.export_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(suites_box)
        layout.addLayout(params)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Results"))
        layout.addWidget(self.output)

        self.run_btn.clicked.connect(self.on_run)
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
            QMessageBox.information(self, "espbench", "enter a device host or enable the simulator")
            return
        self.run_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.output.setPlainText("running…")
        self._thread = QThread(self)
        self._worker = RunWorker(self.host.text().strip(), self.port.value(),
                                 self.sim.isChecked(), suites, self.params())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def _thread_finished(self):
        self._thread = None

    def _on_finished(self, results):
        self.results = results
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.output.setPlainText(to_markdown(results))

    def _on_failed(self, message):
        self.run_btn.setEnabled(True)
        self.output.setPlainText(f"error: {message}")

    def on_export(self):
        if not self.results:
            return
        directory = QFileDialog.getExistingDirectory(self, "export reports")
        if not directory:
            return
        try:
            paths = write_reports(self.results, directory)
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
        csv_btn = QPushButton("Browse…")
        csv_btn.clicked.connect(self._browse_csv)
        csv_row = QHBoxLayout()
        csv_row.addWidget(self.csv_path)
        csv_row.addWidget(csv_btn)
        self.markers_path = QLineEdit()
        markers_btn = QPushButton("Browse…")
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
        self.resize(900, 700)
        tabs = QTabWidget()
        self.suites_tab = SuitesTab()
        self.power_tab = PowerTab()
        tabs.addTab(self.suites_tab, "Suites")
        tabs.addTab(self.power_tab, "Power")
        self.setCentralWidget(tabs)

    def closeEvent(self, event):
        thread = self.suites_tab._thread
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
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
