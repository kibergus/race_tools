"""
Track geometry, geographic origins, and alignment correction definitions.
"""
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping, TypeVar
from race_tools import sanitize


def wgs84_meters_per_degree(lat_deg: float) -> tuple[float, float]:
    """
    Compute WGS84 degree lengths (meters per degree latitude, meters per degree longitude)
    at the given latitude using standard ellipsoidal expansion.

    Args:
        lat_deg: Latitude in decimal degrees.

    Returns:
        A tuple of (meters_per_deg_latitude, meters_per_deg_longitude).
    """
    phi = math.radians(lat_deg)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_per_deg_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi) + 0.118 * math.cos(5 * phi)
    return m_per_deg_lat, m_per_deg_lon


@dataclass
class Correction:
    """Correction factors for track alignment (offsets and rotation)."""
    north_m: float = 0.0
    east_m: float = 0.0
    angle_deg: float = 0.0


@dataclass
class TrackOrigin(ABC):
    """
    Geographic origin and alignment corrections for a specific track.

    This is an abstract base class. Simulator-specific modules (e.g. rFactor,
    Assetto Corsa) implement `to_gps` to perform coordinate translation matching
    their respective coordinate frames.
    """
    lat: float
    lon: float
    correction: Correction = field(default_factory=Correction)
    heading: float = 0.0

    @abstractmethod
    def to_gps(self, x: float, z: float) -> tuple[float, float]:
        """
        Convert local Cartesian coordinates (x, z in meters) to GPS (latitude, longitude).

        Args:
            x: Local Cartesian X coordinate (meters).
            z: Local Cartesian Z coordinate (meters).

        Returns:
            A tuple of (latitude, longitude).
        """
        pass

    def local_ned_yaw_offset_deg(self) -> float:
        """
        Clockwise yaw offset in degrees from Game North to Geodetic WGS84 North.
        """
        return -(self.correction.angle_deg - self.heading)


def canonicalize_quat(
    qx: float,
    qy: float,
    qz: float,
    qw: float,
) -> tuple[float, float, float, float]:
    """
    Normalize a (qx, qy, qz, qw) quaternion and canonicalize its sign so the first
    non-zero element of (qw, qz, qy, qx) is positive. Preserves NaN.
    """
    if any(math.isnan(c) for c in (qx, qy, qz, qw)):
        return math.nan, math.nan, math.nan, math.nan

    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm <= 0.0:
        raise ValueError('Invalid zero quaternion.')
    qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm

    eps = 1e-12
    negate = False
    for c in (qw, qz, qy, qx):
        if abs(c) > eps:
            negate = c < 0.0
            break
    if negate:
        return -qx, -qy, -qz, -qw
    return qx, qy, qz, qw


def quat_to_ned_matrix(
    qx: float,
    qy: float,
    qz: float,
    qw: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """
    Convert a unit Hamilton (qx, qy, qz, qw) quaternion (NED + FRD) into the 3x3
    active rotation matrix rows (row0, row1, row2) such that v_ned = R @ v_frd.
    """
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm <= 0.0 or math.isnan(norm):
        raise ValueError('Invalid zero or NaN quaternion.')
    qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm

    x2, y2, z2 = qx + qx, qy + qy, qz + qz
    xx, yy, zz = qx * x2, qy * y2, qz * z2
    xz, xy, yz = qx * z2, qx * y2, qy * z2
    wx, wy, wz = qw * x2, qw * y2, qw * z2

    row0 = (1.0 - (yy + zz), xy - wz, xz + wy)
    row1 = (xy + wz, 1.0 - (xx + zz), yz - wx)
    row2 = (xz - wy, yz + wx, 1.0 - (xx + yy))
    return row0, row1, row2


def local_ned_quat_to_geodetic_ned(
    quat: tuple[float, float, float, float],
    origin: TrackOrigin,
) -> tuple[float, float, float, float]:
    """
    Rotate a Game NED + FRD quaternion into Geodetic WGS84 NED + FRD by
    left-multiplying by q_Z(delta_psi), preserving NaN.
    """
    qx, qy, qz, qw = quat
    if any(math.isnan(c) for c in (qx, qy, qz, qw)):
        return math.nan, math.nan, math.nan, math.nan

    delta_psi = math.radians(origin.local_ned_yaw_offset_deg())
    sz = math.sin(0.5 * delta_psi)
    cz = math.cos(0.5 * delta_psi)

    rx = cz * qx - sz * qy
    ry = cz * qy + sz * qx
    rz = cz * qz + sz * qw
    rw = cz * qw - sz * qz
    return canonicalize_quat(rx, ry, rz, rw)


def ned_quat_to_heading_deg(quat: tuple[float, float, float, float]) -> float:
    """
    Extract clockwise compass heading in [0.0, 360.0) degrees from a NED + FRD quaternion.
    Returns math.nan on NaN inputs or vertical singularity.
    """
    qx, qy, qz, qw = quat
    if any(math.isnan(c) for c in (qx, qy, qz, qw)):
        return math.nan

    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm <= 0.0:
        return math.nan
    qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm

    d_north = 1.0 - 2.0 * (qy * qy + qz * qz)
    d_east = 2.0 * (qx * qy + qw * qz)
    if math.hypot(d_east, d_north) < 1e-9:
        return math.nan
    return math.degrees(math.atan2(d_east, d_north)) % 360.0


T = TypeVar('T', bound=TrackOrigin)


def find_track_origin(
    track_name: str | None,
    origins: Mapping[str, T],
    source_name: str,
) -> T:
    """
    Find track origin from an origins mapping using fuzzy normalization.

    Args:
        track_name: The name of the track to look up.
        origins: Mapping of track names to TrackOrigin instances.
        source_name: Module or file name for error messaging if lookup fails.

    Returns:
        The matched TrackOrigin instance.

    Raises:
        ValueError: If track_name is missing/empty or not found in origins.
    """
    if not track_name:
        raise ValueError('Track name is missing or empty.')

    norm_track_name = sanitize.normalize(track_name)
    for name, origin in origins.items():
        norm_name = sanitize.normalize(name)
        if norm_name and (norm_name in norm_track_name or norm_track_name in norm_name):
            return origin

    raise ValueError(f"Unknown track: '{track_name}'. Please add its coordinates to TRACK_ORIGINS in {source_name}")
