# ESP32 Eval Bench

[![CI](https://github.com/JiyaMehta-6/ESP32-Eval-Bench/actions/workflows/ci.yml/badge.svg)](https://github.com/JiyaMehta-6/ESP32-Eval-Bench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Hardware-in-the-loop evaluation bench for ESP32 / ESP8266 firmware — **chaos engineering for $5 boards**.

Firmware claims are usually unmeasured: "stable", "low latency", "memory safe".
This bench turns claims into numbers with a MicroPython agent on the device and a
host-side harness over WiFi. No cloud, no accounts, no paid tools.

## Suites

| Suite | What it measures | Failure signal |
|---|---|---|
| **Latency** | p50 / p95 / p99 round-trip to `/ping` | tail latency over budget |
| **Memory** | `mem_free` trend over uptime | monotonic decline = leak |
| **Fuzz** | hostile payloads against `/echo` | crash, 5xx, device reboot |
| **Chaos** ⭐ | fault proxy: refuse / delay / corrupt / cut connections | device or client fails to recover |
| **Replay** ⭐ | record an HIL session, replay it **without hardware** | "flaky on my desk" becomes a CI test |
| **Power** ⭐ | mA / mAh from a logger CSV + per-operation attribution | peak draw, battery-life projection |

Every response carries `X-Boot-Count`, so a reboot mid-fuzz is detected even when
the TCP connection just dies.

## Architecture

```
┌────────────┐   HTTP / WiFi   ┌──────────────────────┐
│  espbench  │ ──────────────► │ ESP32 running        │
│  (host)    │ ◄────────────── │ firmware/agent.py    │
└─────┬──────┘   JSON + headers └──────────────────────┘
      │
      ├─ latency.py  → percentiles        ├─ chaos.py   → fault proxy + recovery times
      ├─ memory.py   → leak slope (B/s)   ├─ replay.py  → record now, replay anywhere
      ├─ fuzz.py     → payload × crash    ├─ power.py   → mAh per operation
      └─ report.py   → JSON + Markdown + JUnit
```

## Setup

### 1. Flash the agent (free tooling: mpremote / Thonny)

```bash
cp firmware/wifi.example.py firmware/wifi.py   # edit ssid/password
mpremote cp firmware/agent.py firmware/boot.py firmware/wifi.py :
mpremote run firmware/boot.py
```

### 2. Install the bench (uv, fully offline after first run)

```bash
uv venv && uv pip install -e ".[dev,gui]"
```

### 3. Run

```bash
espbench doctor                             # environment + device preflight
espbench run --host 192.168.1.42 --suites latency,memory,fuzz --out reports/
espbench chaos --host 192.168.1.42 --faults refuse,delay,cut
espbench replay-record --host 192.168.1.42 --out session.json
espbench replay-run --fixture session.json
espbench power --csv power_log.csv --markers markers.csv --capacity 1200
espbench gui                                # or: espbench-gui
```

No board? Everything except live runs works against the bundled simulator:

```bash
espbench run --sim --suites latency,memory,fuzz
```

Long commands announce their progress on **stderr** (`run: suite memory (2/3)`,
`soak: iteration 12 ok`), so pipes and redirects stay clean while CI logs show
exactly where a run is.

## Desktop GUI

`espbench gui` (or the `espbench-gui` shortcut) opens a dark-themed PySide6
window with four tabs:

| Tab | What it does |
|---|---|
| **Suites** | host/sim config, suite picker, params, **Test connection** probe (fw + ping), live per-suite progress, markdown results with Insights, latency histogram preview |
| **Soak** | hours/interval, optional suites per iteration, live iteration feed, pass/fail verdict |
| **Chaos** | fault-mode picker, duration/recovery/delay knobs, timed schedule field, recovery summary |
| **Power** | current-log CSV + markers analysis, battery projection, markdown export |

Everything runs in a background thread with a progress bar; **Export reports**
writes the same `report.json` / `report.md` / `junit.xml` as the CLI plus a
`latency.svg` chart. Host, port and simulator preference are remembered between
sessions (QSettings), and the device probe never touches the network in sim
mode. The GUI is fully offline - no telemetry, no accounts.

## Commands

| Command | What it does |
|---|---|
| `run` | run suites live, `--repeat N` for distributions, `--bundle` to record, `--fixture` to replay |
| `soak` | endurance loop for N hours (health or suites each iteration) |
| `chaos` | fault injection: `--faults refuse,delay,...` or timed `--schedule "refuse:2s,normal:1s"` |
| `replay-record` / `replay-run` | fuzz-only shortcuts for record & replay |
| `power` | marker-aligned current-log CSV analysis |
| `baseline` / `compare` / `check` | quality gates: freeze, diff, budget (see below) |
| `diff` | behavioural delta between two replay fixtures |
| `insight` | advisory hints for any saved report (`--strict` exits 1 on warnings) |
| `chart` | dependency-free SVG histogram from report samples |
| `badge` | status badge SVG for one metric |
| `init` | scaffold the GitHub Actions workflow |
| `doctor` | preflight: python/GUI/requests versions, local files, device reachability |
| `gui` | launch the desktop GUI |
| `--completions bash\|zsh\|fish` | shell completion script |

Exit codes are the CI contract:

| Exit | Meaning |
|---|---|
| `0` | passed / no regressions |
| `1` | suite failure, regression, unrecovered fault, budget breach, `doctor` probe failure, `insight --strict` warnings |
| `2` | bad input: unknown metric, unreadable report, invalid flag or fixture params |

## Sample output

`espbench run --out reports/` writes `report.json`, `report.md`, and `junit.xml`:

```json
{
  "suites": {
    "latency": { "n": 200, "errors": 0, "p50": 11.2, "p95": 24.8,
                 "p99": 41.6, "unit": "ms" },
    "memory":  { "samples": 25, "start_free": 98304, "end_free": 94208,
                 "drop_bytes": 4096, "bytes_per_s": -2.1, "leak_detected": true },
    "fuzz":    { "total": 12, "errors": 1, "reboots": 1, "passed": false }
  },
  "device": { "uptime_s": 412, "boot_count": 3, "mem_free": 94208,
              "sensor": "ok", "simulated": false }
}
```

`espbench chaos --out reports/` writes the same three files with the chaos report
at the top level (`{ "chaos": { "total": 4, "all_recovered": true, ... } }`).

## Lab sessions

Long-run stability, timed fault scripts, and hardware-free reruns of a session:

```bash
espbench soak --host 192.168.1.42 --hours 8 --interval 60 --suites latency,memory
espbench soak --sim --hours 0 --out soak/          # single health pass, exit 0/1
espbench chaos --host 192.168.1.42 --schedule "refuse:2s,normal:1s,refuse:2s"
espbench run --fixture session.json                # replay a bundled session
```

- **soak** samples health (or the chosen suites) until the hour budget runs out,
  counting failures, errors, and reboots; the last 50 events land in the report
  (`events_dropped` tracks anything older), and a reboot or any failure fails the
  run.
- **chaos `--schedule`** replaces `--faults`/`--duration`: each `MODE:DURATION`
  phase is probed independently, a `normal` phase right after a fault measures
  how long recovery took, and trailing faults are drained at the end - the report
  gains a `schedule` list and per-phase recovery columns.
- **`run --fixture`** replays a `--bundle` session through the full run pipeline;
  the fixture remembers which suites and params recorded it, so no flags are
  needed (`--sim`/`--host` cannot be combined with `--fixture`).
- **log-on-failure**: any report whose device payload errored or whose suite
  failed carries the device's `/log` ring buffer under a `Device log` section -
  post-mortem context arrives with the failure.

## Insights & charts

`run`, `chaos`, and `soak` end their console report with an **Insights**
section only when something deserves attention - dropped pings, tail latency,
leaks, reboots, unrecovered faults, soak failures. Clean runs stay quiet:

```bash
espbench insight reports/report.json              # the same hints, on any saved report
espbench insight reports/report.json --strict     # exit 1 when warnings exist (CI gate)
espbench chart reports/report.json --unit ms      # -> reports/report.svg
espbench chart reports/report.json --title "p95 across repeats" --out p95.svg
```

`chart` draws a dependency-free SVG histogram from the report's raw latency
samples (or the per-run values of a `--repeat` aggregate) - embed it in a PR,
a dashboard, or the CI step summary. Both commands are read-only and exit 0 on
success, 2 when the report is missing or has no sample values.

## CI integration

This repository ships a hand-tuned `.github/workflows/ci.yml` (Python 3.10 +
3.12 matrix, lock check, firmware syntax check, manual `workflow_dispatch`
hardware job). For new projects, one command scaffolds an equivalent setup:

```bash
espbench init            # -> .github/workflows/espbench.yml
```

It creates two jobs - `tests` (ruff + pytest against the simulator, no
hardware in CI) and `sim-report` (a simulated run whose report and insights
land in the job's **step summary** automatically, with a status-badge SVG
shipped in the artifact). Every report-producing command (`run`, `chaos`,
`soak`, `compare`, `check`, `insight`, `init`, `power`, `diff`, `baseline`,
`chart`, `badge`, `replay-*`) appends its markdown to `$GITHUB_STEP_SUMMARY`
when that variable is set - no wrapper scripts needed.

More CI glue:

```bash
espbench badge reports/report.json --metric p95 --out reports/badge.svg
source <(espbench --completions bash)        # or zsh / fish
```

JUnit XML results and live-hardware reports upload as CI artifacts, and the
`workflow_dispatch` trigger keeps real-hardware jobs manual so they only run
when a board is plugged in. Gate CI on the exit codes above.

## Quality gates

Freeze a known-good run, then fail CI when the next firmware regresses:

```bash
espbench baseline reports/report.json            # once: golden run -> baseline.json
espbench run --out reports/                      # candidate run
espbench compare reports/report.json             # exit 1 if any metric regressed
espbench check reports/report.json --budgets budgets.json   # exit 1 on breach
```

`compare` prints a per-metric table (`p95: 20.0 -> 26.0 (+30.0%) regression`)
with `--tolerance %` for noisy metrics; directions are automatic (higher-better
for `mem_free`/`recovered`, neutral for sample counts, bools judged by a
badness table). `check` budgets are `METRIC=LIMIT` (ceiling), `METRIC>=LIMIT`
(floor) or `METRIC=false` (equality), resolved by unique suffix (`p95<=50`),
via repeatable `--budget` flags or a `--budgets` JSON file:

```json
{ "latency.p95": 50, "device.mem_free": ">=50000", "memory.leak_detected": false }
```

### Statistical A/B

Single runs lie about noisy hardware. `run --repeat N` runs the suites N times
and aggregates: medians for scalars, `all()` for booleans, worst-case `max()`
for error/reboot counters; the report gains a `repeat` block (per-metric
value lists, also written per-run under `out/runs/`). When both sides of a
`compare` carry such distributions, verdicts become statistical: a point
regression only counts when a 1000-sample bootstrap CI of the median
difference excludes zero, wildly variable metrics are labelled `flaky` (and
never gate), and stable improvements are promoted to `improved`:

```bash
espbench run --repeat 5 --out a/ && espbench run --repeat 5 --out b/
espbench compare a/report.json b/report.json    # regression | improved | ok | flaky
```

`baseline --auto` derives a budget file from a report instead of hand-writing
one - each metric gets a ceiling of `value + margin%` (floors for
higher-is-better metrics, bools only when currently good, neutral counts
skipped), then `check --budgets budgets.json` gates against it:

```bash
espbench baseline reports/report.json --auto --margin 20   # -> budgets.json
```

### Firmware differential

Every report records the agent firmware (`device.fw`, from `/stats` or the
`/version` fallback). `compare` refuses to diff reports from *different*
firmware unless you say so - that mistake silently turns a version change into
a "regression":

```bash
espbench compare baseline.json report.json                    # exit 2 on fw mismatch
espbench compare baseline.json report.json --allow-mismatch   # compare on purpose
```

`run --bundle` records every device interaction (stats, echoes, control ops)
as a replay fixture next to the report; `diff` then shows the behavioural delta
between two sessions - same workload, two firmwares - ignoring volatile fields
(latencies, uptimes, free memory) and exiting 1 when behaviour changed:

```bash
espbench run --out fw-1.0/ --bundle && espbench run --out fw-1.1/ --bundle
espbench diff fw-1.0/session.json fw-1.1/session.json    # exit 1 on differences
```

The agent itself serves `GET /version` and `GET /log` (a 64-entry ring buffer
of boot/mark/restart/error events) for on-device post-mortems.

## Roadmap

- [x] Chaos fault proxy (refuse / delay / corrupt / cut)
- [x] Record & replay without hardware
- [x] Power attribution from marker-aligned CSV
- [x] PySide6 GUI
- [x] Quality gates: baseline / compare / budget checks
- [x] Statistical A/B: repeats, bootstrap verdicts, auto-budgets
- [x] Firmware differential: version gate, bundles, fixture diff
- [x] Lab sessions: soak, timed schedules, fixture reruns, log-on-failure
- [x] Insights & charts: advisory hints, SVG histograms
- [x] CI wow: `init` workflow scaffold, step summaries, badges, completions
- [x] Preflight & progress: `doctor`, stderr progress, `insight --strict`
- [x] GUI 4-tab overhaul: probe, live progress, histograms, soak/chaos tabs
- [ ] Device-as-client chaos (MQTT reconnect under broker loss)
- [ ] Multi-device matrix runs
- [ ] Trend charts across runs (regression tracking)

## License

MIT
