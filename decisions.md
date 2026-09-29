# Decisions

1. **Scope.** The bench measures five things host-side (latency, memory, fuzz, chaos,
   power) plus record/replay, so every claim in the README maps to a runnable suite.

2. **MicroPython agent, not a custom firmware.** A plain `agent.py` + `boot.py` pair
   runs on stock MicroPython; no SDK, no build chain, no vendor login. Flashing uses
   free tools (`mpremote`, Thonny).

3. **HTTP on port 80 for the control channel.** One protocol for `/stats`, `/ping`,
   `/echo`, `/mark`, `/sensor`, `/clock`, `/restart` keeps the host code in
   `requests`-only territory and makes the fault proxy protocol-agnostic.

4. **Reboot detection via `X-Boot-Count` header + `/stats.boot_count`.**
   Fuzzing needs to distinguish "slow response" from "device rebooted"; a persistent
   boot counter file does that without extra hardware.

5. **Chaos is a TCP proxy, not firmware patching.** `FaultProxy` sits between host and
   device and injects `refuse` / `delay` / `corrupt` / `cut`. This tests the *client's*
   recovery behaviour too, and it works against any TCP service (MQTT later).

6. **Delay faults are judged by latency, not failure.** A `delay` fault keeps the
   connection healthy by design, so `fault_took_effect` for `delay` is
   `probe_ms_max >= delay_ms`; for the other modes it is `healthy is False`.

7. **Record/replay as JSON fixtures (version 1).** A recorded HIL session replays
   without hardware, so a desk-flaky run becomes a CI test. `ReplayDevice` validates
   op order, payload bytes, and errors, and raises `ReplayError` on mismatch or
   exhaustion.

8. **Power analysis from logger CSVs, not on-device ADC.** Free loggers (e.g. Energy
   Profiler exports, plain `time_ms,mA` dumps) are parsed with the stdlib `csv`
   module; `energy_mah` uses trapezoidal integration, and optional `time_ms,label`
   markers attribute energy to named operations.

9. **Simulator first-class (`--sim`).** `FakeDevice` supports latency/jitter, leak
   rate, error payloads, and reboot payloads, so every suite and the GUI run with zero
   hardware — CI included.

10. **PySide6 GUI with a worker `QThread`.** Runs never block the UI; results render
    inline (Markdown) and export through the same `report.py` writers as the CLI.
    GUI tests run offscreen (`QT_QPA_PLATFORM=offscreen`).

11. **Three report formats from one result dict:** JSON (machine), Markdown (humans),
    JUnit XML (CI test reporting). Same shape for suites and chaos.

12. **Separate `uv` venv per repository** (`uv venv --python 3.12`), editable install
    with `[dev,gui]` extras, `requirements.txt` frozen via `uv pip freeze` for
    non-uv users. Only free, offline-capable dependencies: `requests`,
    `PySide6`, `pytest`, `ruff`.

13. **Commands exit non-zero on failure.** `espbench run` returns 1 when the device is
    unreachable, a latency sample errors, a fuzz case fails, or a memory leak is
    detected; `espbench chaos` returns 1 unless every fault recovered; input errors
    (missing files, bad arguments) return 2 with a one-line message. CI gates on the
    exit code, not on parsing the report.

14. **The agent reads the full request body before dispatching.** `Content-Length`
    bytes are awaited after the header block, so a payload split across TCP segments
    (any POST over a few hundred bytes) no longer produces a spurious 400.

15. **The agent stays importable on CPython.** `network` is imported inside
    `connect()`, and `uptime_s()` / `stats()` fall back to `time.time()` / `None`
    when MicroPython-only APIs are absent — which lets the request handler itself be
    unit-tested in CI with a scripted fake socket.

16. **`/stats` body failures are `DeviceError`, not `JSONDecodeError`.** A corrupt or
    truncated JSON body used to escape the fuzz suite's `except DeviceError` and abort
    the whole run with a traceback; it is now wrapped, so one bad response counts as
    one error. Likewise a sample with `mem_free: null` counts as a memory error instead
    of crashing the suite with `TypeError`.

17. **Proxy/simulator teardown is safe without a start.** `socketserver.shutdown()`
    blocks forever if `serve_forever` never ran — verified: `FaultProxy.stop()` and
    `EchoServer.stop()` now skip `shutdown()` unless their thread was started, so a
    double-stop or stop-without-start cannot hang a script or test.

18. **A chaos run must prove the fault actually landed.** The report carries
    `faults_effective` / `all_faults_effective`, and `espbench chaos` exits non-zero
    unless every fault took effect *and* every fault recovered — a green run where the
    proxy was never in the path (duration 0, misconfigured port) now fails. CLI
    `--corrupt-rate` defaults to 1.0 so the `corrupt` mode is deterministic in CI;
    callers wanting probabilistic corruption can lower it.

19. **Input-shape failures are `ReplayError` / exit 2.** `load_steps` rejects
    non-object JSON and missing/non-list `steps` (previously an uncaught
    `AttributeError` traceback); `parse` sorts `(time_ms, mA)` rows so unsorted logger
    exports cannot produce negative durations or negative energy; `espbench chaos`
    requires at least one fault and (without `--sim`) a `--host`. Control-op recording
    (`mark` / `set_sensor` / `restart`) now actually forwards to the inner device, so a
    fixture records what really happened.

20. **JUnit output must be valid XML on failures.** `xml.sax.saxutils.escape` does not
    escape `"`, and every failure message is built with `json.dumps` — so the first
    failed fuzz case (or any quote-bearing message) produced an unparseable
    `junit.xml`, breaking GitHub test reporting exactly when it matters. Attributes
    now use `quoteattr`; a test round-trips a quote-laden message through
    `ElementTree.fromstring`.

21. **One failure predicate feeds exit codes and JUnit.** `hil.suite_failed` is the
    single source of truth: `cli._run_failed` and `report._collect_cases` both call
    it, and the device-error case is emitted explicitly. Previously JUnit showed
    latency runs with `errors > 0` as green while the CLI exited 1, and a
    device-unreachable run wrote an all-green `junit.xml` with exit 1.

22. **Empty `--suites` is an input error, and suites are validated before I/O.**
    `espbench run --suites " , "` used to write a report containing only device
    stats and exit 0 (the vacuous-pass class already closed for `--faults` in #18);
    `run_suites` now raises `ValueError` before calling `device.stats()`, so a
    typo'd suite fails fast without touching the network.

23. **Firmware request parsing degrades to 400, not 500.** A non-integer
    `Content-Length` or `/clock?offset=abc` raised through to `serve`'s generic
    500 handler; invalid UTF-8 in the request line or header values raised
    `UnicodeDecodeError`. Decoding uses `errors="replace"` and both parses are
    guarded, so malformed probes get deterministic 400/normal responses.

24. **Reports escape their own cells; version strings are single-sourced.**
    Markdown `_cell` escapes `\`, `|`, and newlines in leaf values (and row names),
    so a payload error or group label containing `|`/newline cannot break report
    tables. `ReplayError` subclasses `ValueError`, making #19's "exit 2" literal —
    bad fixtures now print `error: ...` instead of a traceback. The CLI version
    comes from `espbench.__version__`, pinned to `pyproject.toml` and to the
    firmware's `espbench-agent/...` header by tests, and `python -m espbench` is
    covered by a subprocess test. The README sample output now matches the real
    `report.json` shape (`{"suites": ..., "device": ...}`).

25. **Closing mid-run no longer crashes the GUI; export reflects only
    completed runs.** Audit 4 found that closing the window while the suite
    QThread was running destroyed the thread's target and logged
    "QThread: Destroyed while thread is still running", and that the
    export button could stay enabled after a failed run, offering stale
    results. `MainWindow.closeEvent` now quits a running thread and waits
    1.5s, ignoring the close (with an information dialog) if the run is
    still active; `SuitesTab._thread` is cleared on `finished` so the
    guard never touches a `deleteLater`-ed object; `on_run` disables the
    export button and only a successful `_on_finished` re-enables it.
    Also in this pass: `run --suites` gained inline help, CI pushes to
    `main` only (was: every branch, duplicating pull-request runs), runs
    `uv lock --check`, uploads junit XML as a CI artifact, and the
    hardware job uploads `live-reports/` instead of discarding it.
    Tests: 96 -> 100.

26. **Report renderer matches the table layout used by mleval.**
    `to_markdown` now recurses the same way: scalar payloads become
    `| field | value |` tables; the suites payload, whose latency and memory
    rows share few columns, renders as `### latency` / `### memory`
    subsections instead of one 18-column union table; sparse dicts-of-dicts
    anywhere get the same treatment; headings capitalise the first letter
    (`suites` -> `Suites`); lists of dicts render as tables while scalar
    lists stay bullets (`- 1`). `_cell` keeps its 160-char truncation for
    nested payloads and the junit writer is untouched, so all escaping and
    XML tests pass unchanged.

27. **Perfection pass: input validation, dead-device fast-fail, stale GUI
    export.** Every CLI flag that could reach a crash or a silent thread
    death now has a range check, so bad input exits 2 with a named
    argument instead of a traceback or a hung run: `--port` 1-65535
    (was: OverflowError outside main's try), `--latency-n` /
    `--memory-samples` >= 1, `--memory-interval` / `--settle` /
    `--duration` / `--recovery-timeout` / `--delay-ms` >= 0 (`--duration
    0` stays valid and still reports "ineffective", per the pinned test),
    `--corrupt-rate` in [0, 1], `--capacity` >= 0 (0 = no projection).
    `run_chaos` and `FaultController.set` validate the same bounds at the
    library boundary (a negative delay would otherwise kill the proxy's
    pump thread with an uncaught ValueError and mark later faults
    ineffective), and `battery_life_hours` rejects capacity <= 0.
    `run_suites` now probes `ping()` once when `stats()` fails and skips
    all suites if that also fails: an unreachable device fails in two
    quick connection attempts instead of ~110 timed-out pings, while a
    device whose stats endpoint alone is broken still runs suites
    (existing test pins that). GUI: `PowerTab.on_analyze` clears the
    previous report and disables Export up front, so a failed re-analysis
    can no longer offer the stale first report for export. `_cell(None)`
    renders `_none_` (battery projection with a zero-current log used to
    print the word "None"). Replay fixtures are validated on load (each
    step must be an object with a string op) and on use (`stats` needs a
    result object, `echo` a status), and control-op recording now stores
    and replays the actual returned status instead of always `True`.
    pyproject gains classifiers and project URLs. Tests: 100 -> 131.

28. **Quality gates: baseline, compare, check.** New espbench/gate.py turns any
    report JSON into flat dotted metrics (named lists like fuzz cases and chaos
    faults flatten under their name; strings, NaN, unnamed lists and _meta keys
    are skipped). Three commands ride on it with the usual 0/1/2 exit contract:
    'baseline' freezes a report as baseline.json with a version/timestamp stamp;
    'compare' (one arg = candidate vs ./baseline.json, two = before after) prints
    a per-metric before/after/delta table and exits 1 when a metric worsens beyond
    --tolerance percent - directions are automatic (higher-better for mem_free,
    recovered, faults_effective, hours, bytes_per_s; neutral for counts like n,
    samples, total; bools judged by a badness table: leak_detected true is bad,
    passed false is bad), any worsening from an exact 0 always fails regardless of
    tolerance, and disjoint reports are an input error; 'check' asserts budgets
    (METRIC=LIMIT ceiling, METRIC>=LIMIT floor, METRIC=false equality) resolved by
    exact or unique suffix path (p95<=50 -> suites.latency.p95), fed by repeatable
    --budget flags and/or a --budgets JSON file, exiting 1 on any breach. Report
    JSON gains no new required keys; pyproject version untouched. Tests: 131 -> 156.

29. **Statistical A/B: repeat, bootstrap, flaky, auto-budgets.** run gains --repeat N
    (default 1, unchanged output): gate.aggregate_runs rebuilds the report from N runs
    with statistics.median for scalar leaves, all() for bools, worst-case max() for the
    HARD counters errors/reboots, and any run's device error surviving wholesale; the
    report gains repeat.count plus repeat.metrics rows (metric/median/min/max/values)
    which flatten skips (list without name/fault/label keys), each raw run is also
    written to out/runs/report-NN.json, and the exit code fails if ANY run failed.
    compare now reads those distributions: when both sides have >=3 numeric samples
    for a metric (not a HARD counter, not bools, not a zero-before case), a
    1000-resample bootstrap CI (seed 42) of the median difference gates the verdict -
    point-worse beyond tolerance only counts as regression when the CI excludes 0 in
    the worse direction, stable gains become improved, coefficient of variation above
    25% labels the metric flaky (summary gains a flaky count, never gates), and every
    other case falls back to the old point logic (Phase 1 tests untouched, 156 still
    green). baseline gains --auto/--margin (default 20): budgets.json of ceilings
    value+margin%, floors >=value-margin% for higher-better metrics, bools budgeted
    only when currently good, neutral counts and unknown bools skipped, round-tripped
    through check; plain --out default stays baseline.json (auto default budgets.json).
    pyproject version untouched. Tests: 156 -> 176.

30. **Firmware differential: fw gate, /log, bundles, fixture diff.** The agent gains
    FW_VERSION (single source for the 'agent' stat string, package-version test still
    green), a 'fw' field in /stats, GET /version, and GET /log - a 64-entry ring buffer
    fed by boot/mark/sensor/restart/error events. Device grows version()/log() with the
    usual DeviceError contract; RecordingDevice/ReplayDevice learn both ops so fixtures
    round-trip them. run_suites fills device.fw from device.version() when stats omits
    it (best effort; old agents simply lack the field) so every report carries a
    firmware tag. compare now refuses reports from DIFFERENT firmware with exit 2 and a
    --allow-mismatch hint (strings never reach flatten, so gate reads device.fw
    directly via firmware_version()); with the flag a Firmware section documents the
    cross-version comparison. run --bundle records the entire session (stats, version,
    echoes, ...) to session.json (or out/session.json, or a custom path); new 'diff'
    command compares two fixtures step-by-step on BEHAVIOUR signatures - echo
    payload+status, control-op outcomes, stats sensor, error strings - deliberately
    ignoring volatile latency_ms/uptime/mem_free/fw banners, truncating long rows,
    exiting 1 on any changed/only-before/only-after step (unix diff convention).
    pyproject version untouched. Tests: 176 -> 201.

31. **Lab sessions: soak, timed chaos schedules, fixture runs, log-on-failure.**
    chaos gains --schedule "refuse:2s,normal:1s,..." as a replacement for the
    per-fault --faults/--duration timing: parse_schedule validates MODE:DURATION
    phases (ms/s/m/bare seconds, unknown mode -> ValueError before any proxy
    traffic) and run_schedule probes each phase window, appending duration_s,
    healthy_under_fault, probe_ms_max, fault_took_effect, and recovery fields;
    a normal phase right after a fault measures recovery inside min(window,
    recovery_timeout), and a final drain does the same for trailing faults - the
    report mirrors run_chaos plus a schedule list, so exit codes and sections
    stay unchanged. hil gains run_soak: hours/interval are validated up front,
    at least one iteration always runs, each iteration is either run_suites
    (device error or suite failure counts) or stats+ping health, reboots are
    detected via boot_count increases, and events are capped at 50 with an
    events_dropped counter; passed = no failures and no reboots (exit 0/1).
    run_suites now attaches the device log whenever the payload has an error or
    any suite failed (best effort, inside try/except) and _sections renders it
    as a Device log section; aggregate_runs picks up the log from the first run
    that has one. replay fixtures carry optional run metadata: save_steps writes
    {suites, params} and load_fixture validates it (bad run -> ReplayError),
    so espbench run --fixture bundle.json replays without repeating CLI flags -
    meta suites/params win over CLI, device flags (--sim/--host) conflict with
    --fixture (exit 2), each repeat gets a fresh ReplayDevice, and --bundle
    merges per-run recorders back into one session.json with the run meta.
    RecordingDevice now snapshots stats/log dicts into steps: run_suites fills
    device.fw by mutating the stats payload, which used to leak fw into the
    recorded fixture and desync replay (version step skipped, latency mismatch
    swallowed by its broad except). FakeDevice serves version/log so sim bundles
    round-trip. pyproject version untouched. Tests: 201 -> 235.

32. **Insights and charts: advisory hints, dependency-free SVG histograms.**
    insight.hints(report) scans a report (any shape: suites, chaos, soak, device)
    and returns prefixed strings - warn: dropped pings, tail latency (p95 >= 3x
    p50), memory leak or restart, fuzz reboots/errors, generic suite-failure
    fallback for anything unmentioned, chaos unrecovered/ineffective faults,
    soak reboot/error/failure counts, unreachable device or bad sensor - while
    clean reports return nothing. run/chaos/soak append an Insights section to
    their console output only when hints exist (report formats untouched), and
    the new 'insight' command prints the same hints for any saved report (exit
    0 always; unreadable/invalid file -> 2 via the usual error path). latency
    now records "_samples" (raw post-warmup pings): underscore keys are skipped
    by gate.flatten AND by report._render (both the scalar/container split and
    the name-row splat, which would otherwise leak them into markdown tables),
    so samples reach report.json for charting without polluting markdown,
    junit, baselines, or budgets. svg.histogram_svg(values, title, unit) draws
    a 640x320 bar chart with Sturges-capped bins (max 30), XML-escaped labels,
    axis ticks, and a "no data" empty state - pure string building, no
    dependencies. the new 'chart' command loads a report, prefers
    suites.latency._samples then repeat.metrics[*].values (else exit 2 with a
    fix-it message), and writes report.svg next to the report by default.
    pyproject version untouched. Tests: 235 -> 257.

33. **CI wow: step summaries, badges, completions, workflow scaffold.**
    Every report-producing command (run, chaos, soak, compare, check, insight,
    init) now appends its markdown to $GITHUB_STEP_SUMMARY when the variable is
    set - _append_summary opens the file in append mode and swallows OSError
    (unset var = no-op, a directory as path = still exit 0), so CI needs no
    wrapper scripts. svg gains badge_svg(label, value, color): shields-style
    two-segment 20px SVG with clipPath rounding, 7px-per-char width estimate,
    and badge_color validating shields names (brightgreen, red, blue, ...) or
    hex - the new 'badge' command resolves a metric through gate.resolve_metric
    (unknown/ambiguous -> exit 2 with close-metric hints), renders bools as
    pass/fail (auto green/red) and numerics blue by default, to stdout or --out.
    completion.py generates bash/zsh/fish scripts straight from build_parser()
    via the argparse _SubParsersAction (command list at position 1, per-command
    options deeper; zsh gets #compdef + _describe with help text, fish gets
    per-flag -l/-s completes with -d descriptions) - exposed as the top-level
    --completions bash|zsh|fish flag, which required making subcommands
    optional: main() prints the script, else parser.error('the following
    arguments are required: command') keeps the old exit-2 behavior for a bare
    'espbench'. init scaffolds .github/workflows/espbench.yml (tests job:
    ruff+pytest; sim-report job: sim run + insight + badge + artifact upload)
    and refuses to overwrite without --force (exit 2). README gains a full
    command table, an exit-code contract table, and a rewritten CI section.
    The repo's own .github/workflows/ci.yml (hand-tuned: 3.10/3.12 matrix, uv,
    lock check, firmware syntax check, manual hardware job) gains a sim-report job
    that dogfoods the new features - sim run + insight to the step summary, badge
    and histogram SVGs in the artifact - while the README badge keeps pointing at
    ci.yml. pyproject version untouched.
    Tests: 257 -> 272.

34. **Audit-driven hardening: logic, flow, and a 4-tab GUI overhaul.**
    A full-codebase audit (buckets: logic bugs, CLI flow gaps, GUI capability
    gaps) drove this pass. Logic: fuzz reboot detection no longer KeyErrors on
    a stats payload without boot_count (and no longer silently skips detection
    when the pre-echo stats failed - unknown is conservatively counted as a
    reboot, matching the suite's pass/fail contract); fuzz `bytes` now measures
    the UTF-8 encoding, not str length; suite_failed() treats
    device_restarted=True as a failure (exit 1, JUnit failure, device log
    attached), aligning hil with gate.BAD_BOOL and insight; run_suites()
    validates fixture/CLI suite params against the suite signature before
    touching the device (typo -> ValueError -> exit 2 instead of an
    uncatchable TypeError traceback) and gained a progress= callback that the
    CLI prints per suite on stderr (stderr keeps stdout pipes/redirects clean);
    run_soak() gained progress=, counts boot_count changes in BOTH directions
    (reflash mid-soak is a reboot too), and floors interval=0 to 1s only when
    hours>0.01 so a long tight loop cannot become a request storm (the
    hours=0 single-pass fast path stays instant); memory.run sleeps between
    failed samples so a sick device is not hammered back-to-back. gate:
    NEUTRAL += iterations/elapsed_s/hours_planned (two healthy soaks with
    different wall-times no longer compare as regressions); parse_budget
    rejects inf/nan/1e999 (a typo exponent used to mint an always-pass budget);
    flatten() falls back to index paths for duplicate-named rows (repeat
    refuse faults used to drop every per-fault metric out of
    check/compare/badge - now chaos.results.1... stays visible, unnamed lists
    still skipped as pinned), mirrored by aggregate_runs' rebuild; 
    aggregate_runs() computes medians from runs that actually have suites (a
    dead first run no longer erased every later metric) while still stamping
    the first device error onto the combined report; write_reports() emits
    strict JSON (non-finite floats become null, allow_nan=False) so jq/CI can
    read every report. chaos: run_chaos clamps its sleep to the duration
    deadline (no overshoot), and the whole proxy-target setup moved from
    cmd_chaos into chaos.run_target() (shared by CLI and the new GUI chaos
    tab) with the real-device probe timeout scaled to delay_ms
    (max(5s, delay/1000+2) instead of a hard 1s that mislabelled deep delays
    as ineffective). insight flags p95 tails even when p50 is exactly 0.
    Flow: `espbench doctor` (python/PySide6 versions, baseline/budgets/report
    presence, workflow detection, optional device probe with fw+ping; exit 1
    only on a failed probe, skip/warn never fails) and `espbench gui` (lazy
    import) join the command list; insight gains --strict (exit 1 on warn:
    hints) so advisories can gate CI; _append_summary now covers power, diff,
    baseline, replay-record/-run, chart and badge too; cmd_check merges
    --budget flags over --budgets file entries by RESOLVED metric path (a CLI
    override actually wins instead of double-counting); _in_range says "an
    int"; main() reconfigures stdout/stderr to utf-8 with errors=replace
    (device logs with non-CP1252 characters no longer crash a finished run
    with exit 2). Sections builders moved report_sections/chaos_sections/
    soak_sections into report.py for CLI/GUI parity. GUI: rewritten around the
    pinned test contract - Suites tab gains a Test connection probe
    (fw + ping, never touching the network in sim mode), a progress bar +
    status line fed by the new run_suites progress callback, Insights merged
    into the rendered report, a QSvgWidget latency-histogram preview (also
    written as latency.svg by Export), and QSettings persistence of
    host/port/sim; new Soak tab (hours/interval/suites, iteration feed,
    pass/fail verdict, export) and new Chaos tab (fault checkboxes,
    duration/recovery/delay, schedule field) run through SoakWorker/
    ChaosWorker on QThreads sharing the RunWorker wiring pattern;
    MainWindow adds File/Help menus (About shows the version), a status bar,
    a dark Fusion stylesheet (STYLE), and closeEvent now guards all three run
    threads; RunWorker keeps its positional signature and emits a new
    progress signal. pyproject version untouched (0.1.0).
    Tests: 272 -> 309.
