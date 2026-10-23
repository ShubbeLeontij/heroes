"""Ресурсы: получение (с выбором «любого» ресурса и заменой дерева), оплата, отметки ПО.

Кроме дерева, железа и золота платить можно уникальными ресурсами фракций: яростью орков
(PlayerState.tokens) и краками наг (это войска: PlayerState.units).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .data import RESOURCES, TOKENS
from .decisions import check_resource_split
from .effects import effects_of, has_effect, source_name
from .state import RESOURCE_NAMES, RESOURCE_VP

if TYPE_CHECKING:
    from .game import Game


def fmt_res(resources: dict[str, int]) -> str:
    parts = [f"{RESOURCE_NAMES.get(k, k)} {v}" for k, v in resources.items() if v]
    return ", ".join(parts) or "nothing"


def sum_costs(*costs: dict[str, int]) -> dict[str, int]:
    total: dict[str, int] = {}
    for cost in costs:
        for k, v in cost.items():
            total[k] = total.get(k, 0) + v
    return total


def can_afford(resources: dict[str, int], cost: dict[str, int]) -> bool:
    return all(resources.get(k, 0) >= v for k, v in cost.items())


def stock(game: Game, p: int, key: str) -> int:
    """Сколько у игрока того, чем платят: ресурса склада, уникального ресурса или войск-ресурса."""
    player = game.state.players[p]
    if key in RESOURCES:
        return player.resources[key]
    if key in TOKENS:
        return player.tokens.get(key, 0)
    return player.units.get(key, 0)


def holdings(game: Game, p: int) -> dict[str, int]:
    """Склад вместе с уникальными ресурсами и войсками-ресурсом — для проверки цен."""
    player = game.state.players[p]
    units = {u: player.units.get(u, 0) for u in game.faction_of(p).resource_units()}
    return {**player.resources, **player.tokens, **units}


def can_pay(game: Game, p: int, cost: dict[str, int]) -> bool:
    return all(stock(game, p, k) >= v for k, v in cost.items())


def pay(game: Game, p: int, cost: dict[str, int]) -> None:
    player = game.state.players[p]
    if not can_pay(game, p, cost):
        raise ValueError(f"Player {p} cannot afford {cost}")
    for k, v in cost.items():
        if k in RESOURCES:
            player.resources[k] -= v
        elif k in TOKENS:
            player.tokens[k] -= v
        else:
            player.units[k] -= v


def ask_split(game: Game, p: int, amount: int, reason: str, default_resource: str = "wood") -> dict[str, int]:
    """Спросить, как разложить amount ресурсов по дереву/железу/золоту."""
    answer = game.ask(
        p, "distribute_resources",
        f"{reason}. Split {amount} resource(s) between wood, iron and gold.",
        {"amount": amount, "allowed": list(RESOURCES), "reason": reason},
        lambda a: check_resource_split(a, amount),
        {default_resource: amount},
    )
    return {k: v for k, v in answer.items() if v}


def gain(game: Game, p: int, resources: dict[str, int], reason: str) -> None:
    """Получить ресурсы. «any» раскладывается выбором игрока; дерево может заменяться (Таскар);
    наука идёт на шкалу гномов, ярость — к уникальным ресурсам орков."""
    fixed = {k: v for k, v in resources.items() if k != "any" and v}
    if fixed.get("science"):
        gain_science(game, p, fixed.pop("science"), reason)
    wood = fixed.pop("wood", 0)
    if wood and has_effect(game, p, "wood_substitution"):
        fixed = sum_costs(fixed, ask_split(game, p, wood, f"{reason}: instead of {wood} wood you may take iron and/or gold"))
    elif wood:
        fixed["wood"] = wood
    if resources.get("any"):
        fixed = sum_costs(fixed, ask_split(game, p, resources["any"], f"{reason}: resources of your choice"))
    fixed = exchange_on_gain(game, p, fixed)
    if not fixed:
        return
    player = game.state.players[p]
    for k, v in fixed.items():
        if k in TOKENS:
            player.tokens[k] = player.tokens.get(k, 0) + v
        else:
            player.resources[k] += v
    game.log(f"{player.name} gains {fmt_res(fixed)} ({reason})")
    mark_resource_vp(game, p)


def exchange_on_gain(game: Game, p: int, fixed: dict[str, int]) -> dict[str, int]:
    """Часовня нежити: из полученных 3+ железа можно сразу обменять 3 на палача (один раз за получение)."""
    for e in effects_of(game, p, "exchange_on_gain"):
        if fixed.get(e["resource"], 0) < e["min_gain"]:
            continue
        unit = game.faction_of(p).units[e["unit"]]
        source = source_name(game, p, "exchange_on_gain")
        if not game.ask(
            p, "exchange_on_gain",
            f"{source}: you are gaining {fixed[e['resource']]} {RESOURCE_NAMES[e['resource']]}. Exchange "
            f"{e['pay']} of it for {e['count']} {unit.name}? The rest goes to your stock.",
            {"resource": e["resource"], "gained": fixed[e["resource"]], "pay": e["pay"], "unit": e["unit"],
             "count": e["count"]},
            lambda a: None if isinstance(a, bool) else "The answer must be true or false",
            False,
        ):
            continue
        fixed = {**fixed, e["resource"]: fixed[e["resource"]] - e["pay"]}
        units = game.state.players[p].units
        units[e["unit"]] = units.get(e["unit"], 0) + e["count"]
        game.log(f"{game.state.players[p].name} exchanges {e['pay']} {RESOURCE_NAMES[e['resource']]} "
                 f"for {unit.name} ×{e['count']} ({source})")
    return {k: v for k, v in fixed.items() if v}


def gain_science(game: Game, p: int, amount: int, reason: str) -> None:
    """Наука гномов: отметки на шкале; сверх её конца наука пропадает."""
    track = effects_of(game, p, "science_track")
    if not track or amount <= 0:
        return
    player = game.state.players[p]
    current = player.tokens.get("science", 0)
    gained = min(amount, track[0]["max"] - current)
    if gained <= 0:
        return
    player.tokens["science"] = current + gained
    game.log(f"{player.name} gains {gained} science ({reason}), now {player.tokens['science']}")


def exchange_for_units(game: Game, p: int) -> None:
    """Наги в начале строительства обязаны обменять все возможные ресурсы на краков; излишки остаются."""
    player = game.state.players[p]
    for e in effects_of(game, p, "krak_exchange"):
        rates = dict(e["rates"])
        for mod in effects_of(game, p, "krak_exchange_rates"):  # Кормушка
            rates.update(mod["rates"])
        spent: dict[str, int] = {}
        for res, rate in rates.items():
            n = player.resources[res] // rate
            if n:
                player.resources[res] -= n * rate
                spent[res] = n * rate
        got = sum(spent[res] // rates[res] for res in spent)
        if got:
            player.units[e["unit"]] = player.units.get(e["unit"], 0) + got
            game.log(f"{player.name} exchanges {fmt_res(spent)} for {game.faction_of(p).units[e['unit']].name} ×{got}")


def token_vp(game: Game, p: int) -> dict[str, dict[int, int]]:
    """Пороги ПО за накопление уникальных ресурсов (ярость орков: 12/18/24)."""
    return {e["resource"]: {int(t): vp for t, vp in e["vp_thresholds"].items()}
            for e in effects_of(game, p, "faction_resource")}


def mark_resource_vp(game: Game, p: int) -> None:
    if has_effect(game, p, "no_resource_vp"):  # наги: за ресурсы ПО не начисляются
        return
    player = game.state.players[p]
    for res in RESOURCES:
        for threshold, vp in RESOURCE_VP:
            if player.resources[res] >= threshold and threshold not in player.resource_marks[res]:
                player.resource_marks[res].append(threshold)
                game.log(f"{player.name} reached {threshold} {RESOURCE_NAMES[res]} — {vp} VP circled")
    for res, thresholds in token_vp(game, p).items():
        marks = player.resource_marks.setdefault(res, [])
        for threshold, vp in sorted(thresholds.items()):
            if player.tokens.get(res, 0) >= threshold and threshold not in marks:
                marks.append(threshold)
                game.log(f"{player.name} reached {threshold} {RESOURCE_NAMES[res]} — {vp} VP circled")
