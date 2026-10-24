"""Метки значков в тексте: символы частной области Юникода вместо картинок (ресурсы, сила, ПО…).

Метки ставит перевод (ru.py), рисует страница веб-клиента (heroes/web/static/js/rich.js): порядок
NAMES она получает с /ui.json (heroes/net/web.py).
"""
from __future__ import annotations

NAMES = ["wood", "iron", "gold", "any", "shield", "laurel", "forest", "field", "lake", "mountain",
         "wood_off", "iron_off", "gold_off", "any_off", "mp", "build", "power", "artifact", "foundation", "victory",
         "modify", "avatar0", "avatar1", "avatar2", "avatar3",
         "science", "rage", "note", "krak", "curse", "hydra"]   # особенности фракций (из памятки и правил)
BASE = 0xE100
MARKS = {name: chr(BASE + i) for i, name in enumerate(NAMES)}   # _off — бледные ресурсы
