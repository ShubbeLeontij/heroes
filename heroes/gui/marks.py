"""Метки значков в тексте: символы частной области Юникода вместо картинок (ресурсы, сила, ПО…).

Отдельно от icons.py, потому что здесь нет tkinter: метки нужны и переводу (ru.py), и веб-клиенту
(heroes/net/web.py), которые работают и на компьютере без Tk. Рисует их icons.py (окно) и страница
веб-клиента (heroes/web/static/js/rich.js); порядок NAMES — общий для обоих.
"""
from __future__ import annotations

import re

NAMES = ["wood", "iron", "gold", "any", "shield", "laurel", "forest", "field", "lake", "mountain",
         "wood_off", "iron_off", "gold_off", "any_off", "mp", "build", "power", "artifact", "foundation", "victory",
         "modify", "avatar0", "avatar1", "avatar2", "avatar3",
         "science", "rage", "note", "krak", "curse", "hydra"]   # особенности фракций (из памятки и правил)
BASE = 0xE100
MARKS = {name: chr(BASE + i) for i, name in enumerate(NAMES)}   # _off — бледные ресурсы
KINDS = {mark: kind for kind, mark in MARKS.items()}
CLASS = "[" + "".join(MARKS.values()) + "]"
SPLIT = re.compile(f"({CLASS})")
TOKEN = re.compile(r"\{(\w+)\}")
AVATARS = {f"avatar{p}" for p in range(4)}


def markup(text: str) -> str:
    """«4{wood}» → «4» + метка дерева (неизвестное слово в скобках остаётся как есть)."""
    return TOKEN.sub(lambda m: MARKS.get(m[1], m[0]), text)


def has_icons(text: str) -> bool:
    return bool(SPLIT.search(text))


def segments(text: str) -> list[str]:
    """Текст и метки по очереди: «2», метка, « 1», метка…"""
    return [s for s in SPLIT.split(text) if s]
