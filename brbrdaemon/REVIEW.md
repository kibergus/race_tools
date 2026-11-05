# brbrdaemon Code Review

Scope: everything under `brbrdaemon/` (`src/*.cpp|h`, `CMakeLists.txt`, `build.ps1`, `README.md`, test config). `thirdparty/nlohmann/json.hpp` was not reviewed.

Each finding below is written as a **self-contained prompt** for a coding agent. Findings are ordered by severity:

- **P0**: crash, undefined behaviour, or silent data loss/corruption in normal use
- **P1**: incorrect behaviour or a robustness bug that users will hit
- **P2**: incorrect data, misleading docs, dead code, maintainability
- **P3**: tests and tooling

General constraints for every task: keep the existing code style (2-space indent, `race_tools` namespace, Apache header). Do not touch `thirdparty/`. Add or extend a unit test in `src/unit_tests.cpp` whenever the behaviour can be tested. Make sure `.\build.ps1 -Target test` still passes.

---

## P0 — Critical

### 1. Detached sink-joiner threads outlive `Logger`, `TelemetryCollector` and `main()` (use-after-free + lost data on exit)

**Where:** [telemetry_collector.cpp `EndSession`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L611-L635), [telemetry_collector.cpp `Stop`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L195-L205), [main.cpp shutdown](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L414-L438), [mock_runner.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/mock_runner.cpp)

> **Prompt:** `TelemetryCollector::EndSession()` moves `sinkWorkers_` into a `std::thread(...).detach()`. The workers' sinks hold `Logger&` (a stack object in `main`) and `std::atomic<int>& activeUploads_` (a member of the collector). When `Stop()` is called during shutdown, `main()` returns right away. The `Logger` and the collector are destroyed while the detached thread is still flushing the CSV or retrying the HTTP upload (which can take minutes). That is a use-after-free, and the process exit also kills the final CSV flush and HTTP finalize.
> Fix it like this:
> (a) Stop detaching. Keep "retiring" workers in a collector-owned container, such as `std::vector<std::unique_ptr<SinkWorker>> retiringWorkers_` guarded by a mutex. Reap finished ones periodically from the polling thread, using a `done` flag on `SinkWorker`.
> (b) Add `TelemetryCollector::Stop(std::chrono::milliseconds drainTimeout)`. It ends the session, waits up to `drainTimeout` for all workers (current and retiring) to finish, then calls `SinkWorker::Abort()` on the rest and joins them. `SinkWorker::Abort` is currently never called.
> (c) In `main.cpp`, call `Stop` with a sensible timeout, for example 10 s normally and ~3 s on `CTRL_CLOSE_EVENT`/`WM_ENDSESSION`. Make sure `logger` outlives every worker.
> (d) `SinkWorker::Abort()` must also unblock `reader_.Pop()`. Today `active_ = false` is only checked after `Pop` returns, so call `SignalShutdown()` on the queue or add an abortable wait.
> (e) Make `HttpSink::operator()(EndSession)` back-off sleeps interruptible, for example with a condition variable that `Abort()` signals, instead of `std::this_thread::sleep_for`.
> Add a unit test that calls `collector.Stop()` and then immediately asserts that the CSV file is complete and closed, i.e. the last line ends with `\n` and the file can be opened exclusively.

### 2. Queue capacity overflow can drop `StartSession`/header/`EndSession` control messages

**Where:** [safe_queue.h `EnforceCapacityLocked`](file:///mnt/windows/code/race_tools/brbrdaemon/src/safe_queue.h#L238-L252), [telemetry_collector.cpp sessionQueue creation](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L471)

> **Prompt:** `SafeQueue<Sink::Message>` is created with capacity 50000. When a slow reader falls behind (e.g. `HttpSink` blocked in `WinHttpWriteData` or in the multi-minute `EndSession` retry loop), `EnforceCapacityLocked()` pops the oldest items *regardless of type*. If those items include `Sink::StartSession`, the metadata header `Line`s (`is_header=true`) or `Sink::EndSession`, the sink ends up in an inconsistent state: no file open, CSV without header, or a session that is never finalized. Drops are also completely silent.
> Change the overflow policy so control messages and header lines are never dropped. Options: only evict `Line` items with `is_header == false`, or keep control messages in a separate side channel. Log a warning, rate-limited, when samples are dropped for a reader, and expose a per-reader dropped counter. Add a unit test that overflows a queue with a slow reader and verifies that `StartSession`, the header lines and `EndSession` are still delivered in order.

### 3. Config fallback creates duplicate file sinks that write to the same file

**Where:** [main.cpp config load](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L308-L340)

> **Prompt:** In `main()`, when the config file does not exist, the code pushes a file sink and an http sink into `config.sinks` and then calls `SavePluginConfig`. If `SavePluginConfig` throws (e.g. a read-only install dir such as `Program Files`), the `catch` block pushes *another* file sink onto the already populated `config.sinks`. That gives two `FileSink`s with the same directory and the same generated filename, both opened with `std::ios::trunc`, so the CSV is corrupted.
> Restructure the code: build a `PluginConfig MakeDefaultConfig(const std::wstring& telemetryDir)` helper. Load or create into a local variable, and on any failure assign `config = MakeDefaultConfig(...)` (assign, don't append). Wrap `SavePluginConfig` in its own try/catch that only logs. Also dedupe sinks by `(type, resolved path / server address)` in `TelemetryCollector::StartSession` as a defensive measure.

### 4. Seqlock read never reports failure; torn reads are silently accepted

**Where:** [ace_shared_memory.cpp `ReadSeqlockStruct`](file:///mnt/windows/code/race_tools/brbrdaemon/src/ace_shared_memory.cpp#L75-L91)

> **Prompt:** `ReadSeqlockStruct` returns `true` even after all 5 attempts saw `idBefore != idAfter`, so torn snapshots are passed to the collector. This contradicts the README's claim of "consistency against torn reads". Return `false`, or a distinct "inconsistent" status, after the retries are exhausted. In `TelemetryCollector::PollingThreadWorker`, treat that as "skip this tick", *not* as a disconnect: today `!physOk` triggers `EndSession()` + `Disconnect()`, which would be wrong for a transient torn read. Distinguish "not mapped" from "inconsistent snapshot", e.g. with an enum `ReadResult { Ok, Torn, NotConnected }`. Note that the static page is read with a plain `memcpy` in `ReadUnifiedStatic`/`ReadStatic`/`ReadStaticClassic`, and that the Classic graphics path in `ReadUnifiedGraphic` reads strings directly from `pStaticRaw_` without copying. Document why that is acceptable, or read the static page into a local copy once per tick.

### 5. Shared-memory views are read with `memcpy(sizeof(T))` without checking the mapped size

**Where:** [ace_shared_memory.cpp `TryOpenMapGroup`](file:///mnt/windows/code/race_tools/brbrdaemon/src/ace_shared_memory.cpp#L158-L232), all `Read*` functions

> **Prompt:** `MapViewOfFile(..., 0)` maps the whole section, but the code then does `memcpy(&out, raw, sizeof(T))`, where `T` is the ACE Evo struct (`ks::SPageFilePhysics`, `SPageFileGraphicEvo`, `SPageFileStaticEvo`). These structs are much larger than the classic AC/ACR sections. If a game (or another tool creating `Local\acpmf_*`) creates a smaller section, this reads past the view and causes an access violation. That is an SEH exception, which the C++ `catch` blocks do not handle, so the daemon crashes. After mapping each view, call `VirtualQuery` to get `RegionSize`, store it per view, and make each `Read*` function check `regionSize >= sizeof(T)`. For the Classic layout, read physics into a classic-sized struct or copy only `min(regionSize, sizeof(T))` bytes after zero-initialising `out`. Add `static_assert`s for the expected `sizeof` of `SPageFilePhysics`, `SPageFileGraphicClassic` and `SPageFileStaticClassic`, plus `offsetof` checks for a few key fields, so layout regressions are caught at compile time.

---

## P1 — High

### 6. Session start/end loop that spams empty CSV files when the game is LIVE but physics is frozen

**Where:** [telemetry_collector.cpp staleness + state machine](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L715-L785), [sink.cpp `FileSink::operator()(StartSession)`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L80-L97)

> **Prompt:** If `graphics.status == AC_LIVE` but `physics.packetId` stops advancing (game hung, some menus, alt-tab with a paused physics thread), the staleness check ends the session after 10 s and disconnects. On the next tick it reconnects, sees `Idle` + `AC_LIVE`, and immediately calls `StartSession` again, which resets `lastPacketAdvanceTime_`. Every ~10.5 s a new header-only CSV is created. Fix:
> (a) only transition `Idle -> Live` when packets are actually advancing (`packetsAdvancing == true`);
> (b) make `FileSink` open the file lazily on the first non-header `Line` with samples, buffering the header lines until then, so sessions without samples leave no file;
> (c) apply the staleness check in `Paused` too (see #7).
> Add a unit test with the mock: status LIVE, frozen packetId for >10 s (make the timeout injectable so the test is fast). It should assert that no more than one CSV file is created and none with zero samples.

### 7. Paused session never ends if the game exits while paused (explicit `--simulator` mode)

**Where:** [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L697-L730)

> **Prompt:** The process-liveness check (`DetectRunningSimulator`) only runs when `reqSim == Auto`, and the staleness check only runs while `sessionState_ == Live`. With `simulator: "acevo"` (or `--simulator acevo`), if the game crashes or exits while paused, the daemon still holds the file mapping open. That keeps the section alive with frozen data, so `ReadPhysics` keeps succeeding, the state stays `Paused` forever, and the CSV file stays open. If the user later starts the same car/track, the identity matches and samples are appended to the old file hours later. Fix: run the process-liveness check periodically in all modes, using the configured sim's process names when explicit. If the process is gone, end the session and disconnect regardless of state. Also cap the maximum pause duration (configurable, e.g. 30 min) after which the session is ended.

### 8. Shutdown/logoff handling: `WM_QUERYENDSESSION` exits even if shutdown is cancelled; `WM_ENDSESSION` cleanup races process termination

**Where:** [main.cpp WndProc](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L165-L168), [main.cpp ConsoleCtrlHandler](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L83-L99)

> **Prompt:** `WM_QUERYENDSESSION` calls `PostQuitMessage`, so the daemon exits even when another application vetoes the logoff/shutdown. On `WM_ENDSESSION`, Windows may terminate the process as soon as the handler returns, but the code only posts a quit message and does its cleanup later in `main()`. Change it so that `WM_QUERYENDSESSION` just returns `TRUE`. `WM_ENDSESSION` with `wParam == TRUE` should perform the flush synchronously inside the handler: call `g_pCollector->Stop(shortTimeout)` (see #1), log, and only then return. Use `ShutdownBlockReasonCreate` while finalizing an upload if needed. Do the same for `CTRL_LOGOFF_EVENT`, which is currently not distinguished. Also handle `GetMessage` returning `-1` (the loop `while (!g_exitRequested && GetMessage(...))` treats -1 as true).

### 9. Named stop event can be pre-signalled, causing a new instance to exit immediately

**Where:** [main.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L253-L254), [main.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L418-L420)

> **Prompt:** `Local\brbrdaemon_stop_event` is a manual-reset event, and `main()` calls `SetEvent` on it at shutdown to unblock the watcher thread. If any other process (the installer, a lingering `--stop` caller) still holds a handle, the kernel object survives in the signalled state. The next daemon started then gets that object from `CreateEventW`, its watcher fires immediately, and it exits. Fix: after `CreateEventW`, call `ResetEvent` if `GetLastError() == ERROR_ALREADY_EXISTS`. Use a separate, private, unnamed event (or `WaitForMultipleObjects` on {stopEvent, internalExitEvent}) to unblock the watcher instead of signalling the public event. Also make `--stop` wait (with timeout, e.g. 15 s) until the instance mutex is released, so the installer can safely replace the exe. Return a non-zero exit code if no instance was found or the wait timed out. Since the binary is `/SUBSYSTEM:WINDOWS`, `std::cout`/`std::cerr` output for `--stop` and "Unknown argument" is invisible. Call `AttachConsole(ATTACH_PARENT_PROCESS)` as `ShowHelp` does.

### 10. HTTP sink retries permanent errors and burns minutes on 4xx

**Where:** [sink.cpp `Finalize`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L234-L282), [sink.cpp `EndSession`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L284-L345)

> **Prompt:** `HttpSink::Finalize` treats every non-2xx status the same way, and `operator()(EndSession)` then re-uploads the entire buffer up to `max_retries` times with 2–16 s sleeps. A 401/403 (bad API key), 400 or 413 will never succeed, yet it blocks the sink thread for minutes (and, with #2, causes the next session's data to overflow). Classify responses: 2xx means success; 408/429/5xx and transport errors are retryable (honour `Retry-After` for 429/503); other 4xx are permanent. On a permanent error, log once with the response body, set a "disabled for this session" flag, and stop connecting and retrying. Also apply this classification to the in-session reconnect path in `operator()(const Line&)`.

### 11. `RetryBackoff` ignores `maxAttempts`/window semantics; docs and code disagree

**Where:** [sink.h `RetryBackoff`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.h#L36-L51), [sink.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L39-L67), [telemetry_collector.cpp sink creation](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L539-L548)

> **Prompt:** `RetryBackoff::CanAttempt` clamps the exponent to 4 (16 s) no matter what `maxAttempts` is, so `maxAttempts` has no observable effect, and the "sliding window" only slightly changes the delay. The header comment ("clamped to maxAttempts, up to 16-30s") is wrong. `TelemetryCollector` hard-codes `backoffWindow=10min`, `maxBackoffAttempts=8`, `maxConnectionDuration=2min`. The config key `max_retries` actually only controls *end-of-session re-upload attempts*, which the README doesn't explain. Decide the intended semantics, e.g. exponential backoff with jitter capped at `maxBackoff`, plus "give up after N failures within the window", and implement and document them. Use `HttpSinkOptions` (not the positional "backward-compatible" constructor, which should be deleted) when constructing the sink. Expose the relevant knobs in config with validation. Update `TestRetryBackoff` to cover the cap and the give-up behaviour.

### 12. Thread-unsafe access to collector/reader state from the UI thread

**Where:** [main.cpp tray menu](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L125-L133), [telemetry_collector.h](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.h#L101-L115), [ace_shared_memory.h](file:///mnt/windows/code/race_tools/brbrdaemon/src/ace_shared_memory.h#L93-L97)

> **Prompt:** The tray `WndProc` (main thread) calls `g_pCollector->GetConnectedSimulatorName()` → `shmReader_->IsConnected()` / `GetSimulatorName()`. These read `pPhysicsRaw_` and `detectedSim_` while the polling thread mutates them in `Connect`/`Disconnect`, which is a data race (UB). `GetSessionState()` likewise reads non-atomic `sessionState_`. Fix: have the polling thread publish a small status snapshot (`std::atomic<SimulatorType>`, `std::atomic<SessionState>`, `std::atomic<bool> connected`, maybe `std::atomic<int> activeUploads`), and make the public getters read only those atomics. Also, `GetConnectedSimulatorName()` returns `"Assetto Corsa"` when nothing is connected, so the tray shows "Ready: Assetto Corsa" with no game running. Show "Waiting for simulator" instead, and include the active upload count in the tray status/tooltip.

### 13. Default data paths are next to the exe, which is unwritable when installed under `Program Files`

**Where:** [main.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L273-L291), [config.cpp `GetCurrentModuleDirectory`](file:///mnt/windows/code/race_tools/brbrdaemon/src/config.cpp#L26-L37)

> **Prompt:** Config, log and `Telemetry\` all default to the module directory. Under a per-machine install this fails silently: `Logger` swallows open errors, `FileSink` just logs "Failed to open file", and `SavePluginConfig` throws (which triggers bug #3). Coordinate with the installer project (`../installer`) on the expected locations, then: resolve the data dir as `%LOCALAPPDATA%\BrBrDb\` via `SHGetKnownFolderPath(FOLDERID_LocalAppData)`, unless the module dir is writable or a `portable` marker file exists. Keep `--config` as an override. Also: `GetCurrentModuleDirectory` uses a fixed `MAX_PATH` buffer and ignores truncation (use a growing buffer and check `GetLastError() == ERROR_INSUFFICIENT_BUFFER`), and `LoadPluginConfig`/`SavePluginConfig` build error messages with `std::filesystem::path(path).string()`, which throws on non-ANSI characters on Windows (use `WideToUtf8`). Update README "Output Paths".

### 14. HTTP sink metadata is missing fields that the CSV has (track config, origin, heading, game version)

**Where:** [telemetry_collector.cpp `StartSession`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L560-L601), [brt1_protocol.h `Brt1SessionMetadata`](file:///mnt/windows/code/race_tools/brbrdaemon/src/brt1_protocol.h#L40-L49), [brt1_protocol.cpp `EncodeMetadataChunk`](file:///mnt/windows/code/race_tools/brbrdaemon/src/brt1_protocol.cpp#L116-L164)

> **Prompt:** The CSV header contains `Track configuration`, `Track Origin Latitude/Longitude/Heading` and `Game version`, but `Brt1SessionMetadata` and the JSON metadata chunk don't. Sessions uploaded via HTTP therefore lose information that file-based sessions have, including the geo-origin needed for map alignment. Extend `Brt1SessionMetadata` with these optional fields and emit them in `EncodeMetadataChunk` (only when present). Parse them in `Brt1StreamDecoder`, and build both the CSV header and the BRT1 metadata from a single `SessionMetadata` instance so they can't diverge. Check the server-side BRT1 parser in this repo (search for `BRT1`/`MetadataJson` outside `brbrdaemon/`) and keep the key names consistent. Also consider removing the triple-duplicated key aliases (`track_name`/`Track name`, ...) once the server accepts a canonical form.

---

## P2 — Medium

### 15. Single source of truth for channels (CSV header, CSV formatting, values vector, BRT1 channel list)

**Where:** [telemetry_collector.cpp `csvColumnHeaders`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L151-L165), [`ExtractTelemetryData`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L214-L426), [brt1_protocol.cpp channels](file:///mnt/windows/code/race_tools/brbrdaemon/src/brt1_protocol.cpp#L25-L91)

> **Prompt:** The 62-channel layout is defined four times: the `csvColumnHeaders` string, the hand-written `ss << ...` CSV formatting, the `outValues = {...}` initializer, and `GetBrBrDaemonBrt1Channels()`. Nothing checks that they agree. Refactor so that one table (name, BRT1 scale, CSV formatting rule such as "empty when NaN" and precision) drives everything. `ExtractTelemetryData` should fill only `outValues`; derive the CSV line from `outValues` plus the channel table, keeping the ISO timestamp for the `Time` column, and generate the CSV header from the table. Add a test asserting `values.size() == channels.size()` and that the CSV header names match the BRT1 channel names. Also delete the `FormatTelemetryLine(…, SPageFileGraphicEvo, …)` "backward-compatibility" overload if only tests use it.

### 16. Steering-angle units are wrong for the fallback path, and quantized to whole degrees on ACE

**Where:** [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L285-L291)

> **Prompt:** `steering = physics.steerAngle * (180/PI)` assumes radians, but `steerAngle` is documented in `ace_evo_protocol.h` as *normalised −1..+1*. For classic AC/ACR, `steerDegrees` is always 0, so every sample takes the fallback and produces wrong units (±57.3 "degrees"). On ACE, `steer_degrees` is an `int32_t`, so the channel only has 1° resolution even though BRT1 uses scale 0.05. Values also switch sources whenever `steer_degrees` happens to be 0 near centre. Fix: for Evo, compute `steerAngle * (lockDegrees)` using a lock value from the static/graphics page if one exists; otherwise use `steer_degrees` consistently. For Classic, output normalised × known lock if available, otherwise NaN/empty, and document it. Never mix sources sample-to-sample.

### 17. Verify G-force axis mapping per simulator

**Where:** [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L269-L272)

> **Prompt:** The code maps `accG[0]=lat, accG[1]=lon, accG[2]=vert`, following the ACE header comment. In classic Assetto Corsa shared memory, `accG` is commonly documented as X=lateral, **Y=vertical, Z=longitudinal** (world Y-up). Confirm the layout for AC, ACR and ACE, using official docs or a recorded sample: at rest, vertical should read ≈1 g. Then make the mapping layout-specific (`LayoutFormat::Classic` vs `Evo`). Add a unit test with a synthetic "at rest" physics frame for each layout that asserts `GForceVert ≈ 1`. Also verify the quaternion convention (`EulerToQuaternion` treats heading as yaw about Z, while AC's world is Y-up) against the downstream consumer in `race_tools/assetto_corsa.py`.

### 18. Wrong/placeholder data written as real values

**Where:** [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L307-L308), [ace_shared_memory.cpp `ReadUnifiedStatic`/`ReadUnifiedGraphic`](file:///mnt/windows/code/race_tools/brbrdaemon/src/ace_shared_memory.cpp#L363-L435)

> **Prompt:** Fix fields that are populated with misleading values:
> (a) `Raining` and `Avg Track Wetness` are hard-coded to `0.0`, which downstream reads as "dry". Emit NaN (empty CSV / `BRT1_NAN_DELTA`) when unknown, or read real values from the ACE graphics page if available.
> (b) Classic `ReadUnifiedStatic` sets `out.sessionType = st.numberOfSessions`, which is not a session type. Set it to -1 and rely on `graphics.session`.
> (c) The Evo `ReadUnifiedGraphic` hard-codes `out.session = 0`, so `SessionTypeToString` always yields "Practice" when `session_name` is empty. Map the real ACE session field.
> (d) Classic `handbrakePercent` is forced to 0. Output NaN instead.
> (e) Ambient/track temperatures are in °C while tyre temps are converted to K. Confirm what the downstream pipeline expects and document units per channel.

### 19. `ExtractNumber` produces bogus kart numbers; `kart_number_filter`/`car_name_regexp` config is dead

**Where:** [telemetry_collector.cpp `ExtractNumber`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L94-L121), [config.h](file:///mnt/windows/code/race_tools/brbrdaemon/src/config.h#L44-L57), [config.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/config.cpp#L62-L73)

> **Prompt:** Without a `#`, `ExtractNumber` returns the last digit run of the car name, so "Ferrari 296 GT3" → "3" and "Porsche 911 GT3 R" → "3". These values are emitted as `Kart number` metadata. Only extract a number when it is explicitly marked (`#NN`), or make the pattern configurable. Separately, `PluginConfig::carNameRegexp`, `kartNumberFilter`, `SimulatorSettings::carNameRegexp` and `GetCarNameRegexpForSim()` are parsed and saved but never used, and `GetConfigFilePath()` is unused. Either implement them (e.g. only record sessions whose car matches `GetCarNameRegexpForSim(sim)`, validated as a regex at load time like `driver_name_overrides`) or remove them from the code, the README and `brbrdaemon.test.json`. Note that `driver_name_overrides` uses `std::regex_match` (full match), so the test config's `"#8"` never matches a name like "Kart #8". Document full-match semantics or switch to `regex_search`. Also precompile override regexes once in the constructor instead of on every session start.

### 20. Inconsistent simulator keys (`ace` vs `acevo`) and unvalidated config values

**Where:** [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L500-L514), [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L645-L655), [config.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/config.cpp#L44-L189)

> **Prompt:** The per-sim `simulators` map is looked up with key `"ace"`, but every other place (CLI, `simulator` field, default league) uses `"acevo"`. A user writing `"simulators": {"acevo": {...}}` gets silently ignored. Use one canonical set of keys (`acevo`, `acr`, `ac`), accepting `ace` as an alias if you want. In `LoadPluginConfig`, add validation:
> - `simulator` ∈ {auto, acevo, acr, ac} (unknown values currently fall back to Auto silently, and so does `--simulator`);
> - `polling_rate_hz` within [10, 500];
> - `max_retries` ≥ 0;
> - `buffer_duration_minutes` within [1, 240];
> - `server_address` parses to a non-empty host.
>
> Make the `simulators` section strict like the rest, since invalid entries are currently silently skipped. Write the config atomically (temp file + `MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)`) so a crash or a concurrent installer write can't leave a truncated JSON. Add unit tests for each validation error.

### 21. Insecure HTTP defaults

**Where:** [net_utils.cpp `ParseServerAddress`](file:///mnt/windows/code/race_tools/brbrdaemon/src/net_utils.cpp#L69-L134), [sink.cpp `Connect`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L347-L420)

> **Prompt:**
> (a) `ParseServerAddress` defaults to `http://` when no scheme is given, so `X-API-Key` goes over the network in clear text. Default to `https://`, and log a prominent warning when an API key is used with a non-HTTPS, non-loopback host.
> (b) If the user configures `https://host/`, the path is `/`, so the upload goes to `/` instead of `/api/upload/stream`. Treat an empty path *or* `/` as "use default endpoint", and document it.
> (c) `WINHTTP_ACCESS_TYPE_DEFAULT_PROXY` ignores the user's system/IE proxy. Use `WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY` (Win 8.1+).
> (d) The user agent is hard-coded `BrBrDbTelemetry/1.0`. Include the real version from `version.rc` via a generated header.
> (e) Create the `WinHttpOpen` session once per sink instead of per reconnect.

### 22. Race between `HttpSink::Abort` and in-flight WinHTTP calls; `aborted_` is reset by the next session

**Where:** [sink.cpp `Abort`/`WriteChunk`/`Finalize`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L149-L169)

> **Prompt:** `WriteChunk`/`Finalize` copy `hRequest_` under the mutex, release it, and then call `WinHttpWriteData`/`WinHttpReceiveResponse` on the copy. Meanwhile `Abort()` can `WinHttpCloseHandle` that handle on another thread, so the worker uses a closed (and possibly recycled) handle. Make the cancellation explicit. Either keep the handle valid and cancel with `WinHttpSetOption(WINHTTP_OPTION_...)`/close the *connect* handle, letting the worker close the request after the call returns; or switch to async WinHTTP with a callback. Ensure only the owning thread ever calls `WinHttpCloseHandle` on `hRequest_`. Also, `operator()(StartSession)` sets `aborted_ = false`, which revives a sink that was aborted for shutdown if another `StartSession` is queued behind it. An abort should be terminal.

### 23. `SinkWorker` swallows exceptions without logging

**Where:** [sink.cpp `SinkWorker::WorkerThread`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L501-L515)

> **Prompt:** The exception boundary in `SinkWorker::WorkerThread` discards exceptions with `(void)e;`. Pass a `Logger&` to `SinkWorker` (or expose one from `Sink`) and log `e.what()` with the sink type, rate-limited so a persistently failing sink can't flood the log. Also log the number of messages processed and dropped when the worker exits.

### 24. `FileSink` robustness: filename collisions and unchecked write errors

**Where:** [sink.cpp `FileSink`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L73-L116)

> **Prompt:**
> (a) Filenames use `time_t` seconds plus the participant tag and are opened with `std::ios::trunc`, so two sessions starting within the same second (e.g. an identity flip) overwrite each other. Add milliseconds or a uniqueness suffix, and open with `CREATE_NEW` semantics, retrying with `_1`, `_2`, ….
> (b) Stream write failures (disk full, path removed) are never checked. Check `file_.fail()` after flush, log once, and try to reopen.
> (c) Clamp the total path length (driver + car + track + session can approach `MAX_PATH` in deep directories) or use `\\?\` prefixed paths.
> (d) Consider a human-readable UTC timestamp (`YYYYMMDD_HHMMSS`) in the name instead of raw epoch seconds; check that downstream tools don't depend on the current pattern before changing it.

### 25. Memory retained when a session has no enabled sinks; `Line` carries redundant payloads

**Where:** [safe_queue.h `Push`](file:///mnt/windows/code/race_tools/brbrdaemon/src/safe_queue.h#L107-L119), [telemetry_collector.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L767-L773)

> **Prompt:** If every sink is disabled, `StartSession` still creates a queue and pushes samples. With no readers, `PruneLocked` is never called on push, so up to 50 000 `Line`s (each with a CSV string *and* a 62-double vector) are retained, roughly 50 MB. Skip queue creation and sample extraction when there are no workers, or prune on push when `readerIndices_` is empty. Also avoid building the CSV string when no file sink is active, and avoid building `values` when no HTTP sink is active. `Pop` copies the whole `Message` (`out = deque_[i]`). Consider `std::shared_ptr<const Message>` elements so multiple readers share one allocation.

### 26. Hard-coded track-origin table uses fragile substring matches

**Where:** [telemetry_collector.cpp `ResolveTrackOrigin`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L61-L92)

> **Prompt:** `ResolveTrackOrigin` matches lowercase substrings like `"spa"`, `"cota"` and `"atlanta"` anywhere in the track id. This gives false positives (any id containing "spa" gets Spa's coordinates) and the order is significant. Move the table into a data file, or at least a `static const` array of `{exact_id_or_regex, lat, lon}`, match on the exact track id (case-insensitive), and allow overriding/extending it from config. Keep the shared-memory lat/lon preference. Add a unit test for a couple of real ACE/AC track ids and one negative case.

### 27. `timeBeginPeriod(1)` held for the daemon's entire lifetime

**Where:** [telemetry_collector.cpp `PollingThreadWorker`](file:///mnt/windows/code/race_tools/brbrdaemon/src/telemetry_collector.cpp#L637-L640)

> **Prompt:** The polling thread raises the global timer resolution to 1 ms as soon as it starts and keeps it there even while idle (no game running, polling every 500 ms). This increases system-wide power use on laptops. Call `timeBeginPeriod(1)` only while connected to a simulator and `timeEndPeriod(1)` on disconnect, or use a high-resolution waitable timer (`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`) instead. Also, `DetectRunningSimulator()` takes a full Toolhelp process snapshot once per second while connected. Cache the game's PID after the first detection and use `OpenProcess` + `WaitForSingleObject(h, 0)` to check liveness.

### 28. `Brt1StreamEncoder`/`Decoder` edge cases and unused end-of-stream chunk

**Where:** [brt1_protocol.cpp `EncodeSample`](file:///mnt/windows/code/race_tools/brbrdaemon/src/brt1_protocol.cpp#L198-L244), [decoder unknown chunk](file:///mnt/windows/code/race_tools/brbrdaemon/src/brt1_protocol.cpp#L383-L384), [sink.cpp `Finalize`](file:///mnt/windows/code/race_tools/brbrdaemon/src/sink.cpp#L234-L240)

> **Prompt:**
> (a) `EncodeSample` calls `std::lround(diff/scale)` on values that may be ±inf (non-NaN), which is undefined; `lround` also returns a 32-bit `long` on Windows. Treat non-finite values as NaN and use `std::llround`.
> (b) `EncodeEOSChunk()` is never sent. `HttpSink::Finalize` ends the HTTP body without a BRT1 EOS, so the server cannot tell a proactive 2-minute rollover from a real end of session or from a truncated stream. Send EOS only on real session end, and make the rollover explicit (e.g. a metadata flag, a `Rollover` chunk type, or a session UUID plus a part index in the metadata) so the server can stitch parts. Coordinate the change with the server-side parser in this repo.
> (c) `Brt1StreamDecoder::Step` returns `NeedMoreData` after skipping an unknown chunk even when more data is buffered, so callers that loop until `NeedMoreData` stop early. Add a `Skipped` result, or loop internally.
> (d) Add an encoder→decoder round-trip test (random walk, NaN transitions, overflow→keyframe, keyframe interval) asserting the reconstructed values are within `scale/2` of the input. `Brt1StreamDecoder` currently has no test coverage.

### 29. README accuracy

**Where:** [README.md](file:///mnt/windows/code/race_tools/brbrdaemon/README.md)

> **Prompt:** After the fixes above, update `README.md` so it matches the code:
> - remove or qualify the "seqlock consistency against torn reads" and "O(1) memory" claims until #2/#4/#25 are done;
> - document every config key (`simulators`, `car_name_regexp`, `kart_number_filter` if kept), the real meaning of `max_retries` and `buffer_duration_minutes`, valid ranges, and the full-match regex semantics of `driver_name_overrides`;
> - document the HTTP endpoint default (`/api/upload/stream`), the BRT1 stream format and the 2-minute rollover;
> - document the data/log/config locations (#13), the CSV channel list with units, and the `--stop` exit codes (#9).
>
> Keep the build section and update it if `build.ps1` gains new targets.

---

## P3 — Tests & tooling

### 30. Make tests deterministic and cover the untested components

**Where:** [unit_tests.cpp](file:///mnt/windows/code/race_tools/brbrdaemon/src/unit_tests.cpp)

> **Prompt:** Improve the test suite:
> (a) Remove the `sleep_for(50/500ms)` timing assumptions in `TestSessionStateTransitions`, `TestTelemetryCollectorWithMockSim` and `TestAcRallyTelemetryCollectorSession`. Expose a `TelemetryCollector::PollOnce(now)` step function, or inject a clock and run the state machine synchronously in tests.
> (b) `TestTelemetryCollectorWithMockSim` reads the CSV right after `Stop()`, which races the detached sink thread (#1), and its `Logger` is destroyed while that thread may still use it.
> (c) `TestAcRallySharedMemoryMappingAndReading` creates real game section names (`Local\acr_pmf_*`), which collide with a running game. Use unique test names.
> (d) Add an `HttpSink` test against an in-process loopback HTTP server, e.g. a minimal `winsock` listener in the test, that decodes the chunked body with `Brt1StreamDecoder`. Cover: normal session, connection drop mid-stream with buffer replay, proactive rollover, 401 permanent failure (#10), 503 retry, and `Abort()` during the retry sleep.
> (e) Add config-validation tests (#20), queue overflow tests (#2), and `ParseServerAddress` tests for `https://host/`, IPv6 `[::1]:8080`, and schemeless input.
> (f) Replace the home-grown `TEST_ASSERT` macro (which `return`s from the test and skips cleanup, leaving temp dirs behind) with RAII cleanup guards, or adopt a small framework such as doctest (single header in `thirdparty/`).

### 31. Build/tooling hygiene

**Where:** [CMakeLists.txt](file:///mnt/windows/code/race_tools/brbrdaemon/CMakeLists.txt), [build.ps1](file:///mnt/windows/code/race_tools/brbrdaemon/build.ps1), [main.cpp includes](file:///mnt/windows/code/race_tools/brbrdaemon/src/main.cpp#L16-L29)

> **Prompt:** Small cleanups:
> - `main.cpp` uses `std::thread` and `std::vector` without including `<thread>`/`<vector>`;
> - `telemetry_collector.cpp` has `#pragma comment(lib, "winmm.lib")` although CMake already links `winmm`;
> - the `/W4 /EHsc /permissive-` flags are repeated per target, so set them once (e.g. via an interface target) and add `/WX` for `brbrdaemon_core` once the warnings are clean;
> - add `/utf-8` because sources and log strings contain non-ASCII;
> - define `WIN32_LEAN_AND_MEAN`/`NOMINMAX` globally instead of the `(std::min)` workarounds;
> - `build.ps1` supports only Release, so add a `-Config Debug` option and a `-Target all`;
> - generate a `version.h` from CMake (alongside `version.rc`) so the daemon can log its own version at startup and use it in the HTTP User-Agent (#21);
> - `FAVICON_PATH` points outside the project (`../favicon.ico`), so fail configuration with a clear message if it is missing.

