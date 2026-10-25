"""Текстовое представление партии: планшет, соперники, карта, итоги.

Без цветов и разметки, чтобы этот же текст можно было показывать и человеку, и Claude.
Ресурсы сокращаются: w — wood, i — iron, g — gold, a — any; уникальные ресурсы фракций (ярость,
наука, краки) пишутся словами.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..engine.actions import buildable
from ..engine.combat import army_strength, artifact_condition
from ..engine.data import TERRAINS
from ..engine.economy import holdings, token_vp
from ..engine.effects import building_cost, effects_of, hire_cost, hireable_units, hydra_unit, move_cost, preview_battle, unit_strength
from ..engine.scoring import CATEGORIES, score_player
from ..engine.state import ACTION_NAMES, RESOURCE_NAMES, RESOURCE_VP, ROUNDS, TERRAIN_NAMES

if TYPE_CHECKING:
    from ..engine.game import Game

SHORT = {"wood": "w", "iron": "i", "gold": "g", "any": "a"}
LEVELS = {1: "I", 2: "II", 3: "III", 4: "IV"}


def res(resources: dict[str, int]) -> str:
    return " ".join(f"{v}{SHORT[k]}" if k in SHORT else f"{v} {RESOURCE_NAMES.get(k, k)}"
                    for k, v in resources.items() if v) or "—"


def stock(resources: dict[str, int]) -> str:
    return " · ".join(f"{SHORT[k]} {resources[k]}" for k in ("wood", "iron", "gold"))


def name_of(game: Game, p: int) -> str:
    return game.state.players[p].name


def holdings_line(game: Game, p: int) -> str:
    """Склад и то, чем ещё можно платить: ярость, наука, краки."""
    extra = {k: v for k, v in holdings(game, p).items() if k not in SHORT}
    return stock(game.state.players[p].resources) + "".join(f" · {RESOURCE_NAMES.get(k, k)} {v}" for k, v in extra.items())


def terrain_text(game: Game, region: str) -> str:
    """Местность сейчас; изменённая Гейзером — с прежней в скобках."""
    now, was = game.terrain(region), game.map.regions[region].terrain
    return TERRAIN_NAMES[now] + (f" (was {TERRAIN_NAMES[was]})" if now != was else "")


def special_lines(game: Game, p: int, own: bool = True) -> list[str]:
    """Особенности фракции на планшете: уникальные ресурсы, наука, ноты, гидра, проклятия.
    own — планшет читает сам игрок p (иначе — соперник)."""
    st = game.state
    player = st.players[p]
    lines = []
    for key, thresholds in token_vp(game, p).items():
        marks = player.resource_marks.get(key, [])
        steps = ", ".join(f"{t}={vp}VP" + (" ✓" if t in marks else "") for t, vp in sorted(thresholds.items()))
        lines.append(f"{RESOURCE_NAMES[key].capitalize()}: {player.tokens.get(key, 0)} (stockpile VP: {steps})")
    for e in effects_of(game, p, "science_track"):
        free = effects_of(game, p, "free_build_by_science")
        steps = ", ".join(f"level {lvl} buildings free from {t}" for lvl, t in free[0]["thresholds"].items()) if free else ""
        lines.append(f"Science: {player.tokens.get('science', 0)} of {e['max']}" + (f" ({steps})" if steps else ""))
    if player.notes:
        pairs = " ".join("●●" if n == 2 else "●○" if n == 1 else "○○" for n in player.notes)
        lines.append(f"Notes (pairs, ● circled): {pairs}")
    unit = hydra_unit(game, p)
    if unit:
        name = game.faction_of(p).units[unit].name
        where = (f"in {player.hydra}" + (f" ({'with' if player.hydra == player.region else 'apart from'} {'you' if own else 'its owner'})")
                 if player.hydra else "not on the map")
        lines.append(f"{name}: {where}, strength {unit_strength(game, p, unit)}; summoned {player.hydra_summons} time(s)")
    cursed = sorted(r for r, q in st.cursed.items() if q == p)
    if effects_of(game, p, "curse_penalty") or cursed:
        lines.append(f"Cursed regions (by this player): {', '.join(cursed) or 'none'}")
    return lines


def condition_text(cond: dict) -> str:
    kind = cond["condition"]
    if kind == "pay":
        return f"pay {res(cond['cost'])}"
    return {
        "foundationsBuilt": f"{cond['count']} foundations with your buildings",
        "battleWins": f"{cond['count']} battle victories",
        "mountainsVisited": f"{cond['count']} mountain regions visited",
    }[kind]


def units_line(game: Game, p: int) -> str:
    units = game.state.players[p].units
    faction = game.faction_of(p)
    battle = preview_battle(game, p)
    parts = [f"{faction.units[u].name} ×{n} (strength {unit_strength(game, p, u, battle)})" for u, n in units.items() if n]
    total = army_strength(game, p, units, battle)
    return (", ".join(parts) + f"; total strength {total}") if parts else "no units"


def round_line(game: Game) -> str:
    st = game.state
    line = f"Round {st.round}/{ROUNDS}, first player: {name_of(game, st.first_player)}"
    if st.action_order:
        line += ". Actions: " + ", ".join(f"{i}. {ACTION_NAMES[a]}" for i, a in enumerate(st.action_order, 1))
    return line


def board(game: Game, p: int, own: bool = True) -> str:
    """Полный планшет игрока p; own — его читает сам p, иначе — соперник (по правилу открытости)."""
    st = game.state
    player = st.players[p]
    faction = game.faction_of(p)
    you = "you" if own else "this player"
    lines = [f"BOARD: {player.name} ({faction.name})" + ("" if own else f", player {p}"), round_line(game)]
    if player.region:
        lines.append(f"Region: {player.region} ({terrain_text(game, player.region)}). "
                     f"Regions visited: {len(player.visited)} (mountains: {len(player.mountains)}).")
    marks = []
    for k in ("wood", "iron", "gold"):
        done = [f"{t}={vp}VP" for t, vp in RESOURCE_VP if t in player.resource_marks[k]]
        if done:
            marks.append(f"{SHORT[k]}: {', '.join(done)}")
    lines.append(f"Stock: {stock(player.resources)}" + (f"   (stockpile VP circled — {'; '.join(marks)})" if marks else ""))
    lines += special_lines(game, p, own)
    if faction.rules:
        lines.append("Faction properties:")
        lines += [f"  {rule.name}: {rule.text}" for rule in faction.rules.values()]

    lines.append("Units:")
    hireable = hireable_units(game, p)
    for uid, unit in faction.units.items():
        status = "hireable" if uid in hireable else "hiring building not built"
        lines.append(f"  {unit.name} ×{player.units.get(uid, 0)}: strength {unit_strength(game, p, uid, preview_battle(game, p))}, "
                     f"hire {res(hire_cost(game, p, uid))} ({status})")
    lines.append(f"  Total army strength: {army_strength(game, p, player.units, preview_battle(game, p))}")

    lines.append("Buildings (✓ built, + can be built with a build action of its level):")
    can_build = set(buildable(game, p, 4))
    for level in (4, 3, 2, 1):
        for bid, b in faction.buildings.items():
            if b.level != level:
                continue
            mark = "✓" if bid in player.built else ("+" if bid in can_build else " ")
            req = " or ".join(faction.buildings[r].name for r in b.requires)
            req = f"; requires: {req}" if req else ""
            cost = building_cost(game, p, bid)
            cost_text = res(b.cost) + (f" (for {you}: {res(cost)})" if cost != b.cost and bid not in player.built else "")
            prop = (" (The effect after hiring is a property of the units: it applies even if the building "
                    "is not built.)" if any(e.get("unit_property") for e in b.effects) else "")
            lines.append(f"  {mark} {LEVELS[level]:>3} {b.name} [{bid}] — {b.vp} VP, cost {cost_text}{req}. {b.text}{prop}")

    lines.append("Artifacts (region: condition → effect):")
    for region_name, art in faction.artifacts.items():
        holder = st.artifact_holder.get(region_name)
        status = ("YOURS" if own else "THEIRS") if holder == p else (f"held by {name_of(game, holder)}" if holder is not None else "available")
        if region_name in st.reserved and holder is None:
            status += f", reserved by {name_of(game, st.reserved[region_name])}"
        lines.append(f"  {region_name} [{status}]: {condition_text(artifact_condition(game, p, region_name))} → {art.text}")

    if player.foundations:
        lines.append("Foundations: " + ", ".join(f"{r} — level {lvl}" for r, lvl in player.foundations.items()))
    if player.fortresses:
        lines.append("Captured fortresses: " + ", ".join(player.fortresses))
    monsters = []
    for i, m in enumerate(game.monster_line(p)):
        state = {"monster": "✓", "pvp": "✓pvp"}.get(player.monsters[i], "·")
        monsters.append(f"{m.strength}{state}")
    lines.append(f"Monster line (strength, ✓ circled): {' '.join(monsters)}. Battle victories: {player.victories}.")
    lines.append("Movement cost: " + ", ".join(f"{TERRAIN_NAMES[t]} {move_cost(game, p, t)}" for t in TERRAINS))
    score = score_player(game, p)
    lines.append(f"VP if the game ended now: {score['total']}")
    return "\n".join(lines)


def opponents(game: Game, p: int) -> str:
    st = game.state
    blocks = []
    for q, other in enumerate(st.players):
        if q == p:
            continue
        faction = game.faction_of(q)
        built = ", ".join(faction.buildings[b].name for b in other.built) or "none"
        arts = ", ".join(st.artifacts_of(q)) or "none"
        blocks.append("\n".join([
            f"{other.name} ({faction.name}), in {other.region}",
            f"  Stock: {holdings_line(game, q)}. Units: {units_line(game, q)}",
            *(f"  {line}" for line in special_lines(game, q, own=False)),
            f"  Buildings: {built}",
            f"  Artifacts: {arts}. Fortresses: {', '.join(other.fortresses) or 'none'}. Victories: {other.victories}. "
            f"VP now: {score_player(game, q)['total']}",
        ]))
    return "OPPONENTS\n" + "\n".join(blocks)


def object_text(game: Game, p: int, region_name: str) -> str:
    st = game.state
    region = game.map.regions[region_name]
    obj = region.object
    if obj == "fortress":
        f = region.fortress
        text = f"fortress {f.strength} (→ {f.vp} VP + {res(f.loot)})"
        if region_name in st.fortress_owner:
            text += f", captured by {name_of(game, st.fortress_owner[region_name])}"
    elif obj == "artifact":
        text = f"artifact ({condition_text(region.artifact)})"
        if region_name in st.artifact_holder:
            text += f", held by {name_of(game, st.artifact_holder[region_name])}"
    elif obj == "foundation":
        levels = [f"{pl.name} {pl.foundations[region_name]}" for pl in st.players if pl.foundations.get(region_name)]
        text = "foundation" + (f" (levels: {', '.join(levels)})" if levels else "")
    else:
        text = "portal"
    if region_name in st.reserved:
        text += f", reserved by {name_of(game, st.reserved[region_name])}"
    if region_name in st.cursed:
        text += f", cursed by {name_of(game, st.cursed[region_name])}"
    return text


def game_map(game: Game, p: int) -> str:
    st = game.state
    player = st.players[p]
    here: dict[str, list[str]] = {}
    for q, pl in enumerate(st.players):
        here.setdefault(pl.region, []).append(pl.name)
        if pl.hydra:
            here.setdefault(pl.hydra, []).append(f"{pl.name}'s {game.faction_of(q).units[hydra_unit(game, q)].name}")
    lines = ["MAP (✓ — visited by you; first-visit resources already collected)"]
    for name in sorted(st.available):
        region = game.map.regions[name]
        visited = "✓" if name in player.visited else " "
        resources = res(region.resources) if region.resources else "—"
        nbrs = ", ".join(sorted(n for n in game.map.adjacent[name] if n in st.available))
        who = f" · here: {', '.join(here[name])}" if name in here else ""
        lines.append(f"{visited} {name} [{terrain_text(game, name)}] {resources} · {object_text(game, p, name)}{who}")
        lines.append(f"      neighbours: {nbrs}")
    crossed = sorted(set(game.map.regions) - set(st.available))
    if crossed:
        lines.append("Crossed out (not in play): " + ", ".join(crossed))
    lines.append("All portals are adjacent to each other.")
    return "\n".join(lines)


def final_scores(game: Game, scores: list[dict[str, int]], winners: list[int]) -> str:
    names = [f"{pl.name} ({game.faction_of(p).name})" for p, pl in enumerate(game.state.players)]
    width = max(len(n) for n in names) + 2
    lines = ["FINAL SCORES", " " * 22 + "".join(n.rjust(width) for n in names)]
    for key, title in CATEGORIES.items():
        lines.append(title.ljust(22) + "".join(str(s[key]).rjust(width) for s in scores))
    lines.append("TOTAL".ljust(22) + "".join(str(s["total"]).rjust(width) for s in scores))
    lines.append("Winner: " + ", ".join(game.state.players[w].name for w in winners))
    return "\n".join(lines)
