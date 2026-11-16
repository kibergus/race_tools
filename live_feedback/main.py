"""Standalone Live Turn-Exit Audio Feedback Desktop Application."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_feedback.audio import AudioCuePlayer, DEFAULT_MAX_LOSS_S  # noqa: E402
from live_feedback.engine import (  # noqa: E402
    LiveFeedbackEngine,
    TurnCompleteEvent,
    TurnExitEvent,
)
from live_feedback.stream_server import DecodedSample, LiveStreamServer  # noqa: E402
from live_feedback.track_loader import TrackLoader, resolve_track_origin  # noqa: E402


DEFAULT_LISTEN_PORT = 18922
DEFAULT_SERVER_URL = 'https://brbrdb.brbrkitten.com'
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'live_feedback_config.json',
)
logger = logging.getLogger(__name__)

COLOR_BG = '#1E1E2E'
COLOR_PANEL = '#181825'
COLOR_SURFACE = '#313244'
COLOR_TEXT = '#CDD6F4'
COLOR_MUTED = '#A6ADC8'
COLOR_ACCENT = '#89B4FA'
COLOR_GREEN = '#A6E3A1'
COLOR_YELLOW = '#F9E2AF'
COLOR_RED = '#F38BA8'


@dataclass
class TurnUiRow:
    """Latest per-turn metrics displayed in the live feedback table."""

    turn_index: int
    turn_name: str
    best_total_time_s: Optional[float] = None
    last_corner_delta_s: Optional[float] = None
    last_exit_speed_kmh: Optional[float] = None
    ref_exit_speed_kmh: Optional[float] = None
    pred_straight_delta_s: Optional[float] = None
    pred_total_delta_s: Optional[float] = None
    actual_total_delta_s: Optional[float] = None


@dataclass(frozen=True)
class UiSnapshot:
    """Thread-safe snapshot of live controller state for UI rendering."""

    session_active: bool
    driver_name: str
    track_name: str
    league: str
    class_name: str
    lap_num: int
    samples_received: int
    reconnect_count: int
    track_status: str
    track_error: bool
    last_exit_event: Optional[TurnExitEvent]
    turn_rows: List[TurnUiRow]
    last_packet_mono: float = 0.0


def load_app_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load persisted UI settings from JSON, returning defaults if absent."""
    defaults: Dict[str, Any] = {
        'port': DEFAULT_LISTEN_PORT,
        'server_url': DEFAULT_SERVER_URL,
        'api_key': '',
        'volume': 80,
        'muted': False,
        'max_loss_s': DEFAULT_MAX_LOSS_S,
    }
    if not os.path.exists(config_path):
        return defaults
    with open(config_path, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        raise ValueError(f'Config file {config_path!r} must contain a JSON object')
    defaults.update(loaded)
    return defaults


def save_app_config(cfg: Dict[str, Any], config_path: str = DEFAULT_CONFIG_PATH) -> None:
    """Persist UI settings to JSON cleanly."""
    parent = os.path.dirname(os.path.abspath(config_path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


class LiveFeedbackController:
    """Coordinates stream sessions, backend track loading, engine updates, and audio cues."""

    def __init__(
        self,
        server_url: str = DEFAULT_SERVER_URL,
        api_key: str = '',
        track_loader: Optional[TrackLoader] = None,
        audio: Optional[AudioCuePlayer] = None,
        min_cue_interval_s: float = 0.4,
    ) -> None:
        self.server_url = server_url.strip() or DEFAULT_SERVER_URL
        self.api_key = api_key.strip()
        self.track_loader = track_loader or TrackLoader()
        self.audio = audio or AudioCuePlayer()
        self.min_cue_interval_s = float(min_cue_interval_s)

        self._lock = threading.Lock()
        self._active_session_key: Optional[str] = None
        self._active_metadata: Dict[str, Any] = {}
        self._engine: Optional[LiveFeedbackEngine] = None
        self._track_status = 'Waiting for telemetry stream...'
        self._track_error = False
        self._samples_received = 0
        self._reconnect_count = 0
        self._last_exit_event: Optional[TurnExitEvent] = None
        self._turn_rows: Dict[int, TurnUiRow] = {}
        self._last_cue_wall_time = 0.0
        self._last_packet_mono = 0.0

    def set_server_url(self, server_url: str) -> None:
        """Update the backend server URL used for track geometry lookups."""
        cleaned = server_url.strip() or DEFAULT_SERVER_URL
        with self._lock:
            if cleaned != self.server_url:
                self.server_url = cleaned
                self.track_loader.clear_cache()

    def set_api_key(self, api_key: str) -> None:
        """Update the fallback backend API key."""
        with self._lock:
            self.api_key = api_key.strip()

    def reset_benchmarks(self) -> None:
        """Clear all recorded session benchmarks and table rows."""
        with self._lock:
            if self._engine is not None:
                self._engine.reset_session()
                self._turn_rows = {
                    t.index: TurnUiRow(turn_index=t.index, turn_name=t.name)
                    for t in self._engine.track.turns
                }
            else:
                self._turn_rows.clear()
            self._last_exit_event = None

    def on_header(
        self,
        session_key: str,
        metadata: Dict[str, Any],
        channels: List[Dict[str, Any]],
        stream_api_key: str,
    ) -> None:
        """Handle a BRT1 Metadata chunk, preserving state across 60s HTTP rollovers."""
        with self._lock:
            same_session = self._active_session_key == session_key
            self._last_packet_mono = time.monotonic()
            if same_session and self._engine is not None:
                self._reconnect_count += 1
                return
            eff_server = self.server_url
            eff_key = stream_api_key.strip() or self.api_key

        meta_lower = {str(k).strip().lower(): v for k, v in metadata.items()}
        raw_track = str(meta_lower.get('track name', '')).strip()
        league = str(meta_lower.get('league', '')).strip()

        engine: Optional[LiveFeedbackEngine] = None
        rows: Dict[int, TurnUiRow] = {}
        track_error = False
        if not raw_track:
            logger.error('Track name missing in stream metadata.')
            track_status = 'Track name missing in stream metadata.'
            track_error = True
        else:
            try:
                track = self.track_loader.fetch(eff_server, raw_track, api_key=eff_key)
                origin = resolve_track_origin(league, track.track_name, raw_track, metadata)
                ch_names = {
                    str(ch.get('name', '')).strip().lower()
                    for ch in channels
                }
                if origin is None and 'latitude' not in ch_names:
                    raise ValueError(
                        f'No TrackOrigin found for track {raw_track!r} (league {league!r})'
                    )
                engine = LiveFeedbackEngine(
                    track=track,
                    origin=origin,
                    new_best_threshold_s=self.audio.new_best_threshold_s,
                )
                rows = {
                    t.index: TurnUiRow(turn_index=t.index, turn_name=t.name)
                    for t in track.turns
                }
                track_status = (
                    f'Loaded {track.track_name} '
                    f'({len(track.turns)} turns, {track.lap_length:.0f} m)'
                )
            except Exception as exc:
                logger.error('Failed to initialize track %r: %s', raw_track, exc)
                track_status = f'Track load failed ({raw_track}): {exc}'
                track_error = True

        with self._lock:
            same_session = self._active_session_key == session_key
            self._active_session_key = session_key
            self._active_metadata = dict(metadata)
            self._engine = engine
            self._turn_rows = rows
            self._track_status = track_status
            self._track_error = track_error
            self._last_packet_mono = time.monotonic()
            if same_session:
                self._reconnect_count += 1
            else:
                self._samples_received = 0
                self._reconnect_count = 0
                self._last_exit_event = None

    def _apply_exit_event(self, ev: TurnExitEvent) -> None:
        self._last_exit_event = ev
        row = self._turn_rows.get(ev.turn_index)
        if row is None:
            row = TurnUiRow(turn_index=ev.turn_index, turn_name=ev.turn_name)
            self._turn_rows[ev.turn_index] = row
        row.last_corner_delta_s = ev.corner_delta_s
        row.last_exit_speed_kmh = ev.exit_speed_ms * 3.6
        row.ref_exit_speed_kmh = (
            ev.ref_exit_speed_ms * 3.6 if ev.ref_exit_speed_ms is not None else None
        )
        row.pred_straight_delta_s = ev.pred_straight_delta_s
        row.pred_total_delta_s = ev.pred_total_delta_s
        row.actual_total_delta_s = None

    def _apply_complete_event(self, cev: TurnCompleteEvent) -> None:
        row = self._turn_rows.get(cev.turn_index)
        if row is None:
            row = TurnUiRow(turn_index=cev.turn_index, turn_name=cev.turn_name)
            self._turn_rows[cev.turn_index] = row
        row.best_total_time_s = cev.best_total_time_s
        row.actual_total_delta_s = cev.actual_total_delta_s

    def on_samples(self, session_key: str, samples: List[DecodedSample]) -> None:
        """Feed incoming decoded BRT1 samples into the active turn engine."""
        if not samples:
            return

        cues_to_play: List[float] = []
        batch_is_burst = len(samples) > 2

        with self._lock:
            if session_key != self._active_session_key:
                return
            self._last_packet_mono = time.monotonic()
            self._samples_received += len(samples)
            engine = self._engine
            if engine is None:
                return

            now_mono = time.monotonic()
            for s in samples:
                eff_live = bool(s.is_live and not batch_is_burst)
                exit_evs, comp_evs = engine.process_sample(
                    time_s=s.time_s,
                    x=s.x,
                    z=s.z,
                    lat=s.lat,
                    lon=s.lon,
                    speed_ms=s.speed_ms,
                    is_live=eff_live,
                )
                for ev in exit_evs:
                    self._apply_exit_event(ev)
                    if (
                        ev.has_benchmark
                        and ev.is_live
                        and ev.pred_total_delta_s is not None
                        and (now_mono - self._last_cue_wall_time) >= self.min_cue_interval_s
                    ):
                        self._last_cue_wall_time = now_mono
                        cues_to_play.append(ev.pred_total_delta_s)
                for cev in comp_evs:
                    self._apply_complete_event(cev)

        for delta_s in cues_to_play:
            self.audio.play_turn_cue(delta_s)

    def snapshot(self) -> UiSnapshot:
        """Return a thread-safe copy of the current UI state."""
        with self._lock:
            meta_lower = {
                str(k).strip().lower(): v for k, v in self._active_metadata.items()
            }
            driver = str(meta_lower.get('driver name', '')).strip()
            track = str(meta_lower.get('track name', '')).strip()
            league = str(meta_lower.get('league', '')).strip()
            cls = str(meta_lower.get('class', '')).strip()
            lap = self._engine.lap_num if self._engine is not None else 0
            rows = [
                TurnUiRow(
                    turn_index=r.turn_index,
                    turn_name=r.turn_name,
                    best_total_time_s=r.best_total_time_s,
                    last_corner_delta_s=r.last_corner_delta_s,
                    last_exit_speed_kmh=r.last_exit_speed_kmh,
                    ref_exit_speed_kmh=r.ref_exit_speed_kmh,
                    pred_straight_delta_s=r.pred_straight_delta_s,
                    pred_total_delta_s=r.pred_total_delta_s,
                    actual_total_delta_s=r.actual_total_delta_s,
                )
                for r in sorted(self._turn_rows.values(), key=lambda x: x.turn_index)
            ]
            return UiSnapshot(
                session_active=self._active_session_key is not None,
                driver_name=driver,
                track_name=track,
                league=league,
                class_name=cls,
                lap_num=lap,
                samples_received=self._samples_received,
                reconnect_count=self._reconnect_count,
                track_status=self._track_status,
                track_error=self._track_error,
                last_exit_event=self._last_exit_event,
                turn_rows=rows,
                last_packet_mono=self._last_packet_mono,
            )


def _format_signed_delta(val: Optional[float]) -> str:
    if val is None:
        return '—'
    return f'{val:+.3f}s'


def _format_exit_kmh(curr: Optional[float], ref: Optional[float]) -> str:
    if curr is None:
        return '—'
    if ref is None:
        return f'{curr:.1f}'
    return f'{curr:.1f} ({curr - ref:+.1f})'


class LiveFeedbackApp:
    """Tkinter desktop UI for real-time turn-exit audio coaching."""

    UI_INTERVAL_MS = 60

    def __init__(
        self,
        root: tk.Tk,
        port_override: Optional[int] = None,
        server_override: Optional[str] = None,
        config_path: str = DEFAULT_CONFIG_PATH,
    ) -> None:
        self.root = root
        self.root.title('BrBrDb Live Turn Feedback')
        self.root.geometry('980x640')
        self.root.configure(bg=COLOR_BG)

        self._config_path = config_path
        self._cfg = load_app_config(config_path)
        if port_override is not None:
            self._cfg['port'] = int(port_override)
        if server_override is not None:
            self._cfg['server_url'] = server_override.strip()

        self._closing = False
        self._window_mapped = True
        self._ui_ready = False
        self._ui_timer_id: Optional[str] = None

        init_vol = max(0, min(100, int(self._cfg.get('volume', 80))))
        init_muted = bool(self._cfg.get('muted', False))
        init_max_loss = max(0.05, float(self._cfg.get('max_loss_s', DEFAULT_MAX_LOSS_S)))

        self._audio = AudioCuePlayer(
            volume=float(init_vol) / 100.0,
            muted=init_muted,
            max_loss_s=init_max_loss,
        )
        self._audio.init_mixer()
        self._controller = LiveFeedbackController(
            server_url=str(self._cfg.get('server_url', DEFAULT_SERVER_URL)),
            api_key=str(self._cfg.get('api_key', '')),
            audio=self._audio,
        )
        self._stream_server: Optional[LiveStreamServer] = None
        self._bound_listen_port: Optional[int] = None

        self._build_ui(init_vol, init_muted, init_max_loss)
        self._bind_visibility_events()
        self._start_stream_server(
            int(self._cfg.get('port', DEFAULT_LISTEN_PORT)),
            raise_on_error=True,
        )

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._ensure_ui_loop_running()

    def _build_ui(self, init_vol: int, init_muted: bool, init_max_loss: float) -> None:
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            'Treeview',
            background=COLOR_PANEL,
            fieldbackground=COLOR_PANEL,
            foreground=COLOR_TEXT,
            rowheight=26,
            borderwidth=0,
            font=('Segoe UI', 10),
        )
        style.configure(
            'Treeview.Heading',
            background=COLOR_SURFACE,
            foreground=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
        )
        style.map('Treeview', background=[('selected', COLOR_SURFACE)])

        self._port_var = tk.StringVar(value=str(self._cfg.get('port', DEFAULT_LISTEN_PORT)))
        self._server_var = tk.StringVar(
            value=str(self._cfg.get('server_url', DEFAULT_SERVER_URL))
        )
        self._muted_var = tk.BooleanVar(value=init_muted)
        self._volume_var = tk.IntVar(value=init_vol)
        self._max_loss_var = tk.StringVar(value=f'{init_max_loss:.2f}')

        # 1. Connection & Server Configuration Bar
        top_bar = tk.Frame(self.root, bg=COLOR_PANEL, padx=12, pady=10)
        top_bar.pack(fill=tk.X, side=tk.TOP)

        tk.Label(
            top_bar,
            text='Listen Port:',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Entry(
            top_bar,
            textvariable=self._port_var,
            width=7,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            top_bar,
            text='Apply Port',
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            padx=8,
            command=self._on_apply_port,
        ).pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(
            top_bar,
            text='Server URL:',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Entry(
            top_bar,
            textvariable=self._server_var,
            width=34,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            top_bar,
            text='Apply URL',
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            padx=8,
            command=self._on_apply_server,
        ).pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(
            top_bar,
            text='Reset Benchmarks',
            bg=COLOR_SURFACE,
            fg=COLOR_YELLOW,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=10,
            command=self._on_reset_benchmarks,
        ).pack(side=tk.RIGHT)

        # 2. Audio Controls Bar
        audio_bar = tk.Frame(self.root, bg=COLOR_SURFACE, padx=12, pady=8)
        audio_bar.pack(fill=tk.X, side=tk.TOP, pady=(1, 0))

        tk.Checkbutton(
            audio_bar,
            text='Mute',
            variable=self._muted_var,
            command=self._on_audio_changed,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            selectcolor=COLOR_PANEL,
            activebackground=COLOR_SURFACE,
            activeforeground=COLOR_ACCENT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 12))

        tk.Label(
            audio_bar,
            text='Volume:',
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Scale(
            audio_bar,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self._volume_var,
            command=lambda _v: self._on_audio_changed(),
            showvalue=True,
            length=130,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            troughcolor=COLOR_PANEL,
            highlightthickness=0,
        ).pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(
            audio_bar,
            text='Max Loss (s):',
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        sb = tk.Spinbox(
            audio_bar,
            from_=0.10,
            to=2.00,
            increment=0.05,
            textvariable=self._max_loss_var,
            width=6,
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            buttonbackground=COLOR_SURFACE,
            relief=tk.FLAT,
            command=self._on_audio_changed,
        )
        sb.pack(side=tk.LEFT, padx=(0, 14))
        sb.bind('<Return>', lambda _e: self._on_audio_changed())
        sb.bind('<FocusOut>', lambda _e: self._on_audio_changed())

        tk.Button(
            audio_bar,
            text='Test Chirp (PB)',
            bg=COLOR_PANEL,
            fg=COLOR_GREEN,
            relief=tk.FLAT,
            padx=8,
            command=self._audio.play_new_best,
        ).pack(side=tk.RIGHT, padx=(6, 0))

        for label, delta in (
            ('Test +0.50s', 0.50),
            ('Test +0.20s', 0.20),
            ('Test 0.00s', 0.0),
        ):
            tk.Button(
                audio_bar,
                text=label,
                bg=COLOR_PANEL,
                fg=COLOR_TEXT,
                relief=tk.FLAT,
                padx=8,
                command=lambda d=delta: self._audio.play_turn_cue(d),
            ).pack(side=tk.RIGHT, padx=(6, 0))

        # 3. Session & Track Status Strip
        status_bar = tk.Frame(self.root, bg=COLOR_BG, padx=12, pady=8)
        status_bar.pack(fill=tk.X, side=tk.TOP)

        status_row1 = tk.Frame(status_bar, bg=COLOR_BG)
        status_row1.pack(fill=tk.X, side=tk.TOP)

        self._lbl_listen = tk.Label(
            status_row1,
            text='● Listening...',
            bg=COLOR_BG,
            fg=COLOR_ACCENT,
            font=('Segoe UI', 9, 'bold'),
        )
        self._lbl_listen.pack(side=tk.LEFT, padx=(0, 16))

        self._lbl_session = tk.Label(
            status_row1,
            text='No active session',
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9),
        )
        self._lbl_session.pack(side=tk.LEFT)

        status_row2 = tk.Frame(status_bar, bg=COLOR_BG)
        status_row2.pack(fill=tk.X, side=tk.TOP, pady=(4, 0))

        self._lbl_track = tk.Label(
            status_row2,
            text='Waiting for telemetry stream...',
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9),
            anchor='w',
            justify=tk.LEFT,
            wraplength=940,
        )
        self._lbl_track.pack(side=tk.LEFT)

        # 4. Hero Latest Turn-Exit Card
        hero = tk.Frame(self.root, bg=COLOR_PANEL, padx=16, pady=14)
        hero.pack(fill=tk.X, side=tk.TOP, padx=12, pady=(0, 10))

        self._lbl_hero_turn = tk.Label(
            hero,
            text='Last Turn Exit: —',
            bg=COLOR_PANEL,
            fg=COLOR_MUTED,
            font=('Segoe UI', 12, 'bold'),
        )
        self._lbl_hero_turn.pack(side=tk.LEFT)

        self._lbl_hero_delta = tk.Label(
            hero,
            text='Waiting for first completed pass',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 20, 'bold'),
        )
        self._lbl_hero_delta.pack(side=tk.RIGHT)

        self._lbl_hero_detail = tk.Label(
            hero,
            text='',
            bg=COLOR_PANEL,
            fg=COLOR_MUTED,
            font=('Segoe UI', 10),
        )
        self._lbl_hero_detail.pack(side=tk.RIGHT, padx=(0, 22))

        # 5. Per-Turn Table
        table_frame = tk.Frame(self.root, bg=COLOR_BG, padx=12, pady=0)
        table_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=(0, 12))

        columns = (
            'turn',
            'best',
            'corner_dt',
            'exit_kmh',
            'pred_str_dt',
            'pred_tot_dt',
            'actual_tot_dt',
        )
        self._tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show='headings',
            selectmode='none',
        )
        headings = {
            'turn': ('Turn', 170, tk.W),
            'best': ('Best (s)', 95, tk.E),
            'corner_dt': ('Corner Δt', 105, tk.E),
            'exit_kmh': ('Exit km/h (Δ)', 145, tk.E),
            'pred_str_dt': ('Pred. Str Δt', 115, tk.E),
            'pred_tot_dt': ('Pred. Total Δt', 125, tk.E),
            'actual_tot_dt': ('Actual Total Δt', 125, tk.E),
        }
        for col, (title, width, anchor) in headings.items():
            self._tree.heading(col, text=title)
            self._tree.column(col, width=width, anchor=anchor)

        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._ui_ready = True

    def _start_stream_server(self, port: int, raise_on_error: bool = False) -> None:
        if self._stream_server is not None:
            self._stream_server.stop()
            self._stream_server = None
        self._bound_listen_port = None
        try:
            srv = LiveStreamServer(
                host='127.0.0.1',
                port=port,
                on_header=self._controller.on_header,
                on_samples=self._controller.on_samples,
            )
            srv.start()
            self._stream_server = srv
            self._bound_listen_port = srv.bound_port
            self._lbl_listen.configure(
                text=f'● Listening 127.0.0.1:{srv.bound_port} (waiting for simulator)',
                fg=COLOR_ACCENT,
            )
        except OSError as exc:
            self._bound_listen_port = None
            if raise_on_error:
                raise
            self._lbl_listen.configure(
                text=f'▲ Failed to bind port {port}: {exc}',
                fg=COLOR_RED,
            )
            messagebox.showerror('Port Bind Error', f'Failed to listen on port {port}: {exc}')

    def _save_current_config(self) -> None:
        self._cfg['volume'] = int(self._volume_var.get())
        self._cfg['muted'] = bool(self._muted_var.get())
        try:
            self._cfg['max_loss_s'] = round(float(self._max_loss_var.get()), 3)
        except ValueError:
            pass
        self._cfg['server_url'] = self._controller.server_url
        if self._stream_server is not None:
            self._cfg['port'] = self._stream_server.bound_port
        save_app_config(self._cfg, self._config_path)

    def _on_apply_port(self) -> None:
        try:
            new_port = int(self._port_var.get().strip())
            if not (1 <= new_port <= 65535):
                raise ValueError('Port out of range')
        except ValueError:
            messagebox.showerror('Invalid Port', 'Please enter a port between 1 and 65535.')
            return
        self._start_stream_server(new_port)
        self._save_current_config()

    def _on_apply_server(self) -> None:
        url = self._server_var.get().strip()
        if not url.startswith(('http://', 'https://')):
            messagebox.showerror(
                'Invalid URL',
                'Server URL must start with http:// or https://',
            )
            return
        self._controller.set_server_url(url)
        self._save_current_config()

    def _on_audio_changed(self) -> None:
        if not self._ui_ready:
            return
        self._audio.set_muted(bool(self._muted_var.get()))
        self._audio.set_volume(float(self._volume_var.get()) / 100.0)
        try:
            max_loss = float(self._max_loss_var.get())
            self._audio.set_max_loss_s(max_loss)
        except ValueError:
            pass
        self._save_current_config()

    def _on_reset_benchmarks(self) -> None:
        self._controller.reset_benchmarks()
        self._render_snapshot()

    def _bind_visibility_events(self) -> None:
        self.root.bind('<Unmap>', self._on_unmap)
        self.root.bind('<Map>', self._on_map)
        self.root.bind('<Configure>', self._on_configure_check)

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._window_mapped = False
            self._pause_ui_rendering()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._window_mapped = True
            self._ensure_ui_loop_running()

    def _on_configure_check(self, event: tk.Event) -> None:
        if event.widget is self.root:
            if self.is_window_visible():
                self._ensure_ui_loop_running()
            else:
                self._pause_ui_rendering()

    def is_window_visible(self) -> bool:
        """Return True when the window is mapped and not minimized."""
        if self._closing or not self._window_mapped:
            return False
        try:
            if self.root.state() in ('iconic', 'withdrawn'):
                return False
            return bool(self.root.winfo_viewable())
        except tk.TclError:
            return False

    def _pause_ui_rendering(self) -> None:
        if self._ui_timer_id is not None:
            try:
                self.root.after_cancel(self._ui_timer_id)
            except tk.TclError:
                pass
            self._ui_timer_id = None

    def _ensure_ui_loop_running(self) -> None:
        if self._closing or not self.is_window_visible():
            return
        if self._ui_timer_id is None:
            self._ui_timer_id = self.root.after(20, self._ui_tick_loop)

    def _ui_tick_loop(self) -> None:
        self._ui_timer_id = None
        if self._closing or not self.is_window_visible():
            self._pause_ui_rendering()
            return
        self._render_snapshot()
        if not self._closing and self.is_window_visible():
            self._ui_timer_id = self.root.after(self.UI_INTERVAL_MS, self._ui_tick_loop)

    def _render_snapshot(self) -> None:
        snap = self._controller.snapshot()
        if self._stream_server is not None and self._bound_listen_port is not None:
            active_conns = self._stream_server.active_connections
            recently_active = (
                snap.last_packet_mono > 0.0
                and (time.monotonic() - snap.last_packet_mono) < 3.0
            )
            if active_conns > 0 or recently_active:
                self._lbl_listen.configure(
                    text=f'● Connected (127.0.0.1:{self._bound_listen_port})',
                    fg=COLOR_GREEN,
                )
            else:
                self._lbl_listen.configure(
                    text=(
                        f'● Listening 127.0.0.1:{self._bound_listen_port} '
                        '(waiting for simulator)'
                    ),
                    fg=COLOR_ACCENT,
                )

        if snap.session_active:
            cls_part = f' / {snap.class_name}' if snap.class_name else ''
            self._lbl_session.configure(
                text=(
                    f'{snap.driver_name} — {snap.track_name} '
                    f'({snap.league}{cls_part}) | Lap {snap.lap_num} | '
                    f'Samples: {snap.samples_received}'
                ),
                fg=COLOR_TEXT,
            )
        else:
            self._lbl_session.configure(
                text='No active session (waiting for simulator stream)',
                fg=COLOR_MUTED,
            )

        self._lbl_track.configure(
            text=snap.track_status,
            fg=COLOR_RED if snap.track_error else COLOR_MUTED,
        )

        ev = snap.last_exit_event
        if snap.track_error:
            self._lbl_hero_turn.configure(text='Track Load Failed', fg=COLOR_RED)
            self._lbl_hero_detail.configure(text=snap.track_status, fg=COLOR_RED)
            self._lbl_hero_delta.configure(text='ERROR', fg=COLOR_RED)
        elif ev is None:
            self._lbl_hero_turn.configure(text='Last Turn Exit: —', fg=COLOR_MUTED)
            self._lbl_hero_detail.configure(text='', fg=COLOR_MUTED)
            self._lbl_hero_delta.configure(text='Waiting for turn exit', fg=COLOR_TEXT)
        else:
            self._lbl_hero_turn.configure(
                text=f'Lap {ev.lap_num} — {ev.turn_name}',
                fg=COLOR_ACCENT,
            )
            exit_kmh = ev.exit_speed_ms * 3.6
            if not ev.has_benchmark:
                self._lbl_hero_detail.configure(
                    text=f'Corner: {ev.corner_time_s:.3f}s | Exit: {exit_kmh:.1f} km/h',
                    fg=COLOR_MUTED,
                )
                self._lbl_hero_delta.configure(text='Baseline Pass', fg=COLOR_MUTED)
            else:
                ref_kmh = (ev.ref_exit_speed_ms or 0.0) * 3.6
                dv = exit_kmh - ref_kmh
                self._lbl_hero_detail.configure(
                    text=(
                        f'Corner: {_format_signed_delta(ev.corner_delta_s)} | '
                        f'Exit: {exit_kmh:.1f} km/h ({dv:+.1f}) | '
                        f'Pred. Str: {_format_signed_delta(ev.pred_straight_delta_s)}'
                    ),
                    fg=COLOR_MUTED,
                )
                tot = ev.pred_total_delta_s or 0.0
                if ev.is_new_best_predicted:
                    delta_color = COLOR_GREEN
                    prefix = 'NEW BEST '
                elif tot <= 0.10:
                    delta_color = COLOR_ACCENT
                    prefix = ''
                elif tot <= 0.25:
                    delta_color = COLOR_YELLOW
                    prefix = ''
                else:
                    delta_color = COLOR_RED
                    prefix = ''
                self._lbl_hero_delta.configure(
                    text=f'{prefix}{tot:+.3f}s',
                    fg=delta_color,
                )

        existing = set(self._tree.get_children())
        desired = [str(r.turn_index) for r in snap.turn_rows]
        if list(self._tree.get_children()) != desired:
            for iid in existing:
                self._tree.delete(iid)
            for row in snap.turn_rows:
                self._tree.insert('', tk.END, iid=str(row.turn_index))

        for row in snap.turn_rows:
            iid = str(row.turn_index)
            vals = (
                row.turn_name,
                f'{row.best_total_time_s:.3f}' if row.best_total_time_s is not None else '—',
                _format_signed_delta(row.last_corner_delta_s),
                _format_exit_kmh(row.last_exit_speed_kmh, row.ref_exit_speed_kmh),
                _format_signed_delta(row.pred_straight_delta_s),
                _format_signed_delta(row.pred_total_delta_s),
                _format_signed_delta(row.actual_total_delta_s),
            )
            self._tree.item(iid, values=vals)

    def _on_close(self) -> None:
        self._closing = True
        self._pause_ui_rendering()
        self._save_current_config()
        if self._stream_server is not None:
            self._stream_server.stop()
            self._stream_server = None
        self._audio.stop()
        self.root.destroy()


def main() -> None:
    """CLI entry point for the Live Turn Feedback desktop application."""
    parser = argparse.ArgumentParser(
        description='BrBrDb Real-Time Turn-Exit Audio Feedback App',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help=f'Local HTTP listen port for BRT1 stream (default: {DEFAULT_LISTEN_PORT})',
    )
    parser.add_argument(
        '--server',
        type=str,
        default=None,
        help=f'Backend server base URL (default: {DEFAULT_SERVER_URL})',
    )
    args = parser.parse_args()

    root = tk.Tk()
    LiveFeedbackApp(
        root,
        port_override=args.port,
        server_override=args.server,
    )
    root.mainloop()


if __name__ == '__main__':
    main()
