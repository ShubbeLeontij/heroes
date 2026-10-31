"""Замер расхода токенов Claude-игрока на короткой партии: Claude (Люди, первое место, первый ход) против трёх
случайных ботов, только первые раунды (по умолчанию 3). Нужен, чтобы сравнивать версии промптов между собой.

    .venv/bin/python tools/token_bench.py --label baseline          # прогон
    .venv/bin/python tools/token_bench.py --compare                 # все прогоны рядом
    .venv/bin/python tools/token_bench.py --compare baseline-high baseline --metric output   # выбранные прогоны

Прогон ничего не меняет в strategy/ (заметки, скрипты, results.csv, журнал путаницы) и не пишет в saves/:
после обрезанной партии нет ни разбора, ни подсчёта очков. Всё складывается в logs/bench/<время>_<метка>/:
  transcript/<сессия>.jsonl, .story.md — стенограмма и пересказ, как у обычной партии;
  calls.csv      — по строке на вызов Claude: раунд, вопрос, секунды, токены, стоимость, запуски инструментов;
  summary.json   — итоги, разбивка по видам вопросов, версия кода (git) и отпечатки заметок Claude;
  prompt.diff    — незакоммиченные правки кода (git diff HEAD): какую версию промптов мерили;
  confusion.md   — rules_unclear и отклонённые ответы (в обычной партии они идут в strategy/RULES_CONFUSION.md);
  game.json      — сохранение партии на момент остановки.

Боты и их фракции задаются зерном (--seed), так что соперники в прогонах одинаковые; ход партии всё равно
расходится, как только Claude решает иначе. Для честного сравнения гоняйте несколько прогонов на версию.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heroes import savegame  # noqa: E402
from heroes.cli import FACTIONS, resolve_factions  # noqa: E402
from heroes.engine.game import Game  # noqa: E402
from heroes.players.claude_cli import DEFAULT_EFFORT, DEFAULT_MODEL, ClaudeError, ClaudePlayer  # noqa: E402
from heroes.players.random_bot import RandomBot  # noqa: E402
from heroes.protocol import notes  # noqa: E402

BENCH_DIR = ROOT / "logs" / "bench"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
CLAUDE_FACTION = "humans"
FIELDS = ["n", "round", "kind", "ok", "seconds", "input", "output", "thinking", "cost_usd", "tools"]


class BenchClaude(ClaudePlayer):
    """Claude-игрок, который записывает каждый вызов и не трогает strategy/."""

    def __init__(self, run_dir: Path, **kwargs):
        super().__init__(transcript_dir=run_dir / "transcript", **kwargs)
        self.run_dir = run_dir
        self.rows: list[dict] = []

    def call(self, prompt: str, schema: dict, system: str | None) -> dict:
        tokens, cost, tools, started = dict(self.tokens), self.cost_usd, len(self.tool_log), time.monotonic()
        ok = False
        try:
            result = super().call(prompt, schema, system)
            ok = True
            return result
        finally:
            self.rows.append({
                "n": len(self.rows) + 1, "round": self.where[0], "kind": self.where[1], "ok": int(ok),
                "seconds": round(time.monotonic() - started, 1),
                **{k: self.tokens[k] - tokens[k] for k in ("input", "output", "thinking")},
                "cost_usd": round(self.cost_usd - cost, 4), "tools": len(self.tool_log) - tools,
            })
            print(f"  [{self.rows[-1]['n']}] round {self.where[0]} {self.where[1]}: "
                  f"{self.rows[-1]['seconds']} s, out {self.rows[-1]['output']:,} "
                  f"(thinking {self.rows[-1]['thinking']:,}), ${self.rows[-1]['cost_usd']:.2f}"
                  + ("" if ok else " — FAILED"), flush=True)

    def confusion(self, game, p, kind, source, details) -> None:
        with open(self.run_dir / "confusion.md", "a", encoding="utf-8") as f:
            f.write(f"## round {game.state.round} · {kind} · {source}\n\n"
                    + "\n".join(f"- **{k}:** {v}" for k, v in details.items() if v not in (None, "")) + "\n\n")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False).stdout


def fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else None


def by_kind(rows: list[dict]) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["kind"]].append(r)
    return {kind: {"calls": len(rs), **{k: round(sum(r[k] for r in rs), 4) for k in
                                         ("seconds", "input", "output", "thinking", "cost_usd", "tools")}}
            for kind, rs in sorted(groups.items(), key=lambda kv: -sum(r["output"] for r in kv[1]))}


def totals(rows: list[dict]) -> dict:
    return {"calls": len(rows), "failed": sum(not r["ok"] for r in rows),
            **{k: round(sum(r[k] for r in rows), 4) for k in ("seconds", "input", "output", "thinking", "cost_usd", "tools")}}


def print_table(groups: dict[str, dict], total: dict) -> None:
    print(f"\n{'question':22} {'calls':>5} {'sec':>7} {'input':>11} {'output':>9} {'thinking':>9} {'$':>7} {'tools':>5}")
    for kind, g in [*groups.items(), ("TOTAL", total)]:
        print(f"{kind:22} {g['calls']:5} {g['seconds']:7.0f} {g['input']:11,} {g['output']:9,} {g['thinking']:9,} "
              f"{g['cost_usd']:7.2f} {g['tools']:5}")


def run(args: argparse.Namespace) -> int:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = BENCH_DIR / (stamp + (f"_{args.label}" if args.label else ""))
    run_dir.mkdir(parents=True)
    rng = random.Random(args.seed)
    factions = resolve_factions([CLAUDE_FACTION, *(args.bot_factions or ["random"] * 3)], rng)
    names = ["Claude", "Bot 1", "Bot 2", "Bot 3"]
    claude = BenchClaude(run_dir, model=args.model, effort=args.effort, tools=args.tools)
    players = [claude, *(RandomBot(args.seed + i) for i in range(1, 4))]
    meta = {"players": ["claude", "bot", "bot", "bot"], "seed": args.seed, "bench": args.label,
            "claude": {"model": args.model, "effort": args.effort, "tools": args.tools}}
    game = Game(players, names=names, factions=factions, first_player=0, meta=meta, autosave=None)
    (run_dir / "prompt.diff").write_text(git("diff", "HEAD"), encoding="utf-8")
    print(f"Run: {run_dir.relative_to(ROOT)}\nClaude ({CLAUDE_FACTION}, seat 0, first player) vs bots: "
          + ", ".join(factions[1:]) + f"; {args.rounds} round(s); {args.model}, effort {args.effort}, "
          + claude.tools_status(), flush=True)
    started, interrupted, error = time.monotonic(), False, None
    try:
        game.setup()
        for _ in range(args.rounds):
            game.play_round()
            print(f"--- round {game.state.round} done", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        game.stopping.set()
        print("\nInterrupted: writing what was measured so far.")
    except ClaudeError as e:
        error = str(e)
        print(f"\nStopped: {e}")
    finally:
        if claude.workspace:
            claude.workspace.cleanup()   # без sync_back: скрипты Claude в strategy/scripts/ не меняются
    if args.verbose:
        print("\n".join(game.state.log))
    savegame.save(game, run_dir / "game.json")
    with open(run_dir / "calls.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        writer.writerows(claude.rows)
    variant = notes.variant(args.model, args.effort)
    total, groups = totals(claude.rows), by_kind(claude.rows)
    summary = {
        "label": args.label, "time": stamp, "rounds_played": game.state.round, "rounds": args.rounds,
        "interrupted": interrupted, "error": error, "wall_seconds": round(time.monotonic() - started),
        "model": args.model, "effort": args.effort, "tools": claude.tools, "seed": args.seed, "factions": factions,
        "session": claude.session_id, "session_cost_usd": round(claude.cost_usd, 4),
        # Claude так и не дал годного ответа — движок взял ответ по умолчанию
        "defaults_used": sum("Claude:" in line and "the default was used" in line for line in game.state.log),
        "git": {"head": git("rev-parse", "--short", "HEAD").strip(), "dirty": bool(git("status", "--porcelain", "heroes", "docs"))},
        "inputs": {"notes": fingerprint(notes.path(variant, CLAUDE_FACTION)), "rules_special": fingerprint(notes.rules_path()),
                   "rules_eng": fingerprint(ROOT / "docs" / "RULES_ENG.md")},
        "totals": total, "by_kind": groups,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print_table(groups, total)
    print(f"\nSaved to {run_dir.relative_to(ROOT)}/ (summary.json, calls.csv, prompt.diff, transcript/)")
    return 130 if interrupted else 1 if error else 0


METRICS = {"thinking": "thinking tokens", "output": "output tokens", "cost": "cost, $", "seconds": "seconds"}


def load_runs(select: list[str], last: int | None) -> list[tuple[str, dict, list[dict]]]:
    """Прогоны из logs/bench/ по порядку времени; select — метки или части имени папки (пусто — все):
    если у какого-то прогона метка совпадает точно, берётся только он, иначе — все, у кого это часть имени папки.
    last — только последние N из отобранных."""
    paths = sorted(BENCH_DIR.glob("*/summary.json"))
    summaries = {path: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    labels = {s.get("label") for s in summaries.values()}
    runs = []
    for path, s in summaries.items():
        name = path.parent.name
        if select and not any(x == s.get("label") if x in labels else x in name for x in select):
            continue
        with open(path.parent / "calls.csv", encoding="utf-8") as f:
            rows = [{k: v if k == "kind" else float(v) if k in ("seconds", "cost_usd") else int(v) for k, v in r.items()}
                    for r in csv.DictReader(f)]
        runs.append((name, s, rows))
    return runs[-last:] if last else runs


def compare(select: list[str], last: int | None, metric: str, by_round: bool) -> int:
    """План перед игрой, вопросы по видам (metric на один вызов), по желанию — раунды, и сводная."""
    runs = load_runs(select, last)
    if not runs:
        print(f"No matching runs in {BENCH_DIR.relative_to(ROOT)}/")
        return 0
    head = f"{'run':40} {'effort':>6} {'calls':>5} {'output':>9} {'thinking':>9} {'out/call':>8} {'input':>11} {'tools':>5} {'$':>6} {'min':>5}"

    def line(name: str, s: dict, t: dict) -> str:
        return (f"{name[:40]:40} {s['effort']:>6} {t['calls']:5} {t['output']:9,} {t['thinking']:9,} "
                f"{t['output'] // max(t['calls'], 1):8,} {t['input']:11,} {t['tools']:5} {t['cost_usd']:6.2f} "
                f"{t['seconds'] / 60:5.1f}")

    def table(title: str, pick) -> None:
        print(f"\n{title}\n{head}")
        for name, s, rows in runs:
            print(line(name, s, totals([r for r in rows if pick(r)])))

    table("BEFORE THE GAME (game_plan)", lambda r: r["kind"] == "game_plan")

    # вопросы по видам: строки — вопросы, колонки — прогоны, в клетке — среднее на вызов и (число вызовов)
    key = "cost_usd" if metric == "cost" else metric
    # шапка колонки — три строки: метка, усилие, коммит (* — с незакоммиченной правкой, то есть новая версия
    # промптов поверх этого коммита); повторные прогоны одной и той же версии помечаются #2, #3…
    headers, seen = [], defaultdict(int)
    for name, s, _ in runs:
        commit = s["git"]["head"] + ("*" if s["git"]["dirty"] else "")
        version = (s.get("label") or name[:19], s["effort"], commit)
        seen[version] += 1
        headers.append([version[0] + (f" #{seen[version]}" if seen[version] > 1 else ""), version[1], version[2]])
    kinds: dict[str, float] = defaultdict(float)
    for _, _, rows in runs:
        for r in rows:
            if r["kind"] != "game_plan":
                kinds[r["kind"]] += r[key]
    width = max([12, *(len(h) for hs in headers for h in hs)])
    print(f"\nQUESTIONS: {METRICS[metric]} per call (number of calls)")
    for i, title in enumerate(("question", "effort", "git")):
        print(f"{title:22}" + "".join(f" {hs[i]:>{width}}" for hs in headers))
    for kind in sorted(kinds, key=lambda k: -kinds[k]):
        cells = []
        for _, _, rows in runs:
            rs = [r for r in rows if r["kind"] == kind]
            if not rs:
                cells.append("—")
                continue
            avg = sum(r[key] for r in rs) / len(rs)
            value = f"{avg:.2f}" if metric == "cost" else f"{avg:,.0f}" if metric != "seconds" else f"{avg:.1f}"
            cells.append(f"{value} ({len(rs)})")
        print(f"{kind:22}" + "".join(f" {c:>{width}}" for c in cells))

    if by_round:
        for rnd in sorted({r["round"] for _, _, rows in runs for r in rows if r["kind"] != "game_plan"}):
            table(f"ROUND {rnd} (without game_plan)", lambda r, rnd=rnd: r["kind"] != "game_plan" and r["round"] == rnd)
    print(f"\nTOTAL\n{'run':40} {'git':>9} {'rnd':>3} {head[41:]}")
    for name, s, rows in runs:
        commit = s["git"]["head"] + ("*" if s["git"]["dirty"] else "")
        t = {**totals(rows), "seconds": s["wall_seconds"]}   # вся партия, вместе с ходами ботов
        print(f"{name[:40]:40} {commit:>9} {s['rounds_played']:3} {line('', s, t)[41:]}")
    print("\nmin — Claude's answer time (TOTAL: the whole run). * — uncommitted changes in heroes/ or docs/ "
          "(see prompt.diff of the run)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="", help="short name of the run, e.g. the prompt version (goes into the folder name)")
    ap.add_argument("--rounds", type=int, default=3, help="rounds to play before stopping (default 3)")
    ap.add_argument("--seed", type=int, default=1, help="seed for the bots and their factions (default 1)")
    ap.add_argument("--bot-factions", type=lambda s: s.split(","), metavar="F1,F2,F3",
                    help=f"factions of the three bots: {', '.join(f for f in FACTIONS if f != CLAUDE_FACTION)} or random")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default {DEFAULT_MODEL})")
    ap.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT, help=f"effort (default {DEFAULT_EFFORT})")
    ap.add_argument("--tools", action=argparse.BooleanOptionalAction, default=None,
                    help="give Claude its scripts in the bwrap sandbox (default: if bwrap is installed)")
    ap.add_argument("--verbose", action="store_true", help="print the game log at the end")
    cmp = ap.add_argument_group("comparing runs")
    cmp.add_argument("--compare", nargs="*", metavar="RUN",
                     help="compare runs from logs/bench/ and exit; RUN — a label or a part of the folder name (none — all)")
    cmp.add_argument("--last", type=int, metavar="N", help="--compare: only the last N of the selected runs")
    cmp.add_argument("--metric", choices=METRICS, default="thinking",
                     help="--compare: what the question table shows per call (default thinking)")
    cmp.add_argument("--by-round", action="store_true", help="--compare: also a table for every round")
    args = ap.parse_args(argv)
    if args.compare is not None:
        return compare(args.compare, args.last, args.metric, args.by_round)
    if not 1 <= args.rounds <= 16:
        ap.error("--rounds: from 1 to 16")
    if args.bot_factions and len(args.bot_factions) != 3:
        ap.error("--bot-factions: exactly three, comma-separated")
    try:
        return run(args)
    except ValueError as e:   # неизвестная или повторённая фракция
        raise SystemExit(str(e)) from None


if __name__ == "__main__":
    raise SystemExit(main())
