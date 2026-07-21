#!/usr/bin/env python3
"""
Fanatec Wheel Strength Training App.

This app sets a static centering force on the steering wheel:
- When deflected > 15 degrees, it pulls to the center with max strength (configurable).
- Between -15 and 15 degrees, it gradually scales the force.
- Displays a fullscreen window with a 180-degree arc showing the target and current wheel positions.
- The target smoothly moves every 2 seconds by 5 degrees, and every 15 seconds it jumps to a random
  position between -180 and 180 degrees, interpolating over 5 seconds.
"""

import sys
import os
import json
import time
import threading
import math
import random
import argparse
import shlex
import subprocess
import signal
import tkinter as tk
import evdev
from evdev import ecodes, ff
import screeninfo

try:
    from Xlib import display as xdisplay, X as xX, protocol as xprotocol
except ImportError:
    xdisplay = None
    xX = None
    xprotocol = None

try:
    from strength_training.screensaver_inhibitor import ScreensaverInhibitor
except ImportError:
    from screensaver_inhibitor import ScreensaverInhibitor

DEFAULT_STRENGTH_CONFIG_PATH = os.path.expanduser('~/.brbr_strength.json')

# ==============================================================================
# CONFIGURATION CONSTANTS
# ==============================================================================
MAX_FORCE = 32767       # Peak FFB force (maximum is 32767)
FORCE_DIRECTION = 1     # 1 or -1 (set to -1 if force pushes away instead of pulling back)
STIFFNESS_ANGLE = 15.0  # Angle range (+- degrees) where force transition is gradual
DAMPING_COEF = 20      # Damping coefficient to prevent oscillations
LOOP_FREQUENCY = 200    # Force Feedback control loop frequency (Hz)

# Target Movement Timing
NORMAL_STEP_TIME = 2.0  # Seconds per small target step
LARGE_STEP_TIME = 5.0   # Seconds per large target shift
NORMAL_STEP_SIZE = 5.0  # Degrees per small step
LARGE_STEP_INTERVAL = 15.0  # Repeat interval for large shifts (10s small steps + 5s large step)
MIN_TARGET_ANGLE = -90.0
MAX_TARGET_ANGLE = 90.0


def is_valid_target_angle(angle: float, stiffness: float = STIFFNESS_ANGLE,
                          min_angle: float = MIN_TARGET_ANGLE, max_angle: float = MAX_TARGET_ANGLE) -> bool:
    """Check if target angle is within [min_angle, max_angle] and outside (-stiffness, stiffness)."""
    return (min_angle <= angle <= -stiffness) or (stiffness <= angle <= max_angle)


def pick_random_valid_target_angle(stiffness: float = STIFFNESS_ANGLE,
                                   min_angle: float = MIN_TARGET_ANGLE, max_angle: float = MAX_TARGET_ANGLE) -> float:
    """Pick a uniform random target angle outside (-stiffness, stiffness)."""
    if random.random() < 0.5:
        return random.uniform(min_angle, -stiffness)
    else:
        return random.uniform(stiffness, max_angle)


def get_descendant_pids(pid: int) -> set:
    """Recursively collect a process ID and all its descendant child PIDs."""
    pids = {pid}
    try:
        children_path = f'/proc/{pid}/task/{pid}/children'
        if os.path.exists(children_path):
            with open(children_path, 'r', encoding='utf-8') as f:
                for cpid in f.read().split():
                    try:
                        cpid_int = int(cpid)
                        pids.update(get_descendant_pids(cpid_int))
                    except ValueError:
                        pass
    except Exception:
        pass
    return pids


def get_monitor_geometry(index: int = 0) -> tuple:
    """Return (x, y, width, height) for the specified monitor index."""
    try:
        monitors = screeninfo.get_monitors()
        if monitors:
            idx = max(0, min(len(monitors) - 1, index))
            mon = monitors[idx]
            return mon.x, mon.y, mon.width, mon.height
    except Exception:
        pass
    return 0, 0, 1920, 1080


def calculate_window_offset(screen_width: int, deflection: float) -> int:
    """Calculate horizontal window pixel offset based on angle deflection.

    Inverted X axis:
    - 0 degrees deflection -> offset = 0 (centered)
    - +90 degrees deflection -> offset = -0.75 * screen_width (1/4 window left on screen)
    - -90 degrees deflection -> offset = +0.75 * screen_width (1/4 window left on screen)
    """
    ratio = max(-1.0, min(1.0, deflection / 90.0))
    return int(round(-0.75 * screen_width * ratio))


# ==============================================================================
# TARGET TRAJECTORY STATE MACHINE
# ==============================================================================
class TargetState:
    """Manages the moving target angle with smooth interpolation and state transitions."""
    def __init__(self, initial_angle: float = None) -> None:
        self.lock = threading.Lock()
        if initial_angle is not None and is_valid_target_angle(initial_angle):
            self.current_target_angle = initial_angle
        else:
            self.current_target_angle = STIFFNESS_ANGLE

        # Interpolation segment boundaries
        self.start_angle = self.current_target_angle
        self.end_angle = self.current_target_angle
        self.start_time = time.time()
        self.end_time = time.time()

        # Step phase counter
        # Steps 0, 1, 2, 3, 4: 2-second small alterations (5 degrees)
        # Step 5: 5-second large shift to random [-90, 90] (excluding deadzone)
        self.cycle_step = 0

    def update(self, current_time: float) -> float:
        with self.lock:
            # If the current interpolation segment has finished, transition to the next step
            if current_time >= self.end_time:
                self.start_time = self.end_time
                self.start_angle = self.end_angle

                if self.cycle_step < 5:
                    # Normal step: duration 2s, move +-5 degrees
                    self.end_time = self.start_time + NORMAL_STEP_TIME

                    # Randomly choose positive or negative step
                    delta = NORMAL_STEP_SIZE if random.random() < 0.5 else -NORMAL_STEP_SIZE

                    # Ensure candidate angle is valid (outside deadzone and within [-90, 90])
                    candidate = self.start_angle + delta
                    if is_valid_target_angle(candidate):
                        self.end_angle = candidate
                    elif is_valid_target_angle(self.start_angle - delta):
                        self.end_angle = self.start_angle - delta
                    else:
                        self.end_angle = pick_random_valid_target_angle()

                    self.cycle_step += 1
                else:
                    # Large shift: duration 5s, jump to random position between -90 and 90 (outside deadzone)
                    self.end_time = self.start_time + LARGE_STEP_TIME
                    self.end_angle = pick_random_valid_target_angle()
                    self.cycle_step = 0  # Reset cycle

            # Perform smooth cosine interpolation: (1 - cos(pi * t)) / 2
            duration = self.end_time - self.start_time
            if duration > 0:
                t = (current_time - self.start_time) / duration
                t = max(0.0, min(1.0, t))
                smooth_t = (1.0 - math.cos(math.pi * t)) / 2.0
                self.current_target_angle = self.start_angle + (self.end_angle - self.start_angle) * smooth_t
            else:
                self.current_target_angle = self.end_angle

            return self.current_target_angle


def load_strength_config(path: str = DEFAULT_STRENGTH_CONFIG_PATH) -> dict:
    """Load config dictionary from JSON file."""
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f'Warning: Could not load config from {path}: {e}')
    return {}


def save_strength_config(updates: dict, path: str = DEFAULT_STRENGTH_CONFIG_PATH) -> None:
    """Update and persist config dictionary to JSON file."""
    try:
        data = load_strength_config(path)
        data.update(updates)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f'Warning: Could not save config to {path}: {e}')


# ==============================================================================
# AUDIO FEEDBACK CONTROLLER
# ==============================================================================
class AudioFeedbackController:
    """Manages looping playback of audio cues with deflection-based volume scaling."""
    def __init__(self, audio_path: str = None, muted: bool = False) -> None:
        self.muted = muted
        if audio_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.audio_path = os.path.join(script_dir, 'hold_the_wheel.mp3')
        else:
            self.audio_path = audio_path

        self.initialized = False
        self.current_volume = 0.0
        self.init_audio()

    def init_audio(self) -> None:
        """Initialize pygame mixer and begin looping the audio track."""
        try:
            import pygame
            pygame.mixer.init()
            if os.path.exists(self.audio_path):
                pygame.mixer.music.load(self.audio_path)
                pygame.mixer.music.set_volume(0.0 if self.muted else self.current_volume)
                pygame.mixer.music.play(-1)
                self.initialized = True
            else:
                print(f'Warning: Audio file not found: {self.audio_path}')
        except Exception as e:
            print(f'Warning: Could not initialize audio system (pygame): {e}')
            self.initialized = False

    @staticmethod
    def calculate_volume(deflection: float) -> float:
        """Calculate volume [0.0, 1.0] from target deflection angle (degrees).

        - <= 2.0 degrees: 0% volume (0.0)
        - >= 10.0 degrees: 100% volume (1.0)
        - 2.0 to 10.0 degrees: linear scale from 0.0 to 1.0
        """
        d = abs(deflection)
        if d <= 2.0:
            return 0.0
        elif d >= 10.0:
            return 1.0
        else:
            return (d - 2.0) / (10.0 - 2.0)

    def update_volume_for_deflection(self, deflection: float) -> float:
        """Update playback volume based on current deflection and mute status."""
        vol = self.calculate_volume(deflection)
        effective_vol = 0.0 if self.muted else vol
        self.set_volume(effective_vol)
        return effective_vol

    def set_volume(self, volume: float) -> None:
        """Set the active playback volume clamped between 0.0 and 1.0."""
        self.current_volume = max(0.0, min(1.0, float(volume)))
        if self.initialized:
            try:
                import pygame
                pygame.mixer.music.set_volume(self.current_volume)
            except Exception:
                pass

    def toggle_mute(self) -> bool:
        """Toggle mute state and update output volume immediately."""
        self.muted = not self.muted
        if self.muted:
            self.set_volume(0.0)
        return self.muted

    def set_muted(self, muted: bool) -> None:
        """Explicitly set mute state."""
        self.muted = bool(muted)
        if self.muted:
            self.set_volume(0.0)

    def stop(self) -> None:
        """Stop playback and cleanup mixer resources."""
        if self.initialized:
            try:
                import pygame
                pygame.mixer.music.stop()
                pygame.mixer.quit()
            except Exception:
                pass
            self.initialized = False


# ==============================================================================
# FANATEC FFB CONTROLLER
# ==============================================================================
class FanatecFFBController:
    """Handles communication with the Fanatec wheel and runs the FFB loop."""
    def __init__(self, config_path: str = None) -> None:
        self.dev = None
        self.running = False
        self.lock = threading.Lock()

        # Config path for strength persistence
        self.config_path = config_path or DEFAULT_STRENGTH_CONFIG_PATH

        # Shared state between threads
        self.raw_position = 32768
        self.abs_min = 0
        self.abs_max = 65535
        self.hw_range_deg = 360.0
        self.last_force = 0.0
        self.target_angle = 0.0
        self.force_scale = 1.0  # Range 0.0 to 1.0 (adjustable in steps of 0.1)

        # Internals for the FFB loop
        self.last_angle = None
        self.last_time = None
        self.filtered_velocity = 0.0
        self.effect_id = None

        self.input_thread = None
        self.control_thread = None

        # Load persisted strength setting if present
        self.load_strength()

    def load_strength(self, path: str = None) -> float:
        """Load the strength setting from JSON configuration file."""
        config_path = path or self.config_path
        data = load_strength_config(config_path)
        if isinstance(data, dict):
            val = data.get('strength', data.get('force_scale'))
            if val is not None:
                try:
                    val = float(val)
                    if val > 1.0:
                        val = val / 100.0
                    val = max(0.0, min(1.0, round(val, 2)))
                    with self.lock:
                        self.force_scale = val
                    return val
                except (ValueError, TypeError):
                    pass
        return self.force_scale

    def save_strength(self, path: str = None) -> None:
        """Save current strength setting to JSON configuration file."""
        config_path = path or self.config_path
        with self.lock:
            val = self.force_scale
        save_strength_config({'strength': val, 'force_scale': val}, config_path)

    def set_force_scale(self, scale: float) -> float:
        """Set force scaling factor clamped between 0.0 and 1.0."""
        with self.lock:
            self.force_scale = max(0.0, min(1.0, round(scale, 2)))
            val = self.force_scale
        self.save_strength()
        return val

    def adjust_force_scale(self, delta: float) -> float:
        """Adjust force scaling factor by a delta (clamped between 0.0 and 1.0)."""
        with self.lock:
            self.force_scale = max(0.0, min(1.0, round(self.force_scale + delta, 2)))
            val = self.force_scale
        self.save_strength()
        return val

    def get_current_angle(self) -> float:
        """Calculate and return the current steering wheel angle in degrees relative to center."""
        with self.lock:
            if not self.dev:
                return 0.0
            raw_pos = self.raw_position
            center_val = (self.abs_min + self.abs_max) / 2.0
            half_range = (self.abs_max - self.abs_min) / 2.0
            if half_range > 0:
                return ((raw_pos - center_val) / half_range) * (self.hw_range_deg / 2.0)
            return 0.0

    def find_devices(self) -> bool:
        """Scan system for a Fanatec wheel supporting force feedback."""
        print('Scanning for Fanatec FFB devices...')
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                if 'fanatec' in dev.name.lower() and ecodes.EV_FF in dev.capabilities():
                    self.dev = dev
                    print(f'Found Fanatec FFB device: {dev.path} ({dev.name})')

                    # Read ABS_X calibration limits
                    caps = dev.capabilities()
                    if ecodes.EV_ABS in caps:
                        for abs_code, abs_info in caps[ecodes.EV_ABS]:
                            if abs_code == ecodes.ABS_X:
                                self.abs_min = abs_info.min
                                self.abs_max = abs_info.max
                                self.raw_position = abs_info.value
                                print(f'ABS_X: min={self.abs_min}, max={self.abs_max}, current={self.raw_position}')
                    return True
            except Exception:
                continue
        print('No Fanatec FFB device detected. Starting in DEMO MODE.')
        return False

    def read_hardware_range(self) -> None:
        """Read the hardware steering rotation range from sysfs (Linux driver specific)."""
        try:
            event_name = os.path.basename(self.dev.path)
            sysfs_range_path = f'/sys/class/input/{event_name}/device/device/range'
            with open(sysfs_range_path, 'r') as f:
                self.hw_range_deg = float(f.read().strip())
            print(f'Hardware steering range: {self.hw_range_deg} degrees.')
        except Exception as e:
            print(f'Warning: Could not read hardware range from sysfs: {e}. Defaulting to 360.0°.')
            self.hw_range_deg = 360.0

    def init_ffb(self) -> None:
        """Set up FFB limits and disable autocenter."""
        self.dev.write(ecodes.EV_FF, ecodes.FF_GAIN, 0xFFFF)
        self.dev.write(ecodes.EV_FF, ecodes.FF_AUTOCENTER, 0)
        self.effect_id = None
        print('FFB initialized (gain=max, autocenter=off).')

    def start(self) -> None:
        """Start background processing threads if a wheel is connected."""
        if not self.dev:
            return

        self.running = True

        # Start evdev input event reader thread
        self.input_thread = threading.Thread(target=self.input_thread_func, daemon=True)
        self.input_thread.start()

        # Start FFB loop thread
        self.control_thread = threading.Thread(target=self.control_loop, daemon=True)
        self.control_thread.start()

    def input_thread_func(self) -> None:
        """Thread to continuously read raw steering wheel position events."""
        try:
            for event in self.dev.read_loop():
                if not self.running:
                    break
                if event.type == ecodes.EV_ABS and event.code == ecodes.ABS_X:
                    with self.lock:
                        self.raw_position = event.value
        except Exception as e:
            print(f'Input reader thread encountered error: {e}')
            self.running = False

    def update_ffb(self, force: float) -> None:
        """Upload a constant-force FFB command.

        Note: The hid-fanatecff driver uploads and plays fresh effects per-loop
        to circumvent single-effect overwrite limitations.
        """
        try:
            force_val = int(max(-32768, min(32767, force)))
            loop_ms = int(1000 / LOOP_FREQUENCY) + 5

            e = ff.Effect()
            e.type = ecodes.FF_CONSTANT
            e.id = -1  # Allocate new effect ID
            e.direction = 0xC000
            e.ff_trigger.button = 0
            e.ff_trigger.interval = 0
            e.ff_replay.length = loop_ms
            e.ff_replay.delay = 0
            e.u.ff_constant_effect.level = force_val

            old_id = self.effect_id
            self.effect_id = self.dev.upload_effect(e)
            self.dev.write(ecodes.EV_FF, self.effect_id, 1)

            # Erase the previous effect to prevent hardware memory leaking
            if old_id is not None:
                try:
                    self.dev.erase_effect(old_id)
                except Exception:
                    pass
        except Exception as e:
            print(f'FFB update failed: {e}')
            self.running = False

    def control_loop(self) -> None:
        """High-frequency FFB control loop (200Hz)."""
        dt = 1.0 / LOOP_FREQUENCY
        self.last_time = time.time()
        self.last_angle = None

        while self.running:
            start_time = time.time()

            # 1. Safely read shared position
            with self.lock:
                raw_pos = self.raw_position

            # 2. Convert raw value to degrees relative to center
            center_val = (self.abs_min + self.abs_max) / 2.0
            half_range = (self.abs_max - self.abs_min) / 2.0
            if half_range > 0:
                angle = ((raw_pos - center_val) / half_range) * (self.hw_range_deg / 2.0)
            else:
                angle = 0.0

            # 3. Low-pass filter velocity for damping calculation
            t = time.time()
            elapsed = t - self.last_time
            self.last_time = t

            if self.last_angle is not None and elapsed > 0:
                raw_velocity = (angle - self.last_angle) / elapsed
                alpha = 0.4
                self.filtered_velocity = alpha * raw_velocity + (1.0 - alpha) * self.filtered_velocity
            else:
                self.filtered_velocity = 0.0

            self.last_angle = angle

            # 4. Calculate spring force (max force beyond 15 deg, linear transition inside)
            with self.lock:
                effective_max_force = MAX_FORCE * self.force_scale

            if angle > STIFFNESS_ANGLE:
                spring_force = -effective_max_force
            elif angle < -STIFFNESS_ANGLE:
                spring_force = effective_max_force
            else:
                spring_force = -effective_max_force * (angle / STIFFNESS_ANGLE)

            # 5. Apply opposing damping force
            damping_force = -self.filtered_velocity * DAMPING_COEF

            # Total force with direction multiplier
            total_force = (spring_force + damping_force) * FORCE_DIRECTION
            total_force = max(-32768.0, min(32767.0, total_force))

            # 6. Apply FFB
            self.update_ffb(total_force)

            with self.lock:
                self.last_force = total_force

            # Throttle loop to target frequency
            compute_time = time.time() - start_time
            sleep_time = max(0, dt - compute_time)
            time.sleep(sleep_time)

    def clean_up(self) -> None:
        """Zero forces and clean up FFB effects before exiting."""
        print('Cleaning up FFB controller...')
        self.running = False
        if self.dev:
            try:
                self.update_ffb(0)
                if self.effect_id is not None:
                    self.dev.erase_effect(self.effect_id)
            except Exception as e:
                print(f'Error clearing FFB: {e}')


# ==============================================================================
# TKINTER GRAPHICAL USER INTERFACE
# ==============================================================================
class StrengthTrainingApp:
    """Renders the fullscreen training application interface."""
    def __init__(self, controller: FanatecFFBController, monitor_index: int = None) -> None:
        self.controller = controller
        self.session_start_time = time.time()
        self.total_error = 0.0
        self.error_samples = 0
        self.avg_error = 0.0

        # Load configuration (strength, audio mute, screen)
        cfg = load_strength_config(self.controller.config_path)
        if monitor_index is None:
            saved_screen = cfg.get('screen', cfg.get('monitor_index', 0))
            try:
                self.current_monitor_index = int(saved_screen)
            except (ValueError, TypeError):
                self.current_monitor_index = 0
        else:
            self.current_monitor_index = int(monitor_index)

        self.root = tk.Tk()
        self.root.title('Fanatec Strength Training Dashboard')

        # Configure Window Dimensions & Fullscreen
        self.x_offset, self.y_offset, self.width, self.height = get_monitor_geometry(self.current_monitor_index)

        self.root.geometry(f'{self.width}x{self.height}+{self.x_offset}+{self.y_offset}')
        self.root.attributes('-fullscreen', True)
        self.root.config(bg='#0f172a', cursor='none')

        # Ensure window receives input focus
        self.root.focus_force()

        # Canvas Setup
        self.canvas = tk.Canvas(self.root, width=self.width, height=self.height, bg='#0f172a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Audio feedback controller initialization & config persistence
        self.audio_muted = bool(cfg.get('audio_muted', False))
        self.audio_controller = AudioFeedbackController(muted=self.audio_muted)

        # Binds
        self.root.bind_all('<Escape>', self.exit_app)
        self.root.bind_all('q', self.exit_app)
        self.root.bind_all('Q', self.exit_app)
        self.root.bind_all('[', self.decrease_force)
        self.root.bind_all(']', self.increase_force)
        self.root.bind_all('<bracketleft>', self.decrease_force)
        self.root.bind_all('<bracketright>', self.increase_force)
        self.root.bind_all('r', self.reset_stats)
        self.root.bind_all('R', self.reset_stats)
        self.root.bind_all('v', self.toggle_visual_mode)
        self.root.bind_all('V', self.toggle_visual_mode)
        self.root.bind_all('s', self.switch_screen)
        self.root.bind_all('S', self.switch_screen)
        self.root.bind_all('m', self.toggle_audio_mute)
        self.root.bind_all('M', self.toggle_audio_mute)

        self.visual_mode = 'cat_arrow'

        # Demo Mode settings
        if not self.controller.dev:
            self.root.bind('<Motion>', self.mouse_motion)
            self.emulated_angle = 0.0

        # Initialize Target State Generator
        self.target_state = TargetState()

        # Calculate responsive layout coordinates
        self.recalculate_layout()

        # Initialize Screensaver Inhibitor
        self.inhibitor = ScreensaverInhibitor()
        try:
            self.root.update_idletasks()
            win_id = self.root.winfo_id()
        except Exception:
            win_id = None
        self.inhibitor.start(window_id=win_id)

        self.build_gui()
        self.update_gui()

    def recalculate_layout(self) -> None:
        """Calculate responsive UI scaling factors and component coordinates."""
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if isinstance(w, int) and w > 100:
                self.width = w
            if isinstance(h, int) and h > 100:
                self.height = h
        except Exception:
            pass

        self.k = min(self.width * 0.92 / 850.0, self.height * 0.90 / 720.0)
        self.R = 360.0 * self.k
        self.Cx = self.width / 2.0
        content_h = self.R + 390.0 * self.k
        top_margin = max(10.0, (self.height - content_h) / 2.0)
        self.header_y = top_margin + 20.0 * self.k
        self.subtitle_y = self.header_y + 45.0 * self.k
        self.label_0deg_y = self.subtitle_y + 45.0 * self.k
        self.arc_top = self.label_0deg_y + 35.0 * self.k
        self.Cy = self.arc_top + self.R

    def toggle_audio_mute(self, event: tk.Event = None) -> None:
        """Toggle audio feedback cue muting and persist setting in config."""
        self.audio_muted = self.audio_controller.toggle_mute()
        save_strength_config({'audio_muted': self.audio_muted}, self.controller.config_path)

    def reset_stats(self, event: tk.Event = None) -> None:
        """Reset accumulated session error and average deviation statistics."""
        self.total_error = 0.0
        self.error_samples = 0
        self.avg_error = 0.0

    def toggle_visual_mode(self, event: tk.Event = None) -> None:
        """Cycle visual mode between Cat Arrow (default), Regular, and Full Cat."""
        modes = ['cat_arrow', 'regular', 'cat']
        if self.visual_mode in modes:
            idx = modes.index(self.visual_mode)
            self.visual_mode = modes[(idx + 1) % len(modes)]
        else:
            self.visual_mode = 'cat_arrow'

    def decrease_force(self, event: tk.Event = None) -> None:
        """Decrease FFB max force level by 10%."""
        self.controller.adjust_force_scale(-0.1)

    def increase_force(self, event: tk.Event = None) -> None:
        """Increase FFB max force level by 10%."""
        self.controller.adjust_force_scale(0.1)

    def switch_screen(self, event: tk.Event = None) -> None:
        """Switch application fullscreen window to the next monitor in a multiscreen configuration."""
        try:
            monitors = screeninfo.get_monitors()
        except Exception:
            monitors = []

        if not monitors or len(monitors) <= 1:
            return

        self.current_monitor_index = (getattr(self, 'current_monitor_index', 0) + 1) % len(monitors)
        save_strength_config({'screen': self.current_monitor_index}, self.controller.config_path)
        self.x_offset, self.y_offset, self.width, self.height = get_monitor_geometry(self.current_monitor_index)

        self.root.withdraw()
        self.root.attributes('-fullscreen', False)
        self.root.geometry(f'{self.width}x{self.height}+{self.x_offset}+{self.y_offset}')
        self.root.deiconify()
        self.root.focus_force()
        self.root.attributes('-fullscreen', True)

        self._finish_screen_switch()
        if hasattr(self.root, 'after'):
            self.root.after(50, self._finish_screen_switch)

    def _finish_screen_switch(self) -> None:
        """Finalize window dimension updates and rebuild canvas UI elements after fullscreen transition."""
        self.recalculate_layout()
        if hasattr(self, 'canvas') and self.canvas:
            self.canvas.config(width=self.width, height=self.height)
            self.canvas.delete('all')
            self.build_gui()

    def build_gui(self) -> None:
        """Create static canvas components and initialize dynamic elements."""
        k = getattr(self, 'k', 1.0)

        # Session Timer Header
        self.timer_text = self.canvas.create_text(
            self.Cx, self.header_y,
            text='WORKOUT TIME: 00:00',
            fill='#38bdf8', font=('Inter', max(14, int(18 * k)), 'bold'), anchor='center'
        )
        self.canvas.create_text(
            self.Cx, self.subtitle_y,
            text='FIGHT THE CENTERING SPRING TO MATCH THE TARGET INDICATOR',
            fill='#64748b', font=('Inter', max(7, int(8.5 * k)), 'bold'), anchor='center'
        )

        # Background Arc Track
        self.canvas.create_arc(
            self.Cx - self.R, self.Cy - self.R,
            self.Cx + self.R, self.Cy + self.R,
            start=0, extent=180, style='arc', width=max(8, int(16 * k)), outline='#1e293b'
        )

        # Subtle inner gauge border
        self.canvas.create_arc(
            self.Cx - (self.R - 10 * k), self.Cy - (self.R - 10 * k),
            self.Cx + (self.R - 10 * k), self.Cy + (self.R - 10 * k),
            start=0, extent=180, style='arc', width=1, outline='#334155'
        )

        # Render Dial Ticks and Labels (-90° to +90° wheel angle across 180° visual arc)
        for deg in range(-90, 91, 15):
            phi = 90.0 - deg
            rad = math.radians(phi)

            is_major = (deg % 30 == 0)
            tick_len = int(10 * k) if is_major else int(5 * k)

            # Tick lines
            x1 = self.Cx + (self.R - tick_len) * math.cos(rad)
            y1 = self.Cy - (self.R - tick_len) * math.sin(rad)
            x2 = self.Cx + (self.R + tick_len) * math.cos(rad)
            y2 = self.Cy - (self.R + tick_len) * math.sin(rad)
            self.canvas.create_line(x1, y1, x2, y2, fill='#475569', width=max(1, int(2 * k)) if is_major else 1)

            # Tick labels
            if is_major:
                lx = self.Cx + (self.R + 24 * k) * math.cos(rad)
                ly = self.Cy - (self.R + 24 * k) * math.sin(rad)
                deg_str = f'{deg:+d}°' if deg != 0 else '0°'
                self.canvas.create_text(
                    lx, ly, text=deg_str, fill='#475569', font=('Inter', max(6, int(8.0 * k)), 'bold'), anchor='center'
                )

        # Deadzone boundary lines (-STIFFNESS_ANGLE and STIFFNESS_ANGLE)
        for deadzone_deg in (-STIFFNESS_ANGLE, STIFFNESS_ANGLE):
            rad = math.radians(90.0 - deadzone_deg)
            dx1 = self.Cx + (self.R - 12 * k) * math.cos(rad)
            dy1 = self.Cy - (self.R - 12 * k) * math.sin(rad)
            dx2 = self.Cx + (self.R + 12 * k) * math.cos(rad)
            dy2 = self.Cy - (self.R + 12 * k) * math.sin(rad)
            self.canvas.create_line(dx1, dy1, dx2, dy2, fill='#f59e0b', width=max(1, int(2 * k)), dash=(3, 3))

        # Static Panels (5 rounded telemetry cards with generous width and spacing)
        cards_total_width = min(self.width * 0.90, max(self.R * 2.2, 1000.0 * k))
        spacing = cards_total_width / 4.0
        panel_w = spacing - 24.0 * k
        panel_y = self.Cy + 85.0 * k
        panel_h = 75.0 * k

        # Panel 1: Target Angle (Far Left)
        px1 = self.Cx - 2 * spacing
        self.canvas.create_rectangle(
            px1 - panel_w / 2, panel_y, px1 + panel_w / 2, panel_y + panel_h,
            fill='#1e293b', outline='#334155', width=max(1, int(2 * k))
        )
        self.canvas.create_text(
            px1, panel_y + 18.0 * k, text='TARGET ANGLE', fill='#64748b', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='center'
        )
        self.target_text = self.canvas.create_text(
            px1, panel_y + 48.0 * k, text='0.0°', fill='#10b981', font=('Inter', max(11, int(15.0 * k)), 'bold'), anchor='center'
        )

        # Panel 2: Wheel Angle (Inner Left)
        px2 = self.Cx - spacing
        self.canvas.create_rectangle(
            px2 - panel_w / 2, panel_y, px2 + panel_w / 2, panel_y + panel_h,
            fill='#1e293b', outline='#334155', width=max(1, int(2 * k))
        )
        self.canvas.create_text(
            px2, panel_y + 18.0 * k, text='WHEEL ANGLE', fill='#64748b', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='center'
        )
        self.wheel_text = self.canvas.create_text(
            px2, panel_y + 48.0 * k, text='0.0°', fill='#f43f5e', font=('Inter', max(11, int(15.0 * k)), 'bold'), anchor='center'
        )

        # Panel 3: Alignment Error (Center)
        px3 = self.Cx
        self.canvas.create_rectangle(
            px3 - panel_w / 2, panel_y, px3 + panel_w / 2, panel_y + panel_h,
            fill='#1e293b', outline='#334155', width=max(1, int(2 * k))
        )
        self.canvas.create_text(
            px3, panel_y + 18.0 * k, text='ALIGN ERROR', fill='#64748b', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='center'
        )
        self.error_text = self.canvas.create_text(
            px3, panel_y + 48.0 * k, text='0.0°', fill='#ffffff', font=('Inter', max(11, int(15.0 * k)), 'bold'), anchor='center'
        )

        # Panel 4: Avg Deviation (Inner Right)
        px4 = self.Cx + spacing
        self.canvas.create_rectangle(
            px4 - panel_w / 2, panel_y, px4 + panel_w / 2, panel_y + panel_h,
            fill='#1e293b', outline='#334155', width=max(1, int(2 * k))
        )
        self.canvas.create_text(
            px4, panel_y + 18.0 * k, text='AVG ERROR', fill='#64748b', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='center'
        )
        self.avg_error_text = self.canvas.create_text(
            px4, panel_y + 48.0 * k, text='0.0°', fill='#38bdf8', font=('Inter', max(11, int(15.0 * k)), 'bold'), anchor='center'
        )

        # Panel 5: Max Force Level (Far Right)
        px5 = self.Cx + 2 * spacing
        self.canvas.create_rectangle(
            px5 - panel_w / 2, panel_y, px5 + panel_w / 2, panel_y + panel_h,
            fill='#1e293b', outline='#334155', width=max(1, int(2 * k))
        )
        self.canvas.create_text(
            px5, panel_y + 18.0 * k, text='MAX FORCE', fill='#64748b', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='center'
        )
        self.force_scale_text = self.canvas.create_text(
            px5, panel_y + 48.0 * k, text='100%', fill='#38bdf8', font=('Inter', max(11, int(15.0 * k)), 'bold'), anchor='center'
        )

        # Force bar background
        bar_y = panel_y + panel_h + 30.0 * k
        bar_w = min(self.width * 0.5, 450.0 * k)
        self.canvas.create_rectangle(
            self.Cx - bar_w / 2, bar_y - 6 * k, self.Cx + bar_w / 2, bar_y + 6 * k, fill='#1e293b', outline='#334155'
        )
        # Center line marker
        self.canvas.create_line(self.Cx, bar_y - 10 * k, self.Cx, bar_y + 10 * k, fill='#475569', width=max(1, int(2 * k)))
        # Active force indicator
        self.force_bar = self.canvas.create_rectangle(
            self.Cx, bar_y - 4 * k, self.Cx, bar_y + 4 * k, fill='#38bdf8', outline=''
        )
        self.bar_y = bar_y
        self.bar_w = bar_w

        # Text labels below force bar
        self.canvas.create_text(
            self.Cx - bar_w / 2, bar_y + 20.0 * k, text='PULL LEFT', fill='#475569', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='w'
        )
        self.canvas.create_text(
            self.Cx + bar_w / 2, bar_y + 20.0 * k, text='PULL RIGHT', fill='#475569', font=('Inter', max(5, int(6.0 * k)), 'bold'), anchor='e'
        )

        # Status text footer (2-line clean layout)
        status_y = bar_y + 44.0 * k
        self.status_text_dev = self.canvas.create_text(
            self.Cx, status_y, text='', fill='#64748b', font=('Inter', max(7, min(10, int(7.5 * k)))), anchor='center'
        )
        self.status_text_hint = self.canvas.create_text(
            self.Cx, status_y + 20.0 * k, text='', fill='#475569', font=('Inter', max(6, min(9, int(7.0 * k)))), anchor='center'
        )
        self.status_text = self.status_text_dev

        # Create Indicators
        # Target needle
        self.target_needle = self.canvas.create_line(
            self.Cx, self.Cy, self.Cx, self.Cy, fill='#10b981', width=max(2, int(3 * k)), dash=(5, 3)
        )
        # Target glowing marker (concentric circles)
        self.target_glow_outer = self.canvas.create_oval(0, 0, 0, 0, fill='#022c22', outline='')
        self.target_glow_inner = self.canvas.create_oval(0, 0, 0, 0, fill='#064e3b', outline='')
        self.target_core = self.canvas.create_oval(0, 0, 0, 0, fill='#10b981', outline='')

        # Wheel position needle
        self.wheel_needle = self.canvas.create_line(
            self.Cx, self.Cy, self.Cx, self.Cy, fill='#f43f5e', width=max(2, int(4 * k))
        )
        # Wheel glowing marker (concentric circles)
        self.wheel_glow_outer = self.canvas.create_oval(0, 0, 0, 0, fill='#450a0a', outline='')
        self.wheel_glow_inner = self.canvas.create_oval(0, 0, 0, 0, fill='#881337', outline='')
        self.wheel_core = self.canvas.create_oval(0, 0, 0, 0, fill='#f43f5e', outline='')

        # Cat Arrow visual elements (cat ears, whiskers, eyes on wheel arrow head circle)
        self.cat_arrow_items = {}
        self.cat_arrow_items['ear_l'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, fill='#f43f5e', outline='#881337', width=max(1, int(1 * k))
        )
        self.cat_arrow_items['inner_ear_l'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, fill='#f472b6', outline=''
        )
        self.cat_arrow_items['ear_r'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, fill='#f43f5e', outline='#881337', width=max(1, int(1 * k))
        )
        self.cat_arrow_items['inner_ear_r'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, fill='#f472b6', outline=''
        )
        self.cat_arrow_items['eye_l'] = self.canvas.create_oval(
            0, 0, 0, 0, fill='#facc15', outline='#ca8a04', width=max(1, int(1 * k))
        )
        self.cat_arrow_items['pupil_l'] = self.canvas.create_line(0, 0, 0, 0, fill='#0f172a', width=max(1, int(2 * k)))
        self.cat_arrow_items['eye_r'] = self.canvas.create_oval(
            0, 0, 0, 0, fill='#facc15', outline='#ca8a04', width=max(1, int(1 * k))
        )
        self.cat_arrow_items['pupil_r'] = self.canvas.create_line(0, 0, 0, 0, fill='#0f172a', width=max(1, int(2 * k)))
        self.cat_arrow_items['nose'] = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, fill='#f472b6', outline='')
        self.cat_arrow_items['whisker_l1'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_arrow_items['whisker_l2'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_arrow_items['whisker_l3'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_arrow_items['whisker_r1'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_arrow_items['whisker_r2'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_arrow_items['whisker_r3'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))

        for item in self.cat_arrow_items.values():
            self.canvas.itemconfig(item, state='hidden')

        # Cat mode visual elements (running on outer side of arc)
        self.cat_items = {}
        self.cat_items['whisker1'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_items['whisker2'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_items['whisker3'] = self.canvas.create_line(0, 0, 0, 0, fill='#e2e8f0', width=max(1, int(1.5 * k)))
        self.cat_items['tail'] = self.canvas.create_line(
            0, 0, 0, 0, 0, 0, 0, 0, fill='#94a3b8', width=max(2, int(4 * k)), capstyle='round', smooth=True
        )
        self.cat_items['leg_bl'] = self.canvas.create_line(0, 0, 0, 0, fill='#64748b', width=max(2, int(5 * k)), capstyle='round')
        self.cat_items['leg_fl'] = self.canvas.create_line(0, 0, 0, 0, fill='#64748b', width=max(2, int(5 * k)), capstyle='round')
        self.cat_items['leg_br'] = self.canvas.create_line(0, 0, 0, 0, fill='#94a3b8', width=max(2, int(5 * k)), capstyle='round')
        self.cat_items['leg_fr'] = self.canvas.create_line(0, 0, 0, 0, fill='#94a3b8', width=max(2, int(5 * k)), capstyle='round')
        self.cat_items['body'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            fill='#94a3b8', outline='#475569', width=max(1, int(1.5 * k)), smooth=True
        )
        self.cat_items['head'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            fill='#94a3b8', outline='#475569', width=max(1, int(1.5 * k)), smooth=True
        )
        self.cat_items['ear_l'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, fill='#94a3b8', outline='#475569', width=max(1, int(1 * k))
        )
        self.cat_items['inner_ear_l'] = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, fill='#f472b6', outline='')
        self.cat_items['ear_r'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, fill='#94a3b8', outline='#475569', width=max(1, int(1 * k))
        )
        self.cat_items['inner_ear_r'] = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, fill='#f472b6', outline='')
        self.cat_items['eye'] = self.canvas.create_polygon(
            0, 0, 0, 0, 0, 0, 0, 0, fill='#facc15', outline='#ca8a04', width=max(1, int(1 * k)), smooth=True
        )
        self.cat_items['pupil'] = self.canvas.create_line(0, 0, 0, 0, fill='#0f172a', width=max(1, int(2 * k)))
        self.cat_items['nose'] = self.canvas.create_polygon(0, 0, 0, 0, 0, 0, fill='#fb7185', outline='')

        for item in self.cat_items.values():
            self.canvas.itemconfig(item, state='hidden')

        # Center Dial Hub (sleek central ring)
        self.canvas.create_oval(
            self.Cx - 14 * k, self.Cy - 14 * k, self.Cx + 14 * k, self.Cy + 14 * k,
            fill='#1e293b', outline='#475569', width=max(1, int(2 * k))
        )
        self.canvas.create_oval(
            self.Cx - 6 * k, self.Cy - 6 * k, self.Cx + 6 * k, self.Cy + 6 * k,
            fill='#0f172a', outline=''
        )

    def mouse_motion(self, event: tk.Event) -> None:
        """Handle mouse movement for steering emulation in Demo Mode."""
        if self.width > 0:
            normalized_x = max(0.0, min(1.0, event.x / float(self.width)))
            self.emulated_angle = normalized_x * 180.0 - 90.0

    def _update_cat_arrow_coords(self, wx: float, wy: float, wheel_angle: float) -> None:
        """Update coordinates of cat ears, eyes, nose, and whiskers attached to the arrow head circle."""
        k = getattr(self, 'k', 1.0)
        rad = math.radians(90.0 - wheel_angle)
        u_rad_x = math.cos(rad)
        u_rad_y = -math.sin(rad)
        u_tan_x = math.sin(rad)
        u_tan_y = math.cos(rad)

        def to_canvas(df: float, dn: float) -> tuple[float, float]:
            return (wx + df * u_tan_x + dn * u_rad_x, wy + df * u_tan_y + dn * u_rad_y)

        def flatten(pts: list[tuple[float, float]]) -> list[float]:
            res = []
            for df, dn in pts:
                cx, cy = to_canvas(df, dn)
                res.extend((cx, cy))
            return res

        # Ears (proportional size)
        ear_l_pts = [(-12 * k, 6 * k), (-2 * k, 12 * k), (-13 * k, 26 * k)]
        inner_ear_l_pts = [(-10 * k, 8 * k), (-3 * k, 12 * k), (-12 * k, 23 * k)]
        ear_r_pts = [(2 * k, 12 * k), (12 * k, 6 * k), (13 * k, 26 * k)]
        inner_ear_r_pts = [(3 * k, 12 * k), (10 * k, 8 * k), (12 * k, 23 * k)]

        self.canvas.coords(self.cat_arrow_items['ear_l'], *flatten(ear_l_pts))
        self.canvas.coords(self.cat_arrow_items['inner_ear_l'], *flatten(inner_ear_l_pts))
        self.canvas.coords(self.cat_arrow_items['ear_r'], *flatten(ear_r_pts))
        self.canvas.coords(self.cat_arrow_items['inner_ear_r'], *flatten(inner_ear_r_pts))

        # Eyes & Pupils
        ex_l, ey_l = to_canvas(-4.5 * k, 3 * k)
        self.canvas.coords(self.cat_arrow_items['eye_l'], ex_l - 3 * k, ey_l - 3 * k, ex_l + 3 * k, ey_l + 3 * k)
        p_l1 = to_canvas(-4.5 * k, 4.5 * k)
        p_l2 = to_canvas(-4.5 * k, 1.5 * k)
        self.canvas.coords(self.cat_arrow_items['pupil_l'], p_l1[0], p_l1[1], p_l2[0], p_l2[1])

        ex_r, ey_r = to_canvas(4.5 * k, 3 * k)
        self.canvas.coords(self.cat_arrow_items['eye_r'], ex_r - 3 * k, ey_r - 3 * k, ex_r + 3 * k, ey_r + 3 * k)
        p_r1 = to_canvas(4.5 * k, 4.5 * k)
        p_r2 = to_canvas(4.5 * k, 1.5 * k)
        self.canvas.coords(self.cat_arrow_items['pupil_r'], p_r1[0], p_r1[1], p_r2[0], p_r2[1])

        # Nose
        nose_pts = [(-1.5 * k, 0), (1.5 * k, 0), (0, -2 * k)]
        self.canvas.coords(self.cat_arrow_items['nose'], *flatten(nose_pts))

        # Whiskers
        wl1 = [to_canvas(-12 * k, 2 * k), to_canvas(-25 * k, 5 * k)]
        wl2 = [to_canvas(-12 * k, -1 * k), to_canvas(-26 * k, -1 * k)]
        wl3 = [to_canvas(-12 * k, -4 * k), to_canvas(-25 * k, -7 * k)]

        wr1 = [to_canvas(12 * k, 2 * k), to_canvas(25 * k, 5 * k)]
        wr2 = [to_canvas(12 * k, -1 * k), to_canvas(26 * k, -1 * k)]
        wr3 = [to_canvas(12 * k, -4 * k), to_canvas(25 * k, -7 * k)]

        self.canvas.coords(self.cat_arrow_items['whisker_l1'], wl1[0][0], wl1[0][1], wl1[1][0], wl1[1][1])
        self.canvas.coords(self.cat_arrow_items['whisker_l2'], wl2[0][0], wl2[0][1], wl2[1][0], wl2[1][1])
        self.canvas.coords(self.cat_arrow_items['whisker_l3'], wl3[0][0], wl3[0][1], wl3[1][0], wl3[1][1])
        self.canvas.coords(self.cat_arrow_items['whisker_r1'], wr1[0][0], wr1[0][1], wr1[1][0], wr1[1][1])
        self.canvas.coords(self.cat_arrow_items['whisker_r2'], wr2[0][0], wr2[0][1], wr2[1][0], wr2[1][1])
        self.canvas.coords(self.cat_arrow_items['whisker_r3'], wr3[0][0], wr3[0][1], wr3[1][0], wr3[1][1])

    def _update_cat_coords(self, wheel_angle: float, target_angle: float, current_time: float) -> None:
        """Update cat rendering coordinates and animation on the outer side of the arc."""
        k = getattr(self, 'k', 1.0)
        rad = math.radians(90.0 - wheel_angle)
        cat_r = self.R + 28 * k
        cat_x = self.Cx + cat_r * math.cos(rad)
        cat_y = self.Cy - cat_r * math.sin(rad)

        angle_diff = target_angle - wheel_angle
        abs_diff = abs(angle_diff)
        sign = 1.0 if angle_diff >= 0 else -1.0

        u_tan_x = sign * math.sin(rad)
        u_tan_y = sign * math.cos(rad)
        u_norm_x = math.cos(rad)
        u_norm_y = -math.sin(rad)

        def to_canvas(df: float, dn: float) -> tuple[float, float]:
            return (cat_x + df * u_tan_x + dn * u_norm_x, cat_y + df * u_tan_y + dn * u_norm_y)

        def flatten(pts: list[tuple[float, float]]) -> list[float]:
            res = []
            for df, dn in pts:
                cx, cy = to_canvas(df, dn)
                res.extend((cx, cy))
            return res

        is_catching = abs_diff < 18.0

        anim_phase = math.sin(current_time * 12.0)
        tail_wiggle = math.sin(current_time * (25.0 if is_catching else 6.0))

        # Body
        body_pts = [
            (-15 * k, 0), (-11 * k, 7 * k), (0, 8 * k), (10 * k, 7 * k),
            (14 * k, 0), (10 * k, -7 * k), (0, -8 * k), (-11 * k, -7 * k)
        ]
        self.canvas.coords(self.cat_items['body'], *flatten(body_pts))

        # Head
        head_f = 21 * k if is_catching else 20 * k
        head_pts = [
            (10 * k, 2 * k), (13 * k, 8 * k), (head_f, 8 * k), (23 * k, 2 * k),
            (23 * k, -2 * k), (19 * k, -6 * k), (13 * k, -6 * k), (10 * k, -2 * k)
        ]
        self.canvas.coords(self.cat_items['head'], *flatten(head_pts))

        # Ears
        ear_f = 2 * k if is_catching else 0
        ear_l_pts = [(12 * k + ear_f, 6 * k), (15 * k + ear_f, 16 * k), (19 * k + ear_f, 7 * k)]
        inner_ear_l_pts = [(13.5 * k + ear_f, 7 * k), (15.5 * k + ear_f, 14.5 * k), (17.5 * k + ear_f, 7.5 * k)]
        ear_r_pts = [(17 * k + ear_f, 5 * k), (21 * k + ear_f, 14 * k), (23 * k + ear_f, 4 * k)]
        inner_ear_r_pts = [(18.5 * k + ear_f, 6 * k), (21 * k + ear_f, 12.5 * k), (22.5 * k + ear_f, 5 * k)]

        self.canvas.coords(self.cat_items['ear_l'], *flatten(ear_l_pts))
        self.canvas.coords(self.cat_items['inner_ear_l'], *flatten(inner_ear_l_pts))
        self.canvas.coords(self.cat_items['ear_r'], *flatten(ear_r_pts))
        self.canvas.coords(self.cat_items['inner_ear_r'], *flatten(inner_ear_r_pts))

        # Eyes & Pupil
        eye_pts = [(18 * k, 3 * k), (21 * k, 5 * k), (23 * k if is_catching else 22 * k, 3 * k), (20 * k, 1 * k)]
        pupil_pts = [(20.5 * k, 4 * k), (20.5 * k, 1.5 * k)]
        self.canvas.coords(self.cat_items['eye'], *flatten(eye_pts))
        self.canvas.coords(self.cat_items['pupil'], *flatten(pupil_pts))

        # Nose
        nose_pts = [(23 * k, 1 * k), (25 * k, 0), (23 * k, -1 * k)]
        self.canvas.coords(self.cat_items['nose'], *flatten(nose_pts))

        # Whiskers
        w1 = [to_canvas(24 * k, 1 * k), to_canvas(29 * k, 4 * k)]
        w2 = [to_canvas(24 * k, 0), to_canvas(30 * k, 0)]
        w3 = [to_canvas(24 * k, -1 * k), to_canvas(29 * k, -4 * k)]
        self.canvas.coords(self.cat_items['whisker1'], w1[0][0], w1[0][1], w1[1][0], w1[1][1])
        self.canvas.coords(self.cat_items['whisker2'], w2[0][0], w2[0][1], w2[1][0], w2[1][1])
        self.canvas.coords(self.cat_items['whisker3'], w3[0][0], w3[0][1], w3[1][0], w3[1][1])

        # Tail
        tail_pts = [
            (-14 * k, 1 * k),
            (-19 * k, (5 + 3 * tail_wiggle) * k),
            (-24 * k, (10 + 5 * tail_wiggle) * k),
            (-27 * k, (15 + 7 * tail_wiggle) * k)
        ]
        self.canvas.coords(self.cat_items['tail'], *flatten(tail_pts))

        # Legs / Paws
        if is_catching:
            arc_dist = abs_diff * (math.pi / 180.0) * self.R
            reach_f = min(26.0 * k, max(14.0 * k, arc_dist))
            reach_dn = -14.0 * k - (18.0 - min(18.0, abs_diff)) * 0.4 * k
            swipe = math.sin(current_time * 20.0) * 4.0 * k

            leg_fl_pts = [to_canvas(8 * k, -4 * k), to_canvas(8 * k + reach_f, reach_dn)]
            leg_fr_pts = [to_canvas(12 * k, -4 * k), to_canvas(12 * k + reach_f - 3.0 * k, reach_dn - 3.0 * k + swipe)]
            leg_bl_pts = [to_canvas(-10 * k, -4 * k), to_canvas(-18 * k, -10 * k)]
            leg_br_pts = [to_canvas(-6 * k, -4 * k), to_canvas(-14 * k, -12 * k)]
        else:
            df_fl = 6 * anim_phase * k
            df_fr = -6 * anim_phase * k
            df_bl = -6 * anim_phase * k
            df_br = 6 * anim_phase * k

            leg_fl_pts = [to_canvas(8 * k, -4 * k), to_canvas(14 * k + df_fl, -13 * k)]
            leg_fr_pts = [to_canvas(12 * k, -4 * k), to_canvas(16 * k + df_fr, -13 * k)]
            leg_bl_pts = [to_canvas(-10 * k, -4 * k), to_canvas(-16 * k + df_bl, -13 * k)]
            leg_br_pts = [to_canvas(-6 * k, -4 * k), to_canvas(-12 * k + df_br, -13 * k)]

        self.canvas.coords(
            self.cat_items['leg_fl'], leg_fl_pts[0][0], leg_fl_pts[0][1], leg_fl_pts[1][0], leg_fl_pts[1][1]
        )
        self.canvas.coords(
            self.cat_items['leg_fr'], leg_fr_pts[0][0], leg_fr_pts[0][1], leg_fr_pts[1][0], leg_fr_pts[1][1]
        )
        self.canvas.coords(
            self.cat_items['leg_bl'], leg_bl_pts[0][0], leg_bl_pts[0][1], leg_bl_pts[1][0], leg_bl_pts[1][1]
        )
        self.canvas.coords(
            self.cat_items['leg_br'], leg_br_pts[0][0], leg_br_pts[0][1], leg_br_pts[1][0], leg_br_pts[1][1]
        )

    def update_gui(self) -> None:
        """GUI Frame Tick (60 Hz approximation)."""
        current_time = time.time()
        k = getattr(self, 'k', 1.0)

        # 0. Update Workout Timer
        elapsed = int(current_time - self.session_start_time)
        mins, secs = divmod(elapsed, 60)
        hrs, mins = divmod(mins, 60)
        if hrs > 0:
            time_str = f'WORKOUT TIME: {hrs:02d}:{mins:02d}:{secs:02d}'
        else:
            time_str = f'WORKOUT TIME: {mins:02d}:{secs:02d}'
        self.canvas.itemconfig(self.timer_text, text=time_str)

        # 1. Step target state machine and get target angle
        target_angle = self.target_state.update(current_time)

        # 2. Get current wheel position
        if self.controller.dev:
            wheel_angle = self.controller.get_current_angle()
            current_force = self.controller.last_force
        else:
            # Emulated Demo Mode
            wheel_angle = self.emulated_angle

            # Calculate mock force for HUD display
            current_max_force = MAX_FORCE * self.controller.force_scale
            if wheel_angle > STIFFNESS_ANGLE:
                current_force = -current_max_force
            elif wheel_angle < -STIFFNESS_ANGLE:
                current_force = current_max_force
            else:
                current_force = -current_max_force * (wheel_angle / STIFFNESS_ANGLE)

        # Share visual target angle back to FFB controller
        self.controller.target_angle = target_angle

        # 3. Calculate target coordinate geometry (angle range -90° to +90°)
        target_phi = 90.0 - target_angle
        target_rad = math.radians(target_phi)
        tx = self.Cx + self.R * math.cos(target_rad)
        ty = self.Cy - self.R * math.sin(target_rad)

        # 4. Calculate wheel coordinate geometry
        wheel_phi = 90.0 - wheel_angle
        wheel_rad = math.radians(wheel_phi)
        wx = self.Cx + self.R * math.cos(wheel_rad)
        wy = self.Cy - self.R * math.sin(wheel_rad)

        # 5. Move target needles and glows
        self.canvas.coords(self.target_needle, self.Cx, self.Cy, tx, ty)
        self.canvas.coords(self.target_glow_outer, tx - 22 * k, ty - 22 * k, tx + 22 * k, ty + 22 * k)
        self.canvas.coords(self.target_glow_inner, tx - 14 * k, ty - 14 * k, tx + 14 * k, ty + 14 * k)
        self.canvas.coords(self.target_core, tx - 8 * k, ty - 8 * k, tx + 8 * k, ty + 8 * k)

        # 6. Apply visual mode styling (Cat Arrow vs Full Cat vs Regular Mode)
        if self.visual_mode == 'cat':
            # Target shown using red pointer
            self.canvas.itemconfig(self.target_needle, fill='#f43f5e', dash='')
            self.canvas.itemconfig(self.target_glow_outer, fill='#450a0a')
            self.canvas.itemconfig(self.target_glow_inner, fill='#881337')
            self.canvas.itemconfig(self.target_core, fill='#f43f5e')

            # Hide default wheel needle and glow circles
            self.canvas.itemconfig(self.wheel_needle, state='hidden')
            self.canvas.itemconfig(self.wheel_glow_outer, state='hidden')
            self.canvas.itemconfig(self.wheel_glow_inner, state='hidden')
            self.canvas.itemconfig(self.wheel_core, state='hidden')

            # Hide cat arrow items
            for item in self.cat_arrow_items.values():
                self.canvas.itemconfig(item, state='hidden')

            # Show full cat items & update cat position/animation
            for item in self.cat_items.values():
                self.canvas.itemconfig(item, state='normal')
            self._update_cat_coords(wheel_angle, target_angle, current_time)
        elif self.visual_mode == 'cat_arrow':
            # Target is green indicator
            self.canvas.itemconfig(self.target_needle, fill='#10b981', dash=(5, 3))
            self.canvas.itemconfig(self.target_glow_outer, fill='#022c22')
            self.canvas.itemconfig(self.target_glow_inner, fill='#064e3b')
            self.canvas.itemconfig(self.target_core, fill='#10b981')

            # Move wheel needle and glows
            self.canvas.coords(self.wheel_needle, self.Cx, self.Cy, wx, wy)
            self.canvas.coords(self.wheel_glow_outer, wx - 18 * k, wy - 18 * k, wx + 18 * k, wy + 18 * k)
            self.canvas.coords(self.wheel_glow_inner, wx - 11 * k, wy - 11 * k, wx + 11 * k, wy + 11 * k)
            self.canvas.coords(self.wheel_core, wx - 6 * k, wy - 6 * k, wx + 6 * k, wy + 6 * k)

            # Show wheel needle and outer/inner glow circles, but hide the brighter center core dot
            self.canvas.itemconfig(self.wheel_needle, state='normal')
            self.canvas.itemconfig(self.wheel_glow_outer, state='normal')
            self.canvas.itemconfig(self.wheel_glow_inner, state='normal')
            self.canvas.itemconfig(self.wheel_core, state='hidden')

            # Hide full cat items
            for item in self.cat_items.values():
                self.canvas.itemconfig(item, state='hidden')

            # Show cat arrow items & update coords
            for item in self.cat_arrow_items.values():
                self.canvas.itemconfig(item, state='normal')
            self._update_cat_arrow_coords(wx, wy, wheel_angle)
        else:
            # Regular mode: Target is green indicator, standard wheel arrow with plain circle
            self.canvas.itemconfig(self.target_needle, fill='#10b981', dash=(5, 3))
            self.canvas.itemconfig(self.target_glow_outer, fill='#022c22')
            self.canvas.itemconfig(self.target_glow_inner, fill='#064e3b')
            self.canvas.itemconfig(self.target_core, fill='#10b981')

            # Move wheel needles and glows
            self.canvas.coords(self.wheel_needle, self.Cx, self.Cy, wx, wy)
            self.canvas.coords(self.wheel_glow_outer, wx - 18 * k, wy - 18 * k, wx + 18 * k, wy + 18 * k)
            self.canvas.coords(self.wheel_glow_inner, wx - 11 * k, wy - 11 * k, wx + 11 * k, wy + 11 * k)
            self.canvas.coords(self.wheel_core, wx - 6 * k, wy - 6 * k, wx + 6 * k, wy + 6 * k)

            # Show default wheel needle and glow circles
            self.canvas.itemconfig(self.wheel_needle, state='normal')
            self.canvas.itemconfig(self.wheel_glow_outer, state='normal')
            self.canvas.itemconfig(self.wheel_glow_inner, state='normal')
            self.canvas.itemconfig(self.wheel_core, state='normal')

            # Hide cat arrow items and full cat items
            for item in self.cat_arrow_items.values():
                self.canvas.itemconfig(item, state='hidden')
            for item in self.cat_items.values():
                self.canvas.itemconfig(item, state='hidden')

        # 7. Update Force Bar
        max_scalable = MAX_FORCE * self.controller.force_scale
        force_ratio = (current_force / max_scalable) if max_scalable > 0 else 0.0
        bar_half = getattr(self, 'bar_w', 400 * k) / 2.0
        bar_length = int(force_ratio * bar_half)
        bar_y = getattr(self, 'bar_y', self.Cy + 185 * k)

        if bar_length < 0:
            self.canvas.coords(self.force_bar, self.Cx + bar_length, bar_y - 4 * k, self.Cx, bar_y + 4 * k)
            self.canvas.itemconfig(self.force_bar, fill='#be123c')  # Red indicator for centering force pulling left
        else:
            self.canvas.coords(self.force_bar, self.Cx, bar_y - 4 * k, self.Cx + bar_length, bar_y + 4 * k)
            self.canvas.itemconfig(self.force_bar, fill='#0369a1')  # Blue indicator for centering force pulling right

        # 8. Update Telemetry text readouts
        self.canvas.itemconfig(self.target_text, text=f'{target_angle:+.1f}°')
        self.canvas.itemconfig(self.wheel_text, text=f'{wheel_angle:+.1f}°')

        # Color-coded error indicator (redder as error increases)
        error = abs(wheel_angle - target_angle)
        self.canvas.itemconfig(self.error_text, text=f'{error:.1f}°')
        if error < 5.0:
            self.canvas.itemconfig(self.error_text, fill='#10b981')  # green (perfect)
        elif error < 15.0:
            self.canvas.itemconfig(self.error_text, fill='#f59e0b')  # yellow (close)
        else:
            self.canvas.itemconfig(self.error_text, fill='#ef4444')  # red (far)

        # Accumulate deviation metrics and update average deviation display
        self.total_error += error
        self.error_samples += 1
        self.avg_error = self.total_error / self.error_samples if self.error_samples > 0 else 0.0

        self.canvas.itemconfig(self.avg_error_text, text=f'{self.avg_error:.1f}°')
        if self.avg_error < 5.0:
            self.canvas.itemconfig(self.avg_error_text, fill='#10b981')  # green (excellent)
        elif self.avg_error < 15.0:
            self.canvas.itemconfig(self.avg_error_text, fill='#f59e0b')  # yellow (good)
        else:
            self.canvas.itemconfig(self.avg_error_text, fill='#ef4444')  # red (needs work)

        # Max force percentage readout
        self.canvas.itemconfig(self.force_scale_text, text=f'{self.controller.force_scale * 100:.0f}%')

        # 9. Update Audio Cue Volume (Deflection scaling)
        if hasattr(self, 'audio_controller'):
            self.audio_controller.update_volume_for_deflection(error)

        # Connection status & hotkey hint footer (2 lines)
        if self.visual_mode == 'cat_arrow':
            mode_name = 'Cat Arrow'
        elif self.visual_mode == 'cat':
            mode_name = 'Full Cat'
        else:
            mode_name = 'Regular'
        mode_str = f'V Mode ({mode_name})'
        audio_str = 'MUTED' if getattr(self, 'audio_muted', False) else 'ON'
        audio_prompt = f'M Audio ({audio_str})'

        if self.controller.dev:
            dev_msg = f'DEVICE: {self.controller.dev.name}   •   RANGE: -90°..+90°'
        else:
            dev_msg = 'DEMO MODE (MOUSE STEERING)   •   RANGE: -90°..+90°'

        hint_msg = f'[ / ] Force   •   {mode_str}   •   {audio_prompt}   •   R Reset   •   S Screen   •   ESC Exit'

        if hasattr(self, 'status_text_dev'):
            self.canvas.itemconfig(self.status_text_dev, text=dev_msg)
            self.canvas.itemconfig(self.status_text_hint, text=hint_msg)
        elif hasattr(self, 'status_text'):
            self.canvas.itemconfig(self.status_text, text=f'{dev_msg} | {hint_msg}')

        # Heartbeat screensaver inhibitor
        if hasattr(self, 'inhibitor'):
            self.inhibitor.heartbeat()

        # Loop again in 16ms
        self.root.after(16, self.update_gui)

    def exit_app(self, event: tk.Event = None) -> None:
        """Shut down screensaver inhibitor, audio player, FFB controller, and destroy GUI."""
        if hasattr(self, 'audio_controller'):
            self.audio_controller.stop()
        if hasattr(self, 'inhibitor'):
            self.inhibitor.stop()
        self.controller.clean_up()
        self.root.destroy()
        sys.exit(0)

    def run(self) -> None:
        self.root.mainloop()


# ==============================================================================
# EXTERNAL APPLICATION TRACKING RUNNER
# ==============================================================================
class AppTrackingRunner:
    """Launches an external application, tracks its window, and dynamically moves it."""

    def __init__(self, controller: FanatecFFBController, command: str, monitor_index: int = None) -> None:
        self.controller = controller
        self.command = command
        self.running = False
        self.process = None
        self.window_id = None

        cfg = load_strength_config(self.controller.config_path)
        if monitor_index is None:
            saved_screen = cfg.get('screen', cfg.get('monitor_index', 0))
            try:
                self.monitor_index = int(saved_screen)
            except (ValueError, TypeError):
                self.monitor_index = 0
        else:
            self.monitor_index = int(monitor_index)

        self.screen_x, self.screen_y, self.screen_width, self.screen_height = get_monitor_geometry(self.monitor_index)

        # Audio feedback controller
        self.audio_muted = bool(cfg.get('audio_muted', False))
        self.audio_controller = AudioFeedbackController(muted=self.audio_muted)

        # Screensaver inhibitor
        self.inhibitor = ScreensaverInhibitor()

        # Target generator
        self.target_state = TargetState()

        # X11 Display & Atoms
        self.d = None
        self.root = None
        self.net_moveresize = None
        self.net_client_list = None
        self.net_wm_pid = None
        self.net_wm_state = None
        self.net_wm_state_max_v = None
        self.net_wm_state_max_h = None
        self.net_wm_state_fs = None

    def setup_x11(self) -> bool:
        """Initialize Xlib connection and EWMH atoms."""
        if xdisplay is None:
            return False
        try:
            self.d = xdisplay.Display()
            self.root = self.d.screen().root
            self.net_moveresize = self.d.intern_atom('_NET_MOVERESIZE_WINDOW')
            self.net_client_list = self.d.intern_atom('_NET_CLIENT_LIST')
            self.net_wm_pid = self.d.intern_atom('_NET_WM_PID')
            self.net_wm_state = self.d.intern_atom('_NET_WM_STATE')
            self.net_wm_state_max_v = self.d.intern_atom('_NET_WM_STATE_MAXIMIZED_VERT')
            self.net_wm_state_max_h = self.d.intern_atom('_NET_WM_STATE_MAXIMIZED_HORZ')
            self.net_wm_state_fs = self.d.intern_atom('_NET_WM_STATE_FULLSCREEN')
            return True
        except Exception as e:
            print(f'Warning: Could not initialize Xlib display connection: {e}')
            return False

    def launch_app(self) -> bool:
        """Launch the external application subprocess with X11 environment."""
        print(f'Launching external application: {self.command}')
        env = os.environ.copy()
        env['WAYLAND_DISPLAY'] = ''
        env['QT_QPA_PLATFORM'] = 'xcb'
        env['GDK_BACKEND'] = 'x11'
        env['SDL_VIDEODRIVER'] = 'x11'

        try:
            if isinstance(self.command, str):
                cmd_args = shlex.split(self.command)
            else:
                cmd_args = list(self.command)
            self.process = subprocess.Popen(cmd_args, env=env)
            return True
        except Exception as e:
            print(f'Error launching application "{self.command}": {e}')
            return False

    def find_window(self, timeout: float = 10.0) -> int:
        """Locate the X11 window ID associated with the launched process."""
        if not self.process:
            return None
        start = time.time()

        while time.time() - start < timeout:
            if self.process.poll() is not None:
                print('Process terminated before window was found.')
                return None

            pids = get_descendant_pids(self.process.pid)

            # Method 1: xdotool search --pid for any pid in pids
            for pid in pids:
                try:
                    res = subprocess.run(
                        ['xdotool', 'search', '--pid', str(pid)],
                        capture_output=True, text=True, check=False
                    )
                    wids = res.stdout.strip().split()
                    if wids:
                        return int(wids[-1])
                except Exception:
                    pass

            # Method 2: Xlib search via _NET_CLIENT_LIST
            if self.d and self.root and self.net_client_list and self.net_wm_pid and xX:
                try:
                    client_list = self.root.get_full_property(self.net_client_list, xX.AnyPropertyType)
                    if client_list:
                        for wid in client_list.value:
                            w = self.d.create_resource_object('window', wid)
                            pid_prop = w.get_full_property(self.net_wm_pid, xX.AnyPropertyType)
                            if pid_prop and len(pid_prop.value) > 0 and pid_prop.value[0] in pids:
                                return wid
                except Exception:
                    pass

            time.sleep(0.15)

        print(f'Warning: Could not find window for PID {self.process.pid} within {timeout}s.')
        return None

    def move_resize_window(self, wid: int, x: int, y: int, width: int, height: int) -> None:
        """Send EWMH _NET_MOVERESIZE_WINDOW client message or invoke xdotool."""
        if not wid:
            return
        if self.d and self.root and self.net_moveresize and xX and xprotocol:
            try:
                flags = 0xF01  # NorthWest gravity + x + y + width + height
                cm = xprotocol.event.ClientMessage(
                    window=wid,
                    client_type=self.net_moveresize,
                    data=(32, [flags, x & 0xFFFFFFFF, y & 0xFFFFFFFF, width, height])
                )
                self.root.send_event(cm, event_mask=xX.SubstructureRedirectMask | xX.SubstructureNotifyMask)
                self.d.flush()
                return
            except Exception:
                pass

        # Fallback to xdotool
        try:
            subprocess.run(['xdotool', 'windowsize', str(wid), str(width), str(height)], capture_output=True, check=False)
            subprocess.run(['xdotool', 'windowmove', '--', str(wid), str(x), str(y)], capture_output=True, check=False)
        except Exception:
            pass

    def run(self) -> None:
        """Run the main window tracking and motion loop."""
        self.setup_x11()
        if not self.launch_app():
            return

        self.window_id = self.find_window(timeout=10.0)
        if not self.window_id:
            print('Could not find application window. Exiting.')
            self.stop()
            return

        print(f'Tracking window {self.window_id} ({self.screen_width}x{self.screen_height} at {self.screen_x},{self.screen_y}).')

        # Unmaximize / remove fullscreen state if set
        if self.d and self.root and xX and xprotocol and self.net_wm_state:
            try:
                for state_atom in [self.net_wm_state_max_v, self.net_wm_state_max_h, self.net_wm_state_fs]:
                    if state_atom is not None:
                        cm = xprotocol.event.ClientMessage(
                            window=self.window_id,
                            client_type=self.net_wm_state,
                            data=(32, [0, state_atom, 0, 1, 0])
                        )
                        self.root.send_event(cm, event_mask=xX.SubstructureRedirectMask | xX.SubstructureNotifyMask)
                self.d.sync()
            except Exception:
                pass

        # Initial move to center
        self.move_resize_window(self.window_id, self.screen_x, self.screen_y, self.screen_width, self.screen_height)

        # Start screensaver inhibitor
        self.inhibitor.start(window_id=self.window_id)

        self.running = True

        def handle_signal(signum, frame):
            self.running = False

        orig_sigint = signal.signal(signal.SIGINT, handle_signal)
        orig_sigterm = signal.signal(signal.SIGTERM, handle_signal)

        dt = 1.0 / 60.0  # 60Hz update rate
        print('External App Tracking Mode active. Press Ctrl+C or close the application window to exit.')

        try:
            while self.running:
                loop_start = time.time()

                if self.process.poll() is not None:
                    print('Application process exited.')
                    break

                now = time.time()
                # 1. Update target angle
                target_angle = self.target_state.update(now)
                self.controller.target_angle = target_angle

                # 2. Get steering wheel angle (or demo mouse angle)
                if self.controller.dev:
                    wheel_angle = self.controller.get_current_angle()
                else:
                    # Emulated demo mode using pointer X position
                    wheel_angle = 0.0
                    if self.d and self.root:
                        try:
                            qp = self.root.query_pointer()
                            cx = self.screen_x + self.screen_width / 2.0
                            half_w = self.screen_width / 2.0
                            if half_w > 0:
                                ratio = (qp.root_x - cx) / half_w
                                wheel_angle = max(-90.0, min(90.0, ratio * 90.0))
                        except Exception:
                            pass

                # 3. Calculate net deflection (error)
                # When target moves to the right (+deg), deflection is positive, moving window right.
                # User counteracts by turning wheel right (+deg), bringing deflection to 0.
                deflection = target_angle - wheel_angle

                # 4. Audio volume update
                self.audio_controller.update_volume_for_deflection(abs(deflection))

                # 5. Heartbeat screensaver inhibitor
                self.inhibitor.heartbeat()

                # 6. Calculate window position and move window
                offset_x = calculate_window_offset(self.screen_width, deflection)
                target_x = self.screen_x + offset_x
                target_y = self.screen_y
                self.move_resize_window(self.window_id, target_x, target_y, self.screen_width, self.screen_height)

                # Maintain 60Hz timing
                elapsed = time.time() - loop_start
                sleep_time = max(0.0, dt - elapsed)
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            print('\nStopping strength training...')
        finally:
            try:
                signal.signal(signal.SIGINT, orig_sigint)
                signal.signal(signal.SIGTERM, orig_sigterm)
            except Exception:
                pass
            self.stop()

    def stop(self) -> None:
        """Stop tracking, audio, screensaver inhibitor, and child application."""
        self.running = False
        if hasattr(self, 'audio_controller'):
            self.audio_controller.stop()
        if hasattr(self, 'inhibitor'):
            self.inhibitor.stop()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            except Exception:
                pass
        self.controller.clean_up()


def main(argv=None) -> None:
    """Main CLI entry point for Fanatec strength training."""
    parser = argparse.ArgumentParser(description='Fanatec Wheel Strength Training App')
    parser.add_argument('--app', type=str, default=None,
                        help='Command to launch and track (e.g. --app="mpv my.mp4")')
    parser.add_argument('--screen', type=int, default=None,
                        help='Monitor index to target in multi-screen setups (default: saved config or 0)')
    args = parser.parse_args(argv)

    controller = FanatecFFBController()

    # Attempt to locate and configure a Fanatec wheel device
    has_wheel = controller.find_devices()

    if has_wheel:
        controller.read_hardware_range()
        controller.init_ffb()
        controller.start()

    if args.screen is not None:
        save_strength_config({'screen': args.screen}, controller.config_path)

    if args.app:
        runner = AppTrackingRunner(controller, args.app, monitor_index=args.screen)
        try:
            runner.run()
        finally:
            runner.stop()
    else:
        # Build and start GUI
        app = StrengthTrainingApp(controller, monitor_index=args.screen)
        try:
            app.run()
        finally:
            controller.clean_up()


if __name__ == '__main__':
    main()
