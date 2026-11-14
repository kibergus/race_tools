# Spiker:bit 500 Hz Neuro-Telemetry Studio & AUX App

Desktop auxiliary application for the **Backyard Brains Spiker:bit + BBC micro:bit V2** sim-racing neuro-telemetry streamer.

It provides:
1. **Live 500 Hz Multi-Trace Oscilloscope & Mode Selector (`EEG`, `ECG`, `EMG`)** — strictly gated by window visibility so zero DSP or UI rendering runs when minimized or hidden during a race.
2. **One-Click Firmware Flashing** — detects the `MICROBIT` USB Mass Storage drive on Windows and Linux and flashes the bundled 500 Hz Timer + PPI + SAADC EasyDMA firmware ([`firmware/spikerbit_firmware.hex`](file:///c:/code/race_tools/spikerbit/firmware/spikerbit_firmware.hex)).
3. **Built-in 500 Hz Hardware Simulator (`SIMULATOR`)** — lets you explore live `EEG`, `ECG`, and `EMG` curves and test session uploads before connecting physical hardware.
4. **Automatic Session Signalling & Brotli Upload** — listens on `http://127.0.0.1:18920` for `CommandSink` heartbeats from `brbrdaemon` or `rfactor_plugin`, synchronizes and disciplines the micro:bit's 16 MHz crystal clock, and streams Brotli-compressed (`Content-Encoding: br`) BRT1 chunks with `"stream": "spikerbit"` to `POST /api/upload/stream`.

---

## 1. Prerequisites

Install the Python dependencies from the `race_tools` root directory:

```powershell
cd C:\code\race_tools
python -m pip install -r requirements.txt
```

*(Required packages: `numpy`, `scipy`, `brotli`, `pyserial`, `bleak`, plus standard library `tkinter`).*

---

## 2. Running the App

You can launch the app either from the **repository root** (`C:\code\race_tools`) or directly from inside the **`spikerbit/` folder** (`C:\code\race_tools\spikerbit`):

### Option A: From `C:\code\race_tools\spikerbit`
```powershell
cd C:\code\race_tools\spikerbit
python main.py
```

### Option B: From `C:\code\race_tools`
```powershell
cd C:\code\race_tools
python -m spikerbit.main
```

### Command-Line Options
```powershell
python main.py --help
```
- `--aux-port 18920`: Local HTTP port for `CommandSink` session signalling (default: `18920`).
- `--serial-port BLE` (or `COM3`): Connect via Bluetooth Low Energy (`BLE` or `BLE://AA:BB:CC:DD:EE:FF`) or a USB serial port (`COMx` / `/dev/ttyACM0`). By default, the app auto-connects to a detected micro:bit USB COM port or nearby BLE `Spiker:bit XXXX` on startup and never starts `SIMULATOR` unless `--serial-port SIMULATOR` (or `SIMULATOR` in the UI dropdown) is explicitly selected.
- `--backup-dir C:\Telemetry\Spikerbit`: Optional local folder to save `.brt1.br` copies of every uploaded Spiker:bit segment.

---

## 3. Flashing & Wireless Battery Isolation Setup

1. Connect your **BBC micro:bit V2** to your PC via USB once (it mounts as `MICROBIT`).
2. Click **⚡ Flash micro:bit Firmware** in the top-right corner of the Spiker:bit Studio window to install the unified **BLE + USB 500 Hz EasyDMA** firmware.
3. **For 100% Galvanically Isolated Wireless Operation (Recommended near Direct-Drive Wheelbases)**:
   - **Unplug the USB cable** from the micro:bit completely.
   - Insert **2× AAA batteries** into the Spiker:bit board, plug the micro:bit into the Spiker:bit edge connector, and flip the Spiker:bit power switch **ON**.
   - Launch Spiker:bit Studio (or click **Refresh**): it will discover `BLE://... (Spiker:bit XXXX)` over Bluetooth, connect automatically without OS pairing prompts, and lock the 16 MHz crystal clock to `±3–5 ms` via min-RTT `PING/PONG`.
4. **For Wired USB Operation**: Leave the USB cable plugged in; the app will auto-select the micro:bit's `COMx` port.

---

## 4. Linking with the Main Telemetry Daemon / rFactor 2 Plugin

To have `brbrdaemon` (Assetto Corsa / ACE / ACR) or `rfactor_plugin` (rFactor 2) automatically trigger Spiker:bit uploads whenever you drive on track:

1. Open **BrBrDbTelemetryInstaller** and go to the **Sinks / Data Destinations** tab.
2. Click **＋ Add Sink** and set **Sink Type** to `command`.
3. Leave **Server Address** at the default `http://127.0.0.1:18920`, check **Enabled**, and click **Save Sinks Configuration**.

Or in `BrBrDbTelemetry.json`, add a `"command"` entry to `"sinks"`:
```json
{
  "type": "command",
  "enabled": true,
  "server_address": "http://127.0.0.1:18920"
}
```
