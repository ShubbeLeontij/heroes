"""JSON-схемы ответов Claude по типам решений и перевод ответа в формат движка.

Ответ Claude всегда {"answer": ..., "comment": "замысел в 1–2 фразах"} и, по желанию,
"rules_unclear" — что ему непонятно в правилах (это попадает в журнал путаницы для человека).

CLI получает одну и ту же схему RESPONSE_SCHEMA на все вызовы: --json-schema превращается
в инструмент, а инструменты стоят в самом начале запроса, так что разная схема на каждый
вопрос сбрасывала бы кэш промпта и заставляла заново оплачивать всю историю партии.
Точная схема answer для конкретного решения передаётся текстом в вопросе, а строгую
проверку делает движок. «Отказ» задаётся явным полем (attack: false, build: "none").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ..engine.actions import DONE, upgrade_paths
from ..engine.data import RESOURCES
from ..engine.economy import fmt_res, stock
from ..engine.effects import effects_of, has_effect, source_name
from ..engine.state import ACTIONS

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game

NONE = "none"


@dataclass
class Spec:
    schema: dict            # схема поля answer
    instructions: str       # как заполнять answer (показывается в вопросе)
    convert: Callable[[Any], Any] = lambda a: a  # answer Claude -> ответ движку


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def counts(keys) -> dict:
    return obj({k: {"type": "integer", "minimum": 0} for k in keys})


def enum(values) -> dict:
    return {"type": "string", "enum": list(values)}


RESPONSE_SCHEMA = obj({
    "answer": {"type": ["object", "array", "string", "integer", "boolean"]},
    "comment": {"type": "string"},
    "rules_unclear": {"type": "string"},   # необязательно: что в правилах непонятно (журнал для человека)
    # необязательно: сначала посмотреть полные планшеты игроков (номера или имена), ответ тогда не засчитывается
    "view_boards": {"type": "array", "items": {"type": ["integer", "string"]}},
}, required=["answer", "comment"])


def spec_for(game: Game, d: Decision) -> Spec:
    data = d.data
    kind = d.kind
    if kind in ("choose_start", "region_stance"):
        return Spec(enum(data["options"]), "answer — one of the options: " + ", ".join(data["options"]))
    if kind == "destination":
        return Spec(enum(data["options"]), "answer — the destination region name (your current region means staying put)")
    if kind == "action_order":
        return Spec({"type": "array", "items": enum(ACTIONS), "minItems": 4, "maxItems": 4},
                    "answer — a list of 4 distinct actions in execution order (the first gets level 1, "
                    "the fourth level 4): wood, iron, gold, move, build")
    if kind == "distribute_resources":
        return Spec(counts(RESOURCES), f"answer — {{\"wood\": n, \"iron\": n, \"gold\": n}} summing to exactly {data['amount']}")
    if kind == "build_recruit":
        return build_spec(game, d)
    if kind == "reserve_object":
        return Spec(enum([*data["options"], NONE]), f"answer — the region whose object you reserve, or \"{NONE}\"",
                    lambda a: None if a == NONE else a)
    if kind == "commit_army":
        return Spec(counts(data["units"]), "answer — how many units of each type to commit (0 — none)",
                    lambda a: {u: n for u, n in a.items() if n})
    if kind == "capture_fortress":
        return Spec(obj({"attack": {"type": "boolean"}, "army": counts(data["units"])}),
                    "answer.attack — whether to attack; answer.army — units to commit (ignored when attack=false)",
                    lambda a: {u: n for u, n in a["army"].items() if n} if a["attack"] else None)
    if kind == "fight_monster":
        candidates = [int(i) for i in data["candidates"]]
        return Spec(
            obj({"fight": {"type": "boolean"}, "monster": {"type": "integer", "enum": candidates},
                 "army": counts(data["units"])}),
            "answer.fight — whether to fight; answer.monster — the monster number from the list; answer.army — units to commit",
            lambda a: {"monster": a["monster"], "army": {u: n for u, n in a["army"].items() if n}} if a["fight"] else None,
        )
    if kind == "take_artifact":
        return Spec(obj({"take": {"type": "boolean"}}), "answer.take — whether to take the artifact", lambda a: a["take"])
    if kind == "choose_reward":
        return reward_spec(data["options"])
    return faction_spec(kind, data)


def faction_spec(kind: str, data: dict) -> Spec:
    """Особые решения фракций (нежить, гномы, орки, эльфы, наги)."""
    if kind in ("hydra_destination", "summon_hydra", "circle_note"):
        hint = {"hydra_destination": "the region your hydra moves to (its current region means staying)",
                "summon_hydra": "the lake region to summon the hydra into",
                "circle_note": "open (start a new pair) or close (complete a started pair and summon the hydra)"}[kind]
        return Spec(enum(data["options"]), f"answer — {hint}")
    if kind == "curse_region":
        if data["optional"]:
            return Spec(enum([*data["options"], NONE]), f"answer — the region to curse, or \"{NONE}\" to decline",
                        lambda a: None if a == NONE else a)
        return Spec(enum(data["options"]), "answer — the region to curse")
    if kind in ("make_lake", "exchange_on_gain"):
        return Spec({"type": "boolean"}, "answer — true (yes) or false (no)")
    if kind in ("buy_move_points", "battle_upgrade"):
        what = "MP to buy" if kind == "buy_move_points" else "units to upgrade"
        return Spec({"type": "integer", "minimum": 0, "maximum": data["max"]},
                    f"answer — how many {what}, from 0 to {data['max']}")
    if kind == "raise_action":
        return Spec(counts(data["options"]),
                    f"answer — levels to add to each action (0 — none), at most {data['max']} in total",
                    lambda a: {k: v for k, v in a.items() if v})
    if kind == "avoid_battle":
        return Spec(obj({"avoid": {"type": "boolean"}, "pay": counts(RESOURCES)}),
                    f"answer.avoid — whether to pay to cancel the battle; answer.pay — which resources to pay, exactly "
                    f"{data['amount']} in total (ignored when avoid=false)",
                    lambda a: {k: v for k, v in a["pay"].items() if v} if a["avoid"] else None)
    raise ValueError(f"No schema for decision {kind}")


def build_spec(game: Game, d: Decision) -> Spec:
    """Шаги действия строительства. Войска, пути модификации и действия Дома вождей перечислены
    все, а не только доступные сейчас: доступность может появиться от предыдущего шага того же ответа."""
    p = d.player
    faction = game.faction_of(p)
    steps: list[dict] = [enum([DONE])]
    parts = []
    if not d.data["built"]:
        steps.append(obj({"build": enum(faction.buildings)}))
        parts.append('{"build": "<building id>"} — build one building (at most one per action, its level at most '
                     "the action level, required buildings built, affordable)")
    steps.append(obj({"hire": counts(faction.units)}))
    parts.append('{"hire": {"<unit>": n, ...}} — hire units (only into armies whose hiring building is already built, '
                 "including by an earlier step; 0 — none)")
    paths = upgrade_paths(game, p)
    if paths:
        steps.append(obj({"upgrade": counts(paths)}))
        parts.append('{"upgrade": {"<from>><to>": n, ...}} — upgrade units for rage (paths applied in the listed order)')
    if has_effect(game, p, "pay_to_raise_action"):
        steps.append(obj({"raise": counts(["build", *RESOURCES])}))
        parts.append('{"raise": {"<action>": levels, ...}} — Дом вождей: raise this build action (before building) '
                     "or a gathering action already performed this round; others must be 0")
    if has_effect(game, p, "buy_science_on_build"):
        steps.append(obj({"science": {"type": "boolean"}}))
        parts.append('{"science": true} — Библиотека: buy science (once per action)')
    instructions = (f'answer — a list of steps performed in this order; end it with "{DONE}" when you have finished '
                    "this build action. Steps: " + "; ".join(parts) + ". Each step takes effect immediately, so later "
                    f'steps may use what earlier ones gave. Without "{DONE}" you are asked again with the new state. '
                    "If a step turns out impossible, the steps before it stay done and you are asked again with the reason")

    def convert(a):
        result = []
        for step in a:
            if step == DONE:
                result.append(DONE)
                continue
            (key, value), = step.items()
            if isinstance(value, dict):
                value = {k: n for k, n in value.items() if n}
            if value:            # пустой наём, «science: false» — не шаг
                result.append({key: value})
        return result
    return Spec({"type": "array", "items": {"anyOf": steps}}, instructions, convert)


def reward_spec(options: dict) -> Spec:
    props: dict[str, Any] = {"type": enum(options)}
    parts = ["answer.type — the reward kind: " + ", ".join(options)]
    if "resources" in options:
        props["take"] = obj({q: counts(RESOURCES) for q in options["resources"]["from"]})
        parts.append(f"with type=resources answer.take — how many resources to take from each loser "
                     f"(key — player number), at most {options['resources']['amount']} in total")
    if "artifact" in options:
        pairs = {q: arts for q, arts in options["artifact"].items()}
        props["from"] = enum(pairs)
        props["artifact"] = enum(sorted({a for arts in pairs.values() for a in arts}))
        parts.append("with type=artifact answer.from — the loser's player number, answer.artifact — the artifact's region")

    def convert(a):
        if a["type"] == "resources":
            take = {q: {k: v for k, v in r.items() if v} for q, r in a.get("take", {}).items()}
            return {"type": "resources", "take": {q: r for q, r in take.items() if r}}
        if a["type"] == "artifact":
            return {"type": "artifact", "from": a.get("from"), "artifact": a.get("artifact")}
        return {"type": a["type"]}
    return Spec(obj(props, required=["type"]), "; ".join(parts), convert)


# --- составные ответы ---------------------------------------------------------------------------

def nested(spec: Spec, key: str) -> Spec:
    """Тот же ответ, но в поле answer.<key> составного ответа."""
    return Spec(spec.schema, re.sub(r"\banswer\b", f"answer.{key}", spec.instructions), spec.convert)


def combined(specs: dict[str, Spec]) -> Spec:
    """Ответ на несколько вопросов сразу: answer.<key> — ответ на каждый; convert даёт словарь ответов движку."""
    return Spec(obj({k: s.schema for k, s in specs.items()}),
                "; ".join(nested(s, k).instructions for k, s in specs.items()),
                lambda a: {k: s.convert(a[k]) for k, s in specs.items()})


UPGRADE_FOLLOWS = ("commit_army", "capture_fortress", "fight_monster")


def with_upgrade(game: Game, d: Decision, spec: Spec) -> Spec | None:
    """Лавка бронника эльфов: сколько заявленных единиц модифицировать, Claude называет вместе с заявкой
    (answer.upgrade), а convert кладёт это в ответ движку (поле upgrade). None — модифицировать нечего или не на что."""
    if d.kind not in UPGRADE_FOLLOWS:
        return None
    for e in effects_of(game, d.player, "pay_to_upgrade_units"):
        key, price = next(iter(e["cost_per_unit"].items()))
        most = stock(game, d.player, key) // price
        units = {u: (v["strength"], e["strength"][u]) for u, v in d.data["units"].items()
                 if e["strength"].get(u, 0) > v["strength"]}
        if not units or not most:
            continue
        upgrade = Spec({"type": "integer", "minimum": 0},
                       f"answer.upgrade — {source_name(game, d.player, 'pay_to_upgrade_units')}: how many of the "
                       f"committed units to upgrade for this battle, {fmt_res(e['cost_per_unit'])} each (you can afford "
                       f"{most}); the units that gain most are upgraded: "
                       + ", ".join(f"{u} {a}→{b}" for u, (a, b) in units.items())
                       + ". The engine caps it at what the committed units can use; 0 — none")
        both = combined({"commit": spec})

        def convert(a, most=most):
            answer, n = spec.convert(a["commit"]), min(check_count(a["upgrade"]), most)
            if answer is None:
                return None   # отказ от штурма или боя
            if d.kind == "fight_monster":
                return {**answer, "upgrade": n}
            return {"army": answer, "upgrade": n}
        return Spec(obj({"commit": spec.schema, "upgrade": upgrade.schema}),
                    f"{both.instructions}; {upgrade.instructions}", convert)
    return None


def check_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"expected a non-negative integer, got {value!r}")
    return value
