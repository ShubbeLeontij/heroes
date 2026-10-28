"""heroes-tool: мелкие справочные инструменты Claude-игрока. Считают по партии на момент текущего вопроса,
ничего не меняют и не расходуют ходы:

  heroes-tool board [игрок]           полный планшет игрока (правило открытости): здания с ценами и свойствами,
                                      артефакты, цены найма, линия чудовищ, стоимость перемещения
  heroes-tool max_strength [игрок]    сила армии игрока сейчас и сколько можно добавить наймом
  heroes-tool schedule                в каких раундах вы выбираете порядок действий (первый игрок)
  heroes-tool tree [игрок]            дерево строительства: уровни, требования, что открывает, статус
  heroes-tool reach <ОП>              куда можно дойти за столько очков перемещения
  heroes-tool artifacts [игрок]       артефакты: кто владеет, условие, выполнено ли
  heroes-tool fortresses              крепости: сила, награда, владелец, хватит ли вам силы
  heroes-tool monsters [игрок]        линия чудовищ: что обведено, что следующее и хватит ли силы
  heroes-tool scores                  ПО всех игроков по категориям, если бы партия закончилась сейчас
  heroes-tool stockpile [игрок]       пороги накопления ресурсов: что обведено, сколько до следующего

Игрок — номер или имя; по умолчанию вы. Запускается через запускалку (heroes/players/workspace.py)
в той же песочнице, что и heroes-run: нужен срез партии из переменной HEROES_GAME (heroes/sim.py).
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from . import sim
from .engine import scoring
from .engine.combat import artifact_condition, artifact_condition_met
from .engine.data import TERRAINS
from .engine.economy import can_pay, sum_costs, token_vp
from .engine.effects import army_strength, building_cost, hire_cost, hireable_units, move_cost, preview_battle
from .engine.movement import reachable
from .engine.state import RESOURCE_VP, ROUNDS, TERRAIN_NAMES
from .protocol import render_text as R

if TYPE_CHECKING:
    from .engine.game import Game


class ToolError(Exception):
    pass


def player_arg(game: Game, me: int, arg: str | None) -> int:
    """Номер или имя игрока; без аргумента — вы."""
    if arg is None:
        return me
    names = {pl.name.casefold(): q for q, pl in enumerate(game.state.players)}
    q = int(arg) if arg.isdigit() else names.get(arg.casefold())
    if q is None or not 0 <= q < len(game.state.players):
        raise ToolError(f"unknown player {arg!r}; players: " + ", ".join(f"{q} {pl.name}" for q, pl in enumerate(game.state.players)))
    return q


def who(game: Game, me: int, p: int) -> str:
    return game.state.players[p].name + (" (you)" if p == me else "")


# --- инструменты ----------------------------------------------------------------------------------

def board(game: Game, me: int, arg: str | None = None) -> str:
    p = player_arg(game, me, arg)
    return R.board(game, p, own=p == me)


def max_strength(game: Game, me: int, arg: str | None = None) -> str:
    p = player_arg(game, me, arg)
    player = game.state.players[p]
    faction = game.faction_of(p)
    battle = preview_battle(game, p)
    lines = [f"{who(game, me, p)}, {faction.name}: army strength now {army_strength(game, p, player.units, battle)} — "
             + R.units_line(game, p)]
    best = None
    for unit in hireable_units(game, p):
        cost = hire_cost(game, p, unit)
        if not cost:
            continue
        n, total = 0, {}
        while can_pay(game, p, sum_costs(total, cost)) and n < 50:
            total, n = sum_costs(total, cost), n + 1
        if n:
            strength = army_strength(game, p, {**player.units, unit: player.units.get(unit, 0) + n}, battle)
            if best is None or strength > best[0]:
                best = (strength, unit, n)
    stock = R.holdings_line(game, p)
    if best:
        lines.append(f"Hiring (stock {stock}): if all resources go to {faction.units[best[1]].name} ×{best[2]}, "
                     f"strength becomes {best[0]} (one kind only, hiring buildings already built; the best of them).")
    else:
        lines.append(f"Hiring (stock {stock}): nothing hireable is affordable now.")
    unhireable = [u.name for uid, u in faction.units.items() if uid not in hireable_units(game, p)]
    if unhireable:
        lines.append("Not hireable yet (hiring building not built): " + ", ".join(unhireable))
    return "\n".join(lines)


def schedule(game: Game, me: int, arg: str | None = None) -> str:
    st = game.state
    n = len(st.players)
    first = lambda r: (st.first_player + r - st.round) % n   # первый игрок меняется каждый раунд (+1 по кругу)
    mine = [r for r in range(st.round, ROUNDS + 1) if first(r) == me]
    lines = [f"Now: round {st.round}/{ROUNDS}, first player {st.players[st.first_player].name}."]
    lines.append("You choose the action order (are the first player) in rounds: " + (", ".join(map(str, mine)) or "none left")
                 + (f". Next: round {mine[0]}" + (" (this round)" if mine[0] == st.round else f", in {mine[0] - st.round} round(s)")
                    if mine else "."))
    lines.append("First player by round: " + ", ".join(f"{r}:{st.players[first(r)].name}" for r in range(st.round, ROUNDS + 1)))
    return "\n".join(lines)


def tree(game: Game, me: int, arg: str | None = None) -> str:
    p = player_arg(game, me, arg)
    player = game.state.players[p]
    faction = game.faction_of(p)
    opens: dict[str, list[str]] = {bid: [] for bid in faction.buildings}
    for bid, b in faction.buildings.items():
        for req in b.requires:
            opens[req].append(bid)
    lines = [f"Building tree of {who(game, me, p)}, {faction.name} (✓ built; needs = at least one of its requirements):"]
    for level in (1, 2, 3, 4):
        for bid, b in faction.buildings.items():
            if b.level != level:
                continue
            mark = "✓" if bid in player.built else " "
            needs = " or ".join(f"{faction.buildings[r].name}{'✓' if r in player.built else ''}" for r in b.requires) or "—"
            opened = ", ".join(faction.buildings[o].name for o in opens[bid]) or "—"
            lines.append(f" {mark} L{level} {b.name} [{bid}] {b.vp} VP, {R.res(building_cost(game, p, bid))}; needs: {needs}; "
                         f"opens: {opened}. {b.text}")
    return "\n".join(lines)


def reach(game: Game, me: int, arg: str | None = None) -> str:
    if arg is None or not arg.isdigit():
        raise ToolError("usage: heroes-tool reach <movement points>")
    mp = int(arg)
    dist = reachable(game, me, mp)
    player = game.state.players[me]
    lines = [f"From {player.region} with {mp} MP (cost of the cheapest way; ✓ visited by you, first-visit resources in brackets):"]
    for region, cost in sorted(dist.items(), key=lambda kv: (kv[1], kv[0])):
        info = game.map.regions[region]
        here = [pl.name for pl in game.state.players if pl.region == region and pl is not player]
        lines.append(f"  {cost} {region} [{R.terrain_text(game, region)}] "
                     f"{'✓ ' if region in player.visited else ''}({R.res(info.resources)}) {R.object_text(game, me, region)}"
                     + (f" · here: {', '.join(here)}" if here else ""))
    lines.append("Movement cost of your faction: " + ", ".join(f"{TERRAIN_NAMES[t]} {move_cost(game, me, t)}" for t in TERRAINS))
    return "\n".join(lines)


def progress(game: Game, p: int, cond: dict) -> str:
    player = game.state.players[p]
    kind = cond["condition"]
    if kind == "pay":
        return "you have " + R.holdings_line(game, p)
    have = {"foundationsBuilt": sum(1 for lvl in player.foundations.values() if lvl),
            "battleWins": player.victories, "mountainsVisited": len(player.mountains)}[kind]
    return f"you have {have}"


def artifacts(game: Game, me: int, arg: str | None = None) -> str:
    p = player_arg(game, me, arg)
    st = game.state
    lines = [f"Artifacts for {who(game, me, p)} (condition for this faction → effect):"]
    for region, art in game.faction_of(p).artifacts.items():
        holder = st.artifact_holder.get(region)
        cond = artifact_condition(game, p, region)
        state = (f"held by {who(game, me, holder)}" if holder is not None else "free")
        if region in st.reserved and holder is None:
            state += f", reserved by {st.players[st.reserved[region]].name}"
        met = "condition MET" if artifact_condition_met(game, p, region) else "not met"
        lines.append(f"  {region}: {state}; {R.condition_text(cond)} ({progress(game, p, cond)}; {met}) → {art.text}")
    return "\n".join(lines)


def fortresses(game: Game, me: int, arg: str | None = None) -> str:
    st = game.state
    mine = army_strength(game, me, st.players[me].units, preview_battle(game, me))
    lines = [f"Fortresses (your army strength now: {mine}; buying units can raise it, see max_strength):"]
    for region in sorted(st.available):
        f = game.map.regions[region].fortress
        if game.map.regions[region].object != "fortress":
            continue
        owner = st.fortress_owner.get(region)
        state = f"captured by {who(game, me, owner)}" if owner is not None else ("free" if f.strength < mine else f"free, {f.strength - mine + 1} more strength needed")
        lines.append(f"  {region} [{R.terrain_text(game, region)}]: strength {f.strength} → {f.vp} VP + {R.res(f.loot)}; {state}")
    return "\n".join(lines)


def monsters(game: Game, me: int, arg: str | None = None) -> str:
    p = player_arg(game, me, arg)
    player = game.state.players[p]
    battle = preview_battle(game, p)
    mine = army_strength(game, p, player.units, battle)
    lines = [f"Monster line of {who(game, me, p)} (army strength now {mine}):"]
    nxt = True
    for i, m in enumerate(game.monster_line(p)):
        circled = bool(player.monsters[i])
        tag = "circled" if circled else ("NEXT" if nxt else "later")
        extra = ""
        if tag == "NEXT":
            extra = " — enough" if mine > m.strength else f" — {m.strength - mine + 1} more strength needed"
            nxt = False
        lines.append(f"  {i + 1}. strength {m.strength} → {m.vp} VP" + (f" + {R.res(m.loot)}" if m.loot else "") + f" [{tag}]{extra}")
    lines.append(f"Battle victories: {player.victories}. Unspent MP let you skip one monster each.")
    return "\n".join(lines)


def scores(game: Game, me: int, arg: str | None = None) -> str:
    table = scoring.final_scores(game)
    names = [f"{pl.name}{'*' if q == me else ''}" for q, pl in enumerate(game.state.players)]
    width = max(len(n) for n in names) + 2
    lines = ["VP if the game ended now (* — you)", " " * 22 + "".join(n.rjust(width) for n in names)]
    for key, title in scoring.CATEGORIES.items():
        lines.append(title.ljust(22) + "".join(str(s[key]).rjust(width) for s in table))
    lines.append("TOTAL".ljust(22) + "".join(str(s["total"]).rjust(width) for s in table))
    lines.append(f"Round {game.state.round}/{ROUNDS}.")
    return "\n".join(lines)


def stockpile(game: Game, me: int, arg: str | None = None) -> str:
    p = player_arg(game, me, arg)
    player = game.state.players[p]
    lines = [f"Stockpile VP of {who(game, me, p)} (circled permanently once the stock reaches the threshold):"]
    for key in ("wood", "iron", "gold"):
        amount = player.resources[key]
        done = [t for t, _ in RESOURCE_VP if t in player.resource_marks[key]]
        left = [(t, vp) for t, vp in RESOURCE_VP if t not in player.resource_marks[key]]
        nxt = f"next: {left[0][0]} (+{left[0][1]} VP), {max(left[0][0] - amount, 0)} to go" if left else "all circled"
        lines.append(f"  {key}: {amount}; circled at {', '.join(map(str, done)) or 'none'}; {nxt}")
    for key, thresholds in token_vp(game, p).items():
        marks = player.resource_marks.get(key, [])
        left = sorted((t, vp) for t, vp in thresholds.items() if t not in marks)
        have = player.tokens.get(key, 0)
        lines.append(f"  {key}: {have}; " + (f"next: {left[0][0]} (+{left[0][1]} VP), {max(left[0][0] - have, 0)} to go" if left else "all circled"))
    return "\n".join(lines)


TOOLS = {f.__name__: f for f in (board, max_strength, schedule, tree, reach, artifacts, fortresses, monsters, scores, stockpile)}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in TOOLS:
        print("usage: heroes-tool <tool> [argument]\ntools: " + ", ".join(TOOLS) + "\n(see heroes/agent_tools.py)")
        return 2
    try:
        s = sim.load()
        print(TOOLS[argv[0]](s.game, s.me, argv[1] if len(argv) > 1 else None))
    except ToolError as e:
        print(f"error: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
