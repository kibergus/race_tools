"""Unit tests for Spiker:bit BRT1 protocol, clock sync, Brotli uploader, DSP, and flasher."""

import json
import math
import os
import tempfile
import time
from typing import List, Tuple
import urllib.request

import numpy as np

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
from spikerbit.dsp import SpikerbitDspProcessor
from spikerbit.flasher import (
    DEFAULT_HEX_PATH,
    find_microbit_drives,
    flash_firmware,
    is_microbit_mount,
    validate_intel_hex,
)
from spikerbit.main import downsample_trace_for_canvas
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
