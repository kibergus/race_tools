"""
rFactor coordinate conversion utilities.
Handles the conversion between rFactor's Cartesian (meters) coordinate system and GPS (WGS84).
"""
import math
from dataclasses import dataclass, field
from race_tools import sanitize


@dataclass
class Correction:
    """Correction factors for track alignment (offsets and rotation)."""
    north_m: float = 0.0
    east_m: float = 0.0
    angle_deg: float = 0.0


@dataclass
class TrackOrigin:
    """Geographic origin and alignment corrections for a specific track."""
    lat: float
    lon: float
    correction: Correction = field(default_factory=Correction)


# Reference constants for rFactor to GPS conversion
TRACK_ORIGINS = {
    'Bayford Meadows': TrackOrigin(
        51.34, 0.74,
        Correction(north_m=912.5, east_m=372, angle_deg=-2)),
    'Brentwood': TrackOrigin(
        51.597, 0.295,
        Correction(north_m=-23, east_m=154.5, angle_deg=-2)),
    'Buckmore Park': TrackOrigin(
        51.343194, 0.501867,
        Correction(north_m=-70, east_m=-56, angle_deg=-2)),
    'Clay Pigeon': TrackOrigin(
        50.823878, -2.555044,
        Correction(north_m=20, east_m=81, angle_deg=0)),
    'Dunkeswell': TrackOrigin(
        50.867316, -3.237089,
        Correction(north_m=3.5, east_m=-6, angle_deg=1.0)),
    'Ellough Park': TrackOrigin(
        52.4277, 1.5833,
        Correction(north_m=692, east_m=1198, angle_deg=-2.5)),
    'Forest Edge': TrackOrigin(
        51.1802778, -1.36166,
        Correction(north_m=-45.0, east_m=-141.0, angle_deg=0.0)),
    'Fulbeck': TrackOrigin(
        53.045268, -0.656013,
        Correction(north_m=-21.0, east_m=-55.0, angle_deg=0.0)),
    'Glan Y Gors': TrackOrigin(
        53.035557, -3.582806,
        Correction(north_m=-94, east_m=-61, angle_deg=2)),
    'Hooton Park': TrackOrigin(
        53.31, -2.941,
        Correction(north_m=5, east_m=-36, angle_deg=0)),
    'Kimbolton': TrackOrigin(
        52.315824875017825, -0.36923284525509625,
        Correction(north_m=-30, east_m=-68, angle_deg=-1.5)),
    'Larkhall': TrackOrigin(
        55.752193, -3.978246,
        Correction(north_m=-5, east_m=-16, angle_deg=0)),
    'Llandow': TrackOrigin(
        51.434, -3.498,
        Correction(north_m=-42, east_m=162, angle_deg=-1)),
    'Lydd': TrackOrigin(
        50.93515747693628, 0.9073052129051091,
        Correction(north_m=-98, east_m=-16, angle_deg=0)),
    # TODO
    'Nutts Corner': TrackOrigin(
        54.62, -6.14,
        Correction(north_m=0, east_m=0, angle_deg=0)),
    # TODO
    'PFI': TrackOrigin(
        52.9158, -0.6389,
        Correction(north_m=0, east_m=0, angle_deg=0)),
    # TODO
    'Red Lodge': TrackOrigin(
        52.295, 0.478,
        Correction(north_m=60, east_m=-15, angle_deg=-2.0)),
    'Rissington': TrackOrigin(
        51.87, -1.69,
        Correction(north_m=-512, east_m=315, angle_deg=0)),
    'Rowrah': TrackOrigin(
        54.551493, -3.438988,
        Correction(north_m=-71, east_m=-228, angle_deg=0)),
    'Rye House': TrackOrigin(
        51.76745791571449, 0.011617482475658511,
        Correction(north_m=-20, east_m=-26, angle_deg=-1.5)),
    'Shenington': TrackOrigin(
        52.08, -1.47,
        Correction(north_m=165.5, east_m=-419, angle_deg=0.7)),
    # TODO
    'Teesside': TrackOrigin(
        54.58, -1.19,
        Correction(north_m=0, east_m=0, angle_deg=0)),
    # TODO
    'Three Sisters': TrackOrigin(
        53.5, -2.63,
        Correction(north_m=0, east_m=0, angle_deg=0)),
    # TODO
    'Warden Law': TrackOrigin(
        54.8368, -1.4116,
        Correction(north_m=0, east_m=0, angle_deg=0)),
    'Whilton Mill': TrackOrigin(
        52.28, -1.09,
        Correction(north_m=-413, east_m=79, angle_deg=-1)),
}
TRACK_ORIGINS['South Wales Karting Centre'] = TRACK_ORIGINS['Llandow']


def get_track_origin(track_name: str | None) -> TrackOrigin:
    """
    Find track origin by name match.

    Args:
        track_name: The name of the track to look up.

    Returns:
        TrackOrigin object containing lat/lon and corrections.

    Raises:
        ValueError: If track_name is missing or no match is found.
    """
    if not track_name:
        raise ValueError('Track name is missing or empty.')

    norm_track_name = sanitize.normalize(track_name)
    for name, origin in TRACK_ORIGINS.items():
        norm_name = sanitize.normalize(name)
        if norm_name and (norm_name in norm_track_name or norm_track_name in norm_name):
            return origin

    raise ValueError(f"Unknown track: '{track_name}'. Please add its coordinates to TRACK_ORIGINS in rfactor.py")


def rfactor_to_gps(x: float, z: float, origin: TrackOrigin) -> tuple[float, float]:
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
    # 1. Apply Rotation (CCW)
    angle_deg = origin.correction.angle_deg
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
    # See: https://en.wikipedia.org/wiki/Geographic_coordinate_system#Length_of_a_degree
    phi = math.radians(origin.lat)

    # Length of a degree of latitude (meters)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)

    # Length of a degree of longitude (meters)
    m_per_deg_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi) + 0.118 * math.cos(5 * phi)

    lat = origin.lat + (z / m_per_deg_lat)
    lon = origin.lon + (x / m_per_deg_lon)

    return lat, lon
