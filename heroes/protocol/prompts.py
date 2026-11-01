"""Тексты для Claude: системный промпт (правила из docs/RULES_ENG.md, планшет, карта) и вопросы по ходу партии.

Протокол на английском; по-русски остаются только оригинальные названия игры
(регионы, здания, войска, фракция).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..engine import scoring
from ..engine.combat import artifact_condition
from ..engine.data import DATA_DIR, ONE_TIME_EFFECTS, TERRAINS
from ..engine.effects import hydra_unit
from ..engine.state import OBJECT_NAMES, ROUNDS, TERRAIN_NAMES
from . import notes
from . import render_text as R
from .schemas import Spec

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game

DOCS = DATA_DIR.parent / "docs"


def rules_text() -> str:
    """Правила для Claude — docs/RULES_ENG.md, английский перевод docs/RULES.md."""
    return (DOCS / "RULES_ENG.md").read_text(encoding="utf-8").strip()


PROTOCOL = """\
HOW TO ANSWER. The game engine sends you questions one at a time. Reply in two parts, in English:
1) First, as plain text, your analysis: the current situation, the alternatives you considered and why you choose
this one, and your plan for the next rounds. Opponents do not see it; it is kept as the story of your game.
2) Then your decision as one JSON object matching the given schema: "answer".
If an answer is rejected, you will get an error message — answer the same question again.
Optional "rules_unclear": if a rule is unclear to you, seems contradictory, or the engine does not behave as you read
the rules, describe it there in 1–3 sentences (a human reads these reports to improve the rules text). Omit it otherwise.
Play to win: count VP, resources and strength, plan several rounds ahead, watch your opponents."""

MATTERS = ("EVERY DECISION MATTERS: this one affects the outcome of the game. Think it through before you answer: "
           "what it gives you now and in the coming rounds, and what the alternatives would give.")

# Решения, перед которыми Claude получает полную картину: планшет, соперников, состояние карты.
MAJOR = {"choose_start", "action_order", "build_recruit", "destination", "region_stance", "commit_army",
         "choose_reward", "reserve_object", "curse_region", "summon_hydra", "raise_action", "avoid_battle"}


def monster_line_text(monsters) -> str:
    return "; ".join(f"strength {m.strength} → {m.vp} VP" + (f" + {R.res(m.loot)}" if m.loot else "") for m in monsters)


def faction_text(game: Game, q: int, p: int) -> str:
    """Планшет фракции игрока q так, как его видит Claude-игрок p: свой или соперника — одинаково полно.
    Артефакты — отдельно, в artifacts_text: так их проще сравнить между фракциями."""
    faction = game.faction_of(q)
    pl = game.state.players[q]
    lines = [f"YOUR FACTION BOARD: {faction.name}" if q == p else
             f"OPPONENT FACTION BOARD: {faction.name} — {pl.name}, player number {q}. Its texts say «you» meaning "
             f"{pl.name}."]
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
    if faction.monsters:
        lines.append("Own monster line (instead of the common one), left to right: " + monster_line_text(faction.monsters))
    return "\n".join(lines)


def artifacts_text(game: Game, p: int) -> str:
    """Каждый артефакт: условие получения и эффект — для себя и для каждого соперника."""
    order = [p] + [q for q in range(len(game.state.players)) if q != p]
    lines = ["ARTIFACTS — the same 6 regions for everybody, but the condition (for some factions) and the effect differ "
             "per faction. Each artifact has one owner; a battle winner may take one from a loser without meeting its "
             "condition. A one-time effect fires each time the artifact is gained (also when taken in battle) and "
             "stays with a loser; a permanent effect moves with the artifact."]
    for region, a in game.faction_of(p).artifacts.items():
        lines.append(f"  {region}:")
        for q in order:
            effect = game.faction_of(q).artifacts[region]
            kind = "one-time" if all(e["type"] in ONE_TIME_EFFECTS for e in effect.effects) else "permanent"
            who = "you" if q == p else f"{game.state.players[q].name} ({game.faction_of(q).name})"
            lines.append(f"    {who}: {R.condition_text(artifact_condition(game, q, region))} → {kind}: {effect.text}")
    lines.append("Resource abbreviations: w — wood, i — iron, g — gold, a — any resource of your choice.")
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


def time_text(seconds: float) -> str:
    return (f"TIME. Each answer has a hard limit of about {round(seconds / 60)} minutes, thinking and tool calls included; "
            "past it the answer is lost and the question is asked again. Use that time: on key decisions (the start, "
            "action order, building and hiring, moves, battles, rewards) think deeply for several minutes — compare "
            "alternatives, count VP, resources and strength over the next rounds, check your numbers.")


def system_prompt(game: Game, p: int, variant: str, tools: str | None = None, time_limit: float | None = None) -> str:
    """variant — вариант Claude (папка заметок); tools — раздел об инструментах, если они есть;
    time_limit — лимит времени на один ответ, с."""
    st = game.state
    me = st.players[p]
    others = ", ".join(f"{pl.name} ({game.faction_of(q).name})" for q, pl in enumerate(st.players) if q != p)
    seating = " → ".join(pl.name for pl in st.players)
    return "\n\n".join([
        f"You are playing the board strategy game «Герои» (Hobby World) as the faction {game.faction_of(p).name}. "
        f"Your name in this game is {me.name}, your player number is {p}. Opponents: {others}. "
        f"Seating, clockwise: {seating}.",
        rules_text(), notes.rules_prompt_text(), faction_text(game, p, p),
        *(faction_text(game, q, p) for q in range(len(st.players)) if q != p), artifacts_text(game, p), map_text(game),
        notes.prompt_text(variant, game.state.players[p].faction), *([tools] if tools else []),
        *([time_text(time_limit)] if time_limit else []), PROTOCOL,
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
             variant: str, tools_hint: str | None = None) -> str:
    return questions(game, p, [d], spec, events, error, variant, tools_hint)


def questions(game: Game, p: int, ds: list[Decision], spec: Spec, events: list[str], error: str | None,
              variant: str, tools_hint: str | None = None) -> str:
    """Один вопрос или несколько тайных сразу (перемещение и гидра): тогда spec — составной, answer.<вид решения>.
    tools_hint — короткое напоминание об инструментах в конце вопроса, если они есть."""
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
    parts.append(MATTERS)
    parts.append(f"FORMAT: first your analysis as text, then the JSON: {spec.instructions.rstrip('.')}.\n"
                 f"JSON schema of the answer field: {json.dumps(spec.schema, ensure_ascii=False)}")
    if tools_hint:
        parts.append(tools_hint)
    return "\n\n".join(parts)


def played_by(opponents: dict[str, str]) -> str:
    return "; ".join(f"{notes.faction_names()[f]} — {kind}" for f, kind in opponents.items())


def game_plan(game: Game, p: int, tools: bool) -> str:
    """Самый первый вопрос партии: продумать план."""
    return "\n\n".join([
        "THE GAME IS ABOUT TO START.",
        "QUESTION (game_plan): before your first decision, take time to think through your strategy for this whole "
        "game. Use the rules, your faction board, the opponents' boards, the artifacts, the map and your strategy notes. Cover:\n"
        "- your overall plan: economy, the order of buildings, which monsters, fortresses and artifacts to go for, "
        "a rough timeline over the 16 rounds and a good starting region;\n"
        "- your opponents: for each of them, work out from its full faction board its most likely build order (which "
        "buildings first, by which round) and its main sources of VP. Then name the milestones in its development "
        "that you must watch for: the moments it becomes much stronger, faster or starts scoring (a key building, a "
        "hire, a fortress or artifact taken, its army outgrowing yours), with the round you expect each one, and "
        "how you will react.\n"
        "- the artifacts: for each one, what it costs you and what it gives you, and what it would give each opponent. "
        "Which ones to take yourself and when, which ones you must deny an opponent (take first or win in battle), "
        "and which ones are not worth it.\n"
        "You will keep this plan in mind for the rest of the game and adapt it as the game goes."
        + (" This is a good moment to use your tools: read your scripts' README, check numbers with scripts or "
           "simulations." if tools else ""),
        "FORMAT: answer — your plan as a string, terse bullet points, at most about 2500 characters. No separate "
        "analysis text is needed here: the plan is your answer.",
    ])


def reflection(game: Game, p: int, variant: str, opponents: dict[str, str], allowed: list[str], events: list[str],
               history: str, tools: bool, tool_report: str = "") -> str:
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
        "TOOL USAGE IN THIS GAME (an automatic report by the engine):\n" + tool_report,
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
        + f"\n3) RULES CLARIFICATIONS (optional): if this game showed you a gap in the rules text — something the engine "
        f"does or requires that the rules and your prompt do not say, or that they state unclearly — add it as a short, "
        f"precise statement of fact (at most {notes.RULES_MAX_ADD} items, each at most {notes.RULES_ITEM_LIMIT} characters) "
        "in \"rules_add\". Only confirmed facts you saw in this game, not strategy and not guesses. Items are appended to "
        "the special rules list that all your future games (of every Claude variant) get after the rules; you cannot delete old items, and the same item is not "
        "added twice."
        + f"\n4) MISSING TOOLS (required, may be empty): look at the tool usage report above and at the decisions of this "
        f"game. Which information would you have wanted to get with one quick call before a decision, but could not "
        f"(or had to compute by hand)? Name the tools in \"tools_missing\" — at most {notes.TOOLS_MAX_ADD} items, each "
        f"at most {notes.TOOLS_ITEM_LIMIT} characters: what it takes and what it returns, and for which decision it is "
        "needed. A developer reads this list and adds the tools to the engine. If there is nothing missing, send an "
        "empty list. Also say honestly if you had tools but did not use them and why."
        + ("\n5) SCRIPTS (optional): the tools you named that you can write yourself, write now as scripts in your "
           "workspace (they run with heroes-run on a copy of the game, see heroes/sim.py and heroes/agent_tools.py for "
           "the ready-made ones) and describe them in README.md; improve the old scripts too. They are saved for your "
           "future games." if tools else ""),
        'FORMAT (no separate analysis text is needed, put everything in the JSON): answer — {"review": "<your review>", '
        '"update": false, "summary": "..."} or {"review": "<your review>", "update": true, '
        '"sections": {"<section key>": "<new full text of the section>", ...}, "summary": "..."}. '
        "Optionally \"rules_add\": [\"<new rules clarification>\", ...] next to \"update\"; and always "
        "\"tools_missing\": [\"<tool>\", ...] (empty if none) and \"summary\" — what you changed in the notes and "
        "scripts and why, in 1–2 sentences (it becomes the git commit message). "
        "Plus rules_unclear — every rule you were unsure about or misunderstood during the game, if any.",
    ]
    return "\n\n".join(parts)
