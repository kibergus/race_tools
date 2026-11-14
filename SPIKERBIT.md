# Spiker:bit + BBC micro:bit Sim Racing Neuro-Telemetry (`SPIKERBIT.md`)

## 1. Project Overview & Goal
Integrate a **Backyard Brains Spiker:bit** (with a **BBC micro:bit V2**) into the **BrBrDb sim-racing telemetry pipeline** (`race_tools`) as an independent **500 Hz auxiliary BRT1 stream** (`"stream": "spikerbit"`).

The micro:bit samples the Spiker:bit's analog bio-amplifier output (`P1`) at **500.0 Hz** ($\Delta t = 2.000\text{ ms}$) using its 16 MHz crystal oscillator (`HFCLK`), hardware timer (`NRF_TIMER2`), `NRF_PPI`, and `NRF_SAADC` (`8×` hardware oversampling + `EasyDMA`), and streams native uncompressed BRT1 chunks to the local Spiker:bit AUX app, which Brotli-compresses (`Content-Encoding: br`) and uploads **only the raw 500 Hz channel** (`EEG`, `ECG`, or `EMG`) plus `Time` to `POST /api/upload/stream`. All derived neuro-telemetry channels are computed server-side during post-processing.

---

## 2. Backend Specification: Uploaded Stream & Derived Channels

This section defines the exact wire contract uploaded by the Spiker:bit AUX app (`spikerbit/uploader.py`) and the **raw and derived channels** that the backend server should ingest and compute.

### 2.1 Upload Endpoint & Framing
- **Endpoint:** `POST /api/upload/stream`
- **Headers:**
  - `Content-Type: application/octet-stream`
  - `Content-Encoding: br` (Brotli stream, `quality=4`, `lgwin=22`)
  - `X-API-Key: <api_key>`
- **BRT1 Chunk Sequence:**
  1. `0x01` **Magic Chunk:** `b"\x04\x00\x01BRT1"`
  2. `0x02` **Metadata JSON Chunk:**
     Uses the exact canonical metadata keys from `UPLOAD.md` (no duplicated keys), plus `"stream": "spikerbit"`. The active Spiker:bit hardware mode (`P8`/`P9` analog filter bank) is encoded directly as the **raw channel name** (`"EEG"`, `"ECG"`, or `"EMG"`):
     ```json
     {
       "metadata": {
         "Track name": "Spa-Francorchamps",
         "Date": "2026-11-11",
         "Time": "20:00:00",
         "Driver name": "Max",
         "League": "acevo",
         "Class": "GT3",
         "Session": "Practice",
         "Session ID": "optional-session-id",
         "kart_number": "42",
         "stream": "spikerbit"
       },
       "channels": [
         {"name": "Time", "scale": 0.0001},
         {"name": "EEG", "scale": 1.0}
       ]
     }
     ```
     - **`metadata.stream`**: Always `"spikerbit"`. Associates this auxiliary upload with the primary vehicle telemetry session matching `Session ID` or `(League, Date, Time, Track name, Driver name, Session)`.
     - **`channels[1].name`**: One of **`"EEG"`** (default), **`"ECG"`**, or **`"EMG"`**. If the user switches the hardware mode mid-session in the Spiker:bit Studio UI, the current BRT1 segment is finalized and a new segment is uploaded declaring the new channel name (`"ECG"` or `"EMG"`).
  3. `0x03` **Baseline Chunk:** `16 bytes` payload (`2 × float64` little-endian) for `[Time, <EEG|ECG|EMG>]`. Emitted at the start of every segment and every 5.0 seconds (`2500` samples).
  4. `0x04` **Delta Chunk (Batched):** Typically `40 bytes` payload (`10 samples × 2 channels × int16_le`) emitted every `20 ms`. Note that the backend BRT1 parser must support multi-sample `0x04` Delta chunks where `payload_length == num_samples * len(channels) * 2` (in addition to single-sample `0x04` chunks where `num_samples == 1`).
  5. `0xFF` **End-Of-Stream Chunk:** `b"\x00\x00\xff"`.

---

### 2.2 Raw Channels Uploaded Over the Wire (`500 Hz` Only)

**Only two channels are ever uploaded by the AUX app** (`Time` plus whichever single raw hardware mode channel is active — never derived channels):

| Uploaded Channel Name | BRT1 `scale` | Sampling Rate | Units | Range / Semantics |
| :--- | :---: | :---: | :--- | :--- |
| **`Time`** | `0.0001` | `500 Hz` ($\Delta t = 0.0020\text{ s}$) | `s` | Seconds elapsed relative to the session start (`Date` + `Time` in `metadata`). Synchronized and drift-disciplined to the host PC clock ($100\ \mu\text{s}$ resolution). |
| **`EEG`** *(or **`ECG`** or **`EMG`**)* | `1.0` | `500 Hz` ($\Delta t = 0.0020\text{ s}$) | `ADC counts` | Raw 10-bit DC-coupled bio-amplifier output (`0.0` to `1023.0`, resting DC midpoint $\approx 512.0 \equiv 1.65\text{ V}$) for the selected Spiker:bit hardware filter bank. |

---

### 2.3 Server-Side Derived Channels to Compute on the Backend

Depending on which raw channel (`EEG`, `ECG`, or `EMG`) is present in the uploaded `"spikerbit"` stream, the backend can compute the following **derived telemetry channels** during post-processing (either resampled/aligned onto the `100 Hz` primary vehicle telemetry timeline for lap/corner overlays, or retained at `500 Hz` for high-resolution waveform inspection).

#### Common Pre-Processing Step (All Modes)
1. **DC Centering & Rail Detection:**
   - Let $x_{\text{raw}}(t)$ be the uploaded `EEG`, `ECG`, or `EMG` channel (`0..1023`).
   - Samples with $x_{\text{raw}} \le 10$ or $x_{\text{raw}} \ge 1013$ are flagged as rail saturation / lead-off artifacts.
   - Remove DC offset via a `0.3 Hz` 2nd-order high-pass filter or 5-second rolling median: $x_{\text{dc}}(t) = x_{\text{raw}}(t) - \text{median}_{5\text{s}}(x_{\text{raw}})$.
2. **Mains Hum Notch Filter (`50 Hz` & `60 Hz`):**
   - Apply zero-phase (`scipy.signal.sosfiltfilt`) IIR notch filters at **$50.0\text{ Hz}$** ($Q = 30$) and **$60.0\text{ Hz}$** ($Q = 30$) (plus $100\text{ Hz}$ / $120\text{ Hz}$ harmonics when raw channel is `EMG`) to obtain the clean 500 Hz signal $x(t)$.

---

#### Mode 1: Derived Channels from Raw `EEG` Channel (Forehead-Temple `Fpz–F8` Montage)

| Derived Channel Name | Suggested Rate | Units | Band / Window | How to Compute from Raw `EEG` |
| :--- | :---: | :---: | :--- | :--- |
| **`EEG Filtered`** | `500 Hz` / `100 Hz` | `ADC` | $0.5\text{–}40\text{ Hz}$ | Zero-phase 4th-order Butterworth bandpass ($0.5\text{–}40\text{ Hz}$) of $x(t)$. |
| **`EOG Gaze`** | `100 Hz` | `ADC` | $0.3\text{–}3.0\text{ Hz}$ | Zero-phase 4th-order Butterworth bandpass ($0.3\text{–}3.0\text{ Hz}$) of $x(t)$. Captures vertical eye blinks and horizontal "look-to-apex" corneo-retinal dipole deflections. |
| **`Blink Event`** | `100 Hz` | `0` or `1` | $100\text{–}300\text{ ms}$ peaks | Binary indicator (`1` during detected eye blink, `0` otherwise). Detect peaks in $|x_{\text{EOG}}(t)| > 4.0 \times \sigma_{\text{robust}}$ where $\sigma_{\text{robust}} = 1.4826 \times \text{MAD}(x_{\text{EOG}})$ and minimum peak distance $= 200\text{ ms}$. |
| **`Blink Rate`** | `100 Hz` | `blinks/min` | $30\text{ s}$ rolling window | Rolling count of detected blinks over a $30\text{ s}$ window multiplied by $2.0$. Drops in high-focus braking/cornering zones. |
| **`Blink Duration`** | `100 Hz` | `ms` | Per-blink FWHM | Full-Width at Half-Maximum (FWHM in `ms`) of the most recent blink (or rolling average over last 5 blinks). Increases from $\sim 115\text{ ms}$ (alert) to $>180\text{ ms}$ with driver fatigue. |
| **`Gaze Lead Time`** | `100 Hz` | `ms` | $5\text{ s}$ sliding xcorr | Cross-correlation lag (in `ms`, search range $[-500\text{ ms}, +500\text{ ms}]$) maximizing correlation between blink-masked `EOG Gaze` ($0.3\text{–}3\text{ Hz}$) and the vehicle's `Steering Angle` channel. Positive values indicate how many milliseconds ahead of turn-in the driver looks toward the apex. |
| **`EEG Theta Power`** | `100 Hz` | `ADC²` (or $Z$-score) | $4.0\text{–}8.0\text{ Hz}$ | Bandpass filter $x(t)$ to $4.0\text{–}8.0\text{ Hz}$ (masking/interpolating across `Blink Event == 1`), square the signal, and smooth with a $2.0\text{ s}$ moving average. Measures executive cognitive workload in technical corners. |
| **`EEG Alpha Power`** | `100 Hz` | `ADC²` (or $Z$-score) | $8.0\text{–}13.0\text{ Hz}$ | Bandpass filter $x(t)$ to $8.0\text{–}13.0\text{ Hz}$ (blink-masked), square, and smooth with a $2.0\text{ s}$ moving average. Drops (*Alpha Suppression*) during intense visual attention; rises on straights/pits. |
| **`EEG Beta Power`** | `100 Hz` | `ADC²` (or $Z$-score) | $13.0\text{–}30.0\text{ Hz}$ | Bandpass filter $x(t)$ to $13.0\text{–}30.0\text{ Hz}$ (blink-masked, with optional $1 + (f/10)^2$ pre-emphasis to compensate the Spiker:bit's $10\text{ Hz}$ RC roll-off), square, and smooth with a $2.0\text{ s}$ moving average. |
| **`Cognitive Engagement`** | `100 Hz` | `ratio` | $2.0\text{ s}$ rolling | Task engagement / flow ratio: $$\frac{\text{EEG Beta Power}}{\text{EEG Alpha Power} + \text{EEG Theta Power} + \varepsilon}$$ |
| **`Cognitive Fatigue`** | `100 Hz` | `ratio` | $2.0\text{ s}$ rolling | Drowsiness / stint fatigue ratio: $$\frac{\text{EEG Theta Power}}{\text{EEG Alpha Power} + \varepsilon}$$ |
| **`Jaw Tension RMS`** | `100 Hz` | `ADC` | $20.0\text{–}45.0\text{ Hz}$ | Bandpass filter $x(t)$ to $20.0\text{–}45.0\text{ Hz}$ and compute a $100\text{ ms}$ moving Root-Mean-Square (RMS) envelope. Captures involuntary *temporalis*/*frontalis* jaw-clench and brow tension during high-G corners, FFB spikes, and oversteer saves. |

---

#### Mode 2: Derived Channels from Raw `ECG` Channel (Chest / Collarbone Lead Montage)

| Derived Channel Name | Suggested Rate | Units | Band / Window | How to Compute from Raw `ECG` |
| :--- | :---: | :---: | :--- | :--- |
| **`ECG Filtered`** | `500 Hz` / `100 Hz` | `ADC` | $0.5\text{–}40.0\text{ Hz}$ | Zero-phase 4th-order Butterworth bandpass ($0.5\text{–}40.0\text{ Hz}$) of $x(t)$ preserving P-QRS-T morphology. |
| **`ECG QRS Envelope`** | `100 Hz` | `ADC` | $5.0\text{–}35.0\text{ Hz}$ | Bandpass filter $x(t)$ to $5.0\text{–}35.0\text{ Hz}$ and compute a $40\text{ ms}$ moving RMS envelope for R-peak detection. |
| **`Heart Rate`** | `100 Hz` | `BPM` | R-R intervals | Detect R-peaks on $5\text{–}35\text{ Hz}$ filtered ECG (`min_distance = 150 samples = 300 ms`, max 200 BPM). Compute instantaneous $\text{BPM} = 60.0 / \Delta t_{RR}$ (valid for $0.30\text{ s} \le \Delta t_{RR} \le 2.0\text{ s}$) and interpolate onto the `100 Hz` timeline. |
| **`HRV RMSSD`** | `100 Hz` | `ms` | $30\text{ s}$ rolling window | Root Mean Square of Successive Differences of valid R-R intervals ($\Delta t_{RR}$ in `ms`) over a sliding $30\text{ s}$ window: $$\text{RMSSD} = \sqrt{\frac{1}{K-1}\sum_{i=1}^{K-1} (\text{RR}_{i+1} - \text{RR}_i)^2}$$ Drops under sympathetic stress/adrenaline; higher during calm parasympathetic regulation. |

---

#### Mode 3: Derived Channels from Raw `EMG` Channel (Forearm / Shoulder / Leg Surface Muscle Montage)

| Derived Channel Name | Suggested Rate | Units | Band / Window | How to Compute from Raw `EMG` |
| :--- | :---: | :---: | :--- | :--- |
| **`EMG Filtered`** | `500 Hz` | `ADC` | $20.0\text{–}200.0\text{ Hz}$ | Zero-phase 4th-order Butterworth bandpass ($20.0\text{–}200.0\text{ Hz}$) of $x(t)$ (exploiting the full $250\text{ Hz}$ Nyquist bandwidth of the 500 Hz stream). |
| **`Muscle Activation RMS`** | `100 Hz` | `ADC` | $50\text{ ms}$ RMS | Moving $50\text{ ms}$ ($25$-sample at 500 Hz) RMS envelope of `EMG Filtered`, downsampled to `100 Hz`. Quantifies physical steering/braking muscle effort. |
| **`Muscle Fatigue MDF`** | `100 Hz` | `Hz` | $2.0\text{ s}$ STFT window | Rolling Median Frequency ($\text{MDF}$) of the $20\text{–}200\text{ Hz}$ EMG power spectrum during active contractions (`Muscle Activation RMS > threshold`). A downward shift in $\text{MDF}$ (e.g., from $\sim 95\text{ Hz}$ toward $\sim 65\text{ Hz}$) over a race stint indicates peripheral muscle fatigue. |

---

## 3. Spiker:bit Hardware Architecture & Reverse-Engineered Findings

From inspecting the official `BackyardBrains/pxt-spikerbit` extension source code (`spikerbit.ts`), the Spiker:bit expansion board operates as follows:

### 3.1 Pinout & Hardware Multiplexer Truth Table
The Spiker:bit has **one physical analog instrumentation amplifier** connected to the orange 3-lead electrode jack (Lead 1 `+`, Lead 2 `-`, and Black `GND`).

| micro:bit Edge Pin | Direction | Function on Spiker:bit PCB |
| :--- | :--- | :--- |
| **`P1` (`AnalogPin.P1`)** | Analog In | **Single conditioned biosignal output** (`0–3.3 V`, DC-coupled, resting midpoint at `512` on 10-bit ADC / `~1.65 V`). |
| **`P8` (`DigitalPin.P8`)** | Digital Out | Hardware filter/gain multiplexer control bit 0. |
| **`P9` (`DigitalPin.P9`)** | Digital Out | Hardware filter/gain multiplexer control bit 1. |
| **`P0`, `P2`, `P16`** | Digital/Analog | Auxiliary servo / Grove expansion pass-through ports. |
| **`3V`, `GND`** | Power | When the micro:bit is connected to a PC via USB, the micro:bit's onboard $3.3\text{ V}$ LDO regulator powers the Spiker:bit directly through the `3V` edge ring (**no batteries required** for USB-tethered operation). |

#### Hardware Mode Selection (`P8`, `P9`):
| Raw Channel / Mode | `P8` | `P9` | Hardware Filter / Gain Characteristics |
| :--- | :---: | :---: | :--- |
| **`EMG` (Muscle)** | `0` | `0` | Default power-on state. High-frequency passband for muscle action potentials ($20\text{–}200\text{ Hz}$). |
| **`ECG` (Heart)** | `1` | `0` | Wideband ($\sim 0.5\text{–}100\text{ Hz}$); ideal for P-QRS-T complex and R-R interval HRV analysis. |
| **`EEG` (Brain)** | `0` | `1` | High gain ($\sim 32,500\times$ effective), hardware analog passband **$0.34\text{ Hz to }\sim 10\text{–}12\text{ Hz}$** ($-3\text{ dB}$ corner) with gentle RC roll-off capturing EOG, ERPs, $\theta/\alpha/\beta$, and surviving $20\text{–}45\text{ Hz}$ jaw micro-EMG. |

---

## 4. Electrode Placement Montages

### Recommended "All-in-One" Montage (`EEG` Channel): Asymmetric Forehead-Temple (`Fpz–F8`)
If Lead 1 and Lead 2 are placed symmetrically on the left and right forehead (`Fp1–Fp2`), midline cortical signals (`Fpz`) reach both electrodes with equal amplitude and are **subtracted to zero** by the differential amplifier. Using an **asymmetric montage** breaks the symmetry so ocular, cortical, and cranial muscle signals can be captured simultaneously in one run:

| Electrode Clip | Placement Site (10-20 System) | Purpose |
| :--- | :--- | :--- |
| **Lead 1 (`+`)** | **Above Right Eyebrow / Forehead (`Fp2` / `Fpz`)** (~1–2 cm above eyebrow) | Sits directly above the eye to capture strong vertical eye-blink spikes (`+V`) and frontal cortex activity (Theta, Alpha, Beta). |
| **Lead 2 (`-`)** | **Lower Outer Temple / Upper Cheekbone** (just *below* outer corner of right eye, or mastoid behind right ear) | Creates **vertical separation** across the eyeball so vertical blinks do not cancel out as common-mode, while still sitting over *temporalis*/*masseter* for jaw tension and horizontal gaze saccades. |
| **Ground (`GND`)** | **Left Earlobe or Mastoid (`A1`)** | Electrically quiet zero-voltage reference point. |

### Alternative Specialized Montages
* **Chest ECG (`ECG` Channel, `P8=1, P9=0`):** Lead 1 (`+`) on left lower ribcage or left collarbone, Lead 2 (`-`) on right collarbone, `GND` on earlobe or right hip.
* **Forearm / Shoulder EMG (`EMG` Channel, `P8=0, P9=0`):** Lead 1 (`+`) and Lead 2 (`-`) spaced $2\text{–}3\text{ cm}$ along the belly of the *flexor carpi radialis* / *extensor digitorum* (forearm) or *anterior deltoid* (shoulder), `GND` on elbow epicondyle or earlobe.
