# Tobii Eye Tracker 5 — Direct-USB & MediaPipe Primary Channels (`CHANNELS.md`)

## 1. Architecture & Sampling Overview

The `tobii` AUX application communicates **directly with the Tobii Eye Tracker 5 (`USB VID_2104&PID_0313`, Interface `0`)** over OS-native bulk/control USB transfers (`winusb.dll` on Windows, `libusb-1.0` on Linux) using the TTP/TLV framing protocol (adapted from Apache-2.0 [`njmill/tobii-linux`](https://github.com/njmill/tobii-linux)), with **zero Tobii SDK libraries or DLLs**.

* **Strict Primary-Only Policy:** No derived or time-windowed post-processing channels (such as rolling blink rates, FWHM blink durations, saccade velocities, or I-VT fixation classifications) are computed client-side. Only **raw hardware columns emitted by the EyeChip (`0x0500`)** and **direct neural-network outputs from Google MediaPipe Face Landmarker on raw NIR frames (`0x050e`)** are transmitted.
* **Two Independent Native-Rate `BRT1` Streams:**
  Because hardware gaze packets (`0x0500`) arrive immediately (`< 1 ms` latency, `33 Hz` idle scan ramping up to `90–133 Hz` when eyes are tracked) while in-band 8-bit grayscale NIR camera frames (`0x050e`) complete MediaPipe inference `~10–15 ms` after shutter exposure (at `~30 Hz`), the app uploads **two separate Brotli-compressed `BRT1` auxiliary streams** synchronized to the same `CommandSink` session (`session_start_unix_ms`):
  1. **`"stream": "tobii_gaze"`** — Hardware ocular & gaze channels from TTP stream `0x0500`.
  2. **`"stream": "tobii_face"`** — Primary 6-DoF head pose & ocular/facial blendshape scores extracted by MediaPipe from TTP NIR stream `0x050e` (stamped with the **exact hardware camera exposure timestamp** of the NIR frame, eliminating CNN inference lag).
* **Missing / Untracked Values:** Encoded natively in `BRT1` using IEEE `NaN` in `0x03` Baseline chunks and sentinel `-32768` (`0x8000`) in `0x04` `int16` Delta chunks.

---

## 2. Coordinate Systems

1. **2D Normalized Display Space (`[0.0, 1.0]`, Unclamped):**
   * `(0.0, 0.0)` = **top-left** corner of the tracker's configured display plane; `(0.5, 0.5)` = **center**; `(1.0, 1.0)` = **bottom-right**.
   * Values $< 0.0$ or $> 1.0$ occur naturally when looking slightly outside the screen bezel while remaining inside the IR track box.
2. **3D EyeChip Coordinate System (`mm`, Right-Handed):**
   * Origin `(0, 0, 0)` at the center of the tracker facing the user:
     * **`+X`**: Right when facing the user (toward the user's left).
     * **`+Y`**: Upward.
     * **`+Z`**: Distance outward from the tracker toward the user ($\approx 500\text{–}750\text{ mm}$).
3. **MediaPipe Camera Pose Space (`cm` & `deg`):**
   * Direct `4×4` canonical-face-to-NIR-camera rigid transformation matrix (`facial_transformation_matrixes[0]`):
     * Translation `[X, Y, Z]` in metric canonical face units (`cm`).
     * Orientation `[Yaw, Pitch, Roll]` in `degrees` extracted from the transformed forward/in-plane basis vectors.

---

## 3. Stream 1: Hardware Gaze & Ocular Stream (`"stream": "tobii_gaze"`)

Decoded directly from TTP notification `0x0500` (`STREAM_GAZE`) XDS (`0x0bb8`) TLV rows. Device `timestamp_us` (`col 0x01`) is disciplined against host Unix time via `STREAM_SYNC` (`0x1771`) and converted to seconds elapsed since `session_start_unix_ms`.

| BRT1 Channel Name | TTP `0x0500` Column | Validity Gate | Units | Typical Range | BRT1 `scale` | Semantics |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`Time`** | `0x01` (`s64`) | Required | `s` | $\ge 0.0$ | `0.0001` | Hardware sample timestamp relative to session start ($100\ \mu\text{s}$ resolution). |
| **`Gaze X`** | `0x1c` (`Q22.42`) | `col 0x1d == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | EyeChip velocity-adaptive filtered combined 2D horizontal gaze (`NaN` when invalid). |
| **`Gaze Y`** | `0x1c` (`Q22.42`) | `col 0x1d == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | EyeChip velocity-adaptive filtered combined 2D vertical gaze (`NaN` when invalid). |
| **`Gaze Unfiltered X`** | `0x20` (`Q22.42`) | `col 0x21 == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | Unfiltered low-latency combined 2D horizontal gaze (`NaN` when invalid). |
| **`Gaze Unfiltered Y`** | `0x20` (`Q22.42`) | `col 0x21 == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | Unfiltered low-latency combined 2D vertical gaze (`NaN` when invalid). |
| **`Left Gaze X`** | `0x05` (`Q22.42`) | `col 0x1e == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | Separate left-eye 2D horizontal display coordinate (`NaN` if absent/invalid). |
| **`Left Gaze Y`** | `0x05` (`Q22.42`) | `col 0x1e == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | Separate left-eye 2D vertical display coordinate (`NaN` if absent/invalid). |
| **`Right Gaze X`** | `0x0b` (`Q22.42`) | `col 0x1f == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | Separate right-eye 2D horizontal display coordinate (`NaN` if absent/invalid). |
| **`Right Gaze Y`** | `0x0b` (`Q22.42`) | `col 0x1f == 1` | `norm` | `[-0.3, 1.3]` | `0.0001` | Separate right-eye 2D vertical display coordinate (`NaN` if absent/invalid). |
| **`Left Pupil`** | `0x06` (`Q16.16`) | `col 0x07 == 1` | `mm` | `[1.5, 9.0]` | `0.001` | Left pupil diameter estimated by EyeChip (`NaN` if absent/invalid). |
| **`Right Pupil`** | `0x0c` (`Q16.16`) | `col 0x0d == 1` | `mm` | `[1.5, 9.0]` | `0.001` | Right pupil diameter estimated by EyeChip (`NaN` if absent/invalid). |
| **`Left Eye X`** | `0x02` (`Q22.42`) | `col 0x07 == 1` | `mm` | `[-350, +350]` | `0.1` | Left corneal/eyeball 3D origin X in tracker frame (`NaN` when occluded). |
| **`Left Eye Y`** | `0x02` (`Q22.42`) | `col 0x07 == 1` | `mm` | `[-350, +350]` | `0.1` | Left corneal/eyeball 3D origin Y in tracker frame (`NaN` when occluded). |
| **`Left Eye Z`** | `0x02` (`Q22.42`) | `col 0x07 == 1` | `mm` | `[350, 1000]` | `0.1` | Left corneal/eyeball 3D origin Z distance from tracker (`NaN` when occluded). |
| **`Right Eye X`** | `0x08` (`Q22.42`) | `col 0x0d == 1` | `mm` | `[-350, +350]` | `0.1` | Right corneal/eyeball 3D origin X in tracker frame (`NaN` when occluded). |
| **`Right Eye Y`** | `0x08` (`Q22.42`) | `col 0x0d == 1` | `mm` | `[-350, +350]` | `0.1` | Right corneal/eyeball 3D origin Y in tracker frame (`NaN` when occluded). |
| **`Right Eye Z`** | `0x08` (`Q22.42`) | `col 0x0d == 1` | `mm` | `[350, 1000]` | `0.1` | Right corneal/eyeball 3D origin Z distance from tracker (`NaN` when occluded). |

---

## 4. Stream 2: MediaPipe NIR Image Stream (`"stream": "tobii_face"`)

Extracted directly by Google MediaPipe `FaceLandmarker` (`face_landmarker.task`, `VIDEO` mode with `output_facial_transformation_matrixes=True` and `output_face_blendshapes=True`) from 8-bit grayscale in-band NIR frames (`0x050e` `STREAM_IMAGE_PRIMARY`). If no face is detected in an NIR frame, pose and blendshape channels emit `NaN` (`-32768`).

| BRT1 Channel Name | MediaPipe Primary Output | Units | Typical Range | BRT1 `scale` | Semantics |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **`Time`** | `0x050e` `col 0x0001` (`timestamp_us`) | `s` | $\ge 0.0$ | `0.0001` | Exact hardware NIR shutter timestamp relative to session start. |
| **`Head Pos X`** | `matrix[0, 3]` | `cm` | `[-40.0, +40.0]` | `0.01` | Lateral head translation in NIR camera coordinates. |
| **`Head Pos Y`** | `matrix[1, 3]` | `cm` | `[-40.0, +40.0]` | `0.01` | Vertical head translation in NIR camera coordinates. |
| **`Head Pos Z`** | `matrix[2, 3]` | `cm` | `[-120.0, -20.0]` | `0.01` | Depth translation along NIR camera optical axis. |
| **`Head Yaw`** | `atan2(R[0,2], R[2,2])` | `deg` | `[-90.0, +90.0]` | `0.01` | Left/right head turn angle from face-forward vector. |
| **`Head Pitch`** | `atan2(R[1,2], hypot(R[0,2], R[2,2]))` | `deg` | `[-90.0, +90.0]` | `0.01` | Up/down head nod angle from face-forward vector (`+` = up, `-` = down). |
| **`Head Roll`** | `atan2(R[1,0], R[0,0])` | `deg` | `[-90.0, +90.0]` | `0.01` | Ear-to-shoulder head roll angle. |
| **`Eye Blink Left`** | `blendshape["eyeBlinkLeft"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct left upper-eyelid closure score (`0.0` = open, `1.0` = shut). |
| **`Eye Blink Right`** | `blendshape["eyeBlinkRight"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct right upper-eyelid closure score (`0.0` = open, `1.0` = shut). |
| **`Eye Squint Left`** | `blendshape["eyeSquintLeft"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct left lower-lid squint score. |
| **`Eye Squint Right`** | `blendshape["eyeSquintRight"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct right lower-lid squint score. |
| **`Eye Wide Left`** | `blendshape["eyeWideLeft"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct left eyelid-widening / alertness score. |
| **`Eye Wide Right`** | `blendshape["eyeWideRight"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct right eyelid-widening / alertness score. |
| **`Jaw Open`** | `blendshape["jawOpen"]` | `score` | `[0.0, 1.0]` | `0.0001` | Direct mandible opening score (`0.0` = closed/clenched). |

---

## 5. Additional TTP Streams Used Internally (Not Uploaded as Channels)

| TTP Stream / Op | ID | Role in Application |
| :--- | :---: | :--- |
| **`STREAM_SYNC`** | `0x1771` | Device-to-host clock synchronization (`device_ts_us`, `receive_ts_us`), disciplining the tracker's microsecond clock to host Unix time so `tobii_gaze` and `tobii_face` align tightly with `brbrdaemon` and `rfactor_plugin`. |
| **`GET_DISPLAY_AREA`** / **`SET_DISPLAY_AREA`** | `0x0596` / `0x05a0` | Reads (or optionally overrides) the 3D display plane corners stored in tracker flash. |
| **`STREAM_IMAGE_SECONDARY`** | `0x0501` | Secondary/ROI IR image stream (not subscribed by default to conserve USB bandwidth; `0x050e` provides the full wide-angle NIR face frame). |
