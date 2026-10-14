"""Live bridge into the running Steam client.

Steam's UI is a Chromium (CEF) app. When the client is started with
`-cef-enable-debugging` it exposes the Chrome DevTools Protocol on 127.0.0.1:8080,
and the hidden "SharedJSContext" page holds the `SteamClient` object the UI itself
uses to talk to the native client. Evaluating JS there is how we reach features
Valve compiled into the macOS client but never wired to a button.

Stdlib only: this file carries its own minimal WebSocket client.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

DEFAULT_PORT = 8080


class BridgeError(RuntimeError):
    pass


class _WebSocket:
    def __init__(self, url: str, timeout: float = 15.0):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        # No Origin header on purpose: Chromium only enforces --remote-allow-origins
        # when one is present.
        req = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ) % (u.path or "/", u.hostname, u.port or 80, key)
        self.sock.sendall(req.encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BridgeError("websocket handshake: connection closed")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise BridgeError("websocket handshake refused: %r" % head[:120])

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise BridgeError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str, opcode: int = 1) -> None:
        payload = text.encode()
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def recv(self) -> str:
        message = b""
        while True:
            b0, b1 = self._read(2)
            fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                (n,) = struct.unpack(">H", self._read(2))
            elif n == 127:
                (n,) = struct.unpack(">Q", self._read(8))
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            if opcode == 8:
                raise BridgeError("websocket closed by Steam")
            if opcode == 9:  # ping
                self.send(data.decode("utf-8", "replace"), opcode=10)
                continue
            if opcode == 10:
                continue
            message += data
            if fin:
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def targets(port: int = DEFAULT_PORT, timeout: float = 2.0) -> List[Dict[str, Any]]:
    """List debuggable pages, or raise BridgeError if Steam isn't exposing them."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (OSError, ValueError) as e:
        raise BridgeError("Steam's debug bridge is not reachable on port %d (%s)" % (port, e))


def available(port: int = DEFAULT_PORT) -> bool:
    try:
        return any(t.get("title") == "SharedJSContext" for t in targets(port, timeout=1.0))
    except BridgeError:
        return False


class SteamBridge:
    """Evaluate JavaScript inside Steam's SharedJSContext."""

    def __init__(self, port: int = DEFAULT_PORT):
        page = next((t for t in targets(port) if t.get("title") == "SharedJSContext"), None)
        if page is None:
            raise BridgeError("Steam is up but SharedJSContext isn't ready yet")
        self.ws = _WebSocket(page["webSocketDebuggerUrl"])
        self._id = 0

    def __enter__(self) -> "SteamBridge":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ws.close()

    def call(self, method: str, **params: Any) -> Dict[str, Any]:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise BridgeError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})

    def js(self, expression: str, timeout: Optional[float] = None) -> Any:
        """Run `expression` (may be a promise) and return its JSON-serialisable value."""
        if timeout:
            self.ws.sock.settimeout(timeout)
        res = self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=True,
            returnByValue=True,
        )
        if "exceptionDetails" in res:
            d = res["exceptionDetails"]
            text = (d.get("exception") or {}).get("description") or d.get("text")
            raise BridgeError("JS error in Steam: %s" % text)
        return res.get("result", {}).get("value")

    def console(self, command: str, wait: float = 1.0) -> str:
        """Run a Steam console command and return whatever it printed within `wait` seconds."""
        expr = _CONSOLE_JS % (json.dumps(command), int(wait * 1000))
        text = "".join(self.js(expr, timeout=wait + 15) or [])
        # the console echoes the command back without a newline before its output
        return text[len(command):] if text.startswith(command) else text


_CONSOLE_JS = """
(async (cmd, ms) => {
  const lines = [];
  const h = SteamClient.Console.RegisterForSpewOutput(s => lines.push(s.spew ?? ''));
  SteamClient.Console.ExecCommand(cmd);
  await new Promise(r => setTimeout(r, ms));
  h?.unregister?.();
  return lines;
})(%s, %d)
"""
