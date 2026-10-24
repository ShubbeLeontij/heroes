"""Общее для виджетов окна: цвета, шрифты, короткие подписи (по-русски).

Окно работает только с данными из heroes/protocol/snapshot.py (static и state), без движка:
так одинаково показываются и своя партия, и партия на чужом компьютере. Тексты движка
приходят по-английски (их читают Claude и консоль), на экран окна они идут через ru.py.
"""
from __future__ import annotations

import re

from ..profiles import winners
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
OBJECT_GLYPHS = {"fortress": "⚔", "foundation": "▣", "portal": "◎", "artifact": "✦"}
LEVELS = {1: "I", 2: "II", 3: "III", 4: "IV"}
SCORE_NAMES = {"buildings": "здания и эффекты", "foundations": "фундаменты", "resources": "запасы ресурсов",
               "battles": "победы в сражениях", "artifacts": "артефакты"}

BG = "#f6f1e4"
PANEL_BG = "#fbf8f0"
INK = "#2b2620"
MUTED = "#8a8275"
ACCENT = "#c2410c"
GOOD = "#2f7d32"
BAD = "#c62828"

FAMILY = "DejaVu Sans"


def font(size: int = 10, *styles: str) -> tuple:
    return (FAMILY, size, *styles)


def color_of(p: int | None) -> str:
    return MUTED if p is None else PLAYER_COLORS[p % len(PLAYER_COLORS)]


def res(resources: dict[str, int] | None, faded: bool = False) -> str:
    """Короткая запись ресурсов иконками: «2» + иконка дерева… (метки icons.MARKS; выводить через icons).
    faded — бледные иконки (ресурсы уже посещённого региона)."""
    return " ".join(f"{v}{MARKS[k + '_off' if faded else k]}" for k, v in (resources or {}).items() if v) or "—"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова для числа n: 1 ресурс, 3 ресурса, 5 ресурсов."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def res_long(resources: dict[str, int] | None) -> str:
    """«дерево 2, золото 1» — без склонений, как на складе."""
    return ", ".join(f"{RESOURCE_NAMES[k]} {v}" for k, v in (resources or {}).items() if v) or "ничего"


def condition_text(cond: dict) -> str:
    kind = cond["condition"]
    if kind == "pay":
        return f"заплатить: {res_long(cond['cost'])}"
    return {
        "foundationsBuilt": f"{cond['count']} фундамента с вашими зданиями",
        "battleWins": f"{cond['count']} побед в сражениях",
        "mountainsVisited": f"посещено {cond['count']} горных региона",
    }[kind]


def name_of(static: dict, p: int | None, avatar: bool = False) -> str:
    """Игрок с фракцией в скобках: «Аня (Люди)» — так игрок упоминается везде в окне.
    avatar — с меткой его аватарки впереди (для подписей, которые рисуют значки: RichLabel, canvas_text)."""
    if p is None:
        return "—"
    factions = static.get("player_factions")   # у хозяина старой версии фракций в static нет
    name = f"{static['names'][p]} ({static['factions'][factions[p]]['name']})" if factions else static["names"][p]
    return MARKS[f"avatar{p}"] + name if avatar else name


# чем занят игрок, пока принимает решение (state["thinking"]); {region} — регион, где он стоит
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


def thinking_text(static: dict, state: dict, avatar: bool = False) -> str:
    """«Claude (Люди) выбирает порядок действий раунда» — по строке на каждого, кто сейчас решает."""
    lines = []
    for p, kind in state.get("thinking", []):
        doing = DOING.get(kind, "принимает решение").format(region=state["players"][p]["region"] or "—")
        lines.append(f"{name_of(static, p, avatar)} {doing}…")
    return "\n".join(lines)


def turn_order(static: dict, state: dict) -> list[int]:
    """Очерёдность сейчас. В раунде — от первого игрока по часовой стрелке (так объявляют регионы
    и идут сражения); до первого раунда — выбор стартовых регионов: от соседа справа от первого
    против часовой стрелки."""
    n, first = len(static["names"]), state["first_player"]
    if not state["round"]:
        return [(first - 1 - i) % n for i in range(n)]
    return [(first + i) % n for i in range(n)]


def next_first_players(static: dict, state: dict, count: int) -> list[tuple[int, int]]:
    """Кто будет первым игроком в следующих раундах: [(раунд, место), …], не дальше последнего раунда.
    Первым становится сосед слева — следующее место за столом."""
    n, first, now = len(static["names"]), state["first_player"], state["round"]
    shift = 0 if not now else 1   # до первого раунда first_player — первый игрок раунда 1
    return [(r, (first + shift + i) % n) for i, r in enumerate(range(now + 1, min(now + count, static["rounds"]) + 1))]


def names_of(static: dict) -> list[str]:
    return [name_of(static, p) for p in range(len(static["names"]))]


def name_spans(static: dict, line: str) -> list[tuple[int, int]]:
    """Где в строке упомянуты игроки: [(начало имени, место за столом), …] — перед ними журнал ставит аватарки."""
    names = static["names"]
    if not names:
        return []
    pattern = r"(?<!\w)(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?!\w)"
    return [(m.start(), names.index(m[1])) for m in re.finditer(pattern, line)]


def with_factions(static: dict, line: str) -> str:
    """Дописать фракцию после каждого имени игрока в строке журнала (если её там ещё нет)."""
    names = static["names"]
    if not static.get("player_factions") or not names:
        return line
    pattern = r"(?<!\w)(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?!\w)(?! \()"
    return re.sub(pattern, lambda m: name_of(static, names.index(m[1])), line)


def terrain_of(static: dict, state: dict, region: str) -> str:
    """Местность региона сейчас: Гейзер наг делает регионы озёрными для всех."""
    return state.get("terrain", {}).get(region) or static["regions"][region]["terrain"]


def artifact_condition(static: dict, faction: str, region: str) -> dict:
    """Условие артефакта для фракции: у наг часть условий своя (3 крака вместо 12 ресурсов)."""
    return static["factions"][faction].get("artifact_conditions", {}).get(region) or static["regions"][region]["artifact"]


def monster_line(static: dict, faction: str) -> list[dict]:
    """Линия чудовищ фракции: общая или своя (наги)."""
    return static["factions"][faction].get("monsters") or static["monsters"]


def building_text(static: dict, faction: str, bid: str) -> str:
    b = static["factions"][faction]["buildings"][bid]
    return b.get("text_ru") or b["text"]


def artifact_text(static: dict, faction: str, region: str) -> str:
    f = static["factions"][faction]
    return f.get("artifacts_ru", {}).get(region) or f["artifacts"][region]


def object_text(static: dict, state: dict, region: str, faction: str | None = None) -> str:
    """Объект региона с его нынешним положением: чья крепость, у кого артефакт, бронь, проклятие.
    faction — чьими глазами: условие артефакта у наг своё."""
    r = static["regions"][region]
    obj = r["object"]
    if obj == "fortress":
        f = r["fortress"]
        text = f"крепость, сила {f['strength']} → {f['vp']} ПО + {res_long(f['loot'])}"
        if region in state["fortress_owner"]:
            text += f"; захвачена: {name_of(static, state['fortress_owner'][region])}"
    elif obj == "artifact":
        text = f"артефакт ({condition_text(artifact_condition(static, faction, region) if faction else r['artifact'])})"
        if region in state["artifact_holder"]:
            text += f"; у игрока {name_of(static, state['artifact_holder'][region])}"
    elif obj == "foundation":
        levels = [f"{name_of(static, p)} {pl['foundations'][region]}" for p, pl in enumerate(state["players"])
                  if pl["foundations"].get(region)]
        text = "фундамент" + (f" (уровни: {', '.join(levels)})" if levels else "")
    else:
        text = "портал (все порталы соседствуют друг с другом)"
    if region in state["reserved"]:
        text += f"; бронь: {name_of(static, state['reserved'][region])} (Башня иллюзий)"
    if region in state.get("cursed", {}):
        text += f"; проклят: {name_of(static, state['cursed'][region])}"
    return text


def upgrade_gains(units: dict, army: dict[str, int], upgrade: dict | None) -> list[int]:
    """Лавка бронника: на сколько вырастет сила каждой заявленной единицы при модификации (по убыванию).
    upgrade — данные решения (combat.upgrade_data): во что модифицируется каждое войско."""
    gains: list[int] = []
    for unit, n in army.items():
        gain = (upgrade or {}).get("strength", {}).get(unit, 0) - units[unit]["strength"]
        gains += [gain] * n if gain > 0 else []
    return sorted(gains, reverse=True)


def army_strength(units: dict, army: dict[str, int] | None = None, rules: dict | None = None,
                  upgrade: dict | None = None, upgrades: int = 0) -> int:
    """Сила заявки army (по умолчанию — всех войск). units: {unit: {"count", "strength"}} — как в снимке и в данных
    решений; rules — snapshot.strength_rules: прибавка к итогу, войска, чья сила — их число в заявке (краки),
    и войска, удваивающие силу других (механики); upgrades — сколько единиц модифицировано Лавкой бронника
    (upgrade — её данные). Повторяет engine.effects.army_strength."""
    army = {u: v["count"] for u, v in units.items()} if army is None else army
    rules = rules or {}
    doublers = rules.get("doublers", {})
    counted = set(rules.get("count_units", ()))

    def one(unit: str) -> int:
        return army.get(unit, 0) if unit in counted else units[unit]["strength"]

    values = sorted((one(u) for u, n in army.items() if u not in doublers for _ in range(n)), reverse=True)
    boosts = sum(army.get(u, 0) * targets for u, targets in doublers.items())
    total = sum(values) + sum(values[:boosts]) + sum(one(u) * army.get(u, 0) for u in doublers if u in units)
    total += sum(upgrade_gains(units, army, upgrade)[:upgrades]) if upgrades else 0
    return total + rules.get("bonus", 0)


def final_table(state: dict, static: dict | None = None) -> str:
    """Итоговая таблица очков по последнему снимку состояния."""
    names = names_of(static) if static else [pl["name"] for pl in state["players"]]
    width = max(len(n) for n in names) + 2
    lines = [" " * 20 + "".join(n.rjust(width) for n in names)]
    for key, title in SCORE_NAMES.items():
        lines.append(title.ljust(20) + "".join(str(pl["score"][key]).rjust(width) for pl in state["players"]))
    lines.append("ИТОГО".ljust(20) + "".join(str(pl["score"]["total"]).rjust(width) for pl in state["players"]))
    win = winners(state)
    lines.append(("Победитель: " if len(win) == 1 else "Победители: ") + ", ".join(names[p] for p in win))
    return "\n".join(lines)
