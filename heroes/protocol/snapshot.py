"""Структурированное представление партии для графического клиента.

Всё — JSON-совместимые словари: их одинаково получает и интерфейс на компьютере хозяина,
и сетевой клиент. static_data — неизменное за партию (карта, планшеты фракций),
snapshot — текущее открытое состояние вместе с производными величинами (сила войск,
цены найма, стоимость перемещения, ПО), которые клиент сам посчитать не может.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..engine.actions import buildable
from ..engine.combat import affordable_upgrades, army_strength, lone_hydra
from ..engine.data import TERRAINS
from ..engine.economy import holdings
from ..engine.movement import reachable
from ..engine.effects import (Battle, active_effects, battle_bonus, effects_of, hire_cost, hireable_units, hydra_unit,
                              move_cost, preview_battle, unit_strength)
from ..engine.scoring import score_player
from ..engine.state import RESOURCE_VP, ROUNDS

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game


def movement_mods(faction) -> dict[str, int]:
    mods: dict[str, int] = {}
    for item in [*faction.buildings.values(), *faction.artifacts.values()]:
        for e in item.effects:
            if e["type"] == "set_move_cost":
                mods[e["terrain"]] = min(mods.get(e["terrain"], e["value"]), e["value"])
    return mods


def artifact_wood(faction) -> dict | None:
    """Гнездовье эльфов: артефакты за дерево (на планшете цена в дереве — через «/» после обычного условия)."""
    for bid, b in faction.buildings.items():
        for e in b.effects:
            if e["type"] == "artifact_for_wood":
                return {"building": bid, "unit": e["unit"], "prices": dict(e["prices"])}
    return None


UNIT_MODS = ("set_strength", "pay_to_upgrade_units", "terrain_strength", "fortress_strength")


def unit_mods(game: Game, p: int) -> dict[str, list[dict]]:
    """Во что здания, артефакты и свойства фракции могут превратить силу войск, как «1»2» на планшете:
    войско -> [{value, kind, active, terrain | cost}]. active — модификацию можно применить прямо сейчас:
    её источник есть, на неё хватает ресурсов, сражение здесь подходящее (горы, крепость)."""
    faction = game.faction_of(p)
    player = game.state.players[p]
    owned = [id(e) for e in active_effects(game, p)]
    region = player.region
    fortress_here = bool(region and game.map.regions[region].object == "fortress"
                         and region not in game.state.fortress_owner)
    sources = [*faction.rules.values(), *faction.buildings.values(), *faction.artifacts.values()]
    mods: dict[str, list[dict]] = {}
    for e in (e for item in sources for e in item.effects if e["type"] in UNIT_MODS):
        mine = id(e) in owned
        if e["type"] == "pay_to_upgrade_units":
            for unit, value in e["strength"].items():
                mods.setdefault(unit, []).append({"value": value, "kind": "pay", "cost": dict(e["cost_per_unit"]),
                                                  "active": mine and affordable_upgrades(game, p) > 0})
            continue
        mod = {"value": e["value"], "kind": {"set_strength": "set", "terrain_strength": "terrain",
                                             "fortress_strength": "fortress"}[e["type"]]}
        if e["type"] == "terrain_strength":
            mod["terrain"] = e["terrain"]
            mine = mine and bool(region) and game.terrain(region) == e["terrain"]
        elif e["type"] == "fortress_strength":
            mine = mine and fortress_here
        mods.setdefault(e["unit"], []).append({**mod, "active": mine})
    return mods


def faction_rules(faction) -> dict:
    """Особенности фракции, которые страница показывает на планшете: уникальные ресурсы с порогами ПО,
    шкала науки, ноты, гидра, войска-ресурс."""
    listed = [e for rule in faction.rules.values() for e in rule.effects]
    effects = {e["type"]: e for e in listed}
    return {
        "tokens": {e["resource"]: sorted([int(t), vp] for t, vp in e["vp_thresholds"].items())
                   for e in listed if e["type"] == "faction_resource"},
        "science": {"max": effects["science_track"]["max"],
                    "free_build": effects.get("free_build_by_science", {}).get("thresholds", {})}
        if "science_track" in effects else None,
        "notes": effects["notes"]["pairs"] if "notes" in effects else 0,
        "hydra": effects["hydra"]["unit"] if "hydra" in effects else None,
        "resource_units": sorted(faction.resource_units()),
        "no_resource_vp": "no_resource_vp" in effects,
        "curses": "curse_penalty" in effects,
    }


def static_data(game: Game) -> dict:
    regions = {}
    for name, r in game.map.regions.items():
        regions[name] = {
            "terrain": r.terrain, "min_players": r.min_players, "resources": dict(r.resources), "object": r.object,
            "fortress": {"strength": r.fortress.strength, "vp": r.fortress.vp, "loot": dict(r.fortress.loot)}
            if r.fortress else None,
            "artifact": r.artifact,
        }
    factions = {}
    for fid, f in game.factions.items():
        factions[fid] = {
            "name": f.name,
            "movement": dict(f.movement),
            # во что здания и артефакты фракции могут превратить стоимость перемещения (как «2»1» на планшете)
            "movement_mods": movement_mods(f),
            "units": {u.id: {"name": u.name, "strength": u.strength, "cost": dict(u.cost)} for u in f.units.values()},
            "buildings": {b.id: {"name": b.name, "level": b.level, "vp": b.vp, "cost": dict(b.cost),
                                 "requires": list(b.requires), "text": b.text, "text_ru": b.text_ru or b.text,
                                 "text_board": b.text_board or b.text_ru or b.text,
                                 "hires": [e["unit"] for e in b.effects if e["type"] == "hire"]}
                          for b in f.buildings.values()},
            "building_layout": [list(row) for row in f.building_layout],   # клетки зданий, как на печатном планшете
            "artifacts": {region: a.text for region, a in f.artifacts.items()},
            "artifacts_ru": {region: a.text_ru or a.text for region, a in f.artifacts.items()},
            "artifacts_board": {region: a.text_board or a.text_ru or a.text for region, a in f.artifacts.items()},
            # свойства фракции (блоки планшета, не здания) и её особенности
            "rules": {r.id: {"name": r.name, "text": r.text, "text_ru": r.text_ru or r.text,
                             "text_board": r.text_board or r.text_ru or r.text,
                             # модификация войск за ресурс (орки): цены, которых нет в тексте свойства
                             "unit_upgrades": [{"from": x["from"], "to": x["to"], "cost": dict(x["cost"])}
                                               for e in r.effects if e["type"] == "upgrade_units_on_build" for x in e["paths"]],
                             # только пороги ПО уникального ресурса (ярость орков): их показывает склад
                             "in_stock": all(e["type"] == "faction_resource" for e in r.effects)}
                      for r in f.rules.values()},
            "monsters": [{"strength": m.strength, "vp": m.vp, "loot": dict(m.loot)} for m in f.monsters]
            if f.monsters else None,   # своя линия чудовищ (наги) вместо общей
            "artifact_conditions": dict(f.artifact_conditions),   # свои условия артефактов (наги)
            "artifact_wood": artifact_wood(f),   # артефакты за дерево с Гнездовьем (эльфы)
            **faction_rules(f),
        }
    return {
        "rounds": ROUNDS,
        "names": [p.name for p in game.state.players],
        "player_factions": [p.faction for p in game.state.players],
        "kinds": list(game.meta.get("players") or ["human"] * len(game.state.players)),  # human/remote/claude/bot
        "first_player": game.initial_first_player,   # первый игрок первого раунда: от него считается очерёдность
        "game_id": game.meta["game_id"],
        "avatars": [game.avatars.get(p) for p in range(len(game.state.players))],   # PNG в base64 или None
        "regions": regions,
        "adjacent": {name: sorted(nbrs) for name, nbrs in game.map.adjacent.items()},
        "monsters": [{"strength": m.strength, "vp": m.vp, "loot": dict(m.loot)} for m in game.map.monsters],
        "factions": factions,
        "resource_vp": [list(t) for t in RESOURCE_VP],
    }


def thinking(game: Game) -> list[list]:
    """Кто сейчас принимает решение: [[игрок, вид решения], …]."""
    with game.thinking_lock:
        return sorted([p, kind] for p, kind in game.thinking.items())


def snapshot(game: Game) -> dict:
    """Открытое состояние партии (по правилу открытости его видят все)."""
    st = game.state
    players = []
    for p, pl in enumerate(st.players):
        faction = game.faction_of(p)
        hireable = hireable_units(game, p)
        mods = unit_mods(game, p)
        players.append({
            "name": pl.name,
            "faction": pl.faction,
            "region": pl.region,
            "resources": dict(pl.resources),
            "units": {uid: {"count": pl.units.get(uid, 0), "strength": unit_strength(game, p, uid),
                            "hire_cost": hire_cost(game, p, uid), "hireable": uid in hireable,
                            "mods": mods.get(uid, [])}
                      for uid in faction.units},
            "army_strength": army_strength(game, p, pl.units, preview_battle(game, p)),
            "built": list(pl.built),
            "buildable": buildable(game, p, 4),
            "foundations": dict(pl.foundations),
            "visited": list(pl.visited),
            "routes": [list(r) for r in pl.routes],
            "monsters": list(pl.monsters),
            "resource_marks": {k: list(v) for k, v in pl.resource_marks.items()},
            "fortresses": list(pl.fortresses),
            "victories": pl.victories,
            "artifacts": st.artifacts_of(p),
            "move_cost": {t: move_cost(game, p, t) for t in TERRAINS},
            "score": score_player(game, p),
            # особенности фракций
            "holdings": holdings(game, p),   # чем можно платить: склад, ярость, наука, краки
            "tokens": dict(pl.tokens),
            "notes": list(pl.notes),
            "hydra": pl.hydra,
            "hydra_summons": pl.hydra_summons,
            "mountains": list(pl.mountains),
            "move_points": pl.move_points,
            "reach": dict(game.reach.get(p, {})),   # пока выбирают, куда идти: регион -> цена в ОП
        })
    return {
        "round": st.round,
        "first_player": st.first_player,
        "action_order": list(st.action_order),
        "phase": st.phase,
        "thinking": thinking(game),
        "available": list(st.available),
        "fortress_owner": dict(st.fortress_owner),
        "artifact_holder": dict(st.artifact_holder),
        "reserved": dict(st.reserved),
        "cursed": dict(st.cursed),       # проклятый регион -> кто проклял (нежить)
        "terrain": dict(st.terrain),     # изменённая местность (Гейзер наг)
        "players": players,
    }


def safe_snapshot(game: Game, attempts: int = 5) -> dict:
    """Снимок из чужого потока: движок может как раз менять словари состояния — тогда повторить."""
    for _ in range(attempts - 1):
        try:
            return snapshot(game)
        except RuntimeError:  # dictionary changed size during iteration
            continue
    return snapshot(game)


def decision_data(game: Game, decision: Decision) -> dict:
    """Данные решения плюс то, что интерфейсу нужно для подсказок, а посчитать может только движок."""
    data = dict(decision.data)
    p = decision.player
    if decision.kind in ("commit_army", "capture_fortress", "fight_monster"):
        kind = {"commit_army": "pvp", "capture_fortress": "fortress", "fight_monster": "monster"}[decision.kind]
        battle = Battle(kind, data.get("region") or game.state.players[p].region)
        data["strength_rules"] = strength_rules(game, p, battle)
        if decision.kind == "commit_army":
            data["opponent_rules"] = {q: strength_rules(game, int(q), battle) for q in data["opponents"]}
        if decision.kind == "fight_monster" and lone_hydra(game, p):
            data["hydra_alone"] = {"unit": hydra_unit(game, p), "region": lone_hydra(game, p)}
    if decision.kind == "action_order":
        data["reach"] = move_preview(game)
    return data


def move_preview(game: Game) -> dict[str, dict[str, dict[str, int]]]:
    """Пока первый игрок выбирает порядок действий: куда каждый игрок сможет дойти, если перемещение встанет
    на место (уровень) 1–4, — игрок -> уровень -> регион -> цена в ОП. ОП — уровень и постоянные прибавки
    (Обсерватория); то, что докупают или поднимают в самом перемещении (Катафалк, Дом вождей), не учтено."""
    preview = {}
    for p, pl in enumerate(game.state.players):
        if not pl.region:
            continue
        extra = sum(e["value"] for e in effects_of(game, p, "extra_move_points"))
        preview[str(p)] = {str(level): reachable(game, p, level + extra) for level in range(1, 5)}
    return preview


def strength_rules(game: Game, p: int, battle: Battle) -> dict:
    """Как считать силу заявки (js/common.js armyStrength повторяет effects.army_strength): прибавка к итогу,
    войска, чья сила — их число в сражении (краки), и войска, удваивающие силу других (механики).
    Модификации Лавки бронника сюда не входят: их покупают отдельным решением после заявки."""
    return {"bonus": battle_bonus(game, p, battle),
            "count_units": [e["unit"] for e in effects_of(game, p, "strength_equals_count")],
            "doublers": {e["unit"]: e["targets"] for e in effects_of(game, p, "double_unit_strength")}}


def decision_message(game: Game, decision: Decision, request_id: int) -> dict:
    return {"t": "decision", "id": request_id, "player": decision.player, "kind": decision.kind,
            "text": decision.text, "data": decision_data(game, decision), "state": snapshot(game)}


def sync_message(game: Game, you: int | None) -> dict:
    """Всё, что нужно клиенту, чтобы показать партию с нуля (при подключении и после загрузки)."""
    return {"t": "sync", "you": you, "static": static_data(game), "log": list(game.state.log),
            "state": safe_snapshot(game)}
