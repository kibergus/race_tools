"""Backend track geometry fetcher and coordinate origin resolver."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import threading
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
from urllib.parse import quote
import urllib.request

import numpy as np

from race_tools import assetto_corsa, rfactor, track_data


DEFAULT_USER_AGENT = 'BrBrDbTelemetry/1.0'


@dataclass(frozen=True)
class TurnDef:
    """Normalized turn and following-straight distance segment along a track lap."""

    index: int
    name: str
    corner_start: float
    corner_end: float
    straight_end: float
    corner_length: float
    straight_length: float
    apexes: Tuple[float, ...]


@dataclass
class TrackData:
    """Track centerline, bounds, and turn definitions fetched from the backend."""

    track_name: str
    lap_length: float
    cl_lat: np.ndarray
    cl_lon: np.ndarray
    cl_dist: np.ndarray
    cos_lat_track: float
    ref_lat: Optional[float]
    ref_lon: Optional[float]
    max_dist_m: float
    turns: List[TurnDef]


def _parse_apexes(raw_turn: Dict[str, Any]) -> Tuple[float, ...]:
    raw_apex = raw_turn.get('apexes_m')
    if raw_apex is None:
        raw_apex = raw_turn.get('apex', ())
    if isinstance(raw_apex, (int, float)):
        return (float(raw_apex),)
    if isinstance(raw_apex, (list, tuple)):
        out: List[float] = []
        for item in raw_apex:
            if item is not None:
                out.append(float(item))
        return tuple(out)
    return ()


def parse_track_payload(raw: Dict[str, Any], fallback_name: str = '') -> TrackData:
    """Parse a `/api/track_data` JSON payload into normalized `TrackData`."""
    track_name = str(raw.get('track_name') or fallback_name or 'Unknown Track')

    raw_cl = raw.get('center_line')
    if raw_cl is None:
        raw_cl = raw.get('centerline')
    if not isinstance(raw_cl, list):
        raw_cl = []

    pts = [
        p for p in raw_cl
        if isinstance(p, dict) and 'lat' in p and 'lon' in p and 'dist' in p
    ]
    cl_lat = np.array([float(p['lat']) for p in pts], dtype=np.float64)
    cl_lon = np.array([float(p['lon']) for p in pts], dtype=np.float64)
    cl_dist = np.array([float(p['dist']) for p in pts], dtype=np.float64)

    lap_length = float(raw.get('lap_length') or 0.0)
    if lap_length <= 0.0 and cl_dist.size > 0:
        lap_length = float(np.max(cl_dist))
    if lap_length <= 0.0:
        lap_length = 1000.0

    if cl_lat.size > 0:
        ref_lat: Optional[float] = float(np.mean(cl_lat))
        ref_lon: Optional[float] = float(np.mean(cl_lon))
        cos_lat_track = math.cos(math.radians(ref_lat))
        max_dist_m = 5000.0
    else:
        origin = raw.get('origin')
        if isinstance(origin, dict) and 'lat' in origin and 'lon' in origin:
            ref_lat = float(origin['lat'])
            ref_lon = float(origin['lon'])
            cos_lat_track = math.cos(math.radians(ref_lat))
            max_dist_m = 5000.0
        else:
            ref_lat = None
            ref_lon = None
            cos_lat_track = 1.0
            max_dist_m = 0.0

    raw_turns = raw.get('turns') or []
    turn_dicts = [t for t in raw_turns if isinstance(t, dict)]
    turns: List[TurnDef] = []
    num_turns = len(turn_dicts)

    for i, t in enumerate(turn_dicts):
        prev_t = turn_dicts[(i - 1) % num_turns]
        next_t = turn_dicts[(i + 1) % num_turns]

        raw_start = t.get('start')
        if raw_start is None:
            raw_start = prev_t.get('end', 0.0)
        raw_end = t.get('end')
        if raw_end is None:
            raw_end = raw_start

        raw_next_start = next_t.get('start')
        if raw_next_start is None:
            raw_next_start = raw_end

        corner_start = float(raw_start) % lap_length
        corner_end = float(raw_end) % lap_length
        next_start = float(raw_next_start) % lap_length

        corner_length = (corner_end - corner_start) % lap_length
        raw_straight = (next_start - corner_end) % lap_length

        is_overlap = (
            (i < num_turns - 1 and next_start <= corner_end and corner_end >= corner_start)
            or (num_turns >= 3 and raw_straight > 0.65 * lap_length)
        )
        if is_overlap:
            straight_length = 0.0
            straight_end = corner_end
        else:
            straight_length = raw_straight
            straight_end = next_start

        turns.append(
            TurnDef(
                index=i,
                name=str(t.get('name') or f'Turn {i + 1}'),
                corner_start=corner_start,
                corner_end=corner_end,
                straight_end=straight_end,
                corner_length=corner_length,
                straight_length=straight_length,
                apexes=_parse_apexes(t),
            )
        )

    return TrackData(
        track_name=track_name,
        lap_length=lap_length,
        cl_lat=cl_lat,
        cl_lon=cl_lon,
        cl_dist=cl_dist,
        cos_lat_track=cos_lat_track,
        ref_lat=ref_lat,
        ref_lon=ref_lon,
        max_dist_m=max_dist_m,
        turns=turns,
    )


class TrackLoader:
    """Fetches and caches track definitions from the BrBrDb backend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Dict[Tuple[str, str], TrackData] = {}

    def clear_cache(self) -> None:
        """Clear cached track definitions."""
        with self._lock:
            self._cache.clear()

    def fetch(
        self,
        server_url: str,
        track_name: str,
        api_key: str = '',
        timeout: float = 5.0,
    ) -> TrackData:
        """Fetch and parse track metadata for `track_name` from `server_url`."""
        base_url = server_url.strip().rstrip('/')
        norm_name = track_name.strip().lower()
        cache_key = (base_url, norm_name)

        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = f'{base_url}/api/track_data?track={quote(track_name)}'
        headers: Dict[str, str] = {
            'Accept': 'application/json',
            'User-Agent': DEFAULT_USER_AGENT,
        }
        if api_key.strip():
            headers['X-API-Key'] = api_key.strip()

        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            raw_err = exc.read(512).decode('utf-8', errors='replace').strip()
            detail = raw_err
            if raw_err:
                try:
                    err_json = json.loads(raw_err)
                except ValueError:
                    err_json = None
                if isinstance(err_json, dict):
                    detail = str(
                        err_json.get('error') or err_json.get('title') or raw_err
                    ).strip()
            msg = f'HTTP {exc.code} {exc.reason} fetching track {track_name!r}'
            if detail:
                msg = f'{msg}: {detail}'
            raise RuntimeError(msg) from exc

        parsed = parse_track_payload(payload, fallback_name=track_name)
        canonical_key = (base_url, parsed.track_name.strip().lower())
        with self._lock:
            self._cache[cache_key] = parsed
            self._cache[canonical_key] = parsed
        return parsed


def _parse_float_meta(meta_lower: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        val = meta_lower.get(k)
        if val is None or val == '':
            continue
        try:
            f_val = float(val)
        except (TypeError, ValueError):
            continue
        if not math.isnan(f_val) and not math.isinf(f_val):
            return f_val
    return None


def resolve_track_origin(
    league: str,
    resolved_track_name: str,
    raw_track_name: str,
    metadata: Dict[str, Any],
) -> Optional[track_data.TrackOrigin]:
    """Resolve simulator local-to-GPS `TrackOrigin` from metadata or built-in origins."""
    league_norm = (league or '').strip().lower()
    is_ac_family = league_norm in ('acevo', 'ac', 'acr')

    meta_lower = {str(k).strip().lower(): v for k, v in metadata.items()}
    origin_lat = _parse_float_meta(meta_lower, 'track origin latitude', 'track_origin_lat')
    origin_lon = _parse_float_meta(meta_lower, 'track origin longitude', 'track_origin_lon')
    origin_heading = _parse_float_meta(
        meta_lower, 'track origin heading', 'track_origin_heading'
    )
    heading = 0.0 if origin_heading is None else origin_heading

    if origin_lat is not None and origin_lon is not None:
        if is_ac_family:
            corr = track_data.Correction()
            for candidate in (resolved_track_name, raw_track_name):
                if not candidate:
                    continue
                try:
                    corr = assetto_corsa.get_track_origin(candidate).correction
                    break
                except ValueError:
                    pass
            return assetto_corsa.AssettoCorsaTrackOrigin(
                lat=origin_lat,
                lon=origin_lon,
                correction=corr,
                heading=heading,
            )
        return rfactor.RFactorTrackOrigin(
            lat=origin_lat,
            lon=origin_lon,
            heading=heading,
        )

    candidates = [c for c in (resolved_track_name, raw_track_name) if c]
    if is_ac_family:
        for candidate in candidates:
            try:
                return assetto_corsa.get_track_origin(candidate)
            except ValueError:
                pass
        return None

    for candidate in candidates:
        try:
            return rfactor.get_track_origin(candidate)
        except ValueError:
            pass
    return None
