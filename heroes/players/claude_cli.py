"""Claude как игрок: каждое решение — вызов Claude Code CLI в неинтерактивном режиме.

  claude -p --model ... --output-format json --json-schema <схема> --resume <сессия>

У каждого Claude-игрока одна сессия на партию: первый вызов создаёт её (--session-id
и системный промпт с правилами), дальше — --resume, так что Claude помнит ход партии и
свои планы. Вызовы идут без пользовательских настроек и MCP, чтобы не подтягивать проектную
память, хуки и CLAUDE.md пользователя.

Инструменты (heroes/players/workspace.py): если есть песочница bwrap, Claude работает из своей
рабочей папки со скриптами и может запускать их и симуляцию партии (heroes/sim.py); иначе — без
инструментов, из пустой папки.

Опыт Claude ведётся по вариантам (модель и уровень усилий): заметки по фракциям с напоминаниями
(heroes/protocol/notes.py), скрипты, история в git, результаты и журнал путаницы в правилах
(heroes/protocol/journal.py).
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

from .. import sim
from ..engine import scoring
from ..protocol import journal, notes, prompts, schemas, tool_log
from ..protocol import render_text as R
from .base import Player
from .random_bot import RandomBot
from .workspace import Workspace, available as tools_available

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


def place_of(game: Game, p: int) -> tuple[int, int, bool]:
    """Итог игрока: (ПО, место, победитель)."""
    scores = scoring.final_scores(game)
    vp = scores[p]["total"]
    return vp, 1 + sum(s["total"] > vp for s in scores), p in scoring.winners(game, scores)


def opponent_kinds(game: Game, p: int) -> dict[str, str]:
    """id фракции соперника → кто за неё играет: human, random bot или Claude (с вариантом)."""
    def kind(player: Player) -> str:
        if isinstance(player, RandomBot):
            return "random bot"
        return f"Claude ({player.variant})" if isinstance(player, ClaudePlayer) else "human"
    return {game.state.players[q].faction: kind(player) for q, player in enumerate(game.players) if q != p}


class ClaudePlayer(Player):
    parallel_safe = True  # тайные решения можно запрашивать одновременно у всех Claude-игроков

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
        retries: int = 2,
        on_comment: Callable[[str, str, str], None] | None = None,
        transcript_dir: Path | None = TRANSCRIPTS,
        workdir: Path = WORKDIR,
        tools: bool | None = None,
    ):
        """session_id — продолжить существующую сессию (после загрузки сохранения).
        on_comment(имя, тип решения, комментарий) — показать замысел Claude (например, зрителю).
        tools — дать Claude инструменты (None — если есть песочница)."""
        self.model = model
        self.effort = effort
        self.variant = notes.variant(model, effort)
        self.session_id = session_id or str(uuid.uuid4())
        self.started = session_id is not None
        self.resumed = session_id is not None
        self.tools = tools_available() if tools is None else tools
        self.workspace = Workspace(self.variant, self.session_id, workdir) if self.tools else None
        self.prepared = False
        self.timeout = timeout or (900 if self.tools else 600)
        self.retries = retries
        self.on_comment = on_comment
        self.transcript_dir = transcript_dir
        self.workdir = workdir
        self.binary = shutil.which("claude")
        self.seen_log: int | None = None
        # ошибка движка в прошлом ответе и вопрос, к которому она относится: после ответа по умолчанию
        # движок задаёт уже другой вопрос, и ошибка в него не попадает
        self.pending_error: tuple[Decision, str] | None = None
        self.last_comment = ""
        self.where: tuple[int, str] = (0, "")        # раунд и вид вопроса, на который идёт вызов Claude
        self.reported = 0                            # сколько записей tool_log уже вошло в отчёт перед разбором
        self.tool_log: list[dict] = []               # {round, kind, tool, detail}: что запускал Claude
        self.game: Game | None = None
        self.cost_usd = 0.0
        self.calls = 0
        self._lock = threading.Lock()
        self._decide_lock = threading.Lock()

    # --- Player -------------------------------------------------------------------------------

    def decide(self, decision: Decision, view: GameView) -> Any:
        # Тайные решения идут параллельно, и у одного игрока их бывает два сразу (перемещение и гидра наги):
        # одна сессия Claude отвечает на них по очереди — или на оба одним вызовом (decide_together).
        with self._decide_lock:
            return self._decide(decision, view)

    def decide_together(self, decisions: list[Decision], view: GameView) -> list[Any]:
        """Несколько тайных решений сразу — один вопрос и один вызов Claude. Проверяет ответы движок;
        отклонённое он переспросит по отдельности."""
        with self._decide_lock:
            if len({d.kind for d in decisions}) < len(decisions):
                return [self._decide(d, view) for d in decisions]
            game, p = view.game, view.player
            self.begin(game, p)
            spec = schemas.combined({d.kind: schemas.spec_for(game, d) for d in decisions})
            prompt = prompts.questions(game, p, decisions, spec, self.new_events(game), None, self.variant)
            answer = self.query(game, p, decisions, prompt, spec)
            return [d.default for d in decisions] if answer is None else [answer[d.kind] for d in decisions]

    def _decide(self, decision: Decision, view: GameView) -> Any:
        game, p = view.game, view.player
        self.begin(game, p)
        spec = schemas.spec_for(game, decision)
        upgrade = schemas.with_upgrade(game, decision, spec)
        pending, self.pending_error = self.pending_error, None
        error = pending[1] if pending and pending[0] is decision else None
        prompt = prompts.question(game, p, decision, upgrade or spec, self.new_events(game), error, self.variant)
        answer = self.query(game, p, [decision], prompt, upgrade or spec)
        return decision.default if answer is None else answer

    def begin(self, game: Game, p: int) -> None:
        self.game = game
        game.meta.setdefault("claude_sessions", {})[str(p)] = self.session_id
        if not self.started:
            self.make_plan(game, p)

    def query(self, game: Game, p: int, decisions: list[Decision], prompt: str, spec: schemas.Spec) -> Any:
        """Задать вопрос (с повторами при сбое и ответе не по схеме); None — Claude так и не ответил."""
        kind = "+".join(d.kind for d in decisions)
        last_error = None
        shown: set[int] = set()
        for _ in range(self.retries + 1):
            try:
                result = self.ask_with_boards(game, p, kind, prompt, shown)
                raw = unwrap(result["answer"])
            except ClaudeError as e:
                last_error = str(e)
                continue
            except KeyError as e:
                last_error = f"answer does not match the schema: {e}"
                prompt = f"Could not parse your answer ({last_error}). Answer the same question strictly by the schema."
                continue
            self.report_unclear(game, p, kind, result)
            try:
                answer = spec.convert(raw)
            except (KeyError, TypeError, AttributeError, ValueError) as e:
                last_error = f"answer does not match the schema: {e}"
                self.confusion(game, p, kind, "answer not in the expected format",
                               {"Question": "\n\n".join(d.text for d in decisions), "Answer": raw, "Error": last_error,
                                "Claude's comment": result.get("comment")})
                prompt = f"Could not parse your answer ({last_error}). Answer the same question strictly by the schema."
                continue
            comment = str(result.get("comment", "")).strip()
            self.last_comment = comment
            if comment and self.on_comment:
                self.on_comment(game.state.players[p].name, decisions[0].kind, comment)
            return answer
        game.log(f"⚠ {game.state.players[p].name}: Claude did not answer ({last_error}), the default was used")
        return None

    def on_invalid(self, decision, answer, error):
        self.pending_error = (decision, error)
        if self.game is not None:
            self.confusion(self.game, decision.player, decision.kind, "answer rejected by the engine",
                           {"Question": decision.text, "Answer": answer, "Engine": error,
                            "Claude's comment": self.last_comment})

    # --- начало и конец партии ------------------------------------------------------------------

    def system_prompt(self, game: Game, p: int) -> str:
        return prompts.system_prompt(game, p, self.variant, self.workspace.prompt_text() if self.workspace else None)

    def make_plan(self, game: Game, p: int) -> None:
        """Перед первым решением: Claude продумывает план на партию против выпавших фракций.
        План остаётся в сессии, а в журнал идёт как замысел (game_plan)."""
        prompt = prompts.game_plan(game, p, opponent_kinds(game, p), self.tools)
        for _ in range(self.retries + 1):
            try:
                result = self.ask(game, p, "game_plan", prompt)
            except ClaudeError:
                continue
            self.report_unclear(game, p, "game_plan", result)
            plan = unwrap(result.get("answer"))
            plan = plan.strip() if isinstance(plan, str) else "" if plan is None else json.dumps(plan, ensure_ascii=False)
            if plan and self.on_comment:
                self.on_comment(game.state.players[p].name, "game_plan", plan)
            return
        # Не вышло — партия идёт без плана, системный промпт уйдёт с первым решением.

    def reflect(self, game: Game, p: int) -> tuple[str, str] | None:
        """После партии: Claude разбирает свою игру и по желанию переписывает разделы заметок фракции
        и свои скрипты; всё изменённое — коммит в strategy/. Возвращает (разбор, что сделано) или None,
        если сессии не было."""
        if not self.started:
            return None
        faction = game.state.players[p].faction
        opponents = opponent_kinds(game, p)
        allowed = [notes.GENERAL, notes.REMINDERS, *(f for f, kind in opponents.items() if kind != "random bot")]
        prompt = prompts.reflection(game, p, self.variant, opponents, allowed, self.new_events(game),
                                    journal.history(self.variant, faction), self.tools, self.tools_report(game, p))
        self.reported = len(self.tool_log)
        review, changes, rules_add, tools_missing, comment, last_error = "", None, [], [], "", None
        for _ in range(self.retries + 1):
            try:
                result = self.ask(game, p, "game_over", prompt)
                answer = unwrap(result["answer"])
                review = answer.get("review") if isinstance(answer, dict) else None
                if not isinstance(review, str) or not review.strip():
                    raise ValueError('"review" must be a non-empty string')
                changes = notes.validate(answer, allowed)
                rules_add = notes.validate_rules_add(answer)
                tools_missing = notes.validate_tools_missing(answer)
            except ClaudeError as e:
                last_error = str(e)
                continue
            except (KeyError, TypeError, ValueError) as e:
                last_error = str(e)
                prompt = f"YOUR ANSWER WAS REJECTED: {last_error}\nAnswer the same question again."
                continue
            self.report_unclear(game, p, "game_over", result)
            review, comment, last_error = review.strip(), str(result.get("comment", "")).strip(), None
            break
        else:
            review = ""
        outcome = []
        paths = [journal.root() / "results.csv"]
        if last_error:
            outcome.append(f"strategy notes left unchanged — no valid answer ({last_error})")
        elif changes is None:
            outcome.append("no changes to the strategy notes")
        else:
            try:
                paths.append(notes.update(self.variant, faction, changes))
                outcome.append(f"strategy notes updated ({', '.join(changes)}): {notes.path(self.variant, faction)}")
            except OSError as e:
                outcome.append(f"could not write the strategy notes: {e}")
        if rules_add and not last_error:
            try:
                if rules := notes.add_rules(rules_add):
                    paths.append(rules)
                    outcome.append(f"special rules added: {'; '.join(rules_add)}")
            except (OSError, ValueError) as e:
                outcome.append(f"could not add the special rules: {e}")
        if tools_missing and not last_error:
            where = f"{self.variant} · {game.state.players[p].name} ({game.faction_of(p).name}) · game {str(game.meta.get('game_id'))[:8]}"
            try:
                journal.log_tools_wanted(where, tools_missing)
                paths.append(journal.tools_wanted_path())
                outcome.append(f"tools missing: {'; '.join(tools_missing)}")
            except OSError as e:
                outcome.append(f"could not record the missing tools: {e}")
        if self.workspace:
            scripts = self.workspace.sync_back(faction)
            if scripts:
                outcome.append("scripts changed: " + ", ".join(scripts))
            paths.append(self.workspace.shared)
            self.workspace.cleanup()
        vp, place, _ = place_of(game, p)
        name = notes.faction_names()[faction]
        body = (f"Variant {self.variant}. Game {game.meta.get('game_id')}: place {place} of {len(game.players)}, {vp} VP. "
                f"Opponents: {prompts.played_by(opponents)}." + (f"\n\nReview:\n{review}" if review else ""))
        if journal.commit(paths, f"{name}: {comment or 'game results'}"[:150], body):
            outcome.append("committed to git")
        return review, "; ".join(outcome)

    def tools_report(self, game: Game, p: int, review: bool = False) -> str:
        """Автоматический отчёт (не от модели): какие инструменты и на каком вопросе запускал Claude.
        Идёт в журнал и в вопрос о разборе партии, потом — отдельно то, что он запускал во время разбора
        (review=True); сохраняется в logs/claude/<сессия>.tools.txt."""
        entries = self.tool_log[self.reported:] if review else self.tool_log[:self.reported or len(self.tool_log)]
        who = game.state.players[p].name + (" while reviewing" if review else "")
        text = tool_log.report(who, entries, self.calls, self.tools)
        if self.transcript_dir and not review:
            try:
                tool_log.save(self.transcript_dir / f"{self.session_id}.tools.txt", text)
            except OSError:
                pass
        return text

    def result_row(self, game: Game, p: int) -> dict:
        """Строка results.csv для этого игрока."""
        vp, place, winner = place_of(game, p)
        return {"time": time.strftime("%Y-%m-%d %H:%M"), "game_id": game.meta.get("game_id"), "model": self.model,
                "effort": self.effort or "default", "player": game.state.players[p].name,
                "faction": game.state.players[p].faction, "vp": vp, "place": place, "players": len(game.players),
                "winner": int(winner),
                "opponents": "; ".join(f"{f}: {kind}" for f, kind in opponent_kinds(game, p).items())}

    # --- журнал путаницы ------------------------------------------------------------------------

    def confusion(self, game: Game, p: int, kind: str, source: str, details: dict[str, Any]) -> None:
        pl = game.state.players[p]
        where = (f"{self.variant} · {pl.name} ({game.faction_of(p).name}) · game {str(game.meta.get('game_id'))[:8]} "
                 f"· round {game.state.round} · {kind}")
        text = {}
        for key, value in details.items():
            if value is None or value == "":
                continue
            value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            text[key] = value if len(value) <= 800 else value[:800] + " …"
        if self.transcript_dir:
            text["Transcript"] = f"{self.transcript_dir}/{self.session_id}.jsonl"
        try:
            journal.log_confusion(source, where, text)
        except OSError:
            pass

    def report_unclear(self, game: Game, p: int, kind: str, result: dict) -> None:
        unclear = str(result.get("rules_unclear") or "").strip()
        if unclear:
            self.confusion(game, p, kind, "question from Claude", {"Claude": unclear})

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

    def ask(self, game: Game, p: int, kind: str, prompt: str) -> dict:
        """Один вопрос Claude; первый вызов сессии несёт системный промпт. Для симуляции
        сохраняется текущий момент партии (у нескольких вопросов сразу, «destination+hydra_destination», — первый)."""
        self.where = (game.state.round, kind)
        if self.workspace:
            if not self.prepared:
                self.workspace.prepare()
                self.prepared = True
            sim.export(game, p, kind.split("+")[0], self.workspace.game_file)
        return self.call(prompt, schemas.RESPONSE_SCHEMA, None if self.started else self.system_prompt(game, p))

    def ask_with_boards(self, game: Game, p: int, kind: str, prompt: str, shown: set[int]) -> dict:
        """Вопрос, перед ответом на который Claude может посмотреть полные планшеты игроков (view_boards):
        тогда он получает их и тот же вопрос ещё раз, а ответ с просьбой не засчитывается. Каждый планшет
        показывается один раз за решение (shown), так что вопрос не превращается в бесконечный просмотр."""
        result = self.ask(game, p, kind, prompt)
        wanted = [q for q in self.requested_boards(game, result.get("view_boards")) if q not in shown]
        if not wanted:
            return result
        shown.update(wanted)
        self.tool_log.append({"round": game.state.round, "kind": kind, "tool": "view_boards",
                              "detail": ", ".join(game.state.players[q].name for q in wanted)})
        boards = [R.board(game, q, own=q == p) for q in wanted]
        return self.ask(game, p, kind, "\n\n".join(boards) + f"\n\nNow answer the same question ({kind}).")

    @staticmethod
    def requested_boards(game: Game, request: Any) -> list[int]:
        """Номера игроков из view_boards: номера или имена; непонятное пропускается."""
        if not isinstance(request, list):
            return []
        names = {pl.name.casefold(): q for q, pl in enumerate(game.state.players)}
        wanted = []
        for item in request:
            if isinstance(item, bool):
                continue
            text = str(item).strip()
            q = item if isinstance(item, int) else int(text) if text.isdigit() else names.get(text.casefold())
            if q is not None and 0 <= q < len(game.state.players) and q not in wanted:
                wanted.append(q)
        return wanted

    def call(self, prompt: str, schema: dict, system: str | None) -> dict:
        if not self.binary:
            raise ClaudeError("The claude executable (Claude Code CLI) was not found")
        if self.game is not None and self.game.stopping.is_set():   # Ctrl+C убил прошлый вызов — не повторять
            raise ClaudeError("the game is stopping")
        self.workdir.mkdir(parents=True, exist_ok=True)
        tools = self.workspace.cli_args() if self.workspace else ["--tools", ""]
        cmd = [self.binary, "-p", "--model", self.model, *tools, "--strict-mcp-config",
               "--setting-sources", "", "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(schema, ensure_ascii=False)]
        if self.effort:
            cmd += ["--effort", self.effort]
        if system is None:
            cmd += ["--resume", self.session_id]
        else:
            system_file = self.workdir / f"{self.session_id}.system.md"
            system_file.write_text(system, encoding="utf-8")
            cmd += ["--session-id", self.session_id, "--system-prompt-file", str(system_file)]
        cwd = self.workspace.dir if self.workspace else self.workdir
        env = self.workspace.env() if self.workspace else None
        started = time.monotonic()
        try:
            # Кодировка явно: на Windows по умолчанию cp1251, а Claude Code читает и пишет UTF-8.
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=self.timeout, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"no answer within {self.timeout:.0f} s") from None
        elapsed = time.monotonic() - started
        out, used = tool_log.parse_stream(proc.stdout)
        if out is None:
            raise ClaudeError(f"exit code {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}")
        self.tool_log += [{"round": self.where[0], "kind": self.where[1], **call} for call in used]
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
        self.write_transcript(prompt, system, out, result, elapsed, used)
        if out.get("is_error") or not isinstance(result, dict):
            raise ClaudeError(f"CLI error: {out.get('subtype')} {str(out.get('result'))[:300]}")
        return result

    def write_transcript(self, prompt: str, system: str | None, out: dict, result: Any, elapsed: float,
                         used: list[dict]) -> None:
        if not self.transcript_dir:
            return
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(elapsed, 1),
                  "cost_usd": out.get("total_cost_usd"), "system": system, "prompt": prompt,
                  "response": result, "raw_result": None if result else out.get("result"),
                  "tools": [f"{c['tool']} {c['detail']}".strip() for c in used]}
        with self._lock, open(self.transcript_dir / f"{self.session_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
