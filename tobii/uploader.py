"""Local CommandSink HTTP server (127.0.0.1:18921) and dual-stream Brotli BRT1 uploader."""

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import queue
from socketserver import ThreadingMixIn
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import urllib.error
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


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


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


@dataclass
class SessionContext:
    """Tracks the active racing session signalled by CommandSink."""

    active: bool = False
    session_start_unix_ms: int = 0
    participant_tag: str = ''
    metadata: Dict[str, Any] = field(default_factory=dict)
    server_address: str = 'https://brbrdb.brbrkitten.com'
    api_key: str = ''
    last_heartbeat_mono: float = 0.0
    gaze_samples_recorded: int = 0
    face_samples_recorded: int = 0
    segments_uploaded: int = 0
    bytes_uploaded: int = 0
    last_upload_error: str = ''


@dataclass
class _UploadJob:
    segment: BrotliBrt1Segment
    server_address: str
    api_key: str
    start_ms: int
    tag: str


class TobiiAuxSessionManager:
    """Coordinates CommandSink session lifecycle and dual (`tobii_gaze`, `tobii_face`) uploads."""

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 18921,
        session_timeout_sec: float = 10.0,
        rollover_interval_sec: float = 60.0,
        backup_dir: Optional[str] = None,
        on_session_start: Optional[Callable[[float, Dict[str, Any]], None]] = None,
        on_session_stop: Optional[Callable[[], None]] = None,
        upload_sender: Optional[Callable[[str, str, bytes], Tuple[int, str]]] = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.session_timeout_sec = float(session_timeout_sec)
        self.rollover_interval_sec = float(rollover_interval_sec)
        self.backup_dir = backup_dir
        self._on_session_start = on_session_start
        self._on_session_stop = on_session_stop
        self._upload_sender = upload_sender or self._default_http_upload

        self._lock = threading.Lock()
        self._ctx = SessionContext()
        self._gaze_segment: Optional[BrotliBrt1Segment] = None
        self._face_segment: Optional[BrotliBrt1Segment] = None
        self._gaze_seg_idx = 0
        self._face_seg_idx = 0

        self._upload_queue: 'queue.Queue[Optional[_UploadJob]]' = queue.Queue()
        self._upload_thread: Optional[threading.Thread] = None
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
                last_heartbeat_mono=self._ctx.last_heartbeat_mono,
                gaze_samples_recorded=self._ctx.gaze_samples_recorded,
                face_samples_recorded=self._ctx.face_samples_recorded,
                segments_uploaded=self._ctx.segments_uploaded,
                bytes_uploaded=self._ctx.bytes_uploaded,
                last_upload_error=self._ctx.last_upload_error,
            )

    @property
    def bound_port(self) -> int:
        """Return actual TCP port bound by the HTTP server."""
        if self._server is not None:
            return int(self._server.server_port)
        return self.port

    def start(self) -> None:
        """Start the local HTTP listener, watchdog thread, and async upload worker."""
        if self._running:
            return
        manager = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(length) if length > 0 else b'{}'
                try:
                    payload = json.loads(raw_body.decode('utf-8'))
                except Exception as exc:
                    self._send_json(400, {'status': 'error', 'message': str(exc)})
                    return

                if self.path == '/api/session/heartbeat':
                    manager.handle_heartbeat(payload)
                    self._send_json(200, {'status': 'ok', 'active': True})
                elif self.path == '/api/session/stop':
                    manager.handle_stop(payload)
                    self._send_json(200, {'status': 'ok', 'active': False})
                else:
                    self._send_json(404, {'status': 'error', 'message': 'Not found'})

            def do_GET(self) -> None:  # noqa: N802
                if self.path == '/api/session/status':
                    ctx = manager.context
                    self._send_json(
                        200,
                        {
                            'status': 'ok',
                            'active': ctx.active,
                            'session_start_unix_ms': ctx.session_start_unix_ms,
                            'participant_tag': ctx.participant_tag,
                            'metadata': ctx.metadata,
                            'gaze_samples_recorded': ctx.gaze_samples_recorded,
                            'face_samples_recorded': ctx.face_samples_recorded,
                            'segments_uploaded': ctx.segments_uploaded,
                            'bytes_uploaded': ctx.bytes_uploaded,
                            'last_upload_error': ctx.last_upload_error,
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

        self._server = _ThreadingHTTPServer((self.host, self.port), _Handler)
        self._running = True
        self._upload_thread = threading.Thread(target=self._upload_worker_loop, daemon=True)
        self._upload_thread.start()
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Stop HTTP server, finalize active streams, and drain pending uploads."""
        self._running = False
        self.handle_stop({})
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._server_thread is not None:
            self._server_thread.join(timeout=1.5)
            self._server_thread = None
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=1.5)
            self._watchdog_thread = None
        if self._upload_thread is not None:
            self._upload_queue.put(None)
            self._upload_thread.join(timeout=5.0)
            self._upload_thread = None

    def wait_for_uploads(self, timeout: float = 5.0) -> None:
        """Block until all queued segment uploads have completed (up to `timeout` seconds)."""
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            if self._upload_queue.unfinished_tasks == 0:
                return
            time.sleep(0.01)

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

    def handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        """Process a `/api/session/heartbeat` signal from `CommandSink`."""
        start_ms = int(payload.get('session_start_unix_ms') or int(time.time() * 1000))
        tag = str(payload.get('participant_tag') or '')
        meta = dict(payload.get('metadata') or {})
        target = dict(payload.get('upload_target') or {})
        server_addr = str(target.get('server_address') or 'https://brbrdb.brbrkitten.com')
        api_key = str(target.get('api_key') or '')

        new_session = False
        jobs_to_enqueue: List[_UploadJob] = []

        with self._lock:
            now_mono = time.monotonic()
            if (
                not self._ctx.active
                or self._ctx.session_start_unix_ms != start_ms
                or self._ctx.participant_tag != tag
            ):
                if self._ctx.active:
                    for seg in (self._gaze_segment, self._face_segment):
                        if seg is not None and seg.samples_count > 0:
                            jobs_to_enqueue.append(
                                _UploadJob(
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
                self._ctx.server_address = server_addr
                self._ctx.api_key = api_key
                self._ctx.last_heartbeat_mono = now_mono
                self._ctx.gaze_samples_recorded = 0
                self._ctx.face_samples_recorded = 0
                self._gaze_seg_idx = 0
                self._face_seg_idx = 0
                self._gaze_segment = self._new_gaze_segment(meta, 0)
                self._face_segment = self._new_face_segment(meta, 0)
                new_session = True
            else:
                self._ctx.last_heartbeat_mono = now_mono
                if meta:
                    self._ctx.metadata = meta
                if server_addr:
                    self._ctx.server_address = server_addr
                if api_key:
                    self._ctx.api_key = api_key

        for job in jobs_to_enqueue:
            self._enqueue_job(job)

        if new_session and self._on_session_start is not None:
            self._on_session_start(start_ms / 1000.0, meta)

    def handle_stop(self, payload: Optional[Dict[str, Any]] = None) -> None:
        """Process a `/api/session/stop` signal or watchdog timeout and finalize uploads."""
        jobs_to_enqueue: List[_UploadJob] = []
        was_active = False

        with self._lock:
            if self._ctx.active:
                was_active = True
                self._ctx.active = False
                for seg in (self._gaze_segment, self._face_segment):
                    if seg is not None and seg.samples_count > 0:
                        jobs_to_enqueue.append(
                            _UploadJob(
                                segment=seg,
                                server_address=self._ctx.server_address,
                                api_key=self._ctx.api_key,
                                start_ms=self._ctx.session_start_unix_ms,
                                tag=self._ctx.participant_tag,
                            )
                        )
                self._gaze_segment = None
                self._face_segment = None

        for job in jobs_to_enqueue:
            self._enqueue_job(job)

        if was_active and self._on_session_stop is not None:
            self._on_session_stop()

    def on_gaze_sample(self, sample_unix_sec: float, sample: GazeSample) -> None:
        """Append a `0x0500` hardware `GazeSample` to the active `tobii_gaze` segment."""
        job: Optional[_UploadJob] = None

        with self._lock:
            if not self._ctx.active or self._gaze_segment is None:
                return
            elapsed_sec = float(sample_unix_sec) - (float(self._ctx.session_start_unix_ms) / 1000.0)
            row = sample.to_brt1_row(elapsed_sec)
            self._gaze_segment.append_sample(row)
            self._ctx.gaze_samples_recorded += 1

            now_mono = time.monotonic()
            if (
                (now_mono - self._gaze_segment.started_mono) >= self.rollover_interval_sec
                and self._gaze_segment.samples_count > 0
            ):
                job = _UploadJob(
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
        """Append a `0x050e` shutter-timestamped `FaceSample` to the active `tobii_face` segment."""
        job: Optional[_UploadJob] = None

        with self._lock:
            if not self._ctx.active or self._face_segment is None:
                return
            elapsed_sec = float(sample_unix_sec) - (float(self._ctx.session_start_unix_ms) / 1000.0)
            row = sample.to_brt1_row(elapsed_sec)
            self._face_segment.append_sample(row)
            self._ctx.face_samples_recorded += 1

            now_mono = time.monotonic()
            if (
                (now_mono - self._face_segment.started_mono) >= self.rollover_interval_sec
                and self._face_segment.samples_count > 0
            ):
                job = _UploadJob(
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

    def _enqueue_job(self, job: _UploadJob) -> None:
        if self._upload_thread is not None and self._upload_thread.is_alive():
            self._upload_queue.put(job)
        else:
            self._execute_upload_job(job)

    def _upload_worker_loop(self) -> None:
        while True:
            job = self._upload_queue.get()
            try:
                if job is None:
                    return
                self._execute_upload_job(job)
            finally:
                self._upload_queue.task_done()

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

    def _execute_upload_job(self, job: _UploadJob) -> None:
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

        if not job.server_address:
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
            'User-Agent': 'BrBrDbTelemetry/1.0',
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
