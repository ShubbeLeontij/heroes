"""JSON-схемы ответов Claude по типам решений и перевод ответа в формат движка.

Ответ Claude всегда {"answer": ..., "comment": "замысел в 1–2 фразах"}.

CLI получает одну и ту же схему RESPONSE_SCHEMA на все вызовы: --json-schema превращается
в инструмент, а инструменты стоят в самом начале запроса, так что разная схема на каждый
вопрос сбрасывала бы кэш промпта и заставляла заново оплачивать всю историю партии.
Точная схема answer для конкретного решения передаётся текстом в вопросе, а строгую
проверку делает движок. «Отказ» задаётся явным полем (attack: false, build: "none").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ..engine.data import RESOURCES
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
})


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
        units = list(game.faction_of(d.player).units)
        return Spec(
            obj({"build": enum([*data["options"], NONE]), "hire": counts(units)}),
            f"answer.build — a building id from the list or \"{NONE}\"; answer.hire — how many of each unit to hire "
            f"({', '.join(units)}; 0 — none). You can only hire units whose hiring building is built "
            "(counting the one being built now); hiring is paid after the building.",
            lambda a: {"build": None if a["build"] == NONE else a["build"],
                       "hire": {u: n for u, n in a["hire"].items() if n}},
        )
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
    if kind in ("make_lake", "buy_science", "exchange_on_gain"):
        return Spec({"type": "boolean"}, "answer — true (yes) or false (no)")
    if kind in ("buy_move_points", "battle_upgrade"):
        what = "MP to buy" if kind == "buy_move_points" else "units to upgrade"
        return Spec({"type": "integer", "minimum": 0, "maximum": data["max"]},
                    f"answer — how many {what}, from 0 to {data['max']}")
    if kind == "raise_action":
        return Spec(counts(data["options"]),
                    f"answer — levels to add to each action (0 — none), at most {data['max']} in total",
                    lambda a: {k: v for k, v in a.items() if v})
    if kind == "upgrade_units":
        return Spec(counts(data["paths"]), "answer — how many units to upgrade along each path (0 — none)",
                    lambda a: {k: v for k, v in a.items() if v})
    if kind == "avoid_battle":
        return Spec(obj({"avoid": {"type": "boolean"}, "pay": counts(RESOURCES)}),
                    f"answer.avoid — whether to pay to cancel the battle; answer.pay — which resources to pay, exactly "
                    f"{data['amount']} in total (ignored when avoid=false)",
                    lambda a: {k: v for k, v in a["pay"].items() if v} if a["avoid"] else None)
    raise ValueError(f"No schema for decision {kind}")


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
