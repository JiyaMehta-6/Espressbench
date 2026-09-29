from espbench.chaos import FaultController, FaultProxy, run_chaos
from espbench.device import Device, DeviceError
from espbench.fuzz import run as fuzz_run
from espbench.gate import check_budgets, compare_reports, flatten
from espbench.hil import run_suites
from espbench.latency import run as latency_run
from espbench.memory import run as memory_run
from espbench.power import battery_life_hours, energy_mah, parse, summarize
from espbench.replay import RecordingDevice, ReplayDevice, load_steps, save_steps
from espbench.simulation import FakeDevice

__version__ = "0.1.0"

__all__ = [
    "Device",
    "DeviceError",
    "FakeDevice",
    "FaultProxy",
    "FaultController",
    "run_chaos",
    "latency_run",
    "memory_run",
    "fuzz_run",
    "run_suites",
    "RecordingDevice",
    "ReplayDevice",
    "load_steps",
    "save_steps",
    "parse",
    "summarize",
    "energy_mah",
    "battery_life_hours",
    "flatten",
    "compare_reports",
    "check_budgets",
    "__version__",
]
