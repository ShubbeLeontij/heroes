"""Кто сейчас решает и фаза раунда: окно пишет «Claude выбирает порядок действий» и подсвечивает фазу."""
from heroes.engine.game import Game
from heroes.gui.common import thinking_text
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import snapshot, static_data


class Spy(RandomBot):
    """Запоминает, что видно в снимке, пока он сам решает."""
    seen: list = []

    def decide(self, decision, view):
        snap = snapshot(view.game)
        Spy.seen.append((decision.kind, snap["phase"], snap["thinking"], snap["round"]))
        return super().decide(decision, view)


def test_thinking_and_phase():
    Spy.seen = []
    game = Game([Spy(0), Spy(1)], names=["Аня", "Claude"])
    calls = []
    game.watchers.append(lambda: calls.append(dict(game.thinking)))
    game.run()
    assert calls and not game.thinking   # после партии никто не решает
    for kind, phase, thinking, rnd in Spy.seen:
        assert [kind] == [k for _, k in thinking]
        if kind == "action_order":
            assert phase == 0
        elif rnd > 0 and kind != "choose_start":
            assert 1 <= phase <= 4
    static = static_data(game)
    state = {"thinking": [[1, "action_order"]], "players": [{"region": "Цурт"}, {"region": "Цурт"}]}
    assert thinking_text(static, state) == "Claude (Люди) выбирает порядок действий раунда…"
