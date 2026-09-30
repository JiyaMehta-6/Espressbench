# Espressbench

[![CI](https://github.com/JiyaMehta-6/Espressbench/actions/workflows/ci.yml/badge.svg)](https://github.com/JiyaMehta-6/Espressbench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Espressbench** (CLI: `espbench`) - hardware-in-the-loop evaluation bench for
Espressif WiFi firmware — **chaos engineering for $5 boards**.

Firmware claims are usually unmeasured: "stable", "low latency", "memory safe".
This bench turns claims into numbers with a MicroPython agent on the device and a
host-side harness over WiFi. No cloud, no accounts, no paid tools.

## Compatibility

The host never touches a chip register - it speaks HTTP/JSON to the MicroPython
agent - so live support follows one rule: **MicroPython + WiFi**. That covers
**8 of the 10 Espressif SoC families MicroPython supports**, and every module or
devkit built on them (WROOM, WROVER, MINI, DevKitC, DevKitM, Saola, NodeMCU,
D1 mini, ...) inherits compatibility unchanged.

| SoC family | WiFi | Live benches | Notes |
|---|---|---|---|
| ESP8266 | ✓ | ✓ | the original $2 WiFi chip |
| ESP32 (SOLO / WROOM / WROVER) | ✓ | ✓ | dual-core Xtensa LX6, reference target |
| ESP32-S2 | ✓ | ✓ | single-core, native USB |
| ESP32-S3 | ✓ | ✓ | dual-core + PSRAM |
| ESP32-C2 | ✓ | ✓ | smallest RAM budget (272 KB) |
| ESP32-C3 | ✓ | ✓ | RISC-V single-core |
| ESP32-C5 | ✓ | ✓ | dual-band WiFi 6 |
| ESP32-C6 | ✓ | ✓ | WiFi 6 |
| ESP32-H2 | ✗ | sim / replay only | BLE + 802.15.4, no WLAN for `/ping` |
| ESP32-P4 | ✗ | sim / replay only | no radio on die; pairs with a C6 |

Radio-less parts still run every hardware-free feature - `--sim` suites, replay
fixtures, `diff`, `compare`, `check`, insights, charts - only live `run`,
`soak`, `chaos` and `replay-record` need the radio.

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
`soak: iteration 12 ok`, `chaos: fault refuse (1/4)`), so pipes and redirects
stay clean while CI logs show exactly where a run is. Device commands accept
`--timeout SECONDS` (default 5) for slow links, and any file `--out` may be a
directory - the command's default filename is used inside it.

## Manual

A guided tour from a bare checkout to a CI gate. Every block is copy-paste, and
every step's exit code is the one CI uses (`0` pass, `1` fail, `2` bad input -
see **Commands** below). Hardware-free commands accept `--sim` instead of
`--host`.

### 1. Preflight

```bash
espbench doctor                       # python/GUI versions, local files, probes
espbench doctor --host 192.168.1.42   # ...and can the board be reached?
espbench run --sim --suites latency --latency-n 10   # smoke-test the pipeline
```

`doctor` exits 1 when a probe fails, so it gates CI as-is. Run the simulator
once before touching hardware: it exercises the identical code path minus the
radio.

### 2. Run suites against real hardware

```bash
espbench run --host 192.168.1.42 --suites latency,memory,fuzz \
  --latency-n 300 --memory-samples 40 --settle 2 --out reports/
```

Progress streams to stderr, so redirects stay clean. The run writes
`reports/report.json`, `reports/report.md` and `reports/junit.xml`. Knobs:
`--port 8080` for a non-default agent port, `--timeout 10` for slow links,
`--repeat 5` for distributions (step 5).

### 3. Read the verdict

```bash
espbench insight reports/report.json            # advisory hints on any report
espbench insight reports/report.json --strict   # exit 1 on warnings: the CI form
espbench chart reports/report.json --unit ms    # histogram -> reports/report.svg
```

`run` already prints an **Insights** section when a report has something to
say - clean runs stay quiet. `--strict` turns every warning into exit 1.

### 4. Freeze a run, gate the next one

```bash
espbench baseline reports/report.json           # golden run -> baseline.json
espbench run --out reports/                     # candidate firmware
espbench compare reports/report.json            # exit 1 on any regression
espbench check reports/report.json --budget "latency.p95<=50" \
  --budget "memory.leak_detected=false"         # exit 1 on any breach
```

`compare` prints per-metric deltas with automatic directions and refuses
reports from different firmware (exit 2) unless you pass `--allow-mismatch`.
Rather than hand-writing budgets: `espbench baseline reports/report.json --auto
--margin 20` derives `budgets.json` (ceiling = value + 20 %), then
`check --budgets budgets.json` gates on it.

### 5. Repeat until it is statistics, not luck

```bash
espbench run --repeat 5 --out a/ && espbench run --repeat 5 --out b/
espbench compare a/report.json b/report.json    # regression | improved | ok | flaky
```

Medians across repeats, a bootstrap confidence interval on the difference, and
noisy metrics labelled `flaky` (they never fail the gate). Per-run JSON lands
under `out/runs/`.

### 6. Break it on purpose (chaos)

```bash
espbench chaos --host 192.168.1.42 --faults refuse,delay,cut --duration 2
espbench chaos --host 192.168.1.42 --schedule "refuse:2s,normal:1s,refuse:2s"
```

Fault modes: `refuse`, `delay`, `corrupt`, `cut` (`normal` is the control
phase a schedule uses to measure recovery). The report only counts faults that
actually landed - an ineffective proxy or an unrecovered fault fails the run.

### 7. Leave it running overnight (soak)

```bash
espbench soak --host 192.168.1.42 --hours 8 --interval 60 --suites latency,memory
espbench soak --sim --hours 0                  # single health pass, exit 0/1
```

Health (or the chosen suites) every interval until the hour budget runs out;
any failure or reboot fails the run, and the last 50 events ship in the report
for post-mortem.

### 8. Work without hardware (record, replay, diff)

```bash
espbench run --host 192.168.1.42 --out fw-1.0/ --bundle
espbench run --host 192.168.1.42 --out fw-1.1/ --bundle
espbench run --fixture fw-1.0/session.json          # board unplugged
espbench diff fw-1.0/session.json fw-1.1/session.json   # exit 1 on behaviour change
```

Fuzz-only shortcuts: `replay-record --out session.json` and `replay-run
--fixture session.json`. A replay whose fixture has steps left over exits 1 -
the suites did not consume the recording. `--fixture` cannot be combined with
`--host` or `--sim`.

### 9. Measure what it draws (power)

```bash
espbench power --csv power_log.csv --markers markers.csv --capacity 1200 \
  --out power_report.md
```

Logger CSV columns are `time_ms,mA`; the optional markers file
(`time_ms,label`) aligns operations to current draw, and `--capacity` (mAh)
turns the trace into a battery-life projection.

### 10. Wire it into CI

```bash
espbench init                          # -> .github/workflows/espbench.yml
espbench badge reports/report.json --metric p95 --out reports/badge.svg
```

Every report-producing command appends its markdown to `$GITHUB_STEP_SUMMARY`
automatically; JUnit XML and live-hardware reports upload as artifacts. Gate on
exit codes only - no wrapper scripts.

### 11. From the desktop

```bash
espbench gui                           # or: espbench-gui
```

Five tabs (Suites, Soak, Chaos, Reports, Power): set host or sim, **Test
connection**, run, watch live progress, export. Same reports as the CLI, host
and port remembered between sessions, fully offline.

### 12. When it goes wrong

| You see (exit) | Meaning | Fix |
|---|---|---|
| `error: --host is required (or pass --sim)` (2) | live command with no target | add `--host <ip>` or `--sim` |
| `error: firmware mismatch: ... --allow-mismatch ...` (2) | comparing reports from different agent builds | re-record the baseline, or compare on purpose with `--allow-mismatch` |
| `error: unknown suite 'x'; expected one of ...` (2) | typo in `--suites` | valid suites: `latency`, `memory`, `fuzz` |
| `error: invalid budget ...` (2) | malformed `--budget` | `METRIC=LIMIT`, `METRIC<=LIMIT`, `METRIC>=LIMIT` or `METRIC=false` |
| `error: --fixture cannot be combined with --host or --sim` (2) | replay mixed with a live target | drop the device flags for fixture runs |
| `run: fixture has N unused steps` (1) | replay never reached the end of the recording | match the suites/params used when recording, or treat as a real failure |
| `doctor` probe failed (1) | board unreachable | check IP/WiFi, raise `--timeout` for slow links |
| chaos fault reported *ineffective* (1) | the proxy never sat in the path | use a positive `--duration` and a `--port` that matches the agent |

## Desktop GUI

`espbench gui` (or the `espbench-gui` shortcut) opens a dark-themed PySide6
window with five tabs:

| Tab | What it does |
|---|---|
| **Suites** | host/sim config, suite picker, params + **repeat runs**, **Test connection** probe (fw + ping), live progress streaming into the results pane, markdown results with Insights, latency histogram preview |
| **Soak** | hours/interval, optional suites per iteration, live iteration feed, pass/fail verdict |
| **Chaos** | fault-mode picker, duration/recovery/delay knobs, timed schedule field, per-fault progress, recovery summary |
| **Reports** | load any saved `report.json`, render it with hints, **Set as baseline**, **Compare to baseline** (with tolerance), **Check budgets** against a `budgets.json` |
| **Power** | current-log CSV + markers analysis, battery projection, markdown export |

Everything runs in a background thread with a progress bar; **Export reports**
writes the same `report.json` / `report.md` / `junit.xml` as the CLI plus a
`latency.svg` chart. Host, port and simulator preference are remembered between
sessions (QSettings), window geometry is restored on reopen, and the device
probe never touches the network in sim mode. The GUI is fully offline - no
telemetry, no accounts.

## Commands

| Command | What it does |
|---|---|
| `run` | run suites live, `--repeat N` for distributions, `--bundle` to record, `--fixture` to replay (exits 1 if the fixture has unused steps) |
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
| `1` | suite failure, regression, unrecovered fault, budget breach, `doctor` probe failure, `insight --strict` warnings, replayed fixtures with unused steps |
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
