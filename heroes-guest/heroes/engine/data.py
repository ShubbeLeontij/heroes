"""Загрузка и проверка статических данных: карта, линия чудовищ, планшеты фракций."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

RESOURCES = ("wood", "iron", "gold")
TERRAINS = ("field", "forest", "mountain", "lake")
OBJECT_TYPES = ("fortress", "foundation", "portal", "artifact")
# Уникальные ресурсы фракций: ярость орков, наука гномов. Хранятся отдельно от склада
# (PlayerState.tokens): соперники не могут их забрать, а склад окна знает только три ресурса.
TOKENS = ("rage", "science")
ARTIFACT_CONDITIONS = ("pay", "foundationsBuilt", "battleWins", "mountainsVisited")
EFFECT_TYPES = (
    # люди
    "hire", "set_strength", "set_hire_cost", "set_move_cost",
    "on_new_region", "on_monster_win", "vp_per_circled_monster", "reserve_object",
    "wood_substitution", "gain_units", "gain", "vp_per_built_foundation", "vp_per_fortress",
    # общие для нескольких фракций
    "battle_strength_bonus", "vp_per_artifact", "vp_per_visited_regions", "vp_flat",
    # нежить
    "curse_penalty", "vp_per_cursed_region", "no_artifact_vp", "resurrect_after_battle", "buy_move_points",
    "fight_any_monster_if_stayed", "strength_equals_move_points", "exchange_on_gain", "curse_on_build",
    "vp_per_unit", "resurrect_if_present", "move_cost_to_object", "curse_regions",
    # гномы
    "science_track", "free_build_by_science", "on_built_science_per_unit", "double_unit_strength",
    "on_built_gain", "buy_science_on_build", "hire_discount_per_science", "science_per_hire",
    "vp_per_science_threshold", "strength_per_science", "gain_science",
    # орки
    "faction_resource", "upgrade_units_on_build", "on_battle_win", "on_foundation_build", "on_hire",
    "terrain_strength", "pay_to_raise_action", "fortress_strength", "on_moved_gain", "on_artifact_gain",
    # эльфы
    "pay_to_upgrade_units", "artifact_for_wood", "free_build_resource", "extra_move_points", "bonus_on_gather",
    # наги
    "krak_exchange", "strength_equals_count", "no_resource_vp", "hydra", "notes", "hydra_move",
    "vp_per_hydra_summon", "vp_per_terrain_region", "pay_to_avoid_battle", "make_lake", "note_on_artifact",
    "krak_exchange_rates", "no_krak_hydra_penalty",
)
# Эффекты, срабатывающие один раз при получении артефакта (и повторно при его возврате).
ONE_TIME_EFFECTS = ("gain_units", "gain", "curse_regions", "gain_science")


class DataError(ValueError):
    pass


@dataclass(frozen=True)
class Fortress:
    strength: int
    vp: int
    loot: dict[str, int]


@dataclass(frozen=True)
class Region:
    name: str
    terrain: str
    min_players: int
    resources: dict[str, int]
    object: str
    fortress: Fortress | None
    artifact: dict | None  # условие получения: {"condition": ..., "cost"/"count": ...}


@dataclass(frozen=True)
class Monster:
    strength: int
    vp: int
    loot: dict[str, int]


@dataclass(frozen=True)
class GameMap:
    regions: dict[str, Region]
    adjacent: dict[str, frozenset[str]]
    monsters: tuple[Monster, ...]

    @property
    def portals(self) -> list[str]:
        return [r.name for r in self.regions.values() if r.object == "portal"]

    def available_regions(self, n_players: int) -> set[str]:
        return {r.name for r in self.regions.values() if r.min_players <= n_players}


@dataclass(frozen=True)
class UnitType:
    id: str
    name: str
    strength: int
    cost: dict[str, int]
    start: int


@dataclass(frozen=True)
class Building:
    id: str
    name: str
    level: int
    vp: int
    cost: dict[str, int]
    requires: tuple[str, ...]  # достаточно одного из перечисленных
    text: str
    effects: tuple[dict, ...]
    text_ru: str = ""  # для экрана (графический интерфейс); Claude и консоль получают text
    text_board: str = ""  # как на печатном планшете: со значками {wood}, {laurel}… (heroes/gui/icons.py)


@dataclass(frozen=True)
class ArtifactEffect:
    text: str
    effects: tuple[dict, ...]
    text_ru: str = ""
    text_board: str = ""


@dataclass(frozen=True)
class Rule:
    """Свойство фракции (блок на планшете, не здание): действует всю партию."""
    id: str
    name: str
    text: str
    effects: tuple[dict, ...]
    text_ru: str = ""
    text_board: str = ""


@dataclass(frozen=True)
class Faction:
    id: str
    name: str
    movement: dict[str, int]
    units: dict[str, UnitType]
    buildings: dict[str, Building]
    artifacts: dict[str, ArtifactEffect]
    rules: dict[str, Rule] = field(default_factory=dict)
    monsters: tuple[Monster, ...] | None = None    # своя линия чудовищ (наги) вместо общей
    artifact_conditions: dict[str, dict] = field(default_factory=dict)  # свои условия артефактов (наги)

    def resource_units(self) -> set[str]:
        """Войска, которые служат и ресурсом: ими платят за здания и артефакты (краки наг)."""
        return {e["unit"] for r in self.rules.values() for e in r.effects if e["type"] == "krak_exchange"}


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_map(path: Path | None = None) -> GameMap:
    raw = _read(path or DATA_DIR / "map.json")
    regions = {}
    for name, r in raw["regions"].items():
        fortress = Fortress(**r["fortress"]) if "fortress" in r else None
        regions[name] = Region(
            name=name,
            terrain=r["terrain"],
            min_players=r["minPlayers"],
            resources=dict(r["resources"]),
            object=r["objects"][0] if len(r["objects"]) == 1 else ",".join(r["objects"]),
            fortress=fortress,
            artifact=r.get("artifact"),
        )
    adjacent: dict[str, set[str]] = {name: set() for name in regions}
    for a, b in raw["adjacent"]:
        if a not in regions or b not in regions:
            raise DataError(f"Adjacency with an unknown region: {a}–{b}")
        if a == b or b in adjacent[a]:
            raise DataError(f"Self-loop or duplicate adjacency: {a}–{b}")
        adjacent[a].add(b)
        adjacent[b].add(a)
    monsters = tuple(Monster(**m) for m in raw["monsters"])
    game_map = GameMap(regions, {k: frozenset(v) for k, v in adjacent.items()}, monsters)
    errors = validate_map(game_map)
    if errors:
        raise DataError("; ".join(errors))
    return game_map


def validate_map(game_map: GameMap) -> list[str]:
    errors = []
    for r in game_map.regions.values():
        if r.terrain not in TERRAINS:
            errors.append(f"{r.name}: unknown terrain {r.terrain}")
        if r.object not in OBJECT_TYPES:
            errors.append(f"{r.name}: a region must have exactly one object, not {r.object!r}")
        if (r.object == "fortress") != (r.fortress is not None):
            errors.append(f"{r.name}: fortress description missing")
        if r.object == "artifact":
            if not r.artifact or r.artifact.get("condition") not in ARTIFACT_CONDITIONS:
                errors.append(f"{r.name}: unknown artifact condition")
        for res in r.resources:
            if res not in RESOURCES + ("any",):
                errors.append(f"{r.name}: unknown resource {res}")
    portals = set(game_map.portals)
    for n in (2, 3, 4):
        avail = game_map.available_regions(n)
        start = next(iter(avail))
        seen, stack = {start}, [start]
        while stack:
            cur = stack.pop()
            nbrs = set(game_map.adjacent[cur])
            if cur in portals:
                nbrs |= portals
            for nb in nbrs & avail - seen:
                seen.add(nb)
                stack.append(nb)
        if seen != avail:
            errors.append(f"The {n}-player map is disconnected: unreachable {sorted(avail - seen)}")
    return errors


def load_faction(faction_id: str, game_map: GameMap | None = None) -> Faction:
    raw = _read(DATA_DIR / "factions" / f"{faction_id}.json")
    units = {uid: UnitType(id=uid, **u) for uid, u in raw["units"].items()}
    buildings = {
        bid: Building(
            id=bid, name=b["name"], level=b["level"], vp=b["vp"], cost=dict(b["cost"]),
            requires=tuple(b["requires"]), text=b["text"], effects=tuple(b["effects"]), text_ru=b.get("text_ru", ""),
            text_board=b.get("text_board", ""),
        )
        for bid, b in raw["buildings"].items()
    }
    artifacts = {
        region: ArtifactEffect(text=a["text"], effects=tuple(a["effects"]), text_ru=a.get("text_ru", ""),
                               text_board=a.get("text_board", ""))
        for region, a in raw["artifacts"].items()
    }
    rules = {
        rid: Rule(id=rid, name=r["name"], text=r["text"], effects=tuple(r["effects"]), text_ru=r.get("text_ru", ""),
                  text_board=r.get("text_board", ""))
        for rid, r in raw.get("rules", {}).items()
    }
    monsters = tuple(Monster(**m) for m in raw["monsters"]) if "monsters" in raw else None
    faction = Faction(raw["id"], raw["name"], dict(raw["movement"]), units, buildings, artifacts, rules, monsters,
                      dict(raw.get("artifact_conditions", {})))
    errors = validate_faction(faction, game_map or load_map())
    if errors:
        raise DataError("; ".join(errors))
    return faction


def validate_faction(faction: Faction, game_map: GameMap) -> list[str]:
    errors = []
    if set(faction.movement) != set(TERRAINS):
        errors.append("Movement cost is not defined for every terrain")
    all_effects = [(b.name, e) for b in faction.buildings.values() for e in b.effects]
    all_effects += [(f"artifact {r}", e) for r, a in faction.artifacts.items() for e in a.effects]
    all_effects += [(r.name, e) for r in faction.rules.values() for e in r.effects]
    paid_with = set(RESOURCES) | set(TOKENS) | faction.resource_units()
    for owner, e in all_effects:
        if e["type"] not in EFFECT_TYPES:
            errors.append(f"{owner}: unknown effect {e['type']}")
        units = [e[k] for k in ("unit", "trigger_unit") if k in e] + list(e.get("except", ()))
        units += [u for path in e.get("paths", ()) for u in (path["from"], path["to"])]
        units += list(e.get("strength", {}))
        for unit in units:
            if unit not in faction.units:
                errors.append(f"{owner}: unknown unit {unit}")
        for key in ("terrain", "summon_terrain"):
            if key in e and e[key] not in TERRAINS:
                errors.append(f"{owner}: unknown terrain {e[key]}")
        if "object" in e and e["object"] not in OBJECT_TYPES:
            errors.append(f"{owner}: unknown object {e['object']}")
        for region in e.get("prices", {}):
            if region not in faction.artifacts:
                errors.append(f"{owner}: unknown artifact region {region}")
    costs = [(b.name, b.cost) for b in faction.buildings.values()]
    costs += [(u.name, u.cost) for u in faction.units.values()]
    costs += [(f"artifact {r}", c.get("cost", {})) for r, c in faction.artifact_conditions.items()]
    for owner, cost in costs:
        for k in cost:
            if k not in paid_with:
                errors.append(f"{owner}: unknown cost resource {k}")
    for b in faction.buildings.values():
        if not 1 <= b.level <= 4:
            errors.append(f"{b.name}: level outside 1–4")
        for req in b.requires:
            if req not in faction.buildings:
                errors.append(f"{b.name}: unknown required building {req}")
    artifact_regions = {r.name for r in game_map.regions.values() if r.object == "artifact"}
    if set(faction.artifacts) != artifact_regions:
        errors.append("Faction artifacts do not match the artifact regions on the map")
    for region, cond in faction.artifact_conditions.items():
        if region not in artifact_regions or cond.get("condition") not in ARTIFACT_CONDITIONS:
            errors.append(f"artifact {region}: bad faction-specific condition")
    if faction.monsters is not None and len(faction.monsters) != len(game_map.monsters):
        errors.append("The faction's monster line must have as many monsters as the common one")
    return errors
