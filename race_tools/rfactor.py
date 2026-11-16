"""
rFactor coordinate conversion utilities.
Handles the conversion between rFactor's Cartesian (meters) coordinate system and GPS (WGS84).
"""
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from race_tools import track_data


@dataclass
class RFactorTrackOrigin(track_data.TrackOrigin):
    """rFactor-specific TrackOrigin implementation."""

    def to_gps(self, x: float, z: float) -> tuple[float, float]:
        """Convert local Cartesian coordinates (x, z in meters) to GPS (latitude, longitude)."""
        return rfactor_to_gps(x, z, self)


# Reference constants for rFactor to GPS conversion
TRACK_ORIGINS: dict[str, RFactorTrackOrigin] = {
    'Bayford Meadows': RFactorTrackOrigin(
        51.34, 0.74,
        track_data.Correction(north_m=912.5, east_m=372, angle_deg=-2)),
    'Brentwood': RFactorTrackOrigin(
        51.597, 0.295,
        track_data.Correction(north_m=-23, east_m=154.5, angle_deg=-2)),
    'Buckmore Park': RFactorTrackOrigin(
        51.343194, 0.501867,
        track_data.Correction(north_m=-70, east_m=-56, angle_deg=-2)),
    'Clay Pigeon': RFactorTrackOrigin(
        50.823878, -2.555044,
        track_data.Correction(north_m=20, east_m=81, angle_deg=0)),
    'Dunkeswell': RFactorTrackOrigin(
        50.867316, -3.237089,
        track_data.Correction(north_m=3.5, east_m=-6, angle_deg=1.0)),
    'Ellough Park': RFactorTrackOrigin(
        52.4277, 1.5833,
        track_data.Correction(north_m=692, east_m=1198, angle_deg=-2.5)),
    'Forest Edge': RFactorTrackOrigin(
        51.1802778, -1.36166,
        track_data.Correction(north_m=-45.0, east_m=-141.0, angle_deg=0.0)),
    'Fulbeck': RFactorTrackOrigin(
        53.045268, -0.656013,
        track_data.Correction(north_m=-21.0, east_m=-55.0, angle_deg=0.0)),
    'Glan Y Gors': RFactorTrackOrigin(
        53.035557, -3.582806,
        track_data.Correction(north_m=-94, east_m=-61, angle_deg=2)),
    'Hooton Park': RFactorTrackOrigin(
        53.31, -2.941,
        track_data.Correction(north_m=5, east_m=-36, angle_deg=0)),
    'Kimbolton': RFactorTrackOrigin(
        52.315824875017825, -0.36923284525509625,
        track_data.Correction(north_m=-30, east_m=-68, angle_deg=-1.5)),
    'Larkhall': RFactorTrackOrigin(
        55.752193, -3.978246,
        track_data.Correction(north_m=-5, east_m=-16, angle_deg=0)),
    'Llandow': RFactorTrackOrigin(
        51.434, -3.498,
        track_data.Correction(north_m=-42, east_m=162, angle_deg=-1)),
    'Lydd': RFactorTrackOrigin(
        50.93515747693628, 0.9073052129051091,
        track_data.Correction(north_m=-98, east_m=-16, angle_deg=0)),
    'Nutts Corner': RFactorTrackOrigin(
        54.62245248013883, -6.145107376760258,
        track_data.Correction(north_m=-70, east_m=-65, angle_deg=0)),
    'PFI': RFactorTrackOrigin(
        53.038403944190826, -0.6612882809870662,
        track_data.Correction(north_m=-5, east_m=27, angle_deg=-0.5)),
    'Red Lodge': RFactorTrackOrigin(
        52.295, 0.478,
        track_data.Correction(north_m=62, east_m=-14, angle_deg=-2.0)),
    'Rissington': RFactorTrackOrigin(
        51.87, -1.69,
        track_data.Correction(north_m=-512, east_m=315, angle_deg=0)),
    'Rowrah': RFactorTrackOrigin(
        54.551493, -3.438988,
        track_data.Correction(north_m=-71, east_m=-228, angle_deg=0)),
    'Rye House': RFactorTrackOrigin(
        51.76745791571449, 0.011617482475658511,
        track_data.Correction(north_m=-20, east_m=-26, angle_deg=-1.5)),
    'Shenington': RFactorTrackOrigin(
        52.08, -1.47,
        track_data.Correction(north_m=165.5, east_m=-419, angle_deg=0.7)),
    'Teesside': RFactorTrackOrigin(
        54.58, -1.19,
        track_data.Correction(north_m=-257, east_m=40, angle_deg=0)),
    'Three Sisters': RFactorTrackOrigin(
        53.50690336837593, -2.632337509845641,
        track_data.Correction(north_m=-43, east_m=-35, angle_deg=0)),
    'Warden Law': RFactorTrackOrigin(
        54.84856307661587, -1.423336782654707,
        track_data.Correction(north_m=1, east_m=3, angle_deg=-0.5)),
    'Whilton Mill': RFactorTrackOrigin(
        52.28, -1.09,
        track_data.Correction(north_m=-413, east_m=79, angle_deg=-1)),
    'Willow Springs': RFactorTrackOrigin(
        34.87085, -118.26726,
        track_data.Correction(north_m=18.0, east_m=12.0, angle_deg=0.0)),
}
TRACK_ORIGINS['South Wales Karting Centre'] = TRACK_ORIGINS['Llandow']
TRACK_ORIGINS['Willow Springs reverse'] = TRACK_ORIGINS['Willow Springs']


def get_track_origin(track_name: str | None) -> RFactorTrackOrigin:
    """
    Find track origin by name match.

    Args:
        track_name: The name of the track to look up.

    Returns:
        RFactorTrackOrigin object containing lat/lon and corrections.

    Raises:
        ValueError: If track_name is missing or no match is found.
    """
    return track_data.find_track_origin(track_name, TRACK_ORIGINS, 'rfactor.py')


def rfactor_to_gps(x: float, z: float, origin: track_data.TrackOrigin) -> tuple[float, float]:
    """
    Convert rFactor Cartesian coordinates (meters) to GPS (Lat/Lon).

    Args:
        x: rFactor X coordinate (meters).
        z: rFactor Z coordinate (meters).
        origin: TrackOrigin object defining the coordinate system transformation.

    Returns:
        A tuple of (latitude, longitude).

    rFactor Axes:
        +X is Right
        +Z is Forward

    The conversion applies rotation and meter-based shifts defined in origin.correction,
    then uses WGS84 degree lengths for the specific latitude.
    """
    # 1. Apply Rotation
    # origin.heading is clockwise from North; angle_deg is counter-clockwise.
    angle_deg = origin.correction.angle_deg - origin.heading
    if angle_deg != 0:
        angle_rad = math.radians(angle_deg)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        # Standard 2D rotation matrix:
        # [ x' ] = [ cos -sin ] [ x ]
        # [ z' ] = [ sin  cos ] [ z ]
        x_rot = x * cos_a - z * sin_a
        z_rot = x * sin_a + z * cos_a
        x, z = x_rot, z_rot

    # 2. Apply Meter Shifts (North/East)
    x += origin.correction.east_m
    z += origin.correction.north_m

    # 3. Accurate WGS84 degree lengths at the given latitude
    m_per_deg_lat, m_per_deg_lon = track_data.wgs84_meters_per_degree(origin.lat)

    lat = origin.lat + (z / m_per_deg_lat)
    lon = origin.lon + (x / m_per_deg_lon)

    return lat, lon


@dataclass(frozen=True)
class RFactorCameraProjection:
    """Single-screen camera projection parameters recorded in BRT1 metadata ('camera_projection')."""

    vfov_deg: float = 50.0
    resolution_px: tuple[int, int] = (1920, 1080)

    @property
    def aspect_ratio(self) -> float:
        """Return screen width / height aspect ratio."""
        w, h = self.resolution_px
        if w > 0 and h > 0:
            return float(w) / float(h)
        return 16.0 / 9.0

    @classmethod
    def from_metadata(cls, data: Mapping[str, Any]) -> 'RFactorCameraProjection':
        """
        Parse RFactorCameraProjection from either a 'camera_projection' dict
        or a parent BRT1 'metadata' dict containing 'camera_projection'.
        """
        proj = data.get('camera_projection', data)
        if not isinstance(proj, Mapping):
            raise ValueError('Invalid camera_projection metadata object.')

        raw_res = proj.get('resolution_px')
        if isinstance(raw_res, Sequence) and len(raw_res) == 2:
            resolution = (int(raw_res[0]), int(raw_res[1]))
        else:
            resolution = (1920, 1080)

        return cls(
            vfov_deg=float(proj.get('vfov_deg', 50.0)),
            resolution_px=resolution,
        )


def unproject_rfactor_gaze_ray(
    u: float,
    v: float,
    projection: RFactorCameraProjection,
) -> tuple[float, float, float]:
    """
    Unproject normalized screen gaze coordinates (u, v in [0, 1], top-left origin)
    into a unit 3D gaze ray in FRD camera coordinates
    (+x = Forward, +y = Right, +z = Down).
    """
    if math.isnan(u) or math.isnan(v):
        return math.nan, math.nan, math.nan

    half_vfov_rad = 0.5 * math.radians(projection.vfov_deg)
    tan_half_vfov = math.tan(half_vfov_rad)
    tan_half_hfov = projection.aspect_ratio * tan_half_vfov

    dx = 1.0
    dy = (2.0 * u - 1.0) * tan_half_hfov
    dz = (2.0 * v - 1.0) * tan_half_vfov

    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm <= 0.0:
        return 1.0, 0.0, 0.0
    return dx / norm, dy / norm, dz / norm


unproject_gaze_ray_frd = unproject_rfactor_gaze_ray
quat_to_rfactor_matrix = track_data.quat_to_ned_matrix


def slerp_quat(
    q0: tuple[float, float, float, float],
    q1: tuple[float, float, float, float],
    alpha: float,
) -> tuple[float, float, float, float]:
    """
    Shortest-path spherical linear interpolation (SLERP) between two (qx, qy, qz, qw) quaternions.
    """
    n0 = math.sqrt(sum(c * c for c in q0))
    n1 = math.sqrt(sum(c * c for c in q1))
    if n0 <= 0.0 or n1 <= 0.0 or math.isnan(n0) or math.isnan(n1) or math.isnan(alpha):
        return math.nan, math.nan, math.nan, math.nan

    x0, y0, z0, w0 = q0[0] / n0, q0[1] / n0, q0[2] / n0, q0[3] / n0
    x1, y1, z1, w1 = q1[0] / n1, q1[1] / n1, q1[2] / n1, q1[3] / n1

    dot = x0 * x1 + y0 * y1 + z0 * z1 + w0 * w1
    if dot < 0.0:
        dot = -dot
        x1, y1, z1, w1 = -x1, -y1, -z1, -w1

    if dot > 0.9995:
        rx = x0 + alpha * (x1 - x0)
        ry = y0 + alpha * (y1 - y0)
        rz = z0 + alpha * (z1 - z0)
        rw = w0 + alpha * (w1 - w0)
        rn = math.sqrt(rx * rx + ry * ry + rz * rz + rw * rw)
        return rx / rn, ry / rn, rz / rn, rw / rn

    theta_0 = math.acos(min(1.0, dot))
    theta = theta_0 * alpha
    sin_theta = math.sin(theta)
    sin_theta_0 = math.sin(theta_0)

    s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
    s1 = sin_theta / sin_theta_0
    return (
        s0 * x0 + s1 * x1,
        s0 * y0 + s1 * y1,
        s0 * z0 + s1 * z1,
        s0 * w0 + s1 * w1,
    )


def rfactor_direction_to_gps_bearing(
    dx: float,
    dz: float,
    origin: track_data.TrackOrigin,
) -> float:
    """
    Convert a horizontal direction vector (dx, dz) in rFactor world coordinates
    to a GPS map compass bearing in [0.0, 360.0) degrees clockwise from North.
    """
    if math.isnan(dx) or math.isnan(dz) or (dx == 0.0 and dz == 0.0):
        return math.nan

    angle_deg = origin.correction.angle_deg - origin.heading
    raw_bearing_deg = math.degrees(math.atan2(dx, dz))
    return (raw_bearing_deg - angle_deg) % 360.0


def reconstruct_kart_heading(
    local_ori_quat: tuple[float, float, float, float],
    origin: track_data.TrackOrigin,
) -> float:
    """
    Compute the kart chassis GPS compass heading in [0.0, 360.0) degrees clockwise from North
    from 'Local Ori Quat X/Y/Z/W' (Game NED + FRD).
    """
    geo_quat = track_data.local_ned_quat_to_geodetic_ned(local_ori_quat, origin)
    return track_data.ned_quat_to_heading_deg(geo_quat)


def reconstruct_gaze_map_yaw(
    gaze_x: float,
    gaze_y: float,
    local_cam_quat: tuple[float, float, float, float],
    projection: RFactorCameraProjection | Mapping[str, Any],
    origin: track_data.TrackOrigin,
) -> float:
    """
    Reconstruct the GPS map compass bearing (in [0.0, 360.0) degrees clockwise from North)
    of a driver's 3D gaze ray from eye-tracker normalized coordinates (Gaze X, Gaze Y in [0, 1]),
    driving camera orientation quaternion ('Local Cam Quat X/Y/Z/W', Game NED + FRD),
    camera projection metadata, and track GPS origin.

    Returns math.nan if gaze or camera quaternion contains NaN (e.g. blink or trackside camera),
    or if the gaze ray points vertically with zero horizontal projection.
    """
    qx, qy, qz, qw = local_cam_quat
    if math.isnan(gaze_x) or math.isnan(gaze_y) or any(math.isnan(c) for c in (qx, qy, qz, qw)):
        return math.nan

    proj = (
        projection
        if isinstance(projection, RFactorCameraProjection)
        else RFactorCameraProjection.from_metadata(projection)
    )

    ray_x, ray_y, ray_z = unproject_rfactor_gaze_ray(gaze_x, gaze_y, proj)
    if math.isnan(ray_x):
        return math.nan

    geo_quat = track_data.local_ned_quat_to_geodetic_ned(local_cam_quat, origin)
    row0, row1, _ = track_data.quat_to_ned_matrix(*geo_quat)
    d_north = row0[0] * ray_x + row0[1] * ray_y + row0[2] * ray_z
    d_east = row1[0] * ray_x + row1[1] * ray_y + row1[2] * ray_z
    if math.hypot(d_east, d_north) < 1e-9:
        return math.nan

    return math.degrees(math.atan2(d_east, d_north)) % 360.0


def reconstruct_gaze_pitch_deg(
    gaze_x: float,
    gaze_y: float,
    cam_quat: tuple[float, float, float, float],
    projection: RFactorCameraProjection | Mapping[str, Any],
) -> float:
    """
    Reconstruct the vertical gaze pitch angle in degrees (positive = looking up)
    from eye-tracker normalized coordinates (Gaze X, Gaze Y in [0, 1]),
    camera orientation quaternion (NED + FRD), and camera projection metadata.
    """
    qx, qy, qz, qw = cam_quat
    if math.isnan(gaze_x) or math.isnan(gaze_y) or any(math.isnan(c) for c in (qx, qy, qz, qw)):
        return math.nan

    proj = (
        projection
        if isinstance(projection, RFactorCameraProjection)
        else RFactorCameraProjection.from_metadata(projection)
    )

    ray_x, ray_y, ray_z = unproject_rfactor_gaze_ray(gaze_x, gaze_y, proj)
    if math.isnan(ray_x):
        return math.nan

    _, _, row2 = track_data.quat_to_ned_matrix(qx, qy, qz, qw)
    d_down = row2[0] * ray_x + row2[1] * ray_y + row2[2] * ray_z
    return math.degrees(math.asin(max(-1.0, min(1.0, -d_down))))
