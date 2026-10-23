"""Изменяемое состояние партии. Всё сериализуется в JSON через to_dict()."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ROUNDS = 16
ACTIONS = ("wood", "iron", "gold", "move", "build")
ACTION_NAMES = {
    "wood": "gather wood", "iron": "gather iron", "gold": "gather gold",
    "move": "move", "build": "build & hire",
}
RESOURCE_NAMES = {"wood": "wood", "iron": "iron", "gold": "gold", "any": "any resource",
                  "rage": "rage", "science": "science", "krak": "Крак"}
TERRAIN_NAMES = {"field": "field", "forest": "forest", "mountain": "mountains", "lake": "lake"}
OBJECT_NAMES = {"fortress": "fortress", "foundation": "foundation", "portal": "portal", "artifact": "artifact"}
# Отметки на складе: при накоплении ресурса до порога обводятся ПО (навсегда).
RESOURCE_VP = ((14, 2), (22, 2), (30, 3))


@dataclass
class PlayerState:
    name: str
    faction: str
    resources: dict[str, int] = field(default_factory=lambda: {"wood": 0, "iron": 0, "gold": 0})
    units: dict[str, int] = field(default_factory=dict)
    built: list[str] = field(default_factory=list)
    foundations: dict[str, int] = field(default_factory=dict)  # регион -> вписанный уровень
    visited: list[str] = field(default_factory=list)
    region: str | None = None
    # Линия чудовищ: None — не обведено, "monster" — побеждено чудовище, "pvp" — победа над игроком.
    monsters: list[str | None] = field(default_factory=list)
    resource_marks: dict[str, list[int]] = field(
        default_factory=lambda: {"wood": [], "iron": [], "gold": []}
    )
    fortresses: list[str] = field(default_factory=list)
    # Перемещения по порядку: [откуда, куда] — карта в окне рисует их стрелками. Правил не касается.
    routes: list[list[str]] = field(default_factory=list)
    # --- особенности фракций (у людей пустые) ---
    tokens: dict[str, int] = field(default_factory=dict)   # уникальные ресурсы: ярость орков, наука гномов
    move_points: int = 0          # ОП этого раунда со всеми прибавками (сила палачей нежити); 0 до перемещения
    raised: dict[str, int] = field(default_factory=dict)   # Дом вождей: на сколько подняты действия этого раунда
    mountains: list[str] = field(default_factory=list)     # посещённые регионы, горные в момент посещения (Шог Ид)
    notes: list[int] = field(default_factory=list)         # наги: сколько нот обведено в каждой паре (0–2)
    hydra: str | None = None      # наги: регион гидры на карте (None — гидры нет)
    hydra_summons: int = 0        # наги: сколько раз призвана гидра (Храм)

    @property
    def victories(self) -> int:
        """Победы в сражениях: захваченные крепости + обведённые чудовища."""
        return len(self.fortresses) + sum(1 for m in self.monsters if m)


@dataclass
class GameState:
    players: list[PlayerState]
    available: list[str]                     # регионы, участвующие в партии
    round: int = 0
    first_player: int = 0
    action_order: list[str] = field(default_factory=list)
    phase: int = 0   # фаза раунда: 0 — первый игрок выбирает порядок действий, 1–4 — выполняется действие этого уровня
    fortress_owner: dict[str, int] = field(default_factory=dict)
    artifact_holder: dict[str, int] = field(default_factory=dict)
    reserved: dict[str, int] = field(default_factory=dict)  # Башня иллюзий: регион -> владелец брони
    cursed: dict[str, int] = field(default_factory=dict)    # нежить: проклятый регион -> кто проклял
    terrain: dict[str, str] = field(default_factory=dict)   # изменённая местность (Гейзер наг): регион -> местность
    log: list[str] = field(default_factory=list)

    def turn_order(self) -> list[int]:
        """Игроки по часовой стрелке, начиная с первого игрока раунда."""
        n = len(self.players)
        return [(self.first_player + i) % n for i in range(n)]

    def artifacts_of(self, p: int) -> list[str]:
        return [region for region, holder in self.artifact_holder.items() if holder == p]

    def to_dict(self) -> dict:
        return asdict(self)
