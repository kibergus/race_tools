"""Background MediaPipe Face Landmarker worker for 8-bit NIR camera frames (`0x050e`).

Extracts strictly primary neural-network outputs (6-DoF camera-space pose matrix
and direct ocular/jaw blendshape scores) with zero derived channels.
Adapted under Apache License 2.0 from `njmill/tobii-linux` (`mediapipe-face-worker.py`).
"""

from dataclasses import dataclass, field
import hashlib
import math
import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
import urllib.request
import numpy as np


MODEL_FILENAME = 'face_landmarker.task'
MODEL_URL = (
    'https://storage.googleapis.com/mediapipe-models/'
    'face_landmarker/face_landmarker/float16/1/face_landmarker.task'
)
MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'

# Lightweight subset of landmark indices rendered ONLY when Studio UI is visible
UI_PREVIEW_LANDMARK_INDICES: Tuple[int, ...] = (
    # Left eye contour
    33, 160, 158, 133, 153, 144,
    # Right eye contour
    263, 387, 385, 362, 380, 373,
    # Nose bridge & tip + chin + forehead + temples
    10, 168, 1, 152, 234, 454,
)


@dataclass
class FaceSample:
    """Primary outputs from Google MediaPipe Face Landmarker on a single NIR frame."""

    timestamp_us: int
    host_unix_sec: float
    present: bool = False
    latency_ms: float = 0.0

    head_pos_x_cm: float = float('nan')
    head_pos_y_cm: float = float('nan')
    head_pos_z_cm: float = float('nan')

    head_yaw_deg: float = float('nan')
    head_pitch_deg: float = float('nan')
    head_roll_deg: float = float('nan')

    eye_blink_left: float = float('nan')
    eye_blink_right: float = float('nan')
    eye_squint_left: float = float('nan')
    eye_squint_right: float = float('nan')
    eye_wide_left: float = float('nan')
    eye_wide_right: float = float('nan')
    jaw_open: float = float('nan')

    # Populated ONLY when UI window is mapped/visible; empty otherwise to save CPU
    ui_landmarks_xy: List[Tuple[float, float]] = field(default_factory=list)

    def to_brt1_row(self, session_elapsed_sec: float) -> List[float]:
        """Convert primary MediaPipe outputs into the 14-channel `tobii_face` BRT1 row."""
        return [
            float(session_elapsed_sec),
            self.head_pos_x_cm,
            self.head_pos_y_cm,
            self.head_pos_z_cm,
            self.head_yaw_deg,
            self.head_pitch_deg,
            self.head_roll_deg,
            self.eye_blink_left,
            self.eye_blink_right,
            self.eye_squint_left,
            self.eye_squint_right,
            self.eye_wide_left,
            self.eye_wide_right,
            self.jaw_open,
        ]


def default_model_path() -> str:
    """Return canonical path `tobii/models/face_landmarker.task`."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, 'models', MODEL_FILENAME)


def verify_sha256(file_path: str, expected_hex: str = MODEL_SHA256) -> bool:
    """Verify SHA-256 digest of `file_path`."""
    if not os.path.isfile(file_path):
        return False
    h = hashlib.sha256()
    with open(file_path, 'rb') as f_in:
        while True:
            chunk = f_in.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower() == expected_hex.lower()


def ensure_face_landmarker_model(model_path: Optional[str] = None) -> str:
    """Locate or download and SHA-256 verify `face_landmarker.task`."""
    target = model_path or default_model_path()
    if os.path.isfile(target) and verify_sha256(target):
        return target

    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp_path = f'{target}.tmp'
    req = urllib.request.Request(
        MODEL_URL,
        headers={'User-Agent': 'BrBrDbTelemetry/1.0'},
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp, open(tmp_path, 'wb') as f_out:
        while True:
            block = resp.read(65536)
            if not block:
                break
            f_out.write(block)

    if not verify_sha256(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise RuntimeError('Downloaded face_landmarker.task failed SHA-256 verification')

    os.replace(tmp_path, target)
    return target


def decompose_pose_matrix_forward(matrix_4x4: np.ndarray) -> Tuple[float, float, float, float, float, float]:
    """Extract `(tx_cm, ty_cm, tz_cm, yaw_deg, pitch_deg, roll_deg)` from 4x4 face matrix.

    Uses the face-forward basis vector `(R[0,2], R[1,2], R[2,2])` from `njmill/tobii-linux`
    so head pitch remains observable even during large yaw angles on the ET5 NIR sensor.
    """
    r00 = float(matrix_4x4[0, 0])
    r10 = float(matrix_4x4[1, 0])
    forward_x = float(matrix_4x4[0, 2])
    forward_y = float(matrix_4x4[1, 2])
    forward_z = float(matrix_4x4[2, 2])

    tx = float(matrix_4x4[0, 3])
    ty = float(matrix_4x4[1, 3])
    tz = float(matrix_4x4[2, 3])

    yaw = math.degrees(math.atan2(forward_x, forward_z))
    pitch = math.degrees(math.atan2(forward_y, math.hypot(forward_x, forward_z)))
    roll = math.degrees(math.atan2(r10, r00))
    return tx, ty, tz, yaw, pitch, roll


class MediaPipeFaceExtractor:
    """Synchronous MediaPipe FaceLandmarker wrapper for 8-bit NIR frames."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        use_clahe: bool = True,
        min_confidence: float = 0.35,
    ) -> None:
        import cv2
        import mediapipe as mp

        resolved_model = ensure_face_landmarker_model(model_path)
        self._cv2 = cv2
        self._mp = mp
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if use_clahe else None
        self._last_ts_ms = 0

        base_options = mp.tasks.BaseOptions(model_asset_path=resolved_model)
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_confidence,
            min_face_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    def close(self) -> None:
        """Release underlying MediaPipe C++ graph resources."""
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def process_nir_frame(
        self,
        gray: np.ndarray,
        timestamp_us: int,
        host_unix_sec: float,
        include_ui_landmarks: bool = False,
    ) -> FaceSample:
        """Run FaceLandmarker on a single 8-bit grayscale NIR frame."""
        t0 = time.perf_counter()
        if self._clahe is not None:
            enhanced = self._clahe.apply(gray)
        else:
            enhanced = gray
        rgb = np.ascontiguousarray(
            self._cv2.cvtColor(enhanced, self._cv2.COLOR_GRAY2RGB)
        )

        ts_ms = max(int(timestamp_us // 1000), self._last_ts_ms + 1)
        self._last_ts_ms = ts_ms

        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(mp_image, ts_ms)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        sample = FaceSample(
            timestamp_us=int(timestamp_us),
            host_unix_sec=float(host_unix_sec),
            present=bool(result.face_landmarks),
            latency_ms=latency_ms,
        )
        if not sample.present:
            return sample

        matrices = getattr(result, 'facial_transformation_matrixes', None) or []
        if matrices:
            mat = np.asarray(matrices[0], dtype=np.float64).reshape((4, 4))
            (
                sample.head_pos_x_cm,
                sample.head_pos_y_cm,
                sample.head_pos_z_cm,
                sample.head_yaw_deg,
                sample.head_pitch_deg,
                sample.head_roll_deg,
            ) = decompose_pose_matrix_forward(mat)

        if result.face_blendshapes:
            scores: Dict[str, float] = {
                b.category_name: float(b.score) for b in result.face_blendshapes[0]
            }
            sample.eye_blink_left = scores.get('eyeBlinkLeft', float('nan'))
            sample.eye_blink_right = scores.get('eyeBlinkRight', float('nan'))
            sample.eye_squint_left = scores.get('eyeSquintLeft', float('nan'))
            sample.eye_squint_right = scores.get('eyeSquintRight', float('nan'))
            sample.eye_wide_left = scores.get('eyeWideLeft', float('nan'))
            sample.eye_wide_right = scores.get('eyeWideRight', float('nan'))
            sample.jaw_open = scores.get('jawOpen', float('nan'))

        if include_ui_landmarks:
            lms = result.face_landmarks[0]
            h, w = gray.shape[:2]
            pts: List[Tuple[float, float]] = []
            for idx in UI_PREVIEW_LANDMARK_INDICES:
                if idx < len(lms):
                    pts.append((float(lms[idx].x) * w, float(lms[idx].y) * h))
            sample.ui_landmarks_xy = pts

        return sample


class MediaPipeFaceWorker:
    """Drop-oldest single-slot background worker for real-time NIR MediaPipe inference."""

    def __init__(
        self,
        on_face_sample: Callable[[FaceSample], None],
        max_fps: float = 30.0,
        model_path: Optional[str] = None,
        extractor_factory: Optional[Callable[[], MediaPipeFaceExtractor]] = None,
    ) -> None:
        self._on_face_sample = on_face_sample
        self._min_interval_sec = 1.0 / max(1.0, float(max_fps))
        self._model_path = model_path
        self._extractor_factory = extractor_factory

        self._cond = threading.Condition()
        self._pending: Optional[Tuple[np.ndarray, int, float]] = None
        self._running = False
        self._ui_visible = False
        self._last_submit_mono = 0.0
        self._thread: Optional[threading.Thread] = None
        self._init_error: str = ''

    @property
    def init_error(self) -> str:
        """Return non-empty error string if MediaPipe initialization failed."""
        return self._init_error

    def set_max_fps(self, max_fps: float) -> None:
        """Adjust maximum NIR frame inference rate dynamically."""
        with self._cond:
            self._min_interval_sec = 1.0 / max(1.0, float(max_fps))

    def set_ui_visible(self, visible: bool) -> None:
        """Gate UI-only 2D landmark coordinate extraction on window visibility."""
        with self._cond:
            self._ui_visible = bool(visible)

    def start(self) -> None:
        """Launch the background MediaPipe worker thread."""
        with self._cond:
            if self._running:
                return
            self._running = True
            self._init_error = ''
            self._pending = None
            self._thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop worker thread and release MediaPipe resources."""
        with self._cond:
            self._running = False
            self._pending = None
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def submit_frame(self, gray: np.ndarray, timestamp_us: int, host_unix_sec: float) -> None:
        """Submit latest NIR frame (throttled to `max_fps`, replacing any unprocessed frame)."""
        now_mono = time.monotonic()
        with self._cond:
            if not self._running:
                return
            if (now_mono - self._last_submit_mono) < self._min_interval_sec:
                return
            self._last_submit_mono = now_mono
            self._pending = (gray, int(timestamp_us), float(host_unix_sec))
            self._cond.notify()

    def _worker_loop(self) -> None:
        extractor: Optional[MediaPipeFaceExtractor] = None
        try:
            if self._extractor_factory is not None:
                extractor = self._extractor_factory()
            else:
                extractor = MediaPipeFaceExtractor(model_path=self._model_path)
        except Exception as exc:
            self._init_error = str(exc)
            return

        try:
            while True:
                with self._cond:
                    while self._running and self._pending is None:
                        self._cond.wait(timeout=0.5)
                    if not self._running:
                        break
                    item = self._pending
                    self._pending = None
                    ui_vis = self._ui_visible

                if item is None:
                    continue
                gray, ts_us, host_sec = item
                try:
                    sample = extractor.process_nir_frame(
                        gray,
                        timestamp_us=ts_us,
                        host_unix_sec=host_sec,
                        include_ui_landmarks=ui_vis,
                    )
                    self._on_face_sample(sample)
                except Exception:
                    continue
        finally:
            if extractor is not None:
                extractor.close()
