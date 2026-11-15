"""BRT1 binary telemetry framing encoder and streaming decoder for Spiker:bit."""

from dataclasses import dataclass
import json
import math
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple


CHUNK_MAGIC = 0x01
CHUNK_METADATA = 0x02
CHUNK_BASELINE = 0x03
CHUNK_DELTA = 0x04
CHUNK_CLOCK_PONG = 0x06
CHUNK_EOS = 0xFF

NAN_SENTINEL = -32768

MAGIC_CHUNK_BYTES = b'\x04\x00\x01BRT1'
EOS_CHUNK_BYTES = b'\x00\x00\xff'

VALID_CHANNEL_MODES = ('EEG', 'ECG', 'EMG')

SPIKERBIT_CHANNELS: List[Dict[str, Any]] = [
    {'name': 'Time', 'scale': 0.0001},
    {'name': 'EEG', 'scale': 1.0},
]


def make_spikerbit_channels(mode: str = 'EEG') -> List[Dict[str, Any]]:
    """Return the 2-channel BRT1 schema `[Time, <EEG|ECG|EMG>]` for the active hardware mode."""
    clean_mode = mode.strip().upper()
    if clean_mode not in VALID_CHANNEL_MODES:
        raise ValueError(f'Unsupported Spiker:bit channel mode: {mode!r}')
    return [
        {'name': 'Time', 'scale': 0.0001},
        {'name': clean_mode, 'scale': 1.0},
    ]


def frame_brt1_chunk(chunk_type: int, payload: bytes) -> bytes:
    """Wrap payload in a 3-byte BRT1 chunk header [uint16_le length][uint8 type]."""
    length = len(payload)
    if length > 0xFFFF:
        raise ValueError(f'BRT1 chunk payload exceeds 65535 bytes ({length})')
    return struct.pack('<HB', length, chunk_type) + payload


def encode_magic_chunk() -> bytes:
    """Return the 7-byte BRT1 Magic chunk (0x01)."""
    return MAGIC_CHUNK_BYTES


def encode_eos_chunk() -> bytes:
    """Return the 3-byte BRT1 End-Of-Stream chunk (0xFF)."""
    return EOS_CHUNK_BYTES


def encode_clock_pong_chunk(host_t0_us: int, mcu_time_100us: int) -> bytes:
    """Encode a link-local 0x06 Clock Pong chunk (`int64 host_t0_us`, `int64 mcu_time_100us`)."""
    payload = struct.pack('<qq', int(host_t0_us), int(mcu_time_100us))
    return frame_brt1_chunk(CHUNK_CLOCK_PONG, payload)


def normalize_session_metadata(
    metadata: Dict[str, Any],
    stream_name: Optional[str] = 'spikerbit',
) -> Dict[str, Any]:
    """Build clean canonical session metadata matching `UPLOAD.md` without duplicate keys."""
    track = (
        metadata.get('Track name')
        or metadata.get('track_name')
        or 'Unknown Track'
    )
    date_str = metadata.get('Date') or metadata.get('date') or '1970-01-01'
    time_str = metadata.get('Time') or metadata.get('time') or '00:00:00'
    driver = (
        metadata.get('Driver name')
        or metadata.get('driver_name')
        or 'Unknown Driver'
    )
    league = metadata.get('League') or metadata.get('league') or 'kartsim'
    vehicle_class = (
        metadata.get('Class')
        or metadata.get('class')
        or metadata.get('class_name')
        or 'Unknown Class'
    )
    session_name = (
        metadata.get('Session')
        or metadata.get('session')
        or metadata.get('session_name')
        or 'Practice'
    )

    out: Dict[str, Any] = {
        'Track name': str(track),
        'Date': str(date_str),
        'Time': str(time_str),
        'Driver name': str(driver),
        'League': str(league),
        'Class': str(vehicle_class),
        'Session': str(session_name),
    }

    session_id = metadata.get('Session ID') or metadata.get('session_id')
    if session_id:
        out['Session ID'] = str(session_id)

    kart_num = metadata.get('Kart number') or metadata.get('kart_number')
    if kart_num:
        out['kart_number'] = str(kart_num)

    track_cfg = metadata.get('Track configuration') or metadata.get('track_configuration')
    if track_cfg:
        out['Track configuration'] = str(track_cfg)

    if stream_name:
        out['stream'] = stream_name

    return out


def encode_metadata_chunk(
    metadata: Dict[str, Any],
    channels: Optional[List[Dict[str, Any]]] = None,
    stream_name: Optional[str] = 'spikerbit',
) -> bytes:
    """Encode a 0x02 Metadata JSON chunk."""
    resolved_channels = channels if channels is not None else SPIKERBIT_CHANNELS
    norm_meta = normalize_session_metadata(metadata, stream_name=stream_name)
    doc = {
        'metadata': norm_meta,
        'channels': [
            {'name': str(ch['name']), 'scale': float(ch['scale'])}
            for ch in resolved_channels
        ],
    }
    payload = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    return frame_brt1_chunk(CHUNK_METADATA, payload)


def encode_baseline_chunk(values: Sequence[float]) -> bytes:
    """Encode a 0x03 Baseline (Keyframe) chunk of IEEE-754 float64 values."""
    payload = struct.pack(f'<{len(values)}d', *[float(v) for v in values])
    return frame_brt1_chunk(CHUNK_BASELINE, payload)


def encode_delta_batch_chunk(
    samples: Sequence[Sequence[float]],
    tracked_values: List[float],
    scales: Sequence[float],
) -> Tuple[bytes, bool]:
    """Encode one or more samples into a single 0x04 Delta chunk.

    Updates `tracked_values` in-place with quantized reconstructions.
    Returns `(chunk_bytes, overflow_or_nan_transition)` where the boolean
    is True if any sample required a Baseline keyframe instead.
    """
    num_channels = len(scales)
    int16_deltas: List[int] = []
    working_tracked = list(tracked_values)

    for sample in samples:
        for ch_idx in range(num_channels):
            cur = float(sample[ch_idx])
            prev = working_tracked[ch_idx]
            scale = float(scales[ch_idx])

            cur_nan = math.isnan(cur)
            prev_nan = math.isnan(prev)

            if cur_nan:
                int16_deltas.append(NAN_SENTINEL)
                working_tracked[ch_idx] = float('nan')
            elif prev_nan:
                return b'', True
            else:
                q_delta = int(round((cur - prev) / scale))
                if q_delta < -32767 or q_delta > 32767:
                    return b'', True
                int16_deltas.append(q_delta)
                working_tracked[ch_idx] = prev + q_delta * scale

    tracked_values[:] = working_tracked
    payload = struct.pack(f'<{len(int16_deltas)}h', *int16_deltas)
    return frame_brt1_chunk(CHUNK_DELTA, payload), False


@dataclass
class Brt1DecodedEvent:
    """Represents a decoded chunk event from a BRT1 byte stream."""

    chunk_type: int
    raw_chunk: bytes
    metadata: Optional[Dict[str, Any]] = None
    channels: Optional[List[Dict[str, Any]]] = None
    samples: Optional[List[Tuple[float, ...]]] = None
    clock_pong: Optional[Tuple[int, int]] = None


class Brt1StreamDecoder:
    """Streaming decoder for uncompressed BRT1 chunks (including micro:bit raw streams)."""

    def __init__(self, default_channels: Optional[List[Dict[str, Any]]] = None) -> None:
        self._buf = bytearray()
        self._channels: List[Dict[str, Any]] = (
            list(default_channels) if default_channels is not None else list(SPIKERBIT_CHANNELS)
        )
        self._scales: List[float] = [float(ch['scale']) for ch in self._channels]
        self._metadata: Dict[str, Any] = {}
        self._tracked: Optional[List[float]] = None

    @property
    def channels(self) -> List[Dict[str, Any]]:
        """Return currently active channel definitions."""
        return self._channels

    @property
    def metadata(self) -> Dict[str, Any]:
        """Return decoded metadata dictionary."""
        return self._metadata

    @property
    def has_baseline(self) -> bool:
        """Return True if a 0x03 Baseline keyframe has been received."""
        return self._tracked is not None

    def reset(self) -> None:
        """Clear internal byte buffer and baseline tracking."""
        self._buf.clear()
        self._tracked = None

    def feed(self, data: bytes) -> None:
        """Append incoming bytes to the internal decode buffer."""
        if data:
            self._buf.extend(data)

    def synchronize_to_magic_or_baseline(self) -> bool:
        """Discard leading garbage bytes until a valid 0x01 Magic or 0x03 Baseline header."""
        m_bytes = len(self._scales) * 8
        baseline_hdr = struct.pack('<HB', m_bytes, CHUNK_BASELINE)

        idx_magic = self._buf.find(MAGIC_CHUNK_BYTES)
        idx_base = self._buf.find(baseline_hdr)

        candidates = [idx for idx in (idx_magic, idx_base) if idx >= 0]
        if not candidates:
            if len(self._buf) > 6:
                del self._buf[:-6]
            return False

        best = min(candidates)
        if best > 0:
            del self._buf[:best]
        return True

    def pop_events(self) -> List[Brt1DecodedEvent]:
        """Parse and return all complete BRT1 chunks currently in the buffer."""
        events: List[Brt1DecodedEvent] = []

        while len(self._buf) >= 3:
            length, chunk_type = struct.unpack_from('<HB', self._buf, 0)
            total_len = 3 + length
            if len(self._buf) < total_len:
                break

            raw_chunk = bytes(self._buf[:total_len])
            payload = raw_chunk[3:]
            del self._buf[:total_len]

            if chunk_type == CHUNK_MAGIC:
                if payload != b'BRT1':
                    raise ValueError(f'Invalid BRT1 magic payload: {payload!r}')
                events.append(Brt1DecodedEvent(chunk_type=CHUNK_MAGIC, raw_chunk=raw_chunk))

            elif chunk_type == CHUNK_METADATA:
                doc = json.loads(payload.decode('utf-8'))
                self._metadata = doc.get('metadata', {})
                ch_list = doc.get('channels')
                if isinstance(ch_list, list) and ch_list:
                    self._channels = ch_list
                    self._scales = [float(ch['scale']) for ch in self._channels]
                    self._tracked = None
                events.append(
                    Brt1DecodedEvent(
                        chunk_type=CHUNK_METADATA,
                        raw_chunk=raw_chunk,
                        metadata=dict(self._metadata),
                        channels=list(self._channels),
                    )
                )

            elif chunk_type == CHUNK_BASELINE:
                num_ch = len(self._scales)
                expected_bytes = num_ch * 8
                if length != expected_bytes:
                    raise ValueError(
                        f'Baseline chunk length {length} != expected {expected_bytes}'
                    )
                vals = list(struct.unpack(f'<{num_ch}d', payload))
                self._tracked = list(vals)
                events.append(
                    Brt1DecodedEvent(
                        chunk_type=CHUNK_BASELINE,
                        raw_chunk=raw_chunk,
                        samples=[tuple(vals)],
                    )
                )

            elif chunk_type == CHUNK_DELTA:
                num_ch = len(self._scales)
                row_bytes = num_ch * 2
                if row_bytes == 0 or (length % row_bytes) != 0:
                    raise ValueError(
                        f'Delta chunk length {length} is not a multiple of {row_bytes}'
                    )
                if self._tracked is None:
                    # Ignore delta chunks received before the first baseline keyframe
                    continue

                num_rows = length // row_bytes
                deltas = struct.unpack(f'<{num_rows * num_ch}h', payload)
                decoded_rows: List[Tuple[float, ...]] = []

                for r in range(num_rows):
                    base_idx = r * num_ch
                    for c in range(num_ch):
                        q_val = deltas[base_idx + c]
                        if q_val == NAN_SENTINEL:
                            self._tracked[c] = float('nan')
                        else:
                            if math.isnan(self._tracked[c]):
                                self._tracked[c] = q_val * self._scales[c]
                            else:
                                self._tracked[c] += q_val * self._scales[c]
                    decoded_rows.append(tuple(self._tracked))

                events.append(
                    Brt1DecodedEvent(
                        chunk_type=CHUNK_DELTA,
                        raw_chunk=raw_chunk,
                        samples=decoded_rows,
                    )
                )

            elif chunk_type == CHUNK_CLOCK_PONG:
                if length != 16:
                    raise ValueError(f'Clock pong chunk length {length} != expected 16')
                host_t0_us, mcu_time_100us = struct.unpack('<qq', payload)
                events.append(
                    Brt1DecodedEvent(
                        chunk_type=CHUNK_CLOCK_PONG,
                        raw_chunk=raw_chunk,
                        clock_pong=(int(host_t0_us), int(mcu_time_100us)),
                    )
                )

            elif chunk_type == CHUNK_EOS:
                events.append(Brt1DecodedEvent(chunk_type=CHUNK_EOS, raw_chunk=raw_chunk))

            else:
                raise ValueError(f'Unknown BRT1 chunk type: 0x{chunk_type:02X}')

        return events
