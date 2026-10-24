"""Браузер для тестов веб-клиента: WebSocket-клиент на stdlib и headless Chromium под управлением DevTools.

WsClient — «браузер без браузера»: им тесты подключаются к /ws сервера партии, а Browser — к самому
Chromium (протокол DevTools тоже идёт по WebSocket). Browser открывает страницу, задаёт размер экрана
(телефон вертикально/горизонтально, ПК), выполняет JS и снимает экран. Нет Chromium — Browser.find() None.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request


class WsClient:
    """Браузер без браузера: WebSocket-клиент на stdlib с тем же send/recv, что у protocol.Connection.
    fragment — делить каждое сообщение на кадры по столько байт (проверка склейки фрагментов)."""

    def __init__(self, port: int, fragment: int | None = None, path: str = "/ws", host: str = "127.0.0.1"):
        self.sock = socket.create_connection((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buffer = b""
        head = self._until(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 101"), head
        expected = base64.b64encode(hashlib.sha1(key.encode() + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        assert b"Sec-WebSocket-Accept: " + expected in head, head   # так проверяет и браузер
        self.fragment = fragment
        self.pings = 0

    def _until(self, marker: bytes) -> bytes:
        while marker not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send_frame(self, opcode: int, payload: bytes, fin: bool = True) -> None:
        mask = os.urandom(4)
        n = len(payload)
        b0 = (0x80 if fin else 0) | opcode
        if n < 126:
            head = struct.pack("!BB", b0, 0x80 | n)
        elif n < 1 << 16:
            head = struct.pack("!BBH", b0, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", b0, 0x80 | 127, n)
        self.sock.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def send(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False).encode()
        if not self.fragment:
            self.send_frame(0x1, data)
            return
        parts = [data[i:i + self.fragment] for i in range(0, len(data), self.fragment)]
        for i, part in enumerate(parts):
            self.send_frame(0x1 if i == 0 else 0x0, part, fin=i == len(parts) - 1)

    def recv(self) -> dict | None:
        try:
            while True:
                b0, b1 = self._read(2)
                n = b1 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._read(8))[0]
                assert not b1 & 0x80, "server frames are not masked"
                payload = self._read(n)
                opcode = b0 & 0x0F
                if opcode == 0x8:
                    return None
                if opcode == 0x9:
                    self.pings += 1
                    self.send_frame(0xA, payload)
                    continue
                assert b0 & 0x80 and opcode == 0x1
                return json.loads(payload)
        except (ConnectionError, OSError):
            return None

    def close(self) -> None:
        try:
            self.send_frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class Browser:
    """Headless Chromium с одной вкладкой. errors — исключения JS и console.error страницы."""

    def __init__(self, exe: str):
        self.profile = tempfile.mkdtemp(prefix="heroes-chromium-")
        self.proc = subprocess.Popen(
            [exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", "--mute-audio", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        found = threading.Event()
        self.port = None

        def drain():   # stderr читаем до конца: иначе Chromium встанет, заполнив канал
            for line in self.proc.stderr:
                m = re.search(r"DevTools listening on ws://[^:]+:(\d+)/", line)
                if m and not found.is_set():
                    self.port = int(m[1])
                    found.set()

        threading.Thread(target=drain, daemon=True).start()
        if not found.wait(20):
            self.close()
            raise RuntimeError("Chromium did not start")
        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list"))
        page = next(t for t in targets if t["type"] == "page")
        self.ws = WsClient(self.port, path=re.sub(r"^ws://[^/]+", "", page["webSocketDebuggerUrl"]))
        self.ids = 0
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.call("Page.enable")
        self.call("Runtime.enable")

    @staticmethod
    def find() -> str | None:
        return next((exe for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
                     if (exe := shutil.which(name))), None)

    def _event(self, message: dict) -> None:
        self.events.append(message)
        method, params = message.get("method"), message.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params["exceptionDetails"]
            self.errors.append(details.get("exception", {}).get("description") or details.get("text", "exception"))
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in params["args"]))

    def call(self, method: str, **params) -> dict:
        self.ids += 1
        self.ws.send({"id": self.ids, "method": method, "params": params})
        while True:
            message = self.ws.recv()
            if message is None:
                raise ConnectionError("Chromium closed the connection")
            if message.get("id") == self.ids:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._event(message)

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description", "JS error"))
        return result["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 20.0, step: float = 0.1):
        """Ждать, пока выражение JS не станет истинным; вернуть его значение."""
        end = time.monotonic() + timeout
        while True:
            value = self.evaluate(expression)
            if value:
                return value
            if time.monotonic() > end:
                raise TimeoutError(expression)
            time.sleep(step)

    def screen(self, width: int, height: int, mobile: bool = True, scale: float = 2) -> None:
        """Размер экрана: телефон (mobile — касания, без полос прокрутки) или окно ПК."""
        landscape = width > height
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=mobile,
                  screenOrientation={"type": "landscapePrimary" if landscape else "portraitPrimary",
                                     "angle": 90 if landscape else 0})
        self.call("Emulation.setTouchEmulationEnabled", enabled=mobile, maxTouchPoints=5 if mobile else 1)

    def open(self, url: str) -> None:
        self.events.clear()
        self.call("Page.navigate", url=url)
        end = time.monotonic() + 20
        while not any(e.get("method") == "Page.loadEventFired" for e in self.events):
            if time.monotonic() > end:
                raise TimeoutError(url)
            self.evaluate("1")   # заодно собирает события

    def screenshot(self, path: str) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
