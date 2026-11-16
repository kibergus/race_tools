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
- Keeping your remote upload sink first preserves it as the primary upload target for `CommandSink` AUX apps.

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
