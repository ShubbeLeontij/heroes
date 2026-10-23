"""Партия: подготовка, 16 раундов, подсчёт очков. Опрос игроков через Decision."""
from __future__ import annotations

import threading
import uuid
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence

from . import actions, movement, scoring
from .data import RESOURCES, Faction, GameMap, Monster, load_faction, load_map
from .decisions import Decision, check_choice
from .state import ACTION_NAMES, ACTIONS, ROUNDS, GameState, PlayerState
from .. import savegame
from ..players.base import Player


class GameView:
    """То, что игрок видит при принятии решения. Строится лениво: ботам он обычно не нужен.

    Тайные решения соперников в состоянии не хранятся до раскрытия, поэтому всё состояние
    открыто (правило открытости).
    """

    def __init__(self, game: Game, player: int):
        self.game = game
        self.player = player

    def to_dict(self) -> dict:
        return {"you": self.player, **self.game.state.to_dict()}


class Game:
    def __init__(
        self,
        players: Sequence[Player],
        names: Sequence[str] | None = None,
        factions: Sequence[str] | None = None,
        first_player: int = 0,
        game_map: GameMap | None = None,
        max_attempts: int = 3,
        replay: Sequence[dict] = (),
        meta: dict | None = None,
        autosave: str | None = None,
        legacy_replay: bool = False,
    ):
        """replay — записанные решения (из сохранения): они проигрываются без опроса игроков.
        meta — сведения о запуске (типы игроков и т. п.), сохраняются вместе с партией.
        autosave — папка, куда партия сохраняется в начале каждого раунда (savegame.autosave).
        legacy_replay — сохранение старого формата, где записаны и решения без выбора (forced)."""
        if not 2 <= len(players) <= 4:
            raise ValueError("«Герои» is played by 2 to 4 players")
        self.map = game_map or load_map()
        self.players = list(players)
        self.max_attempts = max_attempts
        self.meta = dict(meta or {})
        self.meta.setdefault("game_id", uuid.uuid4().hex)  # одна и та же партия в статистике профилей — одна запись
        self.autosave = autosave
        self.last_autosave: Path | None = None
        self.initial_first_player = first_player
        self.replay_queue: deque[dict] = deque(replay)
        self.legacy_replay = legacy_replay
        names = names or [f"Player {i + 1}" for i in range(len(players))]
        faction_ids = factions or ["humans"] * len(players)
        self.factions: dict[str, Faction] = {f: load_faction(f, self.map) for f in set(faction_ids)}
        states = [new_player(name, self.factions[fid], self.map) for name, fid in zip(names, faction_ids)]
        self.state = GameState(
            players=states,
            available=sorted(self.map.available_regions(len(players))),
            first_player=first_player,
        )
        self.history: list[dict] = []  # все принятые решения — для журнала и воспроизведения
        self.listeners: list[Callable[[str], None]] = []  # подписчики на события лога (сетевые игроки, зритель)
        # кто сейчас принимает решение и какое (игрок -> вид решения); тайные решения — у нескольких сразу
        self.thinking: dict[int, str] = {}
        self.thinking_lock = threading.Lock()
        self.watchers: list[Callable[[], None]] = []  # подписчики на смену thinking (окно, сетевые окна)
        # свои аватарки игроков (место -> PNG в base64) — только для окон, в сохранение не попадают
        self.avatars: dict[int, str] = {}

    # --- служебное ------------------------------------------------------------------------

    def faction_of(self, p: int) -> Faction:
        return self.factions[self.state.players[p].faction]

    def monster_line(self, p: int) -> tuple[Monster, ...]:
        """Линия чудовищ игрока: общая или своя у фракции (наги)."""
        return self.faction_of(p).monsters or self.map.monsters

    def terrain(self, region: str) -> str:
        """Местность региона сейчас: Гейзер наг превращает регионы в озёрные для всех."""
        return self.state.terrain.get(region) or self.map.regions[region].terrain

    def log(self, message: str) -> None:
        self.state.log.append(message)
        for listener in self.listeners:
            listener(message)

    def ask(self, p: int, kind: str, text: str, data: dict, validate: Callable[[Any], str | None], default: Any,
            forced: bool = False) -> Any:
        """forced — выбора нет: игрока не спрашивать, ответ — default."""
        return self.resolve(Decision(kind, p, text, data, validate, default, forced))

    def ask_all(self, decisions: list[Decision]) -> list[Any]:
        """Тайные одновременные решения: каждый отвечает, не зная ответов остальных.

        Игроки с parallel_safe (Claude) думают одновременно в потоках, остальные (человек
        в консоли) — по очереди в основном потоке. В журнал ответы пишутся в порядке
        decisions, чтобы воспроизведение сохранения было однозначным.
        """
        if self.replay_queue:
            return [self._forced(d) if d.forced else self._replay(d) for d in decisions]
        asked = [d for d in decisions if not d.forced]
        parallel = [d for d in asked if getattr(self.players[d.player], "parallel_safe", False)]
        answers: dict[int, Any] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(parallel))) as pool:
            futures = {id(d): pool.submit(self._answer, d) for d in parallel}
            for d in asked:
                if id(d) not in futures:
                    answers[id(d)] = self._answer(d)
            for d in parallel:
                answers[id(d)] = futures[id(d)].result()
        for d in asked:
            self._record(d, answers[id(d)])
        return [self._forced(d) if d.forced else answers[id(d)] for d in decisions]

    @property
    def replaying(self) -> bool:
        return bool(self.replay_queue)

    def resolve(self, decision: Decision) -> Any:
        if decision.forced:
            return self._forced(decision)
        if self.replay_queue:
            return self._replay(decision)
        answer = self._answer(decision)
        self._record(decision, answer)
        return answer

    def _answer(self, decision: Decision) -> Any:
        """Опросить игрока; при недопустимом ответе — переспросить, затем взять вариант по умолчанию."""
        player = self.players[decision.player]
        self._think(decision.player, decision.kind)
        try:
            for _ in range(self.max_attempts):
                answer = player.decide(decision, GameView(self, decision.player))
                error = decision.validate(answer)
                if error is None:
                    break
                player.on_invalid(decision, answer, error)
            else:
                answer = decision.default
                self.log(f"⚠ {self.state.players[decision.player].name}: no valid answer to {decision.kind!r}, "
                         "the default was used")
        finally:
            self._think(decision.player, None)
        return answer

    def _think(self, p: int, kind: str | None) -> None:
        """Отметить, что игрок p начал (kind) или закончил (None) принимать решение, и сообщить подписчикам."""
        with self.thinking_lock:
            if kind:
                self.thinking[p] = kind
            else:
                self.thinking.pop(p, None)
        for watcher in self.watchers:
            watcher()

    def _record(self, decision: Decision, answer: Any) -> None:
        self.history.append({"round": self.state.round, "player": decision.player, "kind": decision.kind, "answer": answer})

    def _forced(self, decision: Decision) -> Any:
        """Решение без выбора. В старых сохранениях оно записано — тогда берём записанный ответ:
        партия должна восстановиться в точности такой, какой была сыграна."""
        if self.legacy_replay and self.replay_queue:
            head = self.replay_queue[0]
            if head["player"] == decision.player and head["kind"] == decision.kind:
                self.replay_queue.popleft()
                if decision.validate(head["answer"]) is None:
                    return head["answer"]
        return decision.default

    def _replay(self, decision: Decision) -> Any:
        entry = self.replay_queue.popleft()
        if entry["player"] != decision.player or entry["kind"] != decision.kind:
            raise ReplayError(f"The save does not match the game: expected {entry['kind']} of player {entry['player']}, "
                              f"but the engine asks {decision.kind} of player {decision.player}")
        error = decision.validate(entry["answer"])
        if error:
            raise ReplayError(f"A recorded answer is no longer valid ({decision.kind}): {error}")
        self.history.append(entry)
        return entry["answer"]

    # --- ход партии ---------------------------------------------------------------------

    def setup(self) -> None:
        st = self.state
        n = len(st.players)
        portals = [r for r in self.map.portals if r in st.available]
        taken: list[str] = []
        # Первым выбирает игрок справа от первого игрока, дальше — против часовой стрелки.
        for i in range(n):
            p = (st.first_player - 1 - i) % n
            options = [r for r in portals if r not in taken]
            region = self.ask(
                p, "choose_start",
                "Choose your starting region (a region with a portal). Options: " + ", ".join(options),
                {"options": options},
                lambda a, o=options: check_choice(a, o),
                options[0],
                forced=len(options) == 1,  # последнему выбирающему остаётся один регион
            )
            taken.append(region)
            self.log(f"{st.players[p].name} starts in {region}")
        # Стартовый регион посещён во всех смыслах; ресурсы получают все после выбора.
        for p in st.turn_order():
            movement.visit(self, p, taken[(st.first_player - 1 - p) % n])

    def play_round(self) -> None:
        st = self.state
        if self.autosave and not self.replaying:
            savegame.autosave(self, self.autosave)
        st.round += 1
        st.phase = 0
        for player in st.players:
            player.move_points = 0
            player.raised = {}
        p = st.first_player
        self.log(f"=== Round {st.round}, first player: {st.players[p].name} ===")
        order = self.ask(
            p, "action_order",
            "You are the first player: choose 4 of the 5 actions and their order; nobody performs the fifth this round. "
            "An action's position in the order is also its level (1–4).\n"
            f"Actions: {', '.join(f'{a} ({ACTION_NAMES[a]})' for a in ACTIONS)}.",
            {"actions": list(ACTIONS)},
            check_action_order,
            ["wood", "iron", "gold", "build"],
        )
        st.action_order = list(order)
        self.log("Round actions: " + ", ".join(f"{i}. {ACTION_NAMES[a]}" for i, a in enumerate(order, 1)))
        for level, action in enumerate(order, 1):
            st.phase = level
            if action in RESOURCES:
                actions.gather(self, action, level)
            elif action == "move":
                movement.move_action(self, level)
            else:
                actions.build_and_recruit(self, level)
        st.first_player = (st.first_player + 1) % len(st.players)

    def run(self) -> list[dict[str, int]]:
        self.setup()
        while self.state.round < ROUNDS:
            self.play_round()
        scores = scoring.final_scores(self)
        for p, s in enumerate(scores):
            self.log(f"{self.state.players[p].name}: {s['total']} VP " + str({k: v for k, v in s.items() if k != 'total'}))
        win = scoring.winners(self, scores)
        self.log("Winner: " + ", ".join(self.state.players[p].name for p in win))
        return scores


class ReplayError(RuntimeError):
    pass


def new_player(name: str, faction: Faction, game_map: GameMap) -> PlayerState:
    """Планшет в начале партии: стартовые войска, пустая линия чудовищ, шкалы особых свойств фракции."""
    player = PlayerState(name=name, faction=faction.id, units={uid: u.start for uid, u in faction.units.items()},
                         monsters=[None] * len(faction.monsters or game_map.monsters))
    for e in (e for rule in faction.rules.values() for e in rule.effects):
        if e["type"] == "faction_resource":
            player.tokens[e["resource"]] = 0
            player.resource_marks[e["resource"]] = []
        elif e["type"] == "science_track":
            player.tokens["science"] = 0
        elif e["type"] == "notes":
            player.notes = [0] * e["pairs"]
    return player


def check_action_order(answer: Any) -> str | None:
    if not isinstance(answer, list) or len(answer) != 4:
        return "Expected a list of 4 actions"
    if len(set(answer)) != 4 or any(a not in ACTIONS for a in answer):
        return f"Actions must be distinct and from {list(ACTIONS)}"
    return None
