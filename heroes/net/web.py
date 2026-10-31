"""Веб-клиент: страница для браузера (телефон, планшет, ПК) на том же порту, что и сервер партии.

Сервер (server.py) по первым байтам соединения узнаёт HTTP-запрос и отдаёт его сюда. Обычные
запросы получают файлы страницы (heroes/web/static) и картинки (heroes/web/assets), запрос
/ws становится WebSocket-соединением. Оно устроено как Connection из protocol.py — те же
send/recv/close и те же сообщения, — поэтому дальше сервер сажает браузер за место.

POST /profiles — профили игроков на компьютере хозяина (heroes/profiles.py). Тело и ответ — JSON;
если у партии есть пароль, он нужен и здесь (поле "password", иначе 403):
  {"action": "list"}                          → {"profiles": [...]} (profiles.public)
  {"action": "create", "name"}                → {"profile": {...}}
  {"action": "rename", "id", "name"}          → {"profile": {...}}
  {"action": "avatar", "id", "avatar": base64 PNG | null} → {"profile": {...}}
  {"action": "delete", "id"}                  → {}
Отказ — 400 и {"error": "<по-русски>"}.

Браузер получает и русские тексты (перевод — heroes/web/ru.py); они добавляются к сообщениям:
  sync      + "log_ru": [...]          — журнал (имена с фракциями)
  event     + "text_ru", "popup"       — строка журнала; табличка над фишкой: [вид, место, подпись] или null
  decision  + "reason_ru"              — причина получения ресурсов (если есть data["reason"])
  error, end, welcome + "text_ru"
Сверх протокола только {"t": "ping"} от страницы: на него сразу отвечает {"t": "pong"} (так страница
проверяет связь, проснувшись). Сервер сам шлёт кадры ping: браузер отвечает на них без участия
страницы, и соединение, молчащее дольше SILENCE секунд, считается оборванным.
"""
from __future__ import annotations

import base64
import hashlib
import json
import queue
import socket
import struct
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
ASSETS = ROOT / "web" / "assets"
# что можно запросить: путь в адресе → каталог на диске
ROUTES = {"static": STATIC, "assets/icons": ASSETS / "icons", "assets/map": ASSETS / "map",
          "assets/avatars": ASSETS / "avatars"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".png": "image/png",
         ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json"}
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC 6455
MAX_HEADERS = 16_384
MAX_MESSAGE = 1_000_000   # hello с аватаркой — до 300 КБ
IDLE = 30        # сколько ждать следующего HTTP-запроса по тому же соединению
PING = 15        # раз в столько секунд сервер шлёт кадр ping
SILENCE = 45     # от браузера столько секунд ничего (даже pong) — связь оборвана, место свободно


METHODS = (b"GET ", b"HEAD", b"POST")


def is_http(sock: socket.socket) -> bool:
    """Соединение начинается с HTTP-метода — это браузер. Байты остаются в сокете (MSG_PEEK)."""
    while True:
        head = sock.recv(4, socket.MSG_PEEK)
        if len(head) == 4 or not head or not any(m.startswith(head) for m in METHODS):
            return head in METHODS
        time.sleep(0.01)   # пришла только часть первых байт


def accept(sock: socket.socket, password: str | None = None) -> WebSocketConnection | None:
    """Ответить на HTTP-запросы этого соединения. Запрос /ws — WebSocket-соединение для сервера партии;
    None — браузер получил файлы и соединение закрыто. password — пароль партии (для профилей)."""
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    buffer = b""
    try:
        while True:
            while b"\r\n\r\n" not in buffer:
                if len(buffer) > MAX_HEADERS:
                    raise ValueError("headers too long")
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError
                buffer += chunk
            head, _, buffer = buffer.partition(b"\r\n\r\n")
            method, target, headers = parse_request(head)
            path = unquote(urlsplit(target).path).strip("/")
            if path == "ws" and headers.get("upgrade", "").lower() == "websocket":
                return upgrade(sock, headers, buffer)
            if method == "GET" and path == "access":   # нужен ли пароль: без него страница сразу предлагает места
                respond(sock, 200, json.dumps({"password": bool(password)}).encode("utf-8"),
                        {"Content-Type": TYPES[".json"], "Cache-Control": "no-store"})
            elif method == "POST" and path == "profiles":
                length = int(headers.get("content-length") or 0)
                if not 0 <= length <= MAX_MESSAGE:
                    raise ValueError("body too long")
                while len(buffer) < length:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise ConnectionError
                    buffer += chunk
                body, buffer = buffer[:length], buffer[length:]
                status, reply = profile_request(body, password)
                respond(sock, status, json.dumps(reply, ensure_ascii=False).encode("utf-8"),
                        {"Content-Type": TYPES[".json"], "Cache-Control": "no-store"})
            elif method not in ("GET", "HEAD"):
                respond(sock, 405, b"Method not allowed")
            else:
                serve(sock, path, headers, head_only=method == "HEAD")
            if headers.get("connection", "").lower() == "close":
                raise ConnectionError
            sock.settimeout(IDLE)
    except (OSError, ValueError, ConnectionError):
        try:
            sock.close()
        except OSError:
            pass
        return None


def parse_request(head: bytes) -> tuple[str, str, dict[str, str]]:
    lines = head.decode("latin-1").split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return method, target, headers


def respond(sock: socket.socket, status: int, body: bytes = b"", headers: dict[str, str] | None = None,
            head_only: bool = False) -> None:
    reason = {200: "OK", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
              405: "Method Not Allowed"}[status]
    lines = [f"HTTP/1.1 {status} {reason}", f"Content-Length: {len(body)}", *(f"{k}: {v}" for k, v in (headers or {}).items())]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + (b"" if head_only or status == 304 else body))


def resolve(path: str) -> Path | None:
    """Файл по пути из адреса или None, если такого нет или он вне разрешённых каталогов."""
    if path in ("", "index.html"):
        return STATIC / "index.html"
    for prefix, base in sorted(ROUTES.items(), key=lambda item: -len(item[0])):
        if path.startswith(prefix + "/"):
            file = (base / path[len(prefix) + 1:]).resolve()
            return file if file.is_relative_to(base.resolve()) and file.is_file() else None
    return None


_profiles_lock = threading.Lock()   # правки профилей из нескольких браузеров — по одной


def profile_request(body: bytes, password: str | None) -> tuple[int, dict]:
    """Запрос страницы «Профили и статистика» (см. описание модуля): (HTTP-статус, ответ)."""
    from .. import profiles
    try:
        request = json.loads(body.decode("utf-8"))
        assert isinstance(request, dict)
    except (ValueError, AssertionError):
        return 400, {"error": "Непонятный запрос."}
    if password and request.get("password") != password:
        return 403, {"error": "Неверный пароль."}
    action = request.get("action")
    with _profiles_lock:
        if action == "list":
            return 200, {"profiles": profiles.public()}
        try:
            if action == "create":
                profile = profiles.create(str(request.get("name", "")))
            else:
                profile = profiles.get(str(request.get("id", "")))
                if profile is None:
                    return 400, {"error": "Такого профиля нет — возможно, его уже удалили."}
                if action == "rename":
                    profiles.rename(profile, str(request.get("name", "")))
                elif action == "avatar":
                    avatar = request.get("avatar")
                    profiles.set_avatar(profile, avatar if isinstance(avatar, str) else None)
                elif action == "delete":
                    profiles.delete(profile)
                    return 200, {}
                else:
                    return 400, {"error": "Непонятный запрос."}
        except ValueError as e:
            return 400, {"error": str(e)}
        except OSError as e:
            return 400, {"error": f"Не удалось сохранить профиль: {e}"}
        return 200, {"profile": next(p for p in profiles.public() if p["id"] == profile.id)}


_ui: bytes | None = None


def ui_json() -> bytes:
    """Справочные данные для страницы (heroes/web/common.py, marks.py): порядок меток значков, русские названия, цвета."""
    global _ui
    if _ui is None:
        from ..web import common, marks
        _ui = json.dumps({
            "marks": marks.NAMES, "mark_base": marks.BASE, "colors": common.PLAYER_COLORS,
            "terrains": common.TERRAIN_NAMES, "resources": common.RESOURCE_NAMES, "resource_colors": common.RESOURCE_COLORS,
            "actions": common.ACTION_NAMES, "objects": common.OBJECT_NAMES, "glyphs": common.OBJECT_GLYPHS,
            "levels": common.LEVELS, "doing": common.DOING,
        }, ensure_ascii=False).encode("utf-8")
    return _ui


def serve(sock: socket.socket, path: str, headers: dict[str, str], head_only: bool = False) -> None:
    file = None
    if path == "ping":   # пустой ответ: им страница будит свой WebSocket в Safari (net.js)
        respond(sock, 200, headers={"Cache-Control": "no-store"})
        return
    if path == "ui.json":
        kind, etag = TYPES[".json"], '"' + hashlib.sha1(ui_json()).hexdigest()[:16] + '"'
    else:
        file = resolve(path)
        if file is None:
            respond(sock, 404, b"Not found", {"Content-Type": "text/plain"})
            return
        stat = file.stat()
        kind, etag = TYPES.get(file.suffix, "application/octet-stream"), f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    # браузер каждый раз сверяется (ETag): после обновления игры у гостя сразу новая страница
    common = {"Content-Type": kind, "ETag": etag, "Cache-Control": "no-cache"}
    if headers.get("if-none-match") == etag:
        respond(sock, 304, headers=common)
    else:
        respond(sock, 200, file.read_bytes() if file else ui_json(), common, head_only)


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + GUID).digest()).decode("ascii")


def upgrade(sock: socket.socket, headers: dict[str, str], rest: bytes) -> WebSocketConnection:
    key = headers.get("sec-websocket-key", "")
    if not key or headers.get("sec-websocket-version") != "13":
        raise ValueError("bad websocket request")
    sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode("ascii"))
    return WebSocketConnection(sock, rest)


def frame(opcode: int, payload: bytes) -> bytes:
    """Кадр от сервера: без маски, целиком (FIN)."""
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


class WebSocketConnection:
    """Браузер за столом или зритель: то же, что protocol.Connection, но кадрами WebSocket и с русскими текстами.
    Кадры читает свой поток (он же отвечает на ping и замечает обрыв), recv() берёт готовые сообщения."""

    def __init__(self, sock: socket.socket, rest: bytes = b""):
        self.sock = sock
        sock.settimeout(None)
        self.buffer = rest
        self.inbox: queue.Queue[dict | None] = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False
        self.heard = time.monotonic()
        self.static: dict | None = None
        self.tr = None
        threading.Thread(target=self._read_loop, daemon=True, name="websocket").start()
        threading.Thread(target=self._ping_loop, daemon=True, name="websocket-ping").start()

    # --- то же, что у Connection -----------------------------------------------------------

    def send(self, message: dict) -> None:
        self._send(0x1, json.dumps(self.translate(message), ensure_ascii=False).encode("utf-8"))

    def recv(self) -> dict | None:
        """Следующее сообщение или None, если соединение закрыто."""
        if self.closed and self.inbox.empty():
            return None
        message = self.inbox.get()
        if message is None:
            self.inbox.put(None)   # закрыто: и следующие recv() вернут None
        return message

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._send(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        for closer in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                closer()
            except OSError:
                pass
        self.inbox.put(None)

    # --- перевод ---------------------------------------------------------------------------

    def translate(self, message: dict) -> dict:
        """Копия сообщения с русскими текстами (см. описание модуля); само сообщение не меняется —
        его же могут отправлять другим игрокам."""
        from ..web import common, ru
        t = message.get("t")
        if t == "sync":
            self.static = message["static"]
            self.tr = ru.Translator(self.static)
        tr = self.tr or ru.Translator()
        static = self.static

        def line(text: str) -> str:
            text = tr.line(text)
            return common.with_factions(static, text) if static else text

        out = dict(message)
        if t == "sync":
            out["log_ru"] = [line(text) for text in message["log"]]
        elif t == "event":
            out["text_ru"] = line(message["text"])
            found = ru.popup(static["names"], message["text"]) if static else None
            out["popup"] = [found[0][0], found[0][1], found[1]] if found else None
        elif t == "decision" and isinstance(message.get("data"), dict) and "reason" in message["data"]:
            out["reason_ru"] = ru.reason(message["data"]["reason"] or "")
        elif t in ("error", "end", "welcome") and "text" in message:
            out["text_ru"] = tr.line(message["text"].strip())
        return out

    # --- кадры -----------------------------------------------------------------------------

    def _send(self, opcode: int, payload: bytes) -> None:
        with self.lock:
            self.sock.sendall(frame(opcode, payload))

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:   # таймаут, оставшийся от рукопожатия: молчание ловит _ping_loop
                continue
            if not chunk:
                raise ConnectionError
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read(8))[0]
        if n > MAX_MESSAGE:
            raise ValueError("frame too long")
        mask = self._read(4) if b1 & 0x80 else b""
        payload = self._read(n)
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload)) if n < 4096 else _unmask(payload, mask)
        self.heard = time.monotonic()
        return bool(b0 & 0x80), b0 & 0x0F, payload

    def _read_loop(self) -> None:
        parts: list[bytes] = []
        try:
            while not self.closed:
                fin, opcode, payload = self._read_frame()
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    self._send(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x0):
                    parts.append(payload)
                    if sum(map(len, parts)) > MAX_MESSAGE:
                        raise ValueError("message too long")
                    if not fin:
                        continue
                    text, parts = b"".join(parts), []
                    try:
                        message = json.loads(text.decode("utf-8"))
                    except ValueError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("t") == "ping":
                        self.send({"t": "pong"})
                    else:
                        self.inbox.put(message)
                else:
                    raise ValueError(f"unsupported opcode {opcode}")
        except (OSError, ValueError, ConnectionError):
            pass
        self.close()

    def _ping_loop(self) -> None:
        while not self.closed:
            time.sleep(PING)
            if self.closed:
                return
            if time.monotonic() - self.heard > SILENCE:
                self.close()
                return
            try:
                self._send(0x9, b"")
            except OSError:
                self.close()
                return


def _unmask(payload: bytes, mask: bytes) -> bytes:
    """Снять маску с длинного кадра быстро: XOR целым числом, а не по байту."""
    n = len(payload)
    key = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(payload, "big") ^ key).to_bytes(n, "big")
