"""500 Hz real-time DSP filter bank for Spiker:bit EEG/EOG/Jaw-EMG, ECG, and EMG modes."""

import collections
from dataclasses import dataclass, field
import math
from typing import Deque, List, Optional, Sequence, Tuple
import numpy as np
from scipy import signal


FS_HZ = 500.0


class Biquad:
    """Direct Form II Transposed second-order IIR biquad filter (O(1) per sample)."""

    def __init__(
        self,
        b0: float,
        b1: float,
        b2: float,
        a0: float,
        a1: float,
        a2: float,
    ) -> None:
        self.b0 = b0 / a0
        self.b1 = b1 / a0
        self.b2 = b2 / a0
        self.a1 = a1 / a0
        self.a2 = a2 / a0
        self.z1 = 0.0
        self.z2 = 0.0

    def reset(self) -> None:
        """Clear filter delay state."""
        self.z1 = 0.0
        self.z2 = 0.0

    def step(self, x: float) -> float:
        """Advance filter by one sample and return filtered value."""
        y = self.b0 * x + self.z1
        self.z1 = self.b1 * x - self.a1 * y + self.z2
        self.z2 = self.b2 * x - self.a2 * y
        return y

    @classmethod
    def bandpass(cls, fs: float, f0: float, q: float) -> 'Biquad':
        """Create a constant-skirt bandpass biquad centered at `f0`."""
        w0 = 2.0 * math.pi * f0 / fs
        alpha = math.sin(w0) / (2.0 * q)
        return cls(alpha, 0.0, -alpha, 1.0 + alpha, -2.0 * math.cos(w0), 1.0 - alpha)

    @classmethod
    def notch(cls, fs: float, f0: float, q: float) -> 'Biquad':
        """Create a notch (band-reject) biquad centered at `f0`."""
        w0 = 2.0 * math.pi * f0 / fs
        alpha = math.sin(w0) / (2.0 * q)
        c = math.cos(w0)
        return cls(1.0, -2.0 * c, 1.0, 1.0 + alpha, -2.0 * c, 1.0 - alpha)

    @classmethod
    def highpass(cls, fs: float, f0: float, q: float = 0.70710678) -> 'Biquad':
        """Create a 2nd-order high-pass biquad with cutoff `f0`."""
        w0 = 2.0 * math.pi * f0 / fs
        alpha = math.sin(w0) / (2.0 * q)
        c = math.cos(w0)
        return cls(
            (1.0 + c) / 2.0,
            -(1.0 + c),
            (1.0 + c) / 2.0,
            1.0 + alpha,
            -2.0 * c,
            1.0 - alpha,
        )

    @classmethod
    def lowpass(cls, fs: float, f0: float, q: float = 0.70710678) -> 'Biquad':
        """Create a 2nd-order low-pass biquad with cutoff `f0`."""
        w0 = 2.0 * math.pi * f0 / fs
        alpha = math.sin(w0) / (2.0 * q)
        c = math.cos(w0)
        return cls(
            (1.0 - c) / 2.0,
            1.0 - c,
            (1.0 - c) / 2.0,
            1.0 + alpha,
            -2.0 * c,
            1.0 - alpha,
        )


@dataclass
class HumMonitorSnapshot:
    """Real-time mains hum & electrode contact quality readout."""

    state: str
    hum_rms: float
    sig_rms: float
    rail_pct: float
    hum_p2p_pct_fs: float
    f_mains: float
    fault_reason: str = ''


class EegHumMonitor:
    """Streaming 50/60 Hz mains hum and electrode contact monitor on 500 Hz raw ADC samples."""

    STATE_DISCONNECTED = 'DISCONNECTED_OR_RAILED'
    STATE_GOOD = 'GOOD'
    STATE_MODERATE = 'MODERATE'
    STATE_HIGH = 'HIGH_HUM'

    def __init__(
        self,
        fs: float = FS_HZ,
        f_mains: float = 50.0,
        window_s: float = 0.50,
        hop_s: float = 0.25,
        ema_alpha: float = 0.35,
        history_seconds: float = 10.0,
    ) -> None:
        self.fs = float(fs)
        self.f_mains = float(f_mains)
        self.win_n = max(50, int(round(window_s * self.fs)))
        self.hop_n = max(25, int(round(hop_s * self.fs)))
        self.ema_alpha = float(ema_alpha)
        self._history_maxlen = max(4, int(round(history_seconds / hop_s)))

        self.bp_hum = Biquad.bandpass(self.fs, self.f_mains, self.f_mains / 4.0)
        self.ref_hp = Biquad.highpass(self.fs, 1.0)
        self.ref_notch = Biquad.notch(self.fs, self.f_mains, 20.0)
        self.ref_lp = Biquad.lowpass(self.fs, 35.0)

        self.buf_hum: List[float] = [0.0] * self.win_n
        self.buf_sig: List[float] = [0.0] * self.win_n
        self.buf_rail: List[float] = [0.0] * self.win_n
        self.ptr = 0
        self.filled = 0
        self.hop_ctr = 0
        self.hum_rms_ema = 0.0
        self.sig_rms_ema = 0.0
        self.rail_pct_last = 0.0
        self.initialized = False
        self.state = self.STATE_DISCONNECTED
        self.fault_reason = 'WARMING_UP'
        self._history: Deque[float] = collections.deque(maxlen=self._history_maxlen)

    def reset(self) -> None:
        """Reset streaming filter states, buffers, hysteresis state, and sparkline history."""
        self.bp_hum.reset()
        self.ref_hp.reset()
        self.ref_notch.reset()
        self.ref_lp.reset()
        self.buf_hum = [0.0] * self.win_n
        self.buf_sig = [0.0] * self.win_n
        self.buf_rail = [0.0] * self.win_n
        self.ptr = 0
        self.filled = 0
        self.hop_ctr = 0
        self.hum_rms_ema = 0.0
        self.sig_rms_ema = 0.0
        self.rail_pct_last = 0.0
        self.initialized = False
        self.state = self.STATE_DISCONNECTED
        self.fault_reason = 'WARMING_UP'
        self._history.clear()

    def set_mains_frequency(self, f_mains: float) -> None:
        """Retune the mains bandpass and reference notch between 50 Hz and 60 Hz."""
        target_hz = float(f_mains)
        if abs(target_hz - self.f_mains) > 0.1:
            self.f_mains = target_hz
            self.bp_hum = Biquad.bandpass(self.fs, self.f_mains, self.f_mains / 4.0)
            self.ref_notch = Biquad.notch(self.fs, self.f_mains, 20.0)
            self.reset()

    @property
    def history(self) -> List[float]:
        """Return recent EMA `hum_rms` history (up to `history_seconds`)."""
        return list(self._history)

    def snapshot(self, force_stale: bool = False) -> HumMonitorSnapshot:
        """Return the latest hum snapshot, optionally flagging a stale input stream."""
        state = self.STATE_DISCONNECTED if force_stale else self.state
        fault_reason = 'STALE' if force_stale else self.fault_reason
        hum_p2p_pct_fs = (2.0 * math.sqrt(2.0) * self.hum_rms_ema / 1023.0) * 100.0
        return HumMonitorSnapshot(
            state=state,
            hum_rms=self.hum_rms_ema,
            sig_rms=self.sig_rms_ema,
            rail_pct=self.rail_pct_last,
            hum_p2p_pct_fs=hum_p2p_pct_fs,
            f_mains=self.f_mains,
            fault_reason=fault_reason,
        )

    def push_sample(self, raw_adc: float) -> Optional[HumMonitorSnapshot]:
        """Push one 500 Hz raw ADC sample; emit a `HumMonitorSnapshot` every hop."""
        if not math.isfinite(raw_adc):
            raw_adc = 512.0
            is_rail = 1.0
        else:
            is_rail = 1.0 if (raw_adc <= 10.0 or raw_adc >= 1013.0) else 0.0

        centered = raw_adc - 512.0
        h = self.bp_hum.step(centered)
        s = self.ref_lp.step(self.ref_notch.step(self.ref_hp.step(centered)))

        self.buf_hum[self.ptr] = h * h
        self.buf_sig[self.ptr] = s * s
        self.buf_rail[self.ptr] = is_rail
        self.ptr = (self.ptr + 1) % self.win_n
        self.filled = min(self.win_n, self.filled + 1)
        self.hop_ctr += 1
        if self.filled < self.win_n or self.hop_ctr < self.hop_n:
            return None
        self.hop_ctr = 0

        hum_rms = math.sqrt(sum(self.buf_hum) / self.win_n)
        sig_rms = math.sqrt(sum(self.buf_sig) / self.win_n)
        rail_pct = (sum(self.buf_rail) / self.win_n) * 100.0
        self.rail_pct_last = rail_pct

        if not self.initialized:
            self.hum_rms_ema = hum_rms
            self.sig_rms_ema = sig_rms
            self.initialized = True
        else:
            a = self.ema_alpha
            self.hum_rms_ema = a * hum_rms + (1.0 - a) * self.hum_rms_ema
            self.sig_rms_ema = a * sig_rms + (1.0 - a) * self.sig_rms_ema

        self._history.append(self.hum_rms_ema)

        if rail_pct > 2.0:
            self.state = self.STATE_DISCONNECTED
            self.fault_reason = 'RAILED'
        elif self.sig_rms_ema < 1.5:
            self.state = self.STATE_DISCONNECTED
            self.fault_reason = 'FLATLINE'
        else:
            self.fault_reason = ''
            v = self.hum_rms_ema
            if self.state == self.STATE_GOOD:
                if v >= 53.0:
                    self.state = self.STATE_HIGH
                elif v >= 18.0:
                    self.state = self.STATE_MODERATE
                else:
                    self.state = self.STATE_GOOD
            elif self.state == self.STATE_MODERATE:
                if v < 12.0:
                    self.state = self.STATE_GOOD
                elif v >= 53.0:
                    self.state = self.STATE_HIGH
                else:
                    self.state = self.STATE_MODERATE
            elif self.state == self.STATE_HIGH:
                if v < 12.0:
                    self.state = self.STATE_GOOD
                elif v < 47.0:
                    self.state = self.STATE_MODERATE
                else:
                    self.state = self.STATE_HIGH
            else:
                if v < 15.0:
                    self.state = self.STATE_GOOD
                elif v < 50.0:
                    self.state = self.STATE_MODERATE
                else:
                    self.state = self.STATE_HIGH

        return self.snapshot()

    def push_samples(self, raw_adcs: Sequence[float]) -> Optional[HumMonitorSnapshot]:
        """Push a batch of 500 Hz raw ADC samples and return the latest emitted snapshot."""
        latest: Optional[HumMonitorSnapshot] = None
        for val in raw_adcs:
            snap = self.push_sample(float(val))
            if snap is not None:
                latest = snap
        return latest


@dataclass
class DspResult:
    """Processed waveforms and scalar metrics for the visible oscilloscope window."""

    mode: str
    contact_status: str  # 'GOOD', 'NOISY', 'RAILING'
    raw_centered: np.ndarray
    primary_trace: np.ndarray
    secondary_trace: np.ndarray
    tertiary_trace: np.ndarray
    primary_label: str
    secondary_label: str
    tertiary_label: str
    headline_metric: str
    sub_metric: str
    blink_indices: List[int]
    unfiltered_centered: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))


class SpikerbitDspProcessor:
    """Pre-computes 500 Hz IIR SOS filters and evaluates the visible window on demand."""

    def __init__(self, fs: float = FS_HZ, notch_hz: float = 50.0) -> None:
        self.fs = float(fs)
        self.notch_hz = float(notch_hz)
        self._build_filters()

    def set_notch_frequency(self, notch_hz: float) -> None:
        """Switch mains hum notch filter between 50 Hz and 60 Hz."""
        if abs(notch_hz - self.notch_hz) > 0.1:
            self.notch_hz = float(notch_hz)
            self._build_filters()

    def _build_filters(self) -> None:
        b_notch, a_notch = signal.iirnotch(self.notch_hz, 30.0, fs=self.fs)
        self._sos_notch = signal.tf2sos(b_notch, a_notch)

        # EEG Stream A: EOG / Blink (0.5 - 8.0 Hz captures full 150-250ms blink pulse)
        self._sos_eog = signal.butter(
            2, [0.5, 8.0], btype='bandpass', fs=self.fs, output='sos'
        )
        # EEG Stream B: Theta (4 - 8 Hz), Alpha (8 - 13 Hz), Beta (13 - 30 Hz)
        self._sos_theta = signal.butter(
            2, [4.0, 8.0], btype='bandpass', fs=self.fs, output='sos'
        )
        self._sos_alpha = signal.butter(
            2, [8.0, 13.0], btype='bandpass', fs=self.fs, output='sos'
        )
        self._sos_beta = signal.butter(
            2, [13.0, 30.0], btype='bandpass', fs=self.fs, output='sos'
        )
        # EEG Stream C: Jaw / Facial Micro-EMG (20 - 45 Hz)
        self._sos_jaw_emg = signal.butter(
            2, [20.0, 45.0], btype='bandpass', fs=self.fs, output='sos'
        )
        # ECG QRS Bandpass (5 - 35 Hz)
        self._sos_ecg_qrs = signal.butter(
            2, [5.0, 35.0], btype='bandpass', fs=self.fs, output='sos'
        )
        # Full EMG Bandpass (20 - 200 Hz, leveraging 500 Hz sampling rate)
        self._sos_emg_full = signal.butter(
            2, [20.0, 200.0], btype='bandpass', fs=self.fs, output='sos'
        )

    @staticmethod
    def _apply_sos(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
        """Apply zero-phase sosfiltfilt (with sosfilt_zi fallback for tiny windows) to avoid edge transients."""
        if x.size == 0:
            return x
        padlen = 3 * (2 * sos.shape[0] + 1)
        if x.size > padlen:
            return signal.sosfiltfilt(sos, x)
        zi = signal.sosfilt_zi(sos) * float(x[0])
        out, _ = signal.sosfilt(sos, x, zi=zi)
        return out

    @staticmethod
    def _moving_rms(x: np.ndarray, window_samples: int) -> np.ndarray:
        if x.size == 0:
            return x
        win = max(1, min(window_samples, x.size))
        sq = x * x
        kernel = np.ones(win, dtype=np.float64) / float(win)
        mean_sq = np.convolve(sq, kernel, mode='same')
        return np.sqrt(np.maximum(0.0, mean_sq))

    @staticmethod
    def evaluate_contact_quality(adc_samples: np.ndarray) -> str:
        """Classify electrode contact quality from raw 10-bit ADC samples (0..1023)."""
        if adc_samples.size < 10:
            return 'NOISY'
        rail_mask = (adc_samples <= 10.0) | (adc_samples >= 1013.0)
        rail_ratio = float(np.mean(rail_mask))
        if rail_ratio > 0.05:
            return 'RAILING'
        std_val = float(np.std(adc_samples))
        if std_val < 0.2 or std_val > 320.0:
            return 'NOISY'
        return 'GOOD'

    def process_window(self, adc_values: List[float], mode: str = 'EEG') -> DspResult:
        """Filter the visible sample window for the active hardware mode (`EEG`, `ECG`, `EMG`)."""
        clean_mode = mode.upper()
        if not adc_values:
            empty = np.zeros(0, dtype=np.float64)
            return DspResult(
                mode=clean_mode,
                contact_status='NOISY',
                raw_centered=empty,
                primary_trace=empty,
                secondary_trace=empty,
                tertiary_trace=empty,
                primary_label='Primary',
                secondary_label='Secondary',
                tertiary_label='Tertiary',
                headline_metric='--',
                sub_metric='--',
                blink_indices=[],
            )

        raw = np.asarray(adc_values, dtype=np.float64)
        contact = self.evaluate_contact_quality(raw)
        centered = raw - float(np.median(raw))
        notched = self._apply_sos(self._sos_notch, centered)

        if clean_mode == 'EEG':
            return self._process_eeg(notched, contact, centered)
        if clean_mode == 'ECG':
            return self._process_ecg(notched, contact, centered)
        return self._process_emg(notched, contact, centered)

    def _process_eeg(
        self,
        notched: np.ndarray,
        contact: str,
        unfiltered_centered: Optional[np.ndarray] = None,
    ) -> DspResult:
        eog = self._apply_sos(self._sos_eog, notched)
        theta = self._apply_sos(self._sos_theta, notched)
        alpha = self._apply_sos(self._sos_alpha, notched)
        beta = self._apply_sos(self._sos_beta, notched)
        jaw_band = self._apply_sos(self._sos_jaw_emg, notched)
        jaw_rms = self._moving_rms(jaw_band, int(0.100 * self.fs))  # 100 ms RMS

        # Blink detection: > 3.5 * robust sigma (1.4826 * MAD) with 300 ms refractory period
        mad = float(np.median(np.abs(eog - np.median(eog))))
        robust_sigma = max(6.0, 1.4826 * mad)
        thresh = 3.5 * robust_sigma
        peaks, _ = signal.find_peaks(
            np.abs(eog),
            height=thresh,
            prominence=3.0 * robust_sigma,
            distance=max(1, int(0.300 * self.fs)),
        )
        blink_indices = [int(p) for p in peaks]

        # Artifact-free mask for cortical band power ratios
        artifact_mask = (np.abs(eog) > thresh) | (jaw_rms > max(35.0, 2.5 * np.median(jaw_rms)))
        clean_mask = ~artifact_mask
        if np.sum(clean_mask) < 25:
            clean_mask = np.ones_like(notched, dtype=bool)

        p_theta = float(np.mean(theta[clean_mask] ** 2)) + 1e-6
        p_alpha = float(np.mean(alpha[clean_mask] ** 2)) + 1e-6
        p_beta = float(np.mean(beta[clean_mask] ** 2)) + 1e-6

        engagement = p_beta / (p_alpha + p_theta)
        fatigue = p_theta / p_alpha

        raw_unfiltered = (
            unfiltered_centered
            if unfiltered_centered is not None
            else np.zeros(0, dtype=np.float64)
        )
        return DspResult(
            mode='EEG',
            contact_status=contact,
            raw_centered=notched,
            primary_trace=eog,
            secondary_trace=alpha + beta,
            tertiary_trace=jaw_rms,
            primary_label='EOG / Blinks (0.5-8 Hz)',
            secondary_label='Cortical Alpha+Beta (8-30 Hz)',
            tertiary_label='Jaw Tension RMS (20-45 Hz)',
            headline_metric=f'Engagement: {engagement:.2f}  |  Blinks: {len(blink_indices)}',
            sub_metric=f'Fatigue (Theta/Alpha): {fatigue:.2f}  |  Jaw RMS: {float(np.mean(jaw_rms)):.1f}',
            blink_indices=blink_indices,
            unfiltered_centered=raw_unfiltered,
        )

    def _process_ecg(
        self,
        notched: np.ndarray,
        contact: str,
        unfiltered_centered: Optional[np.ndarray] = None,
    ) -> DspResult:
        qrs = self._apply_sos(self._sos_ecg_qrs, notched)
        qrs_env = self._moving_rms(qrs, int(0.040 * self.fs))

        bpm, rmssd_ms, r_peaks = self._compute_hr_and_rmssd(qrs)
        hr_trace = np.full_like(qrs, bpm)

        raw_unfiltered = (
            unfiltered_centered
            if unfiltered_centered is not None
            else np.zeros(0, dtype=np.float64)
        )
        return DspResult(
            mode='ECG',
            contact_status=contact,
            raw_centered=notched,
            primary_trace=qrs,
            secondary_trace=qrs_env,
            tertiary_trace=hr_trace,
            primary_label='ECG Filtered (5-35 Hz)',
            secondary_label='QRS Energy Envelope',
            tertiary_label='Heart Rate (BPM)',
            headline_metric=f'Heart Rate: {bpm:.1f} BPM' if bpm > 0 else 'Heart Rate: -- BPM',
            sub_metric=f'HRV (RMSSD): {rmssd_ms:.1f} ms  |  Beats: {len(r_peaks)}',
            blink_indices=r_peaks,
            unfiltered_centered=raw_unfiltered,
        )

    def _compute_hr_and_rmssd(self, qrs: np.ndarray) -> Tuple[float, float, List[int]]:
        if qrs.size < int(0.8 * self.fs):
            return 0.0, 0.0, []
        mad = float(np.median(np.abs(qrs - np.median(qrs))))
        sigma = max(10.0, 1.4826 * mad)
        peaks, _ = signal.find_peaks(
            qrs,
            height=max(25.0, 2.5 * sigma),
            distance=max(1, int(0.30 * self.fs)),  # Max 200 BPM
        )
        r_indices = [int(p) for p in peaks]
        if len(r_indices) < 2:
            return 0.0, 0.0, r_indices

        rr_sec = np.diff(np.asarray(r_indices, dtype=np.float64)) / self.fs
        valid_rr = rr_sec[(rr_sec >= 0.30) & (rr_sec <= 2.0)]
        if valid_rr.size == 0:
            return 0.0, 0.0, r_indices

        bpm = float(60.0 / np.mean(valid_rr))
        if valid_rr.size >= 2:
            diff_ms = np.diff(valid_rr) * 1000.0
            rmssd_ms = float(np.sqrt(np.mean(diff_ms ** 2)))
        else:
            rmssd_ms = 0.0
        return bpm, rmssd_ms, r_indices

    def _process_emg(
        self,
        notched: np.ndarray,
        contact: str,
        unfiltered_centered: Optional[np.ndarray] = None,
    ) -> DspResult:
        emg_band = self._apply_sos(self._sos_emg_full, notched)
        emg_rms = self._moving_rms(emg_band, int(0.050 * self.fs))  # 50 ms RMS envelope
        peak_rms = float(np.max(emg_rms)) if emg_rms.size > 0 else 0.0
        mean_rms = float(np.mean(emg_rms)) if emg_rms.size > 0 else 0.0

        raw_unfiltered = (
            unfiltered_centered
            if unfiltered_centered is not None
            else np.zeros(0, dtype=np.float64)
        )
        return DspResult(
            mode='EMG',
            contact_status=contact,
            raw_centered=notched,
            primary_trace=emg_band,
            secondary_trace=emg_rms,
            tertiary_trace=np.abs(emg_band),
            primary_label='Surface EMG (20-200 Hz)',
            secondary_label='Muscle Activation Envelope (50 ms RMS)',
            tertiary_label='Rectified EMG (|x|)',
            headline_metric=f'Mean Activation RMS: {mean_rms:.1f}',
            sub_metric=f'Peak Burst RMS: {peak_rms:.1f}',
            blink_indices=[],
            unfiltered_centered=raw_unfiltered,
        )
