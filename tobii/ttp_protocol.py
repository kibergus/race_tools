"""Tobii Eye Tracker 5 USB TTP/TLV wire protocol parser, builders, and clock sync.

Adapted under Apache License 2.0 from `njmill/tobii-linux` (`tools/app/tobii-ttp-mux.c`).
"""

from dataclasses import dataclass
import hashlib
import hmac
import math
import struct
from typing import List, Optional, Tuple
import numpy as np


TOBII_VID = 0x2104
TOBII_PID = 0x0313
TOBII_VENDOR_IFACE = 0
TOBII_EP_IN = 0x83
TOBII_EP_OUT = 0x05

TTP_HDR_SIZE = 24
ENVELOPE_SIZE = 8
MAX_FRAME_BYTES = 2 * 1024 * 1024

TTP_MAGIC_REQ = 0x51
TTP_MAGIC_RSP = 0x52
TTP_MAGIC_NOTIFY = 0x53

TTP_OP_HELLO = 0x03E8
TTP_OP_SUBSCRIBE = 0x04C4
TTP_OP_UNSUBSCRIBE = 0x04CE
TTP_OP_GET_DISPLAY_AREA = 0x0596
TTP_OP_SET_DISPLAY_AREA = 0x05A0
TTP_OP_QUERY_REALM = 0x0640
TTP_OP_OPEN_REALM = 0x076C
TTP_OP_REALM_RESPONSE = 0x0776

STREAM_GAZE = 0x0500
STREAM_IMAGE_SECONDARY = 0x0501
STREAM_IMAGE_COLLECTION = 0x0508
STREAM_IMAGE_PRIMARY = 0x050E
STREAM_SYNC = 0x1771

REALM_KEY = b'IS2LJC6GIRBBEK2K'

Q42_SCALE = 4398046511104.0
Q16_SCALE = 65536.0

_HELLO_PAYLOAD = bytes([
    0x00, 0x00, 0x17, 0x00, 0x00, 0x00, 0x28, 0x00, 0x00, 0x00, 0x09,
    0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x01, 0x00, 0x02,
    0x00, 0x01, 0x00, 0x03, 0x00, 0x01, 0x00, 0x04, 0x00, 0x01, 0x00, 0x05,
    0x00, 0x01, 0x00, 0x06, 0x00, 0x01, 0x00, 0x07, 0x00, 0x01, 0x00, 0x08,
])


@dataclass
class TtpFrame:
    """Decoded TTP frame extracted from USB bulk stream."""

    magic: int
    seq: int
    op: int
    payload: bytes


@dataclass
class GazeSample:
    """Primary hardware ocular & gaze values decoded from TTP stream `0x0500`."""

    timestamp_us: int = 0
    has_timestamp: bool = False
    frame_counter: Optional[int] = None

    gaze_valid: bool = False
    gaze_x: float = float('nan')
    gaze_y: float = float('nan')

    gaze_unfiltered_valid: bool = False
    gaze_unfiltered_x: float = float('nan')
    gaze_unfiltered_y: float = float('nan')

    gaze_l_valid: bool = False
    gaze_l_x: float = float('nan')
    gaze_l_y: float = float('nan')

    gaze_r_valid: bool = False
    gaze_r_x: float = float('nan')
    gaze_r_y: float = float('nan')

    validity_l: bool = False
    validity_r: bool = False
    eye_present_l: bool = False
    eye_present_r: bool = False
    binocular_flag: int = 0

    pupil_l_mm: float = float('nan')
    pupil_r_mm: float = float('nan')

    eye_l_x_mm: float = float('nan')
    eye_l_y_mm: float = float('nan')
    eye_l_z_mm: float = float('nan')

    eye_r_x_mm: float = float('nan')
    eye_r_y_mm: float = float('nan')
    eye_r_z_mm: float = float('nan')

    trackbox_l_x: float = float('nan')
    trackbox_l_y: float = float('nan')
    trackbox_l_z: float = float('nan')

    trackbox_r_x: float = float('nan')
    trackbox_r_y: float = float('nan')
    trackbox_r_z: float = float('nan')

    def to_brt1_row(self, session_elapsed_sec: float) -> List[float]:
        """Convert primary hardware channels into the 17-channel `tobii_gaze` BRT1 row."""
        nan = float('nan')
        gx = self.gaze_x if self.gaze_valid else nan
        gy = self.gaze_y if self.gaze_valid else nan
        ux = self.gaze_unfiltered_x if self.gaze_unfiltered_valid else nan
        uy = self.gaze_unfiltered_y if self.gaze_unfiltered_valid else nan
        lx = self.gaze_l_x if self.gaze_l_valid else nan
        ly = self.gaze_l_y if self.gaze_l_valid else nan
        rx = self.gaze_r_x if self.gaze_r_valid else nan
        ry = self.gaze_r_y if self.gaze_r_valid else nan
        l_eye_ok = self.validity_l or self.eye_present_l or self.gaze_l_valid
        r_eye_ok = self.validity_r or self.eye_present_r or self.gaze_r_valid
        pl = self.pupil_l_mm if (l_eye_ok and self.pupil_l_mm > 0.5) else nan
        pr = self.pupil_r_mm if (r_eye_ok and self.pupil_r_mm > 0.5) else nan
        l_pos_ok = l_eye_ok and abs(self.eye_l_z_mm) > 10.0
        r_pos_ok = r_eye_ok and abs(self.eye_r_z_mm) > 10.0
        elx = self.eye_l_x_mm if l_pos_ok else nan
        ely = self.eye_l_y_mm if l_pos_ok else nan
        elz = self.eye_l_z_mm if l_pos_ok else nan
        erx = self.eye_r_x_mm if r_pos_ok else nan
        ery = self.eye_r_y_mm if r_pos_ok else nan
        erz = self.eye_r_z_mm if r_pos_ok else nan
        return [
            float(session_elapsed_sec),
            gx, gy,
            ux, uy,
            lx, ly,
            rx, ry,
            pl, pr,
            elx, ely, elz,
            erx, ery, erz,
        ]


@dataclass
class NirImageFrame:
    """Decoded 8-bit grayscale in-band NIR frame from TTP stream `0x050e`."""

    object_id: int
    timestamp_us: int
    width: int
    height: int
    bpp: int
    gray: np.ndarray


@dataclass
class SyncFrame:
    """Decoded clock synchronization packet from TTP stream `0x1771`."""

    device_timestamp_us: int
    receive_timestamp_us: int


class TtpStreamParser:
    """Reassembles USB bulk transfers into complete TTP frames."""

    def __init__(self, max_buffer_bytes: int = MAX_FRAME_BYTES) -> None:
        self._buf = bytearray()
        self._max_buffer_bytes = max_buffer_bytes

    def reset(self) -> None:
        """Clear accumulated partial packet state."""
        self._buf.clear()

    def feed(self, data: bytes) -> None:
        """Feed a single USB bulk-IN read buffer into the TTP reassembler."""
        if not data:
            return

        view = memoryview(data)
        if len(self._buf) >= ENVELOPE_SIZE + TTP_HDR_SIZE:
            plen = struct.unpack_from('>I', self._buf, ENVELOPE_SIZE + 20)[0]
            frame_size = ENVELOPE_SIZE + TTP_HDR_SIZE + plen
            if (
                len(self._buf) < frame_size
                and len(view) >= ENVELOPE_SIZE
                and view[0] == 0x01
                and view[1] == 0x00
                and view[2] == 0x00
                and view[3] == 0x00
            ):
                # Multi-transfer continuation packet strips the 8-byte continuation envelope
                view = view[ENVELOPE_SIZE:]

        if len(self._buf) + len(view) > self._max_buffer_bytes:
            self._buf.clear()
            raise BufferError('TTP accumulator overflow')

        self._buf.extend(view)

    def pop_frames(self) -> List[TtpFrame]:
        """Extract all complete TTP frames currently buffered."""
        frames: List[TtpFrame] = []
        while len(self._buf) >= ENVELOPE_SIZE:
            if self._buf[0] != 0x01:
                # Resynchronize to next potential 0x01 envelope header
                idx = self._buf.find(b'\x01\x00\x00\x00', 1)
                if idx < 0:
                    self._buf.clear()
                    break
                del self._buf[:idx]
                if len(self._buf) < ENVELOPE_SIZE:
                    break

            if len(self._buf) < ENVELOPE_SIZE + TTP_HDR_SIZE:
                break

            plen = struct.unpack_from('>I', self._buf, ENVELOPE_SIZE + 20)[0]
            frame_size = ENVELOPE_SIZE + TTP_HDR_SIZE + plen
            if frame_size > self._max_buffer_bytes:
                del self._buf[:1]
                continue
            if len(self._buf) < frame_size:
                break

            magic, seq, _r0, op, _r1, _plen = struct.unpack_from(
                '>6I', self._buf, ENVELOPE_SIZE
            )
            payload_start = ENVELOPE_SIZE + TTP_HDR_SIZE
            payload = bytes(self._buf[payload_start:frame_size])
            del self._buf[:frame_size]
            frames.append(TtpFrame(magic=magic, seq=seq, op=op, payload=payload))

        return frames


def build_ttp_frame(magic: int, seq: int, op: int, payload: bytes = b'') -> bytes:
    """Build a 24-byte TTP header + payload."""
    hdr = struct.pack('>6I', magic, seq, 0, op, 0, len(payload))
    return hdr + payload


def wrap_out_envelope(ttp_bytes: bytes) -> bytes:
    """Wrap an outbound host->device TTP frame in an 8-byte USB bulk envelope."""
    return struct.pack('<II', 0, len(ttp_bytes)) + ttp_bytes


def tlv_u32(value: int) -> bytes:
    """Encode a type-2 uint32 TLV field."""
    return struct.pack('>BII', 0x02, 4, value & 0xFFFFFFFF)


def tlv_q42(value: float) -> bytes:
    """Encode a type-4 Q22.42 fixed-point TLV field."""
    scaled = float(value) * Q42_SCALE
    rounded = int(scaled + 0.5) if scaled >= 0.0 else int(scaled - 0.5)
    return struct.pack('>BIq', 0x04, 8, rounded)


def tlv_tag(tag_value: int) -> bytes:
    """Encode a type-5 32-bit tag TLV field."""
    return struct.pack('>BII', 0x05, 4, tag_value & 0xFFFFFFFF)


def tlv_point3d(x: float, y: float, z: float) -> bytes:
    """Encode an XDS 3D Q22.42 point (`0x031f41`)."""
    return tlv_tag(0x031F41) + tlv_q42(x) + tlv_q42(y) + tlv_q42(z)


def build_hello_packet(seq: int) -> bytes:
    """Build outbound TTP HELLO (`0x03e8`) USB packet."""
    return wrap_out_envelope(build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_HELLO, _HELLO_PAYLOAD))


def build_query_realm_packet(seq: int) -> bytes:
    """Build outbound TTP QUERY_REALM (`0x0640`) USB packet."""
    return wrap_out_envelope(build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_QUERY_REALM, b'\x00\x00'))


def build_open_realm_packet(seq: int, realm_type: int) -> bytes:
    """Build outbound TTP OPEN_REALM (`0x076c`) USB packet."""
    payload = b'\x00\x00' + tlv_u32(realm_type) + b'\x00'
    return wrap_out_envelope(build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_OPEN_REALM, payload))


def compute_realm_hmac(challenge: bytes, key: bytes = REALM_KEY) -> bytes:
    """Compute the 16-byte HMAC-MD5 realm response digest."""
    return hmac.new(key, challenge, hashlib.md5).digest()


def build_realm_response_packet(
    seq: int,
    realm_id: int,
    field_210: int,
    digest16: bytes,
) -> bytes:
    """Build outbound TTP REALM_RESPONSE (`0x0776`) USB packet."""
    if len(digest16) < 16:
        raise ValueError('Realm digest must be at least 16 bytes')
    payload = b'\x00\x00' + tlv_u32(realm_id) + tlv_u32(field_210) + digest16[:16]
    return wrap_out_envelope(
        build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_REALM_RESPONSE, payload)
    )


DisplayAreaCorners = Tuple[
    Tuple[float, float, float],
    Tuple[float, float, float],
    Tuple[float, float, float],
]


def build_get_display_area_packet(seq: int) -> bytes:
    """Build outbound TTP GET_DISPLAY_AREA (`0x0596`) USB packet."""
    return wrap_out_envelope(build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_GET_DISPLAY_AREA, b''))


def build_set_display_area_packet(
    seq: int,
    tl_xyz: Tuple[float, float, float],
    tr_xyz: Tuple[float, float, float],
    bl_xyz: Tuple[float, float, float],
) -> bytes:
    """Build outbound TTP SET_DISPLAY_AREA (`0x05a0`) USB packet."""
    payload = (
        b'\x00\x00'
        + tlv_point3d(*tl_xyz)
        + tlv_point3d(*tr_xyz)
        + tlv_point3d(*bl_xyz)
        + tlv_tag(0x010100)
        + tlv_u32(0x3039)
    )
    return wrap_out_envelope(
        build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_SET_DISPLAY_AREA, payload)
    )


def build_subscribe_packet(seq: int, stream_id: int) -> bytes:
    """Build outbound TTP SUBSCRIBE (`0x04c4`) USB packet for `stream_id`."""
    payload = bytearray([
        0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00,
        0x00, 0x17, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00,
    ])
    payload[9] = (stream_id >> 8) & 0xFF
    payload[10] = stream_id & 0xFF
    return wrap_out_envelope(
        build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_SUBSCRIBE, bytes(payload))
    )


def build_unsubscribe_packet(seq: int, stream_id: int) -> bytes:
    """Build outbound TTP UNSUBSCRIBE (`0x04ce`) USB packet for `stream_id`."""
    payload = bytearray([
        0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00,
        0x00, 0x17, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00,
    ])
    payload[9] = (stream_id >> 8) & 0xFF
    payload[10] = stream_id & 0xFF
    return wrap_out_envelope(
        build_ttp_frame(TTP_MAGIC_REQ, seq, TTP_OP_UNSUBSCRIBE, bytes(payload))
    )


class _TlvReader:
    """Cursor over big-endian TLV fields inside a TTP payload."""

    def __init__(self, buf: bytes, pos: int = 0) -> None:
        self.buf = buf
        self.len = len(buf)
        self.pos = pos

    def read_header(self) -> Optional[Tuple[int, int]]:
        if self.len - self.pos < 5:
            return None
        t_code, size = struct.unpack_from('>BI', self.buf, self.pos)
        self.pos += 5
        if self.len - self.pos < size:
            return None
        return t_code, size

    def read_field_raw(self) -> Optional[Tuple[int, bytes]]:
        hdr = self.read_header()
        if hdr is None:
            return None
        t_code, size = hdr
        val = self.buf[self.pos:self.pos + size]
        self.pos += size
        return t_code, val

    def read_tag(self) -> Optional[int]:
        hdr = self.read_header()
        if hdr is None or hdr[0] != 5 or hdr[1] != 4:
            return None
        val = struct.unpack_from('>I', self.buf, self.pos)[0]
        self.pos += 4
        return val

    def read_u32(self) -> Optional[int]:
        hdr = self.read_header()
        if hdr is None or hdr[0] != 2 or hdr[1] != 4:
            return None
        val = struct.unpack_from('>I', self.buf, self.pos)[0]
        self.pos += 4
        return val

    def read_s64(self) -> Optional[int]:
        hdr = self.read_header()
        if hdr is None or hdr[0] != 6 or hdr[1] != 8:
            return None
        val = struct.unpack_from('>q', self.buf, self.pos)[0]
        self.pos += 8
        return val

    def read_fixed16x16(self) -> Optional[float]:
        hdr = self.read_header()
        if hdr is None or hdr[0] != 3 or hdr[1] != 4:
            return None
        raw = struct.unpack_from('>i', self.buf, self.pos)[0]
        self.pos += 4
        return float(raw) / Q16_SCALE

    def read_q42(self) -> Optional[float]:
        hdr = self.read_header()
        if hdr is None or hdr[0] != 4 or hdr[1] != 8:
            return None
        raw = struct.unpack_from('>q', self.buf, self.pos)[0]
        self.pos += 8
        return float(raw) / Q42_SCALE

    def read_point2d(self) -> Optional[Tuple[float, float]]:
        tag = self.read_tag()
        if tag != 0x021F40:
            return None
        x = self.read_q42()
        y = self.read_q42()
        if x is None or y is None:
            return None
        return x, y

    def read_point3d(self) -> Optional[Tuple[float, float, float]]:
        tag = self.read_tag()
        if tag != 0x031F41:
            return None
        x = self.read_q42()
        y = self.read_q42()
        z = self.read_q42()
        if x is None or y is None or z is None:
            return None
        return x, y, z


def parse_first_u32(payload: bytes) -> int:
    """Read the first uint32 TLV in a response payload (used for `QUERY_REALM`)."""
    if len(payload) < 2:
        return 0
    reader = _TlvReader(payload, pos=2)
    val = reader.read_u32()
    return val if val is not None else 0


def parse_open_realm_challenge(payload: bytes) -> Optional[Tuple[int, int, bytes]]:
    """Parse `(realm_id, field_210, challenge_bytes)` from `OPEN_REALM` response."""
    if len(payload) < 2:
        return None
    reader = _TlvReader(payload, pos=2)
    realm_id = reader.read_u32()
    field_210 = reader.read_u32()
    if realm_id is None or field_210 is None or reader.pos >= reader.len:
        return None
    challenge = payload[reader.pos:]
    if not challenge:
        return None
    return realm_id, field_210, challenge


def parse_display_area_payload(payload: bytes) -> Optional[DisplayAreaCorners]:
    """Decode `(tl_xyz, tr_xyz, bl_xyz)` in mm from a `GET_DISPLAY_AREA` (`0x0596`) response."""
    if len(payload) < 2 or payload[0] != 0 or payload[1] != 0:
        return None
    reader = _TlvReader(payload, pos=2)
    points: List[Tuple[float, float, float]] = []
    while reader.pos < reader.len:
        saved_pos = reader.pos
        pt = reader.read_point3d()
        if pt is not None:
            points.append(pt)
            if len(points) >= 3:
                break
            continue
        reader.pos = saved_pos
        if reader.read_field_raw() is None:
            break

    if len(points) < 3:
        return None

    tl, tr, bl = points[0], points[1], points[2]
    coords = (*tl, *tr, *bl)
    if any(math.isnan(v) or math.isinf(v) for v in coords):
        return None

    w_vec = np.array(tr, dtype=np.float64) - np.array(tl, dtype=np.float64)
    h_vec = np.array(bl, dtype=np.float64) - np.array(tl, dtype=np.float64)
    area_mm2 = float(np.linalg.norm(np.cross(w_vec, h_vec)))
    if area_mm2 < 100.0:
        return None

    return tl, tr, bl


_POINT3D_SKIP_COLS = frozenset({
    0x02, 0x03, 0x04, 0x08, 0x09, 0x0A, 0x17, 0x18, 0x22, 0x24, 0x25, 0x27,
})
_POINT2D_SKIP_COLS = frozenset({0x05, 0x0B, 0x19, 0x1A, 0x1C, 0x20})
_FIXED16_SKIP_COLS = frozenset({0x06, 0x0C, 0x29, 0x2B})
_U32_SKIP_COLS = frozenset({
    0x07, 0x0D, 0x0E, 0x11, 0x14, 0x15, 0x16, 0x1B,
    0x1D, 0x1E, 0x1F, 0x21, 0x23, 0x26, 0x28, 0x2A, 0x2C,
})


def _skip_known_column(reader: _TlvReader, col: int) -> bool:
    if col == 0x01:
        return reader.read_s64() is not None
    if col in _POINT3D_SKIP_COLS:
        return reader.read_point3d() is not None
    if col in _POINT2D_SKIP_COLS:
        return reader.read_point2d() is not None
    if col in _FIXED16_SKIP_COLS:
        return reader.read_fixed16x16() is not None
    if col in _U32_SKIP_COLS:
        return reader.read_u32() is not None
    return False


def decode_gaze_sample(payload: bytes) -> Optional[GazeSample]:
    """Decode a TTP `0x0500` (`STREAM_GAZE`) XDS TLV row into a `GazeSample`."""
    if len(payload) < 2:
        return None
    reader = _TlvReader(payload, pos=2)
    row_tag = reader.read_tag()
    if row_tag is None or (row_tag & 0xFFFF) != 0x0BB8:
        return None
    n_cols = (row_tag >> 16) & 0xFFF

    sample = GazeSample()
    has_gaze_2d = False
    has_unfiltered = False
    has_l_2d = False
    has_r_2d = False
    raw_gaze_valid: Optional[int] = None
    raw_unfiltered_valid: Optional[int] = None
    raw_l_2d_valid: Optional[int] = None
    raw_r_2d_valid: Optional[int] = None
    raw_eye_l: Optional[Tuple[float, float, float]] = None
    raw_eye_r: Optional[Tuple[float, float, float]] = None

    for _ in range(n_cols):
        if reader.pos >= reader.len:
            break
        col_tag = reader.read_tag()
        if col_tag != 0x020BB9:
            break
        col = reader.read_u32()
        if col is None:
            break

        if col == 0x01:
            ts = reader.read_s64()
            if ts is None:
                break
            sample.timestamp_us = ts
            sample.has_timestamp = True
        elif col == 0x02:
            pt3 = reader.read_point3d()
            if pt3 is None:
                break
            sample.eye_l_x_mm, sample.eye_l_y_mm, sample.eye_l_z_mm = pt3
        elif col == 0x03:
            pt3 = reader.read_point3d()
            if pt3 is None:
                break
            sample.trackbox_l_x, sample.trackbox_l_y, sample.trackbox_l_z = pt3
        elif col == 0x05:
            pt2 = reader.read_point2d()
            if pt2 is None:
                break
            sample.gaze_l_x, sample.gaze_l_y = pt2
            has_l_2d = True
        elif col == 0x06:
            pup = reader.read_fixed16x16()
            if pup is None:
                break
            sample.pupil_l_mm = pup
        elif col == 0x07:
            val = reader.read_u32()
            if val is None:
                break
            sample.validity_l = (val == 1)
        elif col == 0x08:
            pt3 = reader.read_point3d()
            if pt3 is None:
                break
            sample.eye_r_x_mm, sample.eye_r_y_mm, sample.eye_r_z_mm = pt3
        elif col == 0x09:
            pt3 = reader.read_point3d()
            if pt3 is None:
                break
            sample.trackbox_r_x, sample.trackbox_r_y, sample.trackbox_r_z = pt3
        elif col == 0x0B:
            pt2 = reader.read_point2d()
            if pt2 is None:
                break
            sample.gaze_r_x, sample.gaze_r_y = pt2
            has_r_2d = True
        elif col == 0x0C:
            pup = reader.read_fixed16x16()
            if pup is None:
                break
            sample.pupil_r_mm = pup
        elif col == 0x0D:
            val = reader.read_u32()
            if val is None:
                break
            sample.validity_r = (val == 1)
        elif col == 0x14:
            fc = reader.read_u32()
            if fc is None:
                break
            sample.frame_counter = fc
        elif col == 0x15:
            ep = reader.read_u32()
            if ep is None:
                break
            sample.eye_present_l = (ep == 1)
        elif col == 0x16:
            ep = reader.read_u32()
            if ep is None:
                break
            sample.eye_present_r = (ep == 1)
        elif col == 0x17:
            pt3 = reader.read_point3d()
            if pt3 is None:
                break
            raw_eye_l = pt3
        elif col == 0x18:
            pt3 = reader.read_point3d()
            if pt3 is None:
                break
            raw_eye_r = pt3
        elif col == 0x1B:
            bf = reader.read_u32()
            if bf is None:
                break
            sample.binocular_flag = bf
        elif col == 0x1C:
            pt2 = reader.read_point2d()
            if pt2 is None:
                break
            sample.gaze_x, sample.gaze_y = pt2
            has_gaze_2d = True
        elif col == 0x1D:
            gv = reader.read_u32()
            if gv is None:
                break
            raw_gaze_valid = gv
        elif col == 0x1E:
            glv = reader.read_u32()
            if glv is None:
                break
            raw_l_2d_valid = glv
        elif col == 0x1F:
            grv = reader.read_u32()
            if grv is None:
                break
            raw_r_2d_valid = grv
        elif col == 0x20:
            pt2 = reader.read_point2d()
            if pt2 is None:
                break
            sample.gaze_unfiltered_x, sample.gaze_unfiltered_y = pt2
            has_unfiltered = True
        elif col == 0x21:
            guv = reader.read_u32()
            if guv is None:
                break
            raw_unfiltered_valid = guv
        else:
            if not _skip_known_column(reader, col):
                break

    if raw_eye_l is not None and (math.isnan(sample.eye_l_z_mm) or abs(sample.eye_l_z_mm) < 10.0):
        sample.eye_l_x_mm, sample.eye_l_y_mm, sample.eye_l_z_mm = raw_eye_l
    if raw_eye_r is not None and (math.isnan(sample.eye_r_z_mm) or abs(sample.eye_r_z_mm) < 10.0):
        sample.eye_r_x_mm, sample.eye_r_y_mm, sample.eye_r_z_mm = raw_eye_r

    sample.gaze_valid = has_gaze_2d and (raw_gaze_valid == 1)
    sample.gaze_unfiltered_valid = has_unfiltered and (raw_unfiltered_valid == 1)
    if raw_l_2d_valid is not None:
        sample.gaze_l_valid = has_l_2d and (raw_l_2d_valid == 1)
    else:
        sample.gaze_l_valid = has_l_2d and (sample.validity_l or sample.eye_present_l)
    if raw_r_2d_valid is not None:
        sample.gaze_r_valid = has_r_2d and (raw_r_2d_valid == 1)
    else:
        sample.gaze_r_valid = has_r_2d and (sample.validity_r or sample.eye_present_r)

    if sample.eye_present_l or sample.gaze_l_valid:
        sample.validity_l = True
    if sample.eye_present_r or sample.gaze_r_valid:
        sample.validity_r = True

    return sample


def decode_image_payload(object_id: int, payload: bytes) -> Optional[NirImageFrame]:
    """Decode an 8-bit grayscale NIR camera frame from TTP stream `0x050e` (or `0x0501`)."""
    if len(payload) < 2:
        return None
    reader = _TlvReader(payload, pos=2)

    timestamp_us = 0
    bpp = 8
    width = 0
    height = 0
    stride = 0
    image_blob: Optional[bytes] = None

    while reader.len - reader.pos >= 5:
        m_field = reader.read_field_raw()
        if m_field is None or m_field[0] != 0x05:
            return None
        id_field = reader.read_field_raw()
        if id_field is None:
            return None
        if id_field[0] == 0x05:
            id_field = reader.read_field_raw()
            if id_field is None:
                return None
        if id_field[0] != 0x02 or len(id_field[1]) != 4:
            return None
        val_field = reader.read_field_raw()
        if val_field is None:
            return None

        col_id = struct.unpack_from('>I', id_field[1], 0)[0]
        v_bytes = val_field[1]
        if col_id == 0x0001 and len(v_bytes) == 8:
            timestamp_us = struct.unpack_from('>Q', v_bytes, 0)[0]
        elif col_id == 0x0002 and len(v_bytes) == 4:
            bpp = struct.unpack_from('>I', v_bytes, 0)[0]
        elif col_id == 0x0003 and len(v_bytes) == 4:
            width = struct.unpack_from('>I', v_bytes, 0)[0]
        elif object_id == STREAM_IMAGE_PRIMARY and col_id == 0x0004 and len(v_bytes) == 4:
            stride = struct.unpack_from('>I', v_bytes, 0)[0]
        elif object_id == STREAM_IMAGE_PRIMARY and col_id == 0x0005 and len(v_bytes) == 4:
            height = struct.unpack_from('>I', v_bytes, 0)[0]
        elif object_id != STREAM_IMAGE_PRIMARY and col_id == 0x0004 and len(v_bytes) == 4:
            height = struct.unpack_from('>I', v_bytes, 0)[0]
        elif object_id == STREAM_IMAGE_PRIMARY and col_id == 0x0006:
            image_blob = v_bytes
        elif object_id != STREAM_IMAGE_PRIMARY and col_id == 0x0005:
            image_blob = v_bytes

    if stride == 0:
        stride = width
    if image_blob is None or len(image_blob) < 4 or width <= 0 or height <= 0 or width > stride:
        return None

    image_len = struct.unpack_from('>I', image_blob, 0)[0]
    required_len = stride * height
    if image_len > len(image_blob) - 4 or image_len < required_len:
        return None

    raw_pixels = np.frombuffer(image_blob, dtype=np.uint8, count=required_len, offset=4)
    rows_2d = raw_pixels.reshape((height, stride))
    gray = np.ascontiguousarray(rows_2d[:, :width])
    return NirImageFrame(
        object_id=object_id,
        timestamp_us=int(timestamp_us),
        width=width,
        height=height,
        bpp=bpp,
        gray=gray,
    )


def decode_sync_payload(payload: bytes) -> Optional[SyncFrame]:
    """Decode a TTP `0x1771` (`STREAM_SYNC`) clock packet."""
    if len(payload) < 2:
        return None
    reader = _TlvReader(payload, pos=2)
    dev_ts = 0
    rx_ts = 0
    while reader.len - reader.pos >= 5:
        m_field = reader.read_field_raw()
        if m_field is None or m_field[0] != 0x05:
            return None
        id_field = reader.read_field_raw()
        if id_field is None:
            return None
        if id_field[0] == 0x05:
            id_field = reader.read_field_raw()
            if id_field is None:
                return None
        if id_field[0] != 0x02 or len(id_field[1]) != 4:
            return None
        val_field = reader.read_field_raw()
        if val_field is None:
            return None
        col_id = struct.unpack_from('>I', id_field[1], 0)[0]
        if col_id == 1 and len(val_field[1]) == 8:
            dev_ts = struct.unpack_from('>Q', val_field[1], 0)[0]
        elif col_id == 2 and len(val_field[1]) == 8:
            rx_ts = struct.unpack_from('>Q', val_field[1], 0)[0]

    if dev_ts == 0 and rx_ts == 0:
        return None
    return SyncFrame(device_timestamp_us=int(dev_ts), receive_timestamp_us=int(rx_ts))


class TobiiClockSynchronizer:
    """Disciplines EyeChip `timestamp_us` to host Unix clock (`time.time()`).

    Uses minimum-latency upper-bound tracking so USB scheduling jitter does not
    advance device timestamps into the future, while smoothly adapting to slow
    crystal frequency offset between the tracker and the PC.
    """

    def __init__(self, alpha: float = 0.02) -> None:
        self._alpha = float(alpha)
        self._offset_us: Optional[float] = None
        self._samples = 0

    @property
    def is_locked(self) -> bool:
        """Return True once at least one timestamp pair has anchored the offset."""
        return self._offset_us is not None

    @property
    def offset_us(self) -> float:
        """Estimated `host_unix_us - device_timestamp_us` offset in microseconds."""
        return self._offset_us if self._offset_us is not None else 0.0

    def reset(self) -> None:
        """Reset clock offset estimate."""
        self._offset_us = None
        self._samples = 0

    def observe(self, device_ts_us: int, host_unix_sec: float) -> None:
        """Observe a packet with hardware `device_ts_us` arriving at `host_unix_sec`."""
        if device_ts_us <= 0:
            return
        candidate_offset = host_unix_sec * 1_000_000.0 - float(device_ts_us)
        self._samples += 1
        if self._offset_us is None:
            self._offset_us = candidate_offset
            return

        diff = candidate_offset - self._offset_us
        if abs(diff) > 2_000_000.0:
            # Large jump (> 2s, e.g. tracker reset or PC wake): re-anchor immediately
            self._offset_us = candidate_offset
        elif diff < 0.0:
            # Earlier-than-expected arrival means lower USB queue delay: snap downward faster
            self._offset_us += 0.25 * diff
        else:
            # Normal arrival jitter or slow clock drift: gently track upward
            self._offset_us += self._alpha * diff

    def device_ts_to_unix_sec(self, device_ts_us: int, fallback_unix_sec: float) -> float:
        """Convert hardware `device_ts_us` to host Unix seconds."""
        if device_ts_us <= 0 or self._offset_us is None:
            return fallback_unix_sec
        return (float(device_ts_us) + self._offset_us) / 1_000_000.0

    def device_ts_to_session_sec(
        self,
        device_ts_us: int,
        session_start_unix_ms: int,
        fallback_unix_sec: float,
    ) -> float:
        """Convert hardware `device_ts_us` to elapsed seconds since `session_start_unix_ms`."""
        unix_sec = self.device_ts_to_unix_sec(device_ts_us, fallback_unix_sec)
        return unix_sec - (float(session_start_unix_ms) / 1000.0)
