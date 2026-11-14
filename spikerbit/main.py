"""Spiker:bit + BBC micro:bit 500 Hz Neuro-Telemetry Studio & AUX Application."""

import argparse
import os
import sys
import threading
from typing import List, Optional, Tuple
import tkinter as tk
from tkinter import messagebox, ttk
import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spikerbit.dsp import SpikerbitDspProcessor  # noqa: E402
from spikerbit.flasher import find_microbit_drives, flash_firmware  # noqa: E402
from spikerbit.serial_brt1 import (  # noqa: E402
    SpikerbitSerialClient,
    list_serial_ports,
    scan_ble_spikerbits,
)
from spikerbit.uploader import AuxSessionManager  # noqa: E402


COLOR_BG = '#1E1E2E'
COLOR_PANEL = '#181825'
COLOR_SURFACE = '#313244'
COLOR_TEXT = '#CDD6F4'
COLOR_MUTED = '#A6ADC8'
COLOR_ACCENT = '#89B4FA'
COLOR_GREEN = '#A6E3A1'
COLOR_YELLOW = '#F9E2AF'
COLOR_RED = '#F38BA8'
COLOR_CYAN = '#94E2D5'
COLOR_MAUVE = '#CBA6F7'


def downsample_trace_for_canvas(
    values: np.ndarray,
    width_px: int,
    y_center: float,
    y_half_height: float,
) -> List[float]:
    """Convert a 1D waveform array into a flat `[x0, y0, x1, y1, ...]` polyline for `tk.Canvas`."""
    n = int(values.size)
    if n < 2 or width_px < 4:
        return []

    max_abs = float(np.max(np.abs(values - np.mean(values))))
    scale = max(15.0, max_abs * 1.15)
    centered = values - float(np.mean(values))

    step = max(1, n // max(1, width_px // 2))
    idx = np.arange(0, n, step, dtype=np.int64)
    if idx[-1] != n - 1:
        idx = np.append(idx, n - 1)

    xs = (idx.astype(np.float64) / float(n - 1)) * float(width_px - 20) + 10.0
    ys = y_center - (centered[idx] / scale) * (y_half_height * 0.82)
    ys = np.clip(ys, y_center - y_half_height + 2.0, y_center + y_half_height - 2.0)

    coords: List[float] = []
    for x_val, y_val in zip(xs, ys):
        coords.append(float(x_val))
        coords.append(float(y_val))
    return coords


class SpikerbitStudioApp:
    """Tkinter desktop app for live 500 Hz Spiker:bit inspection, flashing, and AUX upload."""

    def __init__(
        self,
        root: tk.Tk,
        aux_port: int = 18920,
        initial_port: Optional[str] = None,
        simulate: bool = False,
        backup_dir: Optional[str] = None,
    ) -> None:
        self.root = root
        self.root.title('BrBrDb Spiker:bit 500 Hz Neuro-Telemetry AUX Studio')
        self.root.geometry('1060x720')
        self.root.configure(bg=COLOR_BG)

        self._window_mapped = True
        self._closing = False
        self._ble_ports: List[str] = []
        self._ble_scan_in_progress = False
        self._dsp = SpikerbitDspProcessor(fs=500.0, notch_hz=50.0)

        self._session_mgr = AuxSessionManager(
            host='127.0.0.1',
            port=aux_port,
            backup_dir=backup_dir,
            on_session_start=self._on_aux_session_start,
        )
        self._serial_client = SpikerbitSerialClient(
            on_raw_chunk=self._session_mgr.on_spikerbit_chunk,
            ring_buffer_seconds=10.0,
        )

        self._build_ui()
        self._bind_visibility_events()

        self._session_mgr.start()
        explicit_sim = bool(simulate) or (
            initial_port is not None and initial_port.strip().upper() == 'SIMULATOR'
        )
        detected_hw = self._refresh_ports(
            trigger_ble_scan=not explicit_sim,
            auto_connect_ble=(not explicit_sim and not initial_port),
        )
        if explicit_sim:
            self._port_var.set('SIMULATOR')
            self._connect_selected(simulate=True)
        elif initial_port:
            self._port_var.set(initial_port.strip())
            self._connect_selected(simulate=False)
        elif detected_hw:
            self._port_var.set(detected_hw)
            self._connect_selected(simulate=False)

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._schedule_ui_tick()

    def _build_ui(self) -> None:
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

        tk.Label(
            top_bar,
            text='Port:',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._port_var = tk.StringVar(value='')
        self._cb_port = ttk.Combobox(
            top_bar,
            textvariable=self._port_var,
            width=30,
            state='readonly',
        )
        self._cb_port.pack(side=tk.LEFT, padx=(0, 6))

        self._btn_refresh = tk.Button(
            top_bar,
            text='Refresh',
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            relief=tk.FLAT,
            padx=8,
            command=lambda: self._refresh_ports(trigger_ble_scan=True, auto_connect_ble=False),
        )
        self._btn_refresh.pack(side=tk.LEFT, padx=(0, 6))

        btn_connect = tk.Button(
            top_bar,
            text='Connect',
            bg=COLOR_ACCENT,
            fg=COLOR_PANEL,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=10,
            command=self._on_connect_clicked,
        )
        btn_connect.pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(
            top_bar,
            text='Spiker:bit Mode:',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._mode_var = tk.StringVar(value='EEG')
        for mode_code in ('EEG', 'ECG', 'EMG'):
            rb = tk.Radiobutton(
                top_bar,
                text=mode_code,
                variable=self._mode_var,
                value=mode_code,
                bg=COLOR_PANEL,
                fg=COLOR_TEXT,
                selectcolor=COLOR_SURFACE,
                activebackground=COLOR_PANEL,
                activeforeground=COLOR_ACCENT,
                font=('Segoe UI', 9, 'bold'),
                command=self._on_mode_changed,
            )
            rb.pack(side=tk.LEFT, padx=4)

        tk.Label(
            top_bar,
            text='Notch:',
            bg=COLOR_PANEL,
            fg=COLOR_TEXT,
            font=('Segoe UI', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(14, 4))

        self._notch_var = tk.StringVar(value='50 Hz')
        cb_notch = ttk.Combobox(
            top_bar,
            textvariable=self._notch_var,
            values=['50 Hz', '60 Hz'],
            width=7,
            state='readonly',
        )
        cb_notch.pack(side=tk.LEFT, padx=(0, 12))
        cb_notch.bind('<<ComboboxSelected>>', self._on_notch_changed)

        self._btn_flash = tk.Button(
            top_bar,
            text='⚡ Flash micro:bit Firmware',
            bg=COLOR_SURFACE,
            fg=COLOR_YELLOW,
            font=('Segoe UI', 9, 'bold'),
            relief=tk.FLAT,
            padx=10,
            command=self._on_flash_clicked,
        )
        self._btn_flash.pack(side=tk.RIGHT)

        # 2. Status & Session Telemetry Header
        status_bar = tk.Frame(self.root, bg=COLOR_SURFACE, padx=12, pady=8)
        status_bar.pack(fill=tk.X, side=tk.TOP, pady=(1, 0))

        self._lbl_contact = tk.Label(
            status_bar,
            text='○ Disconnected',
            bg=COLOR_SURFACE,
            fg=COLOR_MUTED,
            font=('Segoe UI', 9, 'bold'),
        )
        self._lbl_contact.pack(side=tk.LEFT, padx=(0, 18))

        self._lbl_clock = tk.Label(
            status_bar,
            text='Disconnected | Select COM port & click Connect',
            bg=COLOR_SURFACE,
            fg=COLOR_MUTED,
            font=('Consolas', 9),
        )
        self._lbl_clock.pack(side=tk.LEFT, padx=(0, 18))

        self._lbl_session = tk.Label(
            status_bar,
            text='AUX Session: Standby (127.0.0.1:18920)',
            bg=COLOR_SURFACE,
            fg=COLOR_ACCENT,
            font=('Segoe UI', 9, 'bold'),
        )
        self._lbl_session.pack(side=tk.RIGHT)

        # 3. Live Metrics Strip
        metrics_bar = tk.Frame(self.root, bg=COLOR_BG, padx=14, pady=6)
        metrics_bar.pack(fill=tk.X, side=tk.TOP)

        self._lbl_headline = tk.Label(
            metrics_bar,
            text='Engagement: --',
            bg=COLOR_BG,
            fg=COLOR_TEXT,
            font=('Segoe UI', 11, 'bold'),
        )
        self._lbl_headline.pack(side=tk.LEFT)

        self._lbl_submetric = tk.Label(
            metrics_bar,
            text='',
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=('Segoe UI', 10),
        )
        self._lbl_submetric.pack(side=tk.RIGHT)

        # 4. Oscilloscope Canvas
        self._canvas = tk.Canvas(
            self.root,
            bg=COLOR_PANEL,
            highlightthickness=0,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True, padx=12, pady=(2, 12))

    def _bind_visibility_events(self) -> None:
        self.root.bind('<Unmap>', self._on_unmap)
        self.root.bind('<Map>', self._on_map)

    def _on_unmap(self, _event: tk.Event) -> None:
        self._window_mapped = False

    def _on_map(self, _event: tk.Event) -> None:
        self._window_mapped = True

    def is_window_visible(self) -> bool:
        """Return True only when the Studio window is mapped and not minimized."""
        if not self._window_mapped:
            return False
        try:
            state = self.root.state()
            if state in ('iconic', 'withdrawn'):
                return False
            return bool(self.root.winfo_viewable())
        except tk.TclError:
            return False

    def _on_aux_session_start(self, session_start_unix_sec: float, _meta: dict) -> None:
        self._serial_client.set_session_start_unix(session_start_unix_sec)

    def _refresh_ports(
        self,
        trigger_ble_scan: bool = True,
        auto_connect_ble: bool = False,
    ) -> Optional[str]:
        ports = list_serial_ports()
        mb_ports = [dev for dev, _desc, is_mb in ports if is_mb]
        other_ports = [dev for dev, _desc, is_mb in ports if not is_mb]
        hw_ports = mb_ports + list(self._ble_ports) + other_ports
        choices = hw_ports + ['BLE', 'SIMULATOR']
        self._cb_port['values'] = choices

        preferred_hw: Optional[str] = None
        if mb_ports:
            preferred_hw = mb_ports[0]
        elif self._ble_ports:
            preferred_hw = self._ble_ports[0]
        elif other_ports:
            preferred_hw = other_ports[0]

        current = self._port_var.get().strip()
        if not current or (current not in ('SIMULATOR', 'BLE') and current not in hw_ports):
            self._port_var.set(preferred_hw or '')

        if trigger_ble_scan and not self._ble_scan_in_progress:
            self._start_ble_scan(auto_connect_if_idle=auto_connect_ble)
        return preferred_hw

    def _start_ble_scan(self, auto_connect_if_idle: bool = False) -> None:
        self._ble_scan_in_progress = True
        try:
            self._btn_refresh.configure(text='Scanning...')
        except tk.TclError:
            pass

        def _scan_worker() -> None:
            found = scan_ble_spikerbits(timeout=1.6)
            if not self._closing:
                try:
                    self.root.after(
                        0, lambda: self._on_ble_scan_finished(found, auto_connect_if_idle)
                    )
                except tk.TclError:
                    pass

        threading.Thread(target=_scan_worker, daemon=True).start()

    def _on_ble_scan_finished(
        self,
        found: List[Tuple[str, str]],
        auto_connect_if_idle: bool,
    ) -> None:
        self._ble_scan_in_progress = False
        if self._closing:
            return
        self._btn_refresh.configure(text='Refresh')
        self._ble_ports = [entry for entry, _desc in found]
        self._refresh_ports(trigger_ble_scan=False, auto_connect_ble=False)

        if (
            auto_connect_if_idle
            and not self._serial_client.is_running
            and self._port_var.get().strip() != 'SIMULATOR'
            and self._ble_ports
        ):
            self._port_var.set(self._ble_ports[0])
            self._connect_selected(simulate=False)

    def _on_connect_clicked(self) -> None:
        selected = self._port_var.get().strip()
        if not selected:
            detected_hw = self._refresh_ports(trigger_ble_scan=True, auto_connect_ble=False)
            selected = self._port_var.get().strip() or (detected_hw or '')
        if not selected:
            messagebox.showwarning(
                'No Device Selected',
                'No micro:bit COM port or BLE Spiker:bit was detected yet.\n\n'
                'Connect via USB, power on battery BLE (or select BLE), or explicitly choose SIMULATOR.',
            )
            return
        self._connect_selected(simulate=(selected == 'SIMULATOR'))

    def _connect_selected(self, simulate: bool) -> None:
        selected = self._port_var.get().strip()
        is_sim = bool(simulate) and (selected == 'SIMULATOR')
        try:
            self._serial_client.start(port=selected, simulate=is_sim)
        except Exception as exc:
            self._serial_client.stop()
            messagebox.showerror('Serial Connection Error', str(exc))

    def _on_mode_changed(self) -> None:
        mode = self._mode_var.get()
        self._serial_client.set_mode(mode)
        self._session_mgr.set_spikerbit_mode(mode)

    def _on_notch_changed(self, _event: Optional[tk.Event] = None) -> None:
        hz = 60.0 if '60' in self._notch_var.get() else 50.0
        self._dsp.set_notch_frequency(hz)

    def _on_flash_clicked(self) -> None:
        drives = find_microbit_drives()
        if not drives:
            messagebox.showwarning(
                'micro:bit Not Found',
                'No MICROBIT USB drive was detected.\n\n'
                'Connect your BBC micro:bit via USB and try again.',
            )
            return

        was_hw_connected = (
            self._serial_client.is_running and not self._serial_client.is_simulating
        )
        prev_port = self._serial_client.port_name if was_hw_connected else ''
        if was_hw_connected:
            self._serial_client.stop()

        target_drive = drives[0]
        self._btn_flash.configure(state=tk.DISABLED, text='⏳ Flashing...')

        def _flash_worker() -> None:
            try:
                dest = flash_firmware(target_drive=target_drive, verify_remount=True)
                self.root.after(0, lambda: self._on_flash_finished(dest, None, was_hw_connected, prev_port))
            except Exception as exc:
                err_msg = str(exc)
                self.root.after(0, lambda: self._on_flash_finished('', err_msg, was_hw_connected, prev_port))

        threading.Thread(target=_flash_worker, daemon=True).start()

    def _on_flash_finished(
        self,
        dest: str,
        err_msg: Optional[str],
        was_hw_connected: bool,
        prev_port: str,
    ) -> None:
        self._btn_flash.configure(state=tk.NORMAL, text='⚡ Flash micro:bit Firmware')
        detected_hw = self._refresh_ports()
        if err_msg is not None:
            messagebox.showerror('Firmware Flash Failed', err_msg)
            return
        reconnect_port = prev_port if (was_hw_connected and prev_port) else detected_hw
        if reconnect_port and self._port_var.get().strip() != 'SIMULATOR':
            self._port_var.set(reconnect_port)
            self._connect_selected(simulate=False)
        messagebox.showinfo(
            'Firmware Flashed',
            f'Successfully flashed Spiker:bit 500 Hz DMA firmware to:\n{dest}',
        )

    def _schedule_ui_tick(self) -> None:
        self._ui_tick()
        self.root.after(40, self._schedule_ui_tick)  # 25 FPS when visible

    def _ui_tick(self) -> None:
        conn_err = self._serial_client.pop_connection_error()
        if conn_err:
            messagebox.showerror('Serial Connection Error', conn_err)

        # Strict visibility gating: zero DSP or canvas redraw when minimized/hidden
        if not self.is_window_visible():
            return

        ctx = self._session_mgr.context
        err_suffix = f' | Err: {ctx.last_upload_error}' if ctx.last_upload_error else ''
        if ctx.active:
            track = ctx.metadata.get('Track name', 'Track')
            driver = ctx.metadata.get('Driver name', 'Driver')
            self._lbl_session.configure(
                text=(
                    f'● LIVE SESSION: {track} ({driver}) | '
                    f'Uploaded: {ctx.segments_uploaded} seg ({ctx.bytes_uploaded // 1024} KB)'
                    f'{err_suffix}'
                ),
                fg=COLOR_RED if ctx.last_upload_error else COLOR_GREEN,
            )
        else:
            self._lbl_session.configure(
                text=(
                    f'○ AUX Standby (127.0.0.1:{self._session_mgr.bound_port}) | '
                    f'Uploaded: {ctx.segments_uploaded} seg{err_suffix}'
                ),
                fg=COLOR_RED if ctx.last_upload_error else COLOR_ACCENT,
            )

        if not self._serial_client.is_running:
            self._lbl_contact.configure(text='○ Disconnected', fg=COLOR_MUTED)
            self._lbl_clock.configure(
                text='Disconnected | Connect micro:bit & click Refresh / Connect'
            )
            self._lbl_headline.configure(text='Disconnected')
            self._lbl_submetric.configure(text='')
            empty_res = self._dsp.process_window([], mode=self._serial_client.mode)
            self._draw_oscilloscope(empty_res)
            return

        cc = self._serial_client.clock_controller.current_cc_ticks
        n_samp = self._serial_client.samples_received
        min_rtt = self._serial_client.clock_controller.min_rtt_ms
        rtt_str = f' | minRTT: {min_rtt:.1f} ms' if min_rtt is not None else ''
        if self._serial_client.is_simulating:
            src_tag = 'SIM'
        elif self._serial_client.is_ble:
            src_tag = f'BLE {self._serial_client.port_name.replace("BLE://", "")}'
        else:
            src_tag = f'USB {self._serial_client.port_name}'
        self._lbl_clock.configure(
            text=f'500 Hz ({src_tag}){rtt_str} | CC[0]: {cc} | Samples: {n_samp}'
        )

        if n_samp == 0:
            self._lbl_contact.configure(text='◌ Connecting...', fg=COLOR_ACCENT)
            self._lbl_headline.configure(text='Connecting to Spiker:bit...')
            self._lbl_submetric.configure(text='')
            return

        # Fetch last 4.0 seconds (2000 samples @ 500 Hz) for live oscilloscope
        _, adc_vals = self._serial_client.get_recent_samples(max_count=2000)
        res = self._dsp.process_window(adc_vals, mode=self._serial_client.mode)

        if res.contact_status == 'GOOD':
            self._lbl_contact.configure(text='● Contact: GOOD', fg=COLOR_GREEN)
        elif res.contact_status == 'RAILING':
            self._lbl_contact.configure(text='▲ Contact: RAILING', fg=COLOR_RED)
        else:
            self._lbl_contact.configure(text='◆ Contact: NOISY', fg=COLOR_YELLOW)

        self._lbl_headline.configure(text=res.headline_metric)
        self._lbl_submetric.configure(text=res.sub_metric)

        self._draw_oscilloscope(res)

    def _draw_oscilloscope(self, res) -> None:
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w < 40 or h < 80:
            return

        self._canvas.delete('all')
        lanes: List[Tuple[str, np.ndarray, str]] = [
            ('Raw 500 Hz Notch-Filtered (P1)', res.raw_centered, COLOR_ACCENT),
            (res.primary_label, res.primary_trace, COLOR_GREEN),
            (res.secondary_label, res.secondary_trace, COLOR_CYAN),
            (res.tertiary_label, res.tertiary_trace, COLOR_MAUVE),
        ]

        lane_h = float(h) / float(len(lanes))
        for idx, (label, trace, color) in enumerate(lanes):
            y_top = idx * lane_h
            y_mid = y_top + 0.5 * lane_h
            y_half = 0.45 * lane_h

            if idx > 0:
                self._canvas.create_line(
                    0, y_top, w, y_top, fill=COLOR_SURFACE, width=1
                )
            self._canvas.create_line(
                10, y_mid, w - 10, y_mid, fill='#252637', dash=(3, 3)
            )
            self._canvas.create_text(
                14,
                y_top + 12,
                text=label,
                anchor=tk.W,
                fill=color,
                font=('Segoe UI', 9, 'bold'),
            )

            coords = downsample_trace_for_canvas(trace, w, y_mid, y_half)
            if len(coords) >= 4:
                self._canvas.create_line(*coords, fill=color, width=1.5)

            if idx == 1 and res.blink_indices and trace.size > 1:
                n_pts = float(trace.size - 1)
                for b_idx in res.blink_indices:
                    bx = 10.0 + (float(b_idx) / n_pts) * float(w - 20)
                    self._canvas.create_oval(
                        bx - 3,
                        y_top + 6,
                        bx + 3,
                        y_top + 12,
                        fill=COLOR_YELLOW,
                        outline='',
                    )

    def _on_close(self) -> None:
        self._closing = True
        self._serial_client.stop()
        self._session_mgr.stop()
        self.root.destroy()


def main() -> None:
    """CLI entry point for the Spiker:bit 500 Hz AUX Studio application."""
    parser = argparse.ArgumentParser(
        description='Spiker:bit 500 Hz Neuro-Telemetry Studio & AUX Uploader'
    )
    parser.add_argument(
        '--aux-port',
        type=int,
        default=18920,
        help='Local HTTP port for CommandSink session signalling (default: 18920)',
    )
    parser.add_argument(
        '--serial-port',
        type=str,
        default=None,
        help='Serial port for micro:bit (e.g. COM3 or /dev/ttyACM0), or SIMULATOR',
    )
    parser.add_argument(
        '--backup-dir',
        type=str,
        default=None,
        help='Optional local directory to save .brt1.br Spiker:bit session backups',
    )
    args = parser.parse_args()

    explicit_sim = (
        args.serial_port is not None
        and args.serial_port.strip().upper() == 'SIMULATOR'
    )
    root = tk.Tk()
    SpikerbitStudioApp(
        root,
        aux_port=args.aux_port,
        initial_port=args.serial_port,
        simulate=explicit_sim,
        backup_dir=args.backup_dir,
    )
    root.mainloop()


if __name__ == '__main__':
    main()
