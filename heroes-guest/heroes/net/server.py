"""Сервер партии: принимает сетевых игроков и привязывает их к местам за столом.

Партия идёт на компьютере хозяина. Сетевой игрок — это консольный игрок, у которого
ввод и вывод идут через Seat, либо графический клиент, которому решения приходят данными.
Если соединение рвётся, партия ждёт, пока игрок подключится снова, и повторяет ему
текущий вопрос. Переподключиться можно и другим клиентом (консольным вместо графического).
"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Callable

from .protocol import MODES, VERSION, Connection

RECENT = 60  # сколько последних сообщений повторить игроку после переподключения
MAX_AVATAR = 300_000  # аватарка в hello (base64) длиннее — не принимается


class ModeChanged(Exception):
    """Игрок переподключился клиентом другого вида: текущий вопрос надо задать заново по-другому."""


class Seat:
    """Место за столом для сетевого игрока."""

    def __init__(self, name: str, log_fn: Callable[[str], None]):
        self.name = name
        self.log_fn = log_fn
        self.conn: Connection | None = None
        self.recent: deque[dict] = deque(maxlen=RECENT)  # последний вывод — для повтора после обрыва
        self.ever_connected = False
        self.mode = "console"
        self.avatar: str | None = None   # своя аватарка игрока (PNG в base64), если клиент её прислал
        # Для графического клиента: сообщения, с которых начинается связь (вся партия целиком).
        self.greeting: Callable[[], list[dict]] | None = None
        self.cond = threading.Condition()
        self.send_lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.conn is not None

    def attach(self, conn: Connection, mode: str = "console") -> None:
        """Посадить игрока за место. Консольному клиенту при переподключении повторить последние
        сообщения: иначе он увидит только строку текущего вопроса без вариантов ответа.
        Графическому — прислать всю партию (greeting)."""
        with self.send_lock:
            if mode == "gui":
                for message in self.greeting() if self.greeting else []:
                    conn.send(message)
            elif self.ever_connected:
                conn.send({"t": "out", "text": "— connection restored, recent messages: —"})
                for message in self.recent:
                    if message.get("t") == "out":
                        conn.send(message)
            with self.cond:
                old, self.conn = self.conn, conn
                self.mode = mode
                self.ever_connected = True
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
            self.recent.append(message)
            conn = self.conn
            if conn is None:
                return
            try:
                conn.send(message)
                return
            except OSError:
                pass
        self.drop(conn)

    def out(self, text: str) -> None:
        self.send({"t": "out", "text": text})

    def request(self, message: dict, reply: str, mode: str) -> dict:
        """Отправить запрос и дождаться ответа вида reply. При обрыве — дождаться переподключения
        и спросить снова. ModeChanged — игрок теперь сидит за клиентом другого вида (не mode)."""
        while True:
            with self.cond:
                while self.conn is None:
                    self.cond.wait()
                conn = self.conn
                if self.mode != mode:
                    raise ModeChanged
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

    def ask(self, prompt: str) -> str:
        """Запросить строку у консольного клиента."""
        return str(self.request({"t": "in", "prompt": prompt}, "line", "console").get("text", ""))


class Server:
    def __init__(self, port: int, password: str | None = None, log_fn: Callable[[str], None] = print):
        self.port = port
        self.password = password
        self.log_fn = log_fn
        self.seats: dict[str, Seat] = {}
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
        conn = Connection(sock)
        sock.settimeout(120)
        try:
            hello = conn.recv()
            while True:
                if not hello or hello.get("t") != "hello":
                    raise ValueError("expected hello")
                if hello.get("version") != VERSION:
                    conn.send({"t": "error", "text": f"Protocol version mismatch (server has {VERSION}). Update the game."})
                    raise ValueError("version")
                if hello.get("mode", "console") not in MODES:
                    conn.send({"t": "error", "text": f"Unknown client mode {hello.get('mode')!r}."})
                    raise ValueError("mode")
                if self.password and hello.get("password") != self.password:
                    conn.send({"t": "error", "text": "Wrong password."})
                    raise ValueError("password")
                free = self.free_seats()
                if not free:
                    conn.send({"t": "error", "text": "All network seats are taken."})
                    raise ValueError("no seats")
                name = hello.get("name")
                if name not in free:
                    if len(free) == 1 and not name:
                        name = free[0]
                    else:
                        conn.send({"t": "choose", "seats": free})
                        hello = conn.recv()
                        continue
                break
        except (OSError, ValueError):
            conn.close()
            return
        sock.settimeout(None)
        conn.send({"t": "welcome", "name": name, "text": f"You are at the table as {name}."})
        self.log_fn(f"{name} connected ({addr[0]})")
        avatar = hello.get("avatar")
        if isinstance(avatar, str) and len(avatar) <= MAX_AVATAR:
            self.seats[name].avatar = avatar
        self.seats[name].attach(conn, hello.get("mode", "console"))

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

    def close(self) -> None:
        if self.sock:
            self.sock.close()
        for seat in self.seats.values():
            if seat.conn:
                seat.conn.close()


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
