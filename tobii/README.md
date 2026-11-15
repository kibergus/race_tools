# Tobii Eye Tracker 5 Direct-USB Telemetry Studio & AUX Uploader

Standalone auxiliary application for the **Tobii Eye Tracker 5** (`USB VID=0x2104, PID=0x0313`) that communicates **directly with the USB hardware** using OS-native USB APIs (`winusb.dll` on Windows, `libusb-1.0` on Linux) with **zero Tobii proprietary libraries or DLLs**.

It provides:
1. **Direct Hardware TTP Streaming (`0x0500` + `0x050e` + `0x1771`)** — decodes primary EyeChip hardware gaze, per-eye screen rays, Q16.16 pupil diameters, and 3D eye origins (`~90–133 Hz`), alongside raw 8-bit grayscale in-band NIR camera frames (`0x050e`).
2. **Google MediaPipe Face Landmarker (`face_landmarker.task`, Apache-2.0)** — extracts primary 6-DoF head pose (`4×4` facial transformation matrix) and raw ocular/jaw blendshape scores (`0..1`) aligned to the exact hardware NIR camera shutter timestamp (`0x050e` column `0x0001`).
3. **Strictly Primary Channels Only** — records only primary device outputs and direct neural-network outputs (see [`CHANNELS.md`](file:///c:/code/race_tools/tobii/CHANNELS.md)). Derived metrics (blink rate, blink FWHM duration, saccade velocity, fixation state) are intentionally not computed client-side.
4. **Dual-Stream Brotli `BRT1` Upload (`CommandSink` Sync)** — listens on `http://127.0.0.1:18921` (coexisting alongside `spikerbit` on `18920`) for `/api/session/heartbeat` and `/api/session/stop` from `brbrdaemon` or `rfactor_plugin`, uploading two synchronized Brotli-compressed (`Content-Encoding: br`) `BRT1` streams:
   - `"stream": "tobii_gaze"` (17 primary EyeChip channels)
   - `"stream": "tobii_face"` (14 primary MediaPipe head-pose & blendshape channels)
5. **In-App 3D Screen & Sim-Rig Calibration (`0x05a0`)** — built-in 9-point fullscreen calibration solves exact 3D display-plane coordinates (`Top-Left`, `Top-Right`, `Bottom-Left`), tracker upward tilt, off-screen TV distance (e.g. tracker mounted above a steering wheel with a distant TV), and personal foveal offset, programming the 3D display plane directly into the EyeChip hardware.
6. **Verification GUI with Zero-CPU Visibility Gating** — live NIR sensor preview with 2D facial landmarks & 3D orientation triad, 2D screen gaze monitor, and 4-second strip charts. When minimized or hidden (`<Unmap>` / iconic state), the Tkinter redraw timer is **completely cancelled (`0 Hz`)** and UI-only frame/landmark copies are disabled so **zero CPU cycles** are spent rendering UI while background telemetry collection continues uninterrupted.

---

## 1. Prerequisites

Install the dependencies into your Python environment:

```powershell
cd C:\code\race_tools
python -m pip install -r tobii\requirements.txt
```

On first launch, [`tobii/mediapipe_worker.py`](file:///c:/code/race_tools/tobii/mediapipe_worker.py) verifies [`tobii/models/face_landmarker.task`](file:///c:/code/race_tools/tobii/models/face_landmarker.task) by SHA-256 (or automatically downloads it from Google's official MediaPipe bucket if absent).

---

## 2. Running the App

### Option A: From Antigravity IDE
Select **`Tobii Eye Tracker`** from the **Run and Debug** dropdown and click Run (`F5`).

### Option B: From `C:\code\race_tools`
```powershell
cd C:\code\race_tools
python -m tobii.main
```

### Option C: From `C:\code\race_tools\tobii`
```powershell
cd C:\code\race_tools\tobii
python main.py
```

### Command-Line Options
```powershell
python -m tobii.main --help
```
- `--aux-port 18921`: Local HTTP port for `CommandSink` session signalling (default: `18921`).
- `--ir-fps 30`: Maximum NIR camera frame rate (`15`, `30`, or `60`) fed to MediaPipe Face Landmarker (default: `30`).
- `--backup-dir C:\Telemetry\Tobii`: Optional local directory to save `TobiiGaze_*_server.brt1.br` and `TobiiFace_*_server.brt1.br` segment backups.
- `--calibration-path C:\path\to\profile.json`: Optional custom JSON path for the persisted screen calibration profile (default: `tobii/calibration_profile.json`).
- `--no-auto-connect`: Do not automatically open the USB device on startup.

---

## 3. In-App Screen & Sim-Rig Calibration

 Tobii Studio includes built-in 3D screen calibration supporting both standard under-monitor setups and **off-screen sim-rig mounts** (for example, mounting the tracker above the steering wheel base `~55–75 cm` from your eyes, tilted upward, while your TV sits much further behind it):

1. Connect the tracker in Tobii Studio and click **🎯 Calibrate Screen** in the top bar.
2. Select your **Target Monitor / TV** and enter its **Screen Diagonal (inches)** (e.g. `27`, `32`, `48`, `55`, `65`).
3. Sit in your normal driving posture and follow the **9 fullscreen targets** (`~15 seconds`).
4. Tobii Studio solves the exact 3D `(Top-Left, Top-Right, Bottom-Left)` display plane in EyeChip coordinates, programs it directly into the hardware via TTP `SET_DISPLAY_AREA` (`0x05a0`), saves the profile locally to `tobii/calibration_profile.json` (auto-applied on future launches), and opens a live hardware gaze verification overlay (`Space` / `Enter` / `Esc` to finish, `R` to recalibrate).

---

## 4. Claiming Direct USB Access & Service Alternation

### Windows (`WinUSB`)
The Tobii Eye Tracker 5 vendor interface (`VID_2104&PID_0313&MI_00`) uses Microsoft's inbox `WinUSB` driver. No vendor software is required. However, if Tobii's background Windows services (`TobiiIS5LEYETRACKER5`, `Tobii Service`, `TobiiGeneric`) are installed on the PC, they hold an exclusive handle on `MI_00` while running:
- Click **🔓 Claim USB (Stop Tobii Svc)** in the top-right corner of Tobii Studio (prompts UAC once) to stop those background services and claim direct `WinUSB` access.
- On connect, Tobii Studio snapshots the device's pre-existing `GET_DISPLAY_AREA` (`0x0596`) geometry before applying your `calibration_profile.json`, and automatically **restores the prior display area on disconnect** so alternating back and forth never overwrites existing desktop settings.
- When finished, click **🔄 Restore Tobii Svc** if you want to restart those Windows background services.

### Linux (`libusb-1.0`)
Grant non-root USB access to `2104:0313` via `udev`:
```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2104", ATTR{idProduct}=="0313", MODE="0660", TAG+="uaccess"' \
  | sudo tee /etc/udev/rules.d/70-tobii-et5.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## 5. Linking with `brbrdaemon` / `rfactor_plugin`

Add a `"command"` sink pointing to `http://127.0.0.1:18921` in `BrBrDbTelemetry.json` (can run simultaneously alongside `spikerbit` on `18920`):

```json
{
  "type": "command",
  "enabled": true,
  "server_address": "http://127.0.0.1:18921"
}
```

---

## 6. License & Attribution

Protocol framing, `HMAC-MD5` realm handshake, `0x0500`/`0x050e`/`0x1771` decoders, and forward-vector pose decomposition are adapted under the **Apache License 2.0** from [`njmill/tobii-linux`](https://github.com/njmill/tobii-linux) (see [`NOTICE`](file:///c:/code/race_tools/tobii/NOTICE)).
