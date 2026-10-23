"""Сохранение и загрузка партии.

Случайности в игре нет, поэтому партия однозначно восстанавливается по параметрам запуска
и журналу решений: при загрузке движок заново проигрывает все записанные ответы.
Так можно сохраниться в любой момент — даже посреди раунда, в ожидании решения.

Имена файлов — дата и время сохранения и номер раунда: saves/2026-10-17_19-45-02_round05.json;
автосохранение в начале каждого раунда — с «_auto» (хранятся все), прерванная
партия — с «_interrupted».
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from .engine.game import Game
    from .players.base import Player

SAVE_VERSION = 3  # 2 — решения без выбора (Decision.forced) не записываются; 3 — заявление в регионе бывает без выбора
# виды решений, которые в сохранениях старых версий записаны, даже если теперь выбора в них нет
RECORDED_FORCED = {2: {"region_stance"}}
SAVE_DIR = Path("saves")


def default_path(game: Game, kind: str = "", folder: str | Path = SAVE_DIR, round_: int | None = None) -> Path:
    """saves/<дата>_<время>_round<NN>[_<kind>].json; round_ — номер раунда, если не текущий."""
    number = game.state.round if round_ is None else round_
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path(folder) / (f"{stamp}_round{number:02d}" + (f"_{kind}" if kind else "") + ".json")


def autosave(game: Game, folder: str | Path) -> Path:
    """Сохранение в начале раунда (номер — того, что начинается). Прежние автосохранения партии остаются:
    вернуться можно к началу любого раунда."""
    path = save(game, default_path(game, "auto", folder, game.state.round + 1))
    game.last_autosave = path
    return path


def latest(folder: str | Path = SAVE_DIR) -> Path | None:
    """Самое свежее сохранение в папке."""
    files = list(Path(folder).glob("*.json")) if Path(folder).is_dir() else []
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def save(game: Game, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": SAVE_VERSION,
        "meta": game.meta,
        "names": [p.name for p in game.state.players],
        "factions": [p.faction for p in game.state.players],
        "first_player": game.initial_first_player,
        "history": game.history,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load(path: str | Path, make_players: Callable[[dict, list[str]], Sequence[Player]], **game_kwargs) -> Game:
    """make_players(meta, names) создаёт живых игроков; записанные решения проигрываются при game.run()."""
    from .engine.game import Game

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") not in (1, 2, SAVE_VERSION):
        raise ValueError(f"Unsupported save version: {data.get('version')}")
    return Game(
        make_players(data["meta"], data["names"]),
        names=data["names"],
        factions=data["factions"],
        first_player=data["first_player"],
        replay=data["history"],
        meta=data["meta"],
        legacy_replay=data["version"] == 1,
        recorded_forced=RECORDED_FORCED.get(data["version"], ()),
        **game_kwargs,
    )
