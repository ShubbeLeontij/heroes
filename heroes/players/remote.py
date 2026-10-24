"""Человек за столом — в браузере, на этом или другом компьютере.

Решения и состояние партии уходят ему данными (heroes/protocol/snapshot.py), а ответ проверяется
здесь же, движком. Зрителям (Spectators) уходит то же состояние без решений и без своего места.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from ..protocol.snapshot import decision_message, snapshot, sync_message
from .base import Player

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game, GameView
    from ..net.server import Audience, Seat


class RemotePlayer(Player):
    parallel_safe = True  # у каждого свой экран: тайные решения люди принимают одновременно

    def __init__(self, seat: Seat):
        self.seat = seat
        self._decide_lock = threading.Lock()
        self.requests = 0
        self.missed_events = False  # клиент пропустил события (загрузка сохранения)

    def bind(self, game: Game, player: int) -> None:
        """Подписаться на события партии, чтобы игрок видел их сразу, а не только перед своим ходом."""
        self.game, self.me = game, player
        if self.on_log not in game.listeners:
            game.listeners.append(self.on_log)
            game.watchers.append(self.on_thinking)
        self.seat.greeting = self.greeting

    def greeting(self) -> list[dict]:
        """Первое, что получает клиент при подключении: всю партию (с его аватаркой)."""
        if self.seat.avatar:
            self.game.avatars[self.me] = self.seat.avatar
        return [sync_message(self.game, self.me)]

    def on_log(self, message: str) -> None:
        if self.game.replaying:  # при загрузке сохранения всю партию покажем перед первым вопросом
            self.missed_events = True
        elif self.missed_events:
            self.missed_events = False
            self.seat.send(sync_message(self.game, self.me))
        else:
            self.seat.send({"t": "event", "text": message, "state": snapshot(self.game)})

    def on_thinking(self) -> None:
        if not self.game.replaying and not self.missed_events:
            self.seat.send({"t": "state", "state": snapshot(self.game)})

    def decide(self, decision: Decision, view: GameView) -> Any:
        """Ответ проверяется сразу: при ошибке клиент получает её и отвечает снова."""
        with self._decide_lock:   # два тайных решения сразу (перемещение и гидра) — по очереди
            game = view.game
            if self.missed_events:
                self.missed_events = False
                self.seat.send(sync_message(game, self.me))
            self.requests += 1
            message = decision_message(game, decision, self.requests)
            while True:
                reply = self.seat.request(message, "answer")
                if reply.get("id") != message["id"]:
                    continue  # запоздалый ответ на прошлый вопрос
                answer = reply.get("answer")
                error = decision.validate(answer)
                if error is None:
                    return answer
                message = {**message, "error": error}


class Spectators:
    """Зрители партии (Audience сервера): вся партия при подключении, затем события и кто чем занят."""

    def __init__(self, game: Game, audience: Audience):
        self.game, self.audience = game, audience
        self.missed_events = False
        audience.greeting = lambda: [sync_message(game, None)]
        game.listeners.append(self.on_log)
        game.watchers.append(self.on_thinking)

    def on_log(self, message: str) -> None:
        if self.game.replaying:
            self.missed_events = True
        elif self.missed_events:
            self.missed_events = False
            self.audience.send(sync_message(self.game, None))
        else:
            self.audience.send({"t": "event", "text": message, "state": snapshot(self.game)})

    def on_thinking(self) -> None:
        if not self.game.replaying and not self.missed_events:
            self.audience.send({"t": "state", "state": snapshot(self.game)})
