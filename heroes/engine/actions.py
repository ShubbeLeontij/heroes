"""Действия добычи и действие строительства и найма (включая Башню иллюзий и особые здания фракций)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .economy import can_pay, exchange_for_units, fmt_res, gain, gain_science, holdings, pay, stock, sum_costs
from .effects import building_cost, effects_of, hire_cost, hireable_units, science, source_name
from .special import apply_raise, check_raise, circle_notes, curse_on_build, raise_offer, raise_text
from .state import OBJECT_NAMES, RESOURCE_NAMES

if TYPE_CHECKING:
    from .game import Game


def gather(game: Game, resource: str, level: int) -> None:
    for p in game.state.turn_order():
        bonus = sum(e["value"] for e in effects_of(game, p, "bonus_on_gather") if e["resource"] == resource)
        gain(game, p, {resource: level + bonus}, f"gather {RESOURCE_NAMES[resource]}")


# --- Строительство и наём -------------------------------------------------------------
#
# По правилам (с. 6) одно здание и наём идут «в любой последовательности», а модификация орков,
# Дом вождей и Библиотека гномов действуют «во время действия строительства». Поэтому действие —
# цикл шагов: игрок отвечает списком шагов, движок применяет их по одному (каждый сразу даёт свои
# эффекты: ярость Арены, науку Цеха, ресурсы Сборщиков) и спрашивает снова, пока игрок не ответит
# «done» или делать больше нечего. Так орк может нанять громил, на их ярость построить Мастерскую
# и нанять взрывателей, а гном — нанять механиков и только потом построить Генератор.

DONE = "done"
STEPS = ("build", "hire", "upgrade", "raise", "science")
MAX_ASKS = 100   # страховка от бесконечного цикла: каждый шаг что-то тратит, так что столько не бывает


@dataclass
class BuildTurn:
    """Действие строительства одного игрока: что уже сделано."""
    level: int                       # уровень действия (Дом вождей прибавляет player.raised["build"])
    built: str | None = None         # здание, построенное в этом действии (оно одно)
    science_bought: bool = False     # Библиотека — один обмен за действие
    steps: list[dict] = field(default_factory=list)   # применённые шаги
    rejected: dict | None = None     # шаг прошлого ответа, который не удалось применить: {"step", "error"}


def build_and_recruit(game: Game, level: int) -> None:
    for p in game.state.turn_order():
        exchange_for_units(game, p)                      # наги: обязательный обмен ресурсов на краков
        build_turn(game, p, level)
        if effects_of(game, p, "reserve_object"):        # люди: Башня иллюзий — в конце действия
            illusion_tower(game, p)
        curse_on_build(game, p)                          # нежить: Чумной столб — в конце действия


def build_turn(game: Game, p: int, level: int) -> None:
    turn = BuildTurn(level)
    for _ in range(MAX_ASKS):
        answer = game.ask(
            p, "build_recruit", build_text(game, p, turn), build_data(game, p, turn),
            lambda a: check_steps(game, p, turn, a),
            [DONE],
            forced=not can_act(game, p, turn),
            reason="nothing more was possible at this build action" if turn.built else
                   f"at this build action (level {level_of(game, p, turn)}) you could neither build, hire nor upgrade "
                   "anything: not enough resources or no building of a fitting level",
        )
        turn.rejected = None
        if not run_steps(game, p, turn, answer):
            return


def run_steps(game: Game, p: int, turn: BuildTurn, steps: list) -> bool:
    """Применить шаги по порядку. False — действие закончено («done» или пустой список).
    Шаг, который уже нельзя выполнить (первый проверен заранее, остальные зависят от предыдущих),
    останавливает применение: сделанное остаётся, игрока спрашивают снова и объясняют почему."""
    if not steps:
        return False
    for step in steps:
        if step == DONE:
            return False
        error = check_step(game, p, turn, step)
        if error:
            turn.rejected = {"step": step, "error": error}
            return True
        apply_step(game, p, turn, step)
    return True


def level_of(game: Game, p: int, turn: BuildTurn) -> int:
    return turn.level + game.state.players[p].raised.get("build", 0)


def buildable(game: Game, p: int, level: int) -> list[str]:
    player = game.state.players[p]
    result = []
    for bid, b in game.faction_of(p).buildings.items():
        if bid in player.built or b.level > level or not can_pay(game, p, building_cost(game, p, bid)):
            continue
        if b.requires and not any(req in player.built for req in b.requires):
            continue
        result.append(bid)
    return result


def foundation_slot(game: Game, p: int) -> str | None:
    """Фундамент в текущем регионе, если он доступен игроку (не забронирован соперником).

    Только что построенное здание возводится на нём автоматически, если его уровень выше
    уже вписанного: это бесплатно и даёт только плюсы, поэтому игрока не спрашиваем."""
    region = game.state.players[p].region
    if game.map.regions[region].object != "foundation":
        return None
    if game.state.reserved.get(region, p) != p:
        return None
    return region


def hire_price(game: Game, p: int, hire: dict[str, int]) -> dict[str, int]:
    """Цена найма по единице: наука Цеха за каждого автоматона сразу удешевляет следующих (Конвейер).
    Поэтому сначала нанимаются те, кто приносит науку, — так никогда не дороже."""
    per_hire = {e["unit"]: e["value"] for e in effects_of(game, p, "science_per_hire")}
    track = effects_of(game, p, "science_track")
    sci = science(game, p)
    total: dict[str, int] = {}
    for unit in sorted(hire, key=lambda u: u not in per_hire):
        for _ in range(hire[unit]):
            total = sum_costs(total, hire_cost(game, p, unit, sci))
            sci = min(sci + per_hire.get(unit, 0), track[0]["max"] if track else sci)
    return total


def upgrade_paths(game: Game, p: int) -> dict[str, dict]:
    """Модификация войск орков: путь «откуда>куда» → описание; пусто — у фракции её нет."""
    return {f"{x['from']}>{x['to']}": x for e in effects_of(game, p, "upgrade_units_on_build") for x in e["paths"]}


def science_offer(game: Game, p: int, turn: BuildTurn) -> dict | None:
    """Библиотека гномов: можно ли сейчас обменять ресурсы на науку (один раз за действие)."""
    if turn.science_bought:
        return None
    track = effects_of(game, p, "science_track")
    for e in effects_of(game, p, "buy_science_on_build"):
        if can_pay(game, p, e["cost"]) and not (track and science(game, p) >= track[0]["max"]):
            return e
    return None


def can_act(game: Game, p: int, turn: BuildTurn) -> bool:
    """Есть ли хоть один возможный шаг — иначе вопрос не задаётся."""
    player = game.state.players[p]
    if not turn.built and buildable(game, p, level_of(game, p, turn)):
        return True
    if any(can_pay(game, p, hire_cost(game, p, u)) for u in hireable_units(game, p)):
        return True
    if any(player.units.get(x["from"], 0) and can_pay(game, p, x["cost"]) for x in upgrade_paths(game, p).values()):
        return True
    return raise_offer(game, p, "build", not turn.built) is not None or science_offer(game, p, turn) is not None


def build_data(game: Game, p: int, turn: BuildTurn) -> dict:
    player = game.state.players[p]
    faction = game.faction_of(p)
    level = level_of(game, p, turn)
    slot = foundation_slot(game, p)
    paths = upgrade_paths(game, p)
    science_e = science_offer(game, p, turn)
    return {
        "level": level,
        "resources": holdings(game, p),   # склад плюс ярость, наука, краки — чем можно платить
        "built": turn.built,
        "steps": list(turn.steps),
        "rejected": turn.rejected,
        "options": {} if turn.built else {
            bid: {"name": faction.buildings[bid].name, "level": faction.buildings[bid].level,
                  "cost": building_cost(game, p, bid), "vp": faction.buildings[bid].vp,
                  "text": faction.buildings[bid].text}
            for bid in buildable(game, p, level)
        },
        "foundation": {"region": slot, "level": player.foundations.get(slot, 0)} if slot else None,
        "hire": {u: {"name": faction.units[u].name, "cost": hire_cost(game, p, u)} for u in hireable_units(game, p)},
        "upgrade": {"paths": {k: {"from": x["from"], "to": x["to"], "cost": x["cost"]} for k, x in paths.items()},
                    "units": {u: n for u, n in player.units.items() if n}} if paths else None,
        "raise": raise_offer(game, p, "build", not turn.built),
        "science": {"cost": science_e["cost"], "value": science_e["value"], "science": science(game, p)}
        if science_e else None,
    }


def step_text(game: Game, p: int, step: dict) -> str:
    kind, value = next(iter(step.items()))
    faction = game.faction_of(p)
    if kind == "build":
        return f"built {faction.buildings[value].name}"
    if kind == "hire":
        return "hired " + ", ".join(f"{faction.units[u].name} ×{n}" for u, n in value.items())
    if kind == "upgrade":
        return "upgraded " + ", ".join(f"{k} ×{n}" for k, n in value.items())
    if kind == "raise":
        return "raised " + ", ".join(f"{a} +{x}" for a, x in value.items())
    return "bought science"


def build_text(game: Game, p: int, turn: BuildTurn) -> str:
    data = build_data(game, p, turn)
    faction = game.faction_of(p)
    lines = [f"Build & hire (level {data['level']}). Stock: {fmt_res(data['resources'])}."]
    if turn.steps:
        lines.append("Done in this action so far: " + "; ".join(step_text(game, p, s) for s in turn.steps) + ".")
    if turn.rejected:
        lines.append(f"NOT PERFORMED: step {json.dumps(turn.rejected['step'], ensure_ascii=False)} — "
                     f"{turn.rejected['error']}. The steps before it were performed; the rest of that answer was dropped.")
    if turn.built:
        lines.append(f"The building of this action is already built ({faction.buildings[turn.built].name}).")
    elif data["options"]:
        lines.append("You may build one building:")
        for bid, o in data["options"].items():
            lines.append(f"  {bid}: {o['name']} (level {o['level']}, {o['vp']} VP, cost: {fmt_res(o['cost'])}) — {o['text']}")
    else:
        lines.append("Nothing can be built right now (resources, level or required buildings).")
    if data["foundation"]:
        f = data["foundation"]
        lines.append(f"Region {f['region']} has a foundation (current level: {f['level']}): a higher-level building "
                     "is placed on it automatically and scores VP equal to its level.")
    if data["hire"]:
        lines.append("Hiring now (per unit): " + "; ".join(f"{u} ({h['name']}) — {fmt_res(h['cost'])}"
                                                           for u, h in data["hire"].items()))
    if data["upgrade"]:
        names = faction.units
        lines.append("Upgrades (not hiring, no hiring building needed): " + "; ".join(
            f"{k}: {names[x['from']].name} → {names[x['to']].name} for {fmt_res(x['cost'])} each"
            for k, x in data["upgrade"]["paths"].items()))
    if data["raise"]:
        lines.append(raise_text(game, p, data["raise"]))
    if data["science"]:
        s = data["science"]
        lines.append(f"{source_name(game, p, 'buy_science_on_build')}: once per build action you may pay "
                     f"{fmt_res(s['cost'])} for {s['value']} science (now {s['science']}).")
    lines.append("Steps take effect immediately and in any order: what one step gives (rage, science, resources, "
                 "a new hiring building) is available to the next.")
    return "\n".join(lines)


def check_steps(game: Game, p: int, turn: BuildTurn, answer: Any) -> str | None:
    """Ответ — список шагов. Форма проверяется у всех, по существу — только первый: остальные
    зависят от того, что дадут предыдущие, и проверяются при применении."""
    if not isinstance(answer, list):
        return 'Expected a list of steps, e.g. [{"hire": {"brute": 2}}, {"build": "workshop"}, "done"]'
    for i, step in enumerate(answer, 1):
        if step == DONE:
            if i != len(answer):
                return f'"{DONE}" must be the last step'
            continue
        error = step_shape(step)
        if error:
            return f"Step {i}: {error}"
    if answer and answer[0] != DONE:
        error = check_step(game, p, turn, answer[0])
        if error:
            return f"Step 1 ({json.dumps(answer[0], ensure_ascii=False)}): {error}"
    return None


def step_shape(step: Any) -> str | None:
    if not isinstance(step, dict) or len(step) != 1 or next(iter(step)) not in STEPS:
        return (f'a step is "{DONE}" or an object with exactly one key: ' + ", ".join(STEPS))
    kind, value = next(iter(step.items()))
    if kind == "build":
        return None if isinstance(value, str) else "build must be a building id"
    if kind == "science":
        return None if value is True else "science must be true"
    if not isinstance(value, dict) or not value:
        return f'{kind} must be a non-empty object {{"<key>": count}}'
    for k, n in value.items():
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            return f"{kind}: the number for {k} must be a positive integer"
    return None


def check_step(game: Game, p: int, turn: BuildTurn, step: Any) -> str | None:
    """Можно ли выполнить шаг прямо сейчас."""
    error = step_shape(step)
    if error:
        return error
    kind, value = next(iter(step.items()))
    player = game.state.players[p]
    if kind == "build":
        if turn.built:
            return f"one building per action, and {game.faction_of(p).buildings[turn.built].name} is already built"
        level = level_of(game, p, turn)
        if value not in buildable(game, p, level):
            return (f"cannot build {value!r}: unknown, already built, level above {level}, "
                    f"missing required building or not enough resources (have {fmt_res(holdings(game, p))})")
        return None
    if kind == "hire":
        allowed = hireable_units(game, p)
        for unit in value:
            if unit not in allowed:
                return f"cannot hire {unit!r}: its hiring building is not built"
        total = hire_price(game, p, value)
        if not can_pay(game, p, total):
            return f"not enough resources: need {fmt_res(total)}, have {fmt_res(holdings(game, p))}"
        return None
    if kind == "upgrade":
        paths = upgrade_paths(game, p)
        return check_upgrade(game, p, value, paths) if paths else "your faction cannot upgrade units"
    if kind == "raise":
        offer = raise_offer(game, p, "build", not turn.built)
        if offer is None:
            return "nothing can be raised now (no Дом вождей, no rage or nothing to raise)"
        return check_raise(value, offer["options"], offer["max"])
    if science_offer(game, p, turn) is None:
        return ("science was already bought in this action" if turn.science_bought
                else "cannot buy science now (no Library, not enough resources or the science track is full)")
    return None


def apply_step(game: Game, p: int, turn: BuildTurn, step: dict) -> None:
    kind, value = next(iter(step.items()))
    if kind == "build":
        build_building(game, p, value)
        turn.built = value
    elif kind == "hire":
        hire_units(game, p, value)
    elif kind == "upgrade":
        apply_upgrade(game, p, value)
    elif kind == "raise":
        apply_raise(game, p, "build", value)
    else:
        buy_science(game, p)
        turn.science_bought = True
    turn.steps.append(step)


def build_building(game: Game, p: int, bid: str) -> None:
    player = game.state.players[p]
    building = game.faction_of(p).buildings[bid]
    pay(game, p, building_cost(game, p, bid))
    player.built.append(bid)
    game.log(f"{player.name} builds {building.name} (level {building.level})")
    slot = foundation_slot(game, p)
    if slot and building.level > player.foundations.get(slot, 0):
        player.foundations[slot] = building.level
        game.log(f"{player.name} places the building on the foundation in {slot} ({building.level} VP)")
        for e in effects_of(game, p, "on_foundation_build"):
            gain(game, p, e["gain"], f"{source_name(game, p, 'on_foundation_build')}: building on a foundation")
    on_built(game, p, bid)


def hire_units(game: Game, p: int, hire: dict[str, int]) -> None:
    player = game.state.players[p]
    faction = game.faction_of(p)
    pay(game, p, hire_price(game, p, hire))
    for unit, n in hire.items():
        player.units[unit] = player.units.get(unit, 0) + n
    game.log(f"{player.name} hires: " + ", ".join(f"{faction.units[u].name} ×{n}" for u, n in hire.items()))
    on_hired(game, p, hire)


def on_built(game: Game, p: int, bid: str) -> None:
    """Разовые эффекты в момент постройки (Генератор, Сборщики) и ноты наг за каждое здание."""
    building = game.faction_of(p).buildings[bid]
    units = game.state.players[p].units
    for e in building.effects:
        if e["type"] == "on_built_science_per_unit":
            gain_science(game, p, units.get(e["unit"], 0) * e["value"], building.name)
        elif e["type"] == "on_built_gain":
            gain_science(game, p, e.get("science", 0), building.name)
            gain(game, p, e.get("resources", {}), building.name)
    for e in effects_of(game, p, "notes"):
        circle_notes(game, p, e["per_building"])


def on_hired(game: Game, p: int, hire: dict[str, int]) -> None:
    """Эффекты найма: ярость за громил (Арена), наука за автоматонов (Цех)."""
    for e in effects_of(game, p, "on_hire"):
        if hire.get(e["unit"]):
            gain(game, p, {k: v * hire[e["unit"]] for k, v in e["gain"].items()}, source_name(game, p, "on_hire"))
    for e in effects_of(game, p, "science_per_hire"):
        if hire.get(e["unit"]):
            gain_science(game, p, hire[e["unit"]] * e["value"], source_name(game, p, "science_per_hire"))


def buy_science(game: Game, p: int) -> None:
    """Библиотека гномов: обменять ресурсы на науку (проверено в check_step)."""
    e = effects_of(game, p, "buy_science_on_build")[0]
    source = source_name(game, p, "buy_science_on_build")
    pay(game, p, e["cost"])
    game.log(f"{game.state.players[p].name} pays {fmt_res(e['cost'])} for science ({source})")
    gain_science(game, p, e["value"], source)


def apply_upgrade(game: Game, p: int, answer: dict[str, int]) -> None:
    """Орки за ярость модифицируют разведчиков в громил, громил во взрывателей (это не наём).
    Пути выполняются в порядке upgrade_paths, так что модифицированных можно модифицировать снова."""
    player = game.state.players[p]
    faction = game.faction_of(p)
    for k, x in upgrade_paths(game, p).items():
        n = answer.get(k, 0)
        if not n:
            continue
        pay(game, p, {r: v * n for r, v in x["cost"].items()})
        player.units[x["from"]] -= n
        player.units[x["to"]] = player.units.get(x["to"], 0) + n
        game.log(f"{player.name} upgrades {faction.units[x['from']].name} ×{n} to {faction.units[x['to']].name}")


def check_upgrade(game: Game, p: int, answer: Any, paths: dict[str, dict]) -> str | None:
    if not isinstance(answer, dict):
        return 'Expected an object {"<from>><to>": count}'
    units = dict(game.state.players[p].units)
    spent: dict[str, int] = {}
    for k in answer:
        if k not in paths:
            return f"Unknown upgrade {k!r}; allowed: {', '.join(paths)}"
    for k, x in paths.items():
        n = answer.get(k, 0)
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return f"Number for {k} must be a non-negative integer"
        if n > units.get(x["from"], 0):
            return f"{k}: not enough units to upgrade"
        units[x["from"]] -= n
        units[x["to"]] = units.get(x["to"], 0) + n
        spent = sum_costs(spent, {r: v * n for r, v in x["cost"].items()})
    if not all(stock(game, p, r) >= v for r, v in spent.items()):
        return f"Not enough to pay {fmt_res(spent)}"
    return None


# --- Башня иллюзий ----------------------------------------------------------------------

def reservable(game: Game, p: int) -> list[str]:
    """Объекты, которые игрок может забронировать: не забронированные и не вычеркнутые у него."""
    st = game.state
    result = []
    for name in st.available:
        region = game.map.regions[name]
        if name in st.reserved:
            continue
        if region.object == "fortress" and name in st.fortress_owner:
            continue
        if region.object == "artifact" and name in st.artifact_holder:
            continue
        result.append(name)
    return result


def illusion_tower(game: Game, p: int) -> None:
    cost = effects_of(game, p, "reserve_object")[0]["cost"]
    player = game.state.players[p]
    if not can_pay(game, p, cost):
        return
    options = reservable(game, p)
    if not options:
        return
    described = {r: OBJECT_NAMES[game.map.regions[r].object] for r in options}
    answer = game.ask(
        p, "reserve_object",
        f"Башня иллюзий: you may pay {fmt_res(cost)} to make the object in a region available only to you. "
        "You may decline. Options: "
        + ", ".join(f"{r} ({o})" for r, o in described.items()),
        {"cost": cost, "options": described},
        lambda a: None if a is None or a in options else f"Region {a!r} cannot be chosen",
        None,
    )
    if answer is None:
        return
    pay(game, p, cost)
    game.state.reserved[answer] = p
    game.log(f"{player.name} reserves the {described[answer]} in {answer} with Башня иллюзий")
