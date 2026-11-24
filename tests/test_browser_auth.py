"""Unit tests for CDP WebSocket client and browser auth cookie extraction."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from pathlib import Path

from amlibmerge.browser_auth import (
    _CDPWebSocket,
    _WS_GUID,
    BrowserAuthError,
    extract_media_user_token_from_cookies,
    find_chromium_browser,
)
import pytest


def test_extract_media_user_token_from_cookies() -> None:
    cookies = [
        {"name": "other", "domain": ".music.apple.com", "value": "abc"},
        {"name": "media-user-token", "domain": "example.com", "value": "wrong_domain"},
        {"name": "media-user-token", "domain": ".music.apple.com", "value": "0.valid_tok_1"},
        {"name": "media-user-token", "domain": "music.apple.com", "value": "0.valid_tok_1"},
        {"name": "media-user-token", "domain": "music.apple.com", "value": "  "},
    ]
    assert extract_media_user_token_from_cookies(cookies) == ["0.valid_tok_1"]


def test_find_chromium_browser_explicit_invalid_raises() -> None:
    with pytest.raises(BrowserAuthError):
        find_chromium_browser("C:/nonexistent/fake_browser_12345.exe")


def _encode_server_text_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header.extend(struct.pack("!H", len(payload)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(payload)))
    return bytes(header) + payload


def _read_masked_client_frame(conn: socket.socket) -> str:
    head = conn.recv(2)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", conn.recv(2))[0]
    mask = conn.recv(4)
    data = b""
    while len(data) < length:
        data += conn.recv(length - len(data))
    return bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode("utf-8")


def test_cdp_websocket_handshake_and_rpc_roundtrip() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    def serve() -> None:
        conn, _ = server.accept()
        with conn:
            req = conn.recv(4096).decode("latin-1")
            key = ""
            for line in req.splitlines():
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode("ascii")
            )
            msg = json.loads(_read_masked_client_frame(conn))
            # Send an unsolicited event first, then the matching RPC response
            conn.sendall(_encode_server_text_frame(json.dumps({"method": "Target.targetCreated"})))
            reply = {
                "id": msg["id"],
                "result": {
                    "cookies": [
                        {
                            "name": "media-user-token",
                            "domain": ".music.apple.com",
                            "value": "0.test_cookie_token",
                        }
                    ]
                },
            }
            conn.sendall(_encode_server_text_frame(json.dumps(reply)))

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        ws = _CDPWebSocket(f"ws://127.0.0.1:{port}/devtools/browser/test", timeout=5.0)
        try:
            res = ws.call("Storage.getCookies")
            assert extract_media_user_token_from_cookies(res["cookies"]) == ["0.test_cookie_token"]
        finally:
            ws.close()
    finally:
        t.join(timeout=3.0)
        server.close()
