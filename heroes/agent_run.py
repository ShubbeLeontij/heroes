"""heroes-run: запуск Python-скрипта Claude-игрока в песочнице bwrap.

Внутри песочницы нет сети; видны только системные файлы (/usr), код игры (heroes/, data/) —
на чтение, рабочая папка Claude — на запись и срез партии для симуляции (heroes/sim.py) — на чтение.
Логи, сохранения, заметки и сессии соперников не видны.

Вызывается запускалкой из рабочей папки (heroes/players/workspace.py):
  python -m heroes.agent_run --workspace <папка> --game <срез партии> -- <аргументы python>
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .engine.data import DATA_DIR
from .sim import GAME_ENV

ROOT = DATA_DIR.parent
TIMEOUT = 120
MAX_OUTPUT = 20000


def bwrap_command(workspace: Path, game: Path, args: list[str]) -> list[str]:
    cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
           "--ro-bind", "/usr", "/usr", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for top in ("/bin", "/lib", "/lib64", "/sbin"):
        if os.path.islink(top):
            cmd += ["--symlink", os.readlink(top), top]
        elif os.path.isdir(top):
            cmd += ["--ro-bind", top, top]
    python = os.path.realpath(sys.executable)
    if not python.startswith("/usr/"):                 # Python не из системы — нужен его каталог
        cmd += ["--ro-bind", sys.base_prefix, sys.base_prefix]
    for folder in (ROOT / "heroes", ROOT / "data"):
        cmd += ["--ro-bind", str(folder), str(folder)]
    cmd += ["--bind", str(workspace), str(workspace)]
    if game.exists():
        cmd += ["--ro-bind", str(game), str(game)]
    cmd += ["--chdir", str(workspace), "--setenv", "PYTHONPATH", str(ROOT), "--setenv", GAME_ENV, str(game),
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONIOENCODING", "utf-8",
            "--setenv", "HOME", str(workspace), python, *args]
    return cmd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="heroes-run", description="Run a Python script in the sandbox: "
                                 "heroes-run <script.py> [args] or heroes-run -c '<code>'")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("args", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)
    args = ns.args[1:] if ns.args[:1] == ["--"] else ns.args
    if not args:
        print("usage: heroes-run <script.py> [args] | heroes-run -c '<code>'")
        return 2
    try:
        proc = subprocess.run(bwrap_command(Path(ns.workspace), Path(ns.game), args), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"Stopped: the script ran longer than {TIMEOUT} s")
        return 124
    output = proc.stdout + proc.stderr
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n… (output cut at {MAX_OUTPUT} characters)"
    print(output, end="")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
