"""Связь окна с партией.

Окно получает поток сообщений-словарей — тех же, что сетевой протокол шлёт графическому
клиенту (sync, event, decision, end; см. heroes/net/protocol.py), — и отвечает на решения
через answer(). Партия может идти на этом компьютере (LocalLink: движок в отдельном
потоке) или на компьютере хозяина (RemoteLink: подключение по сети). Окну всё равно.

Дополнительно, только внутри окна:
  {"t": "status", "text"}                 — служебное сообщение (сервер, подключения)
  {"t": "thought", "name", "kind", "text"} — замысел Claude (если показ включён)
  {"t": "error", "text", "fatal"?}       — ошибка; fatal — партия дальше не пойдёт
  {"t": "disconnected"}                   — связь с хозяином потеряна
"""
from __future__ import annotations

import queue
import socket
import threading
import traceback
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from .. import cli, savegame
from ..engine import scoring
from ..net.protocol import VERSION, Connection, parse_address
from ..players.base import Player
from ..players.console import QuitGame
from ..protocol import render_text as R
from ..protocol.snapshot import decision_message, snapshot, sync_message

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game, GameView
    from ..net.server import Server

_QUIT = object()


class Link:
    local = False

    def __init__(self):
        self.inbox: queue.Queue[dict] = queue.Queue()
        self.closed = False

    def post(self, message: dict) -> None:
        self.inbox.put(message)

    def answer(self, request_id: int, answer: Any) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self.closed = True


class LocalLink(Link):
    """Партия на этом компьютере. Люди за окном — игроки GuiPlayer, остальные — как в консоли."""
    local = True

    def __init__(self):
        super().__init__()
        self.replies: queue.Queue = queue.Queue()
        self.requests = 0
        self.game: Game | None = None
        self.humans: list[int] = []   # места людей за этим окном
        self.server: Server | None = None
        self.finished = False
        self.missed_events = False

    def human(self) -> GuiPlayer:
        return GuiPlayer(self)

    def status(self, text: str) -> None:
        self.post({"t": "status", "text": text})

    def thought(self, name: str, kind: str, comment: str) -> None:
        self.post({"t": "thought", "name": name, "kind": kind, "text": comment})

    def start(self, game: Game, server: Server | None = None, password: str | None = None) -> None:
        self.game, self.server = game, server
        self.humans = [p for p, player in enumerate(game.players) if isinstance(player, GuiPlayer)]
        game.listeners.append(self.on_log)
        game.watchers.append(self.on_thinking)
        self.post(sync_message(game, self.humans[0] if self.humans else None))
        threading.Thread(target=self._run, args=(password,), daemon=True, name="game").start()

    def on_log(self, message: str) -> None:
        if self.closed:
            raise QuitGame  # окно закрыто: остановить партию, а не доигрывать её ботами и Claude
        if self.game.replaying:
            self.missed_events = True
        elif self.missed_events:
            self.missed_events = False
            self.post(sync_message(self.game, None))
        else:
            self.post({"t": "event", "text": message, "state": snapshot(self.game)})

    def on_thinking(self) -> None:
        """Кто-то начал или закончил решать: окно пишет, кого и чего ждёт."""
        if not self.closed and not self.game.replaying:
            self.post({"t": "state", "state": snapshot(self.game)})

    def _run(self, password: str | None) -> None:
        game, server = self.game, self.server
        try:
            if server and not cli.open_table(game, server, SimpleNamespace(password=password), out=self.status):
                return
            if server:   # гости подключились со своими аватарками
                self.post(sync_message(game, self.humans[0] if self.humans else None))
            scores = game.run()
            result = R.final_scores(game, scores, scoring.winners(game, scores))
            usage: list[str] = []
            cli.report_claude_usage(game, out=usage.append)
            self.finished = True
            self.post({"t": "end", "text": "\n\n".join([result, *usage]), "state": snapshot(game)})
            if server:
                server.broadcast_end("\n" + result)
        except QuitGame:
            if server:
                server.broadcast_end("The game was interrupted.")
        except SystemExit as e:  # open_table: порт занят
            self.post({"t": "error", "text": str(e), "fatal": True})
        except Exception:
            self.post({"t": "error", "text": traceback.format_exc(), "fatal": True})
        finally:
            if server:
                server.close()

    def next_id(self) -> int:
        self.requests += 1
        return self.requests

    def request(self, message: dict) -> Any:
        """Показать решение в окне и дождаться ответа (вызывается из потока партии)."""
        self.post(message)
        while True:
            item = self.replies.get()
            if item is _QUIT:
                raise QuitGame
            request_id, answer = item
            if request_id == message["id"]:
                return answer

    def answer(self, request_id: int, answer: Any) -> None:
        self.replies.put((request_id, answer))

    def save(self, path: str | None = None) -> str:
        """Сохранить партию; без пути — в saves/ с датой и номером раунда в имени."""
        return str(savegame.save(self.game, path or savegame.default_path(self.game)))

    def close(self) -> None:
        super().close()
        self.replies.put(_QUIT)
        if self.server:
            self.server.close()


class GuiPlayer(Player):
    """Человек за окном на этом компьютере."""

    def __init__(self, link: LocalLink):
        self.link = link

    def decide(self, decision: Decision, view: GameView) -> Any:
        if self.link.missed_events:  # партия только что загружена: сначала показать её целиком
            self.link.missed_events = False
            self.link.post(sync_message(view.game, None))
        message = decision_message(view.game, decision, self.link.next_id())
        while True:
            answer = self.link.request(message)
            error = decision.validate(answer)
            if error is None:
                return answer
            message = {**message, "error": error}


class RemoteLink(Link):
    """Подключение к партии на компьютере хозяина (python -m heroes на его стороне)."""

    def __init__(self, address: str, name: str | None, password: str | None, avatar: str | None = None):
        super().__init__()
        self.address, self.name, self.password = address, name, password
        self.avatar = avatar   # своя аватарка (PNG в base64): хозяин покажет её всем
        self.conn: Connection | None = None
        self.ended = False

    def start(self) -> None:
        self.ended = False
        threading.Thread(target=self._run, daemon=True, name="network").start()

    def hello(self, name: str | None) -> None:
        hello = {"t": "hello", "version": VERSION, "name": name, "password": self.password, "mode": "gui"}
        if self.avatar:
            hello["avatar"] = self.avatar
        self.conn.send(hello)

    def _run(self) -> None:
        try:
            host, port = parse_address(self.address)
            sock = socket.create_connection((host, port), timeout=10)
        except (OSError, ValueError) as e:
            self.post({"t": "error", "text": f"Не удалось подключиться к {self.address}: {e}", "fatal": True})
            return
        sock.settimeout(None)
        self.conn = conn = Connection(sock)
        try:
            self.hello(self.name)
            while (message := conn.recv()) is not None:
                if message.get("t") == "welcome":
                    self.name = message["name"]
                if message.get("t") in ("end", "error"):
                    self.ended = True
                    if message["t"] == "error":
                        message["fatal"] = True
                self.post(message)
        except (OSError, ValueError):
            pass
        finally:
            conn.close()
        if not self.closed and not self.ended:
            self.post({"t": "disconnected"})

    def choose_seat(self, name: str) -> None:
        self.name = name
        self.hello(name)

    def answer(self, request_id: int, answer: Any) -> None:
        try:
            self.conn.send({"t": "answer", "id": request_id, "answer": answer})
        except (OSError, AttributeError):
            self.post({"t": "disconnected"})

    def close(self) -> None:
        super().close()
        if self.conn:
            self.conn.close()
