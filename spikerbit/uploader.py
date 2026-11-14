"""Local CommandSink HTTP server (127.0.0.1:18920) and Brotli BRT1 auxiliary stream uploader."""

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from socketserver import ThreadingMixIn
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spikerbit.brt1_protocol import (  # noqa: E402
    Brt1DecodedEvent,
    CHUNK_BASELINE,
    CHUNK_DELTA,
    SPIKERBIT_CHANNELS,
    encode_baseline_chunk,
    encode_delta_batch_chunk,
    encode_eos_chunk,
    encode_magic_chunk,
    encode_metadata_chunk,
    make_spikerbit_channels,
)

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
    """Accumulates a self-contained Brotli-compressed BRT1 segment for `"stream": "spikerbit"`."""

    def __init__(
        self,
        metadata: Dict[str, Any],
        channels: Optional[List[Dict[str, Any]]] = None,
        stream_name: str = 'spikerbit',
        quality: int = 4,
    ) -> None:
        self.metadata = dict(metadata)
        self.channels = list(channels) if channels is not None else list(SPIKERBIT_CHANNELS)
        self.scales = [float(ch['scale']) for ch in self.channels]
        self.stream_name = stream_name
        self.started_mono = time.monotonic()
        self.samples_count = 0
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

    def append_event(self, event: Brt1DecodedEvent) -> None:
        """Append a micro:bit 0x03 Baseline or 0x04 Delta chunk to the segment."""
        if self._finalized:
            return

        if event.chunk_type == CHUNK_BASELINE and event.samples:
            self.has_baseline = True
            self._tracked = list(event.samples[0])
            self.samples_count += len(event.samples)
            self._compressed_buf.extend(self._comp.process(event.raw_chunk))
            return

        if event.chunk_type == CHUNK_DELTA and event.samples:
            if not self.has_baseline:
                # Guarantee that the first data chunk of every rollover segment is 0x03 Baseline
                first_sample = event.samples[0]
                base_raw = encode_baseline_chunk(first_sample)
                self._tracked = list(first_sample)
                self.has_baseline = True
                self._compressed_buf.extend(self._comp.process(base_raw))
                remaining = event.samples[1:]
                if remaining:
                    delta_raw, overflow = encode_delta_batch_chunk(
                        remaining, self._tracked, self.scales
                    )
                    if not overflow and delta_raw:
                        self._compressed_buf.extend(self._comp.process(delta_raw))
                self.samples_count += len(event.samples)
            else:
                # Passthrough the micro:bit's native 0x04 Delta chunk directly into Brotli!
                self._tracked = list(event.samples[-1])
                self.samples_count += len(event.samples)
                self._compressed_buf.extend(self._comp.process(event.raw_chunk))

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
    segments_uploaded: int = 0
    bytes_uploaded: int = 0
    last_upload_error: str = ''


class AuxSessionManager:
    """Coordinates CommandSink session lifecycle, 60s rollover, and Brotli HTTP uploads."""

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 18920,
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
        self._spikerbit_mode: str = 'EEG'
        self._segment: Optional[BrotliBrt1Segment] = None
        self._server: Optional[_ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def context(self) -> SessionContext:
        """Return a copy of the current SessionContext."""
        with self._lock:
            return SessionContext(
                active=self._ctx.active,
                session_start_unix_ms=self._ctx.session_start_unix_ms,
                participant_tag=self._ctx.participant_tag,
                metadata=dict(self._ctx.metadata),
                server_address=self._ctx.server_address,
                api_key=self._ctx.api_key,
                last_heartbeat_mono=self._ctx.last_heartbeat_mono,
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

    def set_spikerbit_mode(self, mode: str) -> None:
        """Switch the uploaded raw channel name (`EEG`, `ECG`, or `EMG`) and roll over segment."""
        clean_mode = mode.strip().upper()
        seg_to_rollover: Optional[BrotliBrt1Segment] = None
        server_addr = ''
        api_key = ''
        start_ms = 0
        tag = ''

        with self._lock:
            if self._spikerbit_mode == clean_mode:
                return
            self._spikerbit_mode = clean_mode
            if self._ctx.active and self._segment is not None:
                if self._segment.samples_count > 0:
                    seg_to_rollover = self._segment
                    server_addr = self._ctx.server_address
                    api_key = self._ctx.api_key
                    start_ms = self._ctx.session_start_unix_ms
                    tag = self._ctx.participant_tag
                self._segment = BrotliBrt1Segment(
                    metadata=self._ctx.metadata,
                    channels=make_spikerbit_channels(self._spikerbit_mode),
                    stream_name='spikerbit',
                )

        if seg_to_rollover is not None:
            self._finalize_and_upload_segment(
                seg_to_rollover, server_addr, api_key, start_ms, tag
            )

    def start(self) -> None:
        """Start the local HTTP listener and session watchdog thread."""
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
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Stop HTTP server, finalize any active session, and join background threads."""
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

    def handle_heartbeat(self, payload: Dict[str, Any]) -> None:
        """Process a `/api/session/heartbeat` signal from `CommandSink`."""
        start_ms = int(payload.get('session_start_unix_ms') or int(time.time() * 1000))
        tag = str(payload.get('participant_tag') or '')
        meta = dict(payload.get('metadata') or {})
        target = dict(payload.get('upload_target') or {})
        server_addr = str(target.get('server_address') or 'https://brbrdb.brbrkitten.com')
        api_key = str(target.get('api_key') or '')

        new_session = False
        seg_to_upload: Optional[BrotliBrt1Segment] = None
        prev_server = server_addr
        prev_key = api_key
        prev_tag = tag
        prev_start_ms = start_ms

        with self._lock:
            now_mono = time.monotonic()
            if (
                not self._ctx.active
                or self._ctx.session_start_unix_ms != start_ms
                or self._ctx.participant_tag != tag
            ):
                if self._ctx.active and self._segment is not None:
                    seg_to_upload = self._segment
                    prev_server = self._ctx.server_address
                    prev_key = self._ctx.api_key
                    prev_tag = self._ctx.participant_tag
                    prev_start_ms = self._ctx.session_start_unix_ms

                self._ctx.active = True
                self._ctx.session_start_unix_ms = start_ms
                self._ctx.participant_tag = tag
                self._ctx.metadata = meta
                self._ctx.server_address = server_addr
                self._ctx.api_key = api_key
                self._ctx.last_heartbeat_mono = now_mono
                self._segment = BrotliBrt1Segment(
                    metadata=meta,
                    channels=make_spikerbit_channels(self._spikerbit_mode),
                    stream_name='spikerbit',
                )
                new_session = True
            else:
                self._ctx.last_heartbeat_mono = now_mono
                if meta:
                    self._ctx.metadata = meta
                if server_addr:
                    self._ctx.server_address = server_addr
                if api_key:
                    self._ctx.api_key = api_key

        if seg_to_upload is not None:
            self._finalize_and_upload_segment(
                seg_to_upload, prev_server, prev_key, prev_start_ms, prev_tag
            )

        if new_session and self._on_session_start is not None:
            self._on_session_start(start_ms / 1000.0, meta)

    def handle_stop(self, payload: Optional[Dict[str, Any]] = None) -> None:
        """Process a `/api/session/stop` signal or timeout and finalize upload."""
        seg_to_upload: Optional[BrotliBrt1Segment] = None
        server_addr = ''
        api_key = ''
        start_ms = 0
        tag = ''
        was_active = False

        with self._lock:
            if self._ctx.active:
                was_active = True
                self._ctx.active = False
                seg_to_upload = self._segment
                self._segment = None
                server_addr = self._ctx.server_address
                api_key = self._ctx.api_key
                start_ms = self._ctx.session_start_unix_ms
                tag = self._ctx.participant_tag

        if seg_to_upload is not None:
            self._finalize_and_upload_segment(
                seg_to_upload, server_addr, api_key, start_ms, tag
            )

        if was_active and self._on_session_stop is not None:
            self._on_session_stop()

    def on_spikerbit_chunk(self, event: Brt1DecodedEvent) -> None:
        """Receive a 0x03/0x04 BRT1 chunk from the micro:bit and append if session is active."""
        seg_to_rollover: Optional[BrotliBrt1Segment] = None
        server_addr = ''
        api_key = ''
        start_ms = 0
        tag = ''

        with self._lock:
            if not self._ctx.active or self._segment is None:
                return
            self._segment.append_event(event)
            now_mono = time.monotonic()
            if (
                (now_mono - self._segment.started_mono) >= self.rollover_interval_sec
                and self._segment.samples_count > 0
            ):
                seg_to_rollover = self._segment
                self._segment = BrotliBrt1Segment(
                    metadata=self._ctx.metadata,
                    channels=make_spikerbit_channels(self._spikerbit_mode),
                    stream_name='spikerbit',
                )
                server_addr = self._ctx.server_address
                api_key = self._ctx.api_key
                start_ms = self._ctx.session_start_unix_ms
                tag = self._ctx.participant_tag

        if seg_to_rollover is not None:
            self._finalize_and_upload_segment(
                seg_to_rollover, server_addr, api_key, start_ms, tag
            )

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

    def _finalize_and_upload_segment(
        self,
        segment: BrotliBrt1Segment,
        server_address: str,
        api_key: str,
        start_ms: int,
        tag: str,
    ) -> None:
        if segment.samples_count == 0:
            return
        compressed_bytes = segment.finalize()

        if self.backup_dir:
            try:
                os.makedirs(self.backup_dir, exist_ok=True)
                sec = int(start_ms // 1000)
                suffix = f'_{tag}' if tag else ''
                fname = f'Spikerbit_{sec}{suffix}_server.brt1.br'
                fpath = os.path.join(self.backup_dir, fname)
                with open(fpath, 'wb') as f_out:
                    f_out.write(compressed_bytes)
            except OSError:
                pass

        if not server_address:
            return

        status_code, err_msg = self._upload_sender(server_address, api_key, compressed_bytes)
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
