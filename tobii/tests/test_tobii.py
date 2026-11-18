"""Unit and integration tests for `tobii` TTP protocol, BRT1 streams, MediaPipe, and GUI gating."""

import hashlib
import hmac
import json
import math
import os
import socket
import struct
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import patch
import urllib.request

import brotli
import numpy as np
import pytest

from tobii.brt1_protocol import (
    Brt1StreamDecoder,
    CHUNK_BASELINE,
    CHUNK_DELTA,
    CHUNK_EOS,
    CHUNK_MAGIC,
    CHUNK_METADATA,
    STREAM_TOBII_FACE,
    STREAM_TOBII_GAZE,
    TOBII_FACE_CHANNELS,
    TOBII_GAZE_CHANNELS,
)
from tobii.calibration import (
    DEFAULT_CALIBRATION_TARGETS,
    DisplayAreaProfile,
    WIDE_COLLECTION_PLANE,
    aggregate_target_samples,
    compute_screen_dimensions_mm,
    delete_calibration_profile,
    load_calibration_profile,
    project_ray_onto_display_area,
    save_calibration_profile,
    solve_display_area_3d,
)
from tobii.device_client import TobiiDirectClient
from tobii.mediapipe_worker import (
    FaceSample,
    MediaPipeFaceWorker,
    decompose_pose_matrix_forward,
    default_model_path,
    verify_sha256,
)
from tobii.ttp_protocol import (
    DisplayAreaCorners,
    GazeSample,
    Q16_SCALE,
    REALM_KEY,
    STREAM_GAZE,
    STREAM_IMAGE_PRIMARY,
    STREAM_SYNC,
    TTP_HDR_SIZE,
    TTP_MAGIC_NOTIFY,
    TTP_MAGIC_REQ,
    TTP_MAGIC_RSP,
    TTP_OP_GET_DISPLAY_AREA,
    TTP_OP_HELLO,
    TTP_OP_OPEN_REALM,
    TTP_OP_QUERY_REALM,
    TTP_OP_REALM_RESPONSE,
    TTP_OP_SET_DISPLAY_AREA,
    TTP_OP_SUBSCRIBE,
    TTP_OP_UNSUBSCRIBE,
    TobiiClockSynchronizer,
    TtpStreamParser,
    build_set_display_area_packet,
    build_ttp_frame,
    compute_realm_hmac,
    decode_gaze_sample,
    decode_image_payload,
    decode_sync_payload,
    parse_display_area_payload,
    parse_open_realm_challenge,
    tlv_point3d,
    tlv_q42,
    tlv_tag,
    tlv_u32,
)
from tobii.uploader import (
    BrotliBrt1Segment,
    TobiiAuxSessionManager,
    UploadSinkConfig,
    extract_upload_targets,
    parse_sink_spec,
)
from tobii.usb_transport import (
    BaseUsbTransport,
    WINDOWS_TOBII_SERVICES,
    UsbTransportError,
    get_windows_tobii_services_running,
)


def _wrap_in_envelope(ttp_bytes: bytes) -> bytes:
    return b'\x01\x00\x00\x00' + struct.pack('<I', len(ttp_bytes)) + ttp_bytes


def _tlv_s64(value: int) -> bytes:
    return struct.pack('>BIq', 0x06, 8, int(value))


def _tlv_fixed16x16(value: float) -> bytes:
    scaled = int(round(float(value) * Q16_SCALE))
    return struct.pack('>BIi', 0x03, 4, scaled)


def _tlv_point2d(x: float, y: float) -> bytes:
    return tlv_tag(0x021F40) + tlv_q42(x) + tlv_q42(y)


def _encode_gaze_sample_payload(sample: GazeSample) -> bytes:
    cols: List[Tuple[int, bytes]] = []
    cols.append((0x01, _tlv_s64(sample.timestamp_us)))
    if not math.isnan(sample.eye_l_x_mm):
        cols.append((0x02, tlv_point3d(sample.eye_l_x_mm, sample.eye_l_y_mm, sample.eye_l_z_mm)))
    if not math.isnan(sample.gaze_l_x):
        cols.append((0x05, _tlv_point2d(sample.gaze_l_x, sample.gaze_l_y)))
    if not math.isnan(sample.pupil_l_mm):
        cols.append((0x06, _tlv_fixed16x16(sample.pupil_l_mm)))
    cols.append((0x07, tlv_u32(1 if sample.validity_l else 0)))

    if not math.isnan(sample.eye_r_x_mm):
        cols.append((0x08, tlv_point3d(sample.eye_r_x_mm, sample.eye_r_y_mm, sample.eye_r_z_mm)))
    if not math.isnan(sample.gaze_r_x):
        cols.append((0x0B, _tlv_point2d(sample.gaze_r_x, sample.gaze_r_y)))
    if not math.isnan(sample.pupil_r_mm):
        cols.append((0x0C, _tlv_fixed16x16(sample.pupil_r_mm)))
    cols.append((0x0D, tlv_u32(1 if sample.validity_r else 0)))

    if sample.frame_counter is not None:
        cols.append((0x14, tlv_u32(sample.frame_counter)))
    cols.append((0x15, tlv_u32(1 if sample.eye_present_l else 0)))
    cols.append((0x16, tlv_u32(1 if sample.eye_present_r else 0)))

    gx = 0.0 if math.isnan(sample.gaze_x) else sample.gaze_x
    gy = 0.0 if math.isnan(sample.gaze_y) else sample.gaze_y
    cols.append((0x1C, _tlv_point2d(gx, gy)))
    cols.append((0x1D, tlv_u32(1 if sample.gaze_valid else 0)))
    cols.append((0x1E, tlv_u32(1 if sample.gaze_l_valid else 0)))
    cols.append((0x1F, tlv_u32(1 if sample.gaze_r_valid else 0)))

    ux = 0.0 if math.isnan(sample.gaze_unfiltered_x) else sample.gaze_unfiltered_x
    uy = 0.0 if math.isnan(sample.gaze_unfiltered_y) else sample.gaze_unfiltered_y
    cols.append((0x20, _tlv_point2d(ux, uy)))
    cols.append((0x21, tlv_u32(1 if sample.gaze_unfiltered_valid else 0)))

    n_cols = len(cols)
    row_tag = ((n_cols & 0xFFF) << 16) | 0x0BB8
    parts = [b'\x00\x00', tlv_tag(row_tag)]
    for col_id, encoded_val in cols:
        parts.append(tlv_tag(0x020BB9))
        parts.append(tlv_u32(col_id))
        parts.append(encoded_val)
    return b''.join(parts)


def _encode_image_payload(timestamp_us: int, gray: np.ndarray) -> bytes:
    height, width = gray.shape
    pix_bytes = np.ascontiguousarray(gray).tobytes()
    blob_inner = struct.pack('>I', len(pix_bytes)) + pix_bytes

    def _col(col_id: int, val_type: int, val_bytes: bytes) -> bytes:
        return (
            tlv_tag(0x020BB9)
            + tlv_u32(col_id)
            + struct.pack('>BI', val_type, len(val_bytes))
            + val_bytes
        )

    return b''.join([
        b'\x00\x00',
        _col(0x0001, 0x06, struct.pack('>Q', int(timestamp_us))),
        _col(0x0002, 0x02, struct.pack('>I', 8)),
        _col(0x0003, 0x02, struct.pack('>I', width)),
        _col(0x0004, 0x02, struct.pack('>I', width)),
        _col(0x0005, 0x02, struct.pack('>I', height)),
        _col(0x0006, 0x07, blob_inner),
    ])


class _FakeTtpUsbTransport(BaseUsbTransport):
    """Test-only mock USB transport implementing the TTP wire handshake & stream notifications."""

    def __init__(
        self,
        initial_display_area: DisplayAreaCorners = (
            (-300.0, 340.0, 0.0),
            (300.0, 340.0, 0.0),
            (-300.0, 0.0, 0.0),
        ),
        max_width_mm: Optional[float] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._rx_buf = bytearray()
        self._subscribed: Set[int] = set()
        self._open = False
        self._seq = 100
        self._start_mono = 0.0
        self._last_gaze = 0.0
        self._last_img = 0.0
        self._challenge = b'TEST_CHALLENGE16'
        self._max_width_mm = max_width_mm
        self.display_area: DisplayAreaCorners = initial_display_area
        self.set_display_area_history: List[DisplayAreaCorners] = []

    def open(self) -> None:
        with self._lock:
            self._open = True
            self._rx_buf.clear()
            self._subscribed.clear()
            now = time.monotonic()
            self._start_mono = now
            self._last_gaze = now
            self._last_img = now

    def close(self) -> None:
        with self._lock:
            self._open = False
            self._subscribed.clear()
            self._rx_buf.clear()

    def control_transfer(
        self,
        bm_request_type: int,
        b_request: int,
        w_value: int,
        w_index: int,
        data: bytes = b'',
        timeout_ms: int = 1000,
    ) -> int:
        if not self._open:
            raise UsbTransportError('Closed')
        return len(data)

    def bulk_write(self, ep_out: int, data: bytes, timeout_ms: int = 1000) -> int:
        magic, seq, _r0, op, _r1, plen = struct.unpack_from('>6I', data, 8)
        payload = data[8 + TTP_HDR_SIZE:8 + TTP_HDR_SIZE + plen]
        if magic != TTP_MAGIC_REQ:
            return len(data)

        rsp_payload = b'\x00\x00'
        if op == TTP_OP_HELLO:
            rsp_payload = b'\x00\x00'
        elif op == TTP_OP_QUERY_REALM:
            rsp_payload = b'\x00\x00' + tlv_u32(1)
        elif op == TTP_OP_OPEN_REALM:
            rsp_payload = b'\x00\x00' + tlv_u32(7) + tlv_u32(42) + self._challenge
        elif op == TTP_OP_REALM_RESPONSE:
            if payload[-16:] != compute_realm_hmac(self._challenge):
                raise UsbTransportError('Bad realm HMAC')
        elif op == TTP_OP_GET_DISPLAY_AREA:
            with self._lock:
                tl, tr, bl = self.display_area
            rsp_payload = b'\x00\x00' + tlv_point3d(*tl) + tlv_point3d(*tr) + tlv_point3d(*bl)
        elif op == TTP_OP_SET_DISPLAY_AREA:
            parsed_da = parse_display_area_payload(payload)
            if parsed_da is not None:
                w_mm = float(
                    np.linalg.norm(
                        np.array(parsed_da[1], dtype=np.float64)
                        - np.array(parsed_da[0], dtype=np.float64)
                    )
                )
                with self._lock:
                    self.set_display_area_history.append(parsed_da)
                    if self._max_width_mm is None or w_mm <= self._max_width_mm:
                        self.display_area = parsed_da
            rsp_payload = b'\x00\x00'
        elif op == TTP_OP_SUBSCRIBE and len(payload) >= 11:
            stream_id = (payload[9] << 8) | payload[10]
            with self._lock:
                self._subscribed.add(stream_id)
        elif op == TTP_OP_UNSUBSCRIBE and len(payload) >= 11:
            stream_id = (payload[9] << 8) | payload[10]
            with self._lock:
                self._subscribed.discard(stream_id)

        rsp_ttp = build_ttp_frame(TTP_MAGIC_RSP, seq, op, rsp_payload)
        with self._lock:
            self._rx_buf.extend(_wrap_in_envelope(rsp_ttp))
        return len(data)

    def bulk_read(self, ep_in: int, max_len: int = 16384, timeout_ms: int = 250) -> bytes:
        deadline = time.monotonic() + (max(1, timeout_ms) / 1000.0)
        while time.monotonic() < deadline:
            self._emit_notifications()
            with self._lock:
                if self._rx_buf:
                    out = bytes(self._rx_buf[:max_len])
                    del self._rx_buf[:max_len]
                    return out
            time.sleep(0.004)
        return b''

    def _emit_notifications(self) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._open or not self._subscribed:
                return
            ts_us = int((now - self._start_mono) * 1_000_000.0) + 5_000_000
            if STREAM_GAZE in self._subscribed and (now - self._last_gaze) >= 0.012:
                self._last_gaze = now
                self._seq += 1
                gs = GazeSample(
                    timestamp_us=ts_us,
                    has_timestamp=True,
                    gaze_valid=True,
                    gaze_x=0.5,
                    gaze_y=0.5,
                    validity_l=True,
                    validity_r=True,
                    pupil_l_mm=3.5,
                    pupil_r_mm=3.5,
                    eye_l_x_mm=30.0,
                    eye_l_y_mm=20.0,
                    eye_l_z_mm=600.0,
                    eye_r_x_mm=-30.0,
                    eye_r_y_mm=20.0,
                    eye_r_z_mm=600.0,
                )
                g_ttp = build_ttp_frame(
                    TTP_MAGIC_NOTIFY, self._seq, STREAM_GAZE, _encode_gaze_sample_payload(gs)
                )
                self._rx_buf.extend(_wrap_in_envelope(g_ttp))

            if STREAM_IMAGE_PRIMARY in self._subscribed and (now - self._last_img) >= 0.035:
                self._last_img = now
                self._seq += 1
                gray = np.full((48, 64), 90, dtype=np.uint8)
                i_ttp = build_ttp_frame(
                    TTP_MAGIC_NOTIFY,
                    self._seq,
                    STREAM_IMAGE_PRIMARY,
                    _encode_image_payload(ts_us, gray),
                )
                self._rx_buf.extend(_wrap_in_envelope(i_ttp))

            if STREAM_SYNC in self._subscribed and self._seq == 102:
                self._seq += 1
                sync_payload = (
                    b'\x00\x00'
                    + tlv_tag(0x020BB9) + tlv_u32(1) + struct.pack('>BIQ', 0x06, 8, ts_us)
                    + tlv_tag(0x020BB9) + tlv_u32(2) + struct.pack('>BIQ', 0x06, 8, ts_us + 100)
                )
                s_ttp = build_ttp_frame(TTP_MAGIC_NOTIFY, self._seq, STREAM_SYNC, sync_payload)
                self._rx_buf.extend(_wrap_in_envelope(s_ttp))


class _StubFaceExtractor:
    """Fast deterministic FaceExtractor stub for transport/UI tests."""

    def close(self) -> None:
        return

    def process_nir_frame(
        self,
        gray: np.ndarray,
        timestamp_us: int,
        host_unix_sec: float,
        include_ui_landmarks: bool = False,
    ) -> FaceSample:
        return FaceSample(
            timestamp_us=timestamp_us,
            host_unix_sec=host_unix_sec,
            present=True,
            head_yaw_deg=3.0,
            head_pitch_deg=-1.0,
            head_roll_deg=0.5,
            eye_blink_left=0.05,
            eye_blink_right=0.05,
            ui_landmarks_xy=[(12.0, 16.0)] if include_ui_landmarks else [],
        )


def test_ttp_parser_fragmented_continuation_and_resync() -> None:
    """Verify multi-transfer `0x01 0x00 0x00 0x00` continuation envelope stripping & resync."""
    gray = np.arange(0, 64 * 48, dtype=np.uint8).reshape((48, 64))
    img_payload = _encode_image_payload(123456789, gray)
    ttp_bytes = build_ttp_frame(TTP_MAGIC_NOTIFY, 42, STREAM_IMAGE_PRIMARY, img_payload)
    full_wire = _wrap_in_envelope(ttp_bytes)

    # Split across three USB bulk transfers, prepending 8-byte continuation envelopes
    part1 = full_wire[:512]
    part2 = b'\x01\x00\x00\x00' + struct.pack('<I', 1024) + full_wire[512:1536]
    part3 = b'\x01\x00\x00\x00' + struct.pack('<I', len(full_wire) - 1536) + full_wire[1536:]

    parser = TtpStreamParser()
    parser.feed(b'\x99\x88\x77\x00\xff')
    parser.feed(part1)
    assert parser.pop_frames() == []
    parser.feed(part2)
    assert parser.pop_frames() == []
    parser.feed(part3)

    frames = parser.pop_frames()
    assert len(frames) == 1
    assert frames[0].magic == TTP_MAGIC_NOTIFY
    assert frames[0].seq == 42
    assert frames[0].op == STREAM_IMAGE_PRIMARY

    decoded_img = decode_image_payload(STREAM_IMAGE_PRIMARY, frames[0].payload)
    assert decoded_img is not None
    assert decoded_img.timestamp_us == 123456789
    assert decoded_img.width == 64
    assert decoded_img.height == 48
    assert np.array_equal(decoded_img.gray, gray)


def test_ttp_realm_hmac_and_gaze_sync_roundtrips() -> None:
    """Verify HMAC-MD5 realm auth, Q22.42/Q16.16 `0x0500` GazeSample, and `0x1771` SyncFrame."""
    challenge = b'RANDOM_ET5_CHALLENGE_BYTES'
    open_rsp_payload = b'\x00\x00' + tlv_u32(9) + tlv_u32(77) + challenge
    parsed = parse_open_realm_challenge(open_rsp_payload)
    assert parsed == (9, 77, challenge)

    digest = compute_realm_hmac(challenge)
    expected = hmac.new(REALM_KEY, challenge, hashlib.md5).digest()
    assert digest == expected
    assert len(digest) == 16

    orig = GazeSample(
        timestamp_us=987654321,
        has_timestamp=True,
        frame_counter=555,
        gaze_valid=True,
        gaze_x=0.61234,
        gaze_y=0.38765,
        gaze_unfiltered_valid=True,
        gaze_unfiltered_x=0.61500,
        gaze_unfiltered_y=0.38500,
        gaze_l_valid=True,
        gaze_l_x=0.60800,
        gaze_l_y=0.38800,
        gaze_r_valid=True,
        gaze_r_x=0.61600,
        gaze_r_y=0.38700,
        validity_l=True,
        validity_r=True,
        eye_present_l=True,
        eye_present_r=True,
        pupil_l_mm=3.456,
        pupil_r_mm=3.512,
        eye_l_x_mm=31.25,
        eye_l_y_mm=42.50,
        eye_l_z_mm=605.75,
        eye_r_x_mm=-32.10,
        eye_r_y_mm=41.90,
        eye_r_z_mm=606.20,
    )
    payload = _encode_gaze_sample_payload(orig)
    dec = decode_gaze_sample(payload)
    assert dec is not None
    assert dec.timestamp_us == 987654321
    assert dec.frame_counter == 555
    assert dec.gaze_valid is True
    assert math.isclose(dec.gaze_x, orig.gaze_x, abs_tol=1e-6)
    assert math.isclose(dec.gaze_y, orig.gaze_y, abs_tol=1e-6)
    assert math.isclose(dec.pupil_l_mm, orig.pupil_l_mm, abs_tol=2e-4)
    assert math.isclose(dec.pupil_r_mm, orig.pupil_r_mm, abs_tol=2e-4)
    assert math.isclose(dec.eye_l_z_mm, orig.eye_l_z_mm, abs_tol=1e-6)

    blink = GazeSample(timestamp_us=987665432, has_timestamp=True, gaze_valid=False)
    blink_row = blink.to_brt1_row(1.25)
    assert len(blink_row) == len(TOBII_GAZE_CHANNELS)
    assert blink_row[0] == 1.25
    assert all(math.isnan(v) for v in blink_row[1:])

    sync_payload = (
        b'\x00\x00'
        + tlv_tag(0x020BB9) + tlv_u32(1) + struct.pack('>BIQ', 0x06, 8, 5_000_000)
        + tlv_tag(0x020BB9) + tlv_u32(2) + struct.pack('>BIQ', 0x06, 8, 5_000_200)
    )
    sync_frame = decode_sync_payload(sync_payload)
    assert sync_frame is not None
    assert sync_frame.device_timestamp_us == 5_000_000
    assert sync_frame.receive_timestamp_us == 5_000_200

    clk = TobiiClockSynchronizer()
    clk.observe(5_000_000, 1700000105.0)
    assert clk.is_locked
    session_sec = clk.device_ts_to_session_sec(5_500_000, 1700000100_000, 0.0)
    assert math.isclose(session_sec, 5.5, abs_tol=1e-3)


def test_brt1_blink_nan_and_automatic_baseline_recovery() -> None:
    """Verify `NaN` (`-32768`) blink encoding and automatic `0x03` Baseline keyframe recovery."""
    meta = {
        'Track name': 'Spa-Francorchamps',
        'Driver name': 'EyeDriver',
        'Session': 'Qualifying',
    }
    seg = BrotliBrt1Segment(
        metadata=meta,
        channels=TOBII_GAZE_CHANNELS,
        stream_name=STREAM_TOBII_GAZE,
    )

    s1 = GazeSample(
        gaze_valid=True, gaze_x=0.50, gaze_y=0.45,
        gaze_unfiltered_valid=True, gaze_unfiltered_x=0.50, gaze_unfiltered_y=0.45,
        gaze_l_valid=True, gaze_l_x=0.49, gaze_l_y=0.45,
        gaze_r_valid=True, gaze_r_x=0.51, gaze_r_y=0.45,
        validity_l=True, validity_r=True,
        pupil_l_mm=3.4, pupil_r_mm=3.5,
        eye_l_x_mm=30.0, eye_l_y_mm=20.0, eye_l_z_mm=600.0,
        eye_r_x_mm=-30.0, eye_r_y_mm=20.0, eye_r_z_mm=600.0,
    )
    s2 = GazeSample(
        gaze_valid=True, gaze_x=0.52, gaze_y=0.46,
        gaze_unfiltered_valid=True, gaze_unfiltered_x=0.52, gaze_unfiltered_y=0.46,
        gaze_l_valid=True, gaze_l_x=0.51, gaze_l_y=0.46,
        gaze_r_valid=True, gaze_r_x=0.53, gaze_r_y=0.46,
        validity_l=True, validity_r=True,
        pupil_l_mm=3.42, pupil_r_mm=3.52,
        eye_l_x_mm=30.2, eye_l_y_mm=20.0, eye_l_z_mm=600.1,
        eye_r_x_mm=-29.8, eye_r_y_mm=20.0, eye_r_z_mm=600.1,
    )
    s3_blink = GazeSample(gaze_valid=False, validity_l=False, validity_r=False)
    s4_recover = GazeSample(
        gaze_valid=True, gaze_x=0.65, gaze_y=0.40,
        gaze_unfiltered_valid=True, gaze_unfiltered_x=0.65, gaze_unfiltered_y=0.40,
        gaze_l_valid=True, gaze_l_x=0.64, gaze_l_y=0.40,
        gaze_r_valid=True, gaze_r_x=0.66, gaze_r_y=0.40,
        validity_l=True, validity_r=True,
        pupil_l_mm=3.60, pupil_r_mm=3.65,
        eye_l_x_mm=31.0, eye_l_y_mm=21.0, eye_l_z_mm=598.0,
        eye_r_x_mm=-29.0, eye_r_y_mm=21.0, eye_r_z_mm=598.0,
    )

    seg.append_sample(s1.to_brt1_row(0.010))
    seg.append_sample(s2.to_brt1_row(0.021))
    seg.append_sample(s3_blink.to_brt1_row(0.032))
    seg.append_sample(s4_recover.to_brt1_row(0.043))

    compressed = seg.finalize()
    raw_brt1 = brotli.decompress(compressed)

    dec = Brt1StreamDecoder()
    dec.feed(raw_brt1)
    events = dec.pop_events()

    chunk_types = [ev.chunk_type for ev in events]
    assert chunk_types == [
        CHUNK_MAGIC,
        CHUNK_METADATA,
        CHUNK_BASELINE,
        CHUNK_DELTA,
        CHUNK_DELTA,
        CHUNK_BASELINE,
        CHUNK_EOS,
    ]
    assert events[1].metadata is not None
    assert events[1].metadata['stream'] == 'tobii_gaze'

    all_rows = []
    for ev in events:
        if ev.samples:
            all_rows.extend(ev.samples)
    assert len(all_rows) == 4
    assert math.isclose(all_rows[1][1], 0.52, abs_tol=1e-4)
    assert math.isnan(all_rows[2][1])
    assert math.isnan(all_rows[2][9])
    assert math.isclose(all_rows[3][1], 0.65, abs_tol=1e-6)
    assert math.isclose(all_rows[3][9], 3.60, abs_tol=1e-6)


def test_aux_session_manager_dual_streams_http_and_backups() -> None:
    """Verify CommandSink HTTP server uploads both `tobii_gaze` and `tobii_face` streams."""
    uploaded_payloads: List[Tuple[str, str, bytes]] = []

    def _mock_sender(url: str, key: str, body: bytes) -> Tuple[int, str]:
        uploaded_payloads.append((url, key, body))
        return 200, ''

    with tempfile.TemporaryDirectory() as tmp_dir:
        mgr = TobiiAuxSessionManager(
            host='127.0.0.1',
            port=0,
            backup_dir=tmp_dir,
            upload_sender=_mock_sender,
        )
        mgr.start()
        try:
            base_url = f'http://127.0.0.1:{mgr.bound_port}'
            hb_body = json.dumps({
                'session_start_unix_ms': 1710000000_000,
                'participant_tag': 'car42',
                'metadata': {
                    'Track name': 'Imola',
                    'Driver name': 'Kiber',
                    'League': 'rfactor2',
                    'Class': 'LMGT3',
                    'Session': 'Race',
                },
                'upload_target': {
                    'server_address': 'https://example.invalid',
                    'api_key': 'secret-token',
                },
            }).encode('utf-8')

            req = urllib.request.Request(
                f'{base_url}/api/session/heartbeat',
                data=hb_body,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                assert resp.status == 200

            for idx in range(5):
                t_unix = 1710000000.0 + idx * 0.011
                mgr.on_gaze_sample(
                    t_unix,
                    GazeSample(
                        gaze_valid=True, gaze_x=0.4 + 0.01 * idx, gaze_y=0.5,
                        validity_l=True, validity_r=True,
                        pupil_l_mm=3.2, pupil_r_mm=3.3,
                        eye_l_x_mm=30.0, eye_l_y_mm=10.0, eye_l_z_mm=600.0,
                        eye_r_x_mm=-30.0, eye_r_y_mm=10.0, eye_r_z_mm=600.0,
                    ),
                )
            for idx in range(3):
                t_unix = 1710000000.0 + idx * 0.033
                mgr.on_face_sample(
                    t_unix,
                    FaceSample(
                        timestamp_us=1_000_000 + idx * 33_333,
                        host_unix_sec=t_unix,
                        present=True,
                        head_pos_x_cm=1.5,
                        head_pos_y_cm=-0.5,
                        head_pos_z_cm=-58.0,
                        head_yaw_deg=5.2 + idx,
                        head_pitch_deg=-2.1,
                        head_roll_deg=0.4,
                        eye_blink_left=0.05,
                        eye_blink_right=0.06,
                        eye_squint_left=0.10,
                        eye_squint_right=0.11,
                        eye_wide_left=0.02,
                        eye_wide_right=0.02,
                        jaw_open=0.01,
                    ),
                )

            with urllib.request.urlopen(f'{base_url}/api/session/status', timeout=3.0) as s_resp:
                st = json.loads(s_resp.read().decode('utf-8'))
                assert st['active'] is True
                assert st['gaze_samples_recorded'] == 5
                assert st['face_samples_recorded'] == 3

            stop_req = urllib.request.Request(
                f'{base_url}/api/session/stop',
                data=b'{}',
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(stop_req, timeout=3.0) as stop_resp:
                assert stop_resp.status == 200

            mgr.wait_for_uploads(timeout=3.0)
            assert len(uploaded_payloads) == 2

            streams_seen: Dict[str, Tuple[int, int]] = {}
            for url, key, comp_bytes in uploaded_payloads:
                assert url == 'https://example.invalid'
                assert key == 'secret-token'
                dec = Brt1StreamDecoder()
                dec.feed(brotli.decompress(comp_bytes))
                evs = dec.pop_events()
                stream_id = dec.metadata['stream']
                row_count = sum(len(e.samples) for e in evs if e.samples)
                streams_seen[stream_id] = (len(dec.channels), row_count)

            assert streams_seen[STREAM_TOBII_GAZE] == (len(TOBII_GAZE_CHANNELS), 5)
            assert streams_seen[STREAM_TOBII_FACE] == (len(TOBII_FACE_CHANNELS), 3)

            backup_files = sorted(os.listdir(tmp_dir))
            assert 'TobiiFace_1710000000_car42_server.brt1.br' in backup_files
            assert 'TobiiGaze_1710000000_car42_server.brt1.br' in backup_files
        finally:
            mgr.stop()


def test_pose_decomposition_and_worker_visibility_gating() -> None:
    """Verify forward-vector 4x4 pose angles and `MediaPipeFaceWorker` UI landmark gating."""
    yaw_rad = math.radians(25.0)
    mat = np.eye(4, dtype=np.float64)
    mat[0, 0] = math.cos(yaw_rad)
    mat[0, 2] = math.sin(yaw_rad)
    mat[2, 0] = -math.sin(yaw_rad)
    mat[2, 2] = math.cos(yaw_rad)
    mat[0, 3] = 4.2
    mat[1, 3] = -1.8
    mat[2, 3] = -60.5

    tx, ty, tz, yaw, pitch, roll = decompose_pose_matrix_forward(mat)
    assert math.isclose(tx, 4.2)
    assert math.isclose(ty, -1.8)
    assert math.isclose(tz, -60.5)
    assert math.isclose(yaw, 25.0, abs_tol=1e-5)
    assert math.isclose(pitch, 0.0, abs_tol=1e-5)
    assert math.isclose(roll, 0.0, abs_tol=1e-5)

    # Looking up in MediaPipe OpenGL camera space (+Y up -> R[1, 2] > 0) yields positive pitch
    pitch_up_rad = math.radians(20.0)
    mat_up = np.eye(4, dtype=np.float64)
    mat_up[1, 1] = math.cos(pitch_up_rad)
    mat_up[1, 2] = math.sin(pitch_up_rad)
    mat_up[2, 1] = -math.sin(pitch_up_rad)
    mat_up[2, 2] = math.cos(pitch_up_rad)
    _, _, _, yaw_u, pitch_u, roll_u = decompose_pose_matrix_forward(mat_up)
    assert math.isclose(yaw_u, 0.0, abs_tol=1e-5)
    assert math.isclose(pitch_u, 20.0, abs_tol=1e-5)
    assert math.isclose(roll_u, 0.0, abs_tol=1e-5)

    seen_ui_flags: List[bool] = []
    out_samples: List[FaceSample] = []

    class _FlagStubExtractor:
        def close(self) -> None:
            return

        def process_nir_frame(
            self,
            gray: np.ndarray,
            timestamp_us: int,
            host_unix_sec: float,
            include_ui_landmarks: bool = False,
        ) -> FaceSample:
            seen_ui_flags.append(include_ui_landmarks)
            return FaceSample(
                timestamp_us=timestamp_us,
                host_unix_sec=host_unix_sec,
                present=True,
                ui_landmarks_xy=[(10.0, 20.0)] if include_ui_landmarks else [],
            )

    worker = MediaPipeFaceWorker(
        on_face_sample=out_samples.append,
        max_fps=100.0,
        extractor_factory=_FlagStubExtractor,
    )
    worker.set_ui_visible(False)
    worker.start()
    try:
        dummy = np.zeros((32, 32), dtype=np.uint8)
        worker.submit_frame(dummy, 100_000, 1700000001.0)
        deadline = time.monotonic() + 1.5
        while not out_samples and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(out_samples) == 1
        assert out_samples[0].ui_landmarks_xy == []
        assert seen_ui_flags[-1] is False

        worker.set_ui_visible(True)
        time.sleep(0.02)
        worker.submit_frame(dummy, 200_000, 1700000001.1)
        deadline = time.monotonic() + 1.5
        while len(out_samples) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(out_samples) == 2
        assert out_samples[1].ui_landmarks_xy == [(10.0, 20.0)]
        assert seen_ui_flags[-1] is True
    finally:
        worker.stop()


def test_live_mediapipe_face_extractor_model() -> None:
    """Verify real Google MediaPipe `FaceLandmarker` loads `face_landmarker.task` and runs."""
    pytest.importorskip('mediapipe')
    pytest.importorskip('cv2')
    model_p = default_model_path()
    if not os.path.isfile(model_p):
        pytest.skip('face_landmarker.task is not present on disk')

    from tobii.mediapipe_worker import MediaPipeFaceExtractor

    assert verify_sha256(model_p)
    ext = MediaPipeFaceExtractor(model_path=model_p)
    try:
        frame = np.full((240, 320), 64, dtype=np.uint8)
        res = ext.process_nir_frame(frame, timestamp_us=1_000_000, host_unix_sec=1700000000.0)
        assert res.timestamp_us == 1_000_000
        assert res.latency_ms >= 0.0
    finally:
        ext.close()


def test_direct_client_handshake_and_background_collection() -> None:
    """Verify `TobiiDirectClient` full TTP handshake & streaming when UI is hidden."""
    gaze_rx: List[Tuple[float, GazeSample]] = []
    face_rx: List[Tuple[float, FaceSample]] = []

    client = TobiiDirectClient(
        on_gaze_sample=lambda ts, gs: gaze_rx.append((ts, gs)),
        on_face_sample=lambda ts, fs: face_rx.append((ts, fs)),
        ir_fps=30.0,
        calibration_path=os.path.join(tempfile.gettempdir(), 'nonexistent_tobii_test_cal.json'),
        transport_factory=_FakeTtpUsbTransport,
        extractor_factory=_StubFaceExtractor,
    )
    client.set_ui_visible(False)
    client.start()
    try:
        deadline = time.monotonic() + 2.0
        while (len(gaze_rx) < 5 or len(face_rx) < 2) and time.monotonic() < deadline:
            time.sleep(0.02)

        assert len(gaze_rx) >= 5
        assert len(face_rx) >= 2
        snap = client.get_ui_snapshot()
        assert snap.latest_nir_gray is None
        assert snap.latest_face is not None
        assert snap.latest_face.ui_landmarks_xy == []
    finally:
        client.stop()


def test_studio_gui_zero_cpu_visibility_gating() -> None:
    """Verify `TobiiStudioApp` performs ZERO UI redraw ticks while minimized/withdrawn."""
    tk = pytest.importorskip('tkinter')
    from tobii.main import TobiiStudioApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('No graphical display available for Tkinter')

    app = TobiiStudioApp(
        root,
        aux_port=0,
        ir_fps=30.0,
        calibration_path=os.path.join(tempfile.gettempdir(), 'nonexistent_tobii_gui_cal.json'),
        auto_connect=True,
        transport_factory=_FakeTtpUsbTransport,
        extractor_factory=_StubFaceExtractor,
    )
    try:
        end_vis = time.monotonic() + 0.12
        while time.monotonic() < end_vis:
            root.update()
            time.sleep(0.01)

        root.withdraw()
        app._window_mapped = False
        app._pause_ui_rendering()
        root.update()

        ticks_after_hide = app.ui_tick_counter
        gaze_before = app._client.gaze_samples_received

        end_hidden = time.monotonic() + 0.25
        while time.monotonic() < end_hidden:
            root.update()
            time.sleep(0.01)

        gaze_after = app._client.gaze_samples_received
        assert app.ui_tick_counter == ticks_after_hide
        assert gaze_after > gaze_before
    finally:
        app._on_close()


def test_display_area_payload_roundtrip_and_wrapped_tlv() -> None:
    """Verify `build_set_display_area_packet` and `parse_display_area_payload` TLV decoding."""
    corners: DisplayAreaCorners = (
        (-609.0, 480.5, -720.25),
        (609.0, 480.5, -720.25),
        (-609.0, -204.5, -720.25),
    )
    pkt = build_set_display_area_packet(7, *corners)
    payload = pkt[8 + TTP_HDR_SIZE:]
    parsed = parse_display_area_payload(payload)
    assert parsed is not None
    for p_got, p_exp in zip(parsed, corners):
        for v_got, v_exp in zip(p_got, p_exp):
            assert math.isclose(v_got, v_exp, abs_tol=1e-9)

    # Also verify parsing when wrapped in XDS struct/column tags
    wrapped = (
        b'\x00\x00'
        + tlv_tag(0x030BB8)
        + tlv_tag(0x020BB9) + tlv_u32(1) + tlv_point3d(*corners[0])
        + tlv_tag(0x020BB9) + tlv_u32(2) + tlv_point3d(*corners[1])
        + tlv_tag(0x020BB9) + tlv_u32(3) + tlv_point3d(*corners[2])
    )
    assert parse_display_area_payload(wrapped) is not None

    # Non-zero status or degenerate zero-area display plane returns None
    assert parse_display_area_payload(b'\x00\x01' + payload[2:]) is None
    zero_da = b'\x00\x00' + tlv_point3d(0.0, 0.0, 0.0) * 3
    assert parse_display_area_payload(zero_da) is None


def test_calibration_solver_offscreen_sim_rig_and_kappa_offset() -> None:
    """Verify 9-point 3D calibration for an upward-tilted wheel mount + distant 55\" TV + kappa."""
    # Tracker mounted above steering wheel at origin, tilted +18 deg upward around +X.
    tilt_rad = math.radians(18.0)
    c_t, s_t = math.cos(tilt_rad), math.sin(tilt_rad)
    # World -> Tracker rotation (tilted sensor looks upward at driver's eyes)
    r_world_to_trk = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, c_t, -s_t],
            [0.0, s_t, c_t],
        ],
        dtype=np.float64,
    )

    w_mm, h_mm = compute_screen_dimensions_mm(55.0, 3840, 2160)
    # 55" TV sits 720 mm behind the steering-wheel tracker (Z_world = -720 mm), centered at Y=+140 mm
    tv_center_world = np.array([25.0, 140.0, -720.0], dtype=np.float64)
    tl_world = tv_center_world + np.array([-0.5 * w_mm, 0.5 * h_mm, 0.0])
    tr_world = tv_center_world + np.array([0.5 * w_mm, 0.5 * h_mm, 0.0])
    bl_world = tv_center_world + np.array([-0.5 * w_mm, -0.5 * h_mm, 0.0])

    # Driver's eyes are 620 mm in front of the tracker (total eye-to-TV distance ~= 1340 mm = 134 cm)
    eye_world = np.array([0.0, 130.0, 620.0], dtype=np.float64)
    eye_trk = r_world_to_trk @ eye_world

    # Personal foveal angle kappa (+2.2 deg horizontal, -1.5 deg vertical) in EyeChip space
    kx, ky = math.radians(-1.5), math.radians(2.2)
    r_kappa = (
        np.array([[math.cos(ky), 0.0, math.sin(ky)], [0.0, 1.0, 0.0], [-math.sin(ky), 0.0, math.cos(ky)]])
        @ np.array([[1.0, 0.0, 0.0], [0.0, math.cos(kx), -math.sin(kx)], [0.0, math.sin(kx), math.cos(kx)]])
    )

    rng = np.random.default_rng(42)
    observations = []
    for u_t, v_t in DEFAULT_CALIBRATION_TARGETS:
        target_world = tl_world + u_t * (tr_world - tl_world) + v_t * (bl_world - tl_world)
        target_trk = r_world_to_trk @ target_world
        true_dir_trk = target_trk - eye_trk
        true_dir_trk /= np.linalg.norm(true_dir_trk)
        measured_dir_trk = r_kappa @ true_dir_trk
        measured_dir_trk /= np.linalg.norm(measured_dir_trk)

        raw_samples: List[GazeSample] = []
        for idx in range(25):
            # Inject 2 mid-fixation saccade/blink outliers to verify MAD outlier rejection
            if idx == 5:
                raw_samples.append(GazeSample(gaze_valid=False, validity_l=False, validity_r=False))
                continue
            if idx == 12:
                bad_dir = measured_dir_trk + np.array([0.18, -0.15, 0.0])
                bad_dir /= np.linalg.norm(bad_dir)
                proj_bad = project_ray_onto_display_area(eye_trk, bad_dir, WIDE_COLLECTION_PLANE)
                assert proj_bad is not None
                raw_samples.append(
                    GazeSample(
                        gaze_valid=True,
                        gaze_x=proj_bad[0],
                        gaze_y=proj_bad[1],
                        gaze_unfiltered_valid=True,
                        gaze_unfiltered_x=proj_bad[0],
                        gaze_unfiltered_y=proj_bad[1],
                        validity_l=True,
                        validity_r=True,
                        eye_l_x_mm=float(eye_trk[0] + 31.0),
                        eye_l_y_mm=float(eye_trk[1]),
                        eye_l_z_mm=float(eye_trk[2]),
                        eye_r_x_mm=float(eye_trk[0] - 31.0),
                        eye_r_y_mm=float(eye_trk[1]),
                        eye_r_z_mm=float(eye_trk[2]),
                    )
                )
                continue

            jitter_dir = measured_dir_trk + rng.normal(0.0, 0.0012, size=3)
            jitter_dir /= np.linalg.norm(jitter_dir)
            proj = project_ray_onto_display_area(eye_trk, jitter_dir, WIDE_COLLECTION_PLANE)
            assert proj is not None
            raw_samples.append(
                GazeSample(
                    gaze_valid=True,
                    gaze_x=proj[0],
                    gaze_y=proj[1],
                    gaze_unfiltered_valid=True,
                    gaze_unfiltered_x=proj[0],
                    gaze_unfiltered_y=proj[1],
                    validity_l=True,
                    validity_r=True,
                    eye_l_x_mm=float(eye_trk[0] + 31.0),
                    eye_l_y_mm=float(eye_trk[1]),
                    eye_l_z_mm=float(eye_trk[2]),
                    eye_r_x_mm=float(eye_trk[0] - 31.0),
                    eye_r_y_mm=float(eye_trk[1]),
                    eye_r_z_mm=float(eye_trk[2]),
                )
            )

        obs = aggregate_target_samples(u_t, v_t, raw_samples, WIDE_COLLECTION_PLANE)
        assert obs is not None
        observations.append(obs)

    profile = solve_display_area_3d(
        observations,
        screen_diagonal_in=55.0,
        screen_width_px=3840,
        screen_height_px=2160,
        monitor_index=1,
    )

    # Verify orthogonality of solved 3D display rectangle
    w_solved = np.array(profile.tr_xyz) - np.array(profile.tl_xyz)
    h_solved = np.array(profile.bl_xyz) - np.array(profile.tl_xyz)
    assert abs(float(np.dot(w_solved, h_solved))) < 1e-4

    # Verify sub-0.25% normalized screen RMS accuracy and accurate eye-to-TV depth (~134 cm)
    assert profile.rms_error_norm < 0.0025
    true_eye_to_tv_cm = float(np.linalg.norm(tv_center_world - eye_world)) / 10.0
    assert abs(profile.estimated_eye_to_screen_cm - true_eye_to_tv_cm) < 3.0

    # Verify persistence round-trip
    with tempfile.TemporaryDirectory() as tmp_dir:
        cal_file = os.path.join(tmp_dir, 'calibration_profile.json')
        save_calibration_profile(profile, cal_file)
        loaded = load_calibration_profile(cal_file)
        assert loaded is not None
        assert loaded.monitor_index == 1
        assert math.isclose(loaded.screen_diagonal_in, 55.0)
        assert math.isclose(loaded.tl_xyz[2], profile.tl_xyz[2], abs_tol=1e-6)
        assert delete_calibration_profile(cal_file) is True
        assert load_calibration_profile(cal_file) is None


def test_coexistence_display_area_snapshot_and_restore_on_disconnect() -> None:
    """Verify `TobiiDirectClient` snapshots & restores the tracker's prior display area on exit."""
    tobii_sw_desktop_da: DisplayAreaCorners = (
        (-298.5, 336.0, -12.0),
        (298.5, 336.0, -12.0),
        (-298.5, 0.0, -12.0),
    )
    sim_rig_profile = DisplayAreaProfile(
        tl_xyz=(-608.0, 520.0, -680.0),
        tr_xyz=(608.0, 520.0, -680.0),
        bl_xyz=(-608.0, -165.0, -680.0),
        screen_diagonal_in=55.0,
        monitor_index=0,
        screen_width_px=3840,
        screen_height_px=2160,
        rms_error_norm=0.004,
        estimated_eye_to_screen_cm=130.0,
        estimated_screen_z_cm=-68.0,
        calibrated_at_iso='2026-11-15T12:00:00Z',
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        cal_path = os.path.join(tmp_dir, 'cal.json')
        fake_usb = _FakeTtpUsbTransport(initial_display_area=tobii_sw_desktop_da)

        # 1. Connect without saved profile -> snapshots `tobii_sw_desktop_da`, leaves it active
        client = TobiiDirectClient(
            calibration_path=cal_path,
            transport_factory=lambda: fake_usb,
            extractor_factory=_StubFaceExtractor,
        )
        client.start()
        try:
            orig = client.original_device_display_area
            assert orig is not None
            assert math.isclose(orig[0][0], -298.5, abs_tol=1e-4)

            # Switch to wide collection plane and save new sim-rig calibration
            client.apply_temporary_display_area(WIDE_COLLECTION_PLANE)
            assert math.isclose(fake_usb.display_area[0][0], -500.0, abs_tol=1e-4)

            client.save_and_apply_calibration(sim_rig_profile)
            assert math.isclose(fake_usb.display_area[0][2], -680.0, abs_tol=1e-4)
        finally:
            client.stop()

        # On stop(), hardware display area MUST be restored to `tobii_sw_desktop_da`!
        assert math.isclose(fake_usb.display_area[0][0], -298.5, abs_tol=1e-4)
        assert math.isclose(fake_usb.display_area[0][2], -12.0, abs_tol=1e-4)

        # 2. Reconnect -> automatically reloads `cal.json`, applies sim-rig plane,
        # and `clear_saved_calibration()` immediately restores `tobii_sw_desktop_da` live!
        client2 = TobiiDirectClient(
            calibration_path=cal_path,
            transport_factory=lambda: fake_usb,
            extractor_factory=_StubFaceExtractor,
        )
        client2.start()
        try:
            assert client2.active_calibration_profile is not None
            assert math.isclose(fake_usb.display_area[0][2], -680.0, abs_tol=1e-4)

            assert client2.clear_saved_calibration() is True
            assert client2.active_calibration_profile is None
            assert math.isclose(fake_usb.display_area[0][0], -298.5, abs_tol=1e-4)
        finally:
            client2.stop()


def test_et5_eye_present_and_raw_eye_origin_decoding() -> None:
    """Verify ET5 rows with `col 0x07 == 0`, `eye_present_l == 1`, and `col 0x17` eye origins."""
    cols = [
        (0x01, _tlv_s64(12345678)),
        (0x02, tlv_point3d(0.0, 0.0, 0.0)),
        (0x07, tlv_u32(0)),  # Status 0 (not 1) on ET5 while eye_present_l (0x15) == 1
        (0x08, tlv_point3d(0.0, 0.0, 0.0)),
        (0x0D, tlv_u32(0)),
        (0x15, tlv_u32(1)),
        (0x16, tlv_u32(1)),
        (0x17, tlv_point3d(31.0, 48.0, 610.0)),
        (0x18, tlv_point3d(-31.0, 48.0, 610.0)),
        (0x1C, _tlv_point2d(0.42, 0.36)),
        (0x1D, tlv_u32(1)),
    ]
    row_tag = ((len(cols) & 0xFFF) << 16) | 0x0BB8
    parts = [b'\x00\x00', tlv_tag(row_tag)]
    for col_id, val in cols:
        parts.append(tlv_tag(0x020BB9))
        parts.append(tlv_u32(col_id))
        parts.append(val)
    payload = b''.join(parts)

    decoded = decode_gaze_sample(payload)
    assert decoded is not None
    assert decoded.eye_present_l is True
    assert decoded.eye_present_r is True
    assert decoded.validity_l is True
    assert decoded.validity_r is True
    assert math.isclose(decoded.eye_l_z_mm, 610.0, abs_tol=1e-4)
    assert math.isclose(decoded.eye_r_z_mm, 610.0, abs_tol=1e-4)

    obs = aggregate_target_samples(0.5, 0.5, [decoded] * 12, WIDE_COLLECTION_PLANE)
    assert obs is not None
    assert math.isclose(float(obs.eye_origin_mm[2]), 610.0, abs_tol=1e-3)


def test_firmware_max_width_rejection_scales_plane_along_gaze_cone() -> None:
    """Verify automatic homothetic scaling when ET5 firmware rejects >30-inch display widths."""
    eye_xyz = (0.0, 80.0, 620.0)
    large_tv_profile = DisplayAreaProfile(
        tl_xyz=(-608.0, 560.0, -700.0),
        tr_xyz=(608.0, 560.0, -700.0),
        bl_xyz=(-608.0, -124.0, -700.0),
        screen_diagonal_in=55.0,
        monitor_index=0,
        screen_width_px=3840,
        screen_height_px=2160,
        rms_error_norm=0.003,
        estimated_eye_to_screen_cm=134.0,
        estimated_screen_z_cm=-70.0,
        mean_eye_xyz=eye_xyz,
        calibrated_at_iso='2026-11-15T12:00:00Z',
    )

    scaled = large_tv_profile.scaled_corners_for_max_width(700.0)
    scaled_w = float(np.linalg.norm(np.array(scaled[1]) - np.array(scaled[0])))
    assert math.isclose(scaled_w, 700.0, rel_tol=1e-5)

    # Any 3D gaze ray from `eye_xyz` must intersect both planes at the exact same `(u, v)`!
    eye_arr = np.array(eye_xyz, dtype=np.float64)
    for u_expect, v_expect in [(0.1, 0.1), (0.5, 0.5), (0.9, 0.85), (0.2, 0.75)]:
        tl = np.array(large_tv_profile.tl_xyz)
        tr = np.array(large_tv_profile.tr_xyz)
        bl = np.array(large_tv_profile.bl_xyz)
        pt_tv = tl + u_expect * (tr - tl) + v_expect * (bl - tl)
        ray = pt_tv - eye_arr
        ray /= np.linalg.norm(ray)
        uv_scaled = project_ray_onto_display_area(eye_arr, ray, scaled)
        assert uv_scaled is not None
        assert math.isclose(uv_scaled[0], u_expect, abs_tol=1e-9)
        assert math.isclose(uv_scaled[1], v_expect, abs_tol=1e-9)

    with tempfile.TemporaryDirectory() as tmp_dir:
        cal_path = os.path.join(tmp_dir, 'cal_55in.json')
        fake_usb = _FakeTtpUsbTransport(max_width_mm=800.0)
        client = TobiiDirectClient(
            calibration_path=cal_path,
            transport_factory=lambda: fake_usb,
            extractor_factory=_StubFaceExtractor,
        )
        client.start()
        try:
            client.save_and_apply_calibration(large_tv_profile)
            applied_w = float(
                np.linalg.norm(
                    np.array(fake_usb.display_area[1]) - np.array(fake_usb.display_area[0])
                )
            )
            assert math.isclose(applied_w, 700.0, abs_tol=1e-3)
            assert len(fake_usb.set_display_area_history) >= 2
        finally:
            client.stop()

    # Also verify second-stage 550 mm fallback if firmware caps at 27-inch width (600 mm)
    with tempfile.TemporaryDirectory() as tmp_dir2:
        cal_path2 = os.path.join(tmp_dir2, 'cal_27cap.json')
        fake_usb2 = _FakeTtpUsbTransport(max_width_mm=600.0)
        client2 = TobiiDirectClient(
            calibration_path=cal_path2,
            transport_factory=lambda: fake_usb2,
            extractor_factory=_StubFaceExtractor,
        )
        client2.start()
        try:
            client2.save_and_apply_calibration(large_tv_profile)
            applied_w2 = float(
                np.linalg.norm(
                    np.array(fake_usb2.display_area[1]) - np.array(fake_usb2.display_area[0])
                )
            )
            assert math.isclose(applied_w2, 550.0, abs_tol=1e-3)
        finally:
            client2.stop()


def test_calibration_solver_rejects_corrupted_edge_outliers_and_monocular_ipd_shift() -> None:
    """Verify corrupted left/bottom edge dots & monocular dropout do not twist the 3D TV plane."""
    tilt_rad = math.radians(24.0)
    c_t, s_t = math.cos(tilt_rad), math.sin(tilt_rad)
    r_world_to_trk = np.array(
        [[1.0, 0.0, 0.0], [0.0, c_t, -s_t], [0.0, s_t, c_t]], dtype=np.float64
    )

    w_mm, h_mm = compute_screen_dimensions_mm(40.2, 1920, 1080)
    tv_center_world = np.array([0.0, 220.0, -640.0], dtype=np.float64)
    tl_world = tv_center_world + np.array([-0.5 * w_mm, 0.5 * h_mm, 0.0])
    tr_world = tv_center_world + np.array([0.5 * w_mm, 0.5 * h_mm, 0.0])
    bl_world = tv_center_world + np.array([-0.5 * w_mm, -0.5 * h_mm, 0.0])

    eye_world = np.array([0.0, 180.0, 560.0], dtype=np.float64)
    eye_trk = r_world_to_trk @ eye_world
    eye_l_trk = eye_trk + np.array([-32.0, 0.0, 0.0])
    eye_r_trk = eye_trk + np.array([32.0, 0.0, 0.0])

    observations = []
    for u_t, v_t in DEFAULT_CALIBRATION_TARGETS:
        target_world = tl_world + u_t * (tr_world - tl_world) + v_t * (bl_world - tl_world)
        target_trk = r_world_to_trk @ target_world

        # Inject severe edge-tracking corruption on (0.10, 0.10) and (0.10, 0.90)
        if (u_t, v_t) in ((0.10, 0.10), (0.10, 0.90)):
            corrupted_pt = target_trk + np.array([-260.0, -190.0, 180.0])
            bad_dir = (corrupted_pt - eye_trk) / np.linalg.norm(corrupted_pt - eye_trk)
            proj = project_ray_onto_display_area(eye_trk, bad_dir, WIDE_COLLECTION_PLANE)
            assert proj is not None
            samples = [
                GazeSample(
                    gaze_valid=True,
                    gaze_x=proj[0],
                    gaze_y=proj[1],
                    validity_l=True,
                    validity_r=True,
                    eye_l_x_mm=float(eye_l_trk[0]),
                    eye_l_y_mm=float(eye_l_trk[1]),
                    eye_l_z_mm=float(eye_l_trk[2]),
                    eye_r_x_mm=float(eye_r_trk[0]),
                    eye_r_y_mm=float(eye_r_trk[1]),
                    eye_r_z_mm=float(eye_r_trk[2]),
                )
            ] * 16
        elif (u_t, v_t) == (0.10, 0.50):
            # Simulate right-eye dropout on left edge: only left eye + gaze_l (0x05) valid!
            dir_l = (target_trk - eye_l_trk) / np.linalg.norm(target_trk - eye_l_trk)
            proj_l = project_ray_onto_display_area(eye_l_trk, dir_l, WIDE_COLLECTION_PLANE)
            proj_c = project_ray_onto_display_area(
                eye_trk,
                (target_trk - eye_trk) / np.linalg.norm(target_trk - eye_trk),
                WIDE_COLLECTION_PLANE,
            )
            assert proj_l is not None and proj_c is not None
            samples = [
                GazeSample(
                    gaze_valid=True,
                    gaze_x=proj_c[0],
                    gaze_y=proj_c[1],
                    gaze_l_valid=True,
                    gaze_l_x=proj_l[0],
                    gaze_l_y=proj_l[1],
                    validity_l=True,
                    validity_r=False,
                    eye_l_x_mm=float(eye_l_trk[0]),
                    eye_l_y_mm=float(eye_l_trk[1]),
                    eye_l_z_mm=float(eye_l_trk[2]),
                )
            ] * 16
        else:
            true_dir = (target_trk - eye_trk) / np.linalg.norm(target_trk - eye_trk)
            proj = project_ray_onto_display_area(eye_trk, true_dir, WIDE_COLLECTION_PLANE)
            assert proj is not None
            samples = [
                GazeSample(
                    gaze_valid=True,
                    gaze_x=proj[0],
                    gaze_y=proj[1],
                    validity_l=True,
                    validity_r=True,
                    eye_l_x_mm=float(eye_l_trk[0]),
                    eye_l_y_mm=float(eye_l_trk[1]),
                    eye_l_z_mm=float(eye_l_trk[2]),
                    eye_r_x_mm=float(eye_r_trk[0]),
                    eye_r_y_mm=float(eye_r_trk[1]),
                    eye_r_z_mm=float(eye_r_trk[2]),
                )
            ] * 16

        obs = aggregate_target_samples(u_t, v_t, samples, WIDE_COLLECTION_PLANE)
        assert obs is not None
        observations.append(obs)

    profile = solve_display_area_3d(observations, 40.2, 1920, 1080, monitor_index=0)
    assert profile.inlier_targets == 7
    assert profile.total_targets == 9
    assert profile.rms_error_norm < 0.005

    # Verify true left edge (u=0.0) and bottom edge (v=1.0) map symmetrically with <1% error!
    for u_chk, v_chk in [(0.0, 0.5), (1.0, 0.5), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]:
        pt_trk = r_world_to_trk @ (
            tl_world + u_chk * (tr_world - tl_world) + v_chk * (bl_world - tl_world)
        )
        ray = (pt_trk - eye_trk) / np.linalg.norm(pt_trk - eye_trk)
        uv_hat = project_ray_onto_display_area(eye_trk, ray, profile.corners)
        assert uv_hat is not None
        assert abs(uv_hat[0] - u_chk) < 0.01
        assert abs(uv_hat[1] - v_chk) < 0.01


def test_fullscreen_calibration_opens_in_verify_and_populates_edid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Calibrate Screen opens in live verify mode and auto-populates diagonal from EDID."""
    tk = pytest.importorskip('tkinter')
    import tobii.main as tobii_main
    from tobii.main import TobiiStudioApp, _MonitorDescriptor

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('No graphical display available for Tkinter')
    root.withdraw()

    fake_usb = _FakeTtpUsbTransport()
    saved_profile = DisplayAreaProfile(
        tl_xyz=(-450.0, 420.0, -700.0),
        tr_xyz=(450.0, 420.0, -700.0),
        bl_xyz=(-450.0, -86.0, -700.0),
        screen_diagonal_in=32.0,
        monitor_index=0,
        screen_width_px=1920,
        screen_height_px=1080,
        rms_error_norm=0.012,
        estimated_eye_to_screen_cm=130.0,
        estimated_screen_z_cm=-70.0,
        inlier_targets=9,
        total_targets=9,
        calibrated_at_iso='2026-11-16T00:00:00Z',
    )

    fake_monitors = [
        _MonitorDescriptor(
            index=0,
            x=0,
            y=0,
            width=1920,
            height=1080,
            edid_diagonal_in=40.2,
            is_primary=True,
            name='SAMSUNG',
        ),
        _MonitorDescriptor(
            index=1,
            x=1920,
            y=0,
            width=1920,
            height=1200,
            edid_diagonal_in=16.0,
            is_primary=False,
            name='Internal Display',
        ),
    ]
    monkeypatch.setattr(tobii_main, '_discover_monitors', lambda _root: list(fake_monitors))

    with tempfile.TemporaryDirectory() as tmp_dir:
        cal_path = os.path.join(tmp_dir, 'cal_profile.json')
        save_calibration_profile(saved_profile, cal_path)

        app = TobiiStudioApp(
            root,
            aux_port=0,
            ir_fps=30.0,
            calibration_path=cal_path,
            auto_connect=True,
            transport_factory=lambda: fake_usb,
            extractor_factory=_StubFaceExtractor,
        )
        try:
            # Wait briefly for handshake & saved calibration profile to be applied on hardware
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                root.update()
                if abs(fake_usb.display_area[0][2] - saved_profile.tl_xyz[2]) < 1.0:
                    break
                time.sleep(0.01)
            assert math.isclose(fake_usb.display_area[0][2], saved_profile.tl_xyz[2], abs_tol=1.0)

            app._on_calibrate_clicked()
            overlay = app._active_cal_overlay
            assert overlay is not None
            overlay._win.withdraw()
            root.update()

            # Opens directly in verify mode without replacing the active hardware display area
            assert overlay._phase == 'verify'
            assert math.isclose(fake_usb.display_area[0][2], saved_profile.tl_xyz[2], abs_tol=1.0)
            assert overlay._diag_var.get() == '40.2'

            # Switching monitors in the dropdown updates geometry and auto-populates EDID size
            overlay._cb_monitor.current(1)
            overlay._on_monitor_selected()
            assert overlay._monitor.index == 1
            assert overlay._diag_var.get() == '16.0'

            # Starting recalibration applies WIDE_COLLECTION_PLANE and enters ready_wait
            overlay._start_recalibration()
            assert overlay._phase == 'ready_wait'
            assert math.isclose(fake_usb.display_area[0][0], WIDE_COLLECTION_PLANE[0][0], abs_tol=1.0)

            # Pressing Esc mid-recalibration aborts back to verify mode & restores active display area
            overlay._on_escape()
            assert overlay._phase == 'verify'
            assert math.isclose(fake_usb.display_area[0][2], saved_profile.tl_xyz[2], abs_tol=1.0)

            # Pressing Esc in verify mode closes the overlay cleanly
            overlay._on_escape()
            assert app._active_cal_overlay is None
        finally:
            app._on_close()


def _start_brt1_test_rx(
    hdr_out: List[Tuple[str, Dict[str, Any]]],
    gaze_out: List[Any],
) -> Any:
    """Start a local chunked-HTTP BRT1 receiver for Tobii uploader tests."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from live_feedback.stream_server import DecodedGazeSample, _iter_http_body_chunks

    class _MultiStreamHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format_str: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            dec = Brt1StreamDecoder()
            for chunk in _iter_http_body_chunks(self.rfile, self.headers):
                dec.feed(chunk)
                for ev in dec.pop_events():
                    if ev.chunk_type == CHUNK_METADATA and ev.metadata:
                        hdr_out.append((ev.metadata.get('stream', ''), ev.metadata))
                    elif ev.samples and dec.metadata.get('stream') == STREAM_TOBII_GAZE:
                        for row in ev.samples:
                            gx, gy = row[1], row[2]
                            valid = not (math.isnan(gx) or math.isnan(gy))
                            gaze_out.append(
                                DecodedGazeSample(
                                    time_s=row[0],
                                    gaze_x=gx if valid else None,
                                    gaze_y=gy if valid else None,
                                    gaze_valid=valid,
                                    is_live=True,
                                )
                            )
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class _Srv(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    s = _Srv(('127.0.0.1', 0), _MultiStreamHandler)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    s._thread = t  # type: ignore[attr-defined]
    return s


def test_aux_session_manager_live_multisink_brt1_streaming() -> None:
    """Verify Tobii streams live chunked BRT1 simultaneously to multiple HTTP sinks before stop."""
    from live_feedback.stream_server import DecodedGazeSample

    srv1_headers: List[Tuple[str, Dict[str, Any]]] = []
    srv1_gaze: List[DecodedGazeSample] = []
    srv2_headers: List[Tuple[str, Dict[str, Any]]] = []
    srv2_gaze: List[DecodedGazeSample] = []

    rx1 = _start_brt1_test_rx(srv1_headers, srv1_gaze)
    rx2 = _start_brt1_test_rx(srv2_headers, srv2_gaze)
    assert isinstance(parse_sink_spec('http://127.0.0.1:18922'), UploadSinkConfig)
    assert parse_sink_spec('http://127.0.0.1:18922').max_retries == 0
    assert parse_sink_spec('https://brbrdb.brbrkitten.com').max_retries == 5

    try:
        url1 = f'http://127.0.0.1:{rx1.server_address[1]}'  # type: ignore[attr-defined]
        url2 = f'http://127.0.0.1:{rx2.server_address[1]}'  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as tmp_dir:
            mgr = TobiiAuxSessionManager(
                host='127.0.0.1',
                port=0,
                rollover_interval_sec=30.0,
                backup_dir=tmp_dir,
            )
            mgr.start()
            try:
                base_url = f'http://127.0.0.1:{mgr.bound_port}'
                hb_body = json.dumps({
                    'session_start_unix_ms': 1710000500_000,
                    'participant_tag': '',
                    'metadata': {
                        'Track name': 'Rowrah',
                        'Driver name': 'Kiber',
                        'League': 'kartsim',
                        'Class': 'cadet',
                        'Session': 'Practice',
                    },
                    'upload_targets': [
                        {
                            'server_address': url1,
                            'api_key': 'key-1',
                            'max_retries': 2,
                            'buffer_duration_minutes': 5,
                        },
                        {
                            'server_address': url2,
                            'api_key': '',
                            'max_retries': 0,
                            'buffer_duration_minutes': 0,
                        },
                    ],
                }).encode('utf-8')

                req = urllib.request.Request(
                    f'{base_url}/api/session/heartbeat',
                    data=hb_body,
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    assert resp.status == 200

                # Push live samples including a NaN blink transition mid-session
                gaze_inputs = [
                    (True, 0.35, 0.45),
                    (True, 0.40, 0.46),
                    (False, float('nan'), float('nan')),
                    (True, 0.62, 0.51),
                ]
                for idx, (valid, gx, gy) in enumerate(gaze_inputs):
                    t_unix = 1710000500.0 + idx * 0.016
                    mgr.on_gaze_sample(
                        t_unix,
                        GazeSample(
                            gaze_valid=valid,
                            gaze_x=gx,
                            gaze_y=gy,
                            validity_l=valid,
                            validity_r=valid,
                        ),
                    )
                    time.sleep(0.015)

                mgr.on_face_sample(
                    1710000500.05,
                    FaceSample(
                        timestamp_us=50_000,
                        host_unix_sec=1710000500.05,
                        present=True,
                        head_yaw_deg=3.5,
                    ),
                )

                # Both sinks MUST receive live samples BEFORE /api/session/stop!
                deadline = time.monotonic() + 3.0
                while (len(srv1_gaze) < 4 or len(srv2_gaze) < 4) and time.monotonic() < deadline:
                    time.sleep(0.02)

                assert len(srv1_gaze) == 4
                assert len(srv2_gaze) == 4
                assert srv2_gaze[0].gaze_valid is True
                assert math.isclose(srv2_gaze[0].gaze_x or 0.0, 0.35, abs_tol=1e-4)
                assert srv2_gaze[2].gaze_valid is False
                assert srv2_gaze[3].gaze_valid is True
                assert math.isclose(srv2_gaze[3].gaze_x or 0.0, 0.62, abs_tol=1e-4)

                # Verify both streams (tobii_gaze and tobii_face) arrived on both sinks
                deadline = time.monotonic() + 2.0
                while (len(srv1_headers) < 2 or len(srv2_headers) < 2) and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert {s for s, _ in srv1_headers} == {STREAM_TOBII_GAZE, STREAM_TOBII_FACE}
                assert {s for s, _ in srv2_headers} == {STREAM_TOBII_GAZE, STREAM_TOBII_FACE}

                stop_req = urllib.request.Request(
                    f'{base_url}/api/session/stop',
                    data=b'{}',
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(stop_req, timeout=3.0) as stop_resp:
                    assert stop_resp.status == 200

                mgr.wait_for_uploads(timeout=3.0)
                backups = sorted(os.listdir(tmp_dir))
                assert 'TobiiGaze_1710000500_server.brt1.br' in backups
                assert 'TobiiFace_1710000500_server.brt1.br' in backups
            finally:
                mgr.stop()
    finally:
        for srv in (rx1, rx2):
            srv.shutdown()
            srv.server_close()
            srv._thread.join(timeout=1.0)  # type: ignore[attr-defined]


def test_aux_session_manager_standby_streaming_and_session_handoff() -> None:
    """Verify local-only standby gaze streaming before heartbeat, dual-sink handoff, and post-stop resume."""
    extracted = extract_upload_targets(
        {
            'upload_targets': [
                {
                    'server_address': 'https://brbrdb.brbrkitten.com',
                    'api_key': 'k',
                    'max_retries': 10,
                    'buffer_duration_minutes': 30,
                },
                {
                    'server_address': 'http://127.0.0.1:18922',
                    'api_key': '',
                    'max_retries': 10,
                    'buffer_duration_minutes': 30,
                },
            ]
        }
    )
    assert len(extracted) == 2
    assert extracted[0].max_retries == 10 and extracted[0].buffer_duration_minutes == 30
    assert extracted[1].max_retries == 0 and extracted[1].buffer_duration_minutes == 0

    remote_headers: List[Tuple[str, Dict[str, Any]]] = []
    remote_gaze: List[Any] = []
    local_headers: List[Tuple[str, Dict[str, Any]]] = []
    local_gaze: List[Any] = []

    rx_remote = _start_brt1_test_rx(remote_headers, remote_gaze)
    rx_local = _start_brt1_test_rx(local_headers, local_gaze)

    try:
        remote_url = f'http://127.0.0.1:{rx_remote.server_address[1]}'
        local_url = f'http://127.0.0.1:{rx_local.server_address[1]}'

        mgr = TobiiAuxSessionManager(
            host='127.0.0.1',
            port=0,
            rollover_interval_sec=30.0,
            standby_sinks=[
                parse_sink_spec(local_url),
                parse_sink_spec('https://brbrdb.invalid'),
            ],
        )
        # Remote standby sinks must be ignored so standby gaze never leaks off-box
        assert [s.server_address for s in mgr.standby_sinks] == [local_url]
        mgr.start()
        try:
            base_url = f'http://127.0.0.1:{mgr.bound_port}'

            # 1. Standby samples before any rFactor heartbeat -> local sink only
            for idx, (gx, gy) in enumerate(((0.25, 0.35), (0.30, 0.40))):
                mgr.on_gaze_sample(
                    1710000600.0 + idx * 0.016,
                    GazeSample(
                        gaze_valid=True,
                        gaze_x=gx,
                        gaze_y=gy,
                        validity_l=True,
                        validity_r=True,
                    ),
                )
                time.sleep(0.015)

            deadline = time.monotonic() + 3.0
            while len(local_gaze) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(local_gaze) == 2
            assert math.isclose(local_gaze[0].gaze_x or 0.0, 0.25, abs_tol=1e-4)
            assert len(remote_gaze) == 0
            assert len(remote_headers) == 0

            # 2. Heartbeat lists only remote_url; local standby sink still joins session targets
            hb_body = json.dumps(
                {
                    'session_start_unix_ms': 1710000605_000,
                    'participant_tag': '',
                    'metadata': {'Track name': 'Rowrah', 'Driver name': 'Kiber'},
                    'upload_targets': [
                        {
                            'server_address': remote_url,
                            'api_key': 'key-1',
                            'max_retries': 2,
                            'buffer_duration_minutes': 5,
                        }
                    ],
                }
            ).encode('utf-8')
            req = urllib.request.Request(
                f'{base_url}/api/session/heartbeat',
                data=hb_body,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                assert resp.status == 200

            for idx, (gx, gy) in enumerate(((0.55, 0.45), (0.60, 0.48))):
                mgr.on_gaze_sample(
                    1710000605.05 + idx * 0.016,
                    GazeSample(
                        gaze_valid=True,
                        gaze_x=gx,
                        gaze_y=gy,
                        validity_l=True,
                        validity_r=True,
                    ),
                )
                time.sleep(0.015)

            deadline = time.monotonic() + 3.0
            while (
                (len(remote_gaze) < 2 or len(local_gaze) < 4)
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert len(remote_gaze) == 2
            assert math.isclose(remote_gaze[0].gaze_x or 0.0, 0.55, abs_tol=1e-4)
            assert len(local_gaze) == 4

            # 3. Stop session -> resumes standby streaming to local sink only
            stop_req = urllib.request.Request(
                f'{base_url}/api/session/stop',
                data=b'{}',
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(stop_req, timeout=3.0) as stop_resp:
                assert stop_resp.status == 200

            mgr.on_gaze_sample(
                1710000610.0,
                GazeSample(
                    gaze_valid=True,
                    gaze_x=0.72,
                    gaze_y=0.51,
                    validity_l=True,
                    validity_r=True,
                ),
            )
            deadline = time.monotonic() + 3.0
            while len(local_gaze) < 5 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(local_gaze) == 5
            assert math.isclose(local_gaze[-1].gaze_x or 0.0, 0.72, abs_tol=1e-4)
            assert len(remote_gaze) == 2
        finally:
            mgr.stop()
    finally:
        for srv in (rx_remote, rx_local):
            srv.shutdown()
            srv.server_close()
            srv._thread.join(timeout=1.0)  # type: ignore[attr-defined]


def test_usb_service_detection_port_takeover_and_gaze_reconnect() -> None:
    """Verify service check, exclusive port takeover, and <1.5s SSE gaze reconnect after server restart."""
    from live_feedback.main import LiveFeedbackController
    from live_feedback.overlay import OverlayStateHub
    from live_feedback.stream_server import LiveStreamServer

    # 1. Windows service query works without Invalid state= error and filters cleanly
    real_svcs = get_windows_tobii_services_running()
    assert isinstance(real_svcs, list)
    assert set(real_svcs).issubset(set(WINDOWS_TOBII_SERVICES))

    with (
        patch('tobii.usb_transport.sys.platform', 'win32'),
        patch(
            'tobii.usb_transport._is_windows_service_active',
            side_effect=lambda name: name == WINDOWS_TOBII_SERVICES[0],
        ),
    ):
        assert get_windows_tobii_services_running() == [WINDOWS_TOBII_SERVICES[0]]

    # 2. Exclusive port binding + /api/shutdown takeover on TobiiAuxSessionManager
    mgr1 = TobiiAuxSessionManager(host='127.0.0.1', port=0)
    mgr1.start()
    taken_port = mgr1.bound_port
    mgr2 = TobiiAuxSessionManager(host='127.0.0.1', port=taken_port)
    try:
        mgr2.start()
        assert mgr2.bound_port == taken_port
        assert mgr1._running is False
    finally:
        mgr2.stop()
        mgr1.stop()

    # 3. TobiiAuxSessionManager -> LiveStreamServer -> SSE /events across server restart (<1.5s)
    hub1 = OverlayStateHub()
    ctrl1 = LiveFeedbackController(server_url='https://example.test', overlay_hub=hub1)
    srv1 = LiveStreamServer(
        host='127.0.0.1',
        port=0,
        on_header=ctrl1.on_header,
        on_samples=ctrl1.on_samples,
        on_gaze_samples=ctrl1.on_gaze_samples,
        overlay_hub=hub1,
        status_provider=ctrl1.health_status,
    )
    srv1.start()
    live_port = srv1.bound_port

    mgr = TobiiAuxSessionManager(
        host='127.0.0.1',
        port=0,
        rollover_interval_sec=30.0,
        standby_sinks=[parse_sink_spec(f'http://127.0.0.1:{live_port}')],
    )
    mgr.start()
    srv2: Optional[LiveStreamServer] = None
    sse_sock: Optional[socket.socket] = None
    try:
        t0 = 1710000700.0
        deadline = time.monotonic() + 2.0
        idx = 0
        while time.monotonic() < deadline:
            mgr.on_gaze_sample(
                t0 + idx * 0.02,
                GazeSample(
                    gaze_valid=True,
                    gaze_x=0.42,
                    gaze_y=0.48,
                    validity_l=True,
                    validity_r=True,
                ),
            )
            idx += 1
            time.sleep(0.02)
            if ctrl1.health_status()['gaze_valid_received'] >= 2 and mgr.context.standby_connected:
                break

        assert ctrl1.health_status()['gaze_valid_received'] >= 2
        assert mgr.context.standby_connected is True

        # Simulate restarting Live Feedback on the same port while Tobii keeps running
        srv1.stop()

        hub2 = OverlayStateHub()
        ctrl2 = LiveFeedbackController(server_url='https://example.test', overlay_hub=hub2)
        srv2 = LiveStreamServer(
            host='127.0.0.1',
            port=live_port,
            on_header=ctrl2.on_header,
            on_samples=ctrl2.on_samples,
            on_gaze_samples=ctrl2.on_gaze_samples,
            overlay_hub=hub2,
            status_provider=ctrl2.health_status,
        )
        srv2.start()

        sse_sock = socket.create_connection(('127.0.0.1', live_port), timeout=2.0)
        sse_sock.sendall(
            b'GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n'
        )
        sse_sock.settimeout(0.05)

        sse_buf = bytearray()
        valid_sse_seen = False
        reconn_deadline = time.monotonic() + 1.5
        while time.monotonic() < reconn_deadline:
            mgr.on_gaze_sample(
                t0 + idx * 0.02,
                GazeSample(
                    gaze_valid=True,
                    gaze_x=0.63,
                    gaze_y=0.37,
                    validity_l=True,
                    validity_r=True,
                ),
            )
            idx += 1
            time.sleep(0.03)
            try:
                chunk = sse_sock.recv(8192)
                if chunk:
                    sse_buf.extend(chunk)
            except socket.timeout:
                pass
            for line in sse_buf.decode('utf-8', errors='replace').splitlines():
                if line.startswith('data: '):
                    ev = json.loads(line[6:])
                    if ev.get('type') == 'tick' and ev.get('gaze', {}).get('valid') is True:
                        valid_sse_seen = True
                        break
            if valid_sse_seen and ctrl2.health_status()['gaze_valid_received'] >= 1:
                break

        assert ctrl2.health_status()['gaze_valid_received'] >= 1
        assert valid_sse_seen is True
    finally:
        if sse_sock is not None:
            sse_sock.close()
        mgr.stop()
        srv1.stop()
        if srv2 is not None:
            srv2.stop()
