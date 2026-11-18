"""Real-time HUD state aggregator and SSE broadcaster for OBS Browser Source."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from live_feedback.engine import (
    BenchmarkStore,
    TurnBenchmark,
    TurnCompleteEvent,
    TurnExitEvent,
)
from live_feedback.track_loader import TrackData


TICK_MIN_INTERVAL_S = 0.016
GAZE_STALE_TIMEOUT_S = 0.60
CLIENT_QUEUE_MAX = 128


@dataclass(frozen=True)
class BestLapRecord:
    """Distance-gridded reference arrays for the fastest completed clean lap."""

    lap_time_s: float
    speed_kmh: np.ndarray
    elapsed_s: np.ndarray


def _fill_missing_bins(values: List[Optional[float]]) -> Optional[np.ndarray]:
    """Linearly interpolate small missing-bin gaps in a complete lap array."""
    n = len(values)
    if n == 0:
        return None
    idx_valid = [i for i, v in enumerate(values) if v is not None and math.isfinite(v)]
    if len(idx_valid) < max(4, int(0.85 * n)):
        return None
    xp = np.array(idx_valid, dtype=np.float64)
    fp = np.array([float(values[i]) for i in idx_valid], dtype=np.float64)
    grid = np.arange(n, dtype=np.float64)
    return np.interp(grid, xp, fp)


def build_best_turns_speed_grid(
    track: TrackData,
    grid_dists_m: np.ndarray,
    benchmarks: Dict[int, TurnBenchmark],
) -> List[Optional[float]]:
    """Project per-turn fastest segment speed traces onto the full-lap distance grid."""
    n = int(grid_dists_m.size)
    out: List[Optional[float]] = [None] * n
    lap_len = track.lap_length
    if n == 0 or lap_len <= 0.0:
        return out

    for turn in track.turns:
        bm = benchmarks.get(turn.index)
        if bm is None:
            continue

        pts_s: List[float] = []
        pts_v: List[float] = []
        if bm.corner_dists_m.size > 0 and bm.corner_speeds_ms.size == bm.corner_dists_m.size:
            for d_val, v_val in zip(bm.corner_dists_m, bm.corner_speeds_ms):
                if math.isfinite(d_val) and math.isfinite(v_val):
                    pts_s.append(float(d_val))
                    pts_v.append(max(0.0, float(v_val) * 3.6))

        if bm.straight_dists_m.size > 0 and bm.straight_speeds_ms.size == bm.straight_dists_m.size:
            for d_val, v_val in zip(bm.straight_dists_m, bm.straight_speeds_ms):
                if math.isfinite(d_val) and math.isfinite(v_val):
                    pts_s.append(turn.corner_length + float(d_val))
                    pts_v.append(max(0.0, float(v_val) * 3.6))

        if len(pts_s) < 2:
            continue

        # Sort and deduplicate strictly increasing distance points for np.interp
        pairs = sorted(zip(pts_s, pts_v), key=lambda p: p[0])
        mono_s: List[float] = []
        mono_v: List[float] = []
        for s_val, v_val in pairs:
            if not mono_s or s_val > mono_s[-1] + 1e-4:
                mono_s.append(s_val)
                mono_v.append(v_val)
            else:
                mono_v[-1] = v_val

        if len(mono_s) < 2:
            continue

        s_arr = np.array(mono_s, dtype=np.float64)
        v_arr = np.array(mono_v, dtype=np.float64)
        seg_len = turn.corner_length + turn.straight_length
        s_min = float(s_arr[0])
        s_max = min(seg_len, float(s_arr[-1]))
        if s_max <= s_min:
            continue

        rel_u = (grid_dists_m - turn.corner_start) % lap_len
        mask = (rel_u >= s_min) & (rel_u <= s_max)
        if not np.any(mask):
            continue
        interp_v = np.interp(rel_u[mask], s_arr, v_arr)
        for idx, val in zip(np.flatnonzero(mask), interp_v):
            out[int(idx)] = round(float(val), 2)

    return out


class OverlayStateHub:
    """Thread-safe telemetry/gaze state hub pushing live updates to SSE `/events` clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: List['queue.Queue[Optional[str]]'] = []

        self._track: Optional[TrackData] = None
        self._track_info: Optional[Dict[str, Any]] = None
        self._grid_dists_m = np.zeros(0, dtype=np.float64)
        self._num_bins = 400

        self._curr_lap_speed: List[Optional[float]] = []
        self._curr_lap_elapsed: List[Optional[float]] = []
        self._best_lap: Optional[BestLapRecord] = None
        self._best_turns_speed: Optional[List[Optional[float]]] = None
        self._last_lap_s: Optional[float] = None
        self._lap_num: int = 1

        self._prev_dist: Optional[float] = None
        self._prev_time_s: Optional[float] = None
        self._prev_speed_ms: Optional[float] = None
        self._prev_bin_idx: Optional[int] = None
        self._lap_start_t: Optional[float] = None
        self._lap_valid: bool = False

        self._curr_speed_kmh: float = 0.0
        self._curr_dist_m: Optional[float] = None
        self._curr_lap_time_s: Optional[float] = None
        self._curr_lap_delta_s: Optional[float] = None
        self._throttle_pct: float = 0.0
        self._brake_pct: float = 0.0
        self._clutch_pct: float = 0.0

        self._segment: Optional[Dict[str, Any]] = None
        self._turn_deltas: Dict[str, float] = {}
        self._turn_bests: Dict[str, float] = {}
        self._turn_times: Dict[str, Dict[str, Any]] = {}

        self._gaze_x: Optional[float] = None
        self._gaze_y: Optional[float] = None
        self._gaze_valid: bool = False
        self._gaze_mono: float = 0.0

        self._pending_bins: List[Tuple[int, float]] = []
        self._pending_lap_reset: bool = False
        self._last_tick_mono: float = 0.0

    def register_client(self) -> Tuple['queue.Queue[Optional[str]]', str]:
        """Register an SSE client queue and return `(queue, initial_json_payload)`."""
        q: 'queue.Queue[Optional[str]]' = queue.Queue(maxsize=CLIENT_QUEUE_MAX)
        with self._lock:
            init_doc: Dict[str, Any] = {
                'type': 'init',
                'track': self._track_info,
                'best_lap_speed': (
                    [round(float(v), 2) for v in self._best_lap.speed_kmh]
                    if self._best_lap is not None
                    else None
                ),
                'best_turns_speed': (
                    list(self._best_turns_speed)
                    if self._best_turns_speed is not None
                    else None
                ),
                'curr_lap_speed': list(self._curr_lap_speed),
                'turn_deltas': dict(self._turn_deltas),
                'turn_bests': dict(self._turn_bests),
                'turn_times': {k: dict(v) for k, v in self._turn_times.items()},
                'best_lap_s': (
                    round(self._best_lap.lap_time_s, 4)
                    if self._best_lap is not None
                    else None
                ),
                'last_lap_s': (
                    round(self._last_lap_s, 4)
                    if self._last_lap_s is not None
                    else None
                ),
                'segment': dict(self._segment) if self._segment is not None else None,
            }
            self._clients.append(q)
        return q, json.dumps(init_doc, separators=(',', ':'))

    def unregister_client(self, q: 'queue.Queue[Optional[str]]') -> None:
        """Remove an SSE client queue on disconnect."""
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def close_all_clients(self) -> None:
        """Push shutdown sentinel to all active SSE clients so HTTP server stops cleanly."""
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for q in clients:
            while q.full():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def _broadcast_locked(self, payload: Dict[str, Any]) -> None:
        if not self._clients:
            return
        raw = json.dumps(payload, separators=(',', ':'))
        for q in list(self._clients):
            if q.full():
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
            try:
                q.put_nowait(raw)
            except queue.Full:
                pass

    def set_track(self, track: Optional[TrackData]) -> None:
        """Initialize overlay distance grids for a new track/session."""
        with self._lock:
            self._track = track
            self._best_lap = None
            self._best_turns_speed = None
            self._last_lap_s = None
            self._lap_num = 1
            self._prev_dist = None
            self._prev_time_s = None
            self._prev_speed_ms = None
            self._prev_bin_idx = None
            self._lap_start_t = None
            self._lap_valid = False
            self._curr_dist_m = None
            self._curr_speed_kmh = 0.0
            self._curr_lap_time_s = None
            self._curr_lap_delta_s = None
            self._segment = None
            self._turn_deltas.clear()
            self._turn_bests.clear()
            self._turn_times.clear()
            self._pending_bins.clear()
            self._pending_lap_reset = False

            if track is not None:
                n_bins = min(500, max(200, int(round(track.lap_length / 2.0))))
                self._num_bins = n_bins
                self._grid_dists_m = np.linspace(
                    0.0, track.lap_length, n_bins, endpoint=False
                )
                self._curr_lap_speed = [None] * n_bins
                self._curr_lap_elapsed = [None] * n_bins
                self._track_info = {
                    'track_name': track.track_name,
                    'lap_length': round(track.lap_length, 3),
                    'num_bins': n_bins,
                    'turns': [
                        {
                            'index': t.index,
                            'name': t.name,
                            'corner_start': round(t.corner_start, 2),
                            'corner_end': round(t.corner_end, 2),
                            'straight_end': round(t.straight_end, 2),
                            'corner_length': round(t.corner_length, 2),
                            'straight_length': round(t.straight_length, 2),
                        }
                        for t in track.turns
                    ],
                }
            else:
                self._num_bins = 400
                self._grid_dists_m = np.zeros(0, dtype=np.float64)
                self._curr_lap_speed = []
                self._curr_lap_elapsed = []
                self._track_info = None

            self._broadcast_locked({'type': 'track', 'track': self._track_info})

    def reset_benchmarks(self) -> None:
        """Clear session Best Lap, Best Turns, and current lap trace."""
        with self._lock:
            self._best_lap = None
            self._best_turns_speed = None
            self._last_lap_s = None
            self._curr_lap_speed = [None] * self._num_bins if self._track else []
            self._curr_lap_elapsed = [None] * self._num_bins if self._track else []
            self._prev_bin_idx = None
            self._lap_start_t = None
            self._lap_valid = False
            self._curr_lap_time_s = None
            self._curr_lap_delta_s = None
            self._segment = None
            self._turn_deltas.clear()
            self._turn_bests.clear()
            self._turn_times.clear()
            self._pending_bins.clear()
            self._pending_lap_reset = False
            self._broadcast_locked({'type': 'reset'})

    def _build_tick_locked(self, now_mono: float) -> Dict[str, Any]:
        gaze_fresh = (
            self._gaze_valid
            and self._gaze_x is not None
            and self._gaze_y is not None
            and (now_mono - self._gaze_mono) <= GAZE_STALE_TIMEOUT_S
        )
        gaze_obj: Dict[str, Any]
        if gaze_fresh:
            gaze_obj = {
                'valid': True,
                'x': round(float(self._gaze_x), 4),
                'y': round(float(self._gaze_y), 4),
            }
        else:
            gaze_obj = {'valid': False}

        tick: Dict[str, Any] = {
            'type': 'tick',
            'lap_reset': self._pending_lap_reset,
            'lap_pts': [[idx, spd] for idx, spd in self._pending_bins],
            'lap_num': self._lap_num,
            'lap_time_s': (
                round(self._curr_lap_time_s, 3)
                if self._curr_lap_time_s is not None
                else None
            ),
            'lap_delta_s': (
                round(self._curr_lap_delta_s, 3)
                if self._curr_lap_delta_s is not None
                else None
            ),
            'best_lap_s': (
                round(self._best_lap.lap_time_s, 3)
                if self._best_lap is not None
                else None
            ),
            'last_lap_s': (
                round(self._last_lap_s, 3)
                if self._last_lap_s is not None
                else None
            ),
            'dist_m': (
                round(self._curr_dist_m, 2)
                if self._curr_dist_m is not None
                else None
            ),
            'speed_kmh': round(self._curr_speed_kmh, 2),
            'throttle': round(self._throttle_pct, 1),
            'brake': round(self._brake_pct, 1),
            'clutch': round(self._clutch_pct, 1),
            'segment': dict(self._segment) if self._segment is not None else None,
            'turn_deltas': dict(self._turn_deltas),
            'turn_times': {k: dict(v) for k, v in self._turn_times.items()},
            'gaze': gaze_obj,
        }
        self._pending_bins.clear()
        self._pending_lap_reset = False
        self._last_tick_mono = now_mono
        return tick

    def on_gaze_sample(
        self,
        gaze_x: Optional[float],
        gaze_y: Optional[float],
        gaze_valid: bool,
        is_live: bool = True,
    ) -> None:
        """Update live calibrated screen gaze state and emit throttled tick."""
        now_mono = time.monotonic()
        with self._lock:
            prev_valid = self._gaze_valid
            if (
                gaze_valid
                and gaze_x is not None
                and gaze_y is not None
                and math.isfinite(gaze_x)
                and math.isfinite(gaze_y)
            ):
                self._gaze_x = float(gaze_x)
                self._gaze_y = float(gaze_y)
                self._gaze_valid = True
                self._gaze_mono = now_mono
            else:
                self._gaze_x = None
                self._gaze_y = None
                self._gaze_valid = False
                self._gaze_mono = now_mono

            if is_live and (
                prev_valid != self._gaze_valid
                or (now_mono - self._last_tick_mono) >= TICK_MIN_INTERVAL_S
            ):
                self._broadcast_locked(self._build_tick_locked(now_mono))

    def _record_bin_range_locked(
        self,
        start_bin: int,
        end_bin: int,
        speed_kmh: float,
        elapsed_s: Optional[float],
    ) -> None:
        n = self._num_bins
        if n <= 0:
            return
        start_b = max(0, min(n - 1, start_bin))
        end_b = max(0, min(n - 1, end_bin))
        spd_rounded = round(speed_kmh, 2)
        if start_b <= end_b and (end_b - start_b) <= 24:
            prev_v = self._curr_lap_speed[start_b]
            prev_t = self._curr_lap_elapsed[start_b]
            span = max(1, end_b - start_b)
            for b in range(start_b, end_b + 1):
                frac = float(b - start_b) / float(span)
                v_interp = (
                    prev_v + frac * (spd_rounded - prev_v)
                    if prev_v is not None and b > start_b
                    else spd_rounded
                )
                v_r = round(float(v_interp), 2)
                self._curr_lap_speed[b] = v_r
                self._pending_bins.append((b, v_r))
                if elapsed_s is not None:
                    t_interp = (
                        prev_t + frac * (elapsed_s - prev_t)
                        if prev_t is not None and b > start_b
                        else elapsed_s
                    )
                    self._curr_lap_elapsed[b] = float(t_interp)
        else:
            self._curr_lap_speed[end_b] = spd_rounded
            self._pending_bins.append((end_b, spd_rounded))
            if elapsed_s is not None:
                self._curr_lap_elapsed[end_b] = float(elapsed_s)

    def _promote_completed_lap_locked(self, lap_time_s: float) -> None:
        if lap_time_s <= 2.0 or self._track is None:
            return
        self._last_lap_s = lap_time_s
        speed_arr = _fill_missing_bins(self._curr_lap_speed)
        elapsed_arr = _fill_missing_bins(self._curr_lap_elapsed)
        if speed_arr is None or elapsed_arr is None:
            return
        # Anchor elapsed_s monotonically within [0, lap_time_s]
        elapsed_arr = np.clip(elapsed_arr, 0.0, lap_time_s)
        if self._best_lap is None or lap_time_s < self._best_lap.lap_time_s:
            self._best_lap = BestLapRecord(
                lap_time_s=lap_time_s,
                speed_kmh=speed_arr,
                elapsed_s=elapsed_arr,
            )
            self._broadcast_locked(
                {
                    'type': 'best_lap',
                    'best_lap_s': round(lap_time_s, 3),
                    'speeds': [round(float(v), 2) for v in speed_arr],
                }
            )

    def on_vehicle_sample(
        self,
        time_s: float,
        curr_dist_m: Optional[float],
        speed_ms: Optional[float],
        throttle_pct: Optional[float],
        brake_pct: Optional[float],
        clutch_pct: Optional[float],
        lap_num: int,
        exit_events: List[TurnExitEvent],
        complete_events: List[TurnCompleteEvent],
        benchmark_store: Optional[BenchmarkStore],
        is_live: bool = True,
    ) -> None:
        """Ingest one vehicle telemetry step and broadcast throttled overlay updates."""
        now_mono = time.monotonic()
        with self._lock:
            if throttle_pct is not None and math.isfinite(throttle_pct):
                self._throttle_pct = max(0.0, min(100.0, float(throttle_pct)))
            if brake_pct is not None and math.isfinite(brake_pct):
                self._brake_pct = max(0.0, min(100.0, float(brake_pct)))
            if clutch_pct is not None and math.isfinite(clutch_pct):
                self._clutch_pct = max(0.0, min(100.0, float(clutch_pct)))

            self._lap_num = max(1, int(lap_num))
            track = self._track

            if (
                track is None
                or curr_dist_m is None
                or speed_ms is None
                or not math.isfinite(time_s)
                or not math.isfinite(curr_dist_m)
                or not math.isfinite(speed_ms)
            ):
                self._lap_valid = False
                self._lap_start_t = None
                self._prev_dist = None
                self._prev_bin_idx = None
                self._curr_dist_m = None
                self._curr_lap_time_s = None
                self._curr_lap_delta_s = None
                if speed_ms is not None and math.isfinite(speed_ms):
                    self._curr_speed_kmh = max(0.0, float(speed_ms)) * 3.6
            else:
                lap_len = track.lap_length
                speed_val = max(0.0, float(speed_ms))
                speed_kmh = speed_val * 3.6
                self._curr_speed_kmh = speed_kmh
                self._curr_dist_m = float(curr_dist_m)
                bin_idx = min(
                    self._num_bins - 1,
                    max(0, int((curr_dist_m / lap_len) * self._num_bins)),
                )

                if self._prev_dist is None or self._prev_time_s is None or self._prev_speed_ms is None:
                    self._prev_dist = curr_dist_m
                    self._prev_time_s = time_s
                    self._prev_speed_ms = speed_val
                    self._prev_bin_idx = bin_idx
                    self._lap_valid = False
                    self._lap_start_t = None
                else:
                    dt = time_s - self._prev_time_s
                    if dt > 0.0:
                        step_m = (curr_dist_m - self._prev_dist) % lap_len
                        max_step = max(25.0, 0.4 * lap_len)
                        if speed_val < 1.5 or dt > 2.0:
                            self._lap_valid = False
                            self._lap_start_t = None
                            self._prev_dist = curr_dist_m
                            self._prev_time_s = time_s
                            self._prev_speed_ms = speed_val
                            self._prev_bin_idx = bin_idx
                            self._curr_lap_delta_s = None
                        elif step_m > max_step:
                            if (lap_len - step_m) <= 2.0:
                                self._prev_time_s = time_s
                                self._prev_speed_ms = speed_val
                            else:
                                self._lap_valid = False
                                self._lap_start_t = None
                                self._prev_dist = curr_dist_m
                                self._prev_time_s = time_s
                                self._prev_speed_ms = speed_val
                                self._prev_bin_idx = bin_idx
                                self._curr_lap_delta_s = None
                        else:
                            crossed_sf = (curr_dist_m - self._prev_dist) < -0.5 * lap_len
                            if crossed_sf and step_m > 0.0:
                                sf_off = (lap_len - self._prev_dist) % lap_len
                                alpha = sf_off / step_m
                                t_sf = self._prev_time_s + alpha * dt
                                v_sf_kmh = (
                                    self._prev_speed_ms + alpha * (speed_val - self._prev_speed_ms)
                                ) * 3.6
                                if self._lap_valid and self._lap_start_t is not None:
                                    end_time = t_sf - self._lap_start_t
                                    if self._prev_bin_idx is not None:
                                        self._record_bin_range_locked(
                                            self._prev_bin_idx,
                                            self._num_bins - 1,
                                            v_sf_kmh,
                                            end_time,
                                        )
                                    self._promote_completed_lap_locked(end_time)

                                self._curr_lap_speed = [None] * self._num_bins
                                self._curr_lap_elapsed = [None] * self._num_bins
                                self._pending_bins.clear()
                                self._pending_lap_reset = True
                                self._lap_valid = True
                                self._lap_start_t = t_sf
                                self._prev_bin_idx = 0
                                self._record_bin_range_locked(0, 0, v_sf_kmh, 0.0)

                            elapsed_now = (
                                (time_s - self._lap_start_t)
                                if (self._lap_valid and self._lap_start_t is not None)
                                else None
                            )
                            self._curr_lap_time_s = elapsed_now
                            start_b = (
                                self._prev_bin_idx
                                if self._prev_bin_idx is not None and bin_idx >= self._prev_bin_idx
                                else bin_idx
                            )
                            self._record_bin_range_locked(start_b, bin_idx, speed_kmh, elapsed_now)

                            if elapsed_now is not None and self._best_lap is not None:
                                ref_t = float(
                                    np.interp(
                                        curr_dist_m,
                                        self._grid_dists_m,
                                        self._best_lap.elapsed_s,
                                    )
                                )
                                self._curr_lap_delta_s = elapsed_now - ref_t
                            else:
                                self._curr_lap_delta_s = None

                            self._prev_dist = curr_dist_m
                            self._prev_time_s = time_s
                            self._prev_speed_ms = speed_val
                            self._prev_bin_idx = bin_idx

            # Process turn exit and segment completion events
            for ev in exit_events:
                exit_kmh = ev.exit_speed_ms * 3.6
                exit_diff = (
                    (ev.exit_speed_ms - ev.ref_exit_speed_ms) * 3.6
                    if ev.ref_exit_speed_ms is not None
                    else None
                )
                self._segment = {
                    'turn_index': ev.turn_index,
                    'turn_name': ev.turn_name,
                    'phase': 'exit',
                    'best_s': (
                        round(ev.ref_total_time_s, 3)
                        if ev.ref_total_time_s is not None
                        else None
                    ),
                    'delta_s': (
                        round(ev.pred_total_delta_s, 3)
                        if ev.pred_total_delta_s is not None
                        else None
                    ),
                    'delta_kind': 'pred',
                    'exit_kmh': round(exit_kmh, 1),
                    'exit_diff_kmh': (
                        round(exit_diff, 1) if exit_diff is not None else None
                    ),
                }
                if ev.pred_total_delta_s is not None:
                    self._turn_deltas[str(ev.turn_index)] = round(ev.pred_total_delta_s, 4)
                k = str(ev.turn_index)
                prev_t = self._turn_times.get(k, {})
                best_s = (
                    round(ev.ref_total_time_s, 3)
                    if ev.ref_total_time_s is not None
                    else prev_t.get('best_s')
                )
                pred_delta = (
                    round(ev.pred_total_delta_s, 3)
                    if ev.pred_total_delta_s is not None
                    else None
                )
                pred_total = (
                    round(ev.ref_total_time_s + ev.pred_total_delta_s, 3)
                    if (
                        ev.ref_total_time_s is not None
                        and ev.pred_total_delta_s is not None
                    )
                    else prev_t.get('last_s')
                )
                self._turn_times[k] = {
                    'last_s': pred_total,
                    'best_s': best_s,
                    'delta_s': pred_delta,
                    'kind': 'pred',
                }

            best_turns_updated = False
            for cev in complete_events:
                k = str(cev.turn_index)
                self._turn_bests[k] = round(cev.best_total_time_s, 4)
                self._turn_times[k] = {
                    'last_s': round(cev.total_time_s, 3),
                    'best_s': round(cev.best_total_time_s, 3),
                    'delta_s': (
                        round(cev.actual_total_delta_s, 3)
                        if cev.actual_total_delta_s is not None
                        else None
                    ),
                    'kind': 'actual',
                }
                if cev.actual_total_delta_s is not None:
                    self._turn_deltas[str(cev.turn_index)] = round(
                        cev.actual_total_delta_s, 4
                    )
                    if (
                        self._segment is not None
                        and self._segment.get('turn_index') == cev.turn_index
                    ):
                        self._segment['delta_s'] = round(cev.actual_total_delta_s, 3)
                        self._segment['delta_kind'] = 'actual'
                        self._segment['best_s'] = round(cev.best_total_time_s, 3)
                if cev.benchmark_updated:
                    best_turns_updated = True

            if best_turns_updated and track is not None and benchmark_store is not None:
                self._best_turns_speed = build_best_turns_speed_grid(
                    track,
                    self._grid_dists_m,
                    benchmark_store.all_benchmarks(),
                )
                self._broadcast_locked(
                    {
                        'type': 'best_turns',
                        'speeds': list(self._best_turns_speed),
                        'turn_bests': dict(self._turn_bests),
                        'turn_times': {
                            k: dict(v) for k, v in self._turn_times.items()
                        },
                    }
                )

            if is_live and (
                self._pending_lap_reset
                or (now_mono - self._last_tick_mono) >= TICK_MIN_INTERVAL_S
            ):
                self._broadcast_locked(self._build_tick_locked(now_mono))
