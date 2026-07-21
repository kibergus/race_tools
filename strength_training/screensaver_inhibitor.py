"""
Screensaver and Power Management Inhibitor module.
"""

import sys
import time
import subprocess
import atexit


class ScreensaverInhibitor:
    """Inhibits screensaver and display power saving while application is active."""
    def __init__(self, app_name: str = 'Fanatec Strength Training', reason: str = 'Workout in progress') -> None:
        self.app_name = app_name
        self.reason = reason
        self.dbus_cookie = None
        self.window_id = None
        self.last_ping_time = 0.0
        self.active = False
        atexit.register(self.stop)

    def start(self, window_id: int = None, win_id: int = None) -> None:
        if self.active:
            return
        self.active = True
        self.window_id = window_id if window_id is not None else win_id

        # 1. Windows support via ctypes
        if sys.platform == 'win32':
            try:
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ES_DISPLAY_REQUIRED = 0x00000002
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                )
            except Exception as e:
                print(f'Warning: SetThreadExecutionState failed: {e}')
            return

        # 2. Linux DBus org.freedesktop.ScreenSaver Inhibit
        try:
            cmd = [
                'dbus-send', '--session', '--dest=org.freedesktop.ScreenSaver',
                '--type=method_call', '--print-reply',
                '/org/freedesktop/ScreenSaver',
                'org.freedesktop.ScreenSaver.Inhibit',
                f'string:{self.app_name}', f'string:{self.reason}'
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if 'uint32' in line:
                        parts = line.strip().split()
                        self.dbus_cookie = int(parts[-1])
                        break
        except Exception:
            pass

        # 3. Linux xdg-screensaver suspend <window_id>
        if self.window_id:
            try:
                hex_win_id = hex(self.window_id)
                subprocess.run(['xdg-screensaver', 'suspend', hex_win_id], capture_output=True, timeout=2)
            except Exception:
                pass

        # Initial heartbeat ping
        self.heartbeat(force=True)

    def heartbeat(self, force: bool = False) -> None:
        """Periodic heartbeat to reset screensaver idle timers."""
        if not self.active:
            return
        now = time.time()
        if not force and (now - self.last_ping_time < 30.0):
            return
        self.last_ping_time = now

        if sys.platform != 'win32':
            for cmd in [['xset', 's', 'reset'], ['xdg-screensaver', 'reset']]:
                try:
                    subprocess.run(cmd, capture_output=True, timeout=1)
                except Exception:
                    pass

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False

        if sys.platform == 'win32':
            try:
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass
            return

        # Uninhibit DBus
        if self.dbus_cookie is not None:
            try:
                cmd = [
                    'dbus-send', '--session', '--dest=org.freedesktop.ScreenSaver',
                    '--type=method_call',
                    '/org/freedesktop/ScreenSaver',
                    'org.freedesktop.ScreenSaver.Uninhibit',
                    f'uint32:{self.dbus_cookie}'
                ]
                subprocess.run(cmd, capture_output=True, timeout=2)
            except Exception:
                pass
            self.dbus_cookie = None

        # Resume xdg-screensaver
        if self.window_id:
            try:
                hex_win_id = hex(self.window_id)
                subprocess.run(['xdg-screensaver', 'resume', hex_win_id], capture_output=True, timeout=2)
            except Exception:
                pass
            self.window_id = None
