"""Действие перемещения: очки перемещения, поиск пути, посещение регионов, перемещение гидры наг."""
from __future__ import annotations

import heapq
from typing import TYPE_CHECKING

from . import combat
from .decisions import Decision, check_choice
from .economy import fmt_res, gain, pay, stock
from .effects import effects_of, hydra_unit, source_name, step_cost
from .special import curse_penalty, raise_actions
from .state import OBJECT_NAMES, RESOURCE_NAMES, TERRAIN_NAMES

if TYPE_CHECKING:
    from .game import Game


def portal_usable(game: Game, p: int, region: str) -> bool:
    """Портал работает для игрока, если он не забронирован соперником (Башня иллюзий)."""
    return game.map.regions[region].object == "portal" and game.state.reserved.get(region, p) == p


def neighbours(game: Game, p: int, region: str) -> set[str]:
    """Соседи региона для игрока p: по карте и через работающие порталы. Вычеркнутые регионы не существуют."""
    available = set(game.state.available)
    result = set(game.map.adjacent[region]) & available
    if portal_usable(game, p, region):
        result |= {r for r in game.map.portals if r in available and r != region and portal_usable(game, p, r)}
    return result


def reachable(game: Game, p: int, mp: int) -> dict[str, int]:
    """Минимальная стоимость пути до каждого региона, достижимого за mp очков перемещения.

    Шаг в соседний регион стоит столько, сколько стоит местность региона, куда входишь.
    Все работающие порталы соединены друг с другом. Вычеркнутые регионы не существуют.
    """
    start = game.state.players[p].region
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        d, region = heapq.heappop(heap)
        if d > dist[region]:
            continue
        for nb in neighbours(game, p, region):
            nd = d + step_cost(game, p, nb)
            if nd <= mp and nd < dist.get(nb, mp + 1):
                dist[nb] = nd
                heapq.heappush(heap, (nd, nb))
    return dist


def visit(game: Game, p: int, region: str) -> None:
    """Игрок закончил перемещение в регионе: он текущий и посещённый; ресурсы — при первом посещении."""
    player = game.state.players[p]
    player.region = region
    if game.terrain(region) == "mountain" and region not in player.mountains:
        player.mountains.append(region)  # для Шог Ида — местность в момент посещения
    if region in player.visited:
        return
    player.visited.append(region)
    resources = game.map.regions[region].resources
    if resources:
        gain(game, p, resources, f"first visit to {region}")
    for e in effects_of(game, p, "on_new_region"):
        gain(game, p, e["gain"], f"new region visited: {region}")


def move_points(game: Game, p: int, level: int) -> int:
    """ОП этого раунда: уровень действия, Дом вождей, Обсерватория, купленные в Катафалке."""
    mp = level + raise_actions(game, p, "move")
    mp += sum(e["value"] for e in effects_of(game, p, "extra_move_points"))
    for e in effects_of(game, p, "buy_move_points"):
        key, price = next(iter(e["cost"].items()))
        most = stock(game, p, key) // price
        if most <= 0:
            continue
        source = source_name(game, p, "buy_move_points")
        n = game.ask(
            p, "buy_move_points",
            f"{source}: you have {mp} MP. Buy extra MP for {price} {RESOURCE_NAMES[key]} each (up to {most})? "
            "Answer how many, 0 — none.",
            {"mp": mp, "cost": e["cost"], "max": most},
            lambda a, m=most: None if isinstance(a, int) and not isinstance(a, bool) and 0 <= a <= m
            else f"Expected an integer from 0 to {m}",
            0,
        )
        if n:
            pay(game, p, {key: price * n})
            game.log(f"{game.state.players[p].name} buys {n} MP for {fmt_res({key: price * n})} ({source})")
            mp += n
    return mp


def hydra_reach(game: Game, p: int) -> dict[str, int]:
    """Куда может пойти гидра наги: на 1 регион (с Маяком — на 2) любой местности, через порталы, без ОП."""
    start = game.state.players[p].hydra
    steps = max(e.get("move", e.get("value", 1)) for e in [*effects_of(game, p, "hydra"), *effects_of(game, p, "hydra_move")])
    dist = {start: 0}
    frontier = [start]
    for d in range(1, steps + 1):
        frontier = [nb for r in frontier for nb in neighbours(game, p, r) if nb not in dist]
        for nb in frontier:
            dist.setdefault(nb, d)
    return dist


def move_action(game: Game, level: int) -> None:
    st = game.state
    order = st.turn_order()
    mp = {}
    for p in order:
        mp[p] = move_points(game, p, level)
        st.players[p].move_points = mp[p]
    options = {p: reachable(game, p, mp[p]) for p in order}
    decisions = [
        Decision(
            "destination", p, destination_text(game, p, mp[p], options[p]),
            {"mp": mp[p], "options": {r: {"cost": c, "left": mp[p] - c} for r, c in options[p].items()}},
            lambda a, opts=options[p]: check_choice(a, list(opts)),
            st.players[p].region,
            forced=len(options[p]) == 1,  # очков перемещения не хватает ни на один соседний регион
        )
        for p in order
    ]
    hydras = {p: hydra_reach(game, p) for p in order if st.players[p].hydra}
    decisions += [
        Decision(
            "hydra_destination", p,
            f"Your {game.faction_of(p).units[hydra_unit(game, p)].name} is in {st.players[p].hydra} and moves "
            "separately from you (secretly, no MP spent). Options: "
            + ", ".join(f"{r} ({d} step{'s' if d != 1 else ''})" if d else f"{r} (stay)" for r, d in hydras[p].items()),
            {"from": st.players[p].hydra, "options": hydras[p]},
            lambda a, opts=hydras[p]: check_choice(a, list(opts)),
            st.players[p].hydra,
            forced=len(hydras[p]) == 1,
        )
        for p in hydras
    ]
    game.reach = options   # это открыто: регион, ОП и цены местности всех игроков видны всем
    try:
        answers = game.ask_all(decisions)  # тайно и одновременно: ответы раскрываются только здесь
    finally:
        game.reach = {}
    leftover = {}
    moved = []
    # Сначала все узнают, кто куда пошёл, и только потом — ресурсы, проклятия и сражения.
    for p, dest in zip(order, answers):
        leftover[p] = mp[p] - options[p][dest]
        player = st.players[p]
        if dest == player.region:
            game.log(f"{player.name} stays in {dest}")
        else:
            game.log(f"{player.name} moves to {dest} (spent {options[p][dest]} of {mp[p]} MP)")
            player.routes.append([player.region, dest])
            player.region = dest
            moved.append(p)
    for p, dest in zip(hydras, answers[len(order):]):
        player = st.players[p]
        name = game.faction_of(p).units[hydra_unit(game, p)].name
        if dest != player.hydra:
            game.log(f"{player.name}'s {name} moves to {dest}")
            player.hydra = dest
    for p in moved:
        dest = st.players[p].region
        visit(game, p, dest)
        if st.cursed.get(dest, p) != p:
            curse_penalty(game, p, dest)
        for e in effects_of(game, p, "on_moved_gain"):
            gain(game, p, e["gain"], source_name(game, p, "on_moved_gain"))
    for p in order:
        make_lake(game, p, leftover)
    combat.resolve_encounters(game, leftover, set(moved))


def make_lake(game: Game, p: int, leftover: dict[int, int]) -> None:
    """Гейзер наг: в конце перемещения за 1 ОП текущий регион становится озёрным (для всех)."""
    st = game.state
    region = st.players[p].region
    for e in effects_of(game, p, "make_lake"):
        if leftover[p] < e["cost_mp"] or game.terrain(region) == "lake":
            continue
        source = source_name(game, p, "make_lake")
        if game.ask(
            p, "make_lake",
            f"{source}: spend {e['cost_mp']} of your {leftover[p]} unspent MP to turn {region} "
            f"({TERRAIN_NAMES[game.terrain(region)]}) into a lake for everyone? Unspent MP let you skip monsters.",
            {"region": region, "cost_mp": e["cost_mp"], "left": leftover[p]},
            lambda a: None if isinstance(a, bool) else "The answer must be true or false",
            False,
        ):
            leftover[p] -= e["cost_mp"]
            st.terrain[region] = "lake"
            game.log(f"{st.players[p].name} turns {region} into a lake ({source})")


def destination_text(game: Game, p: int, mp: int, options: dict[str, int]) -> str:
    player = game.state.players[p]
    rows = []
    for region, cost in sorted(options.items(), key=lambda kv: (kv[1], kv[0])):
        mark = " (visited)" if region in player.visited else ""
        here = " — stay" if region == player.region else ""
        cursed = " (cursed)" if game.state.cursed.get(region, p) != p else ""
        rows.append(f"  {region}{here}: {TERRAIN_NAMES[game.terrain(region)]}, "
                    f"{OBJECT_NAMES[game.map.regions[region].object]}{mark}{cursed}; cost {cost}, {mp - cost} MP left")
    return (f"Movement: you have {mp} MP and are in {player.region}. The choice is secret. "
            "Unspent MP let you skip monsters in your line.\n"
            "Options:\n" + "\n".join(rows))
