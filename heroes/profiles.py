"""Профили игроков на этом компьютере: имя, аватарка, статистика партий.

  profiles/profiles.json          — {"active": <id>}: профиль, выбранный на стартовом экране
  profiles/<id>/profile.json      — {"id", "name", "created", "games": [запись о партии, …]}
  profiles/<id>/avatar.png        — своя аватарка (готовит gui/avatars.py)

Запись о партии — только о доигранной (после подсчёта очков); строится по тем же данным, что получает
окно (static и state из heroes/protocol/snapshot.py), поэтому одинаково пишется и у хозяина, и у гостя:
  {"game_id", "date", "you": номер места, "players": [{"name", "kind", "faction", "faction_name",
   "turn": место в очерёдности (1 — первый игрок первого раунда), "vp", "score", "winner", "profile"}, …]}
"""
from __future__ import annotations

import base64
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

PROFILE_DIR = Path("profiles")
MAX_NAME = 24


@dataclass
class Profile:
    id: str
    name: str
    created: str = ""
    games: list[dict] = field(default_factory=list)
    folder: Path = field(default=PROFILE_DIR, repr=False, compare=False)

    @property
    def path(self) -> Path:
        return self.folder / self.id

    @property
    def avatar(self) -> Path | None:
        """Своя аватарка, если задана (avatar_96.png — прежний формат, когда хранилось несколько размеров)."""
        return next((path for path in (self.path / "avatar.png", self.path / "avatar_96.png") if path.exists()), None)

    def to_dict(self) -> dict:
        data = asdict(self)
        del data["folder"]
        return data


def avatar_b64(profile: Profile | None) -> str | None:
    """Своя аватарка профиля (PNG) в base64 — для окон соперников (static["avatars"], hello гостя)."""
    path = profile.avatar if profile else None
    try:
        return base64.b64encode(path.read_bytes()).decode() if path else None
    except OSError:
        return None


def all_profiles(folder: str | Path = PROFILE_DIR) -> list[Profile]:
    """Все профили по алфавиту; повреждённые файлы пропускаются."""
    folder = Path(folder)
    result = []
    for path in folder.glob("*/profile.json") if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append(Profile(data["id"], data["name"], data.get("created", ""), data.get("games", []), folder))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(result, key=lambda p: p.name.casefold())


def get(profile_id: str | None, folder: str | Path = PROFILE_DIR) -> Profile | None:
    return next((p for p in all_profiles(folder) if p.id == profile_id), None) if profile_id else None


def by_name(name: str, folder: str | Path = PROFILE_DIR) -> Profile | None:
    return next((p for p in all_profiles(folder) if p.name == name), None)


def check_name(name: str, folder: str | Path = PROFILE_DIR, own_id: str | None = None) -> str | None:
    """Почему такое имя нельзя (по-русски, для окна), или None."""
    name = name.strip()
    if not name:
        return "Имя не может быть пустым."
    if len(name) > MAX_NAME:
        return f"Имя длиннее {MAX_NAME} символов."
    if any(p.name.casefold() == name.casefold() and p.id != own_id for p in all_profiles(folder)):
        return f"Профиль «{name}» уже есть."
    return None


def save(profile: Profile) -> None:
    profile.path.mkdir(parents=True, exist_ok=True)
    path = profile.path / "profile.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def create(name: str, folder: str | Path = PROFILE_DIR) -> Profile:
    """Новый профиль; первый созданный становится активным."""
    error = check_name(name, folder)
    if error:
        raise ValueError(error)
    profile = Profile(uuid.uuid4().hex[:12], name.strip(), datetime.now().isoformat(timespec="seconds"),
                      folder=Path(folder))
    save(profile)
    if get_active(folder) is None:
        set_active(profile.id, folder)
    return profile


def rename(profile: Profile, name: str) -> None:
    error = check_name(name, profile.folder, profile.id)
    if error:
        raise ValueError(error)
    profile.name = name.strip()
    save(profile)


def delete(profile: Profile) -> None:
    """Удалить профиль со статистикой и аватаркой; активным становится другой (если есть)."""
    shutil.rmtree(profile.path, ignore_errors=True)
    if _settings(profile.folder).get("active") == profile.id:
        rest = all_profiles(profile.folder)
        set_active(rest[0].id if rest else None, profile.folder)


def unique_name(base: str = "Игрок", folder: str | Path = PROFILE_DIR) -> str:
    """«Игрок 1», «Игрок 2»… — первое свободное имя для нового профиля."""
    n = 1
    while check_name(f"{base} {n}", folder):
        n += 1
    return f"{base} {n}"


def _settings(folder: str | Path) -> dict:
    try:
        return json.loads((Path(folder) / "profiles.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_active(folder: str | Path = PROFILE_DIR) -> Profile | None:
    return get(_settings(folder).get("active"), folder)


def set_active(profile_id: str | None, folder: str | Path = PROFILE_DIR) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profiles.json").write_text(json.dumps({**_settings(folder), "active": profile_id}), encoding="utf-8")


# --- статистика ----------------------------------------------------------------------------------

def winners(state: dict) -> list[int]:
    """Как scoring.winners: больше всего ПО, при равенстве — больше ресурсов на складе."""
    players = state["players"]
    best = max(pl["score"]["total"] for pl in players)
    leaders = [p for p, pl in enumerate(players) if pl["score"]["total"] == best]
    stock = {p: sum(players[p]["resources"].values()) for p in leaders}
    return [p for p in leaders if stock[p] == max(stock.values())]


def game_record(static: dict, state: dict, you: int, seat_profiles: dict[int, str] | None = None) -> dict:
    """Запись о доигранной партии с точки зрения игрока на месте you.
    seat_profiles — какие места заняты профилями этого компьютера (для аватарок соперников)."""
    seat_profiles = seat_profiles or {}
    n = len(state["players"])
    first = static.get("first_player", 0)   # у хозяина старой версии этих полей в static нет
    kinds = static.get("kinds") or ["human"] * n
    win = winners(state)
    players = []
    for p, pl in enumerate(state["players"]):
        fid = pl["faction"]
        players.append({
            "name": pl["name"],
            "kind": kinds[p],
            "faction": fid,
            "faction_name": static["factions"][fid]["name"],
            "turn": (p - first) % n + 1,
            "vp": pl["score"]["total"],
            "score": dict(pl["score"]),
            "winner": p in win,
            "profile": seat_profiles.get(p),
        })
    return {"game_id": static.get("game_id") or uuid.uuid4().hex, "date": datetime.now().isoformat(timespec="seconds"),
            "you": you, "players": players}


def add_game(profile: Profile, record: dict) -> None:
    """Добавить партию в статистику; та же партия (загруженная и доигранная снова) заменяет прежнюю запись."""
    profile.games = [g for g in profile.games if g.get("game_id") != record["game_id"]] + [record]
    save(profile)


def record_game(static: dict, state: dict, seat_profiles: dict[int, str], folder: str | Path = PROFILE_DIR) -> list[str]:
    """Записать доигранную партию во все профили за столом. Возвращает имена профилей, куда записано."""
    names = []
    for seat, profile_id in sorted(seat_profiles.items()):
        profile = get(profile_id, folder)
        if profile is not None:
            add_game(profile, game_record(static, state, seat, seat_profiles))
            names.append(profile.name)
    return names


def seat_profiles(meta: dict, names: list[str], folder: str | Path = PROFILE_DIR) -> dict[int, str]:
    """Места людей за этим компьютером → профили: из meta["profiles"], а в партиях без этой записи
    (консоль, старые сохранения) — по совпадению имени человека с именем профиля."""
    if "profiles" in meta:
        return {int(seat): pid for seat, pid in meta["profiles"].items() if pid}
    result = {}
    for seat, (kind, name) in enumerate(zip(meta.get("players", []), names)):
        profile = by_name(name, folder) if kind == "human" else None
        if profile:
            result[seat] = profile.id
    return result


def summary(profile: Profile) -> dict:
    """Итог по всем партиям профиля: число партий, побед, средние и лучшие ПО."""
    vps = [g["players"][g["you"]]["vp"] for g in profile.games]
    wins = sum(1 for g in profile.games if g["players"][g["you"]]["winner"])
    return {"games": len(vps), "wins": wins, "avg_vp": sum(vps) / len(vps) if vps else 0, "best_vp": max(vps, default=0)}
