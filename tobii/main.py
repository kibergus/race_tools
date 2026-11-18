"""Direct-USB Tobii Eye Tracker 5 Telemetry Studio & Dual-Stream BRT1 AUX Uploader."""

import argparse
import ctypes
from dataclasses import dataclass
import math
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import tkinter as tk
from tkinter import messagebox, ttk

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tobii.calibration import (  # noqa: E402
    CalibrationObservation,
    DEFAULT_CALIBRATION_TARGETS,
    DisplayAreaProfile,
    WIDE_COLLECTION_PLANE,
    aggregate_target_samples,
    solve_display_area_3d,
)
from tobii.device_client import TobiiDirectClient, UiTelemetrySnapshot  # noqa: E402
from tobii.uploader import (  # noqa: E402
    TobiiAuxSessionManager,
    UploadSinkConfig,
    parse_sink_spec,
)
from tobii.usb_transport import (  # noqa: E402
    BaseUsbTransport,
    UsbAccessDeniedError,
    get_windows_tobii_services_running,
    is_tobii_usb_present,
    toggle_windows_tobii_services_elevated,
)

try:
    import screeninfo
except ImportError:  # pragma: no cover
    screeninfo = None  # type: ignore

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageTk = None  # type: ignore


COLOR_BG = '#1E1E2E'
COLOR_PANEL = '#181825'
COLOR_SURFACE = '#313244'
COLOR_GRID = '#26273A'
COLOR_TEXT = '#CDD6F4'
COLOR_MUTED = '#A6ADC8'
COLOR_ACCENT = '#89B4FA'
COLOR_GREEN = '#A6E3A1'
COLOR_YELLOW = '#F9E2AF'
COLOR_RED = '#F38BA8'
COLOR_CYAN = '#94E2D5'
COLOR_MAUVE = '#CBA6F7'


def build_strip_polyline(
    times: Sequence[float],
    values: Sequence[float],
    t_min: float,
    t_span: float,
    x_left: float,
    width_px: float,
    y_top: float,
    height_px: float,
    v_min: float,
    v_max: float,
) -> List[List[float]]:
    """Build NaN-segmented polyline coordinate lists `[[x0, y0, x1, y1, ...], ...]`."""
    if len(times) < 2 or width_px < 8.0 or height_px < 8.0 or t_span <= 0.0:
        return []

    segments: List[List[float]] = []
    cur_seg: List[float] = []
    v_span = max(1e-6, v_max - v_min)

    for t_val, v_val in zip(times, values):
        if math.isnan(v_val):
            if len(cur_seg) >= 4:
                segments.append(cur_seg)
            cur_seg = []
            continue
        norm_x = max(0.0, min(1.0, (t_val - t_min) / t_span))
        norm_y = max(0.0, min(1.0, (v_val - v_min) / v_span))
        px = x_left + norm_x * width_px
        py = y_top + (1.0 - norm_y) * height_px
        cur_seg.append(px)
        cur_seg.append(py)

    if len(cur_seg) >= 4:
        segments.append(cur_seg)
    return segments


@dataclass
class _MonitorDescriptor:
    index: int
    x: int
    y: int
    width: int
    height: int
    edid_diagonal_in: Optional[float]
    is_primary: bool
    name: str = ''

    @property
    def label(self) -> str:
        prim = ' [Primary]' if self.is_primary else ''
        diag = f' (~{self.edid_diagonal_in:.0f}")' if self.edid_diagonal_in else ''
        disp_name = f'{self.name} — ' if self.name else ''
        return (
            f'Monitor {self.index + 1}: {disp_name}{self.width}x{self.height} '
            f'at ({self.x}, {self.y}){diag}{prim}'
        )


def _enable_windows_dpi_awareness() -> None:
    """Enable Per-Monitor V2 DPI awareness on Windows so monitors & Tk use true native pixels."""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def _query_win32_friendly_monitor_names() -> Dict[str, str]:
    """Map GDI device names (`\\\\.\\DISPLAY5`) to EDID friendly names (`SAMSUNG`, etc.)."""
    names: Dict[str, str] = {}
    if sys.platform != 'win32':
        return names
    try:
        from ctypes import wintypes as wt

        user32 = ctypes.windll.user32
        qdc_only_active = 0x00000002
        info_get_source = 1
        info_get_target = 2
        tech_internal = 0x80000000
        tech_embedded = 11

        class _Luid(ctypes.Structure):
            _fields_ = [('LowPart', wt.DWORD), ('HighPart', wt.LONG)]

        class _PathSource(ctypes.Structure):
            _fields_ = [
                ('adapterId', _Luid),
                ('id', wt.UINT),
                ('modeInfoIdx', wt.UINT),
                ('statusFlags', wt.UINT),
            ]

        class _Rational(ctypes.Structure):
            _fields_ = [('Numerator', wt.UINT), ('Denominator', wt.UINT)]

        class _PathTarget(ctypes.Structure):
            _fields_ = [
                ('adapterId', _Luid),
                ('id', wt.UINT),
                ('modeInfoIdx', wt.UINT),
                ('outputTechnology', wt.UINT),
                ('rotation', wt.UINT),
                ('scaling', wt.UINT),
                ('refreshRate', _Rational),
                ('scanLineOrdering', wt.UINT),
                ('targetAvailable', wt.BOOL),
                ('statusFlags', wt.UINT),
            ]

        class _PathInfo(ctypes.Structure):
            _fields_ = [
                ('sourceInfo', _PathSource),
                ('targetInfo', _PathTarget),
                ('flags', wt.UINT),
            ]

        class _ModeInfo(ctypes.Structure):
            _fields_ = [('data', ctypes.c_byte * 64)]

        class _InfoHeader(ctypes.Structure):
            _fields_ = [
                ('type', wt.UINT),
                ('size', wt.UINT),
                ('adapterId', _Luid),
                ('id', wt.UINT),
            ]

        class _SourceName(ctypes.Structure):
            _fields_ = [('header', _InfoHeader), ('viewGdiDeviceName', wt.WCHAR * 32)]

        class _TargetName(ctypes.Structure):
            _fields_ = [
                ('header', _InfoHeader),
                ('flags', wt.UINT),
                ('outputTechnology', wt.UINT),
                ('edidManufactureId', wt.USHORT),
                ('edidProductCodeId', wt.USHORT),
                ('connectorInstance', wt.UINT),
                ('monitorFriendlyDeviceName', wt.WCHAR * 64),
                ('monitorDevicePath', wt.WCHAR * 128),
            ]

        num_p = wt.UINT(0)
        num_m = wt.UINT(0)
        if user32.GetDisplayConfigBufferSizes(
            qdc_only_active, ctypes.byref(num_p), ctypes.byref(num_m)
        ) != 0:
            return names
        paths = (_PathInfo * num_p.value)()
        modes = (_ModeInfo * num_m.value)()
        if user32.QueryDisplayConfig(
            qdc_only_active, ctypes.byref(num_p), paths, ctypes.byref(num_m), modes, None
        ) != 0:
            return names

        for path in paths:
            src = _SourceName()
            src.header.type = info_get_source
            src.header.size = ctypes.sizeof(src)
            src.header.adapterId = path.sourceInfo.adapterId
            src.header.id = path.sourceInfo.id

            tgt = _TargetName()
            tgt.header.type = info_get_target
            tgt.header.size = ctypes.sizeof(tgt)
            tgt.header.adapterId = path.targetInfo.adapterId
            tgt.header.id = path.targetInfo.id

            if user32.DisplayConfigGetDeviceInfo(ctypes.byref(src)) == 0:
                friendly = ''
                if user32.DisplayConfigGetDeviceInfo(ctypes.byref(tgt)) == 0:
                    friendly = tgt.monitorFriendlyDeviceName.strip()
                    if not friendly and (
                        tgt.outputTechnology in (tech_internal, tech_embedded)
                        or (tgt.outputTechnology & tech_internal) != 0
                    ):
                        friendly = 'Internal Display'
                if friendly:
                    names[src.viewGdiDeviceName] = friendly
    except Exception:
        pass
    return names


def _enumerate_windows_monitors_win32() -> List[_MonitorDescriptor]:
    """Enumerate all active monitors on Windows via native `user32` / `gdi32` (no extra deps)."""
    out: List[_MonitorDescriptor] = []
    if sys.platform != 'win32':
        return out
    try:
        from ctypes import wintypes as wt

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        friendly_map = _query_win32_friendly_monitor_names()

        class _MonitorInfoExW(ctypes.Structure):
            _fields_ = [
                ('cbSize', wt.DWORD),
                ('rcMonitor', wt.RECT),
                ('rcWork', wt.RECT),
                ('dwFlags', wt.DWORD),
                ('szDevice', wt.WCHAR * 32),
            ]

        enum_proc_t = ctypes.WINFUNCTYPE(
            wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
        )
        raw_items: List[Tuple[bool, int, int, int, int, Optional[float], str]] = []

        def _cb(hmon: int, _hdc: int, _lprect: Any, _lparam: int) -> bool:
            mi = _MonitorInfoExW()
            mi.cbSize = ctypes.sizeof(_MonitorInfoExW)
            if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return True
            r = mi.rcMonitor
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            if w < 200 or h < 200:
                return True

            edid_diag: Optional[float] = None
            dc = gdi32.CreateDCW(None, mi.szDevice, None, None)
            if dc:
                try:
                    w_mm = int(gdi32.GetDeviceCaps(dc, 4))  # HORZSIZE (mm)
                    h_mm = int(gdi32.GetDeviceCaps(dc, 6))  # VERTSIZE (mm)
                    if w_mm > 80 and h_mm > 60:
                        diag_in = math.hypot(float(w_mm), float(h_mm)) / 25.4
                        if 8.0 <= diag_in <= 120.0:
                            edid_diag = diag_in
                finally:
                    gdi32.DeleteDC(dc)

            is_prim = bool(mi.dwFlags & 0x00000001)
            short_dev = mi.szDevice.replace('\\\\.\\', '')
            disp_name = friendly_map.get(mi.szDevice, short_dev)
            raw_items.append((is_prim, int(r.left), int(r.top), w, h, edid_diag, disp_name))
            return True

        user32.EnumDisplayMonitors(None, None, enum_proc_t(_cb), 0)
        # Put Primary first, then order left-to-right
        raw_items.sort(key=lambda item: (0 if item[0] else 1, item[1], item[2]))
        for idx, (is_prim, x, y, w, h, edid_diag, disp_name) in enumerate(raw_items):
            out.append(
                _MonitorDescriptor(
                    index=idx,
                    x=x,
                    y=y,
                    width=w,
                    height=h,
                    edid_diagonal_in=edid_diag,
                    is_primary=is_prim,
                    name=disp_name,
                )
            )
    except Exception:
        out.clear()
    return out


def _discover_monitors(root: tk.Tk) -> List[_MonitorDescriptor]:
    if sys.platform == 'win32':
        win_mons = _enumerate_windows_monitors_win32()
        if win_mons:
            return win_mons

    out: List[_MonitorDescriptor] = []
    if screeninfo is not None:
        try:
            for idx, mon in enumerate(screeninfo.get_monitors()):
                edid_diag: Optional[float] = None
                w_mm = getattr(mon, 'width_mm', None)
                h_mm = getattr(mon, 'height_mm', None)
                if isinstance(w_mm, (int, float)) and isinstance(h_mm, (int, float)):
                    if w_mm > 80 and h_mm > 60:
                        diag_in = math.hypot(float(w_mm), float(h_mm)) / 25.4
                        if 8.0 <= diag_in <= 120.0:
                            edid_diag = diag_in
                is_prim = bool(getattr(mon, 'is_primary', idx == 0))
                mon_name = str(getattr(mon, 'name', '') or '').strip()
                out.append(
                    _MonitorDescriptor(
                        index=idx,
                        x=int(mon.x),
                        y=int(mon.y),
                        width=max(200, int(mon.width)),
                        height=max(200, int(mon.height)),
                        edid_diagonal_in=edid_diag,
                        is_primary=is_prim,
                        name=mon_name,
                    )
                )
        except Exception:
            out.clear()

    if not out:
        out.append(
            _MonitorDescriptor(
                index=0,
                x=0,
                y=0,
                width=max(200, int(root.winfo_screenwidth())),
                height=max(200, int(root.winfo_screenheight())),
                edid_diagonal_in=None,
                is_primary=True,
            )
        )
    return out


def _default_diagonal_str(
    monitor: _MonitorDescriptor,
    saved_profile: Optional[DisplayAreaProfile] = None,
) -> str:
    """Return EDID diagonal string when available, else saved monitor diagonal or `'32.0'`."""
    if monitor.edid_diagonal_in is not None:
        return f'{monitor.edid_diagonal_in:.1f}'
    if saved_profile is not None and saved_profile.monitor_index == monitor.index:
        return f'{saved_profile.screen_diagonal_in:.1f}'
    return '32.0'


class _FullscreenCalibrationWindow:
    """Borderless fullscreen 9-point 3D display-area calibration & live verification overlay."""

    TICK_MS = 25
    TRAVEL_SEC = 0.42
    SETTLE_SEC = 0.75
    COLLECT_SEC = 0.85
    MAX_RETRIES_PER_DOT = 3

    def __init__(
        self,
        parent: tk.Tk,
        client: TobiiDirectClient,
        monitors: Sequence[_MonitorDescriptor],
        initial_monitor_index: int,
        on_finished: Callable[[Optional[DisplayAreaProfile]], None],
    ) -> None:
        self._parent = parent
        self._client = client
        self._monitors = list(monitors)
        if not self._monitors:
            self._monitors = [
                _MonitorDescriptor(
                    index=0,
                    x=0,
                    y=0,
                    width=max(200, int(parent.winfo_screenwidth())),
                    height=max(200, int(parent.winfo_screenheight())),
                    edid_diagonal_in=None,
                    is_primary=True,
                )
            ]
        clamped_idx = max(0, min(int(initial_monitor_index), len(self._monitors) - 1))
        self._monitor = self._monitors[clamped_idx]
        self._on_finished = on_finished

        self._closed = False
        self._timer_id: Optional[str] = None
        self._targets = list(DEFAULT_CALIBRATION_TARGETS)
        self._observations: List[CalibrationObservation] = []
        self._solved_profile: Optional[DisplayAreaProfile] = client.active_calibration_profile
        self._just_calibrated = False
        self._collection_plane_active = False

        init_diag_str = _default_diagonal_str(self._monitor, self._solved_profile)
        self._diagonal_inches = float(init_diag_str)

        self._phase = 'verify'
        self._phase_start = time.monotonic()
        self._eyes_seen_since: Optional[float] = None
        self._target_idx = 0
        self._dot_retries = 0
        self._prev_uv: Tuple[float, float] = self._targets[0]
        self._cur_uv: Tuple[float, float] = self._targets[0]
        self._status_hint = ''
        self._last_verify_banner: Tuple[str, str] = ('', '')

        self._win = tk.Toplevel(parent)
        self._win.title('Tobii Eye Tracker 5 — Screen Calibration')
        self._win.configure(bg='#0B0B12', cursor='')
        self._win.overrideredirect(True)
        self._win.geometry(
            f'{self._monitor.width}x{self._monitor.height}+{self._monitor.x}+{self._monitor.y}'
        )
        self._win.attributes('-topmost', True)
        self._win.lift()
        self._win.focus_force()

        self._canvas = tk.Canvas(self._win, bg='#0B0B12', highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind('<Button-1>', self._on_canvas_click)

        self._verify_bar = tk.Frame(self._win, bg=COLOR_PANEL, padx=14, pady=6)
        self._lbl_verify_status = tk.Label(
            self._verify_bar,
            text='',
            bg=COLOR_PANEL,
            fg=COLOR_GREEN,
            font=('Segoe UI', 10, 'bold'),
        )
        self._lbl_verify_status.pack(side=tk.LEFT, padx=(0, 12))

        ctrl_right = tk.Frame(self._verify_bar, bg=COLOR_PANEL)
        ctrl_right.pack(side=tk.RIGHT)

        tk.Label(
            ctrl_right,
            text='Screen:',
            bg=COLOR_PANEL,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        mon_labels = [m.label for m in self._monitors]
        self._mon_var = tk.StringVar(value=mon_labels[clamped_idx])
        self._cb_monitor = ttk.Combobox(
            ctrl_right,
            textvariable=self._mon_var,
            values=mon_labels,
            width=38,
            state='readonly',
        )
        self._cb_monitor.pack(side=tk.LEFT, padx=(0, 10))
        self._cb_monitor.bind('<<ComboboxSelected>>', self._on_monitor_selected)

        tk.Label(
            ctrl_right,
            text='Size (in):',
            bg=COLOR_PANEL,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._diag_var = tk.StringVar(value=init_diag_str)
        self._ent_diag = tk.Entry(
            ctrl_right,
            textvariable=self._diag_var,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            relief=tk.FLAT,
            font=('Consolas', 10),
            width=6,
        )
        self._ent_diag.pack(side=tk.LEFT, padx=(0, 12))
        self._ent_diag.bind('<Button-1>', lambda _e: self._ent_diag.focus_force(), add='+')

        self._btn_recalibrate = tk.Button(
            ctrl_right,
            text='↻ Recalibrate (R)',
            bg=COLOR_ACCENT,
            fg=COLOR_PANEL,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=10,
            pady=2,
            command=self._start_recalibration,
        )
        self._btn_recalibrate.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_done = tk.Button(
            ctrl_right,
            text='✓ Done (Esc)',
            bg=COLOR_GREEN,
            fg=COLOR_PANEL,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=10,
            pady=2,
            command=self._finish_verified,
        )
        self._btn_done.pack(side=tk.LEFT)

        self._show_verify_bar()

        self._parent_binds: List[Tuple[str, str]] = []
        for seq, handler in (
            ('<Escape>', self._on_escape),
            ('<Return>', self._on_confirm_key),
            ('<space>', self._on_confirm_key),
            ('r', self._on_restart_key),
            ('R', self._on_restart_key),
        ):
            self._win.bind(seq, handler)
            func_id = self._parent.bind(seq, handler, add='+')
            if func_id:
                self._parent_binds.append((seq, func_id))
        self._win.protocol('WM_DELETE_WINDOW', self._cancel)
        self._canvas.focus_set()

        self._schedule_next()

    def _show_verify_bar(self) -> None:
        self._verify_bar.place(x=0, y=0, relwidth=1.0)
        self._verify_bar.lift()

    def _hide_verify_bar(self) -> None:
        self._verify_bar.place_forget()

    def _on_monitor_selected(self, _event: Optional[tk.Event] = None) -> None:
        sel = self._cb_monitor.current()
        if 0 <= sel < len(self._monitors):
            self._monitor = self._monitors[sel]
            self._diag_var.set(_default_diagonal_str(self._monitor, self._solved_profile))
            self._win.geometry(
                f'{self._monitor.width}x{self._monitor.height}+{self._monitor.x}+{self._monitor.y}'
            )
            self._win.lift()
            self._canvas.focus_set()

    def _schedule_next(self) -> None:
        if not self._closed:
            self._timer_id = self._win.after(self.TICK_MS, self._tick)

    def _on_escape(self, _event: Optional[tk.Event] = None) -> None:
        if self._phase == 'verify':
            self._finish_verified()
        else:
            self._abort_recalibration_to_verify()

    def _on_confirm_key(self, event: Optional[tk.Event] = None) -> None:
        if event is not None and event.widget in (self._ent_diag, self._cb_monitor):
            if event.widget is self._ent_diag and getattr(event, 'keysym', '') == 'Return':
                self._canvas.focus_set()
            return
        if self._phase == 'verify':
            self._finish_verified()

    def _on_canvas_click(self, _event: tk.Event) -> None:
        self._canvas.focus_set()
        self._win.focus_force()

    def _on_restart_key(self, event: Optional[tk.Event] = None) -> None:
        if event is not None and event.widget in (self._ent_diag, self._cb_monitor):
            return
        if self._phase == 'verify':
            self._start_recalibration()

    def _start_recalibration(self) -> None:
        if self._closed or self._phase != 'verify':
            return
        sel = self._cb_monitor.current()
        if 0 <= sel < len(self._monitors) and self._monitors[sel] is not self._monitor:
            self._monitor = self._monitors[sel]
            self._win.geometry(
                f'{self._monitor.width}x{self._monitor.height}+{self._monitor.x}+{self._monitor.y}'
            )
        try:
            diag_val = float(self._diag_var.get().strip())
            if not (8.0 <= diag_val <= 150.0):
                raise ValueError('out of range')
        except ValueError:
            messagebox.showerror(
                'Invalid Screen Size',
                'Please enter your TV / monitor diagonal in inches (between 8 and 150).',
                parent=self._win,
            )
            self._ent_diag.focus_force()
            return
        try:
            self._client.apply_temporary_display_area(WIDE_COLLECTION_PLANE)
        except Exception as exc:
            messagebox.showerror(
                'Calibration Error',
                f'Failed to enter calibration mode:\n{exc}',
                parent=self._win,
            )
            return

        self._diagonal_inches = diag_val
        self._collection_plane_active = True
        self._hide_verify_bar()
        self._win.configure(cursor='none')
        self._canvas.focus_set()
        self._win.focus_force()

        self._observations.clear()
        self._target_idx = 0
        self._dot_retries = 0
        self._prev_uv = self._targets[0]
        self._cur_uv = self._targets[0]
        self._eyes_seen_since = None
        self._status_hint = ''
        self._phase = 'ready_wait'
        self._phase_start = time.monotonic()

    def _abort_recalibration_to_verify(self) -> None:
        if self._closed:
            return
        if self._collection_plane_active:
            try:
                self._client.restore_active_or_original_display_area()
            except Exception:
                pass
            self._collection_plane_active = False
        self._observations.clear()
        self._status_hint = ''
        self._phase = 'verify'
        self._win.configure(cursor='')
        self._show_verify_bar()
        self._canvas.focus_set()

    def _cancel(self) -> None:
        if self._closed:
            return
        was_collecting = self._collection_plane_active
        self._collection_plane_active = False
        self._close_internal()
        if was_collecting:
            try:
                self._client.restore_active_or_original_display_area()
            except Exception:
                pass
        self._on_finished(None)

    def _finish_verified(self) -> None:
        if self._closed:
            return
        prof = self._solved_profile
        self._close_internal()
        self._on_finished(prof)

    def _close_internal(self) -> None:
        self._closed = True
        for seq, func_id in getattr(self, '_parent_binds', []):
            try:
                self._parent.unbind(seq, func_id)
            except tk.TclError:
                pass
        if hasattr(self, '_parent_binds'):
            self._parent_binds.clear()
        if self._timer_id is not None:
            try:
                self._win.after_cancel(self._timer_id)
            except tk.TclError:
                pass
            self._timer_id = None
        try:
            self._win.destroy()
        except tk.TclError:
            pass

    def _tick(self) -> None:
        self._timer_id = None
        if self._closed:
            return
        if not self._client.is_running:
            self._cancel()
            return

        w = max(200, self._canvas.winfo_width())
        h = max(200, self._canvas.winfo_height())
        now = time.monotonic()
        snap = self._client.get_ui_snapshot()
        latest = snap.latest_gaze
        recent = self._client.get_recent_gaze_samples(now - 0.35)
        if not recent and latest is not None:
            recent = [latest]
        eyes_ok = any(
            s.gaze_valid
            or s.gaze_unfiltered_valid
            or s.gaze_l_valid
            or s.gaze_r_valid
            or s.eye_present_l
            or s.eye_present_r
            or s.validity_l
            or s.validity_r
            for s in recent
        )

        if self._phase == 'ready_wait':
            if eyes_ok:
                if self._eyes_seen_since is None:
                    self._eyes_seen_since = now
                elif (now - self._eyes_seen_since) >= 0.9:
                    self._phase = 'settle'
                    self._phase_start = now
            else:
                self._eyes_seen_since = None
            self._draw_collection_screen(w, h, now, eyes_ok)
            self._schedule_next()
            return

        if self._phase == 'travel':
            dt = now - self._phase_start
            if dt >= self.TRAVEL_SEC:
                self._phase = 'settle'
                self._phase_start = now
            self._draw_collection_screen(w, h, now, eyes_ok)
            self._schedule_next()
            return

        if self._phase == 'settle':
            dt = now - self._phase_start
            if dt >= self.SETTLE_SEC:
                self._phase = 'collect'
                self._phase_start = now
            self._draw_collection_screen(w, h, now, eyes_ok)
            self._schedule_next()
            return

        if self._phase == 'collect':
            dt = now - self._phase_start
            if dt >= self.COLLECT_SEC:
                samples = self._client.get_recent_gaze_samples(self._phase_start)
                u_t, v_t = self._targets[self._target_idx]
                obs = aggregate_target_samples(
                    u_t, v_t, samples, ref_plane=WIDE_COLLECTION_PLANE, min_valid_samples=12
                )
                if obs is None and self._dot_retries < self.MAX_RETRIES_PER_DOT:
                    self._dot_retries += 1
                    self._status_hint = 'Blink / gaze lost — hold eyes steady on the dot...'
                    self._phase = 'settle'
                    self._phase_start = now
                else:
                    if obs is not None:
                        self._observations.append(obs)
                    self._status_hint = ''
                    self._dot_retries = 0
                    self._prev_uv = self._targets[self._target_idx]
                    self._target_idx += 1
                    if self._target_idx >= len(self._targets):
                        self._complete_and_enter_verify(w, h)
                        if not self._closed:
                            self._schedule_next()
                        return
                    self._cur_uv = self._targets[self._target_idx]
                    self._phase = 'travel'
                    self._phase_start = now
            self._draw_collection_screen(w, h, now, eyes_ok)
            self._schedule_next()
            return

        if self._phase == 'verify':
            self._draw_verify_screen(w, h, latest)
            self._schedule_next()

    def _complete_and_enter_verify(self, canvas_w: int, canvas_h: int) -> None:
        try:
            profile = solve_display_area_3d(
                self._observations,
                screen_diagonal_in=self._diagonal_inches,
                screen_width_px=canvas_w,
                screen_height_px=canvas_h,
                monitor_index=self._monitor.index,
            )
            self._client.save_and_apply_calibration(profile)
            self._collection_plane_active = False
            self._solved_profile = profile
            self._just_calibrated = True
            self._phase = 'verify'
            self._phase_start = time.monotonic()
            self._win.configure(cursor='')
            self._show_verify_bar()
        except Exception as exc:
            if self._collection_plane_active:
                try:
                    self._client.restore_active_or_original_display_area()
                except Exception:
                    pass
                self._collection_plane_active = False
            self._observations.clear()
            self._status_hint = ''
            self._phase = 'verify'
            self._win.configure(cursor='')
            self._show_verify_bar()
            messagebox.showerror(
                'Calibration Failed',
                f'Could not compute 3D screen calibration:\n{exc}\n\n'
                'Ensure the tracker has a clear view of your eyes and try again.',
                parent=self._win,
            )

    def _draw_collection_screen(
        self,
        w: int,
        h: int,
        now: float,
        eyes_ok: bool,
    ) -> None:
        c = self._canvas
        c.delete('all')

        idx_str = f'Target {min(self._target_idx + 1, len(self._targets))} / {len(self._targets)}'
        if self._phase == 'ready_wait':
            if eyes_ok:
                banner = '● Eyes Detected — Look at the center dot...'
                b_col = COLOR_GREEN
            else:
                banner = '⚠ Eyes not detected in trackbox — adjust seating or tracker tilt (Esc to cancel)'
                b_col = COLOR_YELLOW
        elif self._status_hint:
            banner = self._status_hint
            b_col = COLOR_YELLOW
        else:
            banner = f'{idx_str}  •  Keep head comfortable and follow the dot  (Esc to cancel)'
            b_col = COLOR_MUTED

        c.create_text(
            w * 0.5,
            34,
            text=banner,
            fill=b_col,
            font=('Segoe UI', 12, 'bold'),
        )

        if self._phase == 'travel':
            alpha = min(1.0, max(0.0, (now - self._phase_start) / self.TRAVEL_SEC))
            # Smoothstep easing
            ease = alpha * alpha * (3.0 - 2.0 * alpha)
            u = self._prev_uv[0] + ease * (self._cur_uv[0] - self._prev_uv[0])
            v = self._prev_uv[1] + ease * (self._cur_uv[1] - self._prev_uv[1])
            outer_r = 30.0
            ring_col = COLOR_ACCENT
        elif self._phase == 'settle':
            u, v = self._cur_uv
            alpha = min(1.0, max(0.0, (now - self._phase_start) / self.SETTLE_SEC))
            outer_r = 32.0 - 22.0 * alpha
            ring_col = COLOR_ACCENT
        elif self._phase == 'collect':
            u, v = self._cur_uv
            alpha = min(1.0, max(0.0, (now - self._phase_start) / self.COLLECT_SEC))
            outer_r = 10.0 + 4.0 * math.sin(alpha * math.pi)
            ring_col = COLOR_GREEN
        else:
            u, v = self._targets[0]
            outer_r = 28.0
            ring_col = COLOR_GREEN if eyes_ok else COLOR_YELLOW

        px = u * w
        py = v * h
        c.create_oval(
            px - outer_r,
            py - outer_r,
            px + outer_r,
            py + outer_r,
            outline=ring_col,
            width=2.5,
        )
        c.create_oval(px - 4.0, py - 4.0, px + 4.0, py + 4.0, fill='#FFFFFF', outline='')

    def _draw_verify_screen(self, w: int, h: int, latest_gaze: Any) -> None:
        c = self._canvas
        c.delete('all')

        prof = self._solved_profile
        if prof is not None:
            rms_pct = prof.rms_error_norm * 100.0
            dist_cm = prof.estimated_eye_to_screen_cm
            pts_txt = (
                f'{prof.inlier_targets}/{len(self._targets)}'
                if prof.inlier_targets > 0
                else f'{len(self._observations)}/{len(self._targets)}'
            )
            prefix = '✓ Calibration Saved!' if self._just_calibrated else '● Active Calibration'
            status_txt = (
                f'{prefix} ({prof.screen_diagonal_in:.1f}")  |  Targets: {pts_txt}  |  '
                f'RMS: {rms_pct:.2f}%  |  Dist: ~{dist_cm:.0f} cm'
            )
            status_col = COLOR_GREEN if rms_pct < 3.5 else COLOR_YELLOW
        else:
            status_txt = '○ Uncalibrated (Device Default)  |  Check live gaze below or click Recalibrate'
            status_col = COLOR_YELLOW

        if self._last_verify_banner != (status_txt, status_col):
            self._lbl_verify_status.configure(text=status_txt, fg=status_col)
            self._last_verify_banner = (status_txt, status_col)

        y_hint = max(115.0, h * 0.22)
        c.create_text(
            w * 0.5,
            y_hint,
            text=(
                'Look at any ring to verify live hardware gaze   •   '
                'Space / Enter / Esc: Done   •   R: Recalibrate'
            ),
            fill=COLOR_TEXT,
            font=('Segoe UI', 10),
        )
        if prof is not None and prof.mean_eye_xyz[1] < 20.0:
            c.create_text(
                w * 0.5,
                y_hint + 24.0,
                text=(
                    f'⚠ Trackbox Note: Eye Y is {prof.mean_eye_xyz[1]:+.0f} mm (low in sensor FOV). '
                    'Tilting the bar ~10-15° LESS upward centers your eyes (+80..+180 mm) '
                    'for cleaner bottom/edge lock.'
                ),
                fill=COLOR_YELLOW,
                font=('Segoe UI', 9, 'bold'),
            )

        for tu, tv in self._targets:
            tx = tu * w
            ty = tv * h
            c.create_oval(tx - 18, ty - 18, tx + 18, ty + 18, outline=COLOR_SURFACE, width=2)
            c.create_oval(tx - 3.5, ty - 3.5, tx + 3.5, ty + 3.5, fill=COLOR_ACCENT, outline='')

        if latest_gaze is not None:
            gx_norm = math.nan
            gy_norm = math.nan
            if latest_gaze.gaze_valid and not math.isnan(latest_gaze.gaze_x):
                gx_norm, gy_norm = latest_gaze.gaze_x, latest_gaze.gaze_y
            elif (
                latest_gaze.gaze_unfiltered_valid
                and not math.isnan(latest_gaze.gaze_unfiltered_x)
            ):
                gx_norm, gy_norm = latest_gaze.gaze_unfiltered_x, latest_gaze.gaze_unfiltered_y
            if not math.isnan(gx_norm) and not math.isnan(gy_norm):
                gx = max(0.0, min(1.0, gx_norm)) * w
                gy = max(0.0, min(1.0, gy_norm)) * h
                c.create_oval(gx - 12, gy - 12, gx + 12, gy + 12, outline=COLOR_GREEN, width=2.5)
                c.create_line(gx - 18, gy, gx + 18, gy, fill=COLOR_GREEN, width=1.8)
                c.create_line(gx, gy - 18, gx, gy + 18, fill=COLOR_GREEN, width=1.8)


class TobiiStudioApp:
    """Tkinter verification GUI and zero-CPU-when-hidden telemetry collector for Tobii ET5."""

    UI_INTERVAL_MS = 40  # 25 FPS when window is mapped & visible; 0 FPS when hidden/minimized

    def __init__(
        self,
        root: tk.Tk,
        aux_port: int = 18921,
        ir_fps: float = 30.0,
        backup_dir: Optional[str] = None,
        extra_sinks: Optional[Sequence[UploadSinkConfig]] = None,
        standby_sinks: Optional[Sequence[UploadSinkConfig]] = None,
        model_path: Optional[str] = None,
        calibration_path: Optional[str] = None,
        auto_connect: bool = True,
        transport_factory: Optional[Callable[[], BaseUsbTransport]] = None,
        extractor_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.root = root
        self.root.title('BrBrDb Tobii Eye Tracker 5 Direct-USB Telemetry Studio')
        sw = max(800, int(self.root.winfo_screenwidth()))
        sh = max(600, int(self.root.winfo_screenheight()))
        win_w = min(1520, int(sw * 0.90))
        win_h = min(880, int(sh * 0.85))
        self.root.geometry(f'{win_w}x{win_h}')
        self.root.configure(bg=COLOR_BG)

        self._window_mapped = True
        self._closing = False
        self._ui_timer_id: Optional[str] = None
        self._ui_tick_counter = 0
        self._nir_photo_ref: Optional[object] = None
        self._usb_detected = False
        self._custom_transport = transport_factory is not None
        self._active_cal_overlay: Optional[_FullscreenCalibrationWindow] = None

        self._session_mgr = TobiiAuxSessionManager(
            host='127.0.0.1',
            port=aux_port,
            backup_dir=backup_dir,
            extra_sinks=extra_sinks,
            standby_sinks=standby_sinks,
            on_shutdown_request=self._on_remote_shutdown,
        )
        self._client = TobiiDirectClient(
            on_gaze_sample=self._session_mgr.on_gaze_sample,
            on_face_sample=self._session_mgr.on_face_sample,
            ir_fps=ir_fps,
            model_path=model_path,
            history_seconds=4.0,
            calibration_path=calibration_path,
            transport_factory=transport_factory,
            extractor_factory=extractor_factory,
        )

        self._build_ui(initial_ir_fps=ir_fps)
        self._bind_visibility_events()
        self._refresh_calibration_badge()

        self._session_mgr.start()
        self._refresh_usb_state()

        if auto_connect and (self._custom_transport or self._can_auto_connect_now()):
            self._connect_usb()

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._ensure_ui_loop_running()

    @property
    def ui_tick_counter(self) -> int:
        """Number of UI redraw ticks executed (used by tests to verify zero-CPU gating)."""
        return self._ui_tick_counter

    def _can_auto_connect_now(self) -> bool:
        if not self._usb_detected:
            return False
        if sys.platform == 'win32' and get_windows_tobii_services_running():
            return False
        return True

    def _build_ui(self, initial_ir_fps: float) -> None:
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure(
            'TCombobox',
            fieldbackground=COLOR_SURFACE,
            background=COLOR_SURFACE,
            foreground=COLOR_TEXT,
        )

        # 1. Top Control Bar
        top_bar = tk.Frame(self.root, bg=COLOR_PANEL, padx=12, pady=10)
        top_bar.pack(fill=tk.X, side=tk.TOP)

        self._lbl_device = tk.Label(
            top_bar,
            text='USB 2104:0313: Checking...',
            bg=COLOR_PANEL,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9, 'bold'),
        )
        self._lbl_device.pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(
            top_bar,
            text='Refresh',
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            padx=8,
            command=self._refresh_usb_state,
        ).pack(side=tk.LEFT, padx=(0, 6))

        self._btn_connect = tk.Button(
            top_bar,
            text='Connect',
            bg=COLOR_ACCENT,
            fg=COLOR_PANEL,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=12,
            command=self._on_connect_clicked,
        )
        self._btn_connect.pack(side=tk.LEFT, padx=(0, 18))

        tk.Label(
            top_bar,
            text='NIR Face FPS:',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        fps_label = f'{int(round(initial_ir_fps))} FPS'
        if fps_label not in ('15 FPS', '30 FPS', '60 FPS'):
            fps_label = '30 FPS'
        self._fps_var = tk.StringVar(value=fps_label)
        cb_fps = ttk.Combobox(
            top_bar,
            textvariable=self._fps_var,
            values=['15 FPS', '30 FPS', '60 FPS'],
            width=8,
            state='readonly',
        )
        cb_fps.pack(side=tk.LEFT, padx=(0, 14))
        cb_fps.bind('<<ComboboxSelected>>', self._on_fps_changed)

        self._btn_calibrate = tk.Button(
            top_bar,
            text='🎯 Calibrate Screen',
            bg=COLOR_GREEN,
            fg=COLOR_PANEL,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=10,
            command=self._on_calibrate_clicked,
        )
        self._btn_calibrate.pack(side=tk.LEFT, padx=(0, 8))

        self._lbl_cal_status = tk.Label(
            top_bar,
            text='Cal: Device Default',
            bg=COLOR_PANEL,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9),
        )
        self._lbl_cal_status.pack(side=tk.LEFT, padx=(0, 6))

        self._btn_reset_cal = tk.Button(
            top_bar,
            text='Reset Cal',
            bg=COLOR_SURFACE,
            fg=COLOR_MUTED,
            font=('Segoe UI', 8),
            relief=tk.FLAT,
            padx=6,
            command=self._on_reset_cal_clicked,
        )
        self._btn_reset_cal.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_win_svc: Optional[tk.Button] = None
        if sys.platform == 'win32':
            self._btn_win_svc = tk.Button(
                top_bar,
                text='Claim USB (Stop Tobii Svc)',
                bg=COLOR_SURFACE,
                fg=COLOR_YELLOW,
                font=('Segoe UI', 9, 'bold'),
                relief=tk.FLAT,
                padx=10,
                command=self._on_win_svc_clicked,
            )
            self._btn_win_svc.pack(side=tk.RIGHT)

        # 2. Status & AUX Session Bar
        status_bar = tk.Frame(self.root, bg=COLOR_SURFACE, padx=12, pady=8)
        status_bar.pack(fill=tk.X, side=tk.TOP, pady=(1, 0))

        self._lbl_hw_status = tk.Label(
            status_bar,
            text='○ Disconnected',
            bg=COLOR_SURFACE,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9, 'bold'),
        )
        self._lbl_hw_status.pack(side=tk.LEFT, padx=(0, 16))

        self._lbl_rates = tk.Label(
            status_bar,
            text='Click Connect to open Tobii Eye Tracker 5 over direct USB',
            bg=COLOR_SURFACE,
            fg=COLOR_MUTED,
            font=('Consolas', 9),
        )
        self._lbl_rates.pack(side=tk.LEFT, padx=(0, 16))

        self._lbl_session = tk.Label(
            status_bar,
            text=f'○ AUX Standby (127.0.0.1:{self._session_mgr.port})',
            bg=COLOR_SURFACE,
            fg=COLOR_ACCENT,
            font=('Segoe UI', 9, 'bold'),
        )
        self._lbl_session.pack(side=tk.RIGHT)

        # 3. Main Verification Workspace (Left: NIR + MediaPipe, Right: 2D Gaze + Strip Charts)
        body = tk.Frame(self.root, bg=COLOR_BG, padx=12, pady=10)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=6)
        body.rowconfigure(0, weight=1)

        left_frame = tk.Frame(body, bg=COLOR_PANEL)
        left_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 6))

        self._nir_canvas = tk.Canvas(left_frame, bg=COLOR_PANEL, highlightthickness=0)
        self._nir_canvas.pack(fill=tk.BOTH, expand=True)

        right_frame = tk.Frame(body, bg=COLOR_BG)
        right_frame.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        right_frame.rowconfigure(0, weight=5)
        right_frame.rowconfigure(1, weight=6)
        right_frame.columnconfigure(0, weight=1)

        self._gaze_canvas = tk.Canvas(right_frame, bg=COLOR_PANEL, highlightthickness=0)
        self._gaze_canvas.grid(row=0, column=0, sticky='nsew', pady=(0, 6))

        self._charts_canvas = tk.Canvas(right_frame, bg=COLOR_PANEL, highlightthickness=0)
        self._charts_canvas.grid(row=1, column=0, sticky='nsew', pady=(6, 0))

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
        if event.widget is not self.root:
            return
        if self.is_window_visible():
            self._ensure_ui_loop_running()
        else:
            self._pause_ui_rendering()

    def is_window_visible(self) -> bool:
        """Return True only when the Studio window is mapped and not minimized/withdrawn."""
        if self._closing or not self._window_mapped:
            return False
        try:
            state = self.root.state()
            if state in ('iconic', 'withdrawn'):
                return False
            return bool(self.root.winfo_viewable())
        except tk.TclError:
            return False

    def _pause_ui_rendering(self) -> None:
        """Cancel Tkinter `after` timer and stop UI-only landmark/frame work (0% UI CPU)."""
        if self._ui_timer_id is not None:
            try:
                self.root.after_cancel(self._ui_timer_id)
            except tk.TclError:
                pass
            self._ui_timer_id = None
        self._nir_photo_ref = None
        self._client.set_ui_visible(False)

    def _ensure_ui_loop_running(self) -> None:
        """Resume 25 FPS UI timer if and only if the window is currently visible."""
        if self._closing or not self.is_window_visible():
            return
        self._client.set_ui_visible(True)
        if self._ui_timer_id is None:
            self._ui_timer_id = self.root.after(10, self._ui_tick_loop)

    def _ui_tick_loop(self) -> None:
        self._ui_timer_id = None
        if self._closing or not self.is_window_visible():
            self._pause_ui_rendering()
            return

        self._render_ui_frame()
        if not self._closing and self.is_window_visible():
            self._ui_timer_id = self.root.after(self.UI_INTERVAL_MS, self._ui_tick_loop)

    def _refresh_usb_state(self) -> None:
        self._usb_detected = self._custom_transport or is_tobii_usb_present()
        if self._usb_detected:
            self._lbl_device.configure(
                text='● Tobii Eye Tracker 5 (2104:0313)',
                fg=COLOR_GREEN,
            )
        else:
            self._lbl_device.configure(
                text='○ Tobii Eye Tracker 5 Not Detected',
                fg=COLOR_YELLOW,
            )
        self._update_win_service_button()

    def _update_win_service_button(self) -> None:
        if self._btn_win_svc is None:
            return
        running = get_windows_tobii_services_running()
        if running:
            self._btn_win_svc.configure(
                text='🔓 Claim USB (Stop Tobii Svc)',
                fg=COLOR_YELLOW,
            )
        else:
            self._btn_win_svc.configure(
                text='🔄 Restore Tobii Svc',
                fg=COLOR_CYAN,
            )

    def _on_win_svc_clicked(self) -> None:
        if self._btn_win_svc is None:
            return
        running = get_windows_tobii_services_running()
        want_stop = bool(running)
        if not want_stop and self._client.is_running:
            self._client.stop()
            self._btn_connect.configure(text='Connect', bg=COLOR_ACCENT)

        self._btn_win_svc.configure(state=tk.DISABLED, text='⏳ Waiting for UAC...')

        def _worker() -> None:
            ok = toggle_windows_tobii_services_elevated(stop=want_stop)
            if not self._closing:
                self.root.after(0, lambda: self._on_win_svc_done(want_stop, ok))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_win_svc_done(self, stopped_services: bool, ok: bool) -> None:
        if self._btn_win_svc is not None:
            self._btn_win_svc.configure(state=tk.NORMAL)
        self._refresh_usb_state()
        if stopped_services and ok and self._usb_detected and not self._client.is_running:
            self._connect_usb()

    def _on_fps_changed(self, _event: Optional[tk.Event] = None) -> None:
        raw = self._fps_var.get().split()[0]
        try:
            self._client.set_ir_fps(float(raw))
        except ValueError:
            pass

    def _refresh_calibration_badge(self) -> None:
        prof = self._client.active_calibration_profile
        if prof is not None:
            rms_pct = prof.rms_error_norm * 100.0
            dist_cm = prof.estimated_eye_to_screen_cm
            self._lbl_cal_status.configure(
                text=f'● Cal: {prof.screen_diagonal_in:.0f}" (RMS {rms_pct:.1f}%, ~{dist_cm:.0f} cm)',
                fg=COLOR_GREEN,
            )
            self._btn_reset_cal.configure(state=tk.NORMAL, fg=COLOR_TEXT)
        else:
            self._lbl_cal_status.configure(
                text='Cal: Device Default',
                fg=COLOR_MUTED,
            )
            self._btn_reset_cal.configure(state=tk.DISABLED, fg=COLOR_MUTED)

    def _on_reset_cal_clicked(self) -> None:
        if self._client.active_calibration_profile is None:
            return
        confirm = messagebox.askyesno(
            'Reset Screen Calibration',
            "Remove the saved in-app calibration profile and restore the tracker's "
            'original display area?',
        )
        if not confirm:
            return
        try:
            self._client.clear_saved_calibration()
        except Exception as exc:
            messagebox.showerror('Reset Calibration Error', str(exc))
        self._refresh_calibration_badge()

    def _on_calibrate_clicked(self) -> None:
        if self._active_cal_overlay is not None:
            return
        if not self._client.is_running:
            ask = messagebox.askyesno(
                'Connect Required',
                'Tobii Eye Tracker 5 must be streaming over USB before calibrating.\n\n'
                'Connect now?',
            )
            if not ask:
                return
            self._refresh_usb_state()
            self._connect_usb()
            if not self._client.is_running:
                return

        monitors = _discover_monitors(self.root)
        saved_prof = self._client.active_calibration_profile
        init_mon_idx = 0
        if saved_prof is not None and 0 <= saved_prof.monitor_index < len(monitors):
            init_mon_idx = saved_prof.monitor_index
        self._launch_fullscreen_calibration(monitors, init_mon_idx)

    def _launch_fullscreen_calibration(
        self,
        monitors: Sequence[_MonitorDescriptor],
        initial_monitor_index: int,
    ) -> None:
        def _done(_prof: Optional[DisplayAreaProfile]) -> None:
            self._active_cal_overlay = None
            self._refresh_calibration_badge()

        self._active_cal_overlay = _FullscreenCalibrationWindow(
            parent=self.root,
            client=self._client,
            monitors=monitors,
            initial_monitor_index=initial_monitor_index,
            on_finished=_done,
        )

    def _on_connect_clicked(self) -> None:
        if self._client.is_running:
            self._client.stop()
            self._btn_connect.configure(text='Connect', bg=COLOR_ACCENT)
            return
        self._refresh_usb_state()
        self._connect_usb()

    def _connect_usb(self) -> None:
        try:
            self._client.start()
            self._btn_connect.configure(text='Disconnect', bg=COLOR_RED)
        except UsbAccessDeniedError as exc:
            self._client.stop()
            self._btn_connect.configure(text='Connect', bg=COLOR_ACCENT)
            if sys.platform == 'win32':
                ask = messagebox.askyesno(
                    'WinUSB Claimed by Tobii Service',
                    f'{exc}\n\nStop Windows Tobii services now (UAC prompt) and connect?',
                )
                if ask:
                    if toggle_windows_tobii_services_elevated(stop=True):
                        self._refresh_usb_state()
                        self._connect_usb()
            else:
                messagebox.showerror('USB Access Denied', str(exc))
        except Exception as exc:
            self._client.stop()
            self._btn_connect.configure(text='Connect', bg=COLOR_ACCENT)
            messagebox.showerror('Connection Error', str(exc))

    def _render_ui_frame(self) -> None:
        self._ui_tick_counter += 1

        conn_err = self._client.pop_connection_error()
        if conn_err:
            self._btn_connect.configure(text='Connect', bg=COLOR_ACCENT)
            messagebox.showerror('Tobii USB Stream Error', conn_err)

        ctx = self._session_mgr.context
        err_suffix = f' | Err: {ctx.last_upload_error}' if ctx.last_upload_error else ''
        if ctx.active:
            track = ctx.metadata.get('Track name', 'Track')
            driver = ctx.metadata.get('Driver name', 'Driver')
            n_sinks = len(ctx.upload_targets)
            sink_note = f' | {n_sinks} sink{"s" if n_sinks != 1 else ""}'
            self._lbl_session.configure(
                text=(
                    f'● LIVE STREAMING{sink_note}: {track} ({driver}) | '
                    f'Gaze: {ctx.gaze_samples_recorded} | Face: {ctx.face_samples_recorded} | '
                    f'Streamed: {ctx.segments_uploaded} seg ({ctx.bytes_uploaded // 1024} KB)'
                    f'{err_suffix}'
                ),
                fg=COLOR_RED if ctx.last_upload_error else COLOR_GREEN,
            )
        else:
            live_suffix = ''
            if self._session_mgr.standby_sinks:
                target_addr = self._session_mgr.standby_sinks[0].server_address
                target_host = target_addr.replace('http://', '').replace('https://', '')
                if ctx.standby_connected:
                    live_suffix = (
                        f' | Live -> {target_host} ● ({ctx.standby_gaze_pushed})'
                    )
                else:
                    live_suffix = f' | Live -> {target_host} ○ waiting'
            self._lbl_session.configure(
                text=(
                    f'○ AUX Standby (127.0.0.1:{self._session_mgr.bound_port})'
                    f'{live_suffix} | Uploaded: {ctx.segments_uploaded} seg{err_suffix}'
                ),
                fg=COLOR_RED if ctx.last_upload_error else COLOR_ACCENT,
            )

        if not self._client.is_running:
            self._btn_connect.configure(text='Connect', bg=COLOR_ACCENT)
            self._lbl_hw_status.configure(text='○ Disconnected', fg=COLOR_MUTED)
            if sys.platform == 'win32' and self._usb_detected and get_windows_tobii_services_running():
                hint = 'WinUSB busy (Tobii services running) | Click Claim USB or Connect'
            elif self._usb_detected:
                hint = 'Tobii Eye Tracker 5 ready | Click Connect to stream'
            else:
                hint = 'Connect Tobii Eye Tracker 5 via USB & click Refresh'
            self._lbl_rates.configure(text=hint)
            empty_snap = UiTelemetrySnapshot(None, None, None, [], [])
            self._draw_nir_panel(empty_snap)
            self._draw_gaze_panel(empty_snap)
            self._draw_charts_panel(empty_snap)
            return

        mp_err = self._client.mediapipe_error
        self._lbl_hw_status.configure(
            text='● Streaming (Direct USB)',
            fg=COLOR_YELLOW if mp_err else COLOR_GREEN,
        )

        snap = self._client.get_ui_snapshot()
        lat_str = (
            f'{snap.latest_face.latency_ms:.1f} ms'
            if snap.latest_face is not None
            else '--'
        )
        mp_note = f' | MP Err: {mp_err}' if mp_err else ''
        self._lbl_rates.configure(
            text=(
                f'Gaze: {self._client.gaze_hz:5.1f} Hz ({self._client.gaze_samples_received}) | '
                f'NIR Face: {self._client.face_hz:4.1f} Hz '
                f'({self._client.face_samples_received}, {lat_str}){mp_note}'
            )
        )

        self._draw_nir_panel(snap)
        self._draw_gaze_panel(snap)
        self._draw_charts_panel(snap)

    def _draw_nir_panel(self, snap: UiTelemetrySnapshot) -> None:
        c = self._nir_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 60 or h < 60:
            return

        c.delete('all')
        c.create_text(
            14,
            14,
            text='NIR Sensor (0x050e) + MediaPipe Face Landmarker',
            anchor=tk.NW,
            fill=COLOR_ACCENT,
            font=('Segoe UI', 10, 'bold'),
        )

        gray = snap.latest_nir_gray
        face = snap.latest_face
        if gray is None:
            msg = (
                'Waiting for NIR camera frames (0x050e)...'
                if self._client.is_running
                else 'NIR Sensor Offline'
            )
            c.create_text(
                w * 0.5,
                h * 0.5,
                text=msg,
                fill=COLOR_MUTED,
                font=('Segoe UI', 10),
            )
            return

        img_h, img_w = gray.shape[:2]
        avail_w = max(40, w - 24)
        avail_h = max(40, h - 96)
        scale = min(float(avail_w) / float(img_w), float(avail_h) / float(img_h))
        disp_w = max(16, int(round(img_w * scale)))
        disp_h = max(16, int(round(img_h * scale)))
        ox = int((w - disp_w) // 2)
        oy = 38 + int((avail_h - disp_h) // 2)

        if Image is not None and ImageTk is not None:
            pil_img = Image.fromarray(gray, mode='L').resize((disp_w, disp_h))
            self._nir_photo_ref = ImageTk.PhotoImage(pil_img)
            c.create_image(ox, oy, image=self._nir_photo_ref, anchor=tk.NW)
        else:
            c.create_rectangle(
                ox, oy, ox + disp_w, oy + disp_h, outline=COLOR_SURFACE, fill='#11111B'
            )

        c.create_rectangle(ox, oy, ox + disp_w, oy + disp_h, outline=COLOR_SURFACE, width=1)

        if face is not None and face.present:
            for lx, ly in face.ui_landmarks_xy:
                px = ox + lx * scale
                py = oy + ly * scale
                c.create_oval(
                    px - 2.2,
                    py - 2.2,
                    px + 2.2,
                    py + 2.2,
                    fill=COLOR_CYAN,
                    outline='',
                )

            if not math.isnan(face.head_yaw_deg):
                anchor_x = ox + disp_w * 0.5
                anchor_y = oy + disp_h * 0.5
                if len(face.ui_landmarks_xy) >= 15:
                    nx, ny = face.ui_landmarks_xy[14]
                    anchor_x = ox + nx * scale
                    anchor_y = oy + ny * scale

                yaw_r = math.radians(face.head_yaw_deg)
                pitch_r = math.radians(face.head_pitch_deg)
                roll_r = math.radians(face.head_roll_deg)
                axis_len = min(disp_w, disp_h) * 0.22

                cy, sy = math.cos(yaw_r), math.sin(yaw_r)
                cp, sp = math.cos(pitch_r), math.sin(pitch_r)
                cr, sr = math.cos(roll_r), math.sin(roll_r)

                # Project orthonormal 3D frame R = Ry(yaw) @ Rx(-pitch) @ Rz(roll)
                # from OpenGL (+X right, +Y up) onto Canvas (+X right, +Y down)
                x_dx = axis_len * (cr * cy + sr * sy * sp)
                x_dy = -axis_len * (sr * cp)
                y_dx = axis_len * (-sr * cy + cr * sy * sp)
                y_dy = -axis_len * (cr * cp)
                z_dx = axis_len * (sy * cp)
                z_dy = -axis_len * sp

                c.create_line(
                    anchor_x, anchor_y, anchor_x + x_dx, anchor_y + x_dy,
                    fill=COLOR_RED, width=2,
                )
                c.create_line(
                    anchor_x, anchor_y, anchor_x + y_dx, anchor_y + y_dy,
                    fill=COLOR_GREEN, width=2,
                )
                c.create_line(
                    anchor_x, anchor_y, anchor_x + z_dx, anchor_y + z_dy,
                    fill=COLOR_ACCENT, width=2.5, arrow=tk.LAST,
                )

            info_line1 = (
                f'Head Pos (cm): X={face.head_pos_x_cm:+5.1f}  '
                f'Y={face.head_pos_y_cm:+5.1f}  Z={face.head_pos_z_cm:+5.1f}   |   '
                f'Frame: {img_w}x{img_h}'
            )
            info_line2 = (
                f'Head Angles: Yaw={face.head_yaw_deg:+5.1f}°  '
                f'Pitch={face.head_pitch_deg:+5.1f}°  Roll={face.head_roll_deg:+5.1f}°   |   '
                f'Blink L/R: {face.eye_blink_left:.2f}/{face.eye_blink_right:.2f}  '
                f'Jaw: {face.jaw_open:.2f}'
            )
            fg_info = COLOR_TEXT
        else:
            info_line1 = f'Frame: {img_w}x{img_h}  |  No face detected in NIR frame'
            info_line2 = 'Head Pose & Blendshapes: NaN'
            fg_info = COLOR_YELLOW

        c.create_text(
            14,
            h - 44,
            text=info_line1,
            anchor=tk.NW,
            fill=fg_info,
            font=('Consolas', 9),
        )
        c.create_text(
            14,
            h - 24,
            text=info_line2,
            anchor=tk.NW,
            fill=COLOR_MUTED,
            font=('Consolas', 9),
        )

    def _draw_gaze_panel(self, snap: UiTelemetrySnapshot) -> None:
        c = self._gaze_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 60 or h < 60:
            return

        c.delete('all')
        c.create_text(
            14,
            12,
            text='Hardware Screen Gaze & Eye Origins (0x0500)',
            anchor=tk.NW,
            fill=COLOR_GREEN,
            font=('Segoe UI', 10, 'bold'),
        )

        pad_x = 22.0
        top_y = 36.0
        bot_y = max(top_y + 40.0, float(h - 48))
        box_w = max(40.0, float(w) - 2.0 * pad_x)
        box_h = bot_y - top_y

        c.create_rectangle(
            pad_x, top_y, pad_x + box_w, bot_y,
            fill='#11111B', outline=COLOR_SURFACE, width=1.5,
        )
        for frac in (1.0 / 3.0, 2.0 / 3.0):
            gx = pad_x + box_w * frac
            gy = top_y + box_h * frac
            c.create_line(gx, top_y, gx, bot_y, fill=COLOR_GRID, dash=(3, 3))
            c.create_line(pad_x, gy, pad_x + box_w, gy, fill=COLOR_GRID, dash=(3, 3))

        g = snap.latest_gaze
        if g is None:
            c.create_text(
                w * 0.5,
                (top_y + bot_y) * 0.5,
                text='No 0x0500 gaze samples',
                fill=COLOR_MUTED,
                font=('Segoe UI', 9),
            )
            return

        def _clamp_pt(nx: float, ny: float) -> Tuple[float, float]:
            cx = pad_x + max(-0.05, min(1.05, nx)) * box_w
            cy = top_y + max(-0.05, min(1.05, ny)) * box_h
            return cx, cy

        if g.gaze_l_valid and not math.isnan(g.gaze_l_x):
            lx, ly = _clamp_pt(g.gaze_l_x, g.gaze_l_y)
            c.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, outline=COLOR_CYAN, width=1.5)

        if g.gaze_r_valid and not math.isnan(g.gaze_r_x):
            rx, ry = _clamp_pt(g.gaze_r_x, g.gaze_r_y)
            c.create_oval(rx - 4, ry - 4, rx + 4, ry + 4, outline=COLOR_MAUVE, width=1.5)

        if g.gaze_unfiltered_valid and not math.isnan(g.gaze_unfiltered_x):
            ux, uy = _clamp_pt(g.gaze_unfiltered_x, g.gaze_unfiltered_y)
            c.create_oval(ux - 3, uy - 3, ux + 3, uy + 3, fill=COLOR_YELLOW, outline='')

        if g.gaze_valid and not math.isnan(g.gaze_x):
            px, py = _clamp_pt(g.gaze_x, g.gaze_y)
            c.create_oval(px - 8, py - 8, px + 8, py + 8, outline=COLOR_GREEN, width=2)
            c.create_line(px - 13, py, px + 13, py, fill=COLOR_GREEN, width=1.5)
            c.create_line(px, py - 13, px, py + 13, fill=COLOR_GREEN, width=1.5)
            gaze_txt = f'Gaze: ({g.gaze_x:0.3f}, {g.gaze_y:0.3f})'
        else:
            gaze_txt = 'Gaze: NaN (Blink / Out of Trackbox)'

        pup_l = (
            f'{g.pupil_l_mm:0.2f}'
            if g.validity_l and not math.isnan(g.pupil_l_mm) and g.pupil_l_mm > 0.5
            else 'NaN'
        )
        pup_r = (
            f'{g.pupil_r_mm:0.2f}'
            if g.validity_r and not math.isnan(g.pupil_r_mm) and g.pupil_r_mm > 0.5
            else 'NaN'
        )
        ez_l = (
            f'{g.eye_l_z_mm:0.0f}'
            if g.validity_l and not math.isnan(g.eye_l_z_mm) and abs(g.eye_l_z_mm) > 10.0
            else 'NaN'
        )
        ez_r = (
            f'{g.eye_r_z_mm:0.0f}'
            if g.validity_r and not math.isnan(g.eye_r_z_mm) and abs(g.eye_r_z_mm) > 10.0
            else 'NaN'
        )

        c.create_text(
            14,
            h - 38,
            text=f'{gaze_txt}   |   Pupil L/R (mm): {pup_l} / {pup_r}',
            anchor=tk.NW,
            fill=COLOR_TEXT,
            font=('Consolas', 9),
        )
        c.create_text(
            14,
            h - 19,
            text=(
                f'Eye Distance Z L/R (mm): {ez_l} / {ez_r}   |   '
                'Green=Gaze  Yellow=Unfiltered  Cyan/Mauve=L/R'
            ),
            anchor=tk.NW,
            fill=COLOR_MUTED,
            font=('Consolas', 8),
        )

    def _draw_charts_panel(self, snap: UiTelemetrySnapshot) -> None:
        c = self._charts_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 60 or h < 90:
            return

        c.delete('all')
        lane_h = float(h) / 3.0
        x_left = 14.0
        plot_w = float(w) - 28.0
        t_span = 4.0

        now_t = 0.0
        if snap.gaze_history:
            now_t = max(now_t, snap.gaze_history[-1][0])
        if snap.face_history:
            now_t = max(now_t, snap.face_history[-1][0])
        t_min = now_t - t_span

        lanes = [
            ('Eye Blink L / R (0..1)', COLOR_CYAN),
            ('Pupil Diameter L / R (2..6 mm)', COLOR_GREEN),
            ('Head Yaw / Pitch / Roll (-35..+35 deg)', COLOR_ACCENT),
        ]

        for idx, (title, col_a) in enumerate(lanes):
            y0 = idx * lane_h
            if idx > 0:
                c.create_line(0, y0, w, y0, fill=COLOR_SURFACE, width=1)
            c.create_line(
                x_left, y0 + lane_h * 0.5, x_left + plot_w, y0 + lane_h * 0.5,
                fill=COLOR_GRID, dash=(3, 3),
            )
            c.create_text(
                x_left,
                y0 + 6,
                text=title,
                anchor=tk.NW,
                fill=col_a,
                font=('Segoe UI', 9, 'bold'),
            )

        if len(snap.face_history) >= 2:
            f_times = [r[0] for r in snap.face_history]
            blink_l = [r[1] for r in snap.face_history]
            blink_r = [r[2] for r in snap.face_history]
            yaw_v = [r[3] for r in snap.face_history]
            pitch_v = [r[4] for r in snap.face_history]
            roll_v = [r[5] for r in snap.face_history]

            for seg in build_strip_polyline(
                f_times, blink_l, t_min, t_span, x_left, plot_w, 24.0, lane_h - 30.0, 0.0, 1.0
            ):
                c.create_line(*seg, fill=COLOR_CYAN, width=1.5)
            for seg in build_strip_polyline(
                f_times, blink_r, t_min, t_span, x_left, plot_w, 24.0, lane_h - 30.0, 0.0, 1.0
            ):
                c.create_line(*seg, fill=COLOR_MAUVE, width=1.5)

            y2_top = 2.0 * lane_h + 24.0
            y2_h = lane_h - 30.0
            for vals, col in ((yaw_v, COLOR_ACCENT), (pitch_v, COLOR_GREEN), (roll_v, COLOR_RED)):
                for seg in build_strip_polyline(
                    f_times, vals, t_min, t_span, x_left, plot_w, y2_top, y2_h, -35.0, 35.0
                ):
                    c.create_line(*seg, fill=col, width=1.5)

        if len(snap.gaze_history) >= 2:
            g_times = [r[0] for r in snap.gaze_history]
            pup_l = [r[3] for r in snap.gaze_history]
            pup_r = [r[4] for r in snap.gaze_history]
            y1_top = lane_h + 24.0
            y1_h = lane_h - 30.0
            for seg in build_strip_polyline(
                g_times, pup_l, t_min, t_span, x_left, plot_w, y1_top, y1_h, 2.0, 6.0
            ):
                c.create_line(*seg, fill=COLOR_GREEN, width=1.5)
            for seg in build_strip_polyline(
                g_times, pup_r, t_min, t_span, x_left, plot_w, y1_top, y1_h, 2.0, 6.0
            ):
                c.create_line(*seg, fill=COLOR_YELLOW, width=1.5)

    def _on_remote_shutdown(self) -> None:
        self._closing = True
        self._client.stop()
        self._session_mgr.stop()
        try:
            self.root.after(0, self._on_close)
        except Exception:
            pass

    def _on_close(self) -> None:
        self._closing = True
        if self._active_cal_overlay is not None:
            self._active_cal_overlay._close_internal()
            self._active_cal_overlay = None
        self._pause_ui_rendering()
        self._client.stop()
        self._session_mgr.stop()
        self.root.destroy()


def main() -> None:
    """CLI entry point for the Tobii Eye Tracker 5 Direct-USB Telemetry Studio."""
    parser = argparse.ArgumentParser(
        description='Tobii Eye Tracker 5 Direct-USB Telemetry Studio & Dual-Stream BRT1 Uploader'
    )
    parser.add_argument(
        '--aux-port',
        type=int,
        default=18921,
        help='Local HTTP port for CommandSink session signalling (default: 18921)',
    )
    parser.add_argument(
        '--ir-fps',
        type=float,
        default=30.0,
        help='Maximum NIR camera FPS processed by MediaPipe Face Landmarker (default: 30)',
    )
    parser.add_argument(
        '--backup-dir',
        type=str,
        default=None,
        help='Optional local directory to save .brt1.br TobiiGaze & TobiiFace backups',
    )
    parser.add_argument(
        '--model-path',
        type=str,
        default=None,
        help='Optional custom path to face_landmarker.task',
    )
    parser.add_argument(
        '--calibration-path',
        type=str,
        default=None,
        help='Optional custom JSON path for saved screen calibration profile',
    )
    parser.add_argument(
        '--sink',
        action='append',
        default=[],
        help='Additional HTTP BRT1 sink URL (repeatable, e.g. http://127.0.0.1:18922)',
    )
    parser.add_argument(
        '--live-sink',
        default='http://127.0.0.1:18922',
        help='Local live-feedback BRT1 sink for standby gaze streaming',
    )
    parser.add_argument(
        '--no-live-sink',
        action='store_true',
        help='Disable default local standby gaze streaming to live_feedback',
    )
    parser.add_argument(
        '--no-auto-connect',
        action='store_true',
        help='Do not automatically connect to USB hardware on startup',
    )
    args = parser.parse_args()

    extra_sinks = [parse_sink_spec(s) for s in args.sink if str(s).strip()]
    standby_sinks = (
        []
        if args.no_live_sink or not str(args.live_sink).strip()
        else [parse_sink_spec(args.live_sink)]
    )

    _enable_windows_dpi_awareness()
    root = tk.Tk()
    TobiiStudioApp(
        root,
        aux_port=args.aux_port,
        ir_fps=args.ir_fps,
        backup_dir=args.backup_dir,
        extra_sinks=extra_sinks,
        standby_sinks=standby_sinks,
        model_path=args.model_path,
        calibration_path=args.calibration_path,
        auto_connect=not args.no_auto_connect,
    )
    root.mainloop()


if __name__ == '__main__':
    main()
