"""Запросы решений к игрокам и проверка ответов.

Движок не знает, кто отвечает: человек, Claude или бот. Он формирует Decision:
человекочитаемый текст, машинное описание вариантов (data) и функцию проверки ответа.
Ответы — простые JSON-совместимые значения (строки, списки, словари).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .data import RESOURCES

Validator = Callable[[Any], "str | None"]


@dataclass
class Decision:
    kind: str
    player: int
    text: str
    data: dict
    validate: Validator
    default: Any
    # Выбора по факту нет (единственный вариант или все прочие заведомо бессмысленны): движок не
    # спрашивает игрока и берёт default; в журнал решений такое не пишется.
    forced: bool = False
    # Проверка записанного ответа при загрузке сохранения, если она мягче validate: правила, которые движок
    # ввёл позже (запрет заведомо проигрышного штурма), не должны ломать старые сохранения. None — validate.
    recorded: Validator | None = None
    # Почему у решения без выбора (forced) нет выбора — игроку сообщают, что вопрос пропущен и почему.
    reason: str = ""

    def check_recorded(self, answer: Any) -> str | None:
        return (self.recorded or self.validate)(answer)


def check_resource_split(answer: Any, amount: int, allowed=RESOURCES) -> str | None:
    """Раскладка ровно amount ресурсов по видам allowed: {"wood": 1, "gold": 2}."""
    if not isinstance(answer, dict):
        return "Expected an object like {\"wood\": 1, \"iron\": 0, \"gold\": 2}"
    for k, v in answer.items():
        if k not in allowed:
            return f"Not an allowed resource: {k}"
        if not isinstance(v, int) or v < 0:
            return f"Amount of {k} must be a non-negative integer"
    if sum(answer.values()) != amount:
        return f"You must split exactly {amount}, but split {sum(answer.values())}"
    return None


def check_army(answer: Any, available: dict[str, int]) -> str | None:
    """Заявка войск: {"soldier": 2, "knight": 1}; не больше, чем есть."""
    if not isinstance(answer, dict):
        return "Expected an object like {\"soldier\": 2}"
    for unit, count in answer.items():
        if unit not in available:
            return f"Unknown unit: {unit}"
        if not isinstance(count, int) or count < 0:
            return f"Number of {unit} must be a non-negative integer"
        if count > available[unit]:
            return f"{unit}: committed {count}, but you only have {available[unit]}"
    return None


def check_choice(answer: Any, options) -> str | None:
    if answer not in options:
        return f"Not a valid option: {answer!r}. Valid: {', '.join(map(str, options))}"
    return None
