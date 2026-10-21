#!/usr/bin/env python3
"""
Assetto Corsa Evo (ACE) Track Extractor.
Extracts centerline splines, track boundaries, and origin metadata from ACE's
content.kspkg package and generates standard JSON, GeoJSON, and KML files
compatible with the race_tools data pipeline.
"""

import os
import sys
import json
import math
import struct
import argparse
from typing import List, Tuple, Dict, Any

# Ensure race_tools is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from race_tools import kml_utils, geojson_utils

# Constants for kspkg parsing
XOR_KEY = 0x9F9721A97D1135C1
KEY_BYTES = struct.pack('<Q', XOR_KEY)
FILE_TABLE_SIZES = [0x4_000_000, 0x2_000_000]  # 64MB for >=0.7, 32MB for <0.7

# Default known track geographic origins and metadata
KNOWN_TRACK_METADATA = {
    'monza': {
        'track_name': 'Monza',
        'timezone': 'Europe/Rome',
        'aliases': [
            'Monza',
            'Autodromo Nazionale Monza',
            'Monza GP',
            'monza',
            'MonzaGP',
            'Monza GP Time Attack'
        ],
        # Coordinates of the in-game Cartesian (0, 0, 0) origin
        'origin': {
            'lat': 45.61389,
            'lon': 9.28624
        },
        'sector_splits_ratio': [0.275, 0.671]  # Sector 1 and Sector 2 split ratios
    },
    'imola': {
        'track_name': 'Imola',
        'timezone': 'Europe/Rome',
        'aliases': ['Imola', 'Autodromo Enzo e Dino Ferrari', 'imola'],
        'origin': {
            'lat': 44.3412,
            'lon': 11.7134
        },
        'sector_splits_ratio': [0.333, 0.666]
    },
    'spa': {
        'track_name': 'Spa-Francorchamps',
        'timezone': 'Europe/Brussels',
        'aliases': ['Spa', 'Circuit de Spa-Francorchamps', 'spa'],
        'origin': {
            'lat': 50.4372,
            'lon': 5.9714
        },
        'sector_splits_ratio': [0.333, 0.666]
    }
}


def parse_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Parse a Protobuf varint from data starting at pos."""
    res = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        res |= (b & 0x7f) << shift
        shift += 7
        if not (b & 0x80):
            break
    return res, pos


def dump_protobuf_fields(raw: bytes) -> Dict[int, Any]:
    """Shallow decode a Protobuf message into a field map."""
    pos = 0
    res = {}
    while pos < len(raw):
        tag, pos = parse_varint(raw, pos)
        fn = tag >> 3
        wt = tag & 7
        if wt == 0:
            val, pos = parse_varint(raw, pos)
            res[fn] = val
        elif wt == 2:
            l, pos = parse_varint(raw, pos)
            b = raw[pos:pos+l]
            pos += l
            res[fn] = b
        elif wt == 5:
            fval = struct.unpack('<f', raw[pos:pos+4])[0]
            pos += 4
            res[fn] = fval
        elif wt == 1:
            dval = struct.unpack('<d', raw[pos:pos+8])[0]
            pos += 8
            res[fn] = dval
        else:
            break
    return res


class ACEPackageReader:
    """Reads files directly from Assetto Corsa Evo content.kspkg archive."""

    def __init__(self, kspkg_path: str):
        self.kspkg_path = kspkg_path
        self.file_table: Dict[str, Tuple[int, int, int]] = {}  # path -> (offset, size, flags)
        self._load_table()

    def _load_table(self):
        with open(self.kspkg_path, 'rb') as f:
            for table_size in FILE_TABLE_SIZES:
                try:
                    f.seek(-table_size, os.SEEK_END)
                    ftbl = bytearray(f.read(table_size))
                except Exception:
                    continue

                for i in range(len(ftbl)):
                    ftbl[i] ^= KEY_BYTES[i % 8]

                num_entries = table_size // 0x100
                first_entry = ftbl[:0x100]
                first_len = struct.unpack('<h', first_entry[0xE6:0xE8])[0]
                first_path = first_entry[:first_len].decode('ascii', errors='ignore')

                if first_path.startswith('content\\') or 'content' in first_path:
                    # Valid table detected
                    for i in range(num_entries):
                        entry = ftbl[i*0x100 : (i+1)*0x100]
                        path_len = struct.unpack('<h', entry[0xE6:0xE8])[0]
                        path_hash = struct.unpack('<Q', entry[0xE8:0xF0])[0]
                        if path_hash == 0:
                            break
                        if 0 < path_len <= 0xE0:
                            path = entry[:path_len].decode('ascii', errors='ignore')
                            flags = struct.unpack('<h', entry[0xE4:0xE6])[0]
                            file_size = struct.unpack('<q', entry[0xF0:0xF8])[0]
                            file_offs = struct.unpack('<q', entry[0xF8:0x100])[0]
                            self.file_table[path.lower().replace('/', '\\')] = (file_offs, file_size, flags)
                    return

        raise RuntimeError(f"Failed to find valid file table in '{self.kspkg_path}'")

    def read_file(self, internal_path: str) -> bytes:
        norm = internal_path.lower().replace('/', '\\')
        if norm not in self.file_table:
            raise FileNotFoundError(f"File '{internal_path}' not found in kspkg")

        offset, size, flags = self.file_table[norm]
        with open(self.kspkg_path, 'rb') as f:
            f.seek(offset)
            data = bytearray(f.read(size))
            if flags & (1 << 8):  # Encrypted with XOR key
                for i in range(len(data)):
                    data[i] ^= KEY_BYTES[i % 8]
            return bytes(data)


def cartesian_to_gps(x: float, z: float, origin_lat: float, origin_lon: float) -> Tuple[float, float]:
    """
    Converts Cartesian meters (+X = East, +Z = North) to WGS84 GPS (lat, lon).
    In ACE, laser-scanned tracks are natively aligned to True North.
    """
    phi = math.radians(origin_lat)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_per_deg_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi) + 0.118 * math.cos(5 * phi)

    lat = origin_lat + (z / m_per_deg_lat)
    lon = origin_lon + (x / m_per_deg_lon)
    return lat, lon


def extract_track(track_slug: str, kspkg_path: str, output_dir: str):
    """Extracts centerline and boundaries for track_slug from kspkg."""
    reader = ACEPackageReader(kspkg_path)

    # Resolve track metadata
    meta = KNOWN_TRACK_METADATA.get(track_slug.lower(), {
        'track_name': track_slug.capitalize(),
        'timezone': 'UTC',
        'aliases': [track_slug],
        'origin': {'lat': 0.0, 'lon': 0.0},
        'sector_splits_ratio': [0.333, 0.666]
    })

    # Search for splinedata and controlpoints
    spline_file = f'content\\tracks\\{track_slug}\\containers\\layout_gp.splinedata.json'
    ctrl_file = f'content\\tracks\\{track_slug}\\layouts\\layout_gp.trackcontrolpoints'

    # Fallback search if layout_gp naming differs
    if spline_file not in reader.file_table:
        for k in reader.file_table:
            if k.startswith(f'content\\tracks\\{track_slug}\\') and k.endswith('.splinedata.json'):
                spline_file = k
                break

    if ctrl_file not in reader.file_table:
        for k in reader.file_table:
            if k.startswith(f'content\\tracks\\{track_slug}\\') and k.endswith('.trackcontrolpoints'):
                ctrl_file = k
                break

    print(f"Reading centerline spline from: {spline_file}")
    spline_json = json.loads(reader.read_file(spline_file).decode('utf-8'))
    nodes = spline_json['spline']['data']['nodes']

    print(f"Reading control points from: {ctrl_file}")
    ctrl_raw = reader.read_file(ctrl_file)
    pos = 0
    control_points = []
    while pos < len(ctrl_raw):
        tag, pos = parse_varint(ctrl_raw, pos)
        fn = tag >> 3
        wt = tag & 7
        if wt == 2:
            l, pos = parse_varint(ctrl_raw, pos)
            sub = dump_protobuf_fields(ctrl_raw[pos:pos+l])
            pos += l
            pos_raw = dump_protobuf_fields(sub.get(1, b''))
            x = pos_raw.get(1, 0.0)
            y = pos_raw.get(2, 0.0)
            z = pos_raw.get(3, 0.0)
            w_left = sub.get(5, 7.0)
            w_right = sub.get(6, 7.0)
            control_points.append((x, y, z, w_left, w_right))
        elif wt == 0:
            val, pos = parse_varint(ctrl_raw, pos)
        elif wt == 5:
            pos += 4
        else:
            break

    print(f"Loaded {len(nodes)} centerline nodes and {len(control_points)} track width control points.")

    # 1. Compute cumulative lap distances and compute centerline points
    N = len(nodes)
    center_points_gps = []
    dists = [0.0]
    origin_lat = meta['origin']['lat']
    origin_lon = meta['origin']['lon']

    for i in range(N):
        p = nodes[i]['transform']['position']
        if i > 0:
            prev = nodes[i-1]['transform']['position']
            dx = p['x'] - prev['x']
            dy = p['y'] - prev['y']
            dz = p['z'] - prev['z']
            step = math.sqrt(dx*dx + dy*dy + dz*dz)
            dists.append(dists[-1] + step)

    lap_length = dists[-1]
    # Sector split distances
    r1, r2 = meta.get('sector_splits_ratio', [0.333, 0.666])
    sector_ends = [round(lap_length * r1, 2), round(lap_length * r2, 2), round(lap_length, 2)]

    centerline_dicts = []
    center_path_coords = []
    for i in range(N):
        p = nodes[i]['transform']['position']
        lat, lon = cartesian_to_gps(p['x'], p['z'], origin_lat, origin_lon)
        dist = dists[i]

        sector = 0
        if dist >= sector_ends[1]:
            sector = 2
        elif dist >= sector_ends[0]:
            sector = 1

        centerline_dicts.append({
            'lat': lat,
            'lon': lon,
            'dist': round(dist, 4),
            'sector': sector
        })
        center_path_coords.append([lat, lon, round(p['y'], 2)])

    # Close loop on centerline if not closed
    if centerline_dicts:
        last = centerline_dicts[-1]
        first = centerline_dicts[0]
        if abs(last['lat'] - first['lat']) > 1e-6 or abs(last['lon'] - first['lon']) > 1e-6:
            centerline_dicts.append({
                'lat': first['lat'],
                'lon': first['lon'],
                'dist': round(lap_length, 4),
                'sector': 2
            })
            center_path_coords.append(center_path_coords[0])

    # 2. Compute Left and Right track boundaries
    left_path_coords = []
    right_path_coords = []

    for i in range(N):
        curr = nodes[i]['transform']['position']
        prev = nodes[(i - 1 + N) % N]['transform']['position']
        nxt = nodes[(i + 1) % N]['transform']['position']

        dx = nxt['x'] - prev['x']
        dz = nxt['z'] - prev['z']
        length = math.hypot(dx, dz)
        if length < 1e-6:
            tx, tz = 1.0, 0.0
        else:
            tx, tz = dx / length, dz / length

        # Left normal in X-Z plane
        nx, nz = -tz, tx

        # Find nearest control point width
        best_d2 = float('inf')
        best_wl = 7.0
        best_wr = 7.0
        for cx, cy, cz, wl, wr in control_points:
            d2 = (curr['x'] - cx)**2 + (curr['z'] - cz)**2
            if d2 < best_d2:
                best_d2 = d2
                best_wl = wl
                best_wr = wr

        lx = curr['x'] + nx * best_wl
        lz = curr['z'] + nz * best_wl
        rx = curr['x'] - nx * best_wr
        rz = curr['z'] - nz * best_wr

        lat_l, lon_l = cartesian_to_gps(lx, lz, origin_lat, origin_lon)
        lat_r, lon_r = cartesian_to_gps(rx, rz, origin_lat, origin_lon)

        left_path_coords.append([lat_l, lon_l, round(curr['y'], 2)])
        right_path_coords.append([lat_r, lon_r, round(curr['y'], 2)])

    # Close loops for boundaries
    if left_path_coords:
        left_path_coords.append(left_path_coords[0])
    if right_path_coords:
        right_path_coords.append(right_path_coords[0])

    # 3. Output files
    os.makedirs(output_dir, exist_ok=True)
    slug = track_slug.lower()

    # Track JSON
    track_json_data = {
        'track_name': meta['track_name'],
        'timezone': meta['timezone'],
        'aliases': meta['aliases'],
        'lap_length': round(lap_length, 4),
        'sector_end': sector_ends,
        'origin': meta['origin'],
        'center_line': centerline_dicts,
        'turn_end': [],
        'turns': []
    }
    json_path = os.path.join(output_dir, f'{slug}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(track_json_data, f, indent=4)
    print(f"Generated track JSON: {json_path}")

    # Track GeoJSON
    geojson_path = os.path.join(output_dir, f'{slug}.geojson')
    geojson_utils.generate_track_limits_geojson(
        center_path=center_path_coords,
        left_path=left_path_coords,
        right_path=right_path_coords,
        name=f"{meta['track_name']} Track Layout",
        output_path=geojson_path,
        include_centerline=True
    )
    print(f"Generated track GeoJSON: {geojson_path}")

    # Track KML
    kml_path = os.path.join(output_dir, f'{slug}.kml')
    kml_utils.generate_track_limits_kml(
        center_path=center_path_coords,
        left_path=left_path_coords,
        right_path=right_path_coords,
        name=f"{meta['track_name']} Track Layout",
        output_path=kml_path,
        include_centerline=True
    )
    print(f"Generated track KML: {kml_path}")


def main():
    default_kspkg = r"C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa EVO\content.kspkg"
    default_out = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'tracks'))

    parser = argparse.ArgumentParser(description="Assetto Corsa Evo Track Extractor")
    parser.add_argument("--track", default="monza", help="Track name / slug (e.g. monza, imola, spa)")
    parser.add_argument("--kspkg", default=default_kspkg, help="Path to content.kspkg")
    parser.add_argument("--out", default=default_out, help="Output directory for track files")
    args = parser.parse_args()

    extract_track(args.track, args.kspkg, args.out)


if __name__ == "__main__":
    main()
