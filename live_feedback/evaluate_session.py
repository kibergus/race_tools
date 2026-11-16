"""Offline accuracy evaluator for historical BRT1 telemetry recordings."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import brotli  # type: ignore[import-untyped]
import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_feedback.engine import BenchmarkStore, LiveFeedbackEngine, TurnExitEvent  # noqa: E402
from live_feedback.main import DEFAULT_SERVER_URL, load_app_config  # noqa: E402
from live_feedback.stream_server import Brt1IncrementalDecoder, DecodedSample  # noqa: E402
from live_feedback.track_loader import (  # noqa: E402
    TrackData,
    TrackLoader,
    parse_track_payload,
    resolve_track_origin,
)


@dataclass(frozen=True)
class PassEvalRecord:
    """Single matched turn-exit prediction and realized straight-delta pair."""

    lap_num: int
    turn_index: int
    turn_name: str
    exit_speed_kmh: float
    ref_exit_speed_kmh: float
    pred_straight_delta_s: float
    actual_straight_delta_s: float
    error_s: float
    corner_delta_s: float
    pred_total_delta_s: float
    actual_total_delta_s: float


@dataclass(frozen=True)
class ErrorStats:
    """Summary straight-delta error statistics (in milliseconds)."""

    count: int
    mae_ms: float
    medae_ms: float
    rmse_ms: float
    bias_ms: float
    corr_r: Optional[float]


@dataclass(frozen=True)
class TurnEvalSummary:
    """Per-turn clean-pass straight prediction accuracy summary."""

    turn_index: int
    turn_name: str
    straight_length_m: float
    stats: ErrorStats


@dataclass(frozen=True)
class SessionEvalReport:
    """Complete prediction accuracy report for one recorded BRT1 session."""

    file_path: str
    track_name: str
    driver_name: str
    league: str
    class_name: str
    samples_count: int
    duration_s: float
    laps_count: int
    mode: str
    max_abs_delta_s: float
    all_stats: ErrorStats
    clean_stats: ErrorStats
    corner_only_total_mae_ms: Optional[float]
    predicted_total_mae_ms: Optional[float]
    per_turn: List[TurnEvalSummary]
    records: List[PassEvalRecord]


def load_brt1_file(
    path: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[DecodedSample]]:
    """Load and decode a `.brt1.br` or `.brt1` recording."""
    with open(path, 'rb') as f:
        raw = f.read()
    if path.lower().endswith('.br'):
        raw = brotli.decompress(raw)

    dec = Brt1IncrementalDecoder()
    dec.feed(raw)
    new_meta, samples, _ = dec.pop_chunks()
    metadata = new_meta if new_meta is not None else dec.metadata
    if not metadata:
        raise ValueError(f'No BRT1 metadata chunk found in {path!r}.')
    return dict(metadata), list(dec.channels), samples


def _find_matching_track_in_dir(
    tracks_dir: str,
    track_name: str,
    require_turns: bool = False,
) -> Optional[TrackData]:
    if not os.path.isdir(tracks_dir):
        return None
    target = track_name.strip().lower()
    if not target:
        return None

    for entry in sorted(os.listdir(tracks_dir)):
        if not entry.lower().endswith('.json'):
            continue
        stem = entry[:-5].strip().lower()
        fpath = os.path.join(tracks_dir, entry)
        with open(fpath, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            continue
        if require_turns and not raw.get('turns'):
            continue

        candidates = {stem, stem.replace('_', ' ')}
        if raw.get('track_name'):
            candidates.add(str(raw['track_name']).strip().lower())
        for alias in raw.get('aliases') or []:
            if alias is not None:
                candidates.add(str(alias).strip().lower())

        if target in candidates:
            return parse_track_payload(raw, fallback_name=track_name)
    return None


def _resolve_effective_api_key(explicit_api_key: str) -> str:
    if explicit_api_key.strip():
        return explicit_api_key.strip()
    env_key = os.environ.get('BRBRDB_API_KEY', '').strip()
    if env_key:
        return env_key
    cfg = load_app_config()
    return str(cfg.get('api_key', '')).strip()


def resolve_track_for_eval(
    track_name: str,
    track_json: Optional[str] = None,
    tracks_dir: Optional[str] = None,
    server_url: str = DEFAULT_SERVER_URL,
    api_key: str = '',
) -> TrackData:
    """Resolve `TrackData` from local JSON, tracks directory, or backend server."""
    track: Optional[TrackData] = None
    if track_json:
        with open(track_json, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        track = parse_track_payload(raw, fallback_name=track_name)
    elif tracks_dir:
        track = _find_matching_track_in_dir(tracks_dir, track_name, require_turns=False)
        if track is None:
            raise FileNotFoundError(
                f'No track JSON matching {track_name!r} found in {tracks_dir!r}.'
            )
    else:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_tracks_dir = os.path.join(repo_root, 'data', 'tracks')
        track = _find_matching_track_in_dir(
            default_tracks_dir, track_name, require_turns=True
        )
        if track is None:
            eff_key = _resolve_effective_api_key(api_key)
            track = TrackLoader().fetch(server_url, track_name, api_key=eff_key)

    if not track.turns:
        raise ValueError(f'Track {track.track_name!r} has no turn definitions.')
    return track


def _compute_error_stats(records: Sequence[PassEvalRecord]) -> ErrorStats:
    if not records:
        return ErrorStats(
            count=0,
            mae_ms=0.0,
            medae_ms=0.0,
            rmse_ms=0.0,
            bias_ms=0.0,
            corr_r=None,
        )

    errs_ms = np.array([r.error_s for r in records], dtype=np.float64) * 1000.0
    preds = np.array([r.pred_straight_delta_s for r in records], dtype=np.float64)
    acts = np.array([r.actual_straight_delta_s for r in records], dtype=np.float64)

    corr_r: Optional[float] = None
    if len(records) >= 3 and float(np.std(preds)) > 1e-12 and float(np.std(acts)) > 1e-12:
        corr_r = float(np.corrcoef(preds, acts)[0, 1])

    return ErrorStats(
        count=len(records),
        mae_ms=float(np.mean(np.abs(errs_ms))),
        medae_ms=float(np.median(np.abs(errs_ms))),
        rmse_ms=float(np.sqrt(np.mean(errs_ms ** 2))),
        bias_ms=float(np.mean(errs_ms)),
        corr_r=corr_r,
    )


def evaluate_brt1_file(
    file_path: str,
    track_json: Optional[str] = None,
    tracks_dir: Optional[str] = None,
    server_url: str = DEFAULT_SERVER_URL,
    api_key: str = '',
    mode: str = 'causal',
    max_abs_delta_s: float = 1.0,
) -> SessionEvalReport:
    """Replay a recorded `.brt1.br` / `.brt1` file and measure prediction error."""
    if mode not in ('causal', 'fixed'):
        raise ValueError(f'Invalid evaluation mode {mode!r}; expected causal or fixed.')
    if max_abs_delta_s <= 0.0:
        raise ValueError(f'max_abs_delta_s must be positive, got {max_abs_delta_s!r}.')

    metadata, channels, samples = load_brt1_file(file_path)
    meta_lower = {str(k).strip().lower(): v for k, v in metadata.items()}
    raw_track = str(meta_lower.get('track name', '')).strip()
    league = str(meta_lower.get('league', '')).strip()
    driver_name = str(meta_lower.get('driver name', '')).strip()
    class_name = str(meta_lower.get('class', '')).strip()
    if not raw_track:
        raise ValueError(f'Track name missing in metadata for {file_path!r}.')

    track = resolve_track_for_eval(
        track_name=raw_track,
        track_json=track_json,
        tracks_dir=tracks_dir,
        server_url=server_url,
        api_key=api_key,
    )
    origin = resolve_track_origin(league, track.track_name, raw_track, metadata)
    ch_names = {str(ch.get('name', '')).strip().lower() for ch in channels}
    if origin is None and 'latitude' not in ch_names:
        raise ValueError(
            f'No TrackOrigin found for track {raw_track!r} (league {league!r}).'
        )

    store = BenchmarkStore()
    if mode == 'fixed':
        bench_engine = LiveFeedbackEngine(track=track, origin=origin, benchmark_store=store)
        for s in samples:
            bench_engine.process_sample(
                time_s=s.time_s,
                x=s.x,
                z=s.z,
                lat=s.lat,
                lon=s.lon,
                speed_ms=s.speed_ms,
                is_live=True,
            )

    fixed_refs = store.all_benchmarks() if mode == 'fixed' else {}
    engine = LiveFeedbackEngine(track=track, origin=origin, benchmark_store=store)
    pending: Dict[Tuple[int, int], TurnExitEvent] = {}
    records: List[PassEvalRecord] = []

    for s in samples:
        ex_list, co_list = engine.process_sample(
            time_s=s.time_s,
            x=s.x,
            z=s.z,
            lat=s.lat,
            lon=s.lon,
            speed_ms=s.speed_ms,
            is_live=(mode == 'causal'),
        )
        for ex in ex_list:
            if ex.has_benchmark and ex.pred_straight_delta_s is not None:
                pending[(ex.turn_index, ex.lap_num)] = ex
        for co in co_list:
            ex = pending.pop((co.turn_index, co.lap_num), None)
            if ex is None or ex.ref_exit_speed_ms is None:
                continue
            if ex.corner_delta_s is None or ex.pred_straight_delta_s is None:
                continue
            if ex.pred_total_delta_s is None:
                continue

            if mode == 'fixed':
                ref = fixed_refs.get(co.turn_index)
                if ref is None or co.lap_num == ref.lap_num:
                    continue
                actual_straight = co.straight_time_s - ref.straight_time_s
                actual_total = co.total_time_s - ref.total_time_s
            else:
                if co.actual_straight_delta_s is None or co.actual_total_delta_s is None:
                    continue
                actual_straight = co.actual_straight_delta_s
                actual_total = co.actual_total_delta_s

            pred_straight = float(ex.pred_straight_delta_s)
            records.append(
                PassEvalRecord(
                    lap_num=co.lap_num,
                    turn_index=co.turn_index,
                    turn_name=co.turn_name,
                    exit_speed_kmh=ex.exit_speed_ms * 3.6,
                    ref_exit_speed_kmh=ex.ref_exit_speed_ms * 3.6,
                    pred_straight_delta_s=pred_straight,
                    actual_straight_delta_s=float(actual_straight),
                    error_s=pred_straight - float(actual_straight),
                    corner_delta_s=float(ex.corner_delta_s),
                    pred_total_delta_s=float(ex.pred_total_delta_s),
                    actual_total_delta_s=float(actual_total),
                )
            )

    clean_records = [
        r for r in records if abs(r.actual_straight_delta_s) < max_abs_delta_s
    ]
    all_stats = _compute_error_stats(records)
    clean_stats = _compute_error_stats(clean_records)

    corner_only_mae: Optional[float] = None
    predicted_total_mae: Optional[float] = None
    if clean_records:
        c_only_err = np.array(
            [r.corner_delta_s - r.actual_total_delta_s for r in clean_records],
            dtype=np.float64,
        )
        p_tot_err = np.array(
            [r.pred_total_delta_s - r.actual_total_delta_s for r in clean_records],
            dtype=np.float64,
        )
        corner_only_mae = float(np.mean(np.abs(c_only_err)) * 1000.0)
        predicted_total_mae = float(np.mean(np.abs(p_tot_err)) * 1000.0)

    per_turn: List[TurnEvalSummary] = []
    for t in track.turns:
        t_clean = [r for r in clean_records if r.turn_index == t.index]
        per_turn.append(
            TurnEvalSummary(
                turn_index=t.index,
                turn_name=t.name,
                straight_length_m=t.straight_length,
                stats=_compute_error_stats(t_clean),
            )
        )

    duration_s = (
        float(samples[-1].time_s - samples[0].time_s) if len(samples) >= 2 else 0.0
    )
    return SessionEvalReport(
        file_path=file_path,
        track_name=track.track_name,
        driver_name=driver_name,
        league=league,
        class_name=class_name,
        samples_count=len(samples),
        duration_s=duration_s,
        laps_count=engine.lap_num,
        mode=mode,
        max_abs_delta_s=max_abs_delta_s,
        all_stats=all_stats,
        clean_stats=clean_stats,
        corner_only_total_mae_ms=corner_only_mae,
        predicted_total_mae_ms=predicted_total_mae,
        per_turn=per_turn,
        records=records,
    )


def _fmt_stats_line(label: str, stats: ErrorStats) -> str:
    r_str = f'{stats.corr_r:.3f}' if stats.corr_r is not None else 'n/a'
    return (
        f'{label} (n={stats.count}): '
        f'MAE={stats.mae_ms:.1f}ms, MedAE={stats.medae_ms:.1f}ms, '
        f'RMSE={stats.rmse_ms:.1f}ms, Bias={stats.bias_ms:+.1f}ms, r={r_str}'
    )


def format_evaluation_report(report: SessionEvalReport) -> str:
    """Format a `SessionEvalReport` into a human-readable summary table."""
    lines = [
        f'=== {os.path.basename(report.file_path)} ===',
        (
            f'Track: {report.track_name} | League: {report.league} | '
            f'Class: {report.class_name} | Driver: {report.driver_name}'
        ),
        (
            f'Samples: {report.samples_count} ({report.duration_s:.1f}s) | '
            f'Laps: {report.laps_count} | Mode: {report.mode}'
        ),
        '  ' + _fmt_stats_line('All passes', report.all_stats),
        '  ' + _fmt_stats_line(
            f'Clean passes (|actual| < {report.max_abs_delta_s:.2f}s)',
            report.clean_stats,
        ),
    ]
    if (
        report.corner_only_total_mae_ms is not None
        and report.predicted_total_mae_ms is not None
    ):
        lines.append(
            f'  Total (corner+straight) MAE: '
            f'corner-only={report.corner_only_total_mae_ms:.1f}ms -> '
            f'with straight pred={report.predicted_total_mae_ms:.1f}ms'
        )
    lines.append('  Per-turn clean breakdown:')
    for t in report.per_turn:
        st = t.stats
        if st.count == 0:
            lines.append(
                f'    T{t.turn_index:<2d} {t.turn_name:<16s} '
                f'(str={t.straight_length_m:5.1f}m, n= 0): no clean passes'
            )
            continue
        r_str = f'{st.corr_r:.3f}' if st.corr_r is not None else '  n/a'
        lines.append(
            f'    T{t.turn_index:<2d} {t.turn_name:<16s} '
            f'(str={t.straight_length_m:5.1f}m, n={st.count:2d}): '
            f'MAE={st.mae_ms:5.1f}ms  MedAE={st.medae_ms:5.1f}ms  '
            f'RMSE={st.rmse_ms:5.1f}ms  Bias={st.bias_ms:+6.1f}ms  r={r_str}'
        )
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for offline BRT1 straight-delta prediction evaluation."""
    parser = argparse.ArgumentParser(
        description='Evaluate live_feedback straight-delta accuracy on recorded BRT1 files.',
    )
    parser.add_argument(
        'files',
        nargs='+',
        help='One or more recorded .brt1.br or .brt1 telemetry files.',
    )
    parser.add_argument(
        '--track-json',
        default=None,
        help='Optional path to a specific local track JSON file.',
    )
    parser.add_argument(
        '--tracks-dir',
        default=None,
        help='Optional directory containing track JSON files for offline lookup.',
    )
    parser.add_argument(
        '--server',
        default=DEFAULT_SERVER_URL,
        help=f'Backend server base URL fallback (default: {DEFAULT_SERVER_URL}).',
    )
    parser.add_argument(
        '--api-key',
        default='',
        help='Optional API key for --server (otherwise BRBRDB_API_KEY env var or config).',
    )
    parser.add_argument(
        '--mode',
        choices=('causal', 'fixed'),
        default='causal',
        help='causal=live evolving session benchmarks; fixed=final session-best benchmark.',
    )
    parser.add_argument(
        '--max-abs-delta',
        type=float,
        default=1.0,
        help='Clean-pass filter threshold on |actual_straight_delta_s| in seconds (default: 1.0).',
    )
    args = parser.parse_args(argv)

    for idx, fpath in enumerate(args.files):
        report = evaluate_brt1_file(
            file_path=fpath,
            track_json=args.track_json,
            tracks_dir=args.tracks_dir,
            server_url=args.server,
            api_key=args.api_key,
            mode=args.mode,
            max_abs_delta_s=args.max_abs_delta,
        )
        if idx > 0:
            print()
        print(format_evaluation_report(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
