"""
Minimal synchronous WebSocket JSON-RPC client for Moonraker.

Why hand-rolled: the printer Python has no `websocket` / `websockets` module,
the apps repo's Python-dep bundling pipeline (Docker + venv + lib/) is heavy
overkill for one localhost connection, and our use case is narrow enough to
fit in one small stdlib-only module:
  - localhost only, no TLS, no proxy
  - small text frames only (JSON-RPC messages)
  - synchronous: send request, wait for matching response, repeat
  - lifetime: opened for one bias measurement, closed after

Anything beyond that (TLS, fragmentation, large binary frames, reconnect) is
intentionally out of scope.
"""

import base64
import hashlib
import json
import os
import select
import socket
import struct
from collections.abc import Callable
from typing import Any

WS_GUID: str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Frame opcodes (RFC 6455 section 5.2)
OP_CONT: int = 0x0
OP_TEXT: int = 0x1
OP_BINARY: int = 0x2
OP_CLOSE: int = 0x8
OP_PING: int = 0x9
OP_PONG: int = 0xA


class WebSocketError(Exception):
    pass


class JsonRpcWebSocket:
    """
    A minimal JSON-RPC-over-WebSocket client.

    Usage (context manager, recommended):
        with JsonRpcWebSocket() as ws:
            info = ws.call("printer.info")
            ws.subscribe("notify_gcode_response", lambda params: print(params))
            ws.pump(timeout=5.0)

    Or manual lifecycle:
        ws = JsonRpcWebSocket()
        ws.connect()
        ws.call("printer.info")
        ws.close()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7125,
        path: str = "/websocket",
    ) -> None:
        self.host: str = host
        self.port: int = port
        self.path: str = path
        self._sock: socket.socket | None = None
        self._next_id: int = 1
        self._handlers: dict[str, Callable[[Any], None]] = {}
        # Buffer for partial recvs across calls
        self._recv_buf: bytes = b""

    # ----- connection lifecycle -----

    def connect(self, timeout: float = 5.0) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        # Switch back to blocking mode; we manage timeouts with select()
        sock.settimeout(None)
        self._sock = sock
        self._handshake()

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send_frame(OP_CLOSE, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None

    def __enter__(self) -> "JsonRpcWebSocket":
        """Connect on entry so `with JsonRpcWebSocket() as ws:` just works."""
        if self._sock is None:
            self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----- JSON-RPC API -----

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        """Send a JSON-RPC request and block until its response arrives."""
        req_id: int = self._next_id
        self._next_id += 1
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": req_id,
        }
        if params is not None:
            msg["params"] = params
        self._send_text(json.dumps(msg))

        # Dispatch incoming frames until we get our response. Notifications
        # received in the meantime are routed to subscribed handlers.
        while True:
            payload: Any = self._recv_json(timeout=timeout)
            if isinstance(payload, dict) and payload.get("id") == req_id:
                if "error" in payload:
                    raise WebSocketError(f"RPC error: {payload['error']}")
                return payload.get("result")
            self._dispatch(payload)

    def subscribe(self, notification: str, handler: Callable[[Any], None]) -> None:
        """Register a handler for Moonraker `notify_*` messages."""
        self._handlers[notification] = handler

    def pump(self, timeout: float = 1.0) -> bool:
        """
        Read at most one incoming frame within `timeout` seconds and dispatch
        it to handlers. Returns True if a frame was processed, False on
        timeout. Useful when you want to wait for notifications without
        making an RPC call.
        """
        try:
            payload: Any = self._recv_json(timeout=timeout)
        except TimeoutError:
            return False
        self._dispatch(payload)
        return True

    # ----- internals: handshake -----

    def _handshake(self) -> None:
        key_bytes: bytes = os.urandom(16)
        key_b64: str = base64.b64encode(key_bytes).decode("ascii")
        request: str = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key_b64}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        assert self._sock is not None
        self._sock.sendall(request.encode("ascii"))

        header_buf: bytes = b""
        while b"\r\n\r\n" not in header_buf:
            chunk: bytes = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            header_buf += chunk
            if len(header_buf) > 65536:
                raise WebSocketError("handshake response too large")

        header_end: int = header_buf.index(b"\r\n\r\n") + 4
        # Any bytes past the header are the first frame data; save them.
        self._recv_buf = header_buf[header_end:]
        header_text: str = header_buf[:header_end].decode("iso-8859-1")

        status_line: str = header_text.split("\r\n", 1)[0]
        if "101" not in status_line:
            raise WebSocketError(f"unexpected handshake status: {status_line!r}")

        expected: str = base64.b64encode(
            hashlib.sha1((key_b64 + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        accept: str | None = None
        for line in header_text.split("\r\n")[1:]:
            if ":" in line:
                name, _, value = line.partition(":")
                if name.strip().lower() == "sec-websocket-accept":
                    accept = value.strip()
                    break
        if accept != expected:
            raise WebSocketError(
                f"bad Sec-WebSocket-Accept: got {accept!r}, expected {expected!r}"
            )

    # ----- internals: framing -----

    def _send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        assert self._sock is not None
        header: bytes = struct.pack("!B", 0x80 | opcode)  # FIN + opcode
        length: int = len(payload)
        # Client frames MUST be masked (RFC 6455 5.3)
        mask_bit: int = 0x80
        if length < 126:
            header += struct.pack("!B", mask_bit | length)
        elif length < (1 << 16):
            header += struct.pack("!BH", mask_bit | 126, length)
        else:
            header += struct.pack("!BQ", mask_bit | 127, length)
        mask_key: bytes = os.urandom(4)
        header += mask_key
        masked: bytes = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + masked)

    def _recv_exact(self, n: int, timeout: float | None) -> bytes:
        assert self._sock is not None
        while len(self._recv_buf) < n:
            if timeout is not None:
                ready, _, _ = select.select([self._sock], [], [], timeout)
                if not ready:
                    raise TimeoutError(f"timed out waiting for {n} bytes")
            chunk: bytes = self._sock.recv(max(4096, n - len(self._recv_buf)))
            if not chunk:
                raise WebSocketError("connection closed")
            self._recv_buf += chunk
        out: bytes = self._recv_buf[:n]
        self._recv_buf = self._recv_buf[n:]
        return out

    def _recv_frame(self, timeout: float | None) -> tuple[int, bytes]:
        """Return (opcode, payload). Reassembles continuation fragments."""
        opcode_out: int | None = None
        chunks: list[bytes] = []
        while True:
            hdr: bytes = self._recv_exact(2, timeout)
            b0, b1 = hdr[0], hdr[1]
            fin: bool = bool(b0 & 0x80)
            opcode: int = b0 & 0x0F
            masked: bool = bool(b1 & 0x80)
            length: int = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2, timeout))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8, timeout))[0]
            mask_key: bytes = self._recv_exact(4, timeout) if masked else b""
            payload: bytes = self._recv_exact(length, timeout) if length else b""
            if masked:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

            # Control frames: handle inline, never fragment
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                return (OP_CLOSE, payload)

            if opcode_out is None:
                opcode_out = opcode
            chunks.append(payload)
            if fin:
                return (opcode_out, b"".join(chunks))

    def _recv_json(self, timeout: float | None) -> Any:
        opcode, payload = self._recv_frame(timeout)
        if opcode == OP_CLOSE:
            raise WebSocketError("server closed connection")
        if opcode != OP_TEXT:
            raise WebSocketError(f"unexpected opcode: {opcode}")
        return json.loads(payload.decode("utf-8"))

    # ----- internals: dispatch -----

    def _dispatch(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        method: str | None = payload.get("method")
        if method and "id" not in payload:
            handler = self._handlers.get(method)
            if handler is not None:
                handler(payload.get("params"))
