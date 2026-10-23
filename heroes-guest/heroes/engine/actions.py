"""Действия добычи и действие строительства и найма (включая Башню иллюзий и особые здания фракций)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .economy import can_pay, exchange_for_units, fmt_res, gain, gain_science, holdings, pay, stock, sum_costs
from .effects import building_cost, effects_of, hire_cost, hireable_units, source_name
from .special import circle_notes, curse_on_build, raise_actions
from .state import OBJECT_NAMES, RESOURCE_NAMES

if TYPE_CHECKING:
    from .game import Game


def gather(game: Game, resource: str, level: int) -> None:
    for p in game.state.turn_order():
        bonus = sum(e["value"] for e in effects_of(game, p, "bonus_on_gather") if e["resource"] == resource)
        gain(game, p, {resource: level + bonus}, f"gather {RESOURCE_NAMES[resource]}")


# --- Строительство и наём -------------------------------------------------------------

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


def hire_total(game: Game, p: int, hire: dict[str, int], extra_built: tuple[str, ...]) -> dict[str, int]:
    return sum_costs(*[{k: v * n for k, v in hire_cost(game, p, u, extra_built).items()} for u, n in hire.items()])


def can_build_or_hire(game: Game, p: int, level: int) -> bool:
    """Есть ли что построить или кого нанять на свои ресурсы — иначе вопрос не задаётся."""
    if buildable(game, p, level):
        return True
    return any(can_pay(game, p, hire_cost(game, p, u)) for u in hireable_units(game, p))


def build_and_recruit(game: Game, level: int) -> None:
    for p in game.state.turn_order():
        exchange_for_units(game, p)                      # наги: обязательный обмен ресурсов на краков
        own_level = level + raise_actions(game, p, "build")   # орки: Дом вождей
        buy_science(game, p)                             # гномы: Библиотека
        answer = game.ask(
            p, "build_recruit", build_text(game, p, own_level), build_data(game, p, own_level),
            lambda a, p=p, lvl=own_level: check_build(game, p, lvl, a),
            {"build": None, "hire": {}},
            forced=not can_build_or_hire(game, p, own_level),
        )
        apply_build(game, p, answer)
        upgrade_units(game, p)                           # орки: модификация войск за ярость
        if effects_of(game, p, "reserve_object"):
            illusion_tower(game, p)
        curse_on_build(game, p)                          # нежить: Чумной столб


def build_data(game: Game, p: int, level: int) -> dict:
    player = game.state.players[p]
    faction = game.faction_of(p)
    slot = foundation_slot(game, p)
    return {
        "level": level,
        "resources": holdings(game, p),   # склад плюс ярость, наука, краки — чем можно платить
        "options": {
            bid: {"name": faction.buildings[bid].name, "level": faction.buildings[bid].level,
                  "cost": building_cost(game, p, bid), "vp": faction.buildings[bid].vp,
                  "text": faction.buildings[bid].text}
            for bid in buildable(game, p, level)
        },
        "foundation": {"region": slot, "level": player.foundations.get(slot, 0)} if slot else None,
        "hire": {u: {"name": faction.units[u].name, "cost": hire_cost(game, p, u)} for u in hireable_units(game, p)},
    }


def build_text(game: Game, p: int, level: int) -> str:
    data = build_data(game, p, level)
    lines = [f"Build & hire (level {level}). Stock: {fmt_res(data['resources'])}."]
    if data["options"]:
        lines.append("You may build one building:")
        for bid, o in data["options"].items():
            lines.append(f"  {bid}: {o['name']} (level {o['level']}, {o['vp']} VP, cost: {fmt_res(o['cost'])}) — {o['text']}")
    else:
        lines.append("Nothing can be built.")
    if data["foundation"]:
        f = data["foundation"]
        lines.append(f"Region {f['region']} has a foundation (current level: {f['level']}): a higher-level building "
                     "is placed on it automatically and scores VP equal to its level.")
    if data["hire"]:
        lines.append("Hiring: " + "; ".join(f"{u} ({h['name']}) — {fmt_res(h['cost'])}" for u, h in data["hire"].items()))
    lines.append("The building (at most one) is built first; hiring comes after it, any number of units while resources last.")
    return "\n".join(lines)


def check_build(game: Game, p: int, level: int, answer: Any) -> str | None:
    if not isinstance(answer, dict) or set(answer) - {"build", "hire"}:
        return 'Expected an object {"build": ..., "hire": {...}}'
    bid, hire = answer.get("build"), answer.get("hire") or {}
    player = game.state.players[p]
    if bid is not None and bid not in buildable(game, p, level):
        return f"Cannot build {bid!r}: unknown, already built, level too high, missing prerequisite or unaffordable"
    extra = (bid,) if bid else ()
    if not isinstance(hire, dict):
        return "hire must be an object {\"unit\": count}"
    allowed = hireable_units(game, p, extra)
    for unit, n in hire.items():
        if unit not in allowed:
            return f"Cannot hire {unit!r}: its hiring building is not built"
        if not isinstance(n, int) or n < 0:
            return f"Number of {unit} must be a non-negative integer"
    total = sum_costs(building_cost(game, p, bid) if bid else {}, hire_total(game, p, hire, extra))
    if not can_pay(game, p, total):
        return f"Not enough resources: need {fmt_res(total)}, have {fmt_res(player.resources)}"
    return None


def apply_build(game: Game, p: int, answer: dict) -> None:
    player = game.state.players[p]
    faction = game.faction_of(p)
    bid = answer.get("build")
    if bid:
        building = faction.buildings[bid]
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
    hire = {u: n for u, n in (answer.get("hire") or {}).items() if n}
    if hire:
        pay(game, p, hire_total(game, p, hire, ()))
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


# --- Особые возможности строительства ----------------------------------------------------

def buy_science(game: Game, p: int) -> None:
    """Библиотека гномов: за действие строительства можно один раз купить науку."""
    player = game.state.players[p]
    for e in effects_of(game, p, "buy_science_on_build"):
        track = effects_of(game, p, "science_track")
        if not can_pay(game, p, e["cost"]) or (track and player.tokens.get("science", 0) >= track[0]["max"]):
            continue
        source = source_name(game, p, "buy_science_on_build")
        if game.ask(
            p, "buy_science",
            f"{source}: pay {fmt_res(e['cost'])} to gain {e['value']} science before building? "
            f"Your science: {player.tokens.get('science', 0)}.",
            {"cost": e["cost"], "value": e["value"], "science": player.tokens.get("science", 0)},
            lambda a: None if isinstance(a, bool) else "The answer must be true or false",
            False,
        ):
            pay(game, p, e["cost"])
            game.log(f"{player.name} pays {fmt_res(e['cost'])} for science ({source})")
            gain_science(game, p, e["value"], source)


def upgrade_units(game: Game, p: int) -> None:
    """Орки за ярость модифицируют разведчиков в громил, громил во взрывателей (это не наём)."""
    player = game.state.players[p]
    faction = game.faction_of(p)
    for e in effects_of(game, p, "upgrade_units_on_build"):
        paths = {f"{x['from']}>{x['to']}": x for x in e["paths"]}
        if not any(player.units.get(x["from"], 0) and can_pay(game, p, x["cost"]) for x in paths.values()):
            continue
        rows = [f"  {k}: {faction.units[x['from']].name} → {faction.units[x['to']].name} for {fmt_res(x['cost'])} each"
                for k, x in paths.items()]
        answer = game.ask(
            p, "upgrade_units",
            "Upgrade units (applied in the listed order, so upgraded units can be upgraded again). Units: "
            + ", ".join(f"{faction.units[u].name} ×{n}" for u, n in player.units.items() if n)
            + f". Stock: {fmt_res(dict(player.tokens))}.\n" + "\n".join(rows) + "\nYou may upgrade nobody.",
            {"paths": {k: {"from": x["from"], "to": x["to"], "cost": x["cost"]} for k, x in paths.items()},
             "units": {u: n for u, n in player.units.items() if n}, "stock": dict(player.tokens)},
            lambda a: check_upgrade(game, p, a, paths),
            {},
        )
        for k, x in paths.items():
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
