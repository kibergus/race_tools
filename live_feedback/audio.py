"""Low-latency audio cue synthesizer and player using pygame.mixer."""

from __future__ import annotations

import math
import threading
from typing import Optional, Tuple

import numpy as np
import pygame


DEFAULT_SAMPLE_RATE = 44100
DEFAULT_F_HIGH_HZ = 1200.0
DEFAULT_F_LOW_HZ = 240.0
DEFAULT_MAX_LOSS_S = 0.50
DEFAULT_DURATION_S = 0.16
DEFAULT_NEW_BEST_THRESHOLD_S = -0.015


def loss_to_frequency_hz(
    delta_s: float,
    f_high: float = DEFAULT_F_HIGH_HZ,
    f_low: float = DEFAULT_F_LOW_HZ,
    max_loss_s: float = DEFAULT_MAX_LOSS_S,
) -> float:
    """Map time loss (seconds) to tone frequency (Hz) on a logarithmic scale.

    `delta_s <= 0` maps to `f_high` (good), `delta_s >= max_loss_s` maps to `f_low` (bad),
    and equal increments in seconds lost produce equal perceptual musical intervals.
    """
    if max_loss_s <= 0.0:
        u = 0.0 if delta_s <= 0.0 else 1.0
    else:
        u = min(1.0, max(0.0, float(delta_s) / float(max_loss_s)))
    return float(f_high * ((float(f_low) / float(f_high)) ** u))


def synthesize_tone_pcm(
    freq_hz: float,
    duration_s: float = DEFAULT_DURATION_S,
    volume: float = 0.8,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> np.ndarray:
    """Synthesize a smooth mono 16-bit PCM tone with subtle upper harmonics.

    Includes 2nd and 3rd harmonics so low frequencies (e.g. 240 Hz) remain audible
    over engine noise, with raised-cosine attack and release envelopes to avoid clicks.
    """
    vol = min(1.0, max(0.0, float(volume)))
    n_samples = max(1, int(round(float(duration_s) * int(sample_rate))))
    if vol <= 0.0 or freq_hz <= 0.0:
        return np.zeros(n_samples, dtype=np.int16)

    t = np.arange(n_samples, dtype=np.float64) / float(sample_rate)
    w = 2.0 * math.pi * float(freq_hz)
    raw = np.sin(w * t) + 0.30 * np.sin(2.0 * w * t) + 0.12 * np.sin(3.0 * w * t)

    peak = float(np.max(np.abs(raw)))
    if peak > 0.0:
        raw /= peak

    envelope = np.ones(n_samples, dtype=np.float64)
    n_attack = min(n_samples // 3, max(1, int(round(0.008 * sample_rate))))
    n_release = min(n_samples - n_attack, max(1, int(round(0.045 * sample_rate))))

    if n_attack > 0:
        att_idx = np.arange(n_attack, dtype=np.float64)
        envelope[:n_attack] = 0.5 * (1.0 - np.cos(math.pi * att_idx / float(n_attack)))
    if n_release > 0:
        rel_idx = np.arange(n_release, dtype=np.float64)
        envelope[-n_release:] = 0.5 * (1.0 + np.cos(math.pi * (rel_idx + 1.0) / float(n_release)))

    scaled = np.clip(raw * envelope * vol * 32767.0, -32768.0, 32767.0)
    return scaled.astype(np.int16)


def synthesize_chirp_pcm(
    notes_hz: Tuple[float, float] = (880.0, 1318.5),
    durations_s: Tuple[float, float] = (0.065, 0.095),
    gap_s: float = 0.010,
    volume: float = 0.8,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> np.ndarray:
    """Synthesize a rising two-note chirp (int16 mono) signaling a new turn best."""
    first = synthesize_tone_pcm(
        notes_hz[0], duration_s=durations_s[0], volume=volume, sample_rate=sample_rate
    )
    n_gap = max(0, int(round(float(gap_s) * int(sample_rate))))
    gap = np.zeros(n_gap, dtype=np.int16)
    second = synthesize_tone_pcm(
        notes_hz[1], duration_s=durations_s[1], volume=volume, sample_rate=sample_rate
    )
    return np.concatenate([first, gap, second])


class AudioCuePlayer:
    """Plays turn-exit pitch cues and new-best chirps via `pygame.mixer`."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        volume: float = 0.8,
        muted: bool = False,
        f_high: float = DEFAULT_F_HIGH_HZ,
        f_low: float = DEFAULT_F_LOW_HZ,
        max_loss_s: float = DEFAULT_MAX_LOSS_S,
        duration_s: float = DEFAULT_DURATION_S,
        new_best_threshold_s: float = DEFAULT_NEW_BEST_THRESHOLD_S,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.volume = min(1.0, max(0.0, float(volume)))
        self.muted = bool(muted)
        self.f_high = float(f_high)
        self.f_low = float(f_low)
        self.max_loss_s = float(max_loss_s)
        self.duration_s = float(duration_s)
        self.new_best_threshold_s = float(new_best_threshold_s)

        self._lock = threading.Lock()
        self._owns_mixer = False
        self._mixer_rate = self.sample_rate
        self._mixer_channels = 1
        self._active_sound: Optional[pygame.mixer.Sound] = None

    def set_volume(self, volume: float) -> None:
        """Set playback volume in [0.0, 1.0]."""
        with self._lock:
            self.volume = min(1.0, max(0.0, float(volume)))

    def set_muted(self, muted: bool) -> None:
        """Enable or disable audio output."""
        with self._lock:
            self.muted = bool(muted)

    def set_max_loss_s(self, max_loss_s: float) -> None:
        """Set the full-scale loss value (in seconds) mapped to `f_low`."""
        with self._lock:
            self.max_loss_s = max(0.05, float(max_loss_s))

    def init_mixer(self) -> None:
        """Initialize `pygame.mixer` with a low-latency buffer if not already running."""
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(
                frequency=self.sample_rate,
                size=-16,
                channels=1,
                buffer=512,
            )
            self._owns_mixer = True
        init_info = pygame.mixer.get_init()
        if init_info is None:
            raise RuntimeError('pygame.mixer failed to initialize')
        self._mixer_rate = int(init_info[0])
        self._mixer_channels = int(init_info[2])

    def play_pcm(self, pcm: np.ndarray) -> None:
        """Play a mono int16 PCM buffer asynchronously via `pygame.mixer`."""
        with self._lock:
            if self.muted or self.volume <= 0.0:
                return
            self.init_mixer()
            arr = np.asarray(pcm, dtype=np.int16).reshape(-1)
            if self._mixer_channels > 1:
                arr = np.column_stack([arr] * self._mixer_channels)
            snd = pygame.sndarray.make_sound(arr)
            snd.play()
            self._active_sound = snd

    def play_tone_hz(self, freq_hz: float) -> None:
        """Synthesize and play a single feedback tone at `freq_hz`."""
        with self._lock:
            self.init_mixer()
            vol = self.volume
            dur = self.duration_s
            sr = self._mixer_rate
        pcm = synthesize_tone_pcm(freq_hz, duration_s=dur, volume=vol, sample_rate=sr)
        self.play_pcm(pcm)

    def play_new_best(self) -> None:
        """Synthesize and play the rising two-note chirp for a new turn best."""
        with self._lock:
            self.init_mixer()
            vol = self.volume
            sr = self._mixer_rate
        pcm = synthesize_chirp_pcm(volume=vol, sample_rate=sr)
        self.play_pcm(pcm)

    def play_turn_cue(
        self,
        delta_s: float,
        new_best_threshold_s: Optional[float] = None,
        max_loss_s: Optional[float] = None,
    ) -> None:
        """Play either a new-best chirp or a pitch-mapped loss beep for `delta_s`."""
        with self._lock:
            if self.muted or self.volume <= 0.0:
                return
            thresh = (
                self.new_best_threshold_s
                if new_best_threshold_s is None
                else float(new_best_threshold_s)
            )
            eff_max = self.max_loss_s if max_loss_s is None else float(max_loss_s)
            f_high = self.f_high
            f_low = self.f_low

        if delta_s < thresh:
            self.play_new_best()
        else:
            freq = loss_to_frequency_hz(
                delta_s,
                f_high=f_high,
                f_low=f_low,
                max_loss_s=eff_max,
            )
            self.play_tone_hz(freq)

    def stop(self) -> None:
        """Stop active playback and release owned mixer resources."""
        with self._lock:
            self._active_sound = None
            if self._owns_mixer and pygame.mixer.get_init() is not None:
                pygame.mixer.quit()
                self._owns_mixer = False
