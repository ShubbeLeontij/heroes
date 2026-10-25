"""Производные характеристики игрока с учётом построенных зданий, артефактов и свойств фракции.

Постоянные эффекты не хранятся в состоянии, а вычисляются: так потеря артефакта
(отъём в бою) автоматически отменяет его постоянный эффект.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .data import ONE_TIME_EFFECTS

if TYPE_CHECKING:
    from .game import Game


@dataclass(frozen=True)
class Battle:
    """Сражение, для которого считается сила: kind — pvp / monster / fortress, region — где оно идёт."""
    kind: str
    region: str


def active_effects(game: Game, p: int) -> list[dict]:
    """Эффекты под звёздочкой (unit_property) — свойства войск: действуют, даже если здание не построено
    (правила, с. 4)."""
    faction = game.faction_of(p)
    built = game.state.players[p].built
    effects = [e for rule in faction.rules.values() for e in rule.effects]
    effects += [e for bid, b in faction.buildings.items() for e in b.effects if bid in built or e.get("unit_property")]
    for region in game.state.artifacts_of(p):
        effects += [e for e in faction.artifacts[region].effects if e["type"] not in ONE_TIME_EFFECTS]
    return effects


def effects_of(game: Game, p: int, effect_type: str) -> list[dict]:
    return [e for e in active_effects(game, p) if e["type"] == effect_type]


def has_effect(game: Game, p: int, effect_type: str) -> bool:
    return bool(effects_of(game, p, effect_type))


def source_name(game: Game, p: int, effect_type: str) -> str:
    """Название здания, свойства или артефакта, дающего эффект, — для текстов вопросов и журнала."""
    faction = game.faction_of(p)
    built = game.state.players[p].built
    for bid, b in faction.buildings.items():
        if any(e["type"] == effect_type and (bid in built or e.get("unit_property")) for e in b.effects):
            return b.name
    for rule in faction.rules.values():
        if any(e["type"] == effect_type for e in rule.effects):
            return rule.name
    for region in game.state.artifacts_of(p):
        if any(e["type"] == effect_type for e in faction.artifacts[region].effects):
            return f"{region} artifact"
    return effect_type


def science(game: Game, p: int) -> int:
    return game.state.players[p].tokens.get("science", 0)


def hydra_unit(game: Game, p: int) -> str | None:
    """Вид войск-гидра фракции (наги) или None."""
    for rule in game.faction_of(p).rules.values():
        for e in rule.effects:
            if e["type"] == "hydra":
                return e["unit"]
    return None


def unit_strength(game: Game, p: int, unit: str, battle: Battle | None = None,
                  army: dict[str, int] | None = None) -> int:
    """Сила одной единицы. battle — сражение, если сила от него зависит (горы, крепость);
    army — заявленные войска (сила крака равна числу краков в сражении)."""
    player = game.state.players[p]
    effects = [e for e in active_effects(game, p) if e.get("unit") == unit]
    value = game.faction_of(p).units[unit].strength
    for e in effects:  # сначала замены значения, потом прибавки
        if e["type"] == "set_strength":
            value = e["value"]
        elif e["type"] == "strength_equals_move_points":
            value = player.move_points
        elif e["type"] == "strength_equals_count":
            value = (army if army is not None else player.units).get(unit, 0)
    for e in effects:
        if e["type"] == "strength_per_science":
            value += science(game, p) // e["per"] * e["value"]
        elif e["type"] == "terrain_strength" and battle and game.terrain(battle.region) == e["terrain"]:
            value = max(value, e["value"])
        elif e["type"] == "fortress_strength" and battle and battle.kind == "fortress":
            value = max(value, e["value"])
        elif e["type"] == "hydra" and player.hydra == player.region and not has_effect(game, p, "no_krak_hydra_penalty"):
            # гидра рядом с нагой слабеет за каждого крака в войске, заявлен он или нет
            krak = next((x["unit"] for x in effects_of(game, p, "krak_exchange")), None)
            value = max(0, value - e["krak_penalty"] * player.units.get(krak, 0))
    return value


def battle_bonus(game: Game, p: int, battle: Battle | None) -> int:
    """Прибавка к итоговой силе войск (Горн, Металка, артефакт Золена эльфов); действует и при нулевой силе."""
    if battle is None:
        return 0
    return sum(e["value"] for e in effects_of(game, p, "battle_strength_bonus")
               if e["against"] == "any" or e["against"] == battle.kind)


def upgrade_gains(game: Game, p: int, army: dict[str, int], battle: Battle | None) -> list[int]:
    """Лавка бронника: насколько вырастет сила каждой заявленной единицы, если её модифицировать (по убыванию)."""
    gains = []
    for e in effects_of(game, p, "pay_to_upgrade_units"):
        for unit, n in army.items():
            if unit in e["strength"]:
                gain = e["strength"][unit] - unit_strength(game, p, unit, battle, army)
                gains += [gain] * n if gain > 0 else []
    return sorted(gains, reverse=True)


def army_strength(game: Game, p: int, army: dict[str, int], battle: Battle | None = None, upgrades: int = 0) -> int:
    """Итоговая сила заявленных войск. upgrades — сколько единиц модифицировано Лавкой бронника
    (берутся те, кому это даёт больше всего); механики удваивают силу самых сильных единиц."""
    doublers = {e["unit"]: e["targets"] for e in effects_of(game, p, "double_unit_strength")}
    values = sorted((unit_strength(game, p, unit, battle, army) for unit, n in army.items() if unit not in doublers
                     for _ in range(n)), reverse=True)
    total = sum(values) + sum(upgrade_gains(game, p, army, battle)[:upgrades])
    boosts = sum(army.get(unit, 0) * targets for unit, targets in doublers.items())
    total += sum(values[:boosts])  # одну единицу усиливает только один механик
    total += sum(unit_strength(game, p, unit, battle, army) * army.get(unit, 0) for unit in doublers)
    return total + battle_bonus(game, p, battle)


def hire_cost(game: Game, p: int, unit: str, sci: int | None = None) -> dict[str, int]:
    """sci — наука гномов, при которой считать скидку Конвейера (по умолчанию — нынешняя)."""
    cost = dict(game.faction_of(p).units[unit].cost)
    for e in effects_of(game, p, "set_hire_cost"):
        if e["unit"] == unit:
            cost = dict(e["cost"])
    for e in effects_of(game, p, "hire_discount_per_science"):  # Конвейер гномов
        if unit not in e.get("except", ()):
            steps = (science(game, p) if sci is None else sci) // e["per"]
            for k, v in e["discount"].items():
                cost[k] = max(0, cost.get(k, 0) - v * steps)
    return {k: v for k, v in cost.items() if v}


def building_cost(game: Game, p: int, bid: str) -> dict[str, int]:
    """Цена здания для игрока: наука гномов делает здания 2–4 уровня бесплатными, эльфы не тратят дерево
    (Лесистый утёс) или золото (артефакт Шог Ида)."""
    building = game.faction_of(p).buildings[bid]
    for e in effects_of(game, p, "free_build_by_science"):
        threshold = e["thresholds"].get(str(building.level))
        if threshold is not None and science(game, p) >= threshold:
            return {}
    cost = dict(building.cost)
    for e in effects_of(game, p, "free_build_resource"):
        cost.pop(e["resource"], None)
    return cost


def hireable_units(game: Game, p: int) -> list[str]:
    return [e["unit"] for e in effects_of(game, p, "hire")]


def move_cost(game: Game, p: int, terrain: str) -> int:
    value = game.faction_of(p).movement[terrain]
    for e in effects_of(game, p, "set_move_cost"):
        if e["terrain"] == terrain:
            value = min(value, e["value"])
    return value


def step_cost(game: Game, p: int, region: str) -> int:
    """Сколько ОП стоит войти в регион: по его местности (с учётом Гейзера) и объекту (Таскар нежити)."""
    value = move_cost(game, p, game.terrain(region))
    for e in effects_of(game, p, "move_cost_to_object"):
        if game.map.regions[region].object == e["object"]:
            value = min(value, e["value"])
    return value
