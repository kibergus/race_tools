"""Local HTTP server accepting live chunked BRT1 telemetry streams."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import queue
import socket
from socketserver import ThreadingMixIn
import struct
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
import zlib

import brotli  # type: ignore[import-untyped]

from live_feedback.overlay import OverlayStateHub
from live_feedback.overlay_static import OVERLAY_HTML


CHUNK_MAGIC = 0x01
CHUNK_METADATA = 0x02
CHUNK_BASELINE = 0x03
CHUNK_DELTA = 0x04
CHUNK_EOS = 0xFF
NAN_SENTINEL = -32768
BACKLOG_BUFFER_THRESHOLD_BYTES = 256


@dataclass(frozen=True)
class DecodedSample:
    """Decoded single telemetry sample plus live-ness indicator."""

    time_s: float
    x: Optional[float]
    z: Optional[float]
    lat: Optional[float]
    lon: Optional[float]
    speed_ms: Optional[float]
    is_live: bool
    throttle_pct: Optional[float] = None
    brake_pct: Optional[float] = None
    clutch_pct: Optional[float] = None


@dataclass(frozen=True)
class DecodedGazeSample:
    """Decoded single live gaze sample plus live-ness indicator."""

    time_s: float
    gaze_x: Optional[float]
    gaze_y: Optional[float]
    gaze_valid: bool
    is_live: bool


def compute_session_key(metadata: Dict[str, Any]) -> str:
    """Derive a stable session identifier so 60s HTTP rollovers preserve session state."""
    meta_lower = {str(k).strip().lower(): v for k, v in metadata.items()}
    session_id = meta_lower.get('session id') or meta_lower.get('session_id')
    if session_id is not None and str(session_id).strip():
        return f'id:{str(session_id).strip().lower()}'

    parts = [
        str(meta_lower.get('league', '')).strip().lower(),
        str(meta_lower.get('class', '')).strip().lower(),
        str(meta_lower.get('track name', '')).strip().lower(),
        str(meta_lower.get('driver name', '')).strip().lower(),
        str(meta_lower.get('session', '')).strip().lower(),
        str(meta_lower.get('date', '')).strip().lower(),
        str(meta_lower.get('time', '')).strip().lower(),
    ]
    return '|'.join(parts)


class Brt1IncrementalDecoder:
    """Incremental parser for BRT1 binary telemetry chunks."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.magic_seen = False
        self.metadata: Optional[Dict[str, Any]] = None
        self.stream_name: str = ''
        self.channels: List[Dict[str, Any]] = []
        self._scales: List[float] = []
        self._tracked: Optional[List[float]] = None
        self.eos_seen = False
        self.last_row_count: int = 0
        self._gaze_samples: List[DecodedGazeSample] = []

        self._idx_time: Optional[int] = None
        self._idx_x: Optional[int] = None
        self._idx_z: Optional[int] = None
        self._idx_lat: Optional[int] = None
        self._idx_lon: Optional[int] = None
        self._idx_speed: Optional[int] = None
        self._idx_throttle: Optional[int] = None
        self._idx_brake: Optional[int] = None
        self._idx_clutch: Optional[int] = None
        self._idx_gaze_x: Optional[int] = None
        self._idx_gaze_y: Optional[int] = None
        self._idx_gaze_unfilt_x: Optional[int] = None
        self._idx_gaze_unfilt_y: Optional[int] = None
        self._idx_left_gaze_x: Optional[int] = None
        self._idx_left_gaze_y: Optional[int] = None
        self._idx_right_gaze_x: Optional[int] = None
        self._idx_right_gaze_y: Optional[int] = None

    def feed(self, data: bytes) -> None:
        """Append uncompressed BRT1 bytes into the parser buffer."""
        if data:
            self._buf.extend(data)

    @property
    def buffered_bytes(self) -> int:
        """Return number of unparsed bytes currently queued in the decoder."""
        return len(self._buf)

    def pop_gaze_samples(self) -> List[DecodedGazeSample]:
        """Return and clear decoded gaze samples collected on `tobii_gaze` streams."""
        out = list(self._gaze_samples)
        self._gaze_samples.clear()
        return out

    def _update_channel_indices(self) -> None:
        name_map = {
            str(ch['name']).strip().lower(): idx
            for idx, ch in enumerate(self.channels)
        }
        self._idx_time = name_map.get('time')
        self._idx_x = name_map.get('x')
        self._idx_z = name_map.get('z')
        self._idx_lat = name_map.get('latitude')
        self._idx_lon = name_map.get('longitude')
        self._idx_speed = name_map.get('speed')
        self._idx_throttle = name_map.get('throttle')
        self._idx_brake = name_map.get('brake')
        self._idx_clutch = name_map.get('clutch')
        self._idx_gaze_x = name_map.get('gaze x')
        self._idx_gaze_y = name_map.get('gaze y')
        self._idx_gaze_unfilt_x = name_map.get('gaze unfiltered x')
        self._idx_gaze_unfilt_y = name_map.get('gaze unfiltered y')
        self._idx_left_gaze_x = name_map.get('left gaze x')
        self._idx_left_gaze_y = name_map.get('left gaze y')
        self._idx_right_gaze_x = name_map.get('right gaze x')
        self._idx_right_gaze_y = name_map.get('right gaze y')

    def _opt(self, idx: Optional[int]) -> Optional[float]:
        if self._tracked is None or idx is None or idx >= len(self._tracked):
            return None
        val = self._tracked[idx]
        return val if math.isfinite(val) else None

    def _extract_sample(self, is_live: bool) -> Optional[DecodedSample]:
        if self._tracked is None or self._idx_time is None:
            return None
        t_val = self._tracked[self._idx_time]
        if not math.isfinite(t_val):
            return None

        return DecodedSample(
            time_s=t_val,
            x=self._opt(self._idx_x),
            z=self._opt(self._idx_z),
            lat=self._opt(self._idx_lat),
            lon=self._opt(self._idx_lon),
            speed_ms=self._opt(self._idx_speed),
            is_live=is_live,
            throttle_pct=self._opt(self._idx_throttle),
            brake_pct=self._opt(self._idx_brake),
            clutch_pct=self._opt(self._idx_clutch),
        )

    def _extract_gaze_sample(self, is_live: bool) -> Optional[DecodedGazeSample]:
        if self._tracked is None or self._idx_time is None:
            return None
        t_val = self._tracked[self._idx_time]
        if not math.isfinite(t_val):
            return None
        gx = self._opt(self._idx_gaze_x)
        gy = self._opt(self._idx_gaze_y)
        if gx is None or gy is None:
            gx = self._opt(self._idx_gaze_unfilt_x)
            gy = self._opt(self._idx_gaze_unfilt_y)
        if gx is None or gy is None:
            lx = self._opt(self._idx_left_gaze_x)
            ly = self._opt(self._idx_left_gaze_y)
            rx = self._opt(self._idx_right_gaze_x)
            ry = self._opt(self._idx_right_gaze_y)
            if (
                lx is not None
                and ly is not None
                and rx is not None
                and ry is not None
            ):
                gx = 0.5 * (lx + rx)
                gy = 0.5 * (ly + ry)
            elif lx is not None and ly is not None:
                gx, gy = lx, ly
            elif rx is not None and ry is not None:
                gx, gy = rx, ry
        valid = gx is not None and gy is not None
        return DecodedGazeSample(
            time_s=t_val,
            gaze_x=gx if valid else None,
            gaze_y=gy if valid else None,
            gaze_valid=valid,
            is_live=is_live,
        )

    def pop_chunks(self) -> Tuple[Optional[Dict[str, Any]], List[DecodedSample], bool]:
        """Parse all complete chunks currently buffered.

        Returns `(new_metadata, decoded_samples, eos_reached)`.
        """
        new_metadata: Optional[Dict[str, Any]] = None
        samples: List[DecodedSample] = []
        self.last_row_count = 0

        while len(self._buf) >= 3:
            length, chunk_type = struct.unpack_from('<HB', self._buf, 0)
            total_len = 3 + length
            if len(self._buf) < total_len:
                break

            payload = bytes(self._buf[3:total_len])
            del self._buf[:total_len]
            has_backlog = len(self._buf) >= BACKLOG_BUFFER_THRESHOLD_BYTES

            if chunk_type == CHUNK_MAGIC:
                if payload != b'BRT1':
                    raise ValueError(f'Invalid BRT1 magic payload: {payload!r}')
                self.magic_seen = True

            elif chunk_type == CHUNK_METADATA:
                if not self.magic_seen:
                    raise ValueError('BRT1 metadata chunk received before magic chunk.')
                doc = json.loads(payload.decode('utf-8'))
                meta = doc.get('metadata')
                ch_list = doc.get('channels')
                if not isinstance(meta, dict) or not isinstance(ch_list, list) or not ch_list:
                    raise ValueError('Invalid BRT1 metadata JSON structure.')
                self.metadata = meta
                self.stream_name = str(meta.get('stream', '') or '').strip().lower()
                self.channels = ch_list
                self._scales = [float(ch['scale']) for ch in ch_list]
                self._tracked = None
                self._update_channel_indices()
                new_metadata = dict(meta)

            elif chunk_type == CHUNK_BASELINE:
                num_ch = len(self._scales)
                expected = num_ch * 8
                if num_ch == 0 or length != expected:
                    raise ValueError(
                        f'Invalid baseline chunk length {length} (expected {expected}).'
                    )
                self._tracked = list(struct.unpack(f'<{num_ch}d', payload))
                self.last_row_count += 1
                if self.stream_name == 'tobii_gaze':
                    gs = self._extract_gaze_sample(is_live=not has_backlog)
                    if gs is not None:
                        self._gaze_samples.append(gs)
                elif self.stream_name == '':
                    sample = self._extract_sample(is_live=not has_backlog)
                    if sample is not None:
                        samples.append(sample)

            elif chunk_type == CHUNK_DELTA:
                num_ch = len(self._scales)
                row_bytes = num_ch * 2
                if row_bytes == 0 or (length % row_bytes) != 0:
                    raise ValueError(
                        f'Invalid delta chunk length {length} for {num_ch} channels.'
                    )
                if self._tracked is None:
                    continue
                num_rows = length // row_bytes
                deltas = struct.unpack(f'<{num_rows * num_ch}h', payload)
                for r in range(num_rows):
                    base_idx = r * num_ch
                    for c in range(num_ch):
                        q_val = deltas[base_idx + c]
                        if q_val == NAN_SENTINEL:
                            self._tracked[c] = float('nan')
                        elif math.isnan(self._tracked[c]):
                            self._tracked[c] = q_val * self._scales[c]
                        else:
                            self._tracked[c] += q_val * self._scales[c]
                    self.last_row_count += 1
                    is_last_row = (r == num_rows - 1)
                    live_flag = not has_backlog and is_last_row
                    if self.stream_name == 'tobii_gaze':
                        gs = self._extract_gaze_sample(is_live=live_flag)
                        if gs is not None:
                            self._gaze_samples.append(gs)
                    elif self.stream_name == '':
                        sample = self._extract_sample(is_live=live_flag)
                        if sample is not None:
                            samples.append(sample)

            elif chunk_type == CHUNK_EOS:
                self.eos_seen = True

            else:
                raise ValueError(f'Unknown BRT1 chunk type: 0x{chunk_type:02X}')

        return new_metadata, samples, self.eos_seen


class _StreamDecompressor:
    """Incremental HTTP Content-Encoding decoder for identity, br, gzip, and deflate."""

    def __init__(self, content_encoding: str) -> None:
        enc = (content_encoding or '').strip().lower()
        self.encoding = enc
        self._decomp: Any = None
        if enc in ('', 'identity'):
            self.encoding = 'identity'
        elif enc in ('br', 'brotli'):
            self._decomp = brotli.Decompressor()
        elif enc in ('gzip', 'x-gzip'):
            self._decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc in ('deflate', 'zlib'):
            self._decomp = zlib.decompressobj(32 + zlib.MAX_WBITS)
        else:
            raise ValueError(f'Unsupported Content-Encoding: {content_encoding}')

    def process(self, data: bytes) -> bytes:
        """Decompress an incoming slice of HTTP body bytes."""
        if not data or self.encoding == 'identity':
            return data
        if self.encoding in ('br', 'brotli'):
            return bytes(self._decomp.process(data))
        out = self._decomp.decompress(data)
        while self._decomp.unused_data:
            unused = self._decomp.unused_data
            wbits = (16 if 'gzip' in self.encoding else 32) + zlib.MAX_WBITS
            self._decomp = zlib.decompressobj(wbits)
            out += self._decomp.decompress(unused)
        return out

    def flush(self) -> bytes:
        """Flush any trailing decompressed bytes on EOF."""
        if self._decomp is None or self.encoding in ('br', 'brotli'):
            return b''
        return bytes(self._decomp.flush())


def _read_exact(rfile: Any, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = rfile.read(size - len(buf))
        if not chunk:
            raise EOFError('Unexpected EOF while reading HTTP body.')
        buf.extend(chunk)
    return bytes(buf)


def _iter_http_body_chunks(rfile: Any, headers: Any) -> Any:
    transfer_encoding = str(headers.get('Transfer-Encoding', '')).lower()
    if 'chunked' in transfer_encoding:
        while True:
            line = rfile.readline(65536)
            if not line:
                break
            size_part = line.split(b';', 1)[0].strip()
            if not size_part:
                continue
            chunk_size = int(size_part, 16)
            if chunk_size == 0:
                while True:
                    trailer = rfile.readline(65536)
                    if not trailer or trailer in (b'\r\n', b'\n'):
                        break
                break
            data = _read_exact(rfile, chunk_size)
            crlf = rfile.readline(4)
            if crlf not in (b'\r\n', b'\n', b''):
                raise ValueError('Malformed HTTP chunk terminator.')
            yield data
        return

    content_length_raw = headers.get('Content-Length')
    if content_length_raw is not None:
        remaining = max(0, int(content_length_raw))
        while remaining > 0:
            to_read = min(4096, remaining)
            chunk = rfile.read(to_read)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
        return

    while True:
        read_fn = getattr(rfile, 'read1', rfile.read)
        chunk = read_fn(4096)
        if not chunk:
            break
        yield chunk


class ChunkedBrt1Handler(BaseHTTPRequestHandler):
    """HTTP handler for `POST /api/upload/stream`, `GET /overlay`, `GET /events`, and `/health`."""

    protocol_version = 'HTTP/1.1'

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence default BaseHTTPRequestHandler stderr logging."""
        return

    def _enable_tcp_nodelay(self) -> None:
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode('utf-8')
        try:
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _send_html(self, html_text: str) -> None:
        body = html_text.encode('utf-8')
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _serve_sse(self) -> None:
        srv: LiveStreamServer = self.server  # type: ignore[assignment]
        hub = srv.overlay_hub
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Connection', 'close')
        self.end_headers()

        if hub is None:
            init_line = b'data: {"type":"init"}\n\n'
            try:
                self.wfile.write(init_line)
                self.wfile.flush()
            except OSError:
                return
            while not srv.stop_event.wait(0.25):
                pass
            return

        client_q, init_payload = hub.register_client()
        try:
            self.wfile.write(f'data: {init_payload}\n\n'.encode('utf-8'))
            self.wfile.flush()
            while not srv.stop_event.is_set():
                try:
                    msg = client_q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if msg is None:
                    break
                self.wfile.write(f'data: {msg}\n\n'.encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            hub.unregister_client(client_q)

    def do_GET(self) -> None:
        """Serve `/health`, `/overlay`, and `/events`."""
        self._enable_tcp_nodelay()
        path = urlsplit(self.path).path
        if path == '/health':
            payload: Dict[str, Any] = {'status': 'ok'}
            provider = getattr(self.server, 'status_provider', None)
            if callable(provider):
                try:
                    extra = provider()
                    if isinstance(extra, dict):
                        payload.update(extra)
                except Exception:
                    pass
            self._send_json(200, payload)
            return
        if path in ('/overlay', '/overlay/'):
            self._send_html(OVERLAY_HTML)
            return
        if path == '/events':
            self._serve_sse()
            return
        self._send_json(404, {'error': 'Not found'})

    def do_POST(self) -> None:
        """Consume streaming BRT1 telemetry from `/api/upload/stream`."""
        self._enable_tcp_nodelay()
        path = urlsplit(self.path).path
        srv: LiveStreamServer = self.server  # type: ignore[assignment]
        if path == '/api/shutdown':
            peer_ip = str(self.client_address[0] if self.client_address else '')
            if peer_ip not in ('127.0.0.1', '::1'):
                self._send_json(403, {'error': 'Loopback only'})
                return
            self._send_json(200, {'status': 'shutting_down'})
            cb = getattr(self.server, 'on_shutdown_request', None)
            stop_fn = getattr(self.server, 'stop_server', None)
            target = cb if callable(cb) else stop_fn
            if callable(target):
                threading.Thread(target=target, daemon=True).start()
            return
        if path != '/api/upload/stream':
            self._send_json(404, {'error': 'Not found'})
            return

        api_key = str(self.headers.get('X-API-Key', '')).strip()
        content_encoding = str(self.headers.get('Content-Encoding', ''))

        try:
            decomp = _StreamDecompressor(content_encoding)
        except ValueError as exc:
            self._send_json(400, {'error': str(exc)})
            return

        decoder = Brt1IncrementalDecoder()
        session_key: Optional[str] = None
        seen_samples = 0
        eos_seen = False

        srv.on_stream_open()
        try:
            for raw_chunk in _iter_http_body_chunks(self.rfile, self.headers):
                if srv.stop_event.is_set():
                    break
                if eos_seen:
                    continue
                plain = decomp.process(raw_chunk)
                if not plain:
                    continue
                decoder.feed(plain)
                new_meta, samples, eos = decoder.pop_chunks()
                gaze_samples = decoder.pop_gaze_samples()
                if new_meta is not None:
                    session_key = compute_session_key(new_meta)
                    if decoder.stream_name == '':
                        srv.dispatch_header(session_key, new_meta, decoder.channels, api_key)
                if session_key is not None:
                    if samples:
                        srv.dispatch_samples(session_key, samples)
                    if gaze_samples:
                        srv.dispatch_gaze_samples(session_key, gaze_samples)
                seen_samples += decoder.last_row_count
                if eos:
                    eos_seen = True

            if not srv.stop_event.is_set() and not eos_seen:
                tail = decomp.flush()
                if tail:
                    decoder.feed(tail)
                    new_meta, samples, _ = decoder.pop_chunks()
                    gaze_samples = decoder.pop_gaze_samples()
                    if new_meta is not None:
                        session_key = compute_session_key(new_meta)
                        if decoder.stream_name == '':
                            srv.dispatch_header(session_key, new_meta, decoder.channels, api_key)
                    if session_key is not None:
                        if samples:
                            srv.dispatch_samples(session_key, samples)
                        if gaze_samples:
                            srv.dispatch_gaze_samples(session_key, gaze_samples)
                    seen_samples += decoder.last_row_count
        except (BrokenPipeError, ConnectionResetError):
            return
        except EOFError:
            self._send_json(400, {'error': 'Incomplete stream.'})
            return
        except Exception as exc:
            self._send_json(400, {'error': str(exc)})
            return
        finally:
            srv.on_stream_close()

        self._send_json(200, {'status': 'ok', 'samples': seen_samples})


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


class LiveStreamServer:
    """Thread-safe local HTTP server forwarding BRT1 streams and serving OBS overlay."""

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 18922,
        on_header: Optional[
            Callable[[str, Dict[str, Any], List[Dict[str, Any]], str], None]
        ] = None,
        on_samples: Optional[Callable[[str, List[DecodedSample]], None]] = None,
        on_gaze_samples: Optional[Callable[[str, List[DecodedGazeSample]], None]] = None,
        overlay_hub: Optional[OverlayStateHub] = None,
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        on_shutdown_request: Optional[Callable[[], None]] = None,
    ) -> None:
        self.host = host
        self.requested_port = int(port)
        self._on_header = on_header
        self._on_samples = on_samples
        self._on_gaze_samples = on_gaze_samples
        self.overlay_hub = overlay_hub
        self._status_provider = status_provider
        self._on_shutdown_request = on_shutdown_request

        self.stop_event = threading.Event()
        self._callback_lock = threading.Lock()
        self._conn_lock = threading.Lock()
        self._active_connections = 0
        self._httpd: Optional[_ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def bound_port(self) -> int:
        """Return the TCP port bound by the server."""
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self.requested_port

    @property
    def active_connections(self) -> int:
        """Return the number of live incoming telemetry stream connections."""
        with self._conn_lock:
            return self._active_connections

    def on_stream_open(self) -> None:
        """Record that a streaming HTTP POST connection has opened."""
        with self._conn_lock:
            self._active_connections += 1

    def on_stream_close(self) -> None:
        """Record that a streaming HTTP POST connection has closed."""
        with self._conn_lock:
            self._active_connections = max(0, self._active_connections - 1)

    def dispatch_header(
        self,
        session_key: str,
        metadata: Dict[str, Any],
        channels: List[Dict[str, Any]],
        api_key: str,
    ) -> None:
        """Invoke `on_header` serialized across concurrent connections."""
        if self._on_header is None:
            return
        with self._callback_lock:
            self._on_header(session_key, metadata, channels, api_key)

    def dispatch_samples(
        self,
        session_key: str,
        samples: List[DecodedSample],
    ) -> None:
        """Invoke `on_samples` serialized across concurrent connections."""
        if self._on_samples is None or not samples:
            return
        with self._callback_lock:
            self._on_samples(session_key, samples)

    def dispatch_gaze_samples(
        self,
        session_key: str,
        samples: List[DecodedGazeSample],
    ) -> None:
        """Invoke `on_gaze_samples` serialized across concurrent connections."""
        if self._on_gaze_samples is None or not samples:
            return
        with self._callback_lock:
            self._on_gaze_samples(session_key, samples)

    def start(self) -> None:
        """Start listening on `host:port` in a background daemon thread."""
        if self._httpd is not None:
            return
        self.stop_event.clear()
        httpd = _ThreadingHTTPServer((self.host, self.requested_port), ChunkedBrt1Handler)
        httpd.stop_event = self.stop_event  # type: ignore[attr-defined]
        httpd.overlay_hub = self.overlay_hub  # type: ignore[attr-defined]
        httpd.dispatch_header = self.dispatch_header  # type: ignore[attr-defined]
        httpd.dispatch_samples = self.dispatch_samples  # type: ignore[attr-defined]
        httpd.dispatch_gaze_samples = self.dispatch_gaze_samples  # type: ignore[attr-defined]
        httpd.on_stream_open = self.on_stream_open  # type: ignore[attr-defined]
        httpd.on_stream_close = self.on_stream_close  # type: ignore[attr-defined]
        httpd.status_provider = self._status_provider  # type: ignore[attr-defined]
        httpd.on_shutdown_request = self._on_shutdown_request  # type: ignore[attr-defined]
        httpd.stop_server = self.stop  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name='LiveStreamServer',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the HTTP server and close SSE clients cleanly."""
        self.stop_event.set()
        if self.overlay_hub is not None:
            self.overlay_hub.close_all_clients()
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
