"""Журналы обучения Claude в папке strategy/ (NOTES_DIR) — отдельном git-репозитории.

- git: каждое изменение заметок и скриптов Claude — коммит с объяснением от самого Claude;
  перед тем как править заметки, Claude видит последние изменения своего файла (history).
- results.csv — по строке на каждого Claude-игрока в каждой партии: как растёт результат варианта.
- RULES_CONFUSION.md — для человека: где Claude путался в правилах (отклонённые движком ответы,
  ответы не по формату, его собственные вопросы о правилах).
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import notes

AUTHOR = ["-c", "user.name=Heroes Claude", "-c", "user.email=claude@heroes.local"]
HISTORY_COMMITS = 3
RESULTS_FIELDS = ["time", "game_id", "model", "effort", "player", "faction", "vp", "place", "players", "winner", "opponents"]
_lock = threading.Lock()   # Claude-игроки одной партии пишут после неё параллельно


def root() -> Path:
    return notes.NOTES_DIR


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root()), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def ensure_repo() -> bool:
    """Создать репозиторий в strategy/, если его нет. False — git не установлен."""
    if not shutil.which("git"):
        return False
    root().mkdir(parents=True, exist_ok=True)
    if not (root() / ".git").exists():
        git("init", "-q")
        (root() / ".gitignore").write_text("*.tmp\n__pycache__/\n", encoding="utf-8")
    return True


def history(variant: str, faction: str) -> str:
    """Последние изменения файла заметок (с диффами) и папки скриптов варианта."""
    if not ensure_repo():
        return "(git is not installed — no history)"
    notes_file = f"{variant}/{faction}.md"
    log = git("log", f"-{HISTORY_COMMITS}", "-p", "--format=--- %ad · %s%n%b", "--date=format:%Y-%m-%d %H:%M",
              "--", notes_file, check=False).stdout.strip()
    scripts = git("log", f"-{HISTORY_COMMITS}", "--stat", "--format=--- %ad · %s", "--date=format:%Y-%m-%d %H:%M",
                  "--", f"{variant}/scripts", check=False).stdout.strip()
    return "\n\n".join([
        f"Last {HISTORY_COMMITS} changes of your notes file {notes_file}:\n" + (log or "(none yet)"),
        f"Last {HISTORY_COMMITS} changes of your scripts:\n" + (scripts or "(none yet)"),
    ])


def commit(paths: list[Path], subject: str, body: str = "") -> bool:
    """Закоммитить изменения только этих путей. False — изменений нет или git недоступен."""
    with _lock:
        if not ensure_repo():
            return False
        rel = [str(p.relative_to(root())) for p in paths if p.exists()]
        if not rel:
            return False
        git("add", "-A", "--", *rel)
        if not git("diff", "--cached", "--quiet", "--", *rel, check=False).returncode:
            return False
        for _ in range(5):   # параллельная партия могла занять index.lock
            if git(*AUTHOR, "commit", "-q", "-m", subject, "-m", body, "--", *rel, check=False).returncode == 0:
                return True
            time.sleep(1)
        return False


def record_result(row: dict) -> Path:
    """Дописать строку в results.csv."""
    target = root() / "results.csv"
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        new = not target.exists()
        with open(target, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, RESULTS_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow(row)
    return target


def confusion_path() -> Path:
    return root() / "RULES_CONFUSION.md"


def log_confusion(source: str, where: str, details: dict[str, str]) -> None:
    """Запись для человека: source — откуда (rejected by the engine, …), where — кто, когда, на каком решении."""
    target = confusion_path()
    lines = [f"## {time.strftime('%Y-%m-%d %H:%M')} · {source}", f"*{where}*", ""]
    lines += [f"- **{key}:** {value}" for key, value in details.items() if value]
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        new = not target.exists()
        with open(target, "a", encoding="utf-8") as f:
            if new:
                f.write("# Путаница в правилах у Claude-игроков\n\n"
                        "Сюда попадают ответы Claude, отклонённые движком, ответы не по формату и вопросы о правилах, "
                        "которые Claude задал сам. Файл для человека: Claude его не видит.\n\n")
            f.write("\n".join(lines) + "\n\n")
