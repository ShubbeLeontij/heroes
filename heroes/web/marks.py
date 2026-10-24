"""Метки значков в тексте: символы частной области Юникода вместо картинок (ресурсы, сила, ПО…).

Метки ставит перевод (ru.py), рисует страница веб-клиента (heroes/web/static/js/rich.js): порядок
NAMES она получает с /ui.json (heroes/net/web.py).
"""
from __future__ import annotations

import json

from ..engine.data import DATA_DIR

NAMES = ["wood", "iron", "gold", "any", "shield", "laurel", "forest", "field", "lake", "mountain",
         "wood_off", "iron_off", "gold_off", "any_off", "mp", "build", "power", "artifact", "foundation", "victory",
         "modify", "avatar0", "avatar1", "avatar2", "avatar3",
         "science", "rage", "note", "krak", "curse", "hydra",   # особенности фракций (из памятки и правил)
         "move", "portal", "level1", "level2", "level3", "level4"]
# рисунки артефактов с карты (artifact_<регион>): артефакт в тексте — его рисунком, а не названием региона
NAMES += [f"artifact_{name}" for name, r in json.loads((DATA_DIR / "map.json").read_text(encoding="utf-8"))["regions"].items()
          if "artifact" in r["objects"]]
BASE = 0xE100
MARKS = {name: chr(BASE + i) for i, name in enumerate(NAMES)}   # _off — бледные ресурсы


def artifact(region: str) -> str:
    """Значок артефакта региона; если рисунка нет — кубок и название региона."""
    return MARKS.get(f"artifact_{region}") or f"{MARKS['artifact']} {region}"
