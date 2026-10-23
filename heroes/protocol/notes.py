"""Заметки Claude по стратегии: по файлу на фракцию, копятся от партии к партии.

Заметки ведутся отдельно для каждого «варианта» Claude — модели и уровня усилий:
strategy/<модель>/<effort>/<фракция>.md (effort «default», если уровень не задан). Так видно,
как учится каждый вариант, и опыт одного не смешивается с опытом другого.

Файл — markdown из фиксированных разделов: «General strategy», «Reminders» и «Against <фракция>»
по каждой другой фракции. В начале партии файл своей фракции целиком идёт в системный промпт; в конце
Claude может (не обязан) переписать общий раздел, напоминания и разделы против тех фракций, с которыми
играл. Присланный раздел заменяет старый целиком, так что Claude сам сводит старое с новым
и выбрасывает лишнее; длина разделов ограничена, чтобы файл не разрастался.

Напоминания — короткие строки с условием, когда их показать:
  - [rounds 14-16, build_recruit] Every unspent resource is lost at the end: build anything that scores.
  - [destination] Count the MP to the target before moving.
  - [always] ...
Условие — через запятую: «always», «round N», «rounds N-M» и виды решений (DECISION_KINDS).
Раунды между собой и виды решений между собой — «или», раунды с видами — «и». Подходящие
напоминания движок вставляет в текст вопроса.
"""
from __future__ import annotations

import json
import os
import re
from functools import cache
from pathlib import Path

from ..engine.data import DATA_DIR
from ..engine.state import ROUNDS

NOTES_DIR = DATA_DIR.parent / "strategy"
GENERAL = "general"
REMINDERS = "reminders"
TITLES = {GENERAL: "General strategy", REMINDERS: "Reminders"}
EMPTY = "(no notes yet)"
LIMITS = {GENERAL: 2000}     # символов в разделе; разделы против фракций — AGAINST_LIMIT
AGAINST_LIMIT = 800
MAX_REMINDERS = 12
REMINDER_LIMIT = 200         # символов в тексте одного напоминания

# Виды решений, к которым можно привязать напоминание (heroes/protocol/schemas.py).
DECISION_KINDS = (
    "choose_start", "action_order", "build_recruit", "destination", "region_stance", "commit_army",
    "choose_reward", "capture_fortress", "fight_monster", "take_artifact", "reserve_object", "curse_region",
    "summon_hydra", "hydra_destination", "make_lake", "raise_action", "upgrade_units", "battle_upgrade",
    "avoid_battle", "buy_move_points", "buy_science", "distribute_resources", "exchange_on_gain", "circle_note",
)
# Модификация орков, Библиотека гномов и Дом вождей в строительстве — шаги решения build_recruit:
# напоминания с этими видами (из старых заметок) показываются в нём.
ALIASES = {"build_recruit": {"upgrade_units", "buy_science", "raise_action"}}
REMINDER_LINE = re.compile(r"-\s*\[(?P<when>[^\]]*)\]\s*(?P<text>.*)")


def variant(model: str, effort: str | None) -> str:
    """Папка варианта Claude относительно NOTES_DIR."""
    return f"{model}/{effort or 'default'}"


@cache
def faction_names() -> dict[str, str]:
    """id → название фракции, по алфавиту id (порядок разделов в файле)."""
    return {f.stem: json.loads(f.read_text(encoding="utf-8"))["name"] for f in sorted((DATA_DIR / "factions").glob("*.json"))}


def limit(key: str) -> int:
    return LIMITS.get(key, AGAINST_LIMIT)


def path(variant_: str, faction: str) -> Path:
    return NOTES_DIR / variant_ / f"{faction}.md"


def titles(faction: str) -> dict[str, str]:
    """Ключ раздела → заголовок: general, reminders и id остальных фракций."""
    return {**TITLES, **{f: f"Against {name}" for f, name in faction_names().items() if f != faction}}


def load(variant_: str, faction: str) -> dict[str, str]:
    """Разделы файла по ключам; отсутствующие разделы и файл — пустые строки."""
    sections = dict.fromkeys(titles(faction), "")
    try:
        text = path(variant_, faction).read_text(encoding="utf-8")
    except FileNotFoundError:
        return sections
    by_title = {title: key for key, title in titles(faction).items()}
    for title, body in re.findall(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S):
        key = by_title.get(title.strip())
        if key:
            body = body.strip()
            sections[key] = "" if body == EMPTY else body
    return sections


def render(faction: str, sections: dict[str, str]) -> str:
    lines = [f"# Strategy notes: {faction_names()[faction]}"]
    for key, title in titles(faction).items():
        lines += ["", f"## {title}", sections.get(key, "").strip() or EMPTY]
    return "\n".join(lines) + "\n"


def save(variant_: str, faction: str, sections: dict[str, str]) -> Path:
    target = path(variant_, faction)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(render(faction, sections), encoding="utf-8")
    os.replace(tmp, target)   # без полузаписанного файла, если процесс оборвётся
    return target


def update(variant_: str, faction: str, changes: dict[str, str]) -> Path:
    sections = load(variant_, faction)
    sections.update({k: v.strip() for k, v in changes.items()})
    return save(variant_, faction, sections)


# --- напоминания ----------------------------------------------------------------------------------

def parse_reminders(text: str) -> list[tuple[set[int], set[str], str]]:
    """Раздел напоминаний → (раунды, виды решений, текст); пустое множество — без ограничения.
    ValueError с объяснением — строка не по формату."""
    reminders = []
    for line in filter(None, (line.strip() for line in text.splitlines())):
        m = REMINDER_LINE.fullmatch(line)
        if not m or not m["text"].strip():
            raise ValueError(f"reminder {line!r} is not in the format '- [<when>] <text>'")
        rounds: set[int] = set()
        kinds: set[str] = set()
        for cond in filter(None, (c.strip() for c in m["when"].split(","))):
            if cond == "always":
                continue
            if r := re.fullmatch(r"rounds? (\d+)(?:\s*-\s*(\d+))?", cond):
                first, last = int(r[1]), int(r[2] or r[1])
                if not 1 <= first <= last <= ROUNDS:
                    raise ValueError(f"reminder {line!r}: rounds must be within 1–{ROUNDS}")
                rounds |= set(range(first, last + 1))
            elif cond in DECISION_KINDS:
                kinds.add(cond)
            else:
                raise ValueError(f"reminder {line!r}: unknown condition {cond!r}; use always, round N, rounds N-M "
                                 f"or a decision kind: {', '.join(DECISION_KINDS)}")
        if len(m["text"].strip()) > REMINDER_LIMIT:
            raise ValueError(f"reminder {line!r} is longer than {REMINDER_LIMIT} characters")
        reminders.append((rounds, kinds, m["text"].strip()))
    if len(reminders) > MAX_REMINDERS:
        raise ValueError(f"{len(reminders)} reminders, at most {MAX_REMINDERS} are allowed")
    return reminders


def reminders_for(variant_: str, faction: str, round_: int, kind: str) -> list[str]:
    """Напоминания, подходящие к решению kind в раунде round_ (испорченный руками раздел — пусто)."""
    try:
        reminders = parse_reminders(load(variant_, faction)[REMINDERS])
    except ValueError:
        return []
    return [text for rounds, kinds, text in reminders
            if (not rounds or round_ in rounds) and (not kinds or kinds & {kind, *ALIASES.get(kind, ())})]


# --- ответ после партии ---------------------------------------------------------------------------

def validate(answer, allowed: list[str]) -> dict[str, str] | None:
    """Ответ Claude после партии → изменённые разделы (None — ничего не менять).
    ValueError с объяснением — ответ не годится."""
    if not isinstance(answer, dict) or not isinstance(answer.get("update"), bool):
        raise ValueError('answer must be an object with a boolean "update"')
    if not answer["update"]:
        return None
    sections = answer.get("sections")
    if not isinstance(sections, dict) or not sections:
        raise ValueError('with "update": true, "sections" must be a non-empty object')
    for key, text in sections.items():
        if key not in allowed:
            raise ValueError(f"unknown section {key!r}; allowed: {', '.join(allowed)}")
        if not isinstance(text, str):
            raise ValueError(f"section {key!r} must be a string")
        if key == REMINDERS:
            parse_reminders(text)
        elif len(text.strip()) > limit(key):
            raise ValueError(f"section {key!r} is {len(text.strip())} characters, the limit is {limit(key)}: shorten it")
    return {k: v.strip() for k, v in sections.items()}


def section_rule(key: str) -> str:
    """Ограничение раздела — для вопроса после партии."""
    if key == REMINDERS:
        return f"at most {MAX_REMINDERS} lines '- [<when>] <text>', text at most {REMINDER_LIMIT} characters"
    return f"at most {limit(key)} characters"


def prompt_text(variant_: str, faction: str) -> str:
    """Для системного промпта: заметки из прошлых партий этой фракцией."""
    sections = load(variant_, faction)
    name = faction_names()[faction]
    if not any(sections.values()):
        return (f"YOUR STRATEGY NOTES for {name}: none yet — this is your first recorded game as {name}. "
                "After the game you will be able to write down your conclusions.")
    return (f"YOUR STRATEGY NOTES for {name}, written by you after previous games as {name}. "
            "They are your own conclusions, not rules: use them, but trust the rules and the actual game state over them. "
            "The Reminders are also shown to you at the moments they are tied to.\n\n"
            + render(faction, sections))
