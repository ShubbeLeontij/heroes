"""Сохранение и загрузка партии.

Случайности в игре нет, поэтому партия однозначно восстанавливается по параметрам запуска
и журналу решений: при загрузке движок заново проигрывает все записанные ответы.
Так можно сохраниться в любой момент — даже посреди раунда, в ожидании решения.

Сохранения разложены по папкам saves/ по виду, в имени — номер раунда, дата и время:
  saves/manual/round05_2026-10-17_19-45-02.json                   — командой save хозяина;
  saves/auto/round05_auto_2026-10-17_19-45-02.json                — в начале каждого раунда (хранятся все);
  saves/interrupted/round05_interrupted_2026-10-17_19-45-02.json  — партия, прерванная по Ctrl+C;
  saves/final/round16_final_2026-10-17_19-45-02.json              — доигранная (загрузив её, получаешь итоги).
Там же, в saves/profiles/, — профили игроков (heroes/profiles.py); их записи о партиях ссылаются на final.
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
# виды сохранений — у каждого своя папка в SAVE_DIR
MANUAL, AUTO, INTERRUPTED, FINAL = "manual", "auto", "interrupted", "final"
RESUMABLE = (MANUAL, AUTO, INTERRUPTED)   # доигранную партию продолжать нечего


def default_path(game: Game, kind: str = MANUAL, folder: str | Path = SAVE_DIR, round_: int | None = None) -> Path:
    """saves/<kind>/round<NN>[_<kind>]_<дата>_<время>.json (у ручных — без «_<kind>»);
    round_ — номер раунда, если не текущий."""
    number = game.state.round if round_ is None else round_
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path(folder) / kind / (f"round{number:02d}" + (f"_{kind}" if kind != MANUAL else "") + f"_{stamp}.json")


def autosave(game: Game, folder: str | Path) -> Path:
    """Сохранение в начале раунда (номер — того, что начинается). Прежние автосохранения партии остаются:
    вернуться можно к началу любого раунда."""
    path = save(game, default_path(game, AUTO, folder, game.state.round + 1))
    game.last_autosave = path
    return path


def final_save(game: Game, folder: str | Path) -> Path:
    """Доигранная партия — с итогами: её можно открыть ещё раз, чтобы посмотреть их."""
    return save(game, default_path(game, FINAL, folder))


def latest(folder: str | Path = SAVE_DIR) -> Path | None:
    """Самое свежее сохранение, которое можно продолжить (доигранные партии — нет)."""
    files = [f for kind in RESUMABLE for f in (Path(folder) / kind).glob("*.json")]
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
