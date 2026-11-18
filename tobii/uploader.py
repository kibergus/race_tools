"""Local CommandSink HTTP server (127.0.0.1:18921) and multi-sink live chunked BRT1 streamer."""

from collections import deque
from dataclasses import dataclass, field
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import queue
import socket
from socketserver import ThreadingMixIn
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple
import urllib.error
from urllib.parse import urlsplit
import urllib.request

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tobii.brt1_protocol import (  # noqa: E402
    STREAM_TOBII_FACE,
    STREAM_TOBII_GAZE,
    TOBII_FACE_CHANNELS,
    TOBII_GAZE_CHANNELS,
    encode_baseline_chunk,
    encode_delta_batch_chunk,
    encode_eos_chunk,
    encode_magic_chunk,
    encode_metadata_chunk,
)
from tobii.mediapipe_worker import FaceSample  # noqa: E402
from tobii.ttp_protocol import GazeSample  # noqa: E402

try:
    import brotli
except ImportError:  # pragma: no cover
    brotli = None  # type: ignore


DEFAULT_USER_AGENT = 'BrBrDbTelemetry/1.0'


@dataclass(frozen=True)
class UploadSinkConfig:
    """Configuration for a single HTTP BRT1 upload target."""

    server_address: str
    api_key: str = ''
    max_retries: int = 5
    buffer_duration_minutes: int = 10

    @property
    def normalized_key(self) -> str:
        """Return canonical target URL key for deduplication."""
        return self.server_address.strip().rstrip('/').lower()


def is_local_host(host: str) -> bool:
    """Return True if `host` points to the local machine."""
    clean = (host or '').strip().lower()
    return clean in ('127.0.0.1', 'localhost', '::1')


def _is_local_address(server_address: str) -> bool:
    """Return True if `server_address` targets localhost or loopback."""
    addr = (server_address or '').strip()
    if not addr:
        return False
    parsed = urlsplit(addr if '://' in addr else f'http://{addr}')
    return is_local_host(parsed.hostname or '')


def parse_sink_spec(server_address: str, api_key: str = '') -> UploadSinkConfig:
    """Create `UploadSinkConfig` with zero-buffering defaults for localhost sinks."""
    addr = server_address.strip()
    local = _is_local_address(addr)
    return UploadSinkConfig(
        server_address=addr,
        api_key=api_key.strip(),
        max_retries=0 if local else 5,
        buffer_duration_minutes=0 if local else 10,
    )


def extract_upload_targets(
    payload: Dict[str, Any],
    extra_sinks: Sequence[UploadSinkConfig] = (),
) -> List[UploadSinkConfig]:
    """Extract and deduplicate HTTP upload targets from a CommandSink heartbeat payload."""
    targets: List[UploadSinkConfig] = []
    seen: set[str] = set()

    def _add(cfg: UploadSinkConfig) -> None:
        key = cfg.normalized_key
        if not key or key in seen:
            return
        seen.add(key)
        targets.append(cfg)

    raw_list = payload.get('upload_targets')
    if isinstance(raw_list, list) and raw_list:
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            addr = str(item.get('server_address') or '').strip()
            if not addr:
                continue
            api_key = str(item.get('api_key') or '').strip()
            local = _is_local_address(addr)
            raw_retries = item.get('max_retries')
            raw_buf = item.get('buffer_duration_minutes')
            if local:
                max_retries = 0
                buf_min = 0
            else:
                max_retries = (
                    int(raw_retries) if raw_retries is not None else 5
                )
                buf_min = int(raw_buf) if raw_buf is not None else 10
            _add(
                UploadSinkConfig(
                    server_address=addr,
                    api_key=api_key,
                    max_retries=max(0, max_retries),
                    buffer_duration_minutes=max(0, buf_min),
                )
            )

    if not targets:
        raw_single = payload.get('upload_target')
        if isinstance(raw_single, dict):
            addr = str(raw_single.get('server_address') or '').strip()
            api_key = str(raw_single.get('api_key') or '').strip()
            if addr:
                _add(parse_sink_spec(addr, api_key))

    for extra in extra_sinks:
        if extra.server_address.strip():
            _add(extra)

    return targets


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != 'win32'

    def server_bind(self) -> None:
        if sys.platform == 'win32':
            excl = getattr(socket, 'SO_EXCLUSIVEADDRUSE', -5)
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
            except OSError:
                pass
        super().server_bind()


def _probe_local_tobii_status(port: int) -> bool:
    """Return True if an existing Tobii AUX server responds on `127.0.0.1:port`."""
    try:
        with urllib.request.urlopen(
            f'http://127.0.0.1:{int(port)}/api/session/status',
            timeout=0.5,
        ) as resp:
            if int(resp.status) != 200:
                return False
            data = json.loads(resp.read().decode('utf-8'))
            return isinstance(data, dict) and 'gaze_samples_recorded' in data
    except Exception:
        return False


def _request_local_tobii_shutdown(port: int) -> bool:
    """Request an existing loopback Tobii AUX server to shut down cleanly."""
    req = urllib.request.Request(
        f'http://127.0.0.1:{int(port)}/api/shutdown',
        data=b'{}',
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            return int(resp.status) == 200
    except Exception:
        return False


class StreamingBrotliCompressor:
    """Wrapper around `brotli.Compressor(quality=4, lgwin=22)` for streaming BRT1 chunks."""

    def __init__(self, quality: int = 4, lgwin: int = 22) -> None:
        if brotli is None:
            raise RuntimeError('brotli Python package is required for Content-Encoding: br')
        self._comp = brotli.Compressor(
            mode=brotli.MODE_GENERIC,
            quality=quality,
            lgwin=lgwin,
        )

    def process(self, data: bytes) -> bytes:
        """Feed uncompressed bytes and return any immediately available compressed output."""
        if not data:
            return b''
        return self._comp.process(data)

    def flush(self) -> bytes:
        """Flush pending Brotli blocks so the receiver can decompress up to this point."""
        return self._comp.flush()

    def finish(self) -> bytes:
        """Finalize the Brotli stream."""
        return self._comp.finish()


class BrotliBrt1Segment:
    """Accumulates a self-contained Brotli-compressed BRT1 segment for a Tobii stream."""

    def __init__(
        self,
        metadata: Dict[str, Any],
        channels: Sequence[Dict[str, Any]],
        stream_name: str,
        segment_index: int = 0,
        baseline_interval_samples: int = 450,
        quality: int = 4,
    ) -> None:
        self.metadata = dict(metadata)
        self.channels = [dict(ch) for ch in channels]
        self.scales = [float(ch['scale']) for ch in self.channels]
        self.stream_name = stream_name
        self.segment_index = int(segment_index)
        self.baseline_interval_samples = max(1, int(baseline_interval_samples))
        self.started_mono = time.monotonic()
        self.samples_count = 0
        self.samples_since_baseline = 0
        self.has_baseline = False
        self._tracked: List[float] = [0.0 for _ in self.channels]
        self._comp = StreamingBrotliCompressor(quality=quality)
        self._compressed_buf = bytearray()
        self._finalized = False

        header_bytes = encode_magic_chunk() + encode_metadata_chunk(
            self.metadata,
            channels=self.channels,
            stream_name=self.stream_name,
        )
        self._compressed_buf.extend(self._comp.process(header_bytes))
        self._compressed_buf.extend(self._comp.flush())

    def append_sample(self, row: Sequence[float]) -> None:
        """Append a single sample row, emitting `0x03` Baseline or `0x04` Delta automatically."""
        if self._finalized:
            return
        if len(row) != len(self.channels):
            raise ValueError(
                f'Expected {len(self.channels)} values for {self.stream_name}, got {len(row)}'
            )

        if (
            not self.has_baseline
            or self.samples_since_baseline >= self.baseline_interval_samples
        ):
            self._emit_baseline(row)
            return

        delta_raw, needs_baseline = encode_delta_batch_chunk(
            [row], self._tracked, self.scales
        )
        if needs_baseline:
            self._emit_baseline(row)
        else:
            self._compressed_buf.extend(self._comp.process(delta_raw))
            self.samples_count += 1
            self.samples_since_baseline += 1

    def _emit_baseline(self, row: Sequence[float]) -> None:
        base_raw = encode_baseline_chunk(row)
        self._tracked = [float(v) for v in row]
        self.has_baseline = True
        self.samples_count += 1
        self.samples_since_baseline = 1
        self._compressed_buf.extend(self._comp.process(base_raw))

    def finalize(self) -> bytes:
        """Append 0xFF End-Of-Stream chunk, finish Brotli stream, and return compressed bytes."""
        if not self._finalized:
            self._compressed_buf.extend(self._comp.process(encode_eos_chunk()))
            self._compressed_buf.extend(self._comp.finish())
            self._finalized = True
        return bytes(self._compressed_buf)


class _RetryBackoff:
    """Exponential reconnect backoff matching C++ HttpSink."""

    def __init__(
        self,
        window_sec: float = 600.0,
        max_attempts: int = 8,
        base_delay_sec: float = 1.0,
    ) -> None:
        self._window_sec = float(window_sec)
        self._max_attempts = max(1, int(max_attempts))
        self._base_delay_sec = max(0.05, float(base_delay_sec))
        self._attempts: List[float] = []

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_sec
        while self._attempts and self._attempts[0] < cutoff:
            self._attempts.pop(0)

    def can_attempt(self, now: float) -> bool:
        self._prune(now)
        if not self._attempts:
            return True
        n = min(len(self._attempts), self._max_attempts)
        delay = self._base_delay_sec * float(1 << n)
        return (now - self._attempts[-1]) >= delay

    def record_attempt(self, now: float) -> None:
        self._prune(now)
        self._attempts.append(now)

    def reset(self) -> None:
        self._attempts.clear()


class Brt1HttpStreamWorker:
    """Dedicated worker thread streaming live chunked BRT1 bytes to a single HTTP sink."""

    def __init__(
        self,
        sink: UploadSinkConfig,
        metadata: Dict[str, Any],
        channels: Sequence[Dict[str, Any]],
        stream_name: str,
        baseline_interval_samples: int = 450,
        rollover_interval_sec: float = 60.0,
        on_stats: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        self.sink = sink
        self.metadata = dict(metadata)
        self.channels = [dict(ch) for ch in channels]
        self.scales = [float(ch['scale']) for ch in self.channels]
        self.stream_name = stream_name
        self.baseline_interval_samples = max(1, int(baseline_interval_samples))
        self.rollover_interval_sec = max(0.2, float(rollover_interval_sec))
        self._on_stats = on_stats

        self._cond = threading.Condition()
        pending_cap = 256 if sink.buffer_duration_minutes <= 0 else None
        self._pending: Deque[Tuple[float, Tuple[float, ...]]] = deque(maxlen=pending_cap)
        self._replay: Deque[Tuple[float, Tuple[float, ...]]] = deque()
        self._buffer_sec = max(0.0, float(sink.buffer_duration_minutes) * 60.0)
        self._stop_requested = False

        self._conn: Optional[http.client.HTTPConnection] = None
        self._is_connected = False
        self._connected_mono = 0.0
        self._backoff = _RetryBackoff(
            window_sec=600.0,
            max_attempts=2 if sink.buffer_duration_minutes <= 0 else 4,
            base_delay_sec=0.15 if sink.buffer_duration_minutes <= 0 else 1.0,
        )
        self._has_baseline = False
        self._samples_since_baseline = 0
        self._tracked: List[float] = [0.0 for _ in self.channels]
        self._samples_sent_on_conn = 0

        self._thread = threading.Thread(
            target=self._run,
            name=f'Brt1Stream-{stream_name}',
            daemon=True,
        )
        self._thread.start()

    @property
    def is_connected(self) -> bool:
        """Return True while an active HTTP upload connection is open."""
        return self._is_connected

    def push_sample(self, mono_ts: float, row: Sequence[float]) -> None:
        """Enqueue a telemetry sample for immediate live streaming."""
        row_t = tuple(float(v) for v in row)
        with self._cond:
            if self._stop_requested:
                return
            self._pending.append((float(mono_ts), row_t))
            self._cond.notify()

    def close(self, timeout: float = 5.0) -> None:
        """Signal shutdown, flush/finalize stream, and join the worker thread."""
        with self._cond:
            self._stop_requested = True
            self._cond.notify_all()
        effective_timeout = 0.8 if self.sink.max_retries <= 0 else max(0.5, float(timeout))
        self._thread.join(timeout=effective_timeout)

    def _report_stats(self, seg_inc: int, bytes_inc: int, err: str) -> None:
        if self._on_stats is not None:
            self._on_stats(seg_inc, bytes_inc, err)

    def _reset_encoder(self) -> None:
        self._has_baseline = False
        self._samples_since_baseline = 0
        self._tracked = [0.0 for _ in self.channels]

    def _encode_batch(self, rows: Sequence[Tuple[float, ...]]) -> bytes:
        out = bytearray()
        delta_run: List[Tuple[float, ...]] = []
        run_tracked = list(self._tracked)

        def _flush_run() -> None:
            nonlocal run_tracked
            if not delta_run:
                return
            chunk_bytes, _ = encode_delta_batch_chunk(delta_run, self._tracked, self.scales)
            out.extend(chunk_bytes)
            delta_run.clear()
            run_tracked = list(self._tracked)

        for row in rows:
            if (
                not self._has_baseline
                or self._samples_since_baseline >= self.baseline_interval_samples
            ):
                _flush_run()
                out.extend(encode_baseline_chunk(row))
                self._tracked = list(row)
                run_tracked = list(row)
                self._has_baseline = True
                self._samples_since_baseline = 1
                continue

            probe = list(run_tracked)
            _, needs_base = encode_delta_batch_chunk([row], probe, self.scales)
            if needs_base:
                _flush_run()
                out.extend(encode_baseline_chunk(row))
                self._tracked = list(row)
                run_tracked = list(row)
                self._has_baseline = True
                self._samples_since_baseline = 1
            else:
                delta_run.append(row)
                run_tracked = probe
                self._samples_since_baseline += 1

        _flush_run()
        return bytes(out)

    def _parse_endpoint(self) -> Tuple[str, str, int, str]:
        addr = self.sink.server_address.strip()
        parsed = urlsplit(addr if '://' in addr else f'http://{addr}')
        scheme = (parsed.scheme or 'http').lower()
        host = parsed.hostname or '127.0.0.1'
        port = parsed.port or (443 if scheme == 'https' else 80)
        path = parsed.path or ''
        if path in ('', '/'):
            path = '/api/upload/stream'
        elif not path.endswith('/api/upload/stream'):
            path = path.rstrip('/') + '/api/upload/stream'
        if parsed.query:
            path = f'{path}?{parsed.query}'
        return scheme, host, int(port), path

    def _connect(self) -> bool:
        self._disconnect()
        try:
            scheme, host, port, path = self._parse_endpoint()
            timeout_s = 1.5 if is_local_host(host) else 10.0
            if scheme == 'https':
                conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                    host, port, timeout=timeout_s
                )
            else:
                conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
            conn.connect()
            if conn.sock is not None:
                try:
                    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass

            conn.putrequest('POST', path)
            conn.putheader('Content-Type', 'application/octet-stream')
            conn.putheader('Transfer-Encoding', 'chunked')
            conn.putheader('User-Agent', DEFAULT_USER_AGENT)
            if self.sink.api_key:
                conn.putheader('X-API-Key', self.sink.api_key)
            conn.endheaders()

            self._conn = conn
            self._connected_mono = time.monotonic()
            self._samples_sent_on_conn = 0
            self._reset_encoder()

            header_bytes = encode_magic_chunk() + encode_metadata_chunk(
                self.metadata,
                channels=self.channels,
                stream_name=self.stream_name,
            )
            self._write_http_chunk(header_bytes)
            self._is_connected = True
            return True
        except Exception as exc:
            self._disconnect()
            if self.sink.max_retries > 0:
                self._report_stats(0, 0, str(exc))
            return False

    def _disconnect(self) -> None:
        self._is_connected = False
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _write_http_chunk(self, payload: bytes) -> None:
        if not payload:
            return
        if self._conn is None:
            raise OSError('Not connected')
        frame = f'{len(payload):x}\r\n'.encode('ascii') + payload + b'\r\n'
        self._conn.send(frame)
        self._report_stats(0, len(payload), '')

    def _finalize(self) -> bool:
        if self._conn is None:
            return False
        ok = False
        try:
            eos = encode_eos_chunk()
            frame = f'{len(eos):x}\r\n'.encode('ascii') + eos + b'\r\n0\r\n\r\n'
            self._conn.send(frame)
            resp = self._conn.getresponse()
            resp.read()
            if 200 <= resp.status < 300:
                ok = True
                self._report_stats(1, len(eos), '')
            else:
                if self.sink.max_retries > 0:
                    self._report_stats(0, 0, f'HTTP {resp.status} {resp.reason}')
        except Exception as exc:
            if self.sink.max_retries > 0:
                self._report_stats(0, 0, str(exc))
        finally:
            self._disconnect()
        return ok

    def _prune_replay(self, now_mono: float) -> None:
        if self._buffer_sec <= 0.0:
            self._replay.clear()
            return
        cutoff = now_mono - self._buffer_sec
        while self._replay and self._replay[0][0] < cutoff:
            self._replay.popleft()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._stop_requested:
                    self._cond.wait(timeout=0.5)
                stop_now = self._stop_requested
                batch: List[Tuple[float, Tuple[float, ...]]] = []
                while self._pending:
                    batch.append(self._pending.popleft())

            now_mono = time.monotonic()
            if batch and self._buffer_sec > 0.0:
                self._replay.extend(batch)
                self._prune_replay(now_mono)

            if self._conn is not None and self._samples_sent_on_conn > 0:
                if (now_mono - self._connected_mono) >= self.rollover_interval_sec:
                    if self._finalize():
                        self._replay.clear()

            if self._conn is None and (batch or (self._replay and not stop_now)):
                if self._backoff.can_attempt(now_mono):
                    self._backoff.record_attempt(now_mono)
                    if self._connect():
                        self._backoff.reset()
                        replay_rows = (
                            [r for _, r in self._replay]
                            if self._buffer_sec > 0.0
                            else [r for _, r in batch]
                        )
                        if replay_rows:
                            try:
                                encoded = self._encode_batch(replay_rows)
                                self._write_http_chunk(encoded)
                                self._samples_sent_on_conn += len(replay_rows)
                                batch = []
                            except Exception:
                                self._disconnect()

            if self._conn is not None and batch:
                rows = [r for _, r in batch]
                try:
                    encoded = self._encode_batch(rows)
                    self._write_http_chunk(encoded)
                    self._samples_sent_on_conn += len(rows)
                except Exception:
                    self._disconnect()

            if stop_now:
                break

        if self._conn is not None:
            if self._finalize():
                self._replay.clear()
                return

        if self._replay and self.sink.max_retries > 0:
            for attempt in range(1, self.sink.max_retries + 1):
                if self._connect():
                    try:
                        encoded = self._encode_batch([r for _, r in self._replay])
                        self._write_http_chunk(encoded)
                        if self._finalize():
                            self._replay.clear()
                            return
                    except Exception:
                        self._disconnect()
                if attempt < self.sink.max_retries:
                    time.sleep(min(float(1 << attempt), 16.0))
        self._disconnect()


@dataclass
class SessionContext:
    """Tracks the active racing session signalled by CommandSink."""

    active: bool = False
    session_start_unix_ms: int = 0
    participant_tag: str = ''
    metadata: Dict[str, Any] = field(default_factory=dict)
    server_address: str = 'https://brbrdb.brbrkitten.com'
    api_key: str = ''
    upload_targets: List[UploadSinkConfig] = field(default_factory=list)
    last_heartbeat_mono: float = 0.0
    gaze_samples_recorded: int = 0
    face_samples_recorded: int = 0
    segments_uploaded: int = 0
    bytes_uploaded: int = 0
    last_upload_error: str = ''
    standby_gaze_pushed: int = 0
    standby_connected: bool = False


@dataclass
class _BackupJob:
    segment: BrotliBrt1Segment
    server_address: str
    api_key: str
    start_ms: int
    tag: str


@dataclass
class _StreamWorkerPair:
    sink: UploadSinkConfig
    gaze: Brt1HttpStreamWorker
    face: Brt1HttpStreamWorker


class TobiiAuxSessionManager:
    """Coordinates CommandSink session lifecycle and multi-sink live BRT1 streaming."""

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 18921,
        session_timeout_sec: float = 10.0,
        rollover_interval_sec: float = 60.0,
        backup_dir: Optional[str] = None,
        extra_sinks: Optional[Sequence[UploadSinkConfig]] = None,
        standby_sinks: Optional[Sequence[UploadSinkConfig]] = None,
        on_session_start: Optional[Callable[[float, Dict[str, Any]], None]] = None,
        on_session_stop: Optional[Callable[[], None]] = None,
        upload_sender: Optional[Callable[[str, str, bytes], Tuple[int, str]]] = None,
        on_shutdown_request: Optional[Callable[[], None]] = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.session_timeout_sec = float(session_timeout_sec)
        self.rollover_interval_sec = float(rollover_interval_sec)
        self.backup_dir = backup_dir
        self.extra_sinks = list(extra_sinks or ())
        self.standby_sinks: List[UploadSinkConfig] = [
            parse_sink_spec(s.server_address, s.api_key)
            for s in (standby_sinks or ())
            if _is_local_address(s.server_address)
        ]
        self._on_session_start = on_session_start
        self._on_session_stop = on_session_stop
        self._upload_sender = upload_sender
        self._on_shutdown_request = on_shutdown_request

        self._lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._ctx = SessionContext()
        self._stream_workers: List[_StreamWorkerPair] = []
        self._standby_gaze_workers: List[Brt1HttpStreamWorker] = []
        self._standby_start_sec: Optional[float] = None
        self._gaze_segment: Optional[BrotliBrt1Segment] = None
        self._face_segment: Optional[BrotliBrt1Segment] = None
        self._gaze_seg_idx = 0
        self._face_seg_idx = 0

        self._backup_queue: 'queue.Queue[Optional[_BackupJob]]' = queue.Queue()
        self._backup_thread: Optional[threading.Thread] = None
        self._server: Optional[_ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def context(self) -> SessionContext:
        """Return a thread-safe snapshot of the current `SessionContext`."""
        with self._lock:
            return SessionContext(
                active=self._ctx.active,
                session_start_unix_ms=self._ctx.session_start_unix_ms,
                participant_tag=self._ctx.participant_tag,
                metadata=dict(self._ctx.metadata),
                server_address=self._ctx.server_address,
                api_key=self._ctx.api_key,
                upload_targets=list(self._ctx.upload_targets),
                last_heartbeat_mono=self._ctx.last_heartbeat_mono,
                gaze_samples_recorded=self._ctx.gaze_samples_recorded,
                face_samples_recorded=self._ctx.face_samples_recorded,
                segments_uploaded=self._ctx.segments_uploaded,
                bytes_uploaded=self._ctx.bytes_uploaded,
                last_upload_error=self._ctx.last_upload_error,
                standby_gaze_pushed=self._ctx.standby_gaze_pushed,
                standby_connected=any(
                    w.is_connected for w in self._standby_gaze_workers
                ),
            )

    @property
    def bound_port(self) -> int:
        """Return actual TCP port bound by the HTTP server."""
        if self._server is not None:
            return int(self._server.server_port)
        return self.port

    def start(self) -> None:
        """Start the local HTTP listener, watchdog thread, and backup/legacy upload worker."""
        if self._running:
            return
        manager = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                req_path = urlsplit(self.path).path
                if req_path == '/api/shutdown':
                    peer_ip = str(self.client_address[0] if self.client_address else '')
                    if peer_ip not in ('127.0.0.1', '::1'):
                        self._send_json(403, {'status': 'error', 'message': 'Loopback only'})
                        return
                    self._send_json(200, {'status': 'ok', 'shutting_down': True})
                    cb = manager._on_shutdown_request
                    target = cb if callable(cb) else manager.stop
                    threading.Thread(target=target, daemon=True).start()
                    return
                length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(length) if length > 0 else b'{}'
                try:
                    payload = json.loads(raw_body.decode('utf-8'))
                except Exception as exc:
                    self._send_json(400, {'status': 'error', 'message': str(exc)})
                    return

                if req_path == '/api/session/heartbeat':
                    manager.handle_heartbeat(payload)
                    self._send_json(200, {'status': 'ok', 'active': True})
                elif req_path == '/api/session/stop':
                    manager.handle_stop(payload)
                    self._send_json(200, {'status': 'ok', 'active': False})
                else:
                    self._send_json(404, {'status': 'error', 'message': 'Not found'})

            def do_GET(self) -> None:  # noqa: N802
                if urlsplit(self.path).path == '/api/session/status':
                    ctx = manager.context
                    self._send_json(
                        200,
                        {
                            'status': 'ok',
                            'active': ctx.active,
                            'session_start_unix_ms': ctx.session_start_unix_ms,
                            'participant_tag': ctx.participant_tag,
                            'metadata': ctx.metadata,
                            'upload_targets': [
                                t.server_address for t in ctx.upload_targets
                            ],
                            'gaze_samples_recorded': ctx.gaze_samples_recorded,
                            'face_samples_recorded': ctx.face_samples_recorded,
                            'segments_uploaded': ctx.segments_uploaded,
                            'bytes_uploaded': ctx.bytes_uploaded,
                            'last_upload_error': ctx.last_upload_error,
                            'standby_gaze_pushed': ctx.standby_gaze_pushed,
                            'standby_connected': ctx.standby_connected,
                        },
                    )
                else:
                    self._send_json(404, {'status': 'error', 'message': 'Not found'})

            def _send_json(self, code: int, obj: Dict[str, Any]) -> None:
                body = json.dumps(obj).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format_str: str, *args: Any) -> None:
                return

        try:
            self._server = _ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError:
            if (
                self.port > 0
                and _probe_local_tobii_status(self.port)
                and _request_local_tobii_shutdown(self.port)
            ):
                deadline = time.monotonic() + 1.5
                while True:
                    time.sleep(0.15)
                    try:
                        self._server = _ThreadingHTTPServer((self.host, self.port), _Handler)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
            else:
                raise
        self._running = True
        self._backup_thread = threading.Thread(target=self._backup_worker_loop, daemon=True)
        self._backup_thread.start()
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Stop HTTP server, finalize active streams, and drain pending uploads."""
        with self._stop_lock:
            self._running = False
            self.handle_stop({})
            with self._lock:
                standby_to_close = list(self._standby_gaze_workers)
                self._standby_gaze_workers.clear()
                self._standby_start_sec = None
            for worker in standby_to_close:
                worker.close()
            srv = self._server
            self._server = None
            if srv is not None:
                srv.shutdown()
                srv.server_close()
            if self._server_thread is not None:
                self._server_thread.join(timeout=1.5)
                self._server_thread = None
            if self._watchdog_thread is not None:
                self._watchdog_thread.join(timeout=1.5)
                self._watchdog_thread = None
            if self._backup_thread is not None:
                self._backup_queue.put(None)
                self._backup_thread.join(timeout=5.0)
                self._backup_thread = None

    def wait_for_uploads(self, timeout: float = 5.0) -> None:
        """Block until all queued segment backups/uploads have completed."""
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            if self._backup_queue.unfinished_tasks == 0:
                with self._lock:
                    workers_alive = bool(self._stream_workers)
                if not workers_alive:
                    return
            time.sleep(0.01)

    def _on_worker_stats(self, seg_inc: int, bytes_inc: int, err: str) -> None:
        with self._lock:
            self._ctx.segments_uploaded += int(seg_inc)
            self._ctx.bytes_uploaded += int(bytes_inc)
            if err:
                self._ctx.last_upload_error = err
            elif seg_inc > 0:
                self._ctx.last_upload_error = ''

    def _needs_segment_recorder(self) -> bool:
        return bool(self.backup_dir) or (self._upload_sender is not None)

    def _new_gaze_segment(self, meta: Dict[str, Any], seg_idx: int) -> BrotliBrt1Segment:
        return BrotliBrt1Segment(
            metadata=meta,
            channels=TOBII_GAZE_CHANNELS,
            stream_name=STREAM_TOBII_GAZE,
            segment_index=seg_idx,
            baseline_interval_samples=450,
        )

    def _new_face_segment(self, meta: Dict[str, Any], seg_idx: int) -> BrotliBrt1Segment:
        return BrotliBrt1Segment(
            metadata=meta,
            channels=TOBII_FACE_CHANNELS,
            stream_name=STREAM_TOBII_FACE,
            segment_index=seg_idx,
            baseline_interval_samples=150,
        )

    def _create_workers(
        self,
        targets: Sequence[UploadSinkConfig],
        meta: Dict[str, Any],
    ) -> List[_StreamWorkerPair]:
        if self._upload_sender is not None:
            return []
        pairs: List[_StreamWorkerPair] = []
        for sink in targets:
            gaze_w = Brt1HttpStreamWorker(
                sink=sink,
                metadata=meta,
                channels=TOBII_GAZE_CHANNELS,
                stream_name=STREAM_TOBII_GAZE,
                baseline_interval_samples=450,
                rollover_interval_sec=self.rollover_interval_sec,
                on_stats=self._on_worker_stats,
            )
            face_w = Brt1HttpStreamWorker(
                sink=sink,
                metadata=meta,
                channels=TOBII_FACE_CHANNELS,
                stream_name=STREAM_TOBII_FACE,
                baseline_interval_samples=150,
                rollover_interval_sec=self.rollover_interval_sec,
                on_stats=self._on_worker_stats,
            )
            pairs.append(_StreamWorkerPair(sink=sink, gaze=gaze_w, face=face_w))
        return pairs

    def _close_workers(self, pairs: Sequence[_StreamWorkerPair]) -> None:
        for pair in pairs:
            pair.gaze.close()
            pair.face.close()

    def _create_standby_workers(self) -> List[Brt1HttpStreamWorker]:
        if self._upload_sender is not None or not self.standby_sinks:
            return []
        meta = {'standby': True}
        workers: List[Brt1HttpStreamWorker] = []
        for sink in self.standby_sinks:
            workers.append(
                Brt1HttpStreamWorker(
                    sink=sink,
                    metadata=meta,
                    channels=TOBII_GAZE_CHANNELS,
                    stream_name=STREAM_TOBII_GAZE,
                    baseline_interval_samples=450,
                    rollover_interval_sec=self.rollover_interval_sec,
                    on_stats=None,
                )
            )
        return workers

    def handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        """Process a `/api/session/heartbeat` signal from `CommandSink`."""
        start_ms = int(payload.get('session_start_unix_ms') or int(time.time() * 1000))
        tag = str(payload.get('participant_tag') or '')
        meta = dict(payload.get('metadata') or {})
        targets = extract_upload_targets(
            payload, [*self.extra_sinks, *self.standby_sinks]
        )
        if not targets and not self._needs_segment_recorder():
            targets = [parse_sink_spec('https://brbrdb.brbrkitten.com')]

        primary_addr = targets[0].server_address if targets else ''
        primary_key = targets[0].api_key if targets else ''

        new_session = False
        jobs_to_enqueue: List[_BackupJob] = []
        old_workers: List[_StreamWorkerPair] = []
        standby_to_close: List[Brt1HttpStreamWorker] = []

        with self._lock:
            standby_to_close = list(self._standby_gaze_workers)
            self._standby_gaze_workers.clear()
            self._standby_start_sec = None
            now_mono = time.monotonic()
            if (
                not self._ctx.active
                or self._ctx.session_start_unix_ms != start_ms
                or self._ctx.participant_tag != tag
            ):
                if self._ctx.active:
                    old_workers = list(self._stream_workers)
                    self._stream_workers.clear()
                    for seg in (self._gaze_segment, self._face_segment):
                        if seg is not None and seg.samples_count > 0:
                            jobs_to_enqueue.append(
                                _BackupJob(
                                    segment=seg,
                                    server_address=self._ctx.server_address,
                                    api_key=self._ctx.api_key,
                                    start_ms=self._ctx.session_start_unix_ms,
                                    tag=self._ctx.participant_tag,
                                )
                            )

                self._ctx.active = True
                self._ctx.session_start_unix_ms = start_ms
                self._ctx.participant_tag = tag
                self._ctx.metadata = meta
                self._ctx.server_address = primary_addr
                self._ctx.api_key = primary_key
                self._ctx.upload_targets = list(targets)
                self._ctx.last_heartbeat_mono = now_mono
                self._ctx.gaze_samples_recorded = 0
                self._ctx.face_samples_recorded = 0
                self._gaze_seg_idx = 0
                self._face_seg_idx = 0

                if self._needs_segment_recorder():
                    self._gaze_segment = self._new_gaze_segment(meta, 0)
                    self._face_segment = self._new_face_segment(meta, 0)
                else:
                    self._gaze_segment = None
                    self._face_segment = None

                self._stream_workers = self._create_workers(targets, meta)
                new_session = True
            else:
                self._ctx.last_heartbeat_mono = now_mono
                if meta:
                    self._ctx.metadata = meta
                if targets:
                    old_keys = [t.normalized_key for t in self._ctx.upload_targets]
                    new_keys = [t.normalized_key for t in targets]
                    if old_keys != new_keys:
                        old_workers = list(self._stream_workers)
                        self._stream_workers = self._create_workers(targets, self._ctx.metadata)
                    self._ctx.upload_targets = list(targets)
                    self._ctx.server_address = primary_addr
                    self._ctx.api_key = primary_key

        if old_workers:
            self._close_workers(old_workers)
        for worker in standby_to_close:
            worker.close()
        for job in jobs_to_enqueue:
            self._enqueue_job(job)

        if new_session and self._on_session_start is not None:
            self._on_session_start(start_ms / 1000.0, meta)

    def handle_stop(self, payload: Optional[Dict[str, Any]] = None) -> None:
        """Process a `/api/session/stop` signal or watchdog timeout and finalize streams."""
        jobs_to_enqueue: List[_BackupJob] = []
        workers_to_close: List[_StreamWorkerPair] = []
        was_active = False

        with self._lock:
            if self._ctx.active:
                was_active = True
                self._ctx.active = False
                workers_to_close = list(self._stream_workers)
                self._stream_workers.clear()
                for seg in (self._gaze_segment, self._face_segment):
                    if seg is not None and seg.samples_count > 0:
                        jobs_to_enqueue.append(
                            _BackupJob(
                                segment=seg,
                                server_address=self._ctx.server_address,
                                api_key=self._ctx.api_key,
                                start_ms=self._ctx.session_start_unix_ms,
                                tag=self._ctx.participant_tag,
                            )
                        )
                self._gaze_segment = None
                self._face_segment = None

        if workers_to_close:
            self._close_workers(workers_to_close)
        for job in jobs_to_enqueue:
            self._enqueue_job(job)

        if was_active and self._on_session_stop is not None:
            self._on_session_stop()

    def on_gaze_sample(self, sample_unix_sec: float, sample: GazeSample) -> None:
        """Stream a `0x0500` hardware `GazeSample` live and record local backup if enabled."""
        job: Optional[_BackupJob] = None

        with self._lock:
            now_mono = time.monotonic()
            if not self._ctx.active:
                if (
                    not self._running
                    or not self.standby_sinks
                    or self._upload_sender is not None
                ):
                    return
                if not self._standby_gaze_workers:
                    self._standby_start_sec = float(sample_unix_sec)
                    self._standby_gaze_workers = self._create_standby_workers()
                start_sec = (
                    self._standby_start_sec
                    if self._standby_start_sec is not None
                    else float(sample_unix_sec)
                )
                row = sample.to_brt1_row(float(sample_unix_sec) - start_sec)
                self._ctx.standby_gaze_pushed += 1
                for worker in self._standby_gaze_workers:
                    worker.push_sample(now_mono, row)
                return
            elapsed_sec = float(sample_unix_sec) - (
                float(self._ctx.session_start_unix_ms) / 1000.0
            )
            row = sample.to_brt1_row(elapsed_sec)
            self._ctx.gaze_samples_recorded += 1

            for pair in self._stream_workers:
                pair.gaze.push_sample(now_mono, row)

            if self._gaze_segment is not None:
                self._gaze_segment.append_sample(row)
                if (
                    (now_mono - self._gaze_segment.started_mono) >= self.rollover_interval_sec
                    and self._gaze_segment.samples_count > 0
                ):
                    job = _BackupJob(
                        segment=self._gaze_segment,
                        server_address=self._ctx.server_address,
                        api_key=self._ctx.api_key,
                        start_ms=self._ctx.session_start_unix_ms,
                        tag=self._ctx.participant_tag,
                    )
                    self._gaze_seg_idx += 1
                    self._gaze_segment = self._new_gaze_segment(
                        self._ctx.metadata, self._gaze_seg_idx
                    )

        if job is not None:
            self._enqueue_job(job)

    def on_face_sample(self, sample_unix_sec: float, sample: FaceSample) -> None:
        """Stream a `0x050e` shutter-timestamped `FaceSample` live and record backup if enabled."""
        job: Optional[_BackupJob] = None

        with self._lock:
            if not self._ctx.active:
                return
            elapsed_sec = float(sample_unix_sec) - (float(self._ctx.session_start_unix_ms) / 1000.0)
            row = sample.to_brt1_row(elapsed_sec)
            self._ctx.face_samples_recorded += 1
            now_mono = time.monotonic()

            for pair in self._stream_workers:
                pair.face.push_sample(now_mono, row)

            if self._face_segment is not None:
                self._face_segment.append_sample(row)
                if (
                    (now_mono - self._face_segment.started_mono) >= self.rollover_interval_sec
                    and self._face_segment.samples_count > 0
                ):
                    job = _BackupJob(
                        segment=self._face_segment,
                        server_address=self._ctx.server_address,
                        api_key=self._ctx.api_key,
                        start_ms=self._ctx.session_start_unix_ms,
                        tag=self._ctx.participant_tag,
                    )
                    self._face_seg_idx += 1
                    self._face_segment = self._new_face_segment(
                        self._ctx.metadata, self._face_seg_idx
                    )

        if job is not None:
            self._enqueue_job(job)

    def _enqueue_job(self, job: _BackupJob) -> None:
        if self._backup_thread is not None and self._backup_thread.is_alive():
            self._backup_queue.put(job)
        else:
            self._execute_backup_job(job)

    def _backup_worker_loop(self) -> None:
        while True:
            job = self._backup_queue.get()
            try:
                if job is None:
                    return
                self._execute_backup_job(job)
            finally:
                self._backup_queue.task_done()

    def _watchdog_loop(self) -> None:
        while self._running:
            time.sleep(0.25)
            timed_out = False
            with self._lock:
                if self._ctx.active:
                    elapsed = time.monotonic() - self._ctx.last_heartbeat_mono
                    if elapsed > self.session_timeout_sec:
                        timed_out = True
            if timed_out:
                self.handle_stop({})

    def _execute_backup_job(self, job: _BackupJob) -> None:
        segment = job.segment
        if segment.samples_count == 0:
            return
        compressed_bytes = segment.finalize()

        if self.backup_dir:
            try:
                os.makedirs(self.backup_dir, exist_ok=True)
                sec = int(job.start_ms // 1000)
                tag_part = f'_{job.tag}' if job.tag else ''
                part_part = (
                    f'_part{segment.segment_index + 1}' if segment.segment_index > 0 else ''
                )
                prefix = (
                    'TobiiGaze'
                    if segment.stream_name == STREAM_TOBII_GAZE
                    else 'TobiiFace'
                )
                fname = f'{prefix}_{sec}{tag_part}{part_part}_server.brt1.br'
                fpath = os.path.join(self.backup_dir, fname)
                with open(fpath, 'wb') as f_out:
                    f_out.write(compressed_bytes)
            except OSError:
                pass

        if self._upload_sender is None or not job.server_address:
            return

        status_code, err_msg = self._upload_sender(
            job.server_address, job.api_key, compressed_bytes
        )
        with self._lock:
            if 200 <= status_code < 300:
                self._ctx.segments_uploaded += 1
                self._ctx.bytes_uploaded += len(compressed_bytes)
                self._ctx.last_upload_error = ''
            else:
                self._ctx.last_upload_error = err_msg or f'HTTP {status_code}'

    @staticmethod
    def _default_http_upload(
        server_address: str,
        api_key: str,
        compressed_bytes: bytes,
    ) -> Tuple[int, str]:
        base = server_address.rstrip('/')
        if not base.endswith('/api/upload/stream'):
            url = f'{base}/api/upload/stream'
        else:
            url = base

        headers = {
            'Content-Type': 'application/octet-stream',
            'Content-Encoding': 'br',
            'User-Agent': DEFAULT_USER_AGENT,
        }
        if api_key:
            headers['X-API-Key'] = api_key

        req = urllib.request.Request(url, data=compressed_bytes, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                return int(resp.status), ''
        except urllib.error.HTTPError as http_err:
            return int(http_err.code), str(http_err.reason)
        except Exception as exc:
            return 0, str(exc)
