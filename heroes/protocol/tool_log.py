"""Учёт инструментов Claude-игрока: что и на каком вопросе он запускал.

Claude Code вызывается с --output-format stream-json: в потоке видны все его вызовы инструментов
(Bash, Read, Grep…), кроме служебного StructuredOutput — так ответ и приходит. Отчёт составляется
автоматически, без участия модели.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SERVICE = {"StructuredOutput"}
DETAIL_LIMIT = 110


def short(text: str, limit: int = DETAIL_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def tool_name(name: str, args: dict) -> str:
    """Имя в отчёте: у Bash — какая из разрешённых команд (heroes-run, heroes-tool)."""
    if name == "Bash":
        words = str(args.get("command", "")).split()
        return words[0] if words else name
    return name


def describe(name: str, args: dict) -> str:
    if name == "Bash":
        words = str(args.get("command", "")).split(maxsplit=1)
        return short(words[1] if len(words) > 1 else "")
    for key in ("file_path", "pattern", "path"):
        if args.get(key):
            return short(args[key])
    return short(json.dumps(args, ensure_ascii=False))


def parse_stream(stdout: str) -> tuple[dict | None, list[dict]]:
    """Вывод claude -p --output-format stream-json → (итоговая запись result, вызовы инструментов
    [{tool, detail}]). Итога нет (None) — вывод оборван или это не stream-json."""
    result, calls = None, []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") not in SERVICE:
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    calls.append({"tool": tool_name(block["name"], args), "detail": describe(block["name"], args)})
        elif event.get("type") == "result":
            result = event
    return result, calls


def report(who: str, entries: list[dict], questions: int, tools: bool) -> str:
    """Отчёт: по вопросам (раунд · вид решения) — инструменты и аргументы, итог по инструментам.
    entries — [{round, kind, tool, detail}]; questions — сколько вызовов Claude было всего."""
    if not tools:
        return f"Tools used by {who}: none were available (no bwrap or --no-tools); only view_boards could be used."
    if not entries:
        return f"Tools used by {who}: none in {questions} calls (tools were available)."
    lines = [f"Tools used by {who}: {len(entries)} calls in {len({(e['round'], e['kind']) for e in entries})} "
             f"of {questions} questions"]
    last = None
    for e in entries:
        where = (e["round"], e["kind"])
        head = f"  round {e['round']:>2} · {e['kind']}:" if where != last else " " * (len(f"  round {e['round']:>2} · {e['kind']}:"))
        lines.append(f"{head} {e['tool']}" + (f" {e['detail']}" if e["detail"] else ""))
        last = where
    counts = Counter(e["tool"] for e in entries)
    lines.append("  by tool: " + ", ".join(f"{tool} {n}" for tool, n in counts.most_common()))
    return "\n".join(lines)


def save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
