"""Сервер партии: принимает людей (браузеры) и привязывает их к местам за столом.

Партия идёт на компьютере хозяина. Человек за столом получает решения и состояние партии
данными (heroes/protocol/snapshot.py). Если соединение рвётся, партия ждёт, пока игрок
подключится снова, и повторяет ему текущий вопрос. Браузер заходит на тот же порт (web.py):
он получает страницу веб-клиента, а её WebSocket садится за место. Зритель (hello с "watch")
места не занимает: он видит партию так же, как её видят все, без чьих-либо тайн.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from . import web
from .protocol import MODES, VERSION, Connection

MAX_AVATAR = 300_000  # аватарка в hello (base64) длиннее — не принимается


class Seat:
    """Место за столом для человека."""

    def __init__(self, name: str, log_fn: Callable[[str], None]):
        self.name = name
        self.log_fn = log_fn
        self.conn: Connection | None = None
        self.avatar: str | None = None   # своя аватарка игрока (PNG в base64), если клиент её прислал
        # Сообщения, с которых начинается связь: вся партия целиком.
        self.greeting: Callable[[], list[dict]] | None = None
        self.cond = threading.Condition()
        self.send_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        # браузер (web.py) сам знает, что его соединение закрылось: место свободно, не дожидаясь отправки
        return self.conn is not None and not getattr(self.conn, "closed", False)

    def attach(self, conn: Connection) -> None:
        """Посадить игрока за место и прислать ему всю партию (greeting)."""
        with self.send_lock:
            for message in self.greeting() if self.greeting else []:
                conn.send(message)
            with self.cond:
                old, self.conn = self.conn, conn
                self.cond.notify_all()
        if old:
            old.close()

    def drop(self, conn: Connection) -> None:
        with self.cond:
            if self.conn is conn:
                self.conn = None
                self.log_fn(f"⚠ {self.name} disconnected — waiting for them to reconnect")
        conn.close()

    def send(self, message: dict) -> None:
        with self.send_lock:
            conn = self.conn
            if conn is None:
                return
            try:
                conn.send(message)
                return
            except OSError:
                pass
        self.drop(conn)

    def request(self, message: dict, reply: str) -> dict:
        """Отправить запрос и дождаться ответа вида reply. При обрыве — дождаться переподключения
        и спросить снова."""
        while True:
            with self.cond:
                while self.conn is None:
                    self.cond.wait()
                conn = self.conn
            answer = None
            try:
                with self.send_lock:
                    conn.send(message)
                while (answer := conn.recv()) is not None and answer.get("t") != reply:
                    pass
            except (OSError, ValueError):
                answer = None
            if answer is None:
                self.drop(conn)
                continue
            return answer


class Audience:
    """Зрители: видят партию целиком (sync без своего места), её события и кто чем занят."""

    def __init__(self):
        self.conns: list[Connection] = []
        self.lock = threading.Lock()
        self.greeting: Callable[[], list[dict]] | None = None

    def attach(self, conn: Connection) -> None:
        with self.lock:
            for message in self.greeting() if self.greeting else []:
                conn.send(message)
            self.conns.append(conn)

    def send(self, message: dict) -> None:
        with self.lock:
            for conn in list(self.conns):
                if getattr(conn, "closed", False):
                    self.conns.remove(conn)
                    continue
                try:
                    conn.send(message)
                except OSError:
                    self.conns.remove(conn)
                    conn.close()

    def close(self) -> None:
        with self.lock:
            for conn in self.conns:
                conn.close()
            self.conns.clear()


class Server:
    def __init__(self, port: int, password: str | None = None, log_fn: Callable[[str], None] = print):
        self.port = port
        self.password = password
        self.log_fn = log_fn
        self.seats: dict[str, Seat] = {}
        self.audience = Audience()
        self.sock: socket.socket | None = None

    def seat(self, name: str) -> Seat:
        if name not in self.seats:
            self.seats[name] = Seat(name, self.log_fn)
        return self.seats[name]

    def start(self) -> None:
        if self.sock is not None:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", self.port))
        self.port = self.sock.getsockname()[1]  # при port=0 система выбирает свободный
        self.sock.listen()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                sock, addr = self.sock.accept()
            except OSError:
                return  # сервер закрыт
            threading.Thread(target=self._handshake, args=(sock, addr), daemon=True).start()

    def free_seats(self) -> list[str]:
        return [name for name, seat in self.seats.items() if not seat.connected]

    def _handshake(self, sock: socket.socket, addr) -> None:
        sock.settimeout(120)
        try:   # браузер: страница, картинки и профили, а запрос /ws — соединение для партии
            conn = web.accept(sock, self.password) if web.is_http(sock) else Connection(sock)
        except OSError:
            sock.close()
            return
        if conn is None:
            return
        try:
            hello = conn.recv()
            while True:
                if not hello or hello.get("t") != "hello":
                    raise ValueError("expected hello")
                if hello.get("version") != VERSION:
                    conn.send({"t": "error", "text": f"Protocol version mismatch (server has {VERSION}). Update the game."})
                    raise ValueError("version")
                if hello.get("mode") not in MODES:
                    conn.send({"t": "error", "text": f"Unknown client mode {hello.get('mode')!r}."})
                    raise ValueError("mode")
                if self.password and hello.get("password") != self.password:
                    conn.send({"t": "error", "text": "Wrong password."})
                    raise ValueError("password")
                if hello.get("watch"):
                    name = None
                    break
                free = self.free_seats()
                if not free:
                    conn.send({"t": "error", "text": "All network seats are taken."})
                    raise ValueError("no seats")
                name = hello.get("name")
                if name not in free:   # и единственное место не занимаем само: гость мог прийти поправить профиль
                    conn.send({"t": "choose", "seats": free})
                    hello = conn.recv()
                    continue
                break
        except (OSError, ValueError):
            conn.close()
            return
        sock.settimeout(None)
        if name is None:
            conn.send({"t": "welcome", "name": None, "watch": True, "text": "You are watching the game."})
            self.log_fn(f"A spectator connected ({addr[0]})")
            self.audience.attach(conn)
            return
        conn.send({"t": "welcome", "name": name, "text": f"You are at the table as {name}."})
        self.log_fn(f"{name} connected ({addr[0]})")
        avatar = hello.get("avatar")
        if isinstance(avatar, str) and len(avatar) <= MAX_AVATAR:
            self.seats[name].avatar = avatar
        self.seats[name].attach(conn)

    def wait_all(self) -> None:
        """Ждать, пока все сетевые места займут; печатать, кого ещё ждём."""
        shown = None
        while True:
            free = self.free_seats()
            if not free:
                return
            if free != shown:
                self.log_fn("Waiting for network players: " + ", ".join(free))
                shown = free
            time.sleep(0.3)

    def broadcast_end(self, text: str) -> None:
        for seat in self.seats.values():
            seat.send({"t": "end", "text": text})
        self.audience.send({"t": "end", "text": text})

    def close(self) -> None:
        if self.sock:
            self.sock.close()
        for seat in self.seats.values():
            if seat.conn:
                seat.conn.close()
        self.audience.close()


def local_addresses() -> list[str]:
    """IP-адреса этого компьютера в локальной сети (для подсказки, как подключиться)."""
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # пакеты не отправляются, только выбор интерфейса
            addresses.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        addresses.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(a for a in addresses if not a.startswith("127.")) or ["127.0.0.1"]
