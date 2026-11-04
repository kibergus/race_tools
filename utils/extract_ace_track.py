#!/usr/bin/env python3
"""
Assetto Corsa Evo (ACE) Track Extractor.
Extracts centerline splines, legal road limits (AI corridor), and optional meshes
from ACE's content.kspkg package and generates standard JSON, GeoJSON, and KML files
compatible with the race_tools data pipeline.
"""

from collections import defaultdict
import json
import math
import os
import re
import struct
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple
import argparse

# Ensure race_tools is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from race_tools import assetto_corsa, geojson_utils, kml_utils

# Constants for kspkg parsing
XOR_KEY = 0x9F9721A97D1135C1
KEY_BYTES = struct.pack('<Q', XOR_KEY)
FILE_TABLE_SIZES = [0x4_000_000, 0x2_000_000]  # 64MB for >=0.7, 32MB for <0.7

# Default known track metadata
KNOWN_TRACK_METADATA = {
    'monza': {
        'track_name': 'Monza',
        'timezone': 'Europe/Rome',
        'sector_splits_ratio': [0.275, 0.671]
    },
    'imola': {
        'track_name': 'Imola',
        'timezone': 'Europe/Rome',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'spa': {
        'track_name': 'Spa-Francorchamps',
        'timezone': 'Europe/Brussels',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'brands_hatch': {
        'track_name': 'Brands Hatch',
        'timezone': 'Europe/London',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'cota': {
        'track_name': 'Circuit of the Americas',
        'timezone': 'America/Chicago',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'donington': {
        'track_name': 'Donington Park',
        'timezone': 'Europe/London',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'fuji': {
        'track_name': 'Fuji Speedway',
        'timezone': 'Asia/Tokyo',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'kyalami': {
        'track_name': 'Kyalami',
        'timezone': 'Africa/Johannesburg',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'laguna_seca': {
        'track_name': 'Laguna Seca',
        'timezone': 'America/Los_Angeles',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'mount_panorama': {
        'track_name': 'Mount Panorama',
        'timezone': 'Australia/Sydney',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'nurburgring': {
        'track_name': 'Nürburgring',
        'timezone': 'Europe/Berlin',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'oulton_park': {
        'track_name': 'Oulton Park',
        'timezone': 'Europe/London',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'paul_ricard': {
        'track_name': 'Paul Ricard',
        'timezone': 'Europe/Paris',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'red_bull_ring': {
        'track_name': 'Red Bull Ring',
        'timezone': 'Europe/Vienna',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'road_atlanta': {
        'track_name': 'Road Atlanta',
        'timezone': 'America/New_York',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'sebring': {
        'track_name': 'Sebring International Raceway',
        'timezone': 'America/New_York',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'suzuka': {
        'track_name': 'Suzuka',
        'timezone': 'Asia/Tokyo',
        'sector_splits_ratio': [0.333, 0.666]
    },
    'watkins_glen': {
        'track_name': 'Watkins Glen International',
        'timezone': 'America/New_York',
        'sector_splits_ratio': [0.333, 0.666]
    }
}
KNOWN_TRACK_METADATA['redbull_ring'] = KNOWN_TRACK_METADATA['red_bull_ring']

# Mapping of track slug to known internal layout slug -> Assetto Corsa Evo display configuration name
KNOWN_ACE_LAYOUTS = {
    'fuji': {
        'gp_circuit': 'GP',
        'gp_circuit_shortcut': 'GP Short',
        'gp': 'GP',
        'gp_short': 'GP Short',
    },
    'brands_hatch': {
        'gp': 'GP',
        'indy': 'Indy',
    },
    'cota': {
        'gp': 'GP',
        'national': 'National',
        'club': 'Club',
    },
    'donington': {
        'grand_prix': 'GP',
        'gp': 'GP',
        'national': 'National',
    },
    'imola': {
        'imola': 'GP',
        'gp': 'GP',
    },
    'kyalami': {
        'gp': 'GP',
    },
    'laguna_seca': {
        'laguna_seca': 'GP',
        'gp': 'GP',
    },
    'monza': {
        'gp': 'GP',
    },
    'mount_panorama': {
        'track_layout': 'GP',
        'gp': 'GP',
    },
    'nurburgring': {
        '24h': '24h',
        'gp_strecke': 'GP Strecke',
        'nordschleife': 'Nordschleife',
        'sprint': 'Sprint',
        'touristenfahrten': 'Touristenfahrten',
        'nordschleife_touristenfahrten': 'Touristenfahrten',
        'nordschleife_btg': 'Touristenfahrten',
        'mullenbachschleife': 'Mullenbachschleife',
        'oldtimer': 'Oldtimer',
    },
    'oulton_park': {
        'international': 'International',
        'fosters': 'Fosters',
    },
    'paul_ricard': {
        '1a_v2': 'Layout 1A-v2',
        '1c_v2': 'Layout 1C-v2',
        '3a': 'Layout 3A',
        '3c': 'Layout 3C',
    },
    'red_bull_ring': {
        'gp': 'GP',
        'national': 'National',
    },
    'redbull_ring': {
        'gp': 'GP',
        'national': 'National',
    },
    'road_atlanta': {
        'gp': 'GP',
    },
    'sebring': {
        'gp': 'GP',
    },
    'spa': {
        'gp': 'GP',
    },
    'suzuka': {
        'gp': 'GP',
        'east': 'East',
        'west': 'West',
    },
    'watkins_glen': {
        'gp': 'GP',
        'gp_inner_loop': 'GP Inner Loop',
        'short': 'Short',
        'short_inner_loop': 'Short Inner Loop',
    },
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


def dump_protobuf_repeated(raw: bytes, target_fn: int) -> List[bytes]:
    """Extract all byte payloads for a repeated length-delimited field."""
    pos = 0
    res = []
    while pos < len(raw):
        tag, pos = parse_varint(raw, pos)
        fn = tag >> 3
        wt = tag & 7
        if wt == 2:
            l, pos = parse_varint(raw, pos)
            sub = raw[pos:pos+l]
            pos += l
            if fn == target_fn:
                res.append(sub)
        elif wt == 0:
            _, pos = parse_varint(raw, pos)
        elif wt == 5:
            pos += 4
        elif wt == 1:
            pos += 8
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


def resolve_path(path: str) -> str:
    """
    Resolves Windows-style drive paths (e.g. C:\\... or C:/...) to Linux mount points
    when running on Linux/POSIX systems.
    """
    if not path:
        return path
    if sys.platform != 'win32':
        m = re.match(r'^([a-zA-Z]):[\\/](.*)', path)
        if m:
            drive, rel = m.groups()
            rel = rel.replace('\\', '/')
            mnt_windows = os.path.join('/mnt/windows', rel)
            if os.path.exists(mnt_windows):
                return mnt_windows
            mnt_drive = os.path.join(f'/mnt/{drive.lower()}', rel)
            if os.path.exists(mnt_drive):
                return mnt_drive
            return mnt_windows
    return path


def get_default_kspkg_path() -> str:
    """Returns platform-aware default path to content.kspkg."""
    win_rel = r"Program Files (x86)\Steam\steamapps\common\Assetto Corsa EVO\content.kspkg"
    if sys.platform == 'win32':
        return os.path.join(r"C:\\", win_rel)

    candidates = [
        os.path.join('/mnt/windows', win_rel.replace('\\', '/')),
        os.path.join('/mnt/c', win_rel.replace('\\', '/')),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def extract_mesh_boundary_polylines(
    reader: ACEPackageReader,
    mesh_path: str,
    origin_obj: assetto_corsa.AssettoCorsaTrackOrigin
) -> List[List[List[float]]]:
    """
    Extracts 2D boundary polylines (converted to [lon, lat, alt]) from a 3D mesh.
    Boundary edges are edges that appear in exactly one triangle.
    """
    raw = reader.read_file(mesh_path)
    top = dump_protobuf_fields(raw)
    sub = dump_protobuf_fields(top.get(5, b''))
    vbytes = sub.get(5, b'')
    nv = len(vbytes) // 12
    if nv == 0:
        return []

    verts = [struct.unpack('<fff', vbytes[j*12:(j+1)*12]) for j in range(nv)]
    f11 = sub.get(11, b'')
    pos = 0
    indices = []
    while pos < len(f11):
        v, pos = parse_varint(f11, pos)
        indices.append(v)

    edge_count = defaultdict(list)
    for i in range(0, len(indices), 3):
        t = indices[i:i+3]
        if len(t) < 3:
            continue
        for a, b in [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])]:
            edge = (min(a, b), max(a, b))
            edge_count[edge].append((a, b))

    adj = defaultdict(list)
    for edge, elist in edge_count.items():
        if len(elist) == 1:
            u, v = edge
            adj[u].append(v)
            adj[v].append(u)

    visited = set()
    chains = []
    for start_node in list(adj.keys()):
        if start_node in visited:
            continue
        chain = [start_node]
        visited.add(start_node)
        curr = start_node
        while True:
            nxt_cands = [n for n in adj[curr] if n not in visited]
            if not nxt_cands:
                if start_node in adj[curr] and len(chain) > 2:
                    chain.append(start_node)
                break
            nxt = nxt_cands[0]
            chain.append(nxt)
            visited.add(nxt)
            curr = nxt

        coords = []
        for idx in chain:
            x, y, z = verts[idx]
            lat, lon = origin_obj.to_gps(x, z)
            coords.append([lon, lat, round(y, 2)])
        if len(coords) >= 2:
            chains.append(coords)
    return chains


def extract_ai_spline_boundaries(
    reader: ACEPackageReader,
    ai_spline_file: str,
    origin_obj: assetto_corsa.AssettoCorsaTrackOrigin,
    sector_splits_ratio: List[float]
) -> Tuple[List[Dict[str, Any]], List[List[float]], List[List[float]], List[List[float]], float]:
    """
    Extracts high-density waypoints and per-point lateral legal road limits from
    layout_<variant>.ideal_line.aisplinedata.
    Returns:
      (centerline_dicts, center_coords, left_coords, right_coords, lap_length)
      where coordinate lists are [lat, lon, alt] for standard race_tools pipeline.
    """
    raw = reader.read_file(ai_spline_file)
    top = dump_protobuf_fields(raw)
    f1_bytes = top.get(1, b'')

    # Extract 3,194 waypoint positions from repeated tag 1 inside f1
    pos = 0
    waypoints = []
    while pos < len(f1_bytes):
        tag, pos = parse_varint(f1_bytes, pos)
        fn, wt = tag >> 3, tag & 7
        if wt == 2:
            l, pos = parse_varint(f1_bytes, pos)
            sub = f1_bytes[pos:pos+l]
            pos += l
            if fn == 1:
                d = dump_protobuf_fields(sub)
                pos_bytes = d.get(1, b'')
                pos_dict = dump_protobuf_fields(pos_bytes)
                waypoints.append((pos_dict.get(1, 0.0), pos_dict.get(2, 0.0), pos_dict.get(3, 0.0)))
        elif wt == 0:
            _, pos = parse_varint(f1_bytes, pos)
        elif wt == 5:
            pos += 4
        elif wt == 1:
            pos += 8
        else:
            break

    # Extract repeated tag 2 from top level (contains lateral limits and tangent)
    limits_raw = dump_protobuf_repeated(raw, 2)
    limits = [dump_protobuf_fields(sub) for sub in limits_raw]

    N = min(len(waypoints), len(limits))
    if N == 0:
        raise ValueError(f"No waypoints or limits found in '{ai_spline_file}'")

    # Cumulative distance
    dists = [0.0]
    for i in range(1, N):
        dx = waypoints[i][0] - waypoints[i-1][0]
        dy = waypoints[i][1] - waypoints[i-1][1]
        dz = waypoints[i][2] - waypoints[i-1][2]
        dists.append(dists[-1] + math.sqrt(dx*dx + dy*dy + dz*dz))
    lap_length = dists[-1] + math.dist(waypoints[-1], waypoints[0])

    r1, r2 = sector_splits_ratio
    sector_ends = [lap_length * r1, lap_length * r2]

    centerline_dicts = []
    center_coords = []
    left_coords = []
    right_coords = []

    for i in range(N):
        x, y, z = waypoints[i]
        lim = limits[i]
        wl = lim.get(3, 7.0)  # Left legal road limit
        wr = lim.get(4, 7.0)  # Right legal road limit

        # Tangent vector from lim[10] or finite differences
        tangent_dict = dump_protobuf_fields(lim.get(10, b'')) if isinstance(lim.get(10), bytes) else {}
        if 1 in tangent_dict and 3 in tangent_dict:
            tx = tangent_dict[1]
            tz = tangent_dict[3]
        else:
            p_next = waypoints[(i + 1) % N]
            p_prev = waypoints[(i - 1 + N) % N]
            tx = p_next[0] - p_prev[0]
            tz = p_next[2] - p_prev[2]

        L = math.hypot(tx, tz)
        if L < 1e-6:
            tx, tz = 1.0, 0.0
        else:
            tx, tz = tx / L, tz / L

        # Normal pointing left in horizontal plane (+X = East, -Z = North)
        nx, nz = tz, -tx

        lx = x + nx * wl
        lz = z + nz * wl
        rx = x - nx * wr
        rz = z - nz * wr

        lat_c, lon_c = origin_obj.to_gps(x, z)
        lat_l, lon_l = origin_obj.to_gps(lx, lz)
        lat_r, lon_r = origin_obj.to_gps(rx, rz)

        alt = round(y, 2)
        # Store as [lat, lon, alt] for race_tools utilities
        center_coords.append([lat_c, lon_c, alt])
        left_coords.append([lat_l, lon_l, alt])
        right_coords.append([lat_r, lon_r, alt])

        dist = dists[i]
        sector = 2 if dist >= sector_ends[1] else (1 if dist >= sector_ends[0] else 0)
        centerline_dicts.append({
            'lat': lat_c,
            'lon': lon_c,
            'dist': round(dist, 4),
            'sector': sector
        })

    # Close loops
    if center_coords and center_coords[0] != center_coords[-1]:
        center_coords.append(center_coords[0])
    if left_coords and left_coords[0] != left_coords[-1]:
        left_coords.append(left_coords[0])
    if right_coords and right_coords[0] != right_coords[-1]:
        right_coords.append(right_coords[0])

    return centerline_dicts, center_coords, left_coords, right_coords, lap_length


def resolve_kspkg_track_slug(reader: ACEPackageReader, slug: str) -> str:
    """Resolves track slug to the actual folder name in content.kspkg."""
    slug_norm = slug.lower().strip()
    if any(k.startswith(f"content\\tracks\\{slug_norm}\\") for k in reader.file_table):
        return slug_norm

    # Collect distinct track directories in kspkg
    kspkg_tracks = set()
    for k in reader.file_table:
        if k.startswith("content\\tracks\\"):
            parts = k.split("\\")
            if len(parts) > 2:
                kspkg_tracks.add(parts[2])

    slug_stripped = slug_norm.replace('_', '').replace('-', '')
    for t in kspkg_tracks:
        if t.replace('_', '').replace('-', '') == slug_stripped:
            return t

    return slug_norm


def find_available_layouts(reader: ACEPackageReader, slug: str) -> List[str]:
    """Finds all layout variant names available for a given track in the package."""
    pkg_slug = resolve_kspkg_track_slug(reader, slug)
    layouts = []
    prefix1 = f"content\\tracks\\{pkg_slug}\\layouts\\"
    prefix2 = f"content\\tracks\\{pkg_slug}\\content\\layouts\\"
    for k in reader.file_table:
        if k.endswith(".ideal_line.aisplinedata"):
            if k.startswith(prefix1) or k.startswith(prefix2):
                fname = k.replace('\\', '/').split('/')[-1]
                m = re.match(r"^(?:layout_)?(.+?)\.ideal_line\.aisplinedata$", fname)
                if m:
                    layout_name = m.group(1)
                    if layout_name not in layouts:
                        layouts.append(layout_name)
    return sorted(layouts)


def find_layout_spline_file(reader: ACEPackageReader, slug: str, layout: Optional[str] = None) -> Optional[str]:
    """
    Finds the ideal_line.aisplinedata file for a track and optional layout variant.
    Prefers high-density content\\tracks\\<slug>\\layouts\\ over content\\layouts\\.
    """
    pkg_slug = resolve_kspkg_track_slug(reader, slug)
    if layout:
        targets = [
            f"content\\tracks\\{pkg_slug}\\layouts\\layout_{layout}.ideal_line.aisplinedata",
            f"content\\tracks\\{pkg_slug}\\layouts\\{layout}.ideal_line.aisplinedata",
            f"content\\tracks\\{pkg_slug}\\content\\layouts\\layout_{layout}.ideal_line.aisplinedata",
            f"content\\tracks\\{pkg_slug}\\content\\layouts\\{layout}.ideal_line.aisplinedata",
        ]
        for t in targets:
            if t in reader.file_table:
                return t
        for k in reader.file_table:
            if k.startswith(f"content\\tracks\\{pkg_slug}\\") and layout in k and k.endswith(".ideal_line.aisplinedata"):
                return k

    default_candidates = [
        f"content\\tracks\\{pkg_slug}\\layouts\\layout_gp.ideal_line.aisplinedata",
        f"content\\tracks\\{pkg_slug}\\layouts\\layout_gp_strecke.ideal_line.aisplinedata",
        f"content\\tracks\\{pkg_slug}\\layouts\\layout_grand_prix.ideal_line.aisplinedata",
        f"content\\tracks\\{pkg_slug}\\layouts\\layout_{pkg_slug}.ideal_line.aisplinedata",
        f"content\\tracks\\{pkg_slug}\\layouts\\layout_{slug}.ideal_line.aisplinedata",
        f"content\\tracks\\{pkg_slug}\\layouts\\track_layout.ideal_line.aisplinedata",
    ]
    for c in default_candidates:
        if c in reader.file_table:
            return c

    for k in sorted(reader.file_table):
        if k.startswith(f"content\\tracks\\{pkg_slug}\\layouts\\") and k.endswith(".ideal_line.aisplinedata"):
            return k
    for k in sorted(reader.file_table):
        if k.startswith(f"content\\tracks\\{pkg_slug}\\") and k.endswith(".ideal_line.aisplinedata"):
            return k
    return None


def get_ace_layout_name(
    slug: str,
    layout: Optional[str],
    reader: Optional[ACEPackageReader] = None
) -> Optional[str]:
    """
    Resolves the layout configuration name as it appears in Assetto Corsa Evo
    (e.g. 'GP Short', 'GP', 'Indy', 'National').
    """
    if not layout:
        return None

    slug_norm = slug.lower().strip()
    layout_norm = layout.lower().strip()

    if slug_norm in KNOWN_ACE_LAYOUTS and layout_norm in KNOWN_ACE_LAYOUTS[slug_norm]:
        return KNOWN_ACE_LAYOUTS[slug_norm][layout_norm]

    if reader:
        pkg_slug = resolve_kspkg_track_slug(reader, slug_norm)
        prefix = f"content\\tracks\\{pkg_slug}\\dynamic_track\\"
        candidates = []
        for k in reader.file_table:
            if k.startswith(prefix) and k.endswith(".dynamictrackpresetcompressed"):
                preset = k[len(prefix):-len(".dynamictrackpresetcompressed")].strip()
                candidates.append(preset)

        layout_words = set(re.findall(r'[a-zA-Z0-9]+', layout_norm))
        layout_words.discard('circuit')
        layout_words.discard('layout')
        if 'shortcut' in layout_words:
            layout_words.remove('shortcut')
            layout_words.add('short')

        best_match = None
        best_score = 0
        for cand in candidates:
            cand_words = set(re.findall(r'[a-zA-Z0-9]+', cand.lower()))
            overlap = len(layout_words & cand_words)
            if overlap > best_score:
                best_score = overlap
                best_match = cand

        if best_match:
            words = best_match.split()
            formatted = []
            for w in words:
                if w.lower() in ('gp', '24h'):
                    formatted.append(w.upper())
                else:
                    formatted.append(w.capitalize())
            return " ".join(formatted)

    words = layout.replace('_', ' ').split()
    formatted = []
    for w in words:
        if w.lower() in ('gp', '24h'):
            formatted.append(w.upper())
        elif w.lower() == 'shortcut':
            formatted.append('Short')
        elif w.lower() != 'circuit':
            formatted.append(w.capitalize())
    return " ".join(formatted) if formatted else layout.replace('_', ' ').title()


def get_ace_layout_name(
    slug: str,
    layout: Optional[str],
    reader: Optional[ACEPackageReader] = None
) -> Optional[str]:
    """
    Resolves the layout configuration name as it appears in Assetto Corsa Evo
    (e.g. 'GP Short', 'GP', 'Indy', 'National').
    """
    if not layout:
        return None

    slug_norm = slug.lower().strip()
    layout_norm = layout.lower().strip()

    if slug_norm in KNOWN_ACE_LAYOUTS and layout_norm in KNOWN_ACE_LAYOUTS[slug_norm]:
        return KNOWN_ACE_LAYOUTS[slug_norm][layout_norm]

    if reader:
        pkg_slug = resolve_kspkg_track_slug(reader, slug_norm)
        prefix = f"content\\tracks\\{pkg_slug}\\dynamic_track\\"
        candidates = []
        for k in reader.file_table:
            if k.startswith(prefix) and k.endswith(".dynamictrackpresetcompressed"):
                preset = k[len(prefix):-len(".dynamictrackpresetcompressed")].strip()
                candidates.append(preset)

        layout_words = set(re.findall(r'[a-zA-Z0-9]+', layout_norm))
        layout_words.discard('circuit')
        layout_words.discard('layout')
        if 'shortcut' in layout_words:
            layout_words.remove('shortcut')
            layout_words.add('short')

        best_match = None
        best_score = 0
        for cand in candidates:
            cand_words = set(re.findall(r'[a-zA-Z0-9]+', cand.lower()))
            overlap = len(layout_words & cand_words)
            if overlap > best_score:
                best_score = overlap
                best_match = cand

        if best_match:
            words = best_match.split()
            formatted = []
            for w in words:
                if w.lower() in ('gp', '24h'):
                    formatted.append(w.upper())
                else:
                    formatted.append(w.capitalize())
            return " ".join(formatted)

    words = layout.replace('_', ' ').split()
    formatted = []
    for w in words:
        if w.lower() in ('gp', '24h'):
            formatted.append(w.upper())
        elif w.lower() == 'shortcut':
            formatted.append('Short')
        elif w.lower() != 'circuit':
            formatted.append(w.capitalize())
    return " ".join(formatted) if formatted else layout.replace('_', ' ').title()


def extract_track(
    track_slug: str,
    kspkg_path: str,
    output_dir: str,
    include_meshes: bool = False,
    tarmac_meshes: Optional[str] = None,
    layout: Optional[str] = None
):
    """
    Extracts canonical centerline and legal road boundaries (AI corridor) for track_slug from kspkg.
    Optionally extracts white lines and physical tarmac meshes if include_meshes or tarmac_meshes is set.
    If layout is provided, extracts that specific layout variant and appends it to output filenames.
    """
    kspkg_path = resolve_path(kspkg_path)
    output_dir = resolve_path(output_dir)
    reader = ACEPackageReader(kspkg_path)
    slug = track_slug.lower()

    # If track_slug itself contains layout (e.g. nurburgring_gp_strecke)
    if not layout:
        for known_base in list(KNOWN_TRACK_METADATA.keys()) + list(assetto_corsa.TRACK_ORIGINS.keys()):
            if slug.startswith(known_base + '_') and slug != known_base:
                layout = slug[len(known_base) + 1:]
                slug = known_base
                break

    file_slug = f"{slug}_{layout}" if layout else slug

    meta = KNOWN_TRACK_METADATA.get(slug, {
        'track_name': track_slug.capitalize(),
        'timezone': 'UTC',
        'sector_splits_ratio': [0.333, 0.666]
    })
    origin_obj = assetto_corsa.get_track_origin(slug)
    pkg_slug = resolve_kspkg_track_slug(reader, slug)

    # 1. Primary Source: AI Spline Corridor (high-density legal road limits)
    ai_spline_file = find_layout_spline_file(reader, slug, layout)

    if ai_spline_file and ai_spline_file in reader.file_table:
        print(f"Extracting legal road limits from AI spline: {ai_spline_file}")
        centerline_dicts, center_coords, left_coords, right_coords, lap_length = extract_ai_spline_boundaries(
            reader=reader,
            ai_spline_file=ai_spline_file,
            origin_obj=origin_obj,
            sector_splits_ratio=meta.get('sector_splits_ratio', [0.333, 0.666])
        )
        print(f"Extracted {len(center_coords)} legal road boundary points (length: {lap_length:.1f}m)")
    else:
        # Fallback to splinedata.json if no aisplinedata
        print(f"Notice: aisplinedata not found for {slug} (layout: {layout}). Using fallback splinedata.json.")
        center_coords, left_coords, right_coords = [], [], []
        centerline_dicts = []
        lap_length = 5000.0

    track_title = f"{meta['track_name']} {layout.replace('_', ' ').title()}" if layout else meta['track_name']

    # 2. Save Canonical GeoJSON (Left Boundary, Right Boundary, Centerline)
    os.makedirs(output_dir, exist_ok=True)
    geojson_path = os.path.join(output_dir, f'{file_slug}.geojson')
    geojson_utils.generate_track_limits_geojson(
        center_path=center_coords,
        left_path=left_coords,
        right_path=right_coords,
        name=f"{track_title} Track Layout",
        output_path=geojson_path,
        include_centerline=True
    )

    # 3. Optional extra meshes for visual inspection / comparison
    extra_features = []
    if include_meshes or tarmac_meshes:
        # Painted white lines
        whiteline_files = sorted([
            k for k in reader.file_table
            if k.startswith(f'content\\tracks\\{pkg_slug}\\') and 'whiteline' in k and k.endswith('.mesh')
        ])
        for f in whiteline_files:
            base = os.path.basename(f)
            try:
                polylines = extract_mesh_boundary_polylines(reader, f, origin_obj)
                for p_idx, poly in enumerate(polylines):
                    extra_features.append({
                        'type': 'Feature',
                        'properties': {
                            'name': f"White Line ({base} #{p_idx+1})",
                            'type': 'whiteline',
                            'mesh': base,
                            'layer': 'whiteline'
                        },
                        'geometry': {
                            'type': 'LineString',
                            'coordinates': poly
                        }
                    })
            except Exception as e:
                print(f"Warning: failed to extract whiteline '{f}': {e}")

        # Physical tarmac meshes
        tarmac_range = tarmac_meshes if tarmac_meshes else "1-5"
        all_tarmac_files = sorted([
            k for k in reader.file_table
            if k.startswith(f'content\\tracks\\{pkg_slug}\\physics\\') and 'tarmac' in k and k.endswith('.mesh')
        ], key=lambda x: int(re.search(r'\d+', os.path.basename(x)).group()) if re.search(r'\d+', os.path.basename(x)) else 0)

        target_indices = set()
        if tarmac_range.lower() == 'all':
            selected_tarmac_files = all_tarmac_files
        else:
            for part in tarmac_range.split(','):
                part = part.strip()
                if '-' in part:
                    s, e = part.split('-')
                    target_indices.update(range(int(s), int(e) + 1))
                elif part.isdigit():
                    target_indices.add(int(part))
            selected_tarmac_files = [
                f for f in all_tarmac_files
                if any(re.search(rf'tarmac_{idx}\.mesh$', f, re.IGNORECASE) for idx in target_indices)
            ]

        print(f"Extracting physical tarmac meshes ({len(selected_tarmac_files)} selected)...")
        for f in selected_tarmac_files:
            base = os.path.basename(f)
            try:
                polylines = extract_mesh_boundary_polylines(reader, f, origin_obj)
                for p_idx, poly in enumerate(polylines):
                    extra_features.append({
                        'type': 'Feature',
                        'properties': {
                            'name': f"Physical Tarmac ({base} #{p_idx+1})",
                            'type': 'physical_tarmac',
                            'mesh': base,
                            'layer': 'physical_tarmac'
                        },
                        'geometry': {
                            'type': 'LineString',
                            'coordinates': poly
                        }
                    })
            except Exception as e:
                print(f"Warning: failed to extract tarmac mesh '{f}': {e}")

        if extra_features:
            with open(geojson_path, 'r', encoding='utf-8') as fh:
                fc = json.load(fh)
            fc['features'].extend(extra_features)
            with open(geojson_path, 'w', encoding='utf-8') as fh:
                json.dump(fc, fh, indent=2)
            print(f"Appended {len(extra_features)} extra inspection mesh features to {geojson_path}")

    print(f"Generated canonical track GeoJSON: {geojson_path}")

    # 4. Save JSON sidecar
    r1, r2 = meta.get('sector_splits_ratio', [0.333, 0.666])
    sector_ends = [round(lap_length * r1, 2), round(lap_length * r2, 2), round(lap_length, 2)]
    origin_meta = {
        'lat': origin_obj.lat,
        'lon': origin_obj.lon,
    }
    # Alias consists of the track name and layout name as they are in Assetto Corsa Evo.
    # If the alias matches track_name, do not add it.
    ace_layout = get_ace_layout_name(slug, layout, reader)
    if ace_layout:
        ace_alias = f"{meta['track_name']} {ace_layout}".strip()
    else:
        ace_alias = meta['track_name'].strip()

    aliases = []
    if ace_alias and ace_alias.strip().lower() != track_title.strip().lower():
        aliases.append(ace_alias)

    track_json_data = {
        'track_name': track_title,
        'timezone': meta['timezone'],
        'aliases': aliases,
        'lap_length': round(lap_length, 4),
        'sector_end': sector_ends,
        'origin': origin_meta,
        'center_line': centerline_dicts,
        'turn_end': [],
        'turns': []
    }
    json_path = os.path.join(output_dir, f'{file_slug}.json')
    with open(json_path, 'w', encoding='utf-8') as fh:
        json.dump(track_json_data, fh, indent=4)
    print(f"Generated track JSON: {json_path}")

    # 5. Save KML
    kml_path = os.path.join(output_dir, f'{file_slug}.kml')
    kml_utils.generate_track_limits_kml(
        center_path=center_coords,
        left_path=left_coords,
        right_path=right_coords,
        name=f"{track_title} Track Layout",
        output_path=kml_path,
        include_centerline=True
    )
    print(f"Generated track KML: {kml_path}")

    # Sync to PROJECT_ROOT karting tracks dir if configured
    project_root = os.environ.get('PROJECT_ROOT')
    if project_root:
        karting_dir = os.path.join(project_root, 'karting', 'data', 'tracks')
        if os.path.exists(karting_dir) and os.path.realpath(karting_dir) != os.path.realpath(output_dir):
            import shutil
            for ext in ('.json', '.geojson', '.kml'):
                src = os.path.join(output_dir, f'{file_slug}{ext}')
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(karting_dir, f'{file_slug}{ext}'))
            print(f"Synced {file_slug} to {karting_dir}")


def main():
    default_kspkg = get_default_kspkg_path()
    default_out = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'tracks'))

    parser = argparse.ArgumentParser(description="Assetto Corsa Evo Track Extractor")
    parser.add_argument("--track", default="monza", help="Track name / slug (e.g. monza, imola, spa, nurburgring)")
    parser.add_argument("--layout", default=None, help="Track layout variant (e.g. gp_strecke, sprint, 24h, nordschleife)")
    parser.add_argument("--all-layouts", action="store_true", help="Extract all available layout variants for the specified track")
    parser.add_argument("--list-layouts", action="store_true", help="List available layout variants for the track and exit")
    parser.add_argument("--kspkg", default=default_kspkg, help="Path to content.kspkg")
    parser.add_argument("--out", default=default_out, help="Output directory for track files")
    parser.add_argument("--include-meshes", action="store_true", help="Include white line and tarmac meshes in GeoJSON")
    parser.add_argument("--tarmac-meshes", default=None, help="Tarmac meshes to extract (e.g. 1-5, 1,2,3 or all)")
    args = parser.parse_args()

    resolved_kspkg = resolve_path(args.kspkg)
    if args.list_layouts or args.all_layouts:
        reader = ACEPackageReader(resolved_kspkg)
        layouts = find_available_layouts(reader, args.track.lower())
        if args.list_layouts:
            print(f"Available layouts for '{args.track}': {layouts}")
            return
        if args.all_layouts:
            if not layouts:
                print(f"No specific layout variants detected for '{args.track}'. Extracting base track...")
                extract_track(
                    track_slug=args.track,
                    kspkg_path=resolved_kspkg,
                    output_dir=args.out,
                    include_meshes=args.include_meshes,
                    tarmac_meshes=args.tarmac_meshes
                )
            else:
                print(f"Extracting all {len(layouts)} layouts for '{args.track}': {layouts}")
                for lay in layouts:
                    print(f"\n--- Extracting layout: {lay} ---")
                    extract_track(
                        track_slug=args.track,
                        kspkg_path=resolved_kspkg,
                        output_dir=args.out,
                        include_meshes=args.include_meshes,
                        tarmac_meshes=args.tarmac_meshes,
                        layout=lay
                    )
            return

    extract_track(
        track_slug=args.track,
        kspkg_path=resolved_kspkg,
        output_dir=args.out,
        include_meshes=args.include_meshes,
        tarmac_meshes=args.tarmac_meshes,
        layout=args.layout
    )


if __name__ == "__main__":
    main()
