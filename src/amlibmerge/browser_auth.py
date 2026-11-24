"""Interactive Chromium/Edge browser login via Chrome DevTools Protocol (CDP)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from amlibmerge.client import AppleMusicAuthError, AppleMusicClient, fetch_web_developer_token
from amlibmerge.config import get_config_dir
from amlibmerge.i18n import t

APPLE_MUSIC_LOGIN_URL = "https://music.apple.com"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class BrowserAuthError(RuntimeError):
    """Raised when automated browser login cannot start or fails to obtain a token."""


def _candidate_browser_paths() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app = os.environ.get("LocalAppData", "")
        raw = [
            Path(pf86) / "Microsoft/Edge/Application/msedge.exe",
            Path(pf) / "Microsoft/Edge/Application/msedge.exe",
            Path(pf) / "Google/Chrome/Application/chrome.exe",
            Path(pf86) / "Google/Chrome/Application/chrome.exe",
            Path(local_app) / "Google/Chrome/Application/chrome.exe" if local_app else None,
            Path(pf) / "BraveSoftware/Brave-Browser/Application/brave.exe",
            Path(local_app) / "BraveSoftware/Brave-Browser/Application/brave.exe" if local_app else None,
        ]
        candidates.extend(p for p in raw if p is not None)
        for name in ("msedge", "chrome", "brave"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    elif sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    else:
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "microsoft-edge",
            "chromium",
            "chromium-browser",
            "brave-browser",
        ):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return candidates


def find_chromium_browser(explicit_path: str | None = None) -> Path:
    """Locate an installed Chromium-based browser (Edge, Chrome, Brave, Chromium)."""
    override = explicit_path or os.environ.get("AMLIBMERGE_BROWSER", "").strip()
    if override:
        p = Path(override).expanduser()
        if p.exists():
            return p
        which_p = shutil.which(override)
        if which_p:
            return Path(which_p)
        raise BrowserAuthError(t("browser.err_explicit_missing", path=override))

    for candidate in _candidate_browser_paths():
        if candidate.exists():
            return candidate

    raise BrowserAuthError(t("browser.err_none_found"))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _CDPWebSocket:
    """Minimal RFC 6455 WebSocket client for localhost Chrome DevTools Protocol."""

    def __init__(self, ws_url: str, timeout: float = 10.0) -> None:
        parsed = urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        self._next_id = 1
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(req.encode("ascii"))

        while b"\r\n\r\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise BrowserAuthError(t("browser.err_ws_closed"))
            self._buf.extend(chunk)

        sep = self._buf.index(b"\r\n\r\n")
        header_block = bytes(self._buf[:sep]).decode("latin-1")
        del self._buf[: sep + 4]

        status_line = header_block.splitlines()[0]
        if "101" not in status_line:
            raise BrowserAuthError(t("browser.err_ws_handshake", status=status_line))

        expected_accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if expected_accept not in header_block:
            raise BrowserAuthError(t("browser.err_ws_accept"))

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("CDP WebSocket connection closed")
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask_key = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask_key)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_message(self) -> str | None:
        fragments: list[bytes] = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask_key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length) if length else b""
            if mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

            if opcode == 0x8:  # Close
                return None
            if opcode == 0x9:  # Ping -> Pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # Pong
                continue

            fragments.append(payload)
            if fin:
                return b"".join(fragments).decode("utf-8")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"id": req_id, "method": method}
        if params:
            message["params"] = params
        self._send_frame(0x1, json.dumps(message).encode("utf-8"))

        while True:
            raw = self._recv_message()
            if raw is None:
                raise ConnectionError("CDP WebSocket closed before response")
            data = json.loads(raw)
            if data.get("id") == req_id:
                if "error" in data:
                    raise BrowserAuthError(t("browser.err_cdp_call", method=method, err=data["error"]))
                return data.get("result") or {}

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def extract_media_user_token_from_cookies(cookies: list[dict[str, Any]]) -> list[str]:
    """Extract candidate `media-user-token` cookie values for music.apple.com."""
    tokens: list[str] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        domain = str(cookie.get("domain") or "").lower()
        value = str(cookie.get("value") or "").strip()
        if name == "media-user-token" and "music.apple.com" in domain and value:
            if value not in tokens:
                tokens.append(value)
    return tokens


def _wait_for_cdp_ws_url(port: int, proc: subprocess.Popen[Any], timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    with httpx.Client(timeout=2.0, trust_env=False) as http:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise BrowserAuthError(t("browser.err_early_exit"))
            try:
                resp = http.get(url)
                if resp.status_code == 200:
                    ws_url = (resp.json() or {}).get("webSocketDebuggerUrl")
                    if ws_url:
                        return str(ws_url)
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise BrowserAuthError(t("browser.err_cdp_timeout"))


def capture_token_via_browser(
    *,
    browser_path: str | None = None,
    dev_token: str | None = None,
    fresh_profile: bool = False,
    timeout: float = 300.0,
    poll_interval: float = 1.0,
    on_status: Callable[[str], None] | None = None,
) -> tuple[str, str, str, int | None]:
    """Open Apple Music in a Chromium browser window and auto-capture `media-user-token` after login.

    Returns `(media_user_token, storefront, developer_token, developer_token_exp)`.
    """

    def notify(msg: str) -> None:
        if on_status:
            on_status(msg)

    executable = find_chromium_browser(browser_path)

    if not dev_token:
        notify(t("browser.status_jwt"))
        dev_token, dev_exp = fetch_web_developer_token()
    else:
        dev_exp = None

    port = _find_free_port()
    temp_dir: Path | None = None
    if fresh_profile:
        temp_dir = Path(tempfile.mkdtemp(prefix="amlibmerge_auth_"))
        profile_dir = temp_dir
    else:
        profile_dir = get_config_dir() / "browser_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1100,820",
        APPLE_MUSIC_LOGIN_URL,
    ]

    notify(t("browser.status_launch", name=executable.name))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ws: _CDPWebSocket | None = None
    rejected_tokens: set[str] = set()

    try:
        ws_url = _wait_for_cdp_ws_url(port, proc, timeout=20.0)
        ws = _CDPWebSocket(ws_url, timeout=10.0)
        notify(t("browser.status_wait_login"))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise BrowserAuthError(t("browser.err_window_closed"))

            try:
                result = ws.call("Storage.getCookies")
            except (ConnectionError, OSError):
                if proc.poll() is not None:
                    raise BrowserAuthError(t("browser.err_window_closed"))
                time.sleep(poll_interval)
                continue

            cookies = result.get("cookies") or []
            candidates = [
                tok
                for tok in extract_media_user_token_from_cookies(cookies)
                if tok not in rejected_tokens
            ]

            for candidate in candidates:
                notify(t("browser.status_verifying"))
                try:
                    with AppleMusicClient(
                        media_user_token=candidate,
                        developer_token=dev_token,
                        min_delay=0.0,
                        max_delay=0.0,
                    ) as client:
                        storefront = client.get_storefront()
                        actual_dev = client.developer_token or dev_token
                        actual_exp = client.developer_token_exp or dev_exp
                    notify(t("browser.status_verified", sf=storefront))
                    try:
                        ws.call("Browser.close")
                    except Exception:
                        pass
                    return candidate, storefront, actual_dev, actual_exp
                except AppleMusicAuthError:
                    # Stale/expired cookie from an old profile session; ignore and keep waiting.
                    rejected_tokens.add(candidate)
                    notify(t("browser.status_stale_cookie"))

            time.sleep(poll_interval)

        raise BrowserAuthError(t("browser.err_timeout", sec=int(timeout)))
    finally:
        if ws is not None:
            ws.close()
        if proc.poll() is None:
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
