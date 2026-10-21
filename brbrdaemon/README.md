# BrBrDb Telemetry Daemon (brbrdaemon)

A high-performance C++ telemetry companion daemon and logger for simulators including **Assetto Corsa Evo (ACE)**, **Assetto Corsa Rally (ACR)**, and **Assetto Corsa (AC)**. It captures live vehicle dynamics and session data from Windows Named Shared Memory, processes it asynchronously, writes standard CSV telemetry logs, and streams real-time data over HTTP to BrBrDb backend servers.

The daemon auto-detects running simulators and maps their memory layouts seamlessly, defaulting to the appropriate league (`acevo`, `acr`, or `ac`).

---

## 🚀 Key Features

* **Zero Game Overhead:** Runs out-of-process and accesses telemetry via low-latency memory-mapped files.
* **Precise High Frequency (up to 100Hz):** Samples physics ticks with 1ms timer precision (`timeBeginPeriod`) and bounded acquire-fenced seqlock consistency against torn reads.
* **Explicit State Machine:** Tracks on-track session lifecycle (`Idle`, `Live`, `Paused`), avoiding recording replays, keeping files contiguous across in-game pauses, and detecting simulator exit or crash via staleness timeouts.
* **Format Compatible:** Uses CSV structure and chunked HTTP streaming compatible with the downstream data pipeline, writing tire loads, lateral/longitudinal forces, slip ratios, tire temperatures in Kelvin, inputs, and orientation quaternions.
* **Bounded & Pruned Queue:** Multi-consumer queue with automatic reader pruning ensures $O(1)$ memory usage throughout long multi-hour driving sessions.
* **Background Daemon with Tray:** Operates silently in the Windows system tray (`--tray`), headless background (`--headless`), or console diagnostic mode (`--console`).
* **Clean Shutdown:** Supports graceful stopping via `--stop`, tray exit, named stop event, or installer coordination with automatic flushing.

---

## 🖥️ Command-Line Options

```
brbrdaemon.exe [options]
  --tray             Run with Windows system tray icon (default)
  --headless         Run as silent background process without tray icon
  --console, -c      Show diagnostic console window
  --stop             Signal any running brbrdaemon instance to exit cleanly
  --config <path>    Custom path to BrBrDbTelemetry.json configuration
  --simulator <name> Target simulator: auto (default), acevo, acr, ac
  --league <name>    Custom league name (defaults to simulator: acevo, acr, ac)
  --help, -h         Display usage information
```

---

## 📁 Output Paths

By default, files are placed in the application directory:
* **Telemetry logs:** `<ModuleDir>\Telemetry\BrBrDbTelemetry_<timestamp>_<participant>_server.csv`
* **Log file:** `<ModuleDir>\brbrdaemon.log` (with automatic size-based rotation at 5 MB)

---

## ⚙️ Configuration (`BrBrDbTelemetry.json`)

```json
{
  "simulator": "auto",
  "league": "auto",
  "polling_rate_hz": 100,
  "sinks": [
    {
      "type": "file",
      "path": "C:\\path\\to\\Telemetry",
      "enabled": true
    },
    {
      "type": "http",
      "server_address": "https://brbrdb.brbrkitten.com",
      "api_key": "YOUR_API_KEY",
      "enabled": false,
      "max_retries": 10,
      "buffer_duration_minutes": 30
    }
  ],
  "driver_name_overrides": [
    {
      "car_name_regexp": "Porsche.*",
      "driver_name": "My Custom Driver"
    }
  ]
}
```

---

## 🛠️ Compilation & Testing

Ensure you have **Visual Studio** with C++ desktop development installed.

A PowerShell script `build.ps1` is provided:

```powershell
# Run unit tests
.\build.ps1 -Target test

# Run mock telemetry simulation
.\build.ps1 -Target mock

# Build release executable
.\build.ps1 -Target build
```
