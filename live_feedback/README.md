# BrBrDb Live Turn Feedback (`live_feedback`)

Standalone game-agnostic desktop application that provides real-time audio feedback on corner exit across **KartSim / rFactor 2** (`rfactor_plugin`) and **Assetto Corsa / Evo / Rally** (`brbrdaemon`).

At each turn exit (`turn.end`), it combines the actual time delta in the corner (`turn.start -> turn.end`) with the estimated straight-line time delta up to the next turn (`turn.end -> next_turn.start`) based on your exit speed vs. the fastest pass for that turn in the current session:
- **New Turn Best (`< -0.015s`)**: Rising two-note chirp.
- **On Pace / Loss (`>= -0.015s`)**: Single tone whose frequency scales logarithmically from `1200 Hz` (`0.00s` lost, high pitch) down to `240 Hz` (`>= 0.50s` lost, low pitch).

## Simulator Configuration (`BrBrDbTelemetry.json`)

Add a second `"http"` sink after your primary remote upload sink:

```json
{
  "type": "http",
  "enabled": true,
  "server_address": "http://127.0.0.1:18922",
  "api_key": "YOUR_API_KEY",
  "max_retries": 0,
  "buffer_duration_minutes": 0
}
```

- Keeping `"max_retries": 0` and `"buffer_duration_minutes": 0` ensures zero RAM buffering or shutdown delay when the Live Feedback app is not running.
- Keeping your remote upload sink first preserves it as the primary upload target for `CommandSink` AUX apps; `CommandSink` automatically forwards **all** enabled `"http"` sinks (`upload_targets`) so `tobii` streams live gaze & face telemetry to both `brbrdb` and `live_feedback` simultaneously.

## OBS Studio Virtual Camera & Google Meet Overlay

`live_feedback` serves a transparent `1920x1080` HTML5 telemetry HUD at **`http://127.0.0.1:18922/overlay`** fed in real time via SSE (`/events`) from both the simulator vehicle stream and Tobii `tobii_gaze`:

- **Gaze Reticle**: Calibrated 1:1 normalized screen gaze marker (disappears immediately on blink or tracking loss).
- **Lap & All-Turns Timing Card**: Live lap time, best/last lap, delta vs. session best lap, and per-turn times (`LAST`, `BEST`, `DELTA`) for all turns on the track.
- **Pedals Plot**: Unlabeled 5-second scrolling Throttle and Brake plot (`0–100%`).
- **Full-Lap Speed Plot (`0 .. lap_length`, top)**: Unlabeled, legend-free full-lap speed trace (`x=0, y=0`) showing Current Lap vs. Best Lap ghost and per-turn Best Turn references with track turn labels.

### Setup Steps
1. Start **`tobii`** (`python -m tobii.main`) and **`live_feedback`** (`python -m live_feedback.main`).
   - You can open the live overlay in a browser via **`Preview Overlay`** or `http://127.0.0.1:18922/overlay` (query toggles: `units=kmh|mph`, `gaze=0|1`, `trail=0|1`).
2. In **OBS Studio**:
   - Set **Base (Canvas)** and **Output (Scaled)** resolution to `1920x1080` (`60 FPS`).
   - Add **Game Capture** targeting `rFactor2.exe`.
   - Above it, add a **Browser** source pointing to `http://127.0.0.1:18922/overlay` (`Width: 1920`, `Height: 1080`, `FPS: 60`, check *Refresh browser when scene becomes active*).
   - Click **Start Virtual Camera**.
3. In **Google Meet**:
   - **Settings → Video → Camera**: select **`OBS Virtual Camera`** and set send resolution to High Definition. *(Local self-view is mirrored by Meet, while remote viewers see text unmirrored; for full 1080p sharpness + game audio, use OBS **Windowed Projector (Preview)** + Meet **Present → A window**.)*

## Running

### Option A: From Antigravity IDE
Select **`Live Feedback`** from the **Run and Debug** dropdown and click Run (`F5`).

### Option B: From `C:\code\race_tools`
```powershell
cd C:\code\race_tools
python -m live_feedback.main
```

### Option C: From `C:\code\race_tools\live_feedback`
```powershell
cd C:\code\race_tools\live_feedback
python main.py
```
*(Or `python -m live_feedback.main` if `race-tools` is installed via `pip install -e .`.)*

Optional overrides:
- `--port 18922`: Local HTTP stream port.
- `--server https://brbrdb.brbrkitten.com`: Backend URL used for `GET /api/track_data`.

## Offline Prediction Accuracy Evaluation

Replay recorded `.brt1.br` or `.brt1` sessions to measure predicted vs. actual straight-line time deltas:

```powershell
python -m live_feedback.evaluate_session path\to\session.brt1.br
```

By default this checks local track definitions in `data\tracks\` (when present and containing turn definitions), or fetches them from `--server` using `$env:BRBRDB_API_KEY`:

```powershell
# Explicit offline track directory or single file
python -m live_feedback.evaluate_session path\to\session.brt1.br --tracks-dir path\to\tracks
python -m live_feedback.evaluate_session path\to\session.brt1.br --track-json path\to\track.json

# Remote track lookup via API key environment variable (or --api-key)
$env:BRBRDB_API_KEY = 'YOUR_API_KEY'
python -m live_feedback.evaluate_session path\to\session.brt1.br --server https://brbrdb.brbrkitten.com --mode fixed
```

Modes:
- `--mode causal` *(default)*: Matches live driving — benchmarks start empty and update dynamically as faster passes complete.
- `--mode fixed`: Evaluates every non-reference pass against the session's final fastest pass for each turn.
