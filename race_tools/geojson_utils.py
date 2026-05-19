"""
Utilities for generating GeoJSON files from track path data.
"""
import json
from typing import Sequence


def generate_track_limits_geojson(
    center_path: Sequence[Sequence[float]],
    left_path: Sequence[Sequence[float]],
    right_path: Sequence[Sequence[float]],
    name: str = 'Track Layout',
    output_path: str = 'track_limits.geojson',
    include_centerline: bool = True
) -> str:
    """
    Generate a GeoJSON file with center line and track boundaries.

    Args:
        center_path: List of (lat, lon, alt) or (lat, lon) for the centerline.
        left_path: List of (lat, lon, alt) or (lat, lon) for the left boundary.
        right_path: List of (lat, lon, alt) or (lat, lon) for the right boundary.
        name: Name of the feature collection.
        output_path: Path where the GeoJSON file will be saved.
        include_centerline: Whether to include the centerline in the GeoJSON.

    Returns:
        The path to the generated GeoJSON file.

    Note:
        GeoJSON uses [longitude, latitude, altitude] order internally.
    """

    features = []

    def path_to_coords(path: Sequence[Sequence[float]]) -> list[list[float]]:
        coords = []
        for p in path:
            lat, lon = p[0], p[1]
            alt = p[2] if len(p) > 2 else 0
            # GeoJSON is [lon, lat, alt]
            coords.append([lon, lat, alt])
        return coords

    # Left Boundary
    features.append({
        'type': 'Feature',
        'properties': {
            'name': 'Left Boundary',
            'type': 'boundary',
            'side': 'left'
        },
        'geometry': {
            'type': 'LineString',
            'coordinates': path_to_coords(left_path)
        }
    })

    # Right Boundary
    features.append({
        'type': 'Feature',
        'properties': {
            'name': 'Right Boundary',
            'type': 'boundary',
            'side': 'right'
        },
        'geometry': {
            'type': 'LineString',
            'coordinates': path_to_coords(right_path)
        }
    })

    # Center Line
    if include_centerline:
        features.append({
            'type': 'Feature',
            'properties': {
                'name': 'Center Line',
                'type': 'centerline'
            },
            'geometry': {
                'type': 'LineString',
                'coordinates': path_to_coords(center_path)
            }
        })

    geojson_content = {
        'type': 'FeatureCollection',
        'name': name,
        'features': features
    }

    with open(output_path, 'w') as f:
        json.dump(geojson_content, f, indent=2)

    return output_path
