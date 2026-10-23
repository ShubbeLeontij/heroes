"""Инструменты Claude-игрока: личная рабочая папка со скриптами и их запуск в песочнице.

Скрипты общие для варианта Claude (модель и уровень усилий): strategy/<вариант>/scripts/,
там же README.md, который ведёт сам Claude. На партию каждый Claude-игрок получает свою копию
(~/.cache/heroes-claude/ws/<сессия>/): соперник того же варианта не должен видеть, что Claude
пишет по ходу партии. После партии изменения возвращаются в общую папку; если общий файл тем
временем изменил другой игрок, своя версия кладётся рядом с пометкой.

Claude Code запускается в --restricted: файловые инструменты видят только рабочую папку и
код игры (heroes/, data/, docs/), писать можно только в рабочую папку, из Bash разрешена одна
команда — heroes-run (heroes/agent_run.py), которая запускает Python в bwrap без сети.
"""
from __future__ import annotations

import filecmp
import os
import shutil
import sys
from pathlib import Path

from ..engine.data import DATA_DIR
from ..protocol import notes

ROOT = DATA_DIR.parent
TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
READ_DIRS = [ROOT / "heroes", ROOT / "data", ROOT / "docs"]
SKIP = {"__pycache__"}


def available() -> bool:
    """Песочница — bwrap, он есть только в Linux."""
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def readme_template(variant: str) -> str:
    return (f"# Helper scripts of Claude players ({variant})\n\n"
            "Python helper scripts written by Claude players of this variant (model and effort level) during their "
            "games, for future games with any faction. Run one with `heroes-run <script.py> [args]`; the simulation "
            f"API is in `{ROOT / 'heroes' / 'sim.py'}`.\n\n"
            "## Scripts\n\n(none yet)\n")


def files(folder: Path) -> dict[str, Path]:
    return {str(f.relative_to(folder)): f for f in folder.rglob("*")
            if f.is_file() and not SKIP & set(f.relative_to(folder).parts)}


def same(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return filecmp.cmp(a, b, shallow=False)


class Workspace:
    def __init__(self, variant: str, session_id: str, workdir: Path):
        self.variant = variant
        self.shared = notes.NOTES_DIR / variant / "scripts"
        self.dir = workdir / "ws" / session_id
        self.base = workdir / "ws" / f"{session_id}.base"   # общая папка на начало партии — для слияния
        self.game_file = workdir / "sim" / f"{session_id}.json"
        self.bin = workdir / "bin" / session_id

    def prepare(self) -> None:
        """Создать рабочую папку (если её ещё нет — после загрузки сохранения она уже есть) и heroes-run."""
        self.shared.mkdir(parents=True, exist_ok=True)
        if not (self.shared / "README.md").exists():
            (self.shared / "README.md").write_text(readme_template(self.variant), encoding="utf-8")
        if not self.dir.exists():
            shutil.copytree(self.shared, self.dir, ignore=shutil.ignore_patterns(*SKIP))
            shutil.copytree(self.shared, self.base, ignore=shutil.ignore_patterns(*SKIP))
        self.game_file.parent.mkdir(parents=True, exist_ok=True)
        self.bin.mkdir(parents=True, exist_ok=True)
        launcher = self.bin / "heroes-run"
        # Пути зашиты в запускалку: Claude не может подменить их переменными окружения.
        launcher.write_text("#!/bin/sh\n"
                            f'PYTHONPATH="{ROOT}" exec "{sys.executable}" -m heroes.agent_run '
                            f'--workspace "{self.dir}" --game "{self.game_file}" -- "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)

    def prompt_text(self) -> str:
        """Раздел системного промпта об инструментах."""
        return (
            "TOOLS. You have tools: Read, Glob, Grep, Write, Edit and Bash (only the heroes-run command). Each decision "
            "has a time limit, so use them where it matters — key decisions and the start of the game — not on every question.\n"
            f"- Your workspace (the current directory) is your scripts folder: Python helper scripts that you write, keep "
            "and reuse in later games with any faction. Only this folder is writable. Keep its README.md up to date: "
            "what each script does and how to call it — your future self reads it first. Prefer improving a few "
            "reusable scripts over writing many one-off ones; do not keep plans of the current game there.\n"
            "- Run Python: `heroes-run <script.py> [args]` or `heroes-run -c '<code>'`. It runs isolated: standard "
            "library only, no network, 120 s limit.\n"
            "- Simulation: in a script, `from heroes import sim; s = sim.load()` gives a copy of the game exactly at "
            "the question you are answering, and `s.play({s.me: [your answers...]}, rounds=2)` plays on with chosen "
            f"answers and opponent policies and returns the scores. Read {ROOT / 'heroes' / 'sim.py'} for the API. "
            "Any player's full board as text: `heroes.protocol.render_text.board(s.game, q, own=False)`.\n"
            "- You can read the engine code (heroes/), faction data (data/) and the rules (docs/RULES.md, in Russian): "
            f"{', '.join(str(d) for d in READ_DIRS)}. If the engine and your reading of the rules disagree, the engine "
            "decides — and report it in rules_unclear."
        )

    def cli_args(self) -> list[str]:
        return ["--restricted", "--tools", TOOLS, "--permission-mode", "dontAsk",
                "--allowedTools", f"Edit(/{self.dir}/**)", "Bash(heroes-run *)",
                "--add-dir", *(str(d) for d in READ_DIRS if d.exists())]

    def env(self) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}

    def sync_back(self, label: str) -> list[str]:
        """Вернуть изменения в общую папку; label — пометка для конфликтных копий. Возвращает изменённые пути."""
        if not self.dir.exists():
            return []
        mine, base, shared = files(self.dir), files(self.base), files(self.shared)
        changed = []
        for rel in sorted(set(mine) | set(base)):
            if same(mine.get(rel), base.get(rel)):
                continue                                   # не трогал
            if same(mine.get(rel), shared.get(rel)):
                continue                                   # другой игрок сделал то же самое
            target = self.shared / rel
            if not same(shared.get(rel), base.get(rel)):   # общий файл тем временем изменил другой игрок
                if rel not in mine:
                    continue
                target = target.with_name(f"{target.stem}.{label}{target.suffix}")
            if rel in mine:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mine[rel], target)
            else:
                target.unlink(missing_ok=True)
            changed.append(str(target.relative_to(self.shared)))
        return changed

    def cleanup(self) -> None:
        for path in (self.dir, self.base, self.bin):
            shutil.rmtree(path, ignore_errors=True)
        self.game_file.unlink(missing_ok=True)
