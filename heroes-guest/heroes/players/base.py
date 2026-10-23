"""Интерфейс игрока: человек, Claude и бот отвечают на одни и те же запросы Decision."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import GameView


class Player:
    def decide(self, decision: Decision, view: GameView) -> Any:
        raise NotImplementedError

    def on_invalid(self, decision: Decision, answer: Any, error: str) -> None:
        """Ответ отклонён движком; следующий decide() по тому же запросу — повторная попытка."""
