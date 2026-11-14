# Spiker:bit 500 Hz Native BRT1 Firmware (`BBC micro:bit V2`)

This directory contains the firmware for the **Backyard Brains Spiker:bit + BBC micro:bit V2** sim-racing neuro-telemetry streamer.

## Hardware Architecture

1. **Crystal Clock (`HFCLK` + `TIMER1` 1 MHz Clock)**: Enables Nordic S113 SoftDevice (`LFCLK` calibrated RC) and locks the external 16 MHz crystal oscillator via `sd_clock_hfclk_request()` for `< 30 ppm` clock stability, with `NRF_TIMER1` (`1 µs` resolution) providing sub-100 µs hardware timestamp interpolation for `P:` -> `0x06` `PONG` min-RTT BLE clock sync.
2. **Dual-Transport (BLE Wireless Isolation + 115200-baud USB Serial)**:
   - **BLE Wireless**: Advertises as `"Spiker:bit XXXX"` (`5b170001-b5a3-f393-e0a9-e50e24dcca9e`, `ATT_MTU = 247`, `0 dBm`, linear LDO mode for zero switching ripple) for 100% galvanically isolated operation on Spiker:bit's 2× AAA batteries with USB unplugged.
   - **USB Serial**: Supports `115200`-baud CDC UART (`TX=P0.06`, `RX=P1.08`) simultaneously when connected via USB.
3. **Quiet Board Analog State**: Powers down LED matrix rows, onboard speaker (`P0.00`), and MEMS microphone bias (`P0.20`) to eliminate switching interference on analog input `P1`.
3. **Hardware Mode Mux (`P8` / `P9`)**:
   - `EEG` (Brain / EOG / Jaw Tension): `P8 = 0`, `P9 = 1`
   - `ECG` (Heart Rate / HRV): `P8 = 1`, `P9 = 0`
   - `EMG` (Muscle Activation): `P8 = 0`, `P9 = 0`
4. **Zero-Jitter `NRF_TIMER2` + `NRF_PPI` + `NRF_SAADC` (`EasyDMA`)**:
   - `NRF_TIMER2` runs at `16 MHz` with `CC[0] = 4000` ticks (`4000 Hz`, trimmable dynamically via the `T:` host command).
   - `NRF_PPI` routes `TIMER2->EVENTS_COMPARE[0]` directly to `SAADC->TASKS_SAMPLE` in hardware.
   - `NRF_SAADC` samples `P1` (`AIN1`) with **8× hardware oversampling** (`4000 Hz / 8 = 500.0 Hz`, $\Delta t = 2.000\text{ ms}$) and writes `10`-sample blocks (`20 ms`) into double-buffered RAM via `EasyDMA`.
5. **Native Uncompressed BRT1 Wire Stream (`115200` baud)**:
   - Channels (`EEG` / `ECG` / `EMG` named in `0x02` Metadata by host AUX app): `[{"name": "Time", "scale": 0.0001}, {"name": "EEG", "scale": 1.0}]`
   - Emits `0x01` Magic (`\x04\x00\x01BRT1`) on sync, `0x03` Baseline (`19 bytes`) every 5 seconds or on sync/mode switch, and `0x04` Delta chunks (`43 bytes` = 10 samples) every `20 ms` (`2,150 bytes/s` = 18.6% of `115200` baud).

## Host-to-micro:bit Commands (BLE RX Characteristic & `115200`-baud USB Serial)

- `S:<time_100us>:<EEG|ECG|EMG>\n` — Synchronize clock in `100 µs` units, set Spiker:bit mode, and emit `0x01` Magic + `0x03` Baseline.
- `P:<host_t0_us>\n` — Min-RTT clock probe; immediately replies with a link-local `0x06` `CHUNK_CLOCK_PONG` (`int64 host_t0_us`, `int64 mcu_time_100us`).
- `T:<offset_100us>:<cc_ticks>\n` — Apply phase trim (`offset_100us`) and `NRF_TIMER2->CC[0]` rate trim (`3960..4040` ticks).
- `M:<EEG|ECG|EMG>\n` — Switch Spiker:bit hardware filter bank (`P8`/`P9`) and emit a fresh `0x03` Baseline keyframe.
- `PING\n` — Request a fresh `0x01` Magic + `0x03` Baseline keyframe.
- `STOP\n` — Pause streaming on the active transport.

## Building & Flashing

To rebuild `spikerbit_firmware.hex` from `main.cpp` (automatically downloads a portable `arm-none-eabi-gcc` toolchain and Nordic `nRF52833` MDK headers into `build/` on first run):

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

To flash, use the **⚡ Flash micro:bit Firmware** button in the Spiker:bit AUX app (`python -m spikerbit.main`), run `.\build.ps1 -Target flash`, or copy `spikerbit_firmware.hex` directly onto the `MICROBIT` USB Mass Storage drive.
