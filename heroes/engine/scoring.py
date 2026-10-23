"""Подсчёт победных очков после 16-го раунда."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .data import ONE_TIME_EFFECTS, RESOURCES
from .economy import token_vp
from .effects import has_effect, science
from .state import RESOURCE_VP

if TYPE_CHECKING:
    from .game import Game

CATEGORIES = {
    "buildings": "buildings & effects",
    "foundations": "foundations",
    "resources": "stockpiled resources",
    "battles": "battle victories",
    "artifacts": "artifacts",
}


def effect_vp(game: Game, p: int, e: dict) -> int:
    """ПО в конце игры от одного эффекта здания, артефакта или свойства фракции."""
    st = game.state
    player = st.players[p]
    kind = e["type"]
    if kind == "vp_per_circled_monster":
        return e["value"] * sum(1 for m in player.monsters if m)
    if kind == "vp_per_built_foundation":
        return e["value"] * sum(1 for level in player.foundations.values() if level)
    if kind == "vp_per_fortress":
        return e["value"] * len(player.fortresses)
    if kind == "vp_per_artifact":
        return e["value"] * len(st.artifacts_of(p))
    if kind == "vp_per_visited_regions":
        return e["value"] * (len(player.visited) // e["per"])
    if kind == "vp_per_unit":
        return e["value"] * player.units.get(e["unit"], 0)
    if kind == "vp_per_science_threshold":
        return e["value"] * sum(1 for t in e["thresholds"] if science(game, p) >= t)
    if kind == "vp_per_hydra_summon":
        return e["value"] * player.hydra_summons
    if kind == "vp_per_terrain_region":
        return e["value"] * sum(1 for r in st.available if game.terrain(r) == e["terrain"])
    if kind == "vp_per_cursed_region":
        return e["value"] * sum(1 for q in st.cursed.values() if q == p)
    if kind == "vp_flat":
        return e["value"]
    return 0


def score_player(game: Game, p: int) -> dict[str, int]:
    player = game.state.players[p]
    faction = game.faction_of(p)
    line = game.monster_line(p)
    circled = [i for i, m in enumerate(player.monsters) if m]
    thresholds = {res: dict(RESOURCE_VP) for res in RESOURCES} | token_vp(game, p)

    buildings = sum(faction.buildings[b].vp + sum(effect_vp(game, p, e) for e in faction.buildings[b].effects)
                    for b in player.built)
    # Нежить не получает ПО за артефакты: вместо них в этой ячейке — ПО за проклятые регионы.
    artifacts = 0 if has_effect(game, p, "no_artifact_vp") else sum(
        effect_vp(game, p, e) for region in game.state.artifacts_of(p) for e in faction.artifacts[region].effects
        if e["type"] not in ONE_TIME_EFFECTS)
    artifacts += sum(effect_vp(game, p, e) for rule in faction.rules.values() for e in rule.effects)
    result = {
        "buildings": buildings,
        "foundations": sum(player.foundations.values()),
        "resources": 0 if has_effect(game, p, "no_resource_vp") else
        sum(thresholds[res][t] for res, marks in player.resource_marks.items() for t in marks),
        "battles": sum(game.map.regions[r].fortress.vp for r in player.fortresses) + sum(line[i].vp for i in circled),
        "artifacts": artifacts,
    }
    result["total"] = sum(result.values())
    return result


def final_scores(game: Game) -> list[dict[str, int]]:
    return [score_player(game, p) for p in range(len(game.state.players))]


def winners(game: Game, scores: list[dict[str, int]]) -> list[int]:
    """Больше всего ПО; при ничьей — больше ресурсов на складе; если ничья сохраняется — все."""
    best = max(s["total"] for s in scores)
    leaders = [p for p, s in enumerate(scores) if s["total"] == best]
    stock = {p: sum(game.state.players[p].resources.values()) for p in leaders}
    return [p for p in leaders if stock[p] == max(stock.values())]
