"""500 Hz real-time DSP filter bank for Spiker:bit EEG/EOG/Jaw-EMG, ECG, and EMG modes."""

from dataclasses import dataclass
from typing import List, Tuple
import numpy as np
from scipy import signal


FS_HZ = 500.0


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
            return self._process_eeg(notched, contact)
        if clean_mode == 'ECG':
            return self._process_ecg(notched, contact)
        return self._process_emg(notched, contact)

    def _process_eeg(self, notched: np.ndarray, contact: str) -> DspResult:
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
        )

    def _process_ecg(self, notched: np.ndarray, contact: str) -> DspResult:
        qrs = self._apply_sos(self._sos_ecg_qrs, notched)
        qrs_env = self._moving_rms(qrs, int(0.040 * self.fs))

        bpm, rmssd_ms, r_peaks = self._compute_hr_and_rmssd(qrs)
        hr_trace = np.full_like(qrs, bpm)

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

    def _process_emg(self, notched: np.ndarray, contact: str) -> DspResult:
        emg_band = self._apply_sos(self._sos_emg_full, notched)
        emg_rms = self._moving_rms(emg_band, int(0.050 * self.fs))  # 50 ms RMS envelope
        peak_rms = float(np.max(emg_rms)) if emg_rms.size > 0 else 0.0
        mean_rms = float(np.mean(emg_rms)) if emg_rms.size > 0 else 0.0

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
        )
