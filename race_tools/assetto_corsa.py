"""
Assetto Corsa Evo coordinate and track alignment utilities.
"""
import math
from dataclasses import dataclass
from race_tools import track_data


@dataclass
class AssettoCorsaTrackOrigin(track_data.TrackOrigin):
    """Assetto Corsa / Assetto Corsa Evo specific TrackOrigin implementation."""

    def to_gps(self, x: float, z: float) -> tuple[float, float]:
        """Convert local Cartesian coordinates (x, z in meters) to GPS (latitude, longitude)."""
        return assetto_corsa_to_gps(x, z, self)


# Reference constants for Assetto Corsa / ACE to GPS conversion
TRACK_ORIGINS: dict[str, AssettoCorsaTrackOrigin] = {
    'monza': AssettoCorsaTrackOrigin(
        45.613890, 9.286240,
        track_data.Correction(north_m=820.0, east_m=-30.0, angle_deg=0.0)),
    'imola': AssettoCorsaTrackOrigin(44.341200, 11.713400),
    'spa': AssettoCorsaTrackOrigin(50.437200, 5.971400),
    'brands_hatch': AssettoCorsaTrackOrigin(51.356800, 0.263100),
    'cota': AssettoCorsaTrackOrigin(30.132800, -97.641100),
    'donington': AssettoCorsaTrackOrigin(52.830600, -1.375300),
    'fuji': AssettoCorsaTrackOrigin(35.371700, 138.926900),
    'kyalami': AssettoCorsaTrackOrigin(-25.998900, 28.075800),
    'laguna_seca': AssettoCorsaTrackOrigin(36.584400, -121.753600),
    'bathurst': AssettoCorsaTrackOrigin(-33.447500, 149.554400),
    'nurburgring': AssettoCorsaTrackOrigin(50.335600, 6.947500),
    'oulton_park': AssettoCorsaTrackOrigin(53.177200, -2.613600),
    'paul_ricard': AssettoCorsaTrackOrigin(43.250600, 5.791700),
    'red_bull_ring': AssettoCorsaTrackOrigin(47.219700, 14.764700),
    'road_atlanta': AssettoCorsaTrackOrigin(34.148100, -83.817800),
    'sebring': AssettoCorsaTrackOrigin(27.454700, -81.348300),
    'suzuka': AssettoCorsaTrackOrigin(34.843100, 136.541000),
    'watkins_glen': AssettoCorsaTrackOrigin(42.336900, -76.927200),
}


def get_track_origin(track_name: str | None) -> AssettoCorsaTrackOrigin:
    """
    Find track origin for Assetto Corsa / Assetto Corsa Evo by name match.

    Args:
        track_name: The name of the track to look up.

    Returns:
        AssettoCorsaTrackOrigin object containing lat/lon and corrections.

    Raises:
        ValueError: If track_name is missing or no match is found.
    """
    return track_data.find_track_origin(track_name, TRACK_ORIGINS, 'assetto_corsa.py')


def assetto_corsa_to_gps(x: float, z: float, origin: track_data.TrackOrigin) -> tuple[float, float]:
    """
    Convert Assetto Corsa / Assetto Corsa Evo Cartesian coordinates (meters) to GPS (Lat/Lon).

    In Assetto Corsa / ACE engines:
      +X is East
      -Z is North (+Z is South)

    Args:
        x: Local Cartesian X coordinate (meters, +X is East).
        z: Local Cartesian Z coordinate (meters, -Z is North).
        origin: TrackOrigin object defining geographic origin and alignment corrections.

    Returns:
        A tuple of (latitude, longitude).
    """
    # In ACE ground coordinates: East = +X, North = -Z
    east = x
    north = -z

    # 1. Apply Rotation
    # origin.heading is clockwise from North; angle_deg is counter-clockwise.
    angle_deg = origin.correction.angle_deg - origin.heading
    if angle_deg != 0:
        angle_rad = math.radians(angle_deg)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        # Standard 2D rotation matrix:
        east_rot = east * cos_a - north * sin_a
        north_rot = east * sin_a + north * cos_a
        east, north = east_rot, north_rot

    # 2. Apply Meter Shifts (North/East)
    east += origin.correction.east_m
    north += origin.correction.north_m

    # 3. Accurate WGS84 degree lengths at the given latitude
    m_per_deg_lat, m_per_deg_lon = track_data.wgs84_meters_per_degree(origin.lat)

    lat = origin.lat + (north / m_per_deg_lat)
    lon = origin.lon + (east / m_per_deg_lon)

    return lat, lon
