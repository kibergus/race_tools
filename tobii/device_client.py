"""Direct USB Tobii Eye Tracker 5 client coordinating TTP streams, clock sync, and MediaPipe."""

from collections import deque
from dataclasses import dataclass
import os
import queue
import sys
import threading
import time
from typing import Any, Callable, Deque, List, Optional, Tuple
import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tobii.calibration import (  # noqa: E402
    DisplayAreaProfile,
    WIDE_COLLECTION_PLANE,
    delete_calibration_profile,
    load_calibration_profile,
    save_calibration_profile,
)
from tobii.mediapipe_worker import (  # noqa: E402
    FaceSample,
    MediaPipeFaceWorker,
)
from tobii.ttp_protocol import (  # noqa: E402
    DisplayAreaCorners,
    GazeSample,
    NirImageFrame,
    STREAM_GAZE,
    STREAM_IMAGE_PRIMARY,
    STREAM_SYNC,
    TOBII_EP_IN,
    TOBII_EP_OUT,
    TTP_MAGIC_NOTIFY,
    TTP_MAGIC_RSP,
    TobiiClockSynchronizer,
    TtpFrame,
    TtpStreamParser,
    build_get_display_area_packet,
    build_hello_packet,
    build_open_realm_packet,
    build_query_realm_packet,
    build_realm_response_packet,
    build_set_display_area_packet,
    build_subscribe_packet,
    compute_realm_hmac,
    decode_gaze_sample,
    decode_image_payload,
    decode_sync_payload,
    parse_display_area_payload,
    parse_first_u32,
    parse_open_realm_challenge,
)
from tobii.usb_transport import (  # noqa: E402
    BaseUsbTransport,
    UsbTransportError,
    create_usb_transport,
)


def _corners_approx_equal(
    a: DisplayAreaCorners,
    b: DisplayAreaCorners,
    tol_mm: float = 0.5,
) -> bool:
    for pa, pb in zip(a, b):
        for va, vb in zip(pa, pb):
            if abs(va - vb) > tol_mm:
                return False
    return True


@dataclass
class UiTelemetrySnapshot:
    """Thread-safe snapshot of recent primary channels for the Studio verification GUI."""

    latest_gaze: Optional[GazeSample]
    latest_face: Optional[FaceSample]
    latest_nir_gray: Optional[np.ndarray]
    gaze_history: List[Tuple[float, float, float, float, float]]
    face_history: List[Tuple[float, float, float, float, float, float]]


class TobiiDirectClient:
    """Manages direct USB TTP streaming (`0x0500`, `0x050e`, `0x1771`) and MediaPipe worker."""

    def __init__(
        self,
        on_gaze_sample: Optional[Callable[[float, GazeSample], None]] = None,
        on_face_sample: Optional[Callable[[float, FaceSample], None]] = None,
        ir_fps: float = 30.0,
        model_path: Optional[str] = None,
        history_seconds: float = 4.0,
        calibration_path: Optional[str] = None,
        transport_factory: Optional[Callable[[], BaseUsbTransport]] = None,
        extractor_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._on_gaze_sample = on_gaze_sample
        self._on_face_sample = on_face_sample
        self._ir_fps = max(1.0, float(ir_fps))
        self._model_path = model_path
        self._history_seconds = max(1.0, float(history_seconds))
        self._calibration_path = calibration_path
        self._transport_factory = transport_factory or create_usb_transport
        self._extractor_factory = extractor_factory

        self._state_lock = threading.Lock()
        self._clock_lock = threading.Lock()
        self._clock_sync = TobiiClockSynchronizer()

        self._running = False
        self._ui_visible = True
        self._connection_error = ''

        self._active_profile: Optional[DisplayAreaProfile] = load_calibration_profile(
            self._calibration_path
        )
        self._original_device_display_area: Optional[DisplayAreaCorners] = None
        self._display_area_overridden = False
        self._cmd_queue: queue.Queue[
            Tuple[
                DisplayAreaCorners,
                Optional[DisplayAreaCorners],
                bool,
                threading.Event,
                List[str],
            ]
        ] = queue.Queue()

        self._gaze_count = 0
        self._nir_count = 0
        self._face_count = 0
        self._gaze_hz = 0.0
        self._face_hz = 0.0
        self._last_gaze_mono = 0.0
        self._last_face_mono = 0.0

        self._latest_gaze: Optional[GazeSample] = None
        self._latest_face: Optional[FaceSample] = None
        self._latest_nir_gray: Optional[np.ndarray] = None
        self._raw_gaze_ring: Deque[Tuple[float, GazeSample]] = deque(maxlen=400)
        self._gaze_history: Deque[Tuple[float, float, float, float, float]] = deque(maxlen=600)
        self._face_history: Deque[Tuple[float, float, float, float, float, float]] = deque(
            maxlen=300
        )

        self._reader_thread: Optional[threading.Thread] = None
        self._face_worker: Optional[MediaPipeFaceWorker] = None

    @property
    def is_running(self) -> bool:
        """Return True while the background USB reader thread is active."""
        with self._state_lock:
            return self._running

    @property
    def gaze_samples_received(self) -> int:
        """Return total `0x0500` gaze samples decoded since `start()`."""
        with self._state_lock:
            return self._gaze_count

    @property
    def nir_frames_received(self) -> int:
        """Return total `0x050e` NIR camera frames decoded since `start()`."""
        with self._state_lock:
            return self._nir_count

    @property
    def face_samples_received(self) -> int:
        """Return total MediaPipe `FaceSample` outputs produced since `start()`."""
        with self._state_lock:
            return self._face_count

    @property
    def gaze_hz(self) -> float:
        """Return smoothed `0x0500` gaze sample rate in Hz."""
        with self._state_lock:
            return self._gaze_hz

    @property
    def face_hz(self) -> float:
        """Return smoothed MediaPipe face inference rate in Hz."""
        with self._state_lock:
            return self._face_hz

    @property
    def mediapipe_error(self) -> str:
        """Return MediaPipe worker startup error if any."""
        worker = self._face_worker
        return worker.init_error if worker is not None else ''

    @property
    def active_calibration_profile(self) -> Optional[DisplayAreaProfile]:
        """Return currently saved/applied in-app `DisplayAreaProfile`, if any."""
        with self._state_lock:
            return self._active_profile

    @property
    def original_device_display_area(self) -> Optional[DisplayAreaCorners]:
        """Return the device's pre-session `GET_DISPLAY_AREA` corners read on connect."""
        with self._state_lock:
            return self._original_device_display_area

    def get_recent_gaze_samples(self, since_mono: float) -> List[GazeSample]:
        """Return full-rate `GazeSample` items received at or after `since_mono`."""
        with self._state_lock:
            return [sample for ts, sample in self._raw_gaze_ring if ts >= since_mono]

    def _enqueue_display_area(
        self,
        corners: DisplayAreaCorners,
        mark_overridden: bool,
        timeout_sec: float = 2.0,
        fallback_corners: Optional[DisplayAreaCorners] = None,
    ) -> None:
        if not self.is_running:
            return
        done_evt = threading.Event()
        err_box: List[str] = []
        self._cmd_queue.put((corners, fallback_corners, mark_overridden, done_evt, err_box))
        if not done_evt.wait(timeout=timeout_sec):
            raise UsbTransportError('Timed out applying SET_DISPLAY_AREA on USB stream')
        if err_box:
            raise UsbTransportError(err_box[0])

    def apply_temporary_display_area(
        self,
        corners: DisplayAreaCorners = WIDE_COLLECTION_PLANE,
        timeout_sec: float = 2.0,
    ) -> None:
        """Program a temporary display area (e.g. `WIDE_COLLECTION_PLANE`) on the live device."""
        self._enqueue_display_area(corners, mark_overridden=True, timeout_sec=timeout_sec)

    def save_and_apply_calibration(
        self,
        profile: DisplayAreaProfile,
        timeout_sec: float = 2.0,
    ) -> str:
        """Persist `profile` to disk and immediately apply its 3D corners to the EyeChip."""
        saved_path = save_calibration_profile(profile, self._calibration_path)
        with self._state_lock:
            self._active_profile = profile
        if self.is_running:
            self._enqueue_display_area(
                profile.corners,
                mark_overridden=True,
                timeout_sec=timeout_sec,
                fallback_corners=profile.scaled_corners_for_max_width(700.0),
            )
        return saved_path

    def restore_active_or_original_display_area(self, timeout_sec: float = 2.0) -> None:
        """Restore active calibration profile (or pre-session device display area) after cancel."""
        with self._state_lock:
            prof = self._active_profile
            orig = self._original_device_display_area
        if prof is not None:
            self._enqueue_display_area(
                prof.corners,
                mark_overridden=True,
                timeout_sec=timeout_sec,
                fallback_corners=prof.scaled_corners_for_max_width(700.0),
            )
        elif orig is not None:
            self._enqueue_display_area(orig, mark_overridden=False, timeout_sec=timeout_sec)

    def clear_saved_calibration(self, timeout_sec: float = 2.0) -> bool:
        """Delete saved calibration profile and restore the tracker's original display area."""
        removed = delete_calibration_profile(self._calibration_path)
        with self._state_lock:
            self._active_profile = None
            orig = self._original_device_display_area
        if self.is_running and orig is not None:
            self._enqueue_display_area(orig, mark_overridden=False, timeout_sec=timeout_sec)
        return removed

    def pop_connection_error(self) -> str:
        """Pop and clear the latest asynchronous USB connection error message."""
        with self._state_lock:
            err = self._connection_error
            self._connection_error = ''
            return err

    def set_ir_fps(self, fps: float) -> None:
        """Update maximum NIR frame rate fed into MediaPipe FaceLandmarker."""
        clean_fps = max(1.0, float(fps))
        with self._state_lock:
            self._ir_fps = clean_fps
            worker = self._face_worker
        if worker is not None:
            worker.set_max_fps(clean_fps)

    def set_ui_visible(self, visible: bool) -> None:
        """Enable or disable UI-only frame & landmark retention (zero UI CPU when hidden)."""
        vis = bool(visible)
        with self._state_lock:
            self._ui_visible = vis
            if not vis:
                self._latest_nir_gray = None
            worker = self._face_worker
        if worker is not None:
            worker.set_ui_visible(vis)

    def get_ui_snapshot(self) -> UiTelemetrySnapshot:
        """Return a thread-safe snapshot for GUI rendering."""
        cutoff = time.monotonic() - self._history_seconds
        with self._state_lock:
            gaze_hist = [row for row in self._gaze_history if row[0] >= cutoff]
            face_hist = [row for row in self._face_history if row[0] >= cutoff]
            return UiTelemetrySnapshot(
                latest_gaze=self._latest_gaze,
                latest_face=self._latest_face,
                latest_nir_gray=self._latest_nir_gray,
                gaze_history=gaze_hist,
                face_history=face_hist,
            )

    def start(self) -> None:
        """Open direct USB transport, launch MediaPipe worker, and start TTP streaming."""
        self.stop()

        transport = self._transport_factory()
        transport.open()

        with self._clock_lock:
            self._clock_sync.reset()

        with self._state_lock:
            self._running = True
            self._connection_error = ''
            self._original_device_display_area = None
            self._display_area_overridden = False
            self._gaze_count = 0
            self._nir_count = 0
            self._face_count = 0
            self._gaze_hz = 0.0
            self._face_hz = 0.0
            self._last_gaze_mono = 0.0
            self._last_face_mono = 0.0
            self._latest_gaze = None
            self._latest_face = None
            self._latest_nir_gray = None
            self._raw_gaze_ring.clear()
            self._gaze_history.clear()
            self._face_history.clear()
            ui_vis = self._ui_visible
            ir_fps = self._ir_fps

        while not self._cmd_queue.empty():
            try:
                _, _, _, evt, err_box = self._cmd_queue.get_nowait()
                err_box.append('Client restarted')
                evt.set()
            except queue.Empty:
                break

        worker = MediaPipeFaceWorker(
            on_face_sample=self._handle_worker_face_sample,
            max_fps=ir_fps,
            model_path=self._model_path,
            extractor_factory=self._extractor_factory,
        )
        worker.set_ui_visible(ui_vis)
        worker.start()

        with self._state_lock:
            self._face_worker = worker

        self._reader_thread = threading.Thread(
            target=self._usb_reader_loop,
            args=(transport,),
            daemon=True,
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Stop USB reader thread, close USB session (`0x42`), and stop MediaPipe worker."""
        with self._state_lock:
            self._running = False
            worker = self._face_worker
            self._face_worker = None

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.5)
            self._reader_thread = None

        if worker is not None:
            worker.stop()

    def _usb_reader_loop(self, transport: BaseUsbTransport) -> None:
        parser = TtpStreamParser()
        session_opened = False
        next_seq = 1
        try:
            transport.control_transfer(0x41, 0x41, 0, 0, b'', timeout_ms=1000)
            session_opened = True

            next_seq = self._perform_native_handshake(transport, parser)
            transport.bulk_write(
                TOBII_EP_OUT,
                build_subscribe_packet(next_seq, STREAM_IMAGE_PRIMARY),
                timeout_ms=1000,
            )
            next_seq += 1
            transport.bulk_write(
                TOBII_EP_OUT,
                build_subscribe_packet(next_seq, STREAM_SYNC),
                timeout_ms=1000,
            )
            next_seq += 1

            consecutive_timeouts = 0
            while self.is_running:
                while not self._cmd_queue.empty():
                    try:
                        corners, fb_corners, mark_ovr, done_evt, err_box = (
                            self._cmd_queue.get_nowait()
                        )
                    except queue.Empty:
                        break
                    try:
                        next_seq = self._apply_display_area_with_fallback(
                            transport, parser, next_seq, corners, fb_corners
                        )
                        with self._state_lock:
                            self._display_area_overridden = mark_ovr
                    except Exception as cmd_exc:
                        err_box.append(str(cmd_exc))
                    finally:
                        done_evt.set()

                chunk = transport.bulk_read(TOBII_EP_IN, max_len=32768, timeout_ms=80)
                rx_unix = time.time()
                if not chunk:
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= 35:
                        transport.bulk_write(
                            TOBII_EP_OUT,
                            build_subscribe_packet(next_seq, STREAM_GAZE),
                            timeout_ms=1000,
                        )
                        next_seq += 1
                        consecutive_timeouts = 0
                    continue

                consecutive_timeouts = 0
                parser.feed(chunk)
                for frame in parser.pop_frames():
                    if frame.magic == TTP_MAGIC_NOTIFY:
                        self._dispatch_notify_frame(frame, rx_unix)

        except Exception as exc:
            if self.is_running:
                with self._state_lock:
                    self._connection_error = str(exc)
        finally:
            while not self._cmd_queue.empty():
                try:
                    _, _, _, evt, err_box = self._cmd_queue.get_nowait()
                    err_box.append('USB stream stopped')
                    evt.set()
                except queue.Empty:
                    break
            with self._state_lock:
                self._running = False
                restore_da = (
                    self._original_device_display_area
                    if self._display_area_overridden
                    else None
                )
                self._display_area_overridden = False
            if session_opened:
                if restore_da is not None:
                    try:
                        transport.bulk_write(
                            TOBII_EP_OUT,
                            build_set_display_area_packet(next_seq, *restore_da),
                            timeout_ms=500,
                        )
                        next_seq += 1
                        transport.bulk_read(TOBII_EP_IN, max_len=16384, timeout_ms=60)
                    except Exception:
                        pass
                try:
                    transport.control_transfer(0x41, 0x42, 0, 0, b'', timeout_ms=500)
                except Exception:
                    pass
            try:
                transport.close()
            except Exception:
                pass

    def _apply_display_area_with_fallback(
        self,
        transport: BaseUsbTransport,
        parser: TtpStreamParser,
        seq: int,
        corners: DisplayAreaCorners,
        fallback_corners: Optional[DisplayAreaCorners] = None,
    ) -> int:
        candidates: List[DisplayAreaCorners] = [corners]
        if fallback_corners is not None and not _corners_approx_equal(
            corners, fallback_corners, tol_mm=1.0
        ):
            candidates.append(fallback_corners)
            # Also prepare a 550 mm (~25") desktop-width homothetic step in case firmware caps at 27"
            with self._state_lock:
                prof = self._active_profile
            if prof is not None:
                c550 = prof.scaled_corners_for_max_width(550.0)
                if not _corners_approx_equal(fallback_corners, c550, tol_mm=1.0):
                    candidates.append(c550)

        for idx, cand in enumerate(candidates):
            self._send_set_display_area(transport, parser, seq, cand)
            seq += 1
            if idx == len(candidates) - 1:
                break
            try:
                da_rsp = self._send_and_wait_rsp(
                    transport,
                    parser,
                    build_get_display_area_packet(seq),
                    seq,
                    'GET_DISPLAY_AREA',
                    timeout_sec=0.8,
                )
                seq += 1
                readback = parse_display_area_payload(da_rsp.payload)
                if readback is None or _corners_approx_equal(readback, cand, tol_mm=5.0):
                    break
            except UsbTransportError:
                seq += 1
                break
        return seq

    def _send_set_display_area(
        self,
        transport: BaseUsbTransport,
        parser: TtpStreamParser,
        seq: int,
        corners: DisplayAreaCorners,
        wait_sec: float = 0.25,
    ) -> None:
        packet = build_set_display_area_packet(seq, *corners)
        transport.bulk_write(TOBII_EP_OUT, packet, timeout_ms=1000)
        deadline = time.monotonic() + wait_sec
        while self.is_running and time.monotonic() < deadline:
            chunk = transport.bulk_read(TOBII_EP_IN, max_len=16384, timeout_ms=50)
            rx_unix = time.time()
            if chunk:
                parser.feed(chunk)
            for frame in parser.pop_frames():
                if frame.magic == TTP_MAGIC_RSP and frame.seq == seq:
                    return
                if frame.magic == TTP_MAGIC_NOTIFY:
                    self._dispatch_notify_frame(frame, rx_unix)

    def _send_and_wait_rsp(
        self,
        transport: BaseUsbTransport,
        parser: TtpStreamParser,
        packet: bytes,
        seq: int,
        name: str,
        timeout_sec: float = 2.0,
    ) -> TtpFrame:
        transport.bulk_write(TOBII_EP_OUT, packet, timeout_ms=1000)
        deadline = time.monotonic() + timeout_sec
        while self.is_running and time.monotonic() < deadline:
            chunk = transport.bulk_read(TOBII_EP_IN, max_len=16384, timeout_ms=100)
            rx_unix = time.time()
            if chunk:
                parser.feed(chunk)
            for frame in parser.pop_frames():
                if frame.magic == TTP_MAGIC_RSP and frame.seq == seq:
                    return frame
                if frame.magic == TTP_MAGIC_NOTIFY:
                    self._dispatch_notify_frame(frame, rx_unix)
        raise UsbTransportError(f'Timed out waiting for TTP {name} response (seq={seq})')

    def _perform_native_handshake(
        self,
        transport: BaseUsbTransport,
        parser: TtpStreamParser,
    ) -> int:
        seq = 1
        self._send_and_wait_rsp(transport, parser, build_hello_packet(seq), seq, 'HELLO')
        seq += 1

        q_rsp = self._send_and_wait_rsp(
            transport, parser, build_query_realm_packet(seq), seq, 'QUERY_REALM'
        )
        realm_type = parse_first_u32(q_rsp.payload)
        seq += 1

        o_rsp = self._send_and_wait_rsp(
            transport, parser, build_open_realm_packet(seq, realm_type), seq, 'OPEN_REALM'
        )
        seq += 1

        if realm_type != 0:
            parsed = parse_open_realm_challenge(o_rsp.payload)
            if parsed is None:
                raise UsbTransportError('Failed to parse OPEN_REALM challenge payload')
            realm_id, field_210, challenge = parsed
            digest = compute_realm_hmac(challenge)
            self._send_and_wait_rsp(
                transport,
                parser,
                build_realm_response_packet(seq, realm_id, field_210, digest),
                seq,
                'REALM_RESPONSE',
            )
            seq += 1

        transport.bulk_write(
            TOBII_EP_OUT, build_subscribe_packet(seq, STREAM_GAZE), timeout_ms=1000
        )
        seq += 1

        try:
            da_rsp = self._send_and_wait_rsp(
                transport,
                parser,
                build_get_display_area_packet(seq),
                seq,
                'GET_DISPLAY_AREA',
                timeout_sec=1.0,
            )
            parsed_da = parse_display_area_payload(da_rsp.payload)
            if parsed_da is not None and not _corners_approx_equal(
                parsed_da, WIDE_COLLECTION_PLANE
            ):
                with self._state_lock:
                    self._original_device_display_area = parsed_da
        except UsbTransportError:
            pass
        seq += 1

        with self._state_lock:
            saved_profile = self._active_profile
        if saved_profile is not None:
            seq = self._apply_display_area_with_fallback(
                transport,
                parser,
                seq,
                saved_profile.corners,
                saved_profile.scaled_corners_for_max_width(700.0),
            )
            with self._state_lock:
                self._display_area_overridden = True

        transport.bulk_write(
            TOBII_EP_OUT, build_subscribe_packet(seq, STREAM_GAZE), timeout_ms=1000
        )
        seq += 1
        return seq

    def _dispatch_notify_frame(self, frame: TtpFrame, rx_unix_sec: float) -> None:
        stream_id = frame.op & 0xFFFF
        if stream_id == STREAM_GAZE:
            sample = decode_gaze_sample(frame.payload)
            if sample is None:
                return
            with self._clock_lock:
                if sample.has_timestamp and sample.timestamp_us > 0:
                    self._clock_sync.observe(sample.timestamp_us, rx_unix_sec)
                    sample_unix = self._clock_sync.device_ts_to_unix_sec(
                        sample.timestamp_us, rx_unix_sec
                    )
                else:
                    sample_unix = rx_unix_sec

            now_mono = time.monotonic()
            with self._state_lock:
                self._gaze_count += 1
                if self._last_gaze_mono > 0.0:
                    dt = now_mono - self._last_gaze_mono
                    if 0.001 < dt < 1.0:
                        inst = 1.0 / dt
                        self._gaze_hz = (
                            inst if self._gaze_hz <= 0.0 else 0.9 * self._gaze_hz + 0.1 * inst
                        )
                self._last_gaze_mono = now_mono
                self._latest_gaze = sample
                self._raw_gaze_ring.append((now_mono, sample))
                gx = sample.gaze_x if sample.gaze_valid else float('nan')
                gy = sample.gaze_y if sample.gaze_valid else float('nan')
                pl = (
                    sample.pupil_l_mm
                    if (sample.validity_l and sample.pupil_l_mm > 0.5)
                    else float('nan')
                )
                pr = (
                    sample.pupil_r_mm
                    if (sample.validity_r and sample.pupil_r_mm > 0.5)
                    else float('nan')
                )
                self._gaze_history.append((now_mono, gx, gy, pl, pr))

            if self._on_gaze_sample is not None:
                self._on_gaze_sample(sample_unix, sample)

        elif stream_id == STREAM_IMAGE_PRIMARY:
            nir_frame: Optional[NirImageFrame] = decode_image_payload(stream_id, frame.payload)
            if nir_frame is None:
                return
            with self._clock_lock:
                if nir_frame.timestamp_us > 0:
                    self._clock_sync.observe(nir_frame.timestamp_us, rx_unix_sec)

            with self._state_lock:
                self._nir_count += 1
                if self._ui_visible:
                    self._latest_nir_gray = nir_frame.gray
                worker = self._face_worker

            if worker is not None:
                worker.submit_frame(nir_frame.gray, nir_frame.timestamp_us, rx_unix_sec)

        elif stream_id == STREAM_SYNC:
            sync = decode_sync_payload(frame.payload)
            if sync is not None and sync.device_timestamp_us > 0:
                with self._clock_lock:
                    self._clock_sync.observe(sync.device_timestamp_us, rx_unix_sec)

    def _handle_worker_face_sample(self, sample: FaceSample) -> None:
        with self._clock_lock:
            shutter_unix = self._clock_sync.device_ts_to_unix_sec(
                sample.timestamp_us, sample.host_unix_sec
            )

        now_mono = time.monotonic()
        with self._state_lock:
            self._face_count += 1
            if self._last_face_mono > 0.0:
                dt = now_mono - self._last_face_mono
                if 0.002 < dt < 2.0:
                    inst = 1.0 / dt
                    self._face_hz = (
                        inst if self._face_hz <= 0.0 else 0.85 * self._face_hz + 0.15 * inst
                    )
            self._last_face_mono = now_mono
            self._latest_face = sample
            self._face_history.append((
                now_mono,
                sample.eye_blink_left,
                sample.eye_blink_right,
                sample.head_yaw_deg,
                sample.head_pitch_deg,
                sample.head_roll_deg,
            ))

        if self._on_face_sample is not None:
            self._on_face_sample(shutter_unix, sample)
