"""Unit tests for the live_feedback package."""

from __future__ import annotations

import io
import json
import math
import os
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch
import urllib.error
import urllib.request

import brotli  # type: ignore[import-untyped]
import numpy as np
import pygame
import pytest

from live_feedback import evaluate_session
from live_feedback.audio import (
    AudioCuePlayer,
    loss_to_frequency_hz,
    synthesize_chirp_pcm,
    synthesize_tone_pcm,
)
from live_feedback.engine import (
    BenchmarkStore,
    LiveFeedbackEngine,
    TurnBenchmark,
    estimate_straight_delta_s,
    project_to_centerline,
)
from live_feedback.main import LiveFeedbackApp, LiveFeedbackController
from live_feedback.stream_server import DecodedSample, LiveStreamServer
from live_feedback.track_loader import (
    TrackData,
    TrackLoader,
    parse_track_payload,
    resolve_track_origin,
)
from race_tools import assetto_corsa, rfactor


def _dominant_freq_hz(pcm: np.ndarray, sample_rate: int = 44100) -> float:
    arr = pcm.astype(np.float64)
    spec = np.abs(np.fft.rfft(arr))
    freqs = np.fft.rfftfreq(arr.size, d=1.0 / float(sample_rate))
    return float(freqs[int(np.argmax(spec))])


def _make_circle_track_payload(
    origin: rfactor.RFactorTrackOrigin,
    lap_length: float = 1000.0,
    num_pts: int = 200,
) -> Dict[str, Any]:
    radius = lap_length / (2.0 * math.pi)
    cl = []
    for i in range(num_pts):
        dist = (float(i) / float(num_pts)) * lap_length
        theta = (dist / lap_length) * (2.0 * math.pi)
        x = radius * math.cos(theta)
        z = radius * math.sin(theta)
        lat, lon = origin.to_gps(x, z)
        cl.append({'lat': lat, 'lon': lon, 'dist': dist})

    return {
        'track_name': 'Rowrah',
        'lap_length': lap_length,
        'center_line': cl,
        'turns': [
            {'name': 'Turn 1', 'start': 100.0, 'apex': 150.0, 'end': 200.0},
            {'name': 'Turn 2', 'start': 450.0, 'apex': 500.0, 'end': 550.0},
        ],
    }


def _circle_xy_at_dist(dist: float, lap_length: float = 1000.0) -> Tuple[float, float]:
    radius = lap_length / (2.0 * math.pi)
    theta = (dist / lap_length) * (2.0 * math.pi)
    return radius * math.cos(theta), radius * math.sin(theta)


def _frame_brt1_chunk(chunk_type: int, payload: bytes) -> bytes:
    return struct.pack('<HB', len(payload), chunk_type) + payload


def _encode_brt1_metadata(metadata: Dict[str, Any], channels: List[Dict[str, Any]]) -> bytes:
    body = json.dumps({'metadata': metadata, 'channels': channels}).encode('utf-8')
    return _frame_brt1_chunk(0x02, body)


def _encode_brt1_baseline(values: List[float]) -> bytes:
    payload = struct.pack(f'<{len(values)}d', *values)
    return _frame_brt1_chunk(0x03, payload)


def _encode_brt1_delta_rows(
    prev_values: List[float],
    rows: List[List[float]],
    scales: List[float],
) -> bytes:
    deltas: List[int] = []
    tracked = list(prev_values)
    for row in rows:
        for c, val in enumerate(row):
            if math.isnan(val):
                deltas.append(-32768)
                tracked[c] = float('nan')
            else:
                q = int(round((val - tracked[c]) / scales[c]))
                deltas.append(q)
                tracked[c] += q * scales[c]
    payload = struct.pack(f'<{len(deltas)}h', *deltas)
    return _frame_brt1_chunk(0x04, payload)


def _http_chunk(data: bytes) -> bytes:
    return f'{len(data):x}\r\n'.encode('ascii') + data + b'\r\n'


def test_loss_to_frequency_logarithmic_and_synthesis() -> None:
    f_0 = loss_to_frequency_hz(0.0)
    f_neg = loss_to_frequency_hz(-0.2)
    f_mid = loss_to_frequency_hz(0.25)
    f_max = loss_to_frequency_hz(0.50)
    f_over = loss_to_frequency_hz(1.50)

    assert math.isclose(f_0, 1200.0, rel_tol=1e-9)
    assert math.isclose(f_neg, 1200.0, rel_tol=1e-9)
    assert math.isclose(f_max, 240.0, rel_tol=1e-9)
    assert math.isclose(f_over, 240.0, rel_tol=1e-9)
    assert math.isclose(f_mid, math.sqrt(1200.0 * 240.0), rel_tol=1e-6)

    # Equal seconds-lost intervals must produce equal frequency ratios
    freqs = [loss_to_frequency_hz(0.1 * k) for k in range(6)]
    ratios = [freqs[k + 1] / freqs[k] for k in range(5)]
    for r in ratios:
        assert math.isclose(r, ratios[0], rel_tol=1e-7)

    tone = synthesize_tone_pcm(440.0, duration_s=0.16, volume=0.8, sample_rate=44100)
    assert tone.dtype == np.int16
    assert tone.size == int(round(0.16 * 44100))
    assert abs(int(tone[0])) < 50
    assert abs(int(tone[-1])) < 50
    assert abs(_dominant_freq_hz(tone, 44100) - 440.0) < 15.0

    chirp = synthesize_chirp_pcm(volume=0.8, sample_rate=44100)
    assert chirp.dtype == np.int16
    first_half = chirp[:int(0.065 * 44100)]
    second_half = chirp[-int(0.095 * 44100):]
    assert _dominant_freq_hz(second_half, 44100) > _dominant_freq_hz(first_half, 44100)


def test_audio_cue_player_pygame_playback(monkeypatch: pytest.MonkeyPatch) -> None:
    player = AudioCuePlayer(volume=0.05)
    try:
        try:
            player.init_mixer()
        except pygame.error:
            monkeypatch.setenv('SDL_AUDIODRIVER', 'dummy')
            player.init_mixer()

        player.play_tone_hz(880.0)
        assert player._active_sound is not None
        player.play_new_best()
        assert player._active_sound is not None
    finally:
        player.stop()


def test_audio_cue_player_dispatch_and_mute() -> None:
    player = AudioCuePlayer(volume=0.75, muted=False)
    played: List[Tuple[str, Optional[float]]] = []

    with patch.object(player, 'play_new_best', side_effect=lambda: played.append(('chirp', None))), \
         patch.object(player, 'play_tone_hz', side_effect=lambda hz: played.append(('tone', hz))):
        player.play_turn_cue(-0.025)
        player.play_turn_cue(0.00)
        player.play_turn_cue(0.25)
        player.set_muted(True)
        player.play_turn_cue(0.10)

    assert len(played) == 3
    assert played[0][0] == 'chirp'
    assert played[1][0] == 'tone' and math.isclose(played[1][1] or 0.0, 1200.0, rel_tol=1e-6)
    assert played[2][0] == 'tone' and math.isclose(
        played[2][1] or 0.0, math.sqrt(1200.0 * 240.0), rel_tol=1e-6
    )


def test_parse_track_payload_wraparound_and_chicane() -> None:
    origin = rfactor.get_track_origin('Rowrah')
    payload = _make_circle_track_payload(origin, lap_length=1000.0)
    payload['turns'] = [
        {'name': 'T1', 'start': 100.0, 'end': 180.0},
        {'name': 'T2 Chicane A', 'start': 260.0, 'end': 320.0},
        {'name': 'T3 Chicane B', 'start': 315.0, 'end': 380.0},
        {'name': 'T4 Final', 'start': 820.0, 'end': 900.0},
    ]

    track = parse_track_payload(payload)
    assert math.isclose(track.lap_length, 1000.0)
    assert len(track.turns) == 4
    # T1 straight goes 180 -> 260
    assert math.isclose(track.turns[0].straight_length, 80.0)
    # T2 overlaps T3 (315 <= 320), so straight is collapsed to 0
    assert math.isclose(track.turns[1].straight_length, 0.0)
    assert math.isclose(track.turns[1].straight_end, 320.0)
    # T4 wraps across S/F (900 -> 100 = 200 m)
    assert math.isclose(track.turns[3].straight_length, 200.0)
    assert math.isclose(track.turns[3].straight_end, 100.0)


def test_project_to_centerline_bounds_wrap_and_crossover() -> None:
    origin = rfactor.get_track_origin('Rowrah')
    track = parse_track_payload(_make_circle_track_payload(origin, lap_length=1000.0))

    # Out-of-bounds / invalid points
    assert project_to_centerline(0.0, 0.0, track) is None
    assert project_to_centerline(83.5, 73.0, track) is None
    assert project_to_centerline(float('nan'), 0.0, track) is None

    # Interpolated point near S/F wrap (998.5 m)
    x, z = _circle_xy_at_dist(998.5, lap_length=1000.0)
    lat, lon = origin.to_gps(x, z)
    proj = project_to_centerline(lat, lon, track, prev_dist=995.0)
    assert proj is not None
    assert abs(((proj - 998.5 + 500.0) % 1000.0) - 500.0) < 0.6

    # Crossover continuity bias: two centerline sections cross near (54.5515, -3.4390)
    base_lat = 54.5515
    base_lon = -3.4390
    n = 100
    cl_pts = []
    for i in range(n):
        dist = float(i) * 10.0
        if i < 50:
            # First pass goes North through crossing at idx=20 (dist=200)
            p_lat = base_lat + (i - 20) * 0.0001
            p_lon = base_lon
        else:
            # Second pass goes East through exact same point at idx=70 (dist=700)
            p_lat = base_lat
            p_lon = base_lon + (i - 70) * 0.00015
        cl_pts.append({'lat': p_lat, 'lon': p_lon, 'dist': dist})

    cross_track = parse_track_payload(
        {'track_name': 'Cross', 'lap_length': 1000.0, 'center_line': cl_pts, 'turns': []}
    )
    d_pass1 = project_to_centerline(base_lat, base_lon, cross_track, prev_dist=192.0)
    d_pass2 = project_to_centerline(base_lat, base_lon, cross_track, prev_dist=692.0)
    assert d_pass1 is not None and abs(d_pass1 - 200.0) < 2.0
    assert d_pass2 is not None and abs(d_pass2 - 700.0) < 2.0


def test_resolve_track_origin_leagues_and_metadata() -> None:
    rf_orig = resolve_track_origin('kartsim', 'Rowrah Lakeland Circuit 2026', 'Rowrah', {})
    assert isinstance(rf_orig, rfactor.RFactorTrackOrigin)

    ac_orig = resolve_track_origin('acevo', 'monza', 'monza', {})
    assert isinstance(ac_orig, assetto_corsa.AssettoCorsaTrackOrigin)

    custom_rf = resolve_track_origin(
        'kartsim',
        'CustomTrack',
        'CustomTrack',
        {'Track Origin Latitude': '52.1', 'Track Origin Longitude': '-1.2'},
    )
    assert isinstance(custom_rf, rfactor.RFactorTrackOrigin)
    assert math.isclose(custom_rf.lat, 52.1) and math.isclose(custom_rf.lon, -1.2)


def test_estimate_straight_delta_physics_and_braking_trim() -> None:
    accel = 2.0
    v0_ref = 16.0
    v0_curr = 14.5
    length_m = 200.0

    s_acc = np.linspace(0.0, length_m, 101)
    v_acc = np.sqrt(v0_ref ** 2 + 2.0 * accel * s_acc)

    # Exact analytical time difference on constant-acceleration straight
    t_ref = (math.sqrt(v0_ref ** 2 + 2.0 * accel * length_m) - v0_ref) / accel
    t_curr = (math.sqrt(v0_curr ** 2 + 2.0 * accel * length_m) - v0_curr) / accel
    expected_delta = t_curr - t_ref

    ref_clean = TurnBenchmark(
        turn_index=0,
        turn_name='T1',
        lap_num=1,
        corner_time_s=5.0,
        straight_time_s=t_ref,
        total_time_s=5.0 + t_ref,
        exit_speed_ms=v0_ref,
        straight_dists_m=s_acc,
        straight_speeds_ms=v_acc,
    )
    est_clean = estimate_straight_delta_s(v0_curr, ref_clean, max_session_speed_ms=40.0)
    assert abs(est_clean - expected_delta) < 0.002

    # Append 30 m of heavy braking (-6.0 m/s^2) at end of straight; should be trimmed cleanly
    s_brk = np.array([210.0, 220.0, 230.0])
    v_peak = float(v_acc[-1])
    v_brk = np.sqrt(np.maximum(4.0, v_peak ** 2 - 2.0 * 6.0 * (s_brk - 200.0)))
    ref_with_brake = TurnBenchmark(
        turn_index=0,
        turn_name='T1',
        lap_num=1,
        corner_time_s=5.0,
        straight_time_s=t_ref + 1.2,
        total_time_s=6.2 + t_ref,
        exit_speed_ms=v0_ref,
        straight_dists_m=np.concatenate([s_acc, s_brk]),
        straight_speeds_ms=np.concatenate([v_acc, v_brk]),
    )
    est_trimmed = estimate_straight_delta_s(v0_curr, ref_with_brake, max_session_speed_ms=40.0)
    assert abs(est_trimmed - expected_delta) < 0.003

    # Uphill slight deceleration (-0.8 m/s^2) must NOT be trimmed
    s_up = np.linspace(0.0, 100.0, 21)
    v_up = np.sqrt(20.0 ** 2 - 2.0 * 0.8 * s_up)
    ref_up = TurnBenchmark(
        turn_index=0,
        turn_name='Uphill',
        lap_num=1,
        corner_time_s=4.0,
        straight_time_s=5.2,
        total_time_s=9.2,
        exit_speed_ms=20.0,
        straight_dists_m=s_up,
        straight_speeds_ms=v_up,
    )
    est_up = estimate_straight_delta_s(18.0, ref_up, max_session_speed_ms=30.0)
    assert est_up > 0.3

    # Top-speed cap: faster exit cannot exceed vehicle top speed on flat max-speed straight
    s_flat = np.linspace(0.0, 150.0, 31)
    v_flat = np.full_like(s_flat, 26.0)
    ref_flat = TurnBenchmark(
        turn_index=0,
        turn_name='Flat',
        lap_num=1,
        corner_time_s=4.0,
        straight_time_s=5.77,
        total_time_s=9.77,
        exit_speed_ms=26.0,
        straight_dists_m=s_flat,
        straight_speeds_ms=v_flat,
    )
    est_capped = estimate_straight_delta_s(26.5, ref_flat, max_session_speed_ms=26.5)
    assert abs(est_capped) < 0.12

    # Power-limited + drag straight: v * dv/ds = P/m / v - k * v^2
    p_per_kg = 55.0
    k_drag = 0.004
    ds_fine = 0.5
    s_power = np.arange(0.0, 130.0 + 0.5 * ds_fine, ds_fine)

    def _integrate_power_straight(v0: float) -> Tuple[np.ndarray, float]:
        vs = np.empty_like(s_power)
        vs[0] = v0
        for idx in range(s_power.size - 1):
            a_cur = (p_per_kg / vs[idx]) - (k_drag * (vs[idx] ** 2))
            vs[idx + 1] = math.sqrt(max(4.0, (vs[idx] ** 2) + 2.0 * a_cur * ds_fine))
        inv = 1.0 / vs
        elapsed = float(np.sum(0.5 * (inv[:-1] + inv[1:]) * np.diff(s_power)))
        return vs, elapsed

    v_pow_ref, t_pow_ref = _integrate_power_straight(17.0)
    _, t_pow_curr = _integrate_power_straight(15.0)
    true_delta_s = t_pow_curr - t_pow_ref
    ref_power = TurnBenchmark(
        turn_index=0,
        turn_name='PowerDrag',
        lap_num=1,
        corner_time_s=4.0,
        straight_time_s=t_pow_ref,
        total_time_s=4.0 + t_pow_ref,
        exit_speed_ms=17.0,
        straight_dists_m=s_power,
        straight_speeds_ms=v_pow_ref,
    )
    est_power = estimate_straight_delta_s(15.0, ref_power, max_session_speed_ms=30.0)
    assert abs(est_power - true_delta_s) < 0.025


def _simulate_lap_into_engine(
    engine: LiveFeedbackEngine,
    t_start: float,
    lap_length: float,
    speed_fn: Any,
    s_start: float = 1.0,
    dt: float = 0.01,
) -> Tuple[float, float, List[Any], List[Any]]:
    t = t_start
    s = s_start
    exits: List[Any] = []
    completes: List[Any] = []
    while s < lap_length:
        v = float(speed_fn(s))
        x, z = _circle_xy_at_dist(s, lap_length=lap_length)
        ev, cev = engine.process_sample(time_s=t, x=x, z=z, speed_ms=v, is_live=True)
        exits.extend(ev)
        completes.extend(cev)
        s += v * dt
        t += dt
    # Step across S/F line into next lap
    s_wrap = s - lap_length
    v = float(speed_fn(s_wrap))
    x, z = _circle_xy_at_dist(s_wrap, lap_length=lap_length)
    ev, cev = engine.process_sample(time_s=t, x=x, z=z, speed_ms=v, is_live=True)
    exits.extend(ev)
    completes.extend(cev)
    return t + dt, s_wrap + v * dt, exits, completes


def test_live_feedback_engine_multi_lap_and_spin_guard() -> None:
    origin = rfactor.get_track_origin('Rowrah')
    lap_len = 1000.0
    track = parse_track_payload(_make_circle_track_payload(origin, lap_length=lap_len))
    store = BenchmarkStore()
    engine = LiveFeedbackEngine(track=track, origin=origin, benchmark_store=store)

    def speed_profile(base_corner_v: float, exit_v: float, accel: float = 1.5) -> Any:
        def _fn(s: float) -> float:
            if 100.0 <= s < 200.0:
                frac = (s - 100.0) / 100.0
                return base_corner_v + frac * (exit_v - base_corner_v)
            if 200.0 <= s < 450.0:
                return math.sqrt(exit_v ** 2 + 2.0 * accel * (s - 200.0))
            return 22.0
        return _fn

    # Lap 1: baseline (Turn 1 finishes at 450m; Turn 2 straight wraps past S/F to 100m on Lap 2)
    t1, s1, exits1, comps1 = _simulate_lap_into_engine(
        engine, 0.0, lap_len, speed_profile(16.0, 18.0)
    )
    assert len(exits1) == 2
    assert all(not e.has_benchmark for e in exits1)
    assert len(comps1) == 1
    assert store.get(0) is not None and store.get(1) is None
    best_t1_lap1 = store.get(0).total_time_s  # type: ignore[union-attr]

    # Lap 2: slower Turn 1 corner + slower exit speed (18 -> 16 m/s)
    # Turn 2 Lap 1 benchmark finishes at 100m, so both Turn 1 and Turn 2 exit with benchmarks!
    t2, s2, exits2, comps2 = _simulate_lap_into_engine(
        engine, t1, lap_len, speed_profile(14.5, 16.0), s_start=s1
    )
    assert store.get(1) is not None
    assert len(exits2) == 2 and all(e.has_benchmark for e in exits2)
    assert len(comps2) == 2
    t1_exit = [e for e in exits2 if e.turn_index == 0][0]
    t1_comp = [c for c in comps2 if c.turn_index == 0][0]
    assert t1_exit.has_benchmark
    assert (t1_exit.corner_delta_s or 0.0) > 0.3
    assert (t1_exit.pred_straight_delta_s or 0.0) > 0.4
    assert (t1_exit.pred_total_delta_s or 0.0) > 0.7
    assert not t1_exit.is_new_best_predicted
    assert t1_comp.actual_total_delta_s is not None
    assert abs((t1_exit.pred_total_delta_s or 0.0) - t1_comp.actual_total_delta_s) < 0.05
    assert not t1_comp.benchmark_updated
    assert math.isclose(store.get(0).total_time_s, best_t1_lap1)  # type: ignore[union-attr]

    # Lap 3: faster Turn 1 corner + faster exit speed -> predicted PB and updates benchmark
    t3, _s3, exits3, comps3 = _simulate_lap_into_engine(
        engine, t2, lap_len, speed_profile(17.5, 19.5), s_start=s2
    )
    t1_exit3 = [e for e in exits3 if e.turn_index == 0][0]
    t1_comp3 = [c for c in comps3 if c.turn_index == 0][0]
    assert t1_exit3.is_new_best_predicted
    assert (t1_exit3.pred_total_delta_s or 0.0) < -0.3
    assert t1_comp3.benchmark_updated
    assert store.get(0).total_time_s < best_t1_lap1  # type: ignore[union-attr]

    # Lap 4: spin/stop mid-Turn 1 (< 1.5 m/s) invalidates active corner pass
    best_before_spin = store.get(0).total_time_s  # type: ignore[union-attr]
    for k in range(25):
        s_pos = 120.0 + float(k) * 0.2
        x, z = _circle_xy_at_dist(s_pos, lap_len)
        spd = 14.0 if k < 5 else 0.8
        engine.process_sample(time_s=t3 + k * 0.05, x=x, z=z, speed_ms=spd)
    x_exit, z_exit = _circle_xy_at_dist(205.0, lap_len)
    ev_spin, _ = engine.process_sample(time_s=t3 + 1.5, x=x_exit, z=z_exit, speed_ms=15.0)
    assert ev_spin == []
    assert math.isclose(store.get(0).total_time_s, best_before_spin)  # type: ignore[union-attr]


def test_stream_server_chunked_rollover_and_burst_suppression() -> None:
    origin = rfactor.get_track_origin('Rowrah')
    lap_len = 1000.0
    track = parse_track_payload(_make_circle_track_payload(origin, lap_length=lap_len))

    fetched_calls: List[Tuple[str, str, str]] = []
    played_cues: List[float] = []

    class _MockTrackLoader(TrackLoader):
        def fetch(
            self,
            server_url: str,
            track_name: str,
            api_key: str = '',
            timeout: float = 5.0,
        ) -> TrackData:
            fetched_calls.append((server_url, track_name, api_key))
            return track

    class _FakeAudio(AudioCuePlayer):
        def play_turn_cue(
            self,
            delta_s: float,
            new_best_threshold_s: Optional[float] = None,
            max_loss_s: Optional[float] = None,
        ) -> None:
            played_cues.append(delta_s)

    controller = LiveFeedbackController(
        server_url='https://example.test',
        track_loader=_MockTrackLoader(),
        audio=_FakeAudio(),
        min_cue_interval_s=0.0,
    )
    server = LiveStreamServer(
        host='127.0.0.1',
        port=0,
        on_header=controller.on_header,
        on_samples=controller.on_samples,
    )
    server.start()
    try:
        channels = [
            {'name': 'Time', 'scale': 0.001},
            {'name': 'x', 'scale': 0.001},
            {'name': 'z', 'scale': 0.001},
            {'name': 'Speed', 'scale': 0.01},
        ]
        scales = [float(ch['scale']) for ch in channels]
        meta = {
            'Track name': 'Rowrah',
            'Date': '2026-11-16',
            'Time': '20:00:00',
            'Driver name': 'Test Driver',
            'League': 'kartsim',
            'Class': 'cadet',
            'Session': 'Practice',
        }

        def send_stream_connection(
            sample_rows: List[List[float]],
            burst_all: bool = False,
        ) -> str:
            with socket.create_connection(('127.0.0.1', server.bound_port), timeout=5.0) as sock:
                req_hdr = (
                    'POST /api/upload/stream HTTP/1.1\r\n'
                    'Host: 127.0.0.1\r\n'
                    'Content-Type: application/octet-stream\r\n'
                    'Transfer-Encoding: chunked\r\n'
                    'X-API-Key: secret_driver_key\r\n'
                    '\r\n'
                ).encode('ascii')
                sock.sendall(req_hdr)
                sock.sendall(_http_chunk(_frame_brt1_chunk(0x01, b'BRT1')))
                sock.sendall(_http_chunk(_encode_brt1_metadata(meta, channels)))

                base = sample_rows[0]
                if burst_all:
                    blob = bytearray(_encode_brt1_baseline(base))
                    blob.extend(_encode_brt1_delta_rows(base, sample_rows[1:], scales))
                    blob.extend(_frame_brt1_chunk(0xFF, b''))
                    sock.sendall(_http_chunk(bytes(blob)))
                else:
                    sock.sendall(_http_chunk(_encode_brt1_baseline(base)))
                    prev = list(base)
                    for row in sample_rows[1:]:
                        chunk = _encode_brt1_delta_rows(prev, [row], scales)
                        # Account for quantized accumulation just like Brt1StreamEncoder
                        for c, val in enumerate(row):
                            q = int(round((val - prev[c]) / scales[c]))
                            prev[c] += q * scales[c]
                        sock.sendall(_http_chunk(chunk))
                        time.sleep(0.0005)
                    sock.sendall(_http_chunk(_frame_brt1_chunk(0xFF, b'')))

                sock.sendall(b'0\r\n\r\n')
                resp_buf = bytearray()
                while True:
                    part = sock.recv(4096)
                    if not part:
                        break
                    resp_buf.extend(part)
                return resp_buf.decode('utf-8', errors='replace')

        def build_lap_rows(t0: float, speed_ms: float) -> Tuple[float, List[List[float]]]:
            rows: List[List[float]] = []
            t = t0
            s = 1.0
            dt = 0.02
            while s < lap_len:
                x, z = _circle_xy_at_dist(s, lap_len)
                rows.append([t, x, z, speed_ms])
                s += speed_ms * dt
                t += dt
            x_w, z_w = _circle_xy_at_dist(s - lap_len, lap_len)
            rows.append([t, x_w, z_w, speed_ms])
            return t + dt, rows

        # Connection 1: Lap 1 baseline (with 1st half) then rollover mid-session!
        t_after_l1, lap1_rows = build_lap_rows(0.0, 20.0)
        split_idx = len(lap1_rows) // 2
        resp1 = send_stream_connection(lap1_rows[:split_idx], burst_all=False)
        assert '200 OK' in resp1
        assert fetched_calls == [('https://example.test', 'Rowrah', 'secret_driver_key')]

        # Connection 2: 60s rollover resumes Lap 1 and completes Lap 2 live -> preserves benchmarks!
        _, lap2_rows = build_lap_rows(t_after_l1, 18.0)
        resp2 = send_stream_connection(lap1_rows[split_idx:] + lap2_rows, burst_all=False)
        assert '200 OK' in resp2
        # Track should only have been fetched once because rollover kept same session key
        assert len(fetched_calls) == 1
        snap = controller.snapshot()
        assert snap.reconnect_count == 1
        assert len(played_cues) == 2
        assert all(delta > 0.5 for delta in played_cues)

        # Replay burst guard test: big single chunk must populate benchmarks silently (0 audio cues)
        played_cues.clear()
        meta_burst = dict(meta)
        meta_burst['Time'] = '21:00:00'
        meta = meta_burst
        t_b1, burst_lap1 = build_lap_rows(0.0, 20.0)
        _, burst_lap2 = build_lap_rows(t_b1, 18.0)
        resp3 = send_stream_connection(burst_lap1 + burst_lap2, burst_all=True)
        assert '200 OK' in resp3
        assert played_cues == []
        assert controller.snapshot().turn_rows[0].best_total_time_s is not None
    finally:
        server.stop()


def test_live_feedback_app_ui_lifecycle_and_visibility_gating(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tk = pytest.importorskip('tkinter')
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('No graphical display available for Tkinter')

    if pygame.mixer.get_init() is None:
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
            pygame.mixer.quit()
        except pygame.error:
            monkeypatch.setenv('SDL_AUDIODRIVER', 'dummy')

    cfg_path = str(tmp_path / 'live_feedback_config.json')
    app_ui = LiveFeedbackApp(
        root,
        port_override=0,
        server_override='https://example.test',
        config_path=cfg_path,
    )
    try:
        root.update()
        assert app_ui._stream_server is not None
        assert app_ui._stream_server.bound_port > 0

        root.withdraw()
        app_ui._window_mapped = False
        app_ui._pause_ui_rendering()
        root.update()
        assert app_ui._ui_timer_id is None
    finally:
        app_ui._on_close()


def test_track_loader_user_agent_and_http_error_detail() -> None:
    captured_reqs: List[urllib.request.Request] = []
    origin = rfactor.get_track_origin('Rowrah')
    sample_payload = _make_circle_track_payload(origin, lap_length=1000.0)
    sample_payload['track_name'] = 'Willow Springs Reverse'

    class _FakeResp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> '_FakeResp':
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_urlopen(req: urllib.request.Request, timeout: float = 5.0) -> _FakeResp:
        captured_reqs.append(req)
        return _FakeResp(json.dumps(sample_payload).encode('utf-8'))

    loader = TrackLoader()
    with patch('live_feedback.track_loader.urllib.request.urlopen', side_effect=fake_urlopen):
        td = loader.fetch(
            'https://brbrdb.brbrkitten.com',
            'Willow Springs Reverse',
            api_key='test_key',
        )
    assert td.track_name == 'Willow Springs Reverse'
    assert len(captured_reqs) == 1
    assert captured_reqs[0].get_header('User-agent') == 'BrBrDbTelemetry/1.0'
    assert captured_reqs[0].get_header('X-api-key') == 'test_key'

    err_loader = TrackLoader()
    http_err = urllib.error.HTTPError(
        url='https://brbrdb.brbrkitten.com/api/track_data',
        code=403,
        msg='Forbidden',
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"title":"Access denied"}'),
    )
    with patch('live_feedback.track_loader.urllib.request.urlopen', side_effect=http_err):
        with pytest.raises(RuntimeError) as exc_info:
            err_loader.fetch(
                'https://brbrdb.brbrkitten.com',
                'Willow Springs Reverse',
                api_key='test_key',
            )
    err_msg = str(exc_info.value)
    assert 'HTTP 403' in err_msg
    assert 'Access denied' in err_msg


def test_willow_springs_reverse_real_track_and_error_rollover() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    track_path = os.path.join(repo_root, 'data', 'tracks', 'willow_springs_reverse.json')
    with open(track_path, 'r', encoding='utf-8') as f:
        raw_track = json.load(f)

    track = parse_track_payload(raw_track, fallback_name='Willow Springs Reverse')
    assert track.track_name == 'Willow Springs Reverse'
    assert len(track.turns) == 6

    origin = resolve_track_origin(
        'kartsim',
        track.track_name,
        'Willow Springs Reverse',
        {},
    )
    assert isinstance(origin, rfactor.RFactorTrackOrigin)

    class _FlakyTrackLoader(TrackLoader):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def fetch(
            self,
            server_url: str,
            track_name: str,
            api_key: str = '',
            timeout: float = 5.0,
        ) -> TrackData:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('HTTP 403 Forbidden: Access denied')
            return track

    class _SilentAudio(AudioCuePlayer):
        def play_turn_cue(
            self,
            delta_s: float,
            new_best_threshold_s: Optional[float] = None,
            max_loss_s: Optional[float] = None,
        ) -> None:
            return None

    flaky_loader = _FlakyTrackLoader()
    controller = LiveFeedbackController(
        server_url='https://example.test',
        track_loader=flaky_loader,
        audio=_SilentAudio(),
    )
    meta = {
        'Track name': 'Willow Springs Reverse',
        'League': 'kartsim',
        'Class': 'KSP_IWC_RE_UK',
        'Driver name': 'Test Driver',
        'Session': 'Practice',
        'Date': '2026-11-16',
        'Time': '20:42:55',
    }
    channels = [
        {'name': 'Time', 'scale': 0.001},
        {'name': 'x', 'scale': 0.001},
        {'name': 'z', 'scale': 0.001},
        {'name': 'Speed', 'scale': 0.01},
    ]

    controller.on_header('sess_1', meta, channels, 'secret_key')
    snap_err = controller.snapshot()
    assert snap_err.track_error is True
    assert 'Access denied' in snap_err.track_status

    controller.on_samples(
        'sess_1',
        [
            DecodedSample(
                time_s=0.01 * i,
                x=0.0,
                z=0.0,
                lat=None,
                lon=None,
                speed_ms=10.0,
                is_live=True,
            )
            for i in range(5)
        ],
    )
    snap_mid = controller.snapshot()
    assert snap_mid.samples_received == 5

    # 60s rollover with same session_key must preserve samples_received and recover!
    controller.on_header('sess_1', meta, channels, 'secret_key')
    snap_ok = controller.snapshot()
    assert snap_ok.track_error is False
    assert snap_ok.samples_received == 5
    assert snap_ok.reconnect_count == 1
    assert len(snap_ok.turn_rows) == 6
    assert 'Loaded Willow Springs Reverse' in snap_ok.track_status


def test_evaluate_session_offline_and_cli(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    origin = rfactor.get_track_origin('Rowrah')
    lap_len = 1000.0
    track_payload = _make_circle_track_payload(origin, lap_length=lap_len)
    tracks_dir = tmp_path / 'tracks'
    tracks_dir.mkdir()
    track_file = tracks_dir / 'rowrah.json'
    track_file.write_text(json.dumps(track_payload), encoding='utf-8')

    channels = [
        {'name': 'Time', 'scale': 0.001},
        {'name': 'x', 'scale': 0.001},
        {'name': 'z', 'scale': 0.001},
        {'name': 'Speed', 'scale': 0.01},
    ]
    scales = [float(ch['scale']) for ch in channels]
    meta = {
        'Track name': 'Rowrah',
        'Date': '2026-11-16',
        'Time': '21:15:00',
        'Driver name': 'Eval Driver',
        'League': 'kartsim',
        'Class': 'cadet',
        'Session': 'Practice',
    }

    def _lap_speed(base_v: float, exit_v: float, accel: float = 1.5) -> Any:
        def _fn(s: float) -> float:
            if 100.0 <= s < 200.0:
                return base_v + ((s - 100.0) / 100.0) * (exit_v - base_v)
            if 200.0 <= s < 450.0:
                return math.sqrt(exit_v ** 2 + 2.0 * accel * (s - 200.0))
            return 22.0
        return _fn

    rows: List[List[float]] = []
    t_cur = 0.0
    s_cur = 1.0
    dt = 0.02
    for base_v, exit_v in ((16.5, 18.5), (14.5, 16.0), (15.5, 17.0)):
        spd_fn = _lap_speed(base_v, exit_v)
        while s_cur < lap_len:
            x, z = _circle_xy_at_dist(s_cur, lap_len)
            v = float(spd_fn(s_cur))
            rows.append([t_cur, x, z, v])
            s_cur += v * dt
            t_cur += dt
        s_cur -= lap_len

    x_end, z_end = _circle_xy_at_dist(s_cur, lap_len)
    rows.append([t_cur, x_end, z_end, 22.0])

    raw_brt1 = bytearray(_frame_brt1_chunk(0x01, b'BRT1'))
    raw_brt1.extend(_encode_brt1_metadata(meta, channels))
    raw_brt1.extend(_encode_brt1_baseline(rows[0]))
    raw_brt1.extend(_encode_brt1_delta_rows(rows[0], rows[1:], scales))
    raw_brt1.extend(_frame_brt1_chunk(0xFF, b''))

    session_file = tmp_path / 'sample_session.brt1.br'
    session_file.write_bytes(brotli.compress(bytes(raw_brt1)))

    report_causal = evaluate_session.evaluate_brt1_file(
        str(session_file),
        tracks_dir=str(tracks_dir),
        mode='causal',
    )
    assert report_causal.track_name == 'Rowrah'
    assert report_causal.laps_count == 4
    assert len(report_causal.records) >= 3
    assert report_causal.clean_stats.mae_ms < 30.0

    report_fixed = evaluate_session.evaluate_brt1_file(
        str(session_file),
        track_json=str(track_file),
        mode='fixed',
    )
    assert len(report_fixed.records) >= 3
    assert report_fixed.clean_stats.mae_ms < 30.0

    rc = evaluate_session.main(
        [str(session_file), '--tracks-dir', str(tracks_dir), '--mode', 'causal']
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert 'sample_session.brt1.br' in out
    assert 'Clean passes' in out
    assert 'Turn 1' in out
