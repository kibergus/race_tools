import struct
import math
import numpy as np
from scipy.interpolate import interp1d
from scipy.signal import savgol_filter
from dataclasses import dataclass
from typing import Any

import subprocess
import json


@dataclass
class GPMFSample:
    key: str
    type: str
    size: int
    count: int
    data: bytes


def parse_gpmf(data: bytes) -> list[GPMFSample]:
    samples: list[GPMFSample] = []
    i = 0
    while i + 8 <= len(data):
        fourcc = data[i:i + 4].decode('ascii', errors='ignore')
        type_char = chr(data[i + 4])
        size = data[i + 5]
        count = struct.unpack('>H', data[i + 6:i + 8])[0]
        i += 8
        total_size = size * count
        padded_size = (total_size + 3) & ~3
        sample_data = data[i:i + total_size]
        samples.append(GPMFSample(fourcc, type_char, size, count, sample_data))
        i += padded_size
    return samples


def decode_type(type_char: str, size: int, count: int, data: bytes) -> Any:
    if not data:
        return None
    formats = {
        'f': 'f', 'L': 'I', 's': 'h', 'S': 'H', 'b': 'b', 'B': 'B',
        'd': 'd', 'q': 'q', 'Q': 'Q', 'l': 'i', 'J': 'Q', 'j': 'q'
    }
    if type_char in formats:
        fmt = formats[type_char]
        item_size = struct.calcsize('>' + fmt)
        actual_count = len(data) // item_size
        if actual_count == 0:
            return data
        res = struct.unpack(f'>{actual_count}{fmt}', data[:actual_count * item_size])
        return res if actual_count > 1 else res[0]
    elif type_char == 'c':
        return data.decode('ascii', errors='ignore').strip('\x00')
    return data


class GoproTelemetry:
    def __init__(self, file_path: str):
        if file_path.lower().endswith('.mp4'):
            self.raw_data = self._extract_from_mp4(file_path)
        else:
            with open(file_path, 'rb') as f:
                self.raw_data = f.read()
        self.gps_data: list[dict[str, Any]] = []  # List of { 'utc': ..., 'samples': [...] }
        self.imu_data: list[dict[str, Any]] = []  # List of { 'stmp': ..., 'accl': [...], 'gyro': [...] }
        self._parse()

    def _extract_from_mp4(self, mp4_path: str) -> bytes:
        # Find stream index with codec_tag_string 'gpmd'
        probe_cmd = ['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', mp4_path]
        prob_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        if prob_res.returncode != 0:
            raise RuntimeError(f'ffprobe failed to analyze {mp4_path}: {prob_res.stderr}')

        data = json.loads(prob_res.stdout)
        stream_index = None
        for stream in data.get('streams', []):
            if stream.get('codec_tag_string') == 'gpmd':
                stream_index = stream['index']
                break

        if stream_index is None:
            raise RuntimeError(f'No GPMF stream (gpmd) found in {mp4_path}')

        extract_cmd = [
            'ffmpeg', '-v', 'error', '-y', '-i', mp4_path,
            '-map', f'0:{stream_index}',
            '-f', 'rawvideo', '-'
        ]
        result = subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(f'ffmpeg failed to extract GPMF from {mp4_path}: {result.stderr.decode()}')

        return result.stdout

    def _parse(self) -> None:
        samples = parse_gpmf(self.raw_data)
        for s in samples:
            if s.key == 'DEVC':
                self._parse_device(s.data)

    def _parse_device(self, data: bytes) -> None:
        dev_samples = parse_gpmf(data)
        stmp = 0
        for s in dev_samples:
            if s.key == 'STMP':
                stmp = decode_type(s.type, s.size, s.count, s.data)
            elif s.key == 'STRM':
                self._parse_stream(s.data, stmp)

    def _parse_stream(self, data: bytes, stmp: int) -> None:
        strm_samples = parse_gpmf(data)
        temp: dict[str, Any] = {}
        for s in strm_samples:
            if s.type != '\x00':
                temp[s.key] = decode_type(s.type, s.size, s.count, s.data)

        def get_scaled(target_key: str, default_scale: Any = 1.0) -> list[list[float]]:
            val = temp.get(target_key)
            if val is None:
                return []
            scale = temp.get('SCAL', default_scale)
            if not isinstance(scale, (list, tuple)):
                scale = [scale]

            # Ensure scale is a list of floats
            scale = [float(sc) if not isinstance(sc, bytes) else float(struct.unpack('>h', sc[:2])[0]) for sc in scale]

            if not isinstance(val, (list, tuple)):
                val = [val]

            out_samples = []
            # Better: if ACCL we know it's 3 dims
            dims = 3 if target_key in ['ACCL', 'GYRO', 'MAGN'] else (5 if target_key == 'GPS5' else 1)

            for i in range(0, len(val), dims):
                chunk = val[i:i + dims]
                scaled = [chunk[j] / (scale[j] if j < len(scale) else scale[0]) for j in range(len(chunk))]
                out_samples.append(scaled)
            return out_samples

        # Use stream-level STMP if available, else fallback to device-level
        current_stmp = temp.get('STMP', stmp)
        if isinstance(current_stmp, (list, tuple)):
            current_stmp = current_stmp[0]
        if isinstance(current_stmp, bytes):
            if len(current_stmp) == 8:
                current_stmp = struct.unpack('>Q', current_stmp)[0]
            elif len(current_stmp) == 4:
                current_stmp = struct.unpack('>I', current_stmp)[0]
            else:
                current_stmp = stmp

        if 'GPS5' in temp:
            gps_samples = get_scaled('GPS5', [1, 1, 1, 1, 1])
            utc = temp.get('GPSU', '0')
            self.gps_data.append({'stmp': current_stmp, 'utc': utc, 'samples': gps_samples})

        if 'ACCL' in temp or 'GYRO' in temp or 'MAGN' in temp:
            accl_samples = get_scaled('ACCL')
            gyro_samples = get_scaled('GYRO')
            magn_samples = get_scaled('MAGN')
            self.imu_data.append({
                'stmp': current_stmp,
                'accl': accl_samples,
                'gyro': gyro_samples,
                'magn': magn_samples
            })


def lat_lon_to_enu(lat: float, lon: float, anchor_lat: float, anchor_lon: float) -> tuple[float, float]:
    # Rough approximation for local distance
    phi = math.radians(anchor_lat)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_per_deg_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    dn = (lat - anchor_lat) * m_per_deg_lat
    de = (lon - anchor_lon) * m_per_deg_lon
    return de, dn


def enu_to_lat_lon(e: float, n: float, anchor_lat: float, anchor_lon: float) -> tuple[float, float]:
    phi = math.radians(anchor_lat)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_per_deg_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    lat = anchor_lat + (n / m_per_deg_lat)
    lon = anchor_lon + (e / m_per_deg_lon)
    return lat, lon


def interpolate_gps_with_imu(
    gps_pts: list[tuple[float, float, float, float]],
    imu_pts: list[tuple[float, list[float], list[float]]],
    anchor_lat: float, anchor_lon: float
) -> list[tuple[float, float, float]]:
    """
    gps_pts: list of (t, lat, lon, alt)
    imu_pts: list of (t, accl, gyro)
    """
    if not gps_pts:
        return []

    # Sort GPS points by time
    gps_pts.sort()

    # 1. Deduplicate/Merge points with very close timestamps to avoid zig-zags
    # GoPro GPS is typically 18Hz (~55ms). If points are < 10ms apart, they are likely overlaps.
    merged_gps: list[list[float]] = []
    if gps_pts:
        current_group = [gps_pts[0]]
        for i in range(1, len(gps_pts)):
            if gps_pts[i][0] - current_group[-1][0] < 10000:  # 10ms threshold
                current_group.append(gps_pts[i])
            else:
                # Average the group
                avg_pt = [sum(p[j] for p in current_group) / len(current_group) for j in range(len(current_group[0]))]
                merged_gps.append(avg_pt)
                current_group = [gps_pts[i]]
        avg_pt = [sum(p[j] for p in current_group) / len(current_group) for j in range(len(current_group[0]))]
        merged_gps.append(avg_pt)

    if len(merged_gps) < 2:
        return []

    # 2. Convert to ENU for smoothing
    t_gps = np.array([p[0] for p in merged_gps])
    lats = np.array([p[1] for p in merged_gps])
    lons = np.array([p[2] for p in merged_gps])
    alts = np.array([p[3] for p in merged_gps])

    # Convert to ENU
    e_list = []
    n_list = []
    for lat, lon in zip(lats, lons):
        e, n = lat_lon_to_enu(lat, lon, anchor_lat, anchor_lon)
        e_list.append(e)
        n_list.append(n)
    e_array = np.array(e_list)
    n_array = np.array(n_list)

    # 3. Apply smoothing to ENU coordinates
    # For high-frequency GPS (29Hz), a very small window is enough.
    if len(e_array) >= 5:
        # Use a very short window (0.1s)
        window = 5  # Approx 3-5 samples
        if window > len(e_array):
            window = len(e_array)
        if window % 2 == 0:
            window -= 1

        poly_order = 2
        e_array = savgol_filter(e_array, window, poly_order)
        n_array = savgol_filter(n_array, window, poly_order)
        alts = savgol_filter(alts, window, poly_order)

    # 4. Interpolate at IMU timestamps
    # Filter IMU points to be within GPS range to avoid extrapolation errors
    t_min, t_max = t_gps[0], t_gps[-1]
    t_imu = np.array([p[0] for p in imu_pts if t_min <= p[0] <= t_max])

    if len(t_imu) == 0:
        return []

    # Filter GPS points that might be identical in time after merging
    unique_indices = np.where(np.diff(t_gps, prepend=-1) > 0)[0]
    if len(unique_indices) < 2:
        return []

    t_gps = t_gps[unique_indices]
    e_final = e_array[unique_indices]
    n_final = n_array[unique_indices]
    alts_final = alts[unique_indices]

    kind = 'cubic' if len(t_gps) >= 4 else 'linear'
    f_e = interp1d(t_gps, e_final, kind=kind, bounds_error=False, fill_value='extrapolate')
    f_n = interp1d(t_gps, n_final, kind=kind, bounds_error=False, fill_value='extrapolate')
    f_a = interp1d(
        t_gps, alts_final, kind='linear', bounds_error=False, fill_value='extrapolate'
    )  # Altitude is fine with linear

    e_smooth = f_e(t_imu)
    n_smooth = f_n(t_imu)
    a_smooth = f_a(t_imu)

    # 5. Convert back to Lat/Lon
    smoothed_path: list[tuple[float, float, float]] = []
    for e, n, a in zip(e_smooth, n_smooth, a_smooth):
        lat, lon = enu_to_lat_lon(e, n, anchor_lat, anchor_lon)
        smoothed_path.append((lat, lon, float(a)))

    return smoothed_path
