"""Real-time centerline projection, straight-line loss estimation, and turn state engine."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from race_tools import track_data
from live_feedback.track_loader import TrackData, TurnDef


@dataclass(frozen=True)
class TurnBenchmark:
    """Best recorded corner + straight pass for a single turn."""

    turn_index: int
    turn_name: str
    lap_num: int
    corner_time_s: float
    straight_time_s: float
    total_time_s: float
    exit_speed_ms: float
    straight_dists_m: np.ndarray
    straight_speeds_ms: np.ndarray


@dataclass(frozen=True)
class TurnExitEvent:
    """Emitted immediately when the vehicle crosses `turn.end`."""

    turn_index: int
    turn_name: str
    lap_num: int
    corner_time_s: float
    exit_speed_ms: float
    has_benchmark: bool
    ref_corner_time_s: Optional[float] = None
    ref_exit_speed_ms: Optional[float] = None
    ref_total_time_s: Optional[float] = None
    corner_delta_s: Optional[float] = None
    pred_straight_delta_s: Optional[float] = None
    pred_total_delta_s: Optional[float] = None
    is_new_best_predicted: bool = False
    is_live: bool = True


@dataclass(frozen=True)
class TurnCompleteEvent:
    """Emitted when the vehicle finishes the following straight at `next_turn.start`."""

    turn_index: int
    turn_name: str
    lap_num: int
    corner_time_s: float
    straight_time_s: float
    total_time_s: float
    exit_speed_ms: float
    has_prev_benchmark: bool
    actual_straight_delta_s: Optional[float] = None
    actual_total_delta_s: Optional[float] = None
    benchmark_updated: bool = False
    best_total_time_s: float = 0.0
    is_live: bool = True


@dataclass
class _ActiveCorner:
    lap_num: int
    t_start: float
    v_entry: float


@dataclass
class _ActiveStraight:
    lap_num: int
    corner_time_s: float
    t_exit: float
    v_exit: float
    samples: List[Tuple[float, float]]


class BenchmarkStore:
    """Holds per-turn benchmark passes for a session (and future historical imports)."""

    def __init__(self) -> None:
        self._benchmarks: Dict[int, TurnBenchmark] = {}
        self.max_speed_ms: float = 0.0

    def get(self, turn_index: int) -> Optional[TurnBenchmark]:
        """Return the benchmark for `turn_index`, or None if not yet recorded."""
        return self._benchmarks.get(turn_index)

    def update_max_speed(self, speed_ms: float) -> None:
        """Update the observed peak session speed (m/s)."""
        if math.isfinite(speed_ms) and speed_ms > self.max_speed_ms:
            self.max_speed_ms = float(speed_ms)

    def maybe_update(self, candidate: TurnBenchmark) -> bool:
        """Replace the turn benchmark if `candidate` is strictly faster overall."""
        current = self._benchmarks.get(candidate.turn_index)
        if current is None or candidate.total_time_s < current.total_time_s:
            self._benchmarks[candidate.turn_index] = candidate
            return True
        return False

    def load_benchmarks(
        self,
        items: Iterable[TurnBenchmark],
        max_speed_ms: float = 0.0,
    ) -> None:
        """Pre-populate benchmarks (e.g. from backend multi-session data)."""
        for item in items:
            self.maybe_update(item)
        if max_speed_ms > self.max_speed_ms:
            self.max_speed_ms = float(max_speed_ms)

    def all_benchmarks(self) -> Dict[int, TurnBenchmark]:
        """Return a shallow copy of stored per-turn benchmarks."""
        return dict(self._benchmarks)

    def reset(self) -> None:
        """Clear all stored benchmarks and peak speed."""
        self._benchmarks.clear()
        self.max_speed_ms = 0.0


def project_to_centerline(
    lat: float,
    lon: float,
    track: TrackData,
    prev_dist: Optional[float] = None,
) -> Optional[float]:
    """Project `(lat, lon)` onto `track.cl_*` and return lap distance in `[0, lap_length)`."""
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    if track.ref_lat is not None and track.ref_lon is not None and track.max_dist_m > 0.0:
        m_lat_ref, m_lon_ref = track_data.wgs84_meters_per_degree(track.ref_lat)
        dlat_m = (lat - track.ref_lat) * m_lat_ref
        dlon_m = (lon - track.ref_lon) * m_lon_ref
        if dlat_m * dlat_m + dlon_m * dlon_m > track.max_dist_m * track.max_dist_m:
            return None

    n_pts = int(track.cl_lat.size)
    if n_pts == 0:
        return None

    lap_len = track.lap_length
    dlat_all = track.cl_lat - lat
    dlon_all = (track.cl_lon - lon) * track.cos_lat_track
    d2 = dlat_all * dlat_all + dlon_all * dlon_all

    idx: Optional[int] = None
    if prev_dist is not None:
        cdist = np.minimum(
            (track.cl_dist - prev_dist) % lap_len,
            (prev_dist - track.cl_dist) % lap_len,
        )
        local_indices = np.where(cdist <= 40.0)[0]
        if local_indices.size > 0:
            local_best = int(local_indices[int(np.argmin(d2[local_indices]))])
            m_lat, m_lon = track_data.wgs84_meters_per_degree(lat)
            phys_m = math.hypot(
                (float(track.cl_lat[local_best]) - lat) * m_lat,
                (float(track.cl_lon[local_best]) - lon) * m_lon,
            )
            if phys_m < 25.0:
                idx = local_best

    if idx is None:
        idx = int(np.argmin(d2))

    idx_prev = (idx + n_pts - 1) % n_pts
    idx_next = (idx + 1) % n_pts

    lat_a = float(track.cl_lat[idx])
    lon_a = float(track.cl_lon[idx])
    dist_a = float(track.cl_dist[idx])

    lat_prev = float(track.cl_lat[idx_prev])
    lon_prev = float(track.cl_lon[idx_prev])
    dist_prev = float(track.cl_dist[idx_prev])

    lat_next = float(track.cl_lat[idx_next])
    lon_next = float(track.cl_lon[idx_next])
    dist_next = float(track.cl_dist[idx_next])

    cos_lat = math.cos(math.radians(lat_a))
    dlat_p = lat - lat_a
    dlon_p = (lon - lon_a) * cos_lat

    dlat_next = lat_next - lat_a
    dlon_next = (lon_next - lon_a) * cos_lat
    len2_next = dlat_next * dlat_next + dlon_next * dlon_next
    t_next = (
        (dlat_p * dlat_next + dlon_p * dlon_next) / len2_next
        if len2_next > 1e-15
        else 0.0
    )

    dlat_prev = lat_prev - lat_a
    dlon_prev = (lon_prev - lon_a) * cos_lat
    len2_prev = dlat_prev * dlat_prev + dlon_prev * dlon_prev
    t_prev = (
        (dlat_p * dlat_prev + dlon_p * dlon_prev) / len2_prev
        if len2_prev > 1e-15
        else 0.0
    )

    t_next_clipped = min(0.5, max(-0.5, t_next))
    t_prev_clipped = min(0.5, max(-0.5, t_prev))

    diff_next = ((dist_next - dist_a + 0.5 * lap_len) % lap_len) - 0.5 * lap_len
    diff_prev = ((dist_prev - dist_a + 0.5 * lap_len) % lap_len) - 0.5 * lap_len

    dist_if_next = dist_a + t_next_clipped * diff_next
    dist_if_prev = dist_a + t_prev_clipped * diff_prev

    interpolated_dist = dist_if_next if t_next > t_prev else dist_if_prev
    if interpolated_dist >= lap_len:
        interpolated_dist = lap_len - 1e-3

    final_dist = interpolated_dist % lap_len
    if final_dist < 0.0:
        final_dist += lap_len
    return float(final_dist)


def estimate_straight_delta_s(
    exit_speed_ms: float,
    ref: TurnBenchmark,
    max_session_speed_ms: float,
) -> float:
    """Estimate time delta (s) on the following straight from exit speed difference.

    Fits an empirical acceleration sensitivity `c1 = da/dv <= 0` from the
    reference straight speed trace (capturing power drop-off and drag) and
    forward-integrates `a(s, v) = a_ref(s) + c1 * (v - v_ref(s))` from
    `turn.end` up to the pre-braking end of the accelerating straight, capped by
    vehicle top speed (`max_session_speed_ms`). Positive return value means time lost.
    """
    s = np.asarray(ref.straight_dists_m, dtype=np.float64)
    v = np.asarray(ref.straight_speeds_ms, dtype=np.float64)
    if s.size < 2 or float(s[-1]) <= 0.5:
        return 0.0

    end_idx = int(s.size) - 1
    while end_idx > 0:
        ds = float(s[end_idx] - s[end_idx - 1])
        if ds <= 1e-3:
            end_idx -= 1
            continue
        decel = (float(v[end_idx]) ** 2 - float(v[end_idx - 1]) ** 2) / (2.0 * ds)
        if decel < -2.5:
            end_idx -= 1
        else:
            break

    s_ref = s[:end_idx + 1]
    v_ref = v[:end_idx + 1]
    if s_ref.size < 2 or float(s_ref[-1]) <= 0.5:
        return 0.0

    c1 = 0.0
    total_s = float(s_ref[-1] - s_ref[0])
    if total_s >= 6.0:
        n_grid = max(4, min(60, int(total_s // 2.0) + 1))
        s_grid = np.linspace(float(s_ref[0]), float(s_ref[-1]), n_grid)
        v_grid = np.interp(s_grid, s_ref, v_ref)
        ds_g = np.diff(s_grid)
        v_mid = 0.5 * (v_grid[:-1] + v_grid[1:])
        a_g = (v_grid[1:] ** 2 - v_grid[:-1] ** 2) / (2.0 * ds_g)
        valid = a_g > -2.0
        if int(np.sum(valid)) >= 3 and float(np.ptp(v_mid[valid])) > 0.5:
            slope = float(np.polyfit(v_mid[valid], a_g[valid], 1)[0])
            c1 = max(-0.8, min(0.0, slope))

    v_floor = 2.0
    v_cap = max(float(max_session_speed_ms), float(exit_speed_ms), float(np.max(v_ref)))
    v_est = np.empty_like(v_ref)
    v_est[0] = min(v_cap, max(v_floor, float(exit_speed_ms)))

    for i in range(int(s_ref.size) - 1):
        dsi = float(s_ref[i + 1] - s_ref[i])
        if dsi <= 1e-6:
            v_est[i + 1] = v_est[i]
            continue
        a_ref_i = (float(v_ref[i + 1]) ** 2 - float(v_ref[i]) ** 2) / (2.0 * dsi)
        a_i = a_ref_i + c1 * (float(v_est[i]) - float(v_ref[i]))
        v_next = math.sqrt(max(v_floor ** 2, float(v_est[i]) ** 2 + 2.0 * a_i * dsi))
        v_est[i + 1] = min(v_cap, max(v_floor, v_next))

    inv_diff = (1.0 / v_est) - (1.0 / np.maximum(v_floor, v_ref))
    return float(np.sum(0.5 * (inv_diff[:-1] + inv_diff[1:]) * np.diff(s_ref)))


class LiveFeedbackEngine:
    """Stateful turn-by-turn live telemetry tracker and time-loss estimator."""

    def __init__(
        self,
        track: TrackData,
        origin: Optional[track_data.TrackOrigin] = None,
        benchmark_store: Optional[BenchmarkStore] = None,
        new_best_threshold_s: float = -0.015,
    ) -> None:
        self.track = track
        self.origin = origin
        self.benchmark_store = benchmark_store or BenchmarkStore()
        self.new_best_threshold_s = float(new_best_threshold_s)

        self.lap_num: int = 1
        self._prev_dist: Optional[float] = None
        self._prev_time_s: Optional[float] = None
        self._prev_speed_ms: Optional[float] = None
        self._active_corners: Dict[int, _ActiveCorner] = {}
        self._active_straights: Dict[int, _ActiveStraight] = {}

    def invalidate_active_passes(self) -> None:
        """Discard in-progress corner and straight trackers without clearing benchmarks."""
        self._active_corners.clear()
        self._active_straights.clear()

    def reset_session(self) -> None:
        """Reset all active trackers, lap state, and recorded benchmarks."""
        self.invalidate_active_passes()
        self.benchmark_store.reset()
        self.lap_num = 1
        self._prev_dist = None
        self._prev_time_s = None
        self._prev_speed_ms = None

    def _to_gps(
        self,
        x: Optional[float],
        z: Optional[float],
        lat: Optional[float],
        lon: Optional[float],
    ) -> Optional[Tuple[float, float]]:
        if (
            self.origin is not None
            and x is not None
            and z is not None
            and math.isfinite(x)
            and math.isfinite(z)
        ):
            return self.origin.to_gps(float(x), float(z))
        if (
            lat is not None
            and lon is not None
            and math.isfinite(lat)
            and math.isfinite(lon)
        ):
            return float(lat), float(lon)
        return None

    def _finalize_straight(
        self,
        turn: TurnDef,
        straight: _ActiveStraight,
        t_straight_end: float,
        is_live: bool,
    ) -> TurnCompleteEvent:
        straight_time_s = max(0.0, t_straight_end - straight.t_exit)
        total_time_s = straight.corner_time_s + straight_time_s

        ref = self.benchmark_store.get(turn.index)
        has_prev = ref is not None
        actual_straight_delta_s = (
            straight_time_s - ref.straight_time_s if ref is not None else None
        )
        actual_total_delta_s = (
            total_time_s - ref.total_time_s if ref is not None else None
        )

        if straight.samples:
            s_arr = np.array([pt[0] for pt in straight.samples], dtype=np.float64)
            v_arr = np.array([pt[1] for pt in straight.samples], dtype=np.float64)
        else:
            s_arr = np.zeros(0, dtype=np.float64)
            v_arr = np.zeros(0, dtype=np.float64)

        candidate = TurnBenchmark(
            turn_index=turn.index,
            turn_name=turn.name,
            lap_num=straight.lap_num,
            corner_time_s=straight.corner_time_s,
            straight_time_s=straight_time_s,
            total_time_s=total_time_s,
            exit_speed_ms=straight.v_exit,
            straight_dists_m=s_arr,
            straight_speeds_ms=v_arr,
        )
        updated = self.benchmark_store.maybe_update(candidate)
        best = self.benchmark_store.get(turn.index)
        best_total = best.total_time_s if best is not None else total_time_s

        return TurnCompleteEvent(
            turn_index=turn.index,
            turn_name=turn.name,
            lap_num=straight.lap_num,
            corner_time_s=straight.corner_time_s,
            straight_time_s=straight_time_s,
            total_time_s=total_time_s,
            exit_speed_ms=straight.v_exit,
            has_prev_benchmark=has_prev,
            actual_straight_delta_s=actual_straight_delta_s,
            actual_total_delta_s=actual_total_delta_s,
            benchmark_updated=updated,
            best_total_time_s=best_total,
            is_live=is_live,
        )

    def _handle_corner_exit(
        self,
        turn: TurnDef,
        corner: _ActiveCorner,
        t_cross: float,
        v_cross: float,
        is_live: bool,
    ) -> Tuple[TurnExitEvent, Optional[TurnCompleteEvent]]:
        corner_time_s = max(0.0, t_cross - corner.t_start)
        ref = self.benchmark_store.get(turn.index)

        if ref is not None:
            corner_delta_s = corner_time_s - ref.corner_time_s
            if turn.straight_length > 0.0:
                pred_straight_delta_s = estimate_straight_delta_s(
                    v_cross, ref, self.benchmark_store.max_speed_ms
                )
            else:
                pred_straight_delta_s = 0.0
            pred_total_delta_s = corner_delta_s + pred_straight_delta_s
            exit_event = TurnExitEvent(
                turn_index=turn.index,
                turn_name=turn.name,
                lap_num=corner.lap_num,
                corner_time_s=corner_time_s,
                exit_speed_ms=v_cross,
                has_benchmark=True,
                ref_corner_time_s=ref.corner_time_s,
                ref_exit_speed_ms=ref.exit_speed_ms,
                ref_total_time_s=ref.total_time_s,
                corner_delta_s=corner_delta_s,
                pred_straight_delta_s=pred_straight_delta_s,
                pred_total_delta_s=pred_total_delta_s,
                is_new_best_predicted=pred_total_delta_s < self.new_best_threshold_s,
                is_live=is_live,
            )
        else:
            exit_event = TurnExitEvent(
                turn_index=turn.index,
                turn_name=turn.name,
                lap_num=corner.lap_num,
                corner_time_s=corner_time_s,
                exit_speed_ms=v_cross,
                has_benchmark=False,
                is_live=is_live,
            )

        straight = _ActiveStraight(
            lap_num=corner.lap_num,
            corner_time_s=corner_time_s,
            t_exit=t_cross,
            v_exit=v_cross,
            samples=[(0.0, v_cross)],
        )
        if turn.straight_length <= 0.0:
            complete_event = self._finalize_straight(turn, straight, t_cross, is_live)
            return exit_event, complete_event

        self._active_straights[turn.index] = straight
        return exit_event, None

    def process_sample(
        self,
        time_s: float,
        x: Optional[float] = None,
        z: Optional[float] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        speed_ms: Optional[float] = None,
        is_live: bool = True,
    ) -> Tuple[List[TurnExitEvent], List[TurnCompleteEvent]]:
        """Ingest one telemetry sample and return any turn exit/completion events."""
        if not math.isfinite(time_s):
            return [], []
        if self._prev_time_s is not None and time_s <= self._prev_time_s:
            return [], []

        coords = self._to_gps(x, z, lat, lon)
        if coords is None or speed_ms is None or not math.isfinite(speed_ms):
            self.invalidate_active_passes()
            self._prev_dist = None
            self._prev_time_s = time_s
            self._prev_speed_ms = None
            return [], []

        speed_val = max(0.0, float(speed_ms))
        self.benchmark_store.update_max_speed(speed_val)

        curr_dist = project_to_centerline(
            coords[0], coords[1], self.track, prev_dist=self._prev_dist
        )
        if curr_dist is None:
            self.invalidate_active_passes()
            self._prev_dist = None
            self._prev_time_s = time_s
            self._prev_speed_ms = speed_val
            return [], []

        if self._prev_dist is None or self._prev_time_s is None or self._prev_speed_ms is None:
            self._prev_dist = curr_dist
            self._prev_time_s = time_s
            self._prev_speed_ms = speed_val
            return [], []

        lap_len = self.track.lap_length
        dt = time_s - self._prev_time_s
        prev_dist = self._prev_dist
        step_m = (curr_dist - prev_dist) % lap_len
        max_step = max(25.0, 0.4 * lap_len)

        if speed_val < 1.5 or dt > 2.0:
            self.invalidate_active_passes()
            self._prev_dist = curr_dist
            self._prev_time_s = time_s
            self._prev_speed_ms = speed_val
            return [], []

        if step_m > max_step:
            if (lap_len - step_m) <= 2.0:
                # Ignore tiny backward GPS jitter without resetting active trackers
                self._prev_time_s = time_s
                self._prev_speed_ms = speed_val
                return [], []
            self.invalidate_active_passes()
            self._prev_dist = curr_dist
            self._prev_time_s = time_s
            self._prev_speed_ms = speed_val
            return [], []

        crossed_sf = curr_dist - prev_dist < -0.5 * lap_len
        sf_offset = (lap_len - prev_dist) % lap_len if crossed_sf else lap_len + 1.0
        old_lap = self.lap_num
        if crossed_sf:
            self.lap_num += 1

        # Collect all boundary crossings in (prev_dist, curr_dist] ordered by progress
        # Phase order at identical distance: 0=straight_end, 1=corner_start, 2=corner_end
        crossings: List[Tuple[float, int, int, str]] = []
        if step_m > 0.0:
            for turn in self.track.turns:
                if turn.straight_length > 0.0:
                    off_se = (turn.straight_end - prev_dist) % lap_len
                    if 0.0 < off_se <= step_m:
                        crossings.append((off_se, 0, turn.index, 'straight_end'))

                off_cs = (turn.corner_start - prev_dist) % lap_len
                if 0.0 < off_cs <= step_m:
                    crossings.append((off_cs, 1, turn.index, 'corner_start'))

                off_ce = (turn.corner_end - prev_dist) % lap_len
                if 0.0 < off_ce <= step_m:
                    crossings.append((off_ce, 2, turn.index, 'corner_end'))

        crossings.sort(key=lambda item: (item[0], item[1], item[2]))

        exit_events: List[TurnExitEvent] = []
        complete_events: List[TurnCompleteEvent] = []
        prev_t = self._prev_time_s
        prev_v = self._prev_speed_ms

        for offset, _phase_order, turn_idx, kind in crossings:
            alpha = offset / step_m
            t_cross = prev_t + alpha * dt
            v_cross = prev_v + alpha * (speed_val - prev_v)
            turn = self.track.turns[turn_idx]

            if kind == 'straight_end':
                straight = self._active_straights.pop(turn_idx, None)
                if straight is not None:
                    straight.samples.append((turn.straight_length, v_cross))
                    complete_events.append(
                        self._finalize_straight(turn, straight, t_cross, is_live)
                    )
            elif kind == 'corner_start':
                cross_lap = (
                    old_lap + 1 if (crossed_sf and offset >= sf_offset) else old_lap
                )
                self._active_corners[turn_idx] = _ActiveCorner(
                    lap_num=cross_lap,
                    t_start=t_cross,
                    v_entry=v_cross,
                )
            elif kind == 'corner_end':
                corner = self._active_corners.pop(turn_idx, None)
                if corner is not None:
                    exit_ev, comp_ev = self._handle_corner_exit(
                        turn, corner, t_cross, v_cross, is_live
                    )
                    exit_events.append(exit_ev)
                    if comp_ev is not None:
                        complete_events.append(comp_ev)

        for turn_idx, straight in list(self._active_straights.items()):
            turn = self.track.turns[turn_idx]
            rel_s = (curr_dist - turn.corner_end) % lap_len
            if 0.0 < rel_s < turn.straight_length:
                if not straight.samples or rel_s > straight.samples[-1][0] + 1e-4:
                    straight.samples.append((rel_s, speed_val))

        self._prev_dist = curr_dist
        self._prev_time_s = time_s
        self._prev_speed_ms = speed_val
        return exit_events, complete_events
