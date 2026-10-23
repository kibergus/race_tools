"""
rFactor coordinate conversion utilities.
Handles the conversion between rFactor's Cartesian (meters) coordinate system and GPS (WGS84).
"""
import math
from dataclasses import dataclass
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
