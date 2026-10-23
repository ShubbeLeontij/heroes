"""Simulation for Claude players' scripts: a copy of the current game to try out the next moves.

Run your scripts with `heroes-run script.py`; inside them:

    from heroes import sim

    s = sim.load()            # the game exactly as it is at the question you are answering now
    s.me, s.round, s.kind     # your player number, the current round, the kind of the current question
    s.game                    # engine Game at that moment (heroes/engine/game.py; state: s.game.state)

    # Play on from this moment. Your answers are given in the same JSON format as the "answer"
    # field of your replies; the first one answers the current question.
    r = s.play({s.me: ["Чарил"]}, rounds=1)
    r.scores[s.me]["total"]   # VP of every player if the game ended right after the simulation
    r.log                     # game log lines produced by the simulation
    r.asked                   # every question asked: Ask(player, kind, text, data, schema, answer)
    r.errors                  # your answers the engine rejected (the default answer was used instead)
    r.game                    # engine Game after the simulation

Policies — what a player answers (a dict player number → policy; players not listed answer "default"):
    "default"                 the engine's default answer (usually the most passive one)
    "random"                  random legal-looking answers (the random bot), seed from `seed`
    [a1, a2, ...]             these answers in order, then "default" (or `then=` policy)
    callable(q, game) -> a    q is an Ask with kind/text/data/schema/default; return an answer in your
                              JSON format, or sim.DEFAULT for the engine default
Opponents' hidden choices are unknown: try several policies for them (e.g. a few random seeds).

rounds=N plays until the end of round N counting the current one (rounds=1 — finish this round);
the simulation never goes past round 16. Each play() starts from the same saved moment, so you can
call it many times. Everything is deterministic except the "random" policy.

Text views of the state (the same as in your questions): heroes.protocol.render_text — board(game, p),
opponents(game, p), game_map(game, p), holdings_line(game, p).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import savegame
from .engine import scoring
from .engine.game import Game
from .engine.state import ROUNDS
from .players.base import Player
from .players.random_bot import RandomBot
from .protocol import schemas

DEFAULT = object()
GAME_ENV = "HEROES_GAME"


@dataclass
class Ask:
    player: int
    kind: str
    text: str
    data: dict
    schema: dict
    default: Any
    answer: Any = None


@dataclass
class Result:
    game: Game
    scores: list[dict[str, int]]
    log: list[str]
    asked: list[Ask]
    errors: list[str] = field(default_factory=list)


class _Policy(Player):
    def __init__(self, policy, result: Result, seed: int | None, then: Any = "default"):
        self.result = result
        self.queue: list | None = None
        self.call: Callable | None = None
        self.bot: RandomBot | None = None
        self.then = then
        self.rejected = False
        if isinstance(policy, (list, tuple)):
            self.queue = list(policy)
        elif callable(policy):
            self.call = policy
        elif policy == "random":
            self.bot = RandomBot(seed)
        elif policy != "default":
            raise ValueError(f"Unknown policy: {policy!r}")
        if self.queue is not None and then != "default":
            self.fallback = _Policy(then, result, seed)
        else:
            self.fallback = None

    def on_invalid(self, decision, answer, error):
        self.rejected = True
        self.result.errors.append(f"player {decision.player}, {decision.kind}: {error} (answer {answer!r})")

    def decide(self, decision, view):
        game = view.game
        spec = schemas.spec_for(game, decision)
        ask = Ask(decision.player, decision.kind, decision.text, decision.data, spec.schema, decision.default)
        if self.rejected:   # отклонённый ответ не повторяем — берём ответ по умолчанию
            self.rejected = False
            ask.answer = decision.default
        elif self.bot:
            ask.answer = self.bot.decide(decision, view)
        elif self.call or self.queue:
            raw = self.call(ask, game) if self.call else self.queue.pop(0)
            ask.answer = decision.default if raw is DEFAULT else spec.convert(raw)
        elif self.fallback:
            ask.answer = self.fallback.decide(decision, view)
        else:
            ask.answer = decision.default
        self.result.asked.append(ask)
        return ask.answer


class _Stop(Exception):
    pass


class _StopAtQuestion(Player):
    def decide(self, decision, view):
        raise _Stop


class Sim:
    """Сохранённый момент партии, с которого можно много раз играть дальше."""

    def __init__(self, data: dict):
        self.data = data
        self.me: int = data["current"]["player"]
        self.round: int = data["current"]["round"]
        self.kind: str = data["current"]["kind"]
        self.game = self._new_game([_StopAtQuestion() for _ in data["names"]])
        try:
            self._run(self.game, ROUNDS)
        except _Stop:   # записанное проиграно, движок задаёт текущий вопрос
            pass

    def _new_game(self, players: list[Player]) -> Game:
        return Game(players, names=self.data["names"], factions=self.data["factions"],
                    first_player=self.data["first_player"], replay=self.data["history"], meta=self.data["meta"])

    @staticmethod
    def _run(game: Game, target: int) -> None:
        if game.state.round == 0:
            game.setup()
        while game.state.round < target:
            game.play_round()

    def play(self, policies: dict[int, Any] | None = None, rounds: int = 1, seed: int | None = None,
             then: Any = "default") -> Result:
        policies = policies or {}
        result = Result(None, [], [], [])  # type: ignore[arg-type]
        game = self._new_game([_Policy(policies.get(p, "default"), result, None if seed is None else seed + p, then)
                               for p in range(len(self.data["names"]))])
        result.game = game
        self._run(game, min(ROUNDS, max(self.round, 1) + rounds - 1))
        result.log = game.state.log[len(self.game.state.log):]
        result.scores = scoring.final_scores(game)
        return result


def load(path: str | Path | None = None) -> Sim:
    """Текущий момент партии (файл из переменной окружения HEROES_GAME или path)."""
    path = path or os.environ.get(GAME_ENV)
    if not path:
        raise RuntimeError(f"No game to simulate: {GAME_ENV} is not set (run your script with heroes-run)")
    return Sim(json.loads(Path(path).read_text(encoding="utf-8")))


def export(game: Game, player: int, kind: str, path: Path) -> Path:
    """Сохранить момент партии для симуляции: всё сыгранное до вопроса kind игроку player."""
    savegame.save(game, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["meta"] = {k: v for k, v in data["meta"].items() if k != "claude_sessions"}
    data["current"] = {"player": player, "round": game.state.round, "kind": kind}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path
