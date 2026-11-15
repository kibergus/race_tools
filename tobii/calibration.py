"""In-app 3D screen plane & personal gaze calibration for direct-USB Tobii Eye Tracker 5.

Supports arbitrary off-screen sim-rig geometry (e.g. tracker mounted above a steering wheel
tilted upward, with a TV/monitor positioned much further behind it) as well as personal
foveal (`kappa`) offset and horizontal/vertical gain alignment. The solved 3D display plane
`(Top-Left, Top-Right, Bottom-Left)` is programmed directly into the EyeChip hardware via
TTP `SET_DISPLAY_AREA` (`0x05a0`), keeping recorded telemetry strictly primary-only.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
import json
import math
import os
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares

from tobii.ttp_protocol import DisplayAreaCorners, GazeSample


# Hardware-verified 1000 mm x 500 mm reference plane at Z = 0 mm (`tobii-ttp-mux` `--display-area=big`).
# EyeChip outputs unclamped signed Q22.42 `(gaze_x, gaze_y)` relative to this plane for all gaze angles.
WIDE_COLLECTION_PLANE: DisplayAreaCorners = (
    (-500.0, 500.0, 0.0),
    (500.0, 500.0, 0.0),
    (-500.0, 0.0, 0.0),
)

# 9-point calibration sequence starting at screen center and looping smoothly clockwise.
DEFAULT_CALIBRATION_TARGETS: Tuple[Tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.10, 0.10),
    (0.50, 0.10),
    (0.90, 0.10),
    (0.90, 0.50),
    (0.90, 0.90),
    (0.50, 0.90),
    (0.10, 0.90),
    (0.10, 0.50),
)


@dataclass
class CalibrationObservation:
    """Aggregated 3D eye origin and unit gaze ray measured while fixating on `(target_u, target_v)`."""

    target_u: float
    target_v: float
    eye_origin_mm: np.ndarray
    unit_ray_dir: np.ndarray
    sample_count: int = 1


@dataclass
class DisplayAreaProfile:
    """Solved 3D EyeChip display-area configuration persisted across sessions."""

    tl_xyz: Tuple[float, float, float]
    tr_xyz: Tuple[float, float, float]
    bl_xyz: Tuple[float, float, float]
    screen_diagonal_in: float
    monitor_index: int = 0
    screen_width_px: int = 1920
    screen_height_px: int = 1080
    rms_error_norm: float = 0.0
    estimated_eye_to_screen_cm: float = 0.0
    estimated_screen_z_cm: float = 0.0
    mean_eye_xyz: Tuple[float, float, float] = (0.0, 50.0, 600.0)
    inlier_targets: int = 0
    total_targets: int = 0
    calibrated_at_iso: str = ''

    @property
    def corners(self) -> DisplayAreaCorners:
        """Return `(tl_xyz, tr_xyz, bl_xyz)` tuple in EyeChip millimeters."""
        return (self.tl_xyz, self.tr_xyz, self.bl_xyz)

    def scaled_corners_for_max_width(self, max_width_mm: float = 700.0) -> DisplayAreaCorners:
        """Scale plane homothetically toward `mean_eye_xyz` so width <= `max_width_mm`."""
        tl = np.array(self.tl_xyz, dtype=np.float64)
        tr = np.array(self.tr_xyz, dtype=np.float64)
        bl = np.array(self.bl_xyz, dtype=np.float64)
        w_mm = float(np.linalg.norm(tr - tl))
        if w_mm <= max_width_mm or w_mm < 1.0:
            return self.corners
        scale = float(max_width_mm) / w_mm
        eye = np.array(self.mean_eye_xyz, dtype=np.float64)
        tl_s = eye + scale * (tl - eye)
        tr_s = eye + scale * (tr - eye)
        bl_s = eye + scale * (bl - eye)
        return (
            (float(tl_s[0]), float(tl_s[1]), float(tl_s[2])),
            (float(tr_s[0]), float(tr_s[1]), float(tr_s[2])),
            (float(bl_s[0]), float(bl_s[1]), float(bl_s[2])),
        )

    def to_dict(self) -> dict:
        """Serialize profile to a JSON-compatible dictionary."""
        return {
            'version': 1,
            'tl_xyz_mm': list(self.tl_xyz),
            'tr_xyz_mm': list(self.tr_xyz),
            'bl_xyz_mm': list(self.bl_xyz),
            'screen_diagonal_in': float(self.screen_diagonal_in),
            'monitor_index': int(self.monitor_index),
            'screen_width_px': int(self.screen_width_px),
            'screen_height_px': int(self.screen_height_px),
            'rms_error_norm': float(self.rms_error_norm),
            'estimated_eye_to_screen_cm': float(self.estimated_eye_to_screen_cm),
            'estimated_screen_z_cm': float(self.estimated_screen_z_cm),
            'mean_eye_xyz_mm': list(self.mean_eye_xyz),
            'inlier_targets': int(self.inlier_targets),
            'total_targets': int(self.total_targets),
            'calibrated_at_iso': self.calibrated_at_iso,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Optional['DisplayAreaProfile']:
        """Deserialize and validate a `DisplayAreaProfile` from JSON dictionary."""
        try:
            tl_raw = data['tl_xyz_mm']
            tr_raw = data['tr_xyz_mm']
            bl_raw = data['bl_xyz_mm']
            if len(tl_raw) != 3 or len(tr_raw) != 3 or len(bl_raw) != 3:
                return None
            tl = (float(tl_raw[0]), float(tl_raw[1]), float(tl_raw[2]))
            tr = (float(tr_raw[0]), float(tr_raw[1]), float(tr_raw[2]))
            bl = (float(bl_raw[0]), float(bl_raw[1]), float(bl_raw[2]))
            if any(math.isnan(v) or math.isinf(v) for v in (*tl, *tr, *bl)):
                return None
            w_vec = np.array(tr, dtype=np.float64) - np.array(tl, dtype=np.float64)
            h_vec = np.array(bl, dtype=np.float64) - np.array(tl, dtype=np.float64)
            if float(np.linalg.norm(np.cross(w_vec, h_vec))) < 100.0:
                return None
            me_raw = data.get('mean_eye_xyz_mm', [0.0, 50.0, 600.0])
            if isinstance(me_raw, list) and len(me_raw) == 3:
                mean_eye = (float(me_raw[0]), float(me_raw[1]), float(me_raw[2]))
            else:
                mean_eye = (0.0, 50.0, 600.0)
            return cls(
                tl_xyz=tl,
                tr_xyz=tr,
                bl_xyz=bl,
                screen_diagonal_in=max(5.0, float(data.get('screen_diagonal_in', 32.0))),
                monitor_index=max(0, int(data.get('monitor_index', 0))),
                screen_width_px=max(100, int(data.get('screen_width_px', 1920))),
                screen_height_px=max(100, int(data.get('screen_height_px', 1080))),
                rms_error_norm=max(0.0, float(data.get('rms_error_norm', 0.0))),
                estimated_eye_to_screen_cm=float(data.get('estimated_eye_to_screen_cm', 0.0)),
                estimated_screen_z_cm=float(data.get('estimated_screen_z_cm', 0.0)),
                mean_eye_xyz=mean_eye,
                inlier_targets=max(0, int(data.get('inlier_targets', 0))),
                total_targets=max(0, int(data.get('total_targets', 0))),
                calibrated_at_iso=str(data.get('calibrated_at_iso', '')),
            )
        except (KeyError, TypeError, ValueError):
            return None


def default_calibration_path() -> str:
    """Return default JSON path (`tobii/calibration_profile.json`) for persisted calibration."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration_profile.json')


def load_calibration_profile(path: Optional[str] = None) -> Optional[DisplayAreaProfile]:
    """Load saved `DisplayAreaProfile` from disk if present and valid."""
    target = path or default_calibration_path()
    if not os.path.isfile(target):
        return None
    try:
        with open(target, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        return DisplayAreaProfile.from_dict(raw)
    except (OSError, json.JSONDecodeError):
        return None


def save_calibration_profile(profile: DisplayAreaProfile, path: Optional[str] = None) -> str:
    """Atomically write `DisplayAreaProfile` to JSON on disk and return the saved path."""
    target = path or default_calibration_path()
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    tmp_path = f'{target}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(profile.to_dict(), f, indent=2)
    os.replace(tmp_path, target)
    return target


def delete_calibration_profile(path: Optional[str] = None) -> bool:
    """Delete saved `calibration_profile.json` if it exists; return True if removed."""
    target = path or default_calibration_path()
    if not os.path.isfile(target):
        return False
    try:
        os.remove(target)
        return True
    except OSError:
        return False


def compute_screen_dimensions_mm(
    diagonal_inches: float,
    width_px: int,
    height_px: int,
) -> Tuple[float, float]:
    """Compute physical `(width_mm, height_mm)` from diagonal inches and pixel aspect ratio."""
    diag_mm = max(120.0, float(diagonal_inches) * 25.4)
    w_px = max(1.0, float(width_px))
    h_px = max(1.0, float(height_px))
    hypot_px = math.hypot(w_px, h_px)
    return diag_mm * (w_px / hypot_px), diag_mm * (h_px / hypot_px)


def _valid_eye_origins_flags(sample: GazeSample) -> Tuple[bool, bool]:
    any_gaze = sample.gaze_valid or sample.gaze_unfiltered_valid
    l_tracked = sample.validity_l or sample.eye_present_l or sample.gaze_l_valid or any_gaze
    r_tracked = sample.validity_r or sample.eye_present_r or sample.gaze_r_valid or any_gaze
    l_ok = (
        l_tracked
        and not math.isnan(sample.eye_l_x_mm)
        and not math.isnan(sample.eye_l_y_mm)
        and not math.isnan(sample.eye_l_z_mm)
        and abs(sample.eye_l_z_mm) > 20.0
    )
    r_ok = (
        r_tracked
        and not math.isnan(sample.eye_r_x_mm)
        and not math.isnan(sample.eye_r_y_mm)
        and not math.isnan(sample.eye_r_z_mm)
        and abs(sample.eye_r_z_mm) > 20.0
    )
    return l_ok, r_ok


def extract_eye_origin_mm(sample: GazeSample) -> Optional[np.ndarray]:
    """Extract cyclopean (binocular midpoint or valid monocular) 3D eye origin in millimeters."""
    l_ok, r_ok = _valid_eye_origins_flags(sample)
    if l_ok and r_ok:
        return np.array(
            [
                0.5 * (sample.eye_l_x_mm + sample.eye_r_x_mm),
                0.5 * (sample.eye_l_y_mm + sample.eye_r_y_mm),
                0.5 * (sample.eye_l_z_mm + sample.eye_r_z_mm),
            ],
            dtype=np.float64,
        )
    if l_ok:
        return np.array(
            [sample.eye_l_x_mm, sample.eye_l_y_mm, sample.eye_l_z_mm],
            dtype=np.float64,
        )
    if r_ok:
        return np.array(
            [sample.eye_r_x_mm, sample.eye_r_y_mm, sample.eye_r_z_mm],
            dtype=np.float64,
        )
    if sample.gaze_valid or sample.gaze_unfiltered_valid:
        return np.array([0.0, 50.0, 600.0], dtype=np.float64)
    return None


def _combined_gaze_uv(sample: GazeSample) -> Optional[Tuple[float, float]]:
    if (
        sample.gaze_unfiltered_valid
        and not math.isnan(sample.gaze_unfiltered_x)
        and not math.isnan(sample.gaze_unfiltered_y)
    ):
        return sample.gaze_unfiltered_x, sample.gaze_unfiltered_y
    if (
        sample.gaze_valid
        and not math.isnan(sample.gaze_x)
        and not math.isnan(sample.gaze_y)
    ):
        return sample.gaze_x, sample.gaze_y
    if (
        sample.gaze_l_valid
        and sample.gaze_r_valid
        and not math.isnan(sample.gaze_l_x)
        and not math.isnan(sample.gaze_r_x)
    ):
        return 0.5 * (sample.gaze_l_x + sample.gaze_r_x), 0.5 * (sample.gaze_l_y + sample.gaze_r_y)
    return None


def gaze_sample_to_3d_ray(
    sample: GazeSample,
    ref_plane: DisplayAreaCorners = WIDE_COLLECTION_PLANE,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Reconstruct `(eye_origin_mm, unit_ray_dir)` from a `GazeSample` on `ref_plane`."""
    l_ok, r_ok = _valid_eye_origins_flags(sample)
    uv_comb = _combined_gaze_uv(sample)

    origin: Optional[np.ndarray] = None
    uv: Optional[Tuple[float, float]] = None

    if l_ok and r_ok and uv_comb is not None:
        origin = np.array(
            [
                0.5 * (sample.eye_l_x_mm + sample.eye_r_x_mm),
                0.5 * (sample.eye_l_y_mm + sample.eye_r_y_mm),
                0.5 * (sample.eye_l_z_mm + sample.eye_r_z_mm),
            ],
            dtype=np.float64,
        )
        uv = uv_comb
    elif l_ok and not r_ok:
        # Pair single left eye with left-eye 2D gaze (`0x05`) when available to avoid IPD jump
        origin = np.array(
            [sample.eye_l_x_mm, sample.eye_l_y_mm, sample.eye_l_z_mm], dtype=np.float64
        )
        if sample.gaze_l_valid and not math.isnan(sample.gaze_l_x) and not math.isnan(sample.gaze_l_y):
            uv = (sample.gaze_l_x, sample.gaze_l_y)
        else:
            uv = uv_comb
    elif r_ok and not l_ok:
        # Pair single right eye with right-eye 2D gaze (`0x0B`) when available to avoid IPD jump
        origin = np.array(
            [sample.eye_r_x_mm, sample.eye_r_y_mm, sample.eye_r_z_mm], dtype=np.float64
        )
        if sample.gaze_r_valid and not math.isnan(sample.gaze_r_x) and not math.isnan(sample.gaze_r_y):
            uv = (sample.gaze_r_x, sample.gaze_r_y)
        else:
            uv = uv_comb
    else:
        origin = extract_eye_origin_mm(sample)
        if uv_comb is not None:
            uv = uv_comb
        elif sample.gaze_l_valid and not math.isnan(sample.gaze_l_x):
            uv = (sample.gaze_l_x, sample.gaze_l_y)
        elif sample.gaze_r_valid and not math.isnan(sample.gaze_r_x):
            uv = (sample.gaze_r_x, sample.gaze_r_y)

    if origin is None or uv is None:
        return None
    u_ref, v_ref = uv

    tl = np.array(ref_plane[0], dtype=np.float64)
    tr = np.array(ref_plane[1], dtype=np.float64)
    bl = np.array(ref_plane[2], dtype=np.float64)

    p_hit = tl + u_ref * (tr - tl) + v_ref * (bl - tl)
    ray_vec = p_hit - origin
    norm = float(np.linalg.norm(ray_vec))
    if norm < 10.0:
        return None
    return origin, ray_vec / norm


def project_ray_onto_display_area(
    eye_origin_mm: np.ndarray,
    unit_ray_dir: np.ndarray,
    corners: DisplayAreaCorners,
) -> Optional[Tuple[float, float, float]]:
    """Intersect 3D ray `eye_origin_mm + t * unit_ray_dir` with `corners`, returning `(u, v, t_mm)`."""
    tl = np.asarray(corners[0], dtype=np.float64)
    tr = np.asarray(corners[1], dtype=np.float64)
    bl = np.asarray(corners[2], dtype=np.float64)

    w_vec = tr - tl
    h_vec = bl - tl
    normal = np.cross(w_vec, h_vec)
    n_sq = float(np.dot(normal, normal))
    if n_sq < 1e-6:
        return None

    denom = float(np.dot(unit_ray_dir, normal))
    if abs(denom) < 1e-9:
        return None

    t_mm = float(np.dot(tl - eye_origin_mm, normal)) / denom
    delta = eye_origin_mm + t_mm * unit_ray_dir - tl
    u = float(np.dot(np.cross(delta, h_vec), normal)) / n_sq
    v = float(np.dot(np.cross(w_vec, delta), normal)) / n_sq
    return u, v, t_mm


def aggregate_target_samples(
    target_u: float,
    target_v: float,
    samples: Sequence[GazeSample],
    ref_plane: DisplayAreaCorners = WIDE_COLLECTION_PLANE,
    min_valid_samples: int = 8,
) -> Optional[CalibrationObservation]:
    """Aggregate fixation samples on `(target_u, target_v)` with MAD angular outlier rejection."""
    bino_origins: List[np.ndarray] = []
    bino_rays: List[np.ndarray] = []
    all_origins: List[np.ndarray] = []
    all_rays: List[np.ndarray] = []

    for sample in samples:
        parsed = gaze_sample_to_3d_ray(sample, ref_plane=ref_plane)
        if parsed is not None:
            all_origins.append(parsed[0])
            all_rays.append(parsed[1])
            l_ok, r_ok = _valid_eye_origins_flags(sample)
            if l_ok and r_ok:
                bino_origins.append(parsed[0])
                bino_rays.append(parsed[1])

    # Prefer pure binocular rays when sufficient so edge monocular flicker cannot shift IPD origin
    if len(bino_rays) >= min_valid_samples:
        origins = bino_origins
        rays = bino_rays
    else:
        origins = all_origins
        rays = all_rays

    if len(rays) < min_valid_samples:
        return None

    orig_arr = np.vstack(origins)
    ray_arr = np.vstack(rays)

    # Anchor reference ray on the settled latter two-thirds of the window to ignore arrival tails
    tail_start = len(ray_arr) // 3 if len(ray_arr) >= 9 else 0
    med_ray = np.median(ray_arr[tail_start:], axis=0)
    med_norm = float(np.linalg.norm(med_ray))
    if med_norm < 1e-6:
        return None
    med_ray = med_ray / med_norm

    dots = np.clip(ray_arr @ med_ray, -1.0, 1.0)
    ang_dev = np.arccos(dots)
    med_dev = float(np.median(ang_dev[tail_start:]))
    mad = float(np.median(np.abs(ang_dev[tail_start:] - med_dev)))
    threshold = min(0.045, max(0.018, med_dev + 3.5 * max(mad * 1.4826, 0.004)))

    inliers = ang_dev <= threshold
    if int(np.sum(inliers)) < max(4, min_valid_samples // 2):
        return None

    clean_orig = np.median(orig_arr[inliers], axis=0)
    clean_ray = np.median(ray_arr[inliers], axis=0)
    clean_ray_norm = float(np.linalg.norm(clean_ray))
    if clean_ray_norm < 1e-6:
        return None

    return CalibrationObservation(
        target_u=float(target_u),
        target_v=float(target_v),
        eye_origin_mm=clean_orig,
        unit_ray_dir=clean_ray / clean_ray_norm,
        sample_count=int(np.sum(inliers)),
    )


def _rodrigues(omega: np.ndarray) -> np.ndarray:
    """Compute 3x3 rotation matrix `exp([omega]_x)` from rotation vector `omega`."""
    theta = float(np.linalg.norm(omega))
    if theta < 1e-12:
        wx, wy, wz = float(omega[0]), float(omega[1]), float(omega[2])
        return np.array(
            [
                [1.0, -wz, wy],
                [wz, 1.0, -wx],
                [-wy, wx, 1.0],
            ],
            dtype=np.float64,
        )
    k = omega / theta
    kx, ky, kz = float(k[0]), float(k[1]), float(k[2])
    skew = np.array(
        [
            [0.0, -kz, ky],
            [kz, 0.0, -kx],
            [-ky, kx, 0.0],
        ],
        dtype=np.float64,
    )
    c = math.cos(theta)
    s = math.sin(theta)
    return np.eye(3, dtype=np.float64) + s * skew + (1.0 - c) * (skew @ skew)


def _corners_from_params(
    params: np.ndarray,
    r0: np.ndarray,
    width_mm: float,
    height_mm: float,
) -> DisplayAreaCorners:
    center = params[0:3]
    omega = params[3:6]
    rho = float(params[6])

    rot = r0 @ _rodrigues(omega)
    u_axis = rot[:, 0]
    v_axis = rot[:, 1]

    eff_w = width_mm * math.exp(0.5 * rho)
    eff_h = height_mm * math.exp(-0.5 * rho)

    tl = center - 0.5 * eff_w * u_axis - 0.5 * eff_h * v_axis
    tr = tl + eff_w * u_axis
    bl = tl + eff_h * v_axis
    return (
        (float(tl[0]), float(tl[1]), float(tl[2])),
        (float(tr[0]), float(tr[1]), float(tr[2])),
        (float(bl[0]), float(bl[1]), float(bl[2])),
    )


def _estimate_initial_frame(
    u_vals: np.ndarray,
    v_vals: np.ndarray,
    rays: np.ndarray,
    width_mm: float,
    height_mm: float,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Estimate outlier-resistant initial `(f_hat, r0, l0)` via median pairwise finite differences."""
    u_slopes: List[np.ndarray] = []
    v_slopes: List[np.ndarray] = []
    n = len(rays)
    for i in range(n):
        for j in range(i + 1, n):
            du = float(u_vals[j] - u_vals[i])
            dv = float(v_vals[j] - v_vals[i])
            if abs(du) >= 0.30 and abs(dv) <= 0.15:
                u_slopes.append((rays[j] - rays[i]) / du)
            if abs(dv) >= 0.30 and abs(du) <= 0.15:
                v_slopes.append((rays[j] - rays[i]) / dv)

    design = np.column_stack([np.ones(n), u_vals - 0.5, v_vals - 0.5])
    coeffs, _, _, _ = np.linalg.lstsq(design, rays, rcond=None)

    g_u = np.median(np.vstack(u_slopes), axis=0) if len(u_slopes) >= 2 else coeffs[1]
    g_v = np.median(np.vstack(v_slopes), axis=0) if len(v_slopes) >= 2 else coeffs[2]
    center_ests = rays - np.outer(u_vals - 0.5, g_u) - np.outer(v_vals - 0.5, g_v)
    d_center_raw = np.median(center_ests, axis=0)

    f_norm = float(np.linalg.norm(d_center_raw))
    if f_norm < 1e-6:
        raise ValueError('Degenerate mean gaze direction during calibration')
    f_hat = d_center_raw / f_norm

    u_proj = g_u - float(np.dot(g_u, f_hat)) * f_hat
    u_proj_norm = float(np.linalg.norm(u_proj))
    if u_proj_norm < 1e-5:
        raise ValueError('Insufficient horizontal gaze variation across calibration targets')
    u0 = u_proj / u_proj_norm

    v_proj = g_v - float(np.dot(g_v, f_hat)) * f_hat - float(np.dot(g_v, u0)) * u0
    v_proj_norm = float(np.linalg.norm(v_proj))
    if v_proj_norm < 1e-5:
        raise ValueError('Insufficient vertical gaze variation across calibration targets')
    v0 = v_proj / v_proj_norm
    n0 = np.cross(u0, v0)
    r0 = np.column_stack([u0, v0, n0])

    gu_norm = max(0.05, float(np.linalg.norm(g_u)))
    gv_norm = max(0.05, float(np.linalg.norm(g_v)))
    dist_u = width_mm / (2.0 * math.tan(0.5 * min(2.4, gu_norm)))
    dist_v = height_mm / (2.0 * math.tan(0.5 * min(2.4, gv_norm)))
    l0 = max(250.0, min(5000.0, 0.5 * (dist_u + dist_v)))
    return f_hat, r0, l0


def _fit_plane_subset(
    obs_subset: Sequence[CalibrationObservation],
    width_mm: float,
    height_mm: float,
    omega_xy_bound: float,
    omega_z_bound: float,
    omega_reg: float,
    f_scale: float,
) -> Tuple[np.ndarray, np.ndarray, DisplayAreaCorners]:
    u_sub = np.array([o.target_u for o in obs_subset], dtype=np.float64)
    v_sub = np.array([o.target_v for o in obs_subset], dtype=np.float64)
    orig_sub = np.vstack([o.eye_origin_mm for o in obs_subset])
    rays_sub = np.vstack([o.unit_ray_dir for o in obs_subset])

    mean_eye_sub = np.mean(orig_sub, axis=0)
    f_hat, r0, l0 = _estimate_initial_frame(u_sub, v_sub, rays_sub, width_mm, height_mm)
    c0 = mean_eye_sub + l0 * f_hat
    x0 = np.array([c0[0], c0[1], c0[2], 0.0, 0.0, 0.0, 0.0], dtype=np.float64)

    lower = np.array(
        [
            c0[0] - 4000.0,
            c0[1] - 4000.0,
            c0[2] - 4000.0,
            -omega_xy_bound,
            -omega_xy_bound,
            -omega_z_bound,
            -0.25,
        ],
        dtype=np.float64,
    )
    upper = np.array(
        [
            c0[0] + 4000.0,
            c0[1] + 4000.0,
            c0[2] + 4000.0,
            omega_xy_bound,
            omega_xy_bound,
            omega_z_bound,
            0.25,
        ],
        dtype=np.float64,
    )

    def _residuals(params: np.ndarray) -> np.ndarray:
        corners = _corners_from_params(params, r0, width_mm, height_mm)
        res = np.empty(2 * len(obs_subset) + 4, dtype=np.float64)
        for idx, obs in enumerate(obs_subset):
            proj = project_ray_onto_display_area(obs.eye_origin_mm, obs.unit_ray_dir, corners)
            if proj is None:
                res[2 * idx] = 10.0
                res[2 * idx + 1] = 10.0
                continue
            u_hat, v_hat, t_mm = proj
            if t_mm <= 80.0:
                penalty = 5.0 + 0.05 * (80.0 - t_mm)
                res[2 * idx] = penalty
                res[2 * idx + 1] = penalty
            else:
                res[2 * idx] = u_hat - obs.target_u
                res[2 * idx + 1] = v_hat - obs.target_v
        n_obs = 2 * len(obs_subset)
        res[n_obs] = omega_reg * params[3]
        res[n_obs + 1] = omega_reg * params[4]
        res[n_obs + 2] = (omega_reg * 1.5) * params[5]
        res[n_obs + 3] = 0.02 * params[6]
        return res

    opt = least_squares(
        _residuals,
        x0,
        bounds=(lower, upper),
        method='trf',
        loss='soft_l1',
        f_scale=f_scale,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
        max_nfev=600,
    )
    return opt.x, r0, _corners_from_params(opt.x, r0, width_mm, height_mm)


def _closed_form_candidate_corners(
    obs_quad: Sequence[CalibrationObservation],
    width_mm: float,
    height_mm: float,
) -> Optional[DisplayAreaCorners]:
    """Compute fast closed-form 3D plane candidate from a 4-point subset for consensus scoring."""
    u_q = np.array([o.target_u for o in obs_quad], dtype=np.float64)
    v_q = np.array([o.target_v for o in obs_quad], dtype=np.float64)
    if (float(np.max(u_q) - np.min(u_q)) < 0.30) or (float(np.max(v_q) - np.min(v_q)) < 0.30):
        return None
    design = np.column_stack([np.ones(len(obs_quad)), u_q - 0.5, v_q - 0.5])
    if np.linalg.matrix_rank(design) < 3:
        return None

    rays_q = np.vstack([o.unit_ray_dir for o in obs_quad])
    orig_q = np.vstack([o.eye_origin_mm for o in obs_quad])
    coeffs, _, _, _ = np.linalg.lstsq(design, rays_q, rcond=None)
    d_c, g_u, g_v = coeffs[0], coeffs[1], coeffs[2]

    f_norm = float(np.linalg.norm(d_c))
    if f_norm < 1e-6:
        return None
    f_hat = d_c / f_norm

    u_proj = g_u - float(np.dot(g_u, f_hat)) * f_hat
    un = float(np.linalg.norm(u_proj))
    if un < 1e-5:
        return None
    u0 = u_proj / un

    v_proj = g_v - float(np.dot(g_v, f_hat)) * f_hat - float(np.dot(g_v, u0)) * u0
    vn = float(np.linalg.norm(v_proj))
    if vn < 1e-5:
        return None
    v0 = v_proj / vn

    # Reject distorted non-orthogonal subsets (>22 deg shear between horizontal & vertical eye axes)
    v_raw = g_v - float(np.dot(g_v, f_hat)) * f_hat
    v_raw_n = float(np.linalg.norm(v_raw))
    if v_raw_n < 1e-5 or abs(float(np.dot(u0, v_raw / v_raw_n))) > 0.38:
        return None

    gu_norm = max(0.05, float(np.linalg.norm(g_u)))
    gv_norm = max(0.05, float(np.linalg.norm(g_v)))
    dist_u = width_mm / (2.0 * math.tan(0.5 * min(2.4, gu_norm)))
    dist_v = height_mm / (2.0 * math.tan(0.5 * min(2.4, gv_norm)))
    # Reject subsets where horizontal vs vertical implied eye-to-screen distance disagrees >35%
    if abs(dist_u - dist_v) / max(dist_u, dist_v, 1.0) > 0.35:
        return None

    l0 = max(250.0, min(5000.0, 0.5 * (dist_u + dist_v)))
    c0 = np.mean(orig_q, axis=0) + l0 * f_hat
    r0 = np.column_stack([u0, v0, np.cross(u0, v0)])
    return _corners_from_params(np.array([c0[0], c0[1], c0[2], 0.0, 0.0, 0.0, 0.0]), r0, width_mm, height_mm)


def solve_display_area_3d(
    observations: Sequence[CalibrationObservation],
    screen_diagonal_in: float,
    screen_width_px: int = 1920,
    screen_height_px: int = 1080,
    monitor_index: int = 0,
) -> DisplayAreaProfile:
    """Solve 3D EyeChip `(TL, TR, BL)` plane from 3D gaze-ray observations and screen diagonal."""
    if len(observations) < 4:
        raise ValueError('At least 4 valid calibration targets are required to solve 3D plane')

    width_mm, height_mm = compute_screen_dimensions_mm(
        screen_diagonal_in, screen_width_px, screen_height_px
    )

    u_vals = np.array([obs.target_u for obs in observations], dtype=np.float64)
    v_vals = np.array([obs.target_v for obs in observations], dtype=np.float64)

    u_span = float(np.max(u_vals) - np.min(u_vals))
    v_span = float(np.max(v_vals) - np.min(v_vals))
    if u_span < 0.2 or v_span < 0.2:
        raise ValueError('Calibration targets must span both horizontal and vertical screen axes')

    # Stage 1: Exhaustive deterministic 4-point consensus search (<=126 subsets for 9 targets)
    # so 1-3 corrupted edge/corner targets never twist the 3D screen plane.
    active_obs: Sequence[CalibrationObservation] = observations
    if len(observations) >= 6:
        best_inliers: List[int] = []
        best_score = float('inf')
        n_total = len(observations)
        combos = list(combinations(range(n_total), 4))
        if len(combos) > 250:
            step = max(1, len(combos) // 250)
            combos = combos[::step]

        for quad_idx in combos:
            quad = [observations[i] for i in quad_idx]
            cand_corners = _closed_form_candidate_corners(quad, width_mm, height_mm)
            if cand_corners is None:
                continue
            errs: List[float] = []
            for obs in observations:
                proj = project_ray_onto_display_area(
                    obs.eye_origin_mm, obs.unit_ray_dir, cand_corners
                )
                if proj is None or proj[2] <= 80.0:
                    errs.append(10.0)
                else:
                    errs.append(math.hypot(proj[0] - obs.target_u, proj[1] - obs.target_v))

            inl = [i for i, e in enumerate(errs) if e <= 0.042]
            if len(inl) < 5:
                continue
            u_in = [observations[i].target_u for i in inl]
            v_in = [observations[i].target_v for i in inl]
            if (max(u_in) - min(u_in)) < 0.30 or (max(v_in) - min(v_in)) < 0.30:
                continue

            inl_mse = sum(errs[i] * errs[i] for i in inl) / len(inl)
            # Prefer maximum consensus inlier count, breaking ties by lowest inlier MSE
            if len(inl) > len(best_inliers) or (
                len(inl) == len(best_inliers) and inl_mse < best_score
            ):
                best_inliers = inl
                best_score = inl_mse

        if len(best_inliers) >= 5:
            # Refine on consensus subset and re-admit any point within 3.5% of refined 3D plane
            _, _, refined_cand = _fit_plane_subset(
                [observations[i] for i in best_inliers],
                width_mm,
                height_mm,
                omega_xy_bound=0.44,
                omega_z_bound=0.18,
                omega_reg=0.008,
                f_scale=0.018,
            )
            final_inl: List[CalibrationObservation] = []
            for obs in observations:
                proj = project_ray_onto_display_area(
                    obs.eye_origin_mm, obs.unit_ray_dir, refined_cand
                )
                if proj is not None and proj[2] > 80.0:
                    if math.hypot(proj[0] - obs.target_u, proj[1] - obs.target_v) <= 0.040:
                        final_inl.append(obs)
            if len(final_inl) >= 5:
                active_obs = final_inl

    # Stage 2: Full nonlinear perspective refinement on verified clean inliers
    opt_x, _, solved_corners = _fit_plane_subset(
        active_obs,
        width_mm,
        height_mm,
        omega_xy_bound=0.44,
        omega_z_bound=0.18,
        omega_reg=0.005,
        f_scale=0.020,
    )

    mean_eye = np.mean(np.vstack([o.eye_origin_mm for o in active_obs]), axis=0)
    sq_errs: List[float] = []
    for obs in active_obs:
        proj = project_ray_onto_display_area(obs.eye_origin_mm, obs.unit_ray_dir, solved_corners)
        if proj is not None:
            du = proj[0] - obs.target_u
            dv = proj[1] - obs.target_v
            sq_errs.append(du * du + dv * dv)
    rms_err = math.sqrt(sum(sq_errs) / max(1, len(sq_errs)))

    solved_center = opt_x[0:3]
    eye_to_screen_cm = float(np.linalg.norm(solved_center - mean_eye)) / 10.0
    screen_z_cm = float(solved_center[2]) / 10.0
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    return DisplayAreaProfile(
        tl_xyz=solved_corners[0],
        tr_xyz=solved_corners[1],
        bl_xyz=solved_corners[2],
        screen_diagonal_in=float(screen_diagonal_in),
        monitor_index=int(monitor_index),
        screen_width_px=int(screen_width_px),
        screen_height_px=int(screen_height_px),
        rms_error_norm=rms_err,
        estimated_eye_to_screen_cm=eye_to_screen_cm,
        estimated_screen_z_cm=screen_z_cm,
        mean_eye_xyz=(float(mean_eye[0]), float(mean_eye[1]), float(mean_eye[2])),
        inlier_targets=len(active_obs),
        total_targets=len(observations),
        calibrated_at_iso=now_iso,
    )
