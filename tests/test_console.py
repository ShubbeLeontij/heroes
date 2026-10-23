"""Минимальные проверки этапа 4: консольный ввод и сохранения."""
import random

from heroes import savegame
from heroes.engine.game import Game, check_action_order
from heroes.players.console import ConsolePlayer
from heroes.players.random_bot import RandomBot


def scripted(lines):
    it = iter(lines)
    return ConsolePlayer(input_fn=lambda prompt: next(it), output_fn=lambda text: None, color=False)


def test_action_order_input_retries_until_valid():
    human = scripted(["w g", "w w g b", "/b", "w g b m"])
    game = Game([human, RandomBot(0)])
    game.state.players[0].region = game.state.players[1].region = "Карракс"   # нужно для команды /b
    answer = game.ask(0, "action_order", "", {}, check_action_order, [])
    assert answer == ["wood", "gold", "build", "move"]


def test_console_survives_random_input():
    pool = ["1", "2", "0", "", "y", "n", "g", "all", "1 2 3 4", "w g b m", "0 1 1", "1 0 0", "xyz", "/m", "/log 2"]
    rng = random.Random(0)
    human = ConsolePlayer(input_fn=lambda prompt: rng.choice(pool), output_fn=lambda text: None, color=False)
    game = Game([human, RandomBot(1), RandomBot(2)])
    game.run()
    assert game.state.round == 16
    assert not any("the default was used" in line for line in game.state.log)


def test_save_and_load_replays_exactly(tmp_path):
    meta = {"players": ["bot", "bot"], "seed": 3}
    make = lambda m, names: [RandomBot(m["seed"] + i) for i in range(len(m["players"]))]
    game = Game(make(meta, None), meta=meta)
    game.run()
    path = savegame.save(game, tmp_path / "save.json")
    restored = savegame.load(path, make)
    restored.run()
    assert restored.state.to_dict() == game.state.to_dict()


def test_setup_wizard():
    from heroes.cli import setup_wizard
    # места: кто, имя, фракция (4 — орки; повтор орков отклоняется; Enter — случайная)
    answers = iter(["3", "1", "Аня", "4", "1", "", "4", "5", "2", "", "", "3", "1", "y"])
    out = []
    kinds, names, factions, first, model = setup_wizard(None, input_fn=lambda p: next(answers), output_fn=out.append)
    assert kinds == ["human", "human", "claude"]
    assert names == ["Аня", "Player 2", "Claude 1"]
    assert factions == ["orcs", "elves", "random"]
    assert any("faction is taken" in line for line in out)
    assert first == 0 and model == "claude-haiku-5-5"


def test_resolve_factions():
    import pytest
    from heroes.cli import FACTIONS, resolve_factions
    for seed in range(20):
        got = resolve_factions(["orcs", "random", "random", "nagas"], random.Random(seed))
        assert got[0] == "orcs" and got[3] == "nagas" and len(set(got)) == 4 and set(got) <= set(FACTIONS)
    with pytest.raises(ValueError):
        resolve_factions(["orcs", "orcs"])
    with pytest.raises(ValueError):
        resolve_factions(["goblins", "orcs"])


def test_hotseat_hands_over_and_hides_secrets():
    from heroes.players.console import Terminal
    out, rng = [], random.Random(1)
    pool = ["1", "2", "0", "", "y", "n", "w", "g", "all", "w g b m", "1 0 0"]
    terminal = Terminal()
    make = lambda: ConsolePlayer(input_fn=lambda p: rng.choice(pool), output_fn=out.append, color=False, terminal=terminal)
    game = Game([make(), RandomBot(0), make()], names=["Аня", "Бот", "Боря"])
    game.run()
    assert game.state.round == 16
    assert any(line.startswith("Аня's turn") for line in out)
    assert any("choice made and hidden" in line for line in out)


def test_console_plays_every_faction():
    """Случайный ввод за каждую фракцию: на особые решения консоль задаёт свои вопросы, а не просит JSON."""
    factions = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]
    pool = ["1", "2", "0", "", "y", "n", "g", "all", "1 2 3 4", "w g b m", "0 1 1", "1 0 0", "3 0 0", "xyz", "/b"]
    for seed, faction in enumerate(factions):
        rng = random.Random(seed)
        prompts = []
        human = ConsolePlayer(input_fn=lambda p: prompts.append(p) or rng.choice(pool), output_fn=lambda t: None,
                              color=False)
        game = Game([human, RandomBot(seed), RandomBot(seed + 9)], factions=[faction, *rng.sample(
            [f for f in factions if f != faction], 2)])
        game.run()
        assert game.state.round == 16
        assert not any(line.startswith("⚠ Player 1") for line in game.state.log)
        assert not any("JSON" in p for p in prompts)
