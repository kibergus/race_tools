"""USB Serial (115200-baud) & BLE Wireless BRT1 reader, min-RTT PI clock sync, and simulator."""

import asyncio
import collections
import math
import os
import queue
import struct
import sys
import threading
import time
from typing import Callable, Deque, List, Optional, Tuple

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spikerbit.brt1_protocol import (  # noqa: E402
    Brt1DecodedEvent,
    Brt1StreamDecoder,
    CHUNK_BASELINE,
    CHUNK_CLOCK_PONG,
    CHUNK_DELTA,
    MAGIC_CHUNK_BYTES,
    SPIKERBIT_CHANNELS,
    encode_baseline_chunk,
    encode_clock_pong_chunk,
    frame_brt1_chunk,
)

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover
    serial = None  # type: ignore

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover
    BleakClient = None  # type: ignore
    BleakScanner = None  # type: ignore


VALID_MODES = ('EEG', 'ECG', 'EMG')
NOMINAL_CC_TICKS = 4000
MIN_CC_TICKS = 3960
MAX_CC_TICKS = 4040

BLE_PORT_PREFIX = 'BLE://'
BLE_SERVICE_UUID = '5b170001-b5a3-f393-e0a9-e50e24dcca9e'
BLE_RX_CHAR_UUID = '5b170002-b5a3-f393-e0a9-e50e24dcca9e'
BLE_TX_CHAR_UUID = '5b170003-b5a3-f393-e0a9-e50e24dcca9e'


def is_ble_port(port: Optional[str]) -> bool:
    """Return True if `port` specifies a BLE wireless Spiker:bit target."""
    if not port:
        return False
    up = port.strip().upper()
    return up == 'BLE' or up.startswith(BLE_PORT_PREFIX)


def parse_ble_address(port: str) -> str:
    """Extract raw BLE MAC/UUID address from `'BLE://AA:BB:CC:DD:EE:FF (Spiker:bit A4F2)'`."""
    raw = port.strip()
    if raw.upper() == 'BLE':
        return ''
    if raw.upper().startswith(BLE_PORT_PREFIX):
        raw = raw[len(BLE_PORT_PREFIX):].strip()
    if ' ' in raw:
        raw = raw.split(' ', 1)[0].strip()
    return raw


class ClockSyncController:
    """Host-to-micro:bit PI clock phase and frequency discipline with NTP min-RTT filtering."""

    def __init__(
        self,
        ema_alpha: float = 0.25,
        kp_rate: float = 0.08,
        ki_rate: float = 0.015,
        rtt_window_size: int = 8,
    ) -> None:
        self.ema_alpha = float(ema_alpha)
        self.kp_rate = float(kp_rate)
        self.ki_rate = float(ki_rate)
        self.filtered_error_sec: float = 0.0
        self.integral_error_sec: float = 0.0
        self.current_cc_ticks: int = NOMINAL_CC_TICKS
        self.last_rtt_ms: Optional[float] = None
        self.min_rtt_ms: Optional[float] = None
        self._initialized: bool = False
        self._last_update_host_time: Optional[float] = None
        self._last_raw_error_sec: float = 0.0
        self._rtt_window: Deque[List[float]] = collections.deque(maxlen=max(1, rtt_window_size))

    def reset(self) -> None:
        """Reset filter state and min-RTT window to nominal 4000 ticks."""
        self.filtered_error_sec = 0.0
        self.integral_error_sec = 0.0
        self.current_cc_ticks = NOMINAL_CC_TICKS
        self.last_rtt_ms = None
        self.min_rtt_ms = None
        self._initialized = False
        self._last_update_host_time = None
        self._last_raw_error_sec = 0.0
        self._rtt_window.clear()

    @staticmethod
    def format_sync_command(elapsed_sec: float, mode: str = 'EEG') -> bytes:
        """Format initial clock sync command `S:<time_100us>:<mode>\\n`."""
        clean_mode = mode.upper() if mode.upper() in VALID_MODES else 'EEG'
        time_100us = max(0, int(round(elapsed_sec * 10000.0)))
        return f'S:{time_100us}:{clean_mode}\n'.encode('ascii')

    @staticmethod
    def format_ping_command(elapsed_sec: float) -> bytes:
        """Format precision min-RTT probe command `P:<host_t0_us>\\n`."""
        t0_us = max(0, int(round(elapsed_sec * 1_000_000.0)))
        return f'P:{t0_us}\n'.encode('ascii')

    @staticmethod
    def format_trim_command(offset_100us: int, cc_ticks: int) -> bytes:
        """Format periodic clock trim command `T:<offset_100us>:<cc_ticks>\\n`."""
        clamped_cc = max(MIN_CC_TICKS, min(MAX_CC_TICKS, int(cc_ticks)))
        return f'T:{int(offset_100us)}:{clamped_cc}\n'.encode('ascii')

    @staticmethod
    def format_mode_command(mode: str) -> bytes:
        """Format hardware filter bank mode switch command `M:<EEG|ECG|EMG>\\n`."""
        clean_mode = mode.upper()
        if clean_mode not in VALID_MODES:
            raise ValueError(f'Invalid Spiker:bit mode: {mode}')
        return f'M:{clean_mode}\n'.encode('ascii')

    def update(self, host_elapsed_sec: float, mcu_time_sec: float) -> Tuple[int, int]:
        """Compute `(offset_100us, cc_ticks)` from host vs. micro:bit timestamp."""
        raw_error = float(host_elapsed_sec) - float(mcu_time_sec)
        if not self._initialized:
            self.filtered_error_sec = raw_error
            self._last_raw_error_sec = raw_error
            self._last_update_host_time = float(host_elapsed_sec)
            self._initialized = True
            drift_rate = 0.0
            dt = 2.0
        else:
            self.filtered_error_sec = (
                self.ema_alpha * raw_error
                + (1.0 - self.ema_alpha) * self.filtered_error_sec
            )
            dt = 2.0
            if self._last_update_host_time is not None:
                dt = max(0.2, float(host_elapsed_sec) - self._last_update_host_time)
            drift_rate = (raw_error - self._last_raw_error_sec) / dt
            self._last_raw_error_sec = raw_error
            self._last_update_host_time = float(host_elapsed_sec)

        self.integral_error_sec = max(
            -0.05, min(0.05, self.integral_error_sec + self.filtered_error_sec * dt)
        )

        # If mcu_time lags host_elapsed (raw_error > 0), reduce CC[0] slightly so timer fires faster
        cc_delta = -NOMINAL_CC_TICKS * (
            self.kp_rate * drift_rate + self.ki_rate * self.integral_error_sec
        )
        new_cc = int(round(NOMINAL_CC_TICKS + cc_delta))
        self.current_cc_ticks = max(MIN_CC_TICKS, min(MAX_CC_TICKS, new_cc))

        offset_100us = int(round(self.filtered_error_sec * 10000.0))
        if abs(offset_100us) > 50000:
            applied_sec = offset_100us * 0.0001
            self._shift_rtt_window(applied_sec)
            return offset_100us, self.current_cc_ticks

        offset_100us = max(-5000, min(5000, offset_100us))
        applied_sec = offset_100us * 0.0001
        self.filtered_error_sec -= applied_sec
        self._last_raw_error_sec -= applied_sec
        self._shift_rtt_window(applied_sec)
        return offset_100us, self.current_cc_ticks

    def update_from_pong(
        self,
        host_t0_us: int,
        host_t1_sec: float,
        mcu_time_100us: int,
    ) -> Optional[Tuple[int, int]]:
        """Filter BLE/USB round-trip `PONG` via sliding-window min-RTT and compute trim."""
        host_t0_sec = float(host_t0_us) * 1e-6
        rtt_sec = float(host_t1_sec) - host_t0_sec
        if rtt_sec < 0.0:
            return None
        if self._initialized and rtt_sec > 0.250:
            # Reject stalled 2.4 GHz link-layer retransmission burst
            return None

        self.last_rtt_ms = rtt_sec * 1000.0
        host_mid_sec = 0.5 * (host_t0_sec + float(host_t1_sec))
        mcu_sec = float(mcu_time_100us) * 0.0001
        raw_offset_sec = host_mid_sec - mcu_sec

        self._rtt_window.append([rtt_sec, host_mid_sec, raw_offset_sec])
        best_rtt, _best_mid, best_offset = min(self._rtt_window, key=lambda item: item[0])
        self.min_rtt_ms = best_rtt * 1000.0

        # Evaluate update at current monotonic host_t1_sec with min-RTT offset
        return self.update(
            host_elapsed_sec=float(host_t1_sec),
            mcu_time_sec=float(host_t1_sec) - best_offset,
        )

    def _shift_rtt_window(self, applied_offset_sec: float) -> None:
        if applied_offset_sec == 0.0:
            return
        for item in self._rtt_window:
            item[2] -= applied_offset_sec


class MicrobitSimulator:
    """Realistic 500 Hz hardware-accurate micro:bit BRT1 stream simulator."""

    def __init__(self, initial_mode: str = 'EEG') -> None:
        self.mode: str = initial_mode if initial_mode in VALID_MODES else 'EEG'
        self.time_100us: int = 0
        self.cc_ticks: int = NOMINAL_CC_TICKS
        self.force_baseline: bool = True
        self.send_magic: bool = True
        self.chunks_since_baseline: int = 0
        self._tracked_time_100us: int = 0
        self._tracked_adc: int = 512
        self._sample_index: int = 0
        self._rx_buf: bytearray = bytearray()
        self._pending_pongs: Deque[bytes] = collections.deque()

    def write_command(self, data: bytes) -> None:
        """Process incoming host commands (`S:`, `P:`, `T:`, `M:`, `PING`)."""
        self._rx_buf.extend(data)
        while b'\n' in self._rx_buf:
            line_bytes, _, rest = self._rx_buf.partition(b'\n')
            self._rx_buf = bytearray(rest)
            cmd = line_bytes.decode('ascii', errors='ignore').strip()
            self._execute_cmd(cmd)

    def _execute_cmd(self, cmd: str) -> None:
        if cmd.startswith('S:'):
            parts = cmd.split(':')
            if len(parts) >= 2:
                try:
                    self.time_100us = int(parts[1])
                except ValueError:
                    pass
            if len(parts) >= 3 and parts[2].upper() in VALID_MODES:
                self.mode = parts[2].upper()
            self.send_magic = True
            self.force_baseline = True
        elif cmd.startswith('P:'):
            try:
                t0_us = int(cmd[2:].strip())
                self._pending_pongs.append(encode_clock_pong_chunk(t0_us, self.time_100us))
            except ValueError:
                pass
        elif cmd.startswith('T:'):
            parts = cmd.split(':')
            if len(parts) >= 2:
                try:
                    self.time_100us += int(parts[1])
                except ValueError:
                    pass
            if len(parts) >= 3:
                try:
                    cc = int(parts[2])
                    if MIN_CC_TICKS <= cc <= MAX_CC_TICKS:
                        self.cc_ticks = cc
                except ValueError:
                    pass
        elif cmd.startswith('M:'):
            mode_str = cmd[2:].strip().upper()
            if mode_str in VALID_MODES:
                self.mode = mode_str
                self.force_baseline = True
        elif cmd.startswith('PING'):
            self.send_magic = True
            self.force_baseline = True

    def _synthesize_sample(self, t_sec: float) -> int:
        """Synthesize a realistic 10-bit ADC sample (0..1023, midpoint 512) for the current mode."""
        if self.mode == 'EEG':
            alpha = 28.0 * math.sin(2.0 * math.pi * 10.0 * t_sec)
            theta = 18.0 * math.sin(2.0 * math.pi * 6.0 * t_sec + 0.5)
            beta = 14.0 * math.sin(2.0 * math.pi * 20.0 * t_sec + 1.1)
            phase_blink = t_sec % 4.0
            blink = 0.0
            if 1.80 <= phase_blink <= 2.10:
                blink = 210.0 * math.exp(-((phase_blink - 1.95) ** 2) / (2.0 * 0.035 ** 2))
            val = 512.0 + alpha + theta + beta + blink
        elif self.mode == 'ECG':
            rr_phase = (t_sec % 0.80) / 0.80
            p_wave = 25.0 * math.exp(-((rr_phase - 0.18) ** 2) / (2.0 * 0.025 ** 2))
            q_wave = -35.0 * math.exp(-((rr_phase - 0.32) ** 2) / (2.0 * 0.008 ** 2))
            r_wave = 290.0 * math.exp(-((rr_phase - 0.34) ** 2) / (2.0 * 0.007 ** 2))
            s_wave = -55.0 * math.exp(-((rr_phase - 0.36) ** 2) / (2.0 * 0.009 ** 2))
            t_wave = 55.0 * math.exp(-((rr_phase - 0.56) ** 2) / (2.0 * 0.038 ** 2))
            val = 512.0 + p_wave + q_wave + r_wave + s_wave + t_wave
        else:
            burst_env = 0.15 + 0.85 * max(0.0, math.sin(2.0 * math.pi * 0.4 * t_sec))
            carrier = (
                120.0 * math.sin(2.0 * math.pi * 65.0 * t_sec)
                + 85.0 * math.sin(2.0 * math.pi * 110.0 * t_sec + 0.7)
                + 55.0 * math.sin(2.0 * math.pi * 145.0 * t_sec + 1.9)
            )
            val = 512.0 + burst_env * carrier

        return max(0, min(1023, int(round(val))))

    def generate_next_20ms_packet(self) -> bytes:
        """Generate one 20 ms (10-sample) native BRT1 packet matching the micro:bit DMA ISR."""
        out = bytearray()
        while self._pending_pongs:
            out.extend(self._pending_pongs.popleft())

        times_100us: List[int] = []
        adc_vals: List[int] = []

        for _ in range(10):
            self.time_100us += 20  # 2.000 ms = 20 * 100 us
            self._sample_index += 1
            t_sec = self.time_100us * 0.0001
            times_100us.append(self.time_100us)
            adc_vals.append(self._synthesize_sample(t_sec))

        if self.send_magic:
            self.send_magic = False
            out.extend(MAGIC_CHUNK_BYTES)

        if self.force_baseline or self.chunks_since_baseline >= 250:
            self.force_baseline = False
            self.chunks_since_baseline = 0
            t0_sec = times_100us[0] * 0.0001
            out.extend(encode_baseline_chunk([t0_sec, float(adc_vals[0])]))
            self._tracked_time_100us = times_100us[0]
            self._tracked_adc = adc_vals[0]
            start_idx = 1
        else:
            start_idx = 0

        deltas: List[int] = []
        for idx in range(start_idx, 10):
            q_dt = max(-32767, min(32767, times_100us[idx] - self._tracked_time_100us))
            self._tracked_time_100us += q_dt
            q_dadc = adc_vals[idx] - self._tracked_adc
            self._tracked_adc += q_dadc
            deltas.append(q_dt)
            deltas.append(q_dadc)

        if deltas:
            payload = struct.pack(f'<{len(deltas)}h', *deltas)
            out.extend(frame_brt1_chunk(CHUNK_DELTA, payload))

        self.chunks_since_baseline += 1
        return bytes(out)


def list_serial_ports() -> List[Tuple[str, str, bool]]:
    """Return available USB serial ports as `(device, description, is_microbit)`."""
    if serial is None:
        return []
    ports: List[Tuple[str, str, bool]] = []
    for p in serial.tools.list_ports.comports():
        desc = p.description or p.device
        is_mb = (
            (p.vid == 0x0D28 and p.pid == 0x0204)
            or 'microbit' in desc.lower()
            or 'micro:bit' in desc.lower()
            or 'cmsis-dap' in desc.lower()
            or 'mbed' in desc.lower()
        )
        ports.append((p.device, desc, is_mb))
    return ports


async def _discover_ble_spikerbits_async(timeout: float = 1.8) -> List[Tuple[str, str]]:
    if BleakScanner is None:
        return []
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    matches: List[Tuple[int, str, str]] = []
    target_uuid = BLE_SERVICE_UUID.lower()

    for _key, (dev, adv) in discovered.items():
        uuids = [str(u).lower() for u in (adv.service_uuids or [])]
        name = (adv.local_name or dev.name or '').strip()
        if target_uuid in uuids or name.lower().startswith('spiker:bit'):
            label_name = name or 'Spiker:bit'
            rssi = int(adv.rssi) if adv.rssi is not None else -99
            port_entry = f'{BLE_PORT_PREFIX}{dev.address} ({label_name})'
            desc = f'{label_name} ({rssi} dBm)'
            matches.append((rssi, port_entry, desc))

    matches.sort(key=lambda item: item[0], reverse=True)
    return [(port_entry, desc) for _rssi, port_entry, desc in matches]


def scan_ble_spikerbits(timeout: float = 1.8) -> List[Tuple[str, str]]:
    """Scan for advertising BLE Spiker:bit devices; safe to call from background threads."""
    if BleakScanner is None:
        return []
    try:
        return asyncio.run(_discover_ble_spikerbits_async(timeout=timeout))
    except Exception:
        return []


class SpikerbitSerialClient:
    """Manages 500 Hz BRT1 acquisition over BLE wireless, USB serial, or explicit simulator."""

    def __init__(
        self,
        on_raw_chunk: Optional[Callable[[Brt1DecodedEvent], None]] = None,
        ring_buffer_seconds: float = 10.0,
    ) -> None:
        self._on_raw_chunk = on_raw_chunk
        self._max_samples = int(round(ring_buffer_seconds * 500))
        self._samples_lock = threading.Lock()
        self._times_ring: Deque[float] = collections.deque(maxlen=self._max_samples)
        self._adc_ring: Deque[float] = collections.deque(maxlen=self._max_samples)

        self._decoder = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
        self._clock_ctrl = ClockSyncController()
        self._mode: str = 'EEG'
        self._running = False
        self._simulate = False
        self._is_ble = False
        self._port_name: str = 'DISCONNECTED'
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._simulator = MicrobitSimulator(initial_mode=self._mode)
        self._tx_queue: 'queue.Queue[bytes]' = queue.Queue()
        self._error_lock = threading.Lock()
        self._pending_error: Optional[str] = None

        self._session_epoch_unix: float = time.time()
        self._last_trim_mono: float = 0.0
        self._last_mcu_time_sec: float = 0.0
        self._samples_received: int = 0

    @property
    def mode(self) -> str:
        """Return currently selected Spiker:bit mode (`EEG`, `ECG`, `EMG`)."""
        return self._mode

    @property
    def is_running(self) -> bool:
        """Return True if the acquisition thread is running."""
        return self._running

    @property
    def is_simulating(self) -> bool:
        """Return True if running in built-in 500 Hz simulator mode."""
        return self._simulate

    @property
    def is_ble(self) -> bool:
        """Return True if connected over Bluetooth Low Energy."""
        return self._is_ble

    @property
    def port_name(self) -> str:
        """Return active port name (`BLE://...`, `COMx`, `SIMULATOR`, or `DISCONNECTED`)."""
        return self._port_name

    @property
    def samples_received(self) -> int:
        """Return total number of 500 Hz samples decoded."""
        return self._samples_received

    @property
    def clock_controller(self) -> ClockSyncController:
        """Return the active PI clock controller."""
        return self._clock_ctrl

    def pop_connection_error(self) -> Optional[str]:
        """Return and clear any asynchronous connection error reported by the worker."""
        with self._error_lock:
            err = self._pending_error
            self._pending_error = None
            return err

    def _record_error(self, message: str) -> None:
        with self._error_lock:
            self._pending_error = message

    def set_session_start_unix(self, session_start_unix_sec: float) -> None:
        """Re-anchor the clock reference to a specific session start timestamp and sync MCU."""
        self._session_epoch_unix = float(session_start_unix_sec)
        self._clock_ctrl.reset()
        elapsed = max(0.0, time.time() - self._session_epoch_unix)
        self._send_bytes(ClockSyncController.format_sync_command(elapsed, self._mode))

    def set_mode(self, mode: str) -> None:
        """Switch Spiker:bit analog filter bank mode (`EEG`, `ECG`, `EMG`)."""
        clean = mode.upper()
        if clean not in VALID_MODES:
            raise ValueError(f'Unsupported mode: {mode}')
        self._mode = clean
        self._send_bytes(ClockSyncController.format_mode_command(clean))

    def get_recent_samples(self, max_count: Optional[int] = None) -> Tuple[List[float], List[float]]:
        """Return a thread-safe snapshot `(times_sec, adc_values)` from the ring buffer."""
        with self._samples_lock:
            if max_count is None or max_count >= len(self._times_ring):
                return list(self._times_ring), list(self._adc_ring)
            return (
                list(self._times_ring)[-max_count:],
                list(self._adc_ring)[-max_count:],
            )

    def start(self, port: Optional[str] = None, simulate: bool = False) -> None:
        """Start the 500 Hz acquisition thread on BLE, USB COM, or explicit simulator."""
        self.stop()
        self._decoder.reset()
        self._clock_ctrl.reset()
        with self._samples_lock:
            self._times_ring.clear()
            self._adc_ring.clear()
            self._samples_received = 0
        self._session_epoch_unix = time.time()
        with self._error_lock:
            self._pending_error = None
        while not self._tx_queue.empty():
            try:
                self._tx_queue.get_nowait()
            except queue.Empty:
                break

        if simulate or port == 'SIMULATOR':
            self._simulate = True
            self._is_ble = False
            self._port_name = 'SIMULATOR'
            self._simulator = MicrobitSimulator(initial_mode=self._mode)
        elif port in (None, '', 'DISCONNECTED'):
            self._simulate = False
            self._is_ble = False
            self._port_name = 'DISCONNECTED'
            return
        elif is_ble_port(port):
            if BleakClient is None:
                raise RuntimeError('bleak is not installed (run: pip install bleak)')
            self._simulate = False
            self._is_ble = True
            self._port_name = str(port).strip()
        else:
            if serial is None:
                raise RuntimeError('pyserial is not installed')
            self._simulate = False
            self._is_ble = False
            self._port_name = str(port).strip()

        stop_event = threading.Event()
        self._stop_event = stop_event
        self._running = True
        target_fn = self._ble_worker_entry if self._is_ble else self._worker_loop
        self._thread = threading.Thread(
            target=target_fn,
            args=(stop_event, self._simulate, self._port_name),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the acquisition thread to stop cleanly."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
            self._thread = None

    def _send_bytes(self, data: bytes) -> None:
        if self._simulate:
            self._simulator.write_command(data)
        else:
            self._tx_queue.put(data)

    def _process_incoming_bytes(self, raw: bytes, rx_host_elapsed_sec: float) -> None:
        """Decode BRT1 stream & PONG packets from USB Serial, BLE, or Simulator."""
        self._decoder.feed(raw)
        if not self._decoder.has_baseline:
            self._decoder.synchronize_to_magic_or_baseline()

        try:
            events = self._decoder.pop_events()
        except ValueError:
            self._decoder.reset()
            self._send_bytes(b'PING\n')
            return

        for ev in events:
            if ev.chunk_type == CHUNK_CLOCK_PONG and ev.clock_pong is not None:
                host_t0_us, mcu_time_100us = ev.clock_pong
                trim = self._clock_ctrl.update_from_pong(
                    host_t0_us=host_t0_us,
                    host_t1_sec=rx_host_elapsed_sec,
                    mcu_time_100us=mcu_time_100us,
                )
                if trim is not None:
                    offset_100us, cc_ticks = trim
                    if abs(offset_100us) >= 3 or cc_ticks != NOMINAL_CC_TICKS:
                        self._send_bytes(
                            ClockSyncController.format_trim_command(offset_100us, cc_ticks)
                        )
            elif ev.chunk_type in (CHUNK_BASELINE, CHUNK_DELTA) and ev.samples:
                with self._samples_lock:
                    for t_val, adc_val in ev.samples:
                        self._times_ring.append(t_val)
                        self._adc_ring.append(adc_val)
                        self._last_mcu_time_sec = t_val
                        self._samples_received += 1

                if self._on_raw_chunk is not None:
                    self._on_raw_chunk(ev)

    def _ble_worker_entry(
        self,
        stop_event: threading.Event,
        _simulate: bool,
        port_spec: str,
    ) -> None:
        try:
            asyncio.run(self._ble_worker_async(stop_event, port_spec))
        except Exception as exc:
            if not stop_event.is_set():
                self._record_error(f'BLE error ({port_spec}): {exc}')
        finally:
            if not stop_event.is_set():
                self._running = False
                self._simulate = False
                self._is_ble = False
                self._port_name = 'DISCONNECTED'

    async def _ble_worker_async(self, stop_event: threading.Event, port_spec: str) -> None:
        # Set gentler PI gains on BLE so 7.5-15ms connection intervals stay ultra-smooth
        self._clock_ctrl.ema_alpha = 0.18
        self._clock_ctrl.kp_rate = 0.04
        self._clock_ctrl.ki_rate = 0.008

        address = parse_ble_address(port_spec)
        if not address:
            found = await _discover_ble_spikerbits_async(timeout=2.5)
            if not found:
                raise RuntimeError(
                    'No BLE Spiker:bit found. Power on the Spiker:bit battery switch '
                    '(with no other host connected) and click Refresh.'
                )
            port_spec = found[0][0]
            self._port_name = port_spec
            address = parse_ble_address(port_spec)

        rx_packets: Deque[Tuple[bytes, float]] = collections.deque()
        wake_evt = asyncio.Event()
        peer_disconnected = asyncio.Event()

        def _on_disconnect(_client) -> None:
            peer_disconnected.set()
            wake_evt.set()

        def _on_notify(_sender, data: bytearray) -> None:
            rx_elapsed = max(0.0, time.time() - self._session_epoch_unix)
            rx_packets.append((bytes(data), rx_elapsed))
            wake_evt.set()

        async with BleakClient(
            address,
            disconnected_callback=_on_disconnect,
            timeout=10.0,
            pair=False,
        ) as client:
            if stop_event.is_set():
                return

            await client.start_notify(BLE_TX_CHAR_UUID, _on_notify)

            elapsed0 = max(0.0, time.time() - self._session_epoch_unix)
            sync_cmd = ClockSyncController.format_sync_command(elapsed0, self._mode)
            await client.write_gatt_char(BLE_RX_CHAR_UUID, sync_cmd, response=False)

            # Kick off an immediate min-RTT clock probe right after sync
            ping0 = ClockSyncController.format_ping_command(
                max(0.0, time.time() - self._session_epoch_unix)
            )
            await client.write_gatt_char(BLE_RX_CHAR_UUID, ping0, response=False)

            last_ping_mono = time.monotonic()
            last_wakeup_mono = time.monotonic()
            samples_at_start = self._samples_received

            while not stop_event.is_set():
                if peer_disconnected.is_set():
                    raise RuntimeError(f'Bluetooth connection lost ({port_spec})')

                # 1. Drain outgoing host commands (M:, S:, T:, P:)
                while not self._tx_queue.empty():
                    try:
                        pkt = self._tx_queue.get_nowait()
                    except queue.Empty:
                        break
                    await client.write_gatt_char(BLE_RX_CHAR_UUID, pkt, response=False)

                # 2. Process all received BLE GATT notifications
                while rx_packets:
                    raw_bytes, rx_elapsed = rx_packets.popleft()
                    self._process_incoming_bytes(raw_bytes, rx_elapsed)

                now_mono = time.monotonic()

                # Retry S: sync if first notification was still subscribing on connect
                if self._samples_received == samples_at_start and (now_mono - last_wakeup_mono) >= 0.40:
                    last_wakeup_mono = now_mono
                    elapsed_retry = max(0.0, time.time() - self._session_epoch_unix)
                    await client.write_gatt_char(
                        BLE_RX_CHAR_UUID,
                        ClockSyncController.format_sync_command(elapsed_retry, self._mode),
                        response=False,
                    )

                # Periodic min-RTT P: probe every 2.0 seconds
                if (now_mono - last_ping_mono) >= 2.0 and self._samples_received > 0:
                    last_ping_mono = now_mono
                    ping_pkt = ClockSyncController.format_ping_command(
                        max(0.0, time.time() - self._session_epoch_unix)
                    )
                    await client.write_gatt_char(BLE_RX_CHAR_UUID, ping_pkt, response=False)

                wake_evt.clear()
                if rx_packets or not self._tx_queue.empty():
                    continue
                try:
                    await asyncio.wait_for(wake_evt.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass

            # Clean shutdown when user stops or closes Studio
            if client.is_connected:
                try:
                    await client.write_gatt_char(BLE_RX_CHAR_UUID, b'STOP\n', response=False)
                except Exception:
                    pass
                try:
                    await client.stop_notify(BLE_TX_CHAR_UUID)
                except Exception:
                    pass

    @staticmethod
    def _open_serial_with_retry(port: str, stop_event: threading.Event):
        """Open micro:bit CDC serial port on the worker thread with Win32 usbser retry."""
        last_exc: Optional[Exception] = None
        for attempt in range(4):
            if stop_event.is_set():
                return None
            ser = None
            try:
                ser = serial.Serial()
                ser.port = port
                ser.baudrate = 115200
                ser.timeout = 0.02
                ser.write_timeout = 0.2
                ser.rts = False
                ser.dtr = True
                ser.open()
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                return ser
            except Exception as exc:
                last_exc = exc
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                if attempt < 3 and not stop_event.is_set():
                    stop_event.wait(0.25)
        if last_exc is not None:
            raise last_exc
        return None

    def _drain_tx_queue(self, ser) -> None:
        while not self._tx_queue.empty():
            try:
                pkt = self._tx_queue.get_nowait()
            except queue.Empty:
                break
            try:
                ser.write(pkt)
            except Exception:
                break

    def _worker_loop(
        self,
        stop_event: threading.Event,
        simulate: bool,
        port_name: str,
    ) -> None:
        ser = None
        try:
            if not simulate:
                try:
                    ser = self._open_serial_with_retry(port_name, stop_event)
                    if ser is None or stop_event.is_set():
                        return
                except Exception as exc:
                    self._record_error(f'Failed to open {port_name}: {exc}')
                    self._running = False
                    self._simulate = False
                    self._port_name = 'DISCONNECTED'
                    return

            elapsed0 = max(0.0, time.time() - self._session_epoch_unix)
            sync_cmd = ClockSyncController.format_sync_command(elapsed0, self._mode)
            if simulate:
                self._simulator.write_command(sync_cmd)
            else:
                stop_event.wait(0.04)
                self._tx_queue.put(sync_cmd)
                self._tx_queue.put(ClockSyncController.format_ping_command(elapsed0))

            self._last_trim_mono = time.monotonic()
            last_wakeup_mono = time.monotonic()
            next_sim_tick = time.monotonic()
            samples_at_start = self._samples_received

            while not stop_event.is_set():
                if simulate:
                    now_mono = time.monotonic()
                    if now_mono < next_sim_tick:
                        stop_event.wait(max(0.001, next_sim_tick - now_mono))
                        if stop_event.is_set():
                            break
                    next_sim_tick += 0.020
                    raw = self._simulator.generate_next_20ms_packet()
                else:
                    self._drain_tx_queue(ser)
                    try:
                        raw = ser.read(256)
                    except Exception as exc:
                        if not stop_event.is_set():
                            self._record_error(f'Serial connection lost on {port_name}: {exc}')
                            self._running = False
                            self._simulate = False
                            self._port_name = 'DISCONNECTED'
                        break

                if not raw:
                    if not simulate and self._samples_received == samples_at_start:
                        now_mono = time.monotonic()
                        if (now_mono - last_wakeup_mono) >= 0.25:
                            last_wakeup_mono = now_mono
                            elapsed_retry = max(0.0, time.time() - self._session_epoch_unix)
                            self._tx_queue.put(
                                ClockSyncController.format_sync_command(elapsed_retry, self._mode)
                            )
                    continue

                rx_elapsed = max(0.0, time.time() - self._session_epoch_unix)
                self._process_incoming_bytes(raw, rx_elapsed)

                now_mono = time.monotonic()
                if (now_mono - self._last_trim_mono) >= 2.0 and self._samples_received > 0:
                    self._last_trim_mono = now_mono
                    host_elapsed = max(0.0, time.time() - self._session_epoch_unix)
                    if simulate:
                        offset_100us, cc_ticks = self._clock_ctrl.update(
                            host_elapsed, self._last_mcu_time_sec
                        )
                        self._send_bytes(
                            ClockSyncController.format_trim_command(offset_100us, cc_ticks)
                        )
                    else:
                        # Probe exact hardware RTT; fallback to one-way update if older firmware
                        if self._clock_ctrl.min_rtt_ms is not None:
                            self._send_bytes(ClockSyncController.format_ping_command(host_elapsed))
                        else:
                            self._send_bytes(ClockSyncController.format_ping_command(host_elapsed))
                            offset_100us, cc_ticks = self._clock_ctrl.update(
                                host_elapsed, self._last_mcu_time_sec
                            )
                            self._send_bytes(
                                ClockSyncController.format_trim_command(offset_100us, cc_ticks)
                            )
        finally:
            if ser is not None:
                try:
                    if ser.is_open:
                        ser.write(b'STOP\n')
                except Exception:
                    pass
                try:
                    ser.close()
                except Exception:
                    pass
