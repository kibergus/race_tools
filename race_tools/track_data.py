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
