"""Особые механики фракций, которые срабатывают в разных действиях: проклятия нежити,
ноты и призыв гидры наг, Дом вождей орков (повышение уровня действий за ярость)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .data import RESOURCES
from .decisions import check_choice
from .economy import can_pay, fmt_res, gain, pay, stock
from .effects import effects_of, hydra_unit, source_name, unit_strength
from .state import ACTION_NAMES, RESOURCE_NAMES

if TYPE_CHECKING:
    from .game import Game


# --- Проклятия нежити ---------------------------------------------------------------------

def curse(game: Game, p: int, region: str, reason: str) -> None:
    """Проклясть регион (один раз за игру): соперники, стоящие в нём, сразу теряют самую слабую единицу."""
    st = game.state
    st.cursed[region] = p
    game.log(f"{st.players[p].name} curses {region} ({reason})")
    for q, other in enumerate(st.players):
        if q != p and other.region == region:
            curse_penalty(game, q, region)


def curse_penalty(game: Game, q: int, region: str) -> None:
    """Соперник проклявшего region посетил его или стоял в нём в момент проклятия."""
    curser = game.state.cursed[region]
    for e in effects_of(game, curser, "curse_penalty"):
        for _ in range(e["lose_weakest_units"]):
            lose_weakest(game, q, f"the curse of {region}")


def lose_weakest(game: Game, p: int, reason: str) -> None:
    """Потерять одну самую слабую единицу; при равной силе — ту, что выше на планшете. Гидра не гибнет."""
    player = game.state.players[p]
    order = list(game.faction_of(p).units)
    candidates = [u for u in order if player.units.get(u, 0) > 0 and u != hydra_unit(game, p)]
    if not candidates:
        return
    unit = min(candidates, key=lambda u: (unit_strength(game, p, u), order.index(u)))
    player.units[unit] -= 1
    game.log(f"{player.name} loses {game.faction_of(p).units[unit].name} ×1 ({reason})")


def curse_choice(game: Game, p: int, options: list[str], text: str, cost: dict[str, int], optional: bool) -> str | None:
    answer = game.ask(
        p, "curse_region", text,
        {"options": options, "cost": cost, "optional": optional},
        lambda a: None if a is None and optional else check_choice(a, options),
        None if optional else options[0],
        forced=not optional and len(options) == 1,
    )
    return answer


def curse_on_build(game: Game, p: int) -> None:
    """Чумной столб: в конце строительства можно заплатить, чтобы проклясть свой текущий или соседний регион."""
    st = game.state
    for e in effects_of(game, p, "curse_on_build"):
        region = st.players[p].region
        if not can_pay(game, p, e["cost"]):
            return
        options = [r for r in [region, *sorted(game.map.adjacent[region])] if r in st.available and r not in st.cursed]
        if not options:
            return
        source = source_name(game, p, "curse_on_build")
        answer = curse_choice(
            game, p, options,
            f"{source}: you may pay {fmt_res(e['cost'])} to curse your current region or an adjacent one. "
            "Opponents who visit a cursed region, or are in it now, lose their weakest unit; each cursed region "
            "gives you 1 VP at game end. Options: " + ", ".join(options) + ". You may decline.",
            e["cost"], optional=True,
        )
        if answer is not None:
            pay(game, p, e["cost"])
            curse(game, p, answer, source)


def curse_any(game: Game, p: int, count: int, reason: str) -> None:
    """Прокляните count любых регионов (разовый эффект артефактов нежити)."""
    st = game.state
    for i in range(count):
        options = [r for r in st.available if r not in st.cursed]
        if not options:
            return
        answer = curse_choice(
            game, p, options,
            f"{reason}: curse any region ({i + 1} of {count}). Options: " + ", ".join(options) + ".",
            {}, optional=False,
        )
        curse(game, p, answer, reason)


# --- Ноты и гидра наг -----------------------------------------------------------------------

def circle_notes(game: Game, p: int, count: int) -> None:
    """Обвести ноты. Закрыв начатую пару, нага сразу призывает гидру; начав новую пару — откладывает призыв."""
    player = game.state.players[p]
    for _ in range(count):
        half = [i for i, n in enumerate(player.notes) if n == 1]
        empty = [i for i, n in enumerate(player.notes) if n == 0]
        options = (["open"] if empty else []) + (["close"] if half else [])
        if not options:
            return
        answer = game.ask(
            p, "circle_note",
            f"Circle a note on your hydra track (pairs: {player.notes}). "
            "close — complete a started pair and summon Гидра now (a new one replaces the old); "
            "open — start a new pair and summon later. Options: " + ", ".join(options) + ".",
            {"options": options, "notes": list(player.notes)},
            lambda a, o=options: check_choice(a, o),
            options[0],
            forced=len(options) == 1,
        )
        if answer == "open":
            player.notes[empty[0]] = 1
            game.log(f"{player.name} circles a note and starts a new pair")
        else:
            player.notes[half[0]] = 2
            game.log(f"{player.name} circles a note and completes a pair")
            summon_hydra(game, p)


def summon_hydra(game: Game, p: int) -> None:
    st = game.state
    player = st.players[p]
    unit = hydra_unit(game, p)
    e = next(x for x in effects_of(game, p, "hydra"))
    options = sorted(r for r in st.available if game.terrain(r) == e["summon_terrain"])
    region = game.ask(
        p, "summon_hydra",
        "Summon Гидра into a lake region. Options: " + ", ".join(options) + ".",
        {"options": options},
        lambda a: check_choice(a, options),
        options[0],
        forced=len(options) == 1,
    )
    name = game.faction_of(p).units[unit].name
    if player.hydra:
        game.log(f"{player.name}'s {name} in {player.hydra} dies: a new one is summoned")
    player.units[unit] = 1
    player.hydra = region
    player.hydra_summons += 1
    game.log(f"{player.name} summons {name} into {region}")


def hydra_died(game: Game, p: int) -> None:
    """Гидра погибла в сражении: её отметка стирается с карты."""
    player = game.state.players[p]
    unit = hydra_unit(game, p)
    if unit and player.hydra and not player.units.get(unit):
        game.log(f"{player.name}'s {game.faction_of(p).units[unit].name} dies")
        player.hydra = None


# --- Дом вождей орков ------------------------------------------------------------------------

def raise_actions(game: Game, p: int, current: str) -> int:
    """Во время строительства или перемещения орк может платить яростью, повышая уровень текущего действия
    и уже выполненных в этом раунде (прошедшая добыча сразу даёт ещё столько же). Возвращает прибавку
    к уровню текущего действия."""
    st = game.state
    player = st.players[p]
    for e in effects_of(game, p, "pay_to_raise_action"):
        if current not in e["when"]:
            continue
        key, price = next(iter(e["cost_per_level"].items()))
        budget = stock(game, p, key) // price
        if budget <= 0:
            return 0
        done = [a for a in st.action_order[: st.phase - 1] if a in RESOURCES]
        levels = {a: st.action_order.index(a) + 1 + player.raised.get(a, 0) for a in [current, *done]}
        source = source_name(game, p, "pay_to_raise_action")
        answer = game.ask(
            p, "raise_action",
            f"{source}: pay {price} {RESOURCE_NAMES[key]} per level to raise this action ({ACTION_NAMES[current]}) "
            "and/or gathering actions already performed this round (they give the extra resources now). "
            f"You have {stock(game, p, key)} {RESOURCE_NAMES[key]}, enough for {budget} level(s). Current levels: "
            + ", ".join(f"{a} ({ACTION_NAMES[a]}) {lvl}" for a, lvl in levels.items()) + ".",
            {"current": current, "options": levels, "max": budget, "cost_per_level": e["cost_per_level"]},
            lambda a: check_raise(a, levels, budget),
            {},
        )
        bonus = 0
        for action, x in answer.items():
            if not x:
                continue
            pay(game, p, {key: price * x})
            player.raised[action] = player.raised.get(action, 0) + x
            game.log(f"{player.name} pays {fmt_res({key: price * x})} to raise {ACTION_NAMES[action]} by {x} ({source})")
            if action == current:
                bonus += x
            else:
                gain(game, p, {action: x}, f"{ACTION_NAMES[action]} raised by {source}")
        return bonus
    return 0


def check_raise(answer: Any, levels: dict[str, int], budget: int) -> str | None:
    if not isinstance(answer, dict):
        return 'Expected an object {"<action>": levels to add}'
    for action, x in answer.items():
        if action not in levels:
            return f"Action {action!r} cannot be raised now; allowed: {', '.join(levels)}"
        if not isinstance(x, int) or isinstance(x, bool) or x < 0:
            return f"Levels for {action} must be a non-negative integer"
    if sum(answer.values()) > budget:
        return f"You can pay for at most {budget} level(s)"
    return None
