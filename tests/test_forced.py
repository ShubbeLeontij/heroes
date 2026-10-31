"""Решения без выбора: движок их не задаёт, а старые сохранения (где они записаны) загружаются."""
import json

import pytest

from heroes import savegame
from heroes.engine.combat import army_strength, available_units, only_reward
from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot


class Watcher(RandomBot):
    """Бот, который проверяет, что каждый заданный ему вопрос — с настоящим выбором."""

    def decide(self, decision, view):
        game, p, d = view.game, decision.player, decision.data
        pool = {u: info["count"] for u, info in d["units"].items()} if "units" in d else available_units(game, p)
        units = army_strength(game, p, pool)   # у отдельной гидры — своя заявка
        if decision.kind in ("choose_start", ):
            assert len(d["options"]) > 1
        elif decision.kind == "destination":
            assert len(d["options"]) > 1
        elif decision.kind == "build_recruit":
            hire_ok = any(all(d["resources"].get(k, 0) >= v for k, v in h["cost"].items()) for h in d["hire"].values())
            upgrade_ok = d["upgrade"] and any(d["upgrade"]["units"].get(x["from"]) for x in d["upgrade"]["paths"].values())
            assert d["options"] or hire_ok or upgrade_ok or d["raise"] or d["science"]
        elif decision.kind == "commit_army":
            assert units > 0
        elif decision.kind == "capture_fortress":
            assert units > d["strength"]
        elif decision.kind == "fight_monster":
            assert any(c["strength"] < units for c in d["candidates"].values())
        elif decision.kind == "choose_reward":
            assert only_reward(d["options"]) is None
        return super().decide(decision, view)


@pytest.mark.parametrize("n", [2, 3, 4])
def test_players_are_asked_only_real_choices(n):
    for seed in range(15):
        Game([Watcher(seed * 10 + i) for i in range(n)]).run()


class LegacyGame(Game):
    """Движок до этого изменения: спрашивал всегда, даже без выбора."""

    def ask(self, *args, **kwargs):
        kwargs["forced"] = False
        return super().ask(*args, **kwargs)

    def ask_all(self, decisions):
        for d in decisions:
            d.forced = False
        return super().ask_all(decisions)


def test_old_saves_with_forced_answers_replay_exactly(tmp_path):
    make = lambda meta, names: [RandomBot(meta["seed"] + i) for i in range(len(meta["players"]))]
    meta = {"players": ["bot", "bot", "bot"], "seed": 11}
    old = LegacyGame(make(meta, None), meta=meta)
    old.run()
    path = savegame.save(old, tmp_path / "old.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    restored = savegame.load(path, make)
    restored.run()
    assert not restored.replay_queue
    assert restored.state.to_dict() == old.state.to_dict()
    assert len(restored.history) < len(old.history)   # решения без выбора больше не записываются
