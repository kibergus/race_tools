"""Unit tests for Spiker:bit BRT1 protocol, clock sync, Brotli uploader, DSP, and flasher."""

import json
import math
import os
import tempfile
import time
import tkinter as tk
from typing import List, Tuple
import urllib.request

import numpy as np
import pytest

from spikerbit.brt1_protocol import (
    Brt1StreamDecoder,
    CHUNK_BASELINE,
    CHUNK_CLOCK_PONG,
    CHUNK_DELTA,
    CHUNK_EOS,
    CHUNK_MAGIC,
    CHUNK_METADATA,
    SPIKERBIT_CHANNELS,
    encode_baseline_chunk,
    encode_delta_batch_chunk,
    encode_eos_chunk,
    encode_magic_chunk,
    encode_metadata_chunk,
)
from spikerbit.dsp import EegHumMonitor, SpikerbitDspProcessor
from spikerbit.flasher import (
    DEFAULT_HEX_PATH,
    find_microbit_drives,
    flash_firmware,
    is_microbit_mount,
    validate_intel_hex,
)
from spikerbit.main import (
    SpikerbitStudioApp,
    downsample_envelope_for_canvas,
    downsample_trace_for_canvas,
)
from spikerbit.serial_brt1 import (
    ClockSyncController,
    MicrobitSimulator,
    SpikerbitSerialClient,
    is_ble_port,
    parse_ble_address,
)
from spikerbit.uploader import AuxSessionManager, BrotliBrt1Segment

import brotli


def test_brt1_protocol_roundtrip_and_sync() -> None:
    """Verify BRT1 framing, batched 10-sample 0x04 Delta chunks, and garbage synchronization."""
    meta = {
        'Track name': 'Monza GP',
        'Date': '2026-11-11',
        'Time': '18:30:00',
        'Driver name': 'NeuroDriver',
        'League': 'acevo',
        'Class': 'GT3',
        'Session': 'Practice',
        'Session ID': 'sess-999',
    }

    stream = bytearray()
    # Prepend simulated serial mid-stream garbage before Magic
    stream.extend(b'\x99\xfe\x12\x00\x55')
    stream.extend(encode_magic_chunk())
    stream.extend(encode_metadata_chunk(meta, SPIKERBIT_CHANNELS, stream_name='spikerbit'))
    stream.extend(encode_baseline_chunk([0.0020, 512.0]))

    tracked = [0.0020, 512.0]
    scales = [0.0001, 1.0]
    batch_samples = [
        [0.0020 + (i + 1) * 0.0020, 512.0 + float((i % 5) - 2)]
        for i in range(9)
    ]
    delta_chunk, overflow = encode_delta_batch_chunk(batch_samples, tracked, scales)
    assert not overflow
    assert len(delta_chunk) == 3 + 9 * 4
    stream.extend(delta_chunk)
    stream.extend(encode_eos_chunk())

    decoder = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
    decoder.feed(bytes(stream))
    assert decoder.synchronize_to_magic_or_baseline()

    events = decoder.pop_events()
    assert len(events) == 5
    assert events[0].chunk_type == CHUNK_MAGIC
    assert events[1].chunk_type == CHUNK_METADATA
    assert events[1].metadata is not None
    assert events[1].metadata['stream'] == 'spikerbit'
    assert events[1].metadata['Track name'] == 'Monza GP'
    assert 'track_name' not in events[1].metadata
    assert 'spikerbit_mode' not in events[1].metadata
    assert events[1].metadata['Session ID'] == 'sess-999'
    assert events[1].channels == [{'name': 'Time', 'scale': 0.0001}, {'name': 'EEG', 'scale': 1.0}]

    assert events[2].chunk_type == CHUNK_BASELINE
    assert events[2].samples is not None
    assert math.isclose(events[2].samples[0][0], 0.0020, abs_tol=1e-6)
    assert math.isclose(events[2].samples[0][1], 512.0, abs_tol=1e-6)

    assert events[3].chunk_type == CHUNK_DELTA
    assert events[3].samples is not None
    assert len(events[3].samples) == 9
    assert math.isclose(events[3].samples[-1][0], 0.0200, abs_tol=1e-6)

    assert events[4].chunk_type == CHUNK_EOS


def test_microbit_simulator_and_clock_sync() -> None:
    """Verify 500 Hz MicrobitSimulator commands and ClockSyncController PI convergence."""
    sim = MicrobitSimulator(initial_mode='EEG')
    sim.write_command(ClockSyncController.format_sync_command(10.0, 'ECG'))
    assert sim.mode == 'ECG'
    assert sim.time_100us == 100000

    pkt1 = sim.generate_next_20ms_packet()
    pkt2 = sim.generate_next_20ms_packet()

    decoder = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
    decoder.feed(pkt1 + pkt2)
    events = decoder.pop_events()

    decoded_samples: List[Tuple[float, ...]] = []
    for ev in events:
        if ev.samples:
            decoded_samples.extend(ev.samples)

    assert len(decoded_samples) == 20
    # Verify uniform 2.000 ms (500 Hz) spacing across all 20 samples
    for i in range(1, len(decoded_samples)):
        dt = decoded_samples[i][0] - decoded_samples[i - 1][0]
        assert math.isclose(dt, 0.0020, abs_tol=1e-6)

    # Test ClockSyncController phase and rate trim
    ctrl = ClockSyncController()
    # Suppose MCU lags host by +12.0 ms (+120 units of 100 us)
    offset_100us, cc_ticks = ctrl.update(host_elapsed_sec=12.012, mcu_time_sec=12.000)
    assert offset_100us == 120
    assert 3960 <= cc_ticks <= 4040

    sim.write_command(ClockSyncController.format_trim_command(offset_100us, cc_ticks))
    assert sim.cc_ticks == cc_ticks


def test_ble_min_rtt_pong_clock_sync_and_port_parsing() -> None:
    """Verify BLE port parsing, 0x06 PONG framing, and min-RTT jitter rejection."""
    assert is_ble_port('BLE')
    assert is_ble_port('ble://AA:BB:CC:DD:EE:FF (Spiker:bit A4F2)')
    assert not is_ble_port('COM3')
    assert not is_ble_port('SIMULATOR')
    assert parse_ble_address('BLE://AA:BB:CC:DD:EE:FF (Spiker:bit A4F2)') == 'AA:BB:CC:DD:EE:FF'
    assert parse_ble_address('BLE') == ''

    # Verify Simulator P: -> 0x06 CHUNK_CLOCK_PONG roundtrip and exclusion from Brotli upload
    sim = MicrobitSimulator(initial_mode='EEG')
    sim.write_command(ClockSyncController.format_sync_command(5.0, 'EEG'))
    sim.write_command(ClockSyncController.format_ping_command(5.010))
    raw_pkt = sim.generate_next_20ms_packet()

    dec = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
    dec.feed(raw_pkt)
    events = dec.pop_events()
    pong_events = [e for e in events if e.chunk_type == CHUNK_CLOCK_PONG]
    assert len(pong_events) == 1
    assert pong_events[0].clock_pong == (5010000, 50000)

    seg = BrotliBrt1Segment(metadata={'Track name': 'BleTrack'}, stream_name='spikerbit')
    for ev in events:
        seg.append_event(ev)
    verify_dec = Brt1StreamDecoder()
    verify_dec.feed(brotli.decompress(seg.finalize()))
    uploaded_types = [e.chunk_type for e in verify_dec.pop_events()]
    assert CHUNK_CLOCK_PONG not in uploaded_types

    # Simulate BLE connection-event jitter: true MCU lags host by +25.0 ms (+250 * 100us)
    ctrl = ClockSyncController(ema_alpha=1.0, kp_rate=0.04, ki_rate=0.008)
    mcu_lag_sec = 0.025
    applied_total_100us = 0

    # Sample 1: clean 12 ms symmetric BLE round-trip (6 ms each way)
    t0_1 = 10.000
    mcu_at_mid_1 = (t0_1 + 0.006) - (mcu_lag_sec - applied_total_100us * 0.0001)
    res1 = ctrl.update_from_pong(
        host_t0_us=int(round(t0_1 * 1e6)),
        host_t1_sec=t0_1 + 0.012,
        mcu_time_100us=int(round(mcu_at_mid_1 * 10000.0)),
    )
    assert res1 is not None
    applied_total_100us += res1[0]
    assert abs(applied_total_100us - 250) <= 2
    assert math.isclose(ctrl.min_rtt_ms or 0.0, 12.0, abs_tol=0.2)

    # Sample 2: asymmetric 64 ms BLE retry delay on return leg -> min-RTT window must reject spike!
    t0_2 = 12.000
    mcu_at_send_2 = (t0_2 + 0.006) - (mcu_lag_sec - applied_total_100us * 0.0001)
    res2 = ctrl.update_from_pong(
        host_t0_us=int(round(t0_2 * 1e6)),
        host_t1_sec=t0_2 + 0.064,
        mcu_time_100us=int(round(mcu_at_send_2 * 10000.0)),
    )
    assert res2 is not None
    applied_total_100us += res2[0]
    # Total applied offset must remain pinned at +25.0 ms (+/- 0.5 ms), uncorrupted by 64 ms spike!
    assert abs(applied_total_100us - 250) <= 5
    assert math.isclose(ctrl.min_rtt_ms or 0.0, 12.0, abs_tol=0.2)


def test_serial_client_simulator_acquisition() -> None:
    """Verify SpikerbitSerialClient only simulates when explicitly requested and never falls back."""
    received_chunks = []
    client = SpikerbitSerialClient(on_raw_chunk=received_chunks.append)
    # Default initial state must be disconnected, not simulating
    assert not client.is_running
    assert not client.is_simulating
    assert client.port_name == 'DISCONNECTED'

    # Calling start() with no port and simulate=False must stay disconnected
    client.start(port=None, simulate=False)
    assert not client.is_running
    assert not client.is_simulating

    # Calling start() on a nonexistent hardware port must error & disconnect, never start simulator
    client.start(port='COM254', simulate=False)
    deadline = time.monotonic() + 2.5
    while client.is_running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not client.is_simulating
    assert client.port_name == 'DISCONNECTED'
    assert client.pop_connection_error() is not None

    # Explicit simulator request must work
    client.start(port='SIMULATOR', simulate=True)
    try:
        time.sleep(0.12)
        client.set_mode('EMG')
        time.sleep(0.08)
        times, adcs = client.get_recent_samples()
        assert len(times) >= 30
        assert len(times) == len(adcs)
        assert len(received_chunks) >= 3
        assert client.mode == 'EMG'
    finally:
        client.stop()


def test_aux_session_manager_and_brotli_upload() -> None:
    """Verify CommandSink HTTP signalling, rollover baseline injection, and Brotli upload."""
    uploaded_payloads: List[Tuple[str, str, bytes]] = []

    def mock_sender(server_url: str, api_key: str, data: bytes) -> Tuple[int, str]:
        uploaded_payloads.append((server_url, api_key, data))
        return 200, ''

    with tempfile.TemporaryDirectory() as tmp_dir:
        mgr = AuxSessionManager(
            host='127.0.0.1',
            port=0,
            session_timeout_sec=10.0,
            rollover_interval_sec=60.0,
            backup_dir=tmp_dir,
            upload_sender=mock_sender,
        )
        mgr.start()
        try:
            port = mgr.bound_port
            hb_body = json.dumps(
                {
                    'state': 'active',
                    'session_start_unix_ms': 1770000000000,
                    'participant_tag': 'Driver_GT3_Spa_Practice',
                    'metadata': {
                        'Track name': 'Spa-Francorchamps',
                        'Date': '2026-11-11',
                        'Time': '20:00:00',
                        'Driver name': 'Max',
                        'League': 'acevo',
                        'Class': 'GT3',
                        'Session': 'Practice',
                    },
                    'upload_target': {
                        'server_address': 'https://brbrdb.brbrkitten.com',
                        'api_key': 'test_api_key_spiker',
                    },
                }
            ).encode('utf-8')

            req = urllib.request.Request(
                f'http://127.0.0.1:{port}/api/session/heartbeat',
                data=hb_body,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                assert resp.status == 200

            # Stream 3 packets (30 samples = 60 ms) in EEG mode, then switch to ECG mode
            sim = MicrobitSimulator(initial_mode='EEG')
            dec = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
            for _ in range(3):
                dec.feed(sim.generate_next_20ms_packet())
                for ev in dec.pop_events():
                    mgr.on_spikerbit_chunk(ev)

            mgr.set_spikerbit_mode('ECG')
            for _ in range(2):
                dec.feed(sim.generate_next_20ms_packet())
                for ev in dec.pop_events():
                    mgr.on_spikerbit_chunk(ev)

            # Stop session via HTTP POST /api/session/stop
            stop_req = urllib.request.Request(
                f'http://127.0.0.1:{port}/api/session/stop',
                data=b'{}',
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(stop_req, timeout=5.0) as resp:
                assert resp.status == 200

            assert len(uploaded_payloads) == 2
            srv, key, comp_bytes = uploaded_payloads[0]
            assert srv == 'https://brbrdb.brbrkitten.com'
            assert key == 'test_api_key_spiker'

            # Decompress first segment (EEG) and verify BRT1 structure
            raw_brt1 = brotli.decompress(comp_bytes)
            verify_dec = Brt1StreamDecoder()
            verify_dec.feed(raw_brt1)
            verify_events = verify_dec.pop_events()

            assert verify_events[0].chunk_type == CHUNK_MAGIC
            assert verify_events[1].chunk_type == CHUNK_METADATA
            assert verify_events[1].metadata is not None
            assert verify_events[1].metadata['stream'] == 'spikerbit'
            assert verify_events[1].metadata['Track name'] == 'Spa-Francorchamps'
            assert 'track_name' not in verify_events[1].metadata
            assert 'spikerbit_mode' not in verify_events[1].metadata
            assert verify_events[1].channels == [
                {'name': 'Time', 'scale': 0.0001},
                {'name': 'EEG', 'scale': 1.0},
            ]
            assert verify_events[2].chunk_type == CHUNK_BASELINE
            assert verify_events[-1].chunk_type == CHUNK_EOS

            total_samples = sum(len(e.samples) for e in verify_events if e.samples)
            assert total_samples == 30

            # Decompress second segment (ECG) and verify channel name switched to ECG
            raw_brt1_ecg = brotli.decompress(uploaded_payloads[1][2])
            verify_dec_ecg = Brt1StreamDecoder()
            verify_dec_ecg.feed(raw_brt1_ecg)
            ecg_events = verify_dec_ecg.pop_events()
            assert ecg_events[1].channels == [
                {'name': 'Time', 'scale': 0.0001},
                {'name': 'ECG', 'scale': 1.0},
            ]
            assert sum(len(e.samples) for e in ecg_events if e.samples) == 20

            # Check local backup .brt1.br file was written
            backups = [f for f in os.listdir(tmp_dir) if f.endswith('.brt1.br')]
            assert len(backups) == 1
        finally:
            mgr.stop()


def test_segment_synthesizes_baseline_if_started_on_delta() -> None:
    """Verify a rolled-over segment starting on a 0x04 Delta chunk synthesizes a 0x03 Baseline."""
    sim = MicrobitSimulator(initial_mode='EEG')
    dec = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
    # First packet has Magic + Baseline + Delta
    dec.feed(sim.generate_next_20ms_packet())
    _ = dec.pop_events()

    # Second packet has ONLY a 0x04 Delta chunk of 10 samples
    dec.feed(sim.generate_next_20ms_packet())
    delta_events = dec.pop_events()
    assert len(delta_events) == 1
    assert delta_events[0].chunk_type == CHUNK_DELTA

    seg = BrotliBrt1Segment(metadata={'Track name': 'RolloverTrack'}, stream_name='spikerbit')
    seg.append_event(delta_events[0])
    comp = seg.finalize()

    raw_brt1 = brotli.decompress(comp)
    vdec = Brt1StreamDecoder()
    vdec.feed(raw_brt1)
    vevents = vdec.pop_events()

    # Must contain Magic, Metadata, synthesized Baseline (1 sample), Delta (9 samples), EOS
    assert vevents[0].chunk_type == CHUNK_MAGIC
    assert vevents[1].chunk_type == CHUNK_METADATA
    assert vevents[2].chunk_type == CHUNK_BASELINE
    assert vevents[3].chunk_type == CHUNK_DELTA
    assert vevents[4].chunk_type == CHUNK_EOS
    total = sum(len(e.samples) for e in vevents if e.samples)
    assert total == 10


def test_dsp_eeg_ecg_emg_modes() -> None:
    """Verify 500 Hz DSP processor across EEG, ECG, and EMG modes."""
    dsp = SpikerbitDspProcessor(fs=500.0, notch_hz=50.0)

    for mode in ('EEG', 'ECG', 'EMG'):
        sim = MicrobitSimulator(initial_mode=mode)
        dec = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
        adcs: List[float] = []
        # Generate 5.0 seconds (250 packets = 2500 samples @ 500 Hz)
        for _ in range(250):
            dec.feed(sim.generate_next_20ms_packet())
            for ev in dec.pop_events():
                if ev.samples:
                    for _, adc_val in ev.samples:
                        adcs.append(adc_val)

        res = dsp.process_window(adcs, mode=mode)
        assert res.mode == mode
        assert res.contact_status == 'GOOD'
        assert res.primary_trace.size == 2500
        assert res.secondary_trace.size == 2500
        assert res.tertiary_trace.size == 2500

        if mode == 'EEG':
            assert len(res.blink_indices) >= 1
            assert 'Engagement:' in res.headline_metric
        elif mode == 'ECG':
            assert len(res.blink_indices) >= 4
            assert 'Heart Rate:' in res.headline_metric
            assert '75.' in res.headline_metric or '74.' in res.headline_metric or '76.' in res.headline_metric
        elif mode == 'EMG':
            assert 'Mean Activation RMS:' in res.headline_metric

    # Verify RAILING detection
    railing_signal = np.full(200, 1023.0, dtype=np.float64)
    assert SpikerbitDspProcessor.evaluate_contact_quality(railing_signal) == 'RAILING'

    # Verify canvas downsampling helper
    coords = downsample_trace_for_canvas(np.sin(np.linspace(0, 10, 1000)) * 50.0, 400, 100.0, 45.0)
    assert len(coords) >= 20
    assert len(coords) % 2 == 0


def test_firmware_flasher() -> None:
    """Verify bundled Intel HEX validation and MICROBIT USB drive detection & flashing."""
    assert validate_intel_hex(DEFAULT_HEX_PATH)
    assert os.path.getsize(DEFAULT_HEX_PATH) > 4096

    with tempfile.TemporaryDirectory() as tmp_root:
        bad_hex = os.path.join(tmp_root, 'bad_checksum.hex')
        with open(bad_hex, 'w', encoding='ascii') as f_bad:
            f_bad.write(':100000000000022031010000590100005B010000A3\n:00000001FF\n')
        assert not validate_intel_hex(bad_hex)

        fake_mb = os.path.join(tmp_root, 'MICROBIT')
        os.makedirs(fake_mb, exist_ok=True)
        with open(os.path.join(fake_mb, 'DETAILS.TXT'), 'w', encoding='utf-8') as f_det:
            f_det.write('# DAPLink Firmware\nUnique ID: 99043602\n')
        with open(os.path.join(fake_mb, 'MICROBIT.HTM'), 'w', encoding='utf-8') as f_htm:
            f_htm.write('<html></html>\n')

        assert is_microbit_mount(fake_mb)
        drives = find_microbit_drives(search_roots=[tmp_root])
        assert fake_mb in drives

        flashed_path = flash_firmware(target_drive=fake_mb, hex_path=DEFAULT_HEX_PATH)
        assert os.path.isfile(flashed_path)
        assert validate_intel_hex(flashed_path)


def _synth_eeg_with_hum(
    duration_sec: float,
    hum_rms: float,
    mains_hz: float = 50.0,
    eeg_amp: float = 35.0,
    fs: float = 500.0,
) -> List[float]:
    n = int(round(duration_sec * fs))
    out: List[float] = []
    for i in range(n):
        t = i / fs
        bio = (
            eeg_amp * math.sin(2.0 * math.pi * 10.0 * t)
            + (0.5 * eeg_amp) * math.sin(2.0 * math.pi * 6.0 * t + 0.4)
        )
        hum = math.sqrt(2.0) * hum_rms * math.sin(2.0 * math.pi * mains_hz * t)
        out.append(max(0.0, min(1023.0, 512.0 + bio + hum)))
    return out


def test_eeg_hum_monitor_states_hysteresis_and_faults() -> None:
    """Verify streaming EegHumMonitor 4-state thresholds, hysteresis, faults, and 60 Hz retuning."""
    mon = EegHumMonitor(fs=500.0, f_mains=50.0)

    # 1. GOOD contact (2.6 ADC RMS 50 Hz + clean bio-signal)
    snap = mon.push_samples(_synth_eeg_with_hum(2.5, hum_rms=2.6))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_GOOD
    assert snap.hum_rms < 10.0
    assert snap.sig_rms > 10.0

    # Hysteresis: 16.0 ADC must stay GOOD (enter MODERATE requires >= 18.0)
    snap = mon.push_samples(_synth_eeg_with_hum(3.0, hum_rms=16.0))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_GOOD

    # 2. MODERATE contact (35.0 ADC RMS)
    snap = mon.push_samples(_synth_eeg_with_hum(3.0, hum_rms=35.0))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_MODERATE
    assert 28.0 <= snap.hum_rms <= 42.0

    # Hysteresis: 14.0 ADC must stay MODERATE (return to GOOD requires < 12.0)
    snap = mon.push_samples(_synth_eeg_with_hum(3.0, hum_rms=14.0))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_MODERATE

    # Hysteresis: 51.0 ADC must stay MODERATE (enter HIGH_HUM requires >= 53.0)
    snap = mon.push_samples(_synth_eeg_with_hum(3.0, hum_rms=51.0))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_MODERATE

    # 3. HIGH_HUM contact (115.7 ADC RMS)
    snap = mon.push_samples(_synth_eeg_with_hum(3.0, hum_rms=115.7))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_HIGH
    assert 105.0 <= snap.hum_rms <= 125.0
    assert snap.hum_p2p_pct_fs > 28.0

    # Hysteresis: 48.5 ADC must stay HIGH_HUM (exit HIGH_HUM requires < 47.0)
    snap = mon.push_samples(_synth_eeg_with_hum(3.5, hum_rms=48.5))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_HIGH

    # History capped at 10 s / 0.25 s = 40 hops
    assert len(mon.history) == 40

    # 4. Dead-electrode rejection: pure 115 ADC 50 Hz hum with 0 bio-signal -> DISCONNECTED_OR_RAILED
    mon.reset()
    snap = mon.push_samples(_synth_eeg_with_hum(2.5, hum_rms=115.0, eeg_amp=0.0))
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_DISCONNECTED
    assert snap.fault_reason == 'FLATLINE'
    assert snap.sig_rms < 1.5

    # 5. Constant 512.0 flatline -> DISCONNECTED_OR_RAILED
    mon.reset()
    snap = mon.push_samples([512.0] * 1000)
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_DISCONNECTED
    assert snap.fault_reason == 'FLATLINE'

    # 6. Constant 1023.0 rail and NaN handling -> DISCONNECTED_OR_RAILED
    mon.reset()
    snap = mon.push_samples([1023.0] * 500)
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_DISCONNECTED
    assert snap.fault_reason == 'RAILED'
    assert snap.rail_pct == 100.0

    mon.reset()
    snap = mon.push_samples([float('nan')] * 500)
    assert snap is not None
    assert snap.state == EegHumMonitor.STATE_DISCONNECTED
    assert snap.fault_reason == 'RAILED'

    # 7. Stale-stream watchdog override
    stale_snap = mon.snapshot(force_stale=True)
    assert stale_snap.state == EegHumMonitor.STATE_DISCONNECTED
    assert stale_snap.fault_reason == 'STALE'

    # 8. 60 Hz mains retuning rejects 50 Hz and detects 60 Hz hum
    mon.set_mains_frequency(60.0)
    snap = mon.push_samples(_synth_eeg_with_hum(2.5, hum_rms=80.0, mains_hz=60.0))
    assert snap is not None
    assert snap.f_mains == 60.0
    assert snap.state == EegHumMonitor.STATE_HIGH


def test_simulator_hum_and_envelope_downsampling() -> None:
    """Verify simulator hum injection, sample freshness tracking, and anti-aliased envelope."""
    # Default simulator stays GOOD
    sim_clean = MicrobitSimulator(initial_mode='EEG')
    dec_clean = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
    adcs_clean: List[float] = []
    for _ in range(125):
        dec_clean.feed(sim_clean.generate_next_20ms_packet())
        for ev in dec_clean.pop_events():
            if ev.samples:
                adcs_clean.extend(val for _, val in ev.samples)

    mon = EegHumMonitor(fs=500.0, f_mains=50.0)
    snap_clean = mon.push_samples(adcs_clean)
    assert snap_clean is not None
    assert snap_clean.state == EegHumMonitor.STATE_GOOD

    # High-hum simulator drives monitor to HIGH_HUM
    sim_hum = MicrobitSimulator(initial_mode='EEG', hum_adc_rms=85.0, mains_hz=50.0)
    dec_hum = Brt1StreamDecoder(SPIKERBIT_CHANNELS)
    adcs_hum: List[float] = []
    for _ in range(125):
        dec_hum.feed(sim_hum.generate_next_20ms_packet())
        for ev in dec_hum.pop_events():
            if ev.samples:
                adcs_hum.extend(val for _, val in ev.samples)

    mon.reset()
    snap_hum = mon.push_samples(adcs_hum)
    assert snap_hum is not None
    assert snap_hum.state == EegHumMonitor.STATE_HIGH
    assert snap_hum.hum_rms >= 70.0

    # Verify SpikerbitSerialClient.last_sample_monotonic updates when samples arrive
    client = SpikerbitSerialClient()
    assert client.last_sample_monotonic == 0.0
    fresh_sim = MicrobitSimulator(initial_mode='EEG')
    client._process_incoming_bytes(fresh_sim.generate_next_20ms_packet(), 0.02)
    assert client.last_sample_monotonic > 0.0

    # Verify downsample_envelope_for_canvas preserves 50 Hz peak-to-peak span where point-sampling aliases
    t = np.arange(1000, dtype=np.float64) / 500.0
    wave_50hz = 160.0 * np.sin(2.0 * math.pi * 50.0 * t)
    point_coords = downsample_trace_for_canvas(wave_50hz, width_px=200, y_center=100.0, y_half_height=45.0)
    point_ys = point_coords[1::2]
    env_coords = downsample_envelope_for_canvas(wave_50hz, width_px=200, y_center=100.0, y_half_height=45.0)
    env_ys = env_coords[1::2]

    # Step=10 aliases 50 Hz at 500 Hz into a flat line (only appended last sample differs),
    # whereas bin envelope preserves the full peak-to-peak span across every bin
    assert (max(point_ys[:-1]) - min(point_ys[:-1])) < 1e-3
    assert (max(env_ys) - min(env_ys)) > 55.0

    # Verify DSP result keeps raw pre-notch waveform in unfiltered_centered
    dsp = SpikerbitDspProcessor(fs=500.0, notch_hz=50.0)
    res = dsp.process_window(adcs_hum, mode='EEG')
    assert res.unfiltered_centered.size == len(adcs_hum)
    assert float(np.std(res.unfiltered_centered)) > float(np.std(res.raw_centered)) + 40.0


def test_studio_ui_hum_banner_non_blocking() -> None:
    """Verify UI hum banner reflects GOOD -> HIGH_HUM -> STALE -> 60 Hz without blocking uploads."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tkinter display unavailable in headless environment: {exc}')

    root.withdraw()
    uploaded_payloads: List[Tuple[str, str, bytes]] = []

    def mock_sender(server_url: str, api_key: str, data: bytes) -> Tuple[int, str]:
        uploaded_payloads.append((server_url, api_key, data))
        return 200, ''

    app = None
    try:
        app = SpikerbitStudioApp(root, aux_port=0, simulate=True)
        app._serial_client.stop()
        app._serial_client._running = True
        app._serial_client._simulate = True
        app._session_mgr._upload_sender = mock_sender
        app.is_window_visible = lambda: True

        # Start an active AUX session so we can verify uploads never block during HIGH_HUM
        app._session_mgr.handle_heartbeat(
            {
                'state': 'active',
                'session_start_unix_ms': 1770000000000,
                'participant_tag': 'HumTestDriver',
                'metadata': {'Track name': 'Monza', 'Driver name': 'Alexey'},
                'upload_target': {'server_address': 'https://brbrdb.brbrkitten.com', 'api_key': 'k'},
            }
        )

        # 1. Clean EEG stream -> GOOD banner & status
        sim_clean = MicrobitSimulator(initial_mode='EEG', hum_adc_rms=2.0)
        for idx in range(125):
            app._serial_client._process_incoming_bytes(sim_clean.generate_next_20ms_packet(), idx * 0.02)
        app._ui_tick()
        assert 'GOOD' in app._lbl_hum_title.cget('text')
        assert 'GOOD' in app._lbl_contact.cget('text')
        assert 'GOOD' in app._lbl_hum_pill.cget('text')

        # 2. High 50 Hz hum stream -> HIGH HUM alert banner, uploads remain non-blocking
        sim_high = MicrobitSimulator(initial_mode='EEG', hum_adc_rms=115.0)
        for idx in range(250):
            app._serial_client._process_incoming_bytes(sim_high.generate_next_20ms_packet(), (125 + idx) * 0.02)
        app._ui_tick()
        assert 'HIGH 50 Hz MAINS HUM' in app._lbl_hum_title.cget('text')
        assert 'HIGH HUM' in app._lbl_contact.cget('text')
        assert 'HIGH HUM' in app._lbl_hum_pill.cget('text')

        app._session_mgr.handle_stop({})
        assert len(uploaded_payloads) == 1

        # 3. Stale stream (> 500 ms without packets) -> STALE warning
        app._serial_client._last_sample_mono = time.monotonic() - 1.0
        app._ui_tick()
        assert 'NO STREAM DATA' in app._lbl_hum_title.cget('text')
        assert 'STALE' in app._lbl_hum_pill.cget('text')
        assert 'NO DATA' in app._lbl_contact.cget('text')

        # 4. Toggle notch dropdown to 60 Hz -> updates EegHumMonitor and DSP
        app._notch_var.set('60 Hz')
        app._on_notch_changed()
        assert app._hum_monitor.f_mains == 60.0
        assert app._dsp.notch_hz == 60.0
    finally:
        if app is not None:
            app._on_close()
        else:
            root.destroy()


def test_child_unmap_and_ble_status_bar_do_not_freeze_ui() -> None:
    """Verify BLE status bar overflow and child widget <Unmap> cannot freeze UI updates."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tkinter display unavailable in headless environment: {exc}')

    root.geometry('1060x720+50+50')
    app = None
    try:
        app = SpikerbitStudioApp(root, aux_port=0, simulate=True)
        app._serial_client.stop()
        base_samples = app._serial_client.samples_received
        app._serial_client._running = True
        app._serial_client._simulate = False
        app._serial_client._is_ble = True
        app._serial_client._port_name = 'BLE://EC:94:CB:51:A4:F2 (Spiker:bit A4F2)'
        app._serial_client._clock_ctrl.min_rtt_ms = 12.4
        app._session_mgr._upload_sender = lambda *_args: (200, '')

        app._session_mgr.handle_heartbeat(
            {
                'state': 'active',
                'session_start_unix_ms': 1770000000000,
                'participant_tag': 'BleOverflowDriver',
                'metadata': {
                    'Track name': 'Willow Springs Horse Thief Mile',
                    'Driver name': 'Alexey',
                },
                'upload_target': {'server_address': '', 'api_key': ''},
            }
        )
        root.update()
        assert app._window_mapped is True
        assert app.is_window_visible() is True

        # Feed > 0.8 s (45 packets = 450 samples) of HIGH_HUM so monitor exits WARMING_UP
        sim = MicrobitSimulator(initial_mode='EEG', hum_adc_rms=115.0)
        for idx in range(45):
            app._serial_client._process_incoming_bytes(sim.generate_next_20ms_packet(), idx * 0.02)

        app._ui_tick()
        root.update()
        assert app._window_mapped is True
        assert app.is_window_visible() is True
        assert app._lbl_session.winfo_ismapped() == 1
        assert 'BLE Spiker:bit A4F2' in app._lbl_clock.cget('text')
        assert f'Samples: {base_samples + 450}' in app._lbl_clock.cget('text')

        # Unmapping a child widget inside root must never clear _window_mapped
        temp_child = tk.Label(root, text='temporary')
        temp_child.pack()
        root.update()
        temp_child.pack_forget()
        root.update()
        assert app._window_mapped is True
        assert app.is_window_visible() is True

        # Subsequent ticks continue updating UI when new packets arrive
        for idx in range(45, 55):
            app._serial_client._process_incoming_bytes(sim.generate_next_20ms_packet(), idx * 0.02)
        app._ui_tick()
        root.update()
        assert f'Samples: {base_samples + 550}' in app._lbl_clock.cget('text')
    finally:
        if app is not None:
            app._on_close()
        else:
            root.destroy()
