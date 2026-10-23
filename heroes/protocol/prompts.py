"""Тексты для Claude: системный промпт (правила, планшет, карта) и вопросы по ходу партии.

Протокол на английском; по-русски остаются только оригинальные названия игры
(регионы, здания, войска, фракция).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..engine import scoring
from ..engine.combat import artifact_condition
from ..engine.data import TERRAINS
from ..engine.effects import hydra_unit
from ..engine.state import OBJECT_NAMES, ROUNDS, TERRAIN_NAMES
from . import notes
from . import render_text as R
from .schemas import Spec

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game

RULES = """\
GOAL. Score the most victory points (VP) over 16 rounds. On a VP tie, whoever has more resources in stock wins.

ROUND. The round's first player chooses 4 of the 5 actions and their order; an action's position is its level X (1–4), and nobody performs the fifth action this round. All players perform the same actions in that order. The first-player role passes clockwise each round.
- Gather wood / iron / gold, level X: +X of that resource.
- Build & hire, level X: you may build one building of level at most X (if it lists required buildings, you need at least one of them) and hire any number of units into armies whose hiring building you have built; hiring does not depend on the action level. Building and hiring go in any order, and you may hire several times: e.g. hire, build, then hire again. If you stand in a region with a foundation, the building you just built is automatically placed on it: the building's level is written on the foundation and scores that many VP at game end (the written value can only go up). Each player keeps their own foundation marks.
- Move, level X: X movement points (MP). The destination is chosen secretly and simultaneously. Entering a region costs as many MP as its terrain costs for your faction. All portal regions are adjacent to each other. You may stay where you are. You gain a region's resources on your first visit; a region counts as visited only where you end your move (your starting region is visited too).

AFTER MOVEMENT, in this order:
1) If several players are in one region, they declare in turn: fight, claim (claim the region's fortress/artifact) or pass. If anyone chose fight or there is more than one claimant, all players in the region fight. If there is a single claimant and nobody wants to fight, the claimant gets the right to the object.
2) Fortress capture — if you are alone in the region, won the battle there, or were the only claimant. You commit units; to capture you need strength greater than the fortress's. A fortress can be captured once per game: its loot immediately, its VP at game end.
3) Monster fight — in any region, against the leftmost uncircled monster of your own line; each unspent MP lets you skip one monster. At most one monster fight per round. Victory: the monster is circled, its loot immediately, its VP at game end.
4) The region's artifact — if you have the right to the object and meet its condition (a pay condition means you pay). Each artifact has one owner; it is crossed out for everyone else but can be taken away in battle.
Fortress capture, monster fights and artifacts only happen in a round that had a move action.

BATTLES. Army strength is the sum of the committed units' strength. To win you need STRICTLY more than the opponent. All committed units die whatever the outcome, even the winner's, so commit no more than you need.
Battle between players: commitment is secret; the highest strength wins, on a tie everyone loses. The winner circles the leftmost uncircled monster of their own line (its VP count at game end) and picks ONE reward: that monster's loot; OR the losers' resources (at most the difference between the winner's strength and the weakest loser's); OR one artifact of a loser (then you cannot take the region's artifact this round).
Battle victories = captured fortresses + circled monsters.

RESOURCES. The first time your stock of one resource reaches 14 / 22 / 30, you circle 2 / 2 / 3 VP — permanently, even if you spend it later. You split "any resource" gains yourself.

SCORING at game end: VP of built buildings and their effects; foundations (sum of written levels); circled stockpile VP; captured fortresses and circled monsters; artifact effects.

OPENNESS. The whole game state is public except the secret choice of destination and the secret army commitment in player battles.

SPECIAL RULES OF THIS VERSION:
- Regions crossed out because of the player count do not exist: you cannot pass through them.
- Башня иллюзий: a reserved object is unavailable to opponents (a reserved portal stops working for them).
- A taken artifact loses its permanent effect for the previous owner; one-time effects (resources, units) trigger on every acquisition.
- Factions differ: your faction's properties (on your board below) and the opponents' faction properties change some of these rules.
- Names of regions, buildings, units and the faction are the original Russian names from the board game.
"""

PROTOCOL = """\
HOW TO ANSWER. The game engine sends you questions one at a time. Answer strictly with JSON matching the given schema:
"answer" — your decision, "comment" — 1–2 sentences in English about your plan (opponents do not see it).
If an answer is rejected, you will get an error message — answer the same question again.
Optional "rules_unclear": if a rule is unclear to you, seems contradictory, or the engine does not behave as you read
the rules, describe it there in 1–3 sentences (a human reads these reports to improve the rules text). Omit it otherwise.
Optional "view_boards": by the openness rule you may look at any player's full board (buildings with costs and effects,
artifacts, hire costs, monster line, movement costs, stockpile marks) — the questions only carry a summary of the
opponents. List player numbers or names there (e.g. [1, 2]); you then get those boards and the same question again,
and the "answer" and "comment" of that reply are ignored (put any placeholder). Use it when the details matter.
Play to win: count VP, resources and strength, plan several rounds ahead, watch your opponents."""

# Решения, перед которыми Claude получает полную картину: планшет, соперников, состояние карты.
MAJOR = {"choose_start", "action_order", "build_recruit", "destination", "region_stance", "commit_army",
         "choose_reward", "reserve_object", "curse_region", "summon_hydra", "raise_action", "avoid_battle"}


def monster_line_text(monsters) -> str:
    return "; ".join(f"strength {m.strength} → {m.vp} VP" + (f" + {R.res(m.loot)}" if m.loot else "") for m in monsters)


def faction_text(game: Game, p: int) -> str:
    faction = game.faction_of(p)
    lines = [f"FACTION BOARD: {faction.name}"]
    if faction.rules:
        lines.append("Faction properties (they hold all game long):")
        lines += [f"  {rule.name}: {rule.text}" for rule in faction.rules.values()]
    lines += ["Movement cost (MP to enter a region): "
             + ", ".join(f"{TERRAIN_NAMES[t]} {faction.movement[t]}" for t in TERRAINS)
             + " (buildings and artifacts can lower it).",
             "Units (base strength, hire cost, starting count):"]
    for uid, u in faction.units.items():
        lines.append(f"  {uid} — {u.name}: strength {u.strength}, hire {R.res(u.cost)}, at start {u.start}")
    lines.append("Buildings (id — name: level, VP, cost, requires at least one of, effect):")
    for bid, b in sorted(faction.buildings.items(), key=lambda kv: kv[1].level):
        req = " or ".join(faction.buildings[r].name for r in b.requires) or "—"
        lines.append(f"  {bid} — {b.name}: level {b.level}, {b.vp} VP, cost {R.res(b.cost)}, requires: {req}. {b.text}")
    lines.append("Artifacts (region: condition to gain → effect for your faction):")
    for region, a in faction.artifacts.items():
        lines.append(f"  {region}: {R.condition_text(artifact_condition(game, p, region))} → {a.text}")
    if faction.monsters:
        lines.append("Your own monster line (instead of the common one), left to right: " + monster_line_text(faction.monsters))
    lines.append("Resource abbreviations: w — wood, i — iron, g — gold, a — any resource of your choice.")
    return "\n".join(lines)


def opponent_factions_text(game: Game, p: int) -> str:
    """Свойства фракций соперников: без них не понять, чего от них ждать (проклятия, гидра…)."""
    lines = ["OPPONENT FACTIONS (their full boards are summarised in OPPONENTS during the game):"]
    for fid in dict.fromkeys(pl.faction for q, pl in enumerate(game.state.players) if q != p):
        faction = game.factions[fid]
        lines.append(f"  {faction.name}: movement " + ", ".join(f"{TERRAIN_NAMES[t]} {faction.movement[t]}" for t in TERRAINS)
                     + "; units " + ", ".join(f"{u.name} (strength {u.strength})" for u in faction.units.values()))
        lines += [f"    {rule.name}: {rule.text}" for rule in faction.rules.values()]
        if faction.monsters:
            lines.append("    Own monster line: " + monster_line_text(faction.monsters))
    return "\n".join(lines)


def map_text(game: Game) -> str:
    st = game.state
    lines = ["MAP (only these regions are in play; resources are gained on the first visit):"]
    for name in sorted(st.available):
        region = game.map.regions[name]
        obj = region.object
        if obj == "fortress":
            f = region.fortress
            desc = f"fortress of strength {f.strength} (capture: {R.res(f.loot)} immediately and {f.vp} VP at game end)"
        elif obj == "artifact":
            desc = f"artifact ({R.condition_text(region.artifact)})"
        else:
            desc = OBJECT_NAMES[obj]
        nbrs = ", ".join(sorted(n for n in game.map.adjacent[name] if n in st.available))
        lines.append(f"  {name} — {TERRAIN_NAMES[region.terrain]}; resources {R.res(region.resources)}; {desc}; neighbours: {nbrs}")
    lines.append("Portal regions: " + ", ".join(r for r in game.map.portals if r in st.available)
                 + " — all adjacent to each other.")
    lines.append("Monster line (each player has their own, left to right): " + monster_line_text(game.map.monsters))
    return "\n".join(lines)


def system_prompt(game: Game, p: int, variant: str, tools: str | None = None) -> str:
    """variant — вариант Claude (папка заметок); tools — раздел об инструментах, если они есть."""
    st = game.state
    me = st.players[p]
    others = ", ".join(f"{pl.name} ({game.faction_of(q).name})" for q, pl in enumerate(st.players) if q != p)
    seating = " → ".join(pl.name for pl in st.players)
    return "\n\n".join([
        f"You are playing the board strategy game «Герои» (Hobby World) as the faction {game.faction_of(p).name}. "
        f"Your name in this game is {me.name}, your player number is {p}. Opponents: {others}. "
        f"Seating, clockwise: {seating}.",
        RULES, faction_text(game, p), opponent_factions_text(game, p), map_text(game),
        notes.prompt_text(variant, game.state.players[p].faction), *([tools] if tools else []), PROTOCOL,
    ])


def map_state(game: Game, p: int) -> str:
    """Изменяемая часть карты: где кто стоит, что захвачено, взято, забронировано, вписано."""
    st = game.state
    lines = ["MAP STATE:",
             "  Players: " + "; ".join(f"{pl.name} — {pl.region}" for pl in st.players)]
    hydras = [f"{pl.name}'s {game.faction_of(q).units[hydra_unit(game, q)].name} — {pl.hydra}"
              for q, pl in enumerate(st.players) if pl.hydra]
    if hydras:
        lines.append("  Hydras: " + "; ".join(hydras))
    changed = [r for r in sorted(st.available)
               if r in st.fortress_owner or r in st.artifact_holder or r in st.reserved or r in st.cursed
               or any(pl.foundations.get(r) for pl in st.players)]
    for r in changed:
        lines.append(f"  {r}: {R.object_text(game, p, r)}")
    if st.terrain:
        lines.append("  Terrain changed: " + ", ".join(f"{r} — {R.terrain_text(game, r)}" for r in sorted(st.terrain)))
    me = st.players[p]
    unvisited = [r for r in sorted(st.available) if r not in me.visited and game.map.regions[r].resources]
    lines.append(f"  You have visited: {', '.join(me.visited)}")
    lines.append(f"  Not yet visited (with resources): {', '.join(unvisited) or 'none'}")
    return "\n".join(lines)


def question(game: Game, p: int, d: Decision, spec: Spec, events: list[str], error: str | None,
             variant: str) -> str:
    return questions(game, p, [d], spec, events, error, variant)


def questions(game: Game, p: int, ds: list[Decision], spec: Spec, events: list[str], error: str | None,
              variant: str) -> str:
    """Один вопрос или несколько тайных сразу (перемещение и гидра): тогда spec — составной, answer.<вид решения>."""
    parts = []
    if error:
        parts.append(f"YOUR PREVIOUS ANSWER WAS REJECTED: {error}\nAnswer the same question again.")
    else:
        if events:
            parts.append("EVENTS SINCE THE LAST QUESTION:\n" + "\n".join(events))
        if any(d.kind in MAJOR for d in ds):
            parts += [R.board(game, p), R.opponents(game, p), map_state(game, p)]
        else:
            parts.append(R.round_line(game) + f". Your stock: {R.holdings_line(game, p)}.")
    faction = game.state.players[p].faction
    reminders = [] if error else list(dict.fromkeys(
        text for d in ds for text in notes.reminders_for(variant, faction, game.state.round, d.kind)))
    if reminders:
        parts.append("YOUR REMINDERS:\n" + "\n".join(f"- {text}" for text in reminders))
    if len(ds) > 1:
        parts.append(f"{len(ds)} SECRET QUESTIONS AT ONCE — answer all of them in this one reply.")
    parts += [f"QUESTION ({d.kind}, round {game.state.round}/{ROUNDS}):\n{d.text}" for d in ds]
    parts.append(f"FORMAT: {spec.instructions.rstrip('.')}. Plus comment — your plan in 1–2 sentences.\n"
                 f"JSON schema of the answer field: {json.dumps(spec.schema, ensure_ascii=False)}")
    return "\n\n".join(parts)


def played_by(opponents: dict[str, str]) -> str:
    return "; ".join(f"{notes.faction_names()[f]} — {kind}" for f, kind in opponents.items())


def game_plan(game: Game, p: int, opponents: dict[str, str], tools: bool) -> str:
    """Самый первый вопрос партии: продумать план. opponents — id фракции → кто за неё играет."""
    return "\n\n".join([
        f"THE GAME IS ABOUT TO START. Opponents are played by: {played_by(opponents)}.",
        "QUESTION (game_plan): before your first decision, take time to think through your strategy for this whole "
        "game. Use the rules, your faction board, the map and your strategy notes. Cover:\n"
        "- your overall plan: economy, the order of buildings, which monsters, fortresses and artifacts to go for, "
        "a rough timeline over the 16 rounds and a good starting region;\n"
        "- for each opponent faction in this game: what it threatens you with and how you will counter it.\n"
        "You will keep this plan in mind for the rest of the game and adapt it as the game goes."
        + (" This is a good moment to use your tools: read your scripts' README, check numbers with scripts or "
           "simulations." if tools else ""),
        "FORMAT: answer — your plan as a string, terse bullet points, at most about 1500 characters. "
        "Plus comment — the plan in one sentence.",
    ])


def reflection(game: Game, p: int, variant: str, opponents: dict[str, str], allowed: list[str], events: list[str],
               history: str, tools: bool) -> str:
    """Вопрос после партии: разбор своей игры и, по желанию, выводы в заметки фракции.
    opponents — id фракции → кто за неё играл; allowed — разделы, которые можно переписать."""
    faction = game.state.players[p].faction
    scores = scoring.final_scores(game)
    titles = notes.titles(faction)
    sections = "\n".join(f'  "{key}" — {titles[key]}, {notes.section_rule(key)}' for key in allowed)
    parts = []
    if events:
        parts.append("EVENTS SINCE THE LAST QUESTION:\n" + "\n".join(events))
    parts += [
        R.final_scores(game, scores, scoring.winners(game, scores)),
        f"THE GAME IS OVER. Opponents were played by: {played_by(opponents)}. A random bot plays at random, so its play says "
        "nothing about how that faction should be countered.",
        "Your current strategy notes file (the same one you got at the start of the game):\n\n"
        + notes.render(faction, notes.load(variant, faction)),
        "HISTORY of your notes and scripts (git) — read it before you change anything: do not undo a recent lesson "
        "without a reason from this game, and do not repeat a change that did not help.\n\n" + history,
        "QUESTION (strategy_notes), two parts.\n"
        "1) REVIEW (required): look back at your own play against your plan from the start of the game. What worked, "
        "what did not, which decisions were mistakes and what you should have done instead, the turning points. "
        "Be honest and concrete, at most about 1500 characters.\n"
        f"2) NOTES (optional): you may update your strategy notes so that you play {game.faction_of(p).name} better "
        "next time. Update only if this game taught you something important — a lesson that would have "
        "substantially improved your play had you known it from the start. If not, set \"update\": false.\n"
        "What to write: general, reusable conclusions — priorities, timing, which buildings, monsters, fortresses and "
        "artifacts pay off for this faction, what failed and why, how this opponent faction threatens you and how to "
        "beat it. Not the rules, not obvious facts, not a story of this game. Terse bullet points.\n"
        "Each section you send REPLACES that section entirely: merge in the old notes you want to keep, drop what "
        "proved wrong or minor, keep only what matters most. Sections you leave out stay unchanged.\n"
        "Reminders are short lines '- [<when>] <text>' that the engine shows you inside the matching questions. "
        "<when> is a comma-separated list: always, round N, rounds N-M and decision kinds ("
        + ", ".join(notes.DECISION_KINDS) + "); rounds among themselves and kinds among themselves mean «or», "
        "rounds together with kinds mean «and». Example: '- [rounds 14-16, build_recruit] Spend everything on VP.' "
        "Make reminders more useful: sharpen them, tie them to the right moment, drop those that did not help.\n"
        "Sections you may rewrite:\n" + sections
        + ("\n3) SCRIPTS (optional): before answering you may improve the scripts in your workspace and their "
           "README.md; they are saved for your future games." if tools else ""),
        'FORMAT: answer — {"review": "<your review>", "update": false} or {"review": "<your review>", "update": true, '
        '"sections": {"<section key>": "<new full text of the section>", ...}}. '
        "Plus comment — what you changed in the notes and why, in 1–2 sentences (it becomes the git commit message). "
        "Plus rules_unclear — every rule you were unsure about or misunderstood during the game, if any.",
    ]
    return "\n\n".join(parts)
