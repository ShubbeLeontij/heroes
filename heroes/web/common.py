"""Справочное для страницы и перевода (по-русски): цвета мест, названия, метки значков.

Страница (heroes/web/static) получает константы отсюда через /ui.json (heroes/net/web.py),
а ru.py и web.py пользуются функциями ниже, когда дописывают русские тексты к сообщениям.
Всё остальное, что нужно для показа партии, страница считает сама (js/common.js).
"""
from __future__ import annotations

import re

from .marks import MARKS

PLAYER_COLORS = ["#d64545", "#2f6fd6", "#2e9e4f", "#d98a0b"]
TERRAIN_NAMES = {"field": "поле", "forest": "лес", "mountain": "горы", "lake": "озеро"}
RESOURCE_COLORS = {"wood": "#8a5a2b", "iron": "#4f6d8a", "gold": "#c79a00", "any": "#7a4fa0",
                   "rage": "#d9480f", "science": "#c2410c", "krak": "#a3478f"}
# ярость орков, наука гномов и краки наг тоже бывают в ценах и складе
RESOURCE_NAMES = {"wood": "дерево", "iron": "железо", "gold": "золото", "any": "любой",
                  "rage": "ярость", "science": "наука", "krak": "краки"}
ACTION_NAMES = {"wood": "Добыча дерева", "iron": "Добыча железа", "gold": "Добыча золота",
                "move": "Перемещение", "build": "Строительство"}
OBJECT_NAMES = {"fortress": "крепость", "foundation": "фундамент", "portal": "портал", "artifact": "артефакт"}
# значки объектов (у крепости своего значка в памятке нет — символ и подпись словом)
OBJECT_GLYPHS = {"fortress": "⚔ крепость", "foundation": MARKS["foundation"], "portal": MARKS["portal"],
                 "artifact": MARKS["artifact"]}
LEVELS = {n: MARKS[f"level{n}"] for n in range(1, 5)}   # гвозди уровня здания, как в памятке

# чем занят игрок, пока принимает решение (state["thinking"]); {region} — регион из вопроса, иначе — где он стоит
DOING = {
    "choose_start": "выбирает стартовый регион",
    "action_order": "выбирает порядок действий раунда",
    "distribute_resources": "распределяет ресурсы",
    "build_recruit": "строит здания и нанимает войска",
    "reserve_object": "выбирает объект для брони Башней иллюзий",
    "destination": "выбирает, куда переместиться из региона {region}",
    "region_stance": "решает, сражаться ли с соперниками в регионе {region}",
    "commit_army": "тайно заявляет войска на сражение в регионе {region}",
    "choose_reward": "выбирает награду за победу",
    "capture_fortress": "решает, штурмовать ли крепость {region}",
    "fight_monster": "решает, сражаться ли с чудовищем",
    "take_artifact": "решает, брать ли артефакт в регионе {region}",
    # особые решения фракций
    "raise_action": "решает, повышать ли уровень действий яростью",
    "buy_move_points": "решает, покупать ли очки перемещения",
    "hydra_destination": "тайно выбирает, куда пойдёт гидра",
    "make_lake": "решает, превращать ли регион {region} в озёрный",
    "curse_region": "выбирает, какой регион проклясть",
    "exchange_on_gain": "решает, обменивать ли железо на палача",
    "battle_upgrade": "решает, модифицировать ли войска перед сражением",
    "circle_note": "обводит ноту",
    "summon_hydra": "выбирает, куда призвать гидру",
    "avoid_battle": "решает, откупиться ли от сражения в регионе {region}",
}


def res(resources: dict[str, int] | None, faded: bool = False) -> str:
    """Короткая запись ресурсов значками: «2» + метка дерева… (marks.MARKS; рисует их страница, js/rich.js).
    faded — бледные иконки (ресурсы уже посещённого региона)."""
    return " ".join(f"{v}{MARKS[k + '_off' if faded else k]}" for k, v in (resources or {}).items() if v) or "—"


def name_of(static: dict, p: int | None) -> str:
    """Игрок с фракцией в скобках: «Аня (Люди)» — так игрок упоминается везде на странице."""
    if p is None:
        return "—"
    factions = static.get("player_factions")
    return f"{static['names'][p]} ({static['factions'][factions[p]]['name']})" if factions else static["names"][p]


def with_factions(static: dict, line: str) -> str:
    """Дописать фракцию после каждого имени игрока в строке журнала (если её там ещё нет)."""
    names = static["names"]
    if not static.get("player_factions") or not names:
        return line
    pattern = r"(?<!\w)(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?!\w)(?! \()"
    return re.sub(pattern, lambda m: name_of(static, names.index(m[1])), line)
