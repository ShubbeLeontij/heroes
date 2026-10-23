"""Человек за другим компьютером.

С консольным клиентом это консольный игрок, чей ввод и вывод идут по сети. Графическому
клиенту решения и состояние партии уходят данными (heroes/protocol/snapshot.py), а ответ
проверяется здесь же, движком. Вид клиента может смениться при переподключении.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from ..net.server import ModeChanged
from ..protocol.snapshot import decision_message, snapshot, sync_message
from .console import ConsolePlayer, Terminal

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game, GameView
    from ..net.server import Seat


class RemotePlayer(ConsolePlayer):
    parallel_safe = True  # у каждого свой экран: тайные решения сетевые игроки принимают одновременно

    def __init__(self, seat: Seat):
        # Цвета включены всегда: клиент сам вырезает их, если его вывод не в терминал.
        super().__init__(input_fn=seat.ask, output_fn=seat.out, color=True, terminal=Terminal())
        self.seat = seat
        self._events_lock = threading.Lock()
        self._decide_lock = threading.Lock()
        self.requests = 0
        self.missed_events = False  # графический клиент пропустил события (загрузка сохранения)

    def bind(self, game: Game, player: int) -> None:
        """Подписаться на события партии, чтобы игрок видел их сразу, а не только перед своим ходом."""
        self.game, self.me = game, player
        if self.on_log not in game.listeners:
            game.listeners.append(self.on_log)
            game.watchers.append(self.on_thinking)
        self.seat.greeting = self.greeting

    def greeting(self) -> list[dict]:
        """Первое, что получает графический клиент при подключении: всю партию (с его аватаркой)."""
        if self.seat.avatar:
            self.game.avatars[self.me] = self.seat.avatar
        return [sync_message(self.game, self.me)]

    def on_log(self, message: str) -> None:
        if self.game.replaying:  # при загрузке сохранения хвост событий покажем перед первым вопросом
            self.missed_events = True
            return
        if self.seat.mode != "gui":
            self.show_new_events()
        elif self.missed_events:
            self.missed_events = False
            self.seat.send(sync_message(self.game, self.me))
        else:
            self.seat.send({"t": "event", "text": message, "state": snapshot(self.game)})

    def on_thinking(self) -> None:
        if self.seat.mode == "gui" and not self.game.replaying and not self.missed_events:
            self.seat.send({"t": "state", "state": snapshot(self.game)})

    def show_new_events(self) -> None:
        with self._events_lock:  # события пишет основной поток, а вопросы задаются из рабочего
            super().show_new_events()

    def decide(self, decision: Decision, view: GameView) -> Any:
        with self._decide_lock:   # два тайных решения сразу (перемещение и гидра) — по очереди
            return self._decide(decision, view)

    def _decide(self, decision: Decision, view: GameView) -> Any:
        while True:
            try:
                if self.seat.mode == "gui":
                    return self.decide_gui(decision, view.game)
                return super().decide(decision, view)
            except ModeChanged:
                continue

    def decide_gui(self, decision: Decision, game: Game) -> Any:
        """Ответ графического клиента проверяется сразу: при ошибке он получает её и отвечает снова."""
        if self.missed_events:
            self.missed_events = False
            self.seat.send(sync_message(game, self.me))
        self.requests += 1
        message = decision_message(game, decision, self.requests)
        while True:
            reply = self.seat.request(message, "answer", "gui")
            if reply.get("id") != message["id"]:
                continue  # запоздалый ответ на прошлый вопрос
            answer = reply.get("answer")
            error = decision.validate(answer)
            if error is None:
                return answer
            message = {**message, "error": error}
