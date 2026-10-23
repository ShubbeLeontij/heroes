"""Claude как игрок: каждое решение — вызов Claude Code CLI в неинтерактивном режиме.

  claude -p --model ... --tools "" --output-format json --json-schema <схема> --resume <сессия>

У каждого Claude-игрока одна сессия на партию: первый вызов создаёт её (--session-id
и системный промпт с правилами), дальше — --resume, так что Claude помнит ход партии и
свои планы. Вызовы идут из отдельной пустой папки без настроек, MCP и инструментов,
чтобы не подтягивать проектную память, хуки и CLAUDE.md пользователя.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..protocol import prompts, schemas
from .base import Player

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game, GameView

DEFAULT_MODEL = "claude-opus-5-5"
WORKDIR = Path.home() / ".cache" / "heroes-claude"
TRANSCRIPTS = Path("logs") / "claude"


class ClaudeError(RuntimeError):
    pass


def unwrap(answer: Any) -> Any:
    """Схема answer общая (любой тип), и модель иногда присылает ответ, закодированный в JSON
    ещё раз: '"Чарил"' вместо 'Чарил', '{"take": true}' строкой. Такое раскрываем сами."""
    if isinstance(answer, str) and answer.strip()[:1] in ('"', "{", "["):
        try:
            return json.loads(answer)
        except json.JSONDecodeError:
            pass
    return answer


class ClaudePlayer(Player):
    parallel_safe = True  # тайные решения можно запрашивать одновременно у всех Claude-игроков

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str | None = None,
        session_id: str | None = None,
        timeout: float = 600,
        retries: int = 2,
        on_comment: Callable[[str, str, str], None] | None = None,
        transcript_dir: Path | None = TRANSCRIPTS,
        workdir: Path = WORKDIR,
    ):
        """session_id — продолжить существующую сессию (после загрузки сохранения).
        on_comment(имя, тип решения, комментарий) — показать замысел Claude (например, зрителю)."""
        self.model = model
        self.effort = effort
        self.session_id = session_id or str(uuid.uuid4())
        self.started = session_id is not None
        self.resumed = session_id is not None
        self.timeout = timeout
        self.retries = retries
        self.on_comment = on_comment
        self.transcript_dir = transcript_dir
        self.workdir = workdir
        self.binary = shutil.which("claude")
        self.seen_log: int | None = None
        self.pending_error: str | None = None
        self.cost_usd = 0.0
        self.calls = 0
        self._lock = threading.Lock()
        self._decide_lock = threading.Lock()

    # --- Player -------------------------------------------------------------------------------

    def decide(self, decision: Decision, view: GameView) -> Any:
        # Тайные решения идут параллельно, и у одного игрока их бывает два сразу (перемещение и гидра наги):
        # одна сессия Claude отвечает на них по очереди.
        with self._decide_lock:
            return self._decide(decision, view)

    def _decide(self, decision: Decision, view: GameView) -> Any:
        game, p = view.game, view.player
        game.meta.setdefault("claude_sessions", {})[str(p)] = self.session_id
        spec = schemas.spec_for(game, decision)
        prompt = prompts.question(game, p, decision, spec, self.new_events(game), self.pending_error)
        self.pending_error = None
        last_error = None
        for _ in range(self.retries + 1):
            system = None if self.started else prompts.system_prompt(game, p)
            try:
                result = self.call(prompt, schemas.RESPONSE_SCHEMA, system)
                answer = spec.convert(unwrap(result["answer"]))
            except ClaudeError as e:
                last_error = str(e)
                continue
            except (KeyError, TypeError, AttributeError, ValueError) as e:
                last_error = f"answer does not match the schema: {e}"
                prompt = f"Could not parse your answer ({last_error}). Answer the same question strictly by the schema."
                continue
            comment = str(result.get("comment", "")).strip()
            if comment and self.on_comment:
                self.on_comment(game.state.players[p].name, decision.kind, comment)
            return answer
        game.log(f"⚠ {game.state.players[p].name}: Claude did not answer ({last_error}), the default was used")
        return decision.default

    def on_invalid(self, decision, answer, error):
        self.pending_error = error

    def new_events(self, game: Game) -> list[str]:
        """События с прошлого вопроса; в первый раз — только хвост лога (важно после загрузки)."""
        log = game.state.log
        if self.seen_log is None:
            events = log[max(0, len(log) - 60):]
            if self.resumed:
                events = ["(the game was loaded from a save; recent events below)", *events]
        else:
            events = log[self.seen_log:]
        self.seen_log = len(log)
        return events

    # --- вызов CLI ------------------------------------------------------------------------------

    def call(self, prompt: str, schema: dict, system: str | None) -> dict:
        if not self.binary:
            raise ClaudeError("The claude executable (Claude Code CLI) was not found")
        self.workdir.mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, "-p", "--model", self.model, "--tools", "", "--strict-mcp-config",
               "--setting-sources", "", "--output-format", "json", "--json-schema", json.dumps(schema, ensure_ascii=False)]
        if self.effort:
            cmd += ["--effort", self.effort]
        if system is None:
            cmd += ["--resume", self.session_id]
        else:
            system_file = self.workdir / f"{self.session_id}.system.md"
            system_file.write_text(system, encoding="utf-8")
            cmd += ["--session-id", self.session_id, "--system-prompt-file", str(system_file)]
        started = time.monotonic()
        try:
            # Кодировка явно: на Windows по умолчанию cp1251, а Claude Code читает и пишет UTF-8.
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=self.timeout, cwd=self.workdir)
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"no answer within {self.timeout:.0f} s") from None
        elapsed = time.monotonic() - started
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise ClaudeError(f"exit code {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}") from None
        if out.get("session_id") == self.session_id:
            self.started = True
        self.calls += 1
        # total_cost_usd — накопленная стоимость всей сессии, а не одного вызова.
        self.cost_usd = max(self.cost_usd, out.get("total_cost_usd") or 0)
        result = out.get("structured_output")
        if result is None and isinstance(out.get("result"), str):
            try:
                result = json.loads(out["result"])
            except json.JSONDecodeError:
                result = None
        self.write_transcript(prompt, system, out, result, elapsed)
        if out.get("is_error") or not isinstance(result, dict):
            raise ClaudeError(f"CLI error: {out.get('subtype')} {str(out.get('result'))[:300]}")
        return result

    def write_transcript(self, prompt: str, system: str | None, out: dict, result: Any, elapsed: float) -> None:
        if not self.transcript_dir:
            return
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(elapsed, 1),
                  "cost_usd": out.get("total_cost_usd"), "system": system, "prompt": prompt,
                  "response": result, "raw_result": None if result else out.get("result")}
        with self._lock, open(self.transcript_dir / f"{self.session_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
