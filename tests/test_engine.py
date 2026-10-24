"""Минимальные проверки этапов 0–3. Подробные проверки правил — позже."""
import threading
import time

import pytest

from heroes.engine import actions, combat, movement, scoring
from heroes.engine.data import load_faction, load_map
from heroes.engine.decisions import Decision
from heroes.engine.game import Game
from heroes.engine.movement import reachable
from heroes.players.random_bot import RandomBot


def make_game(n=2):
    return Game([RandomBot(i) for i in range(n)])


def test_data_loads():
    game_map = load_map()
    assert len(game_map.regions) == 27
    assert sum(len(v) for v in game_map.adjacent.values()) == 2 * 64
    assert sorted(game_map.portals) == ["Вернесс", "Карракс", "Оррид", "Чарил"]
    humans = load_faction("humans", game_map)
    assert len(humans.buildings) == 10


def test_movement_costs():
    game = make_game(4)
    game.state.players[0].region = "Асандер"
    costs = reachable(game, 0, 4)
    assert costs["Бринлор"] == 3
    assert costs["Есмор"] == 3        # через озёрный Вернесс: 2 + 1
    game.state.players[0].region = "Карракс"
    assert reachable(game, 0, 1)["Чарил"] == 1   # портал → поле


def test_crossed_out_regions_are_impassable():
    game = make_game(2)                 # Вернесс вычеркнут
    game.state.players[0].region = "Асандер"
    costs = reachable(game, 0, 4)
    assert "Вернесс" not in costs
    assert costs["Есмор"] == 3          # без Вернесса: через лесной Дорталь, 2 + 1
    assert "Гленнфор" not in costs and "Юноро" not in costs


def test_reserved_portal_blocks_others():
    game = make_game(2)
    game.state.players[0].region = "Карракс"
    game.state.reserved["Чарил"] = 1
    assert "Чарил" not in reachable(game, 0, 1)
    game.state.players[1].region = "Карракс"
    assert reachable(game, 1, 1)["Чарил"] == 1


def test_all_moves_revealed_before_visits(monkeypatch):
    """Ресурсы за новый регион (с выбором у людей и гномов) — только когда все уже знают, кто куда пошёл."""
    seen = []
    visit = movement.visit
    def spy(game, p, region):
        seen.append([pl.region for pl in game.state.players])
        visit(game, p, region)
    game = make_game(4)
    game.setup()
    monkeypatch.setattr(movement, "visit", spy)
    for p, region in enumerate(["Асандер", "Карракс", "Оррид", "Чарил"]):
        game.state.players[p].region = region
    movement.move_action(game, 4)
    final = [pl.region for pl in game.state.players]
    assert seen and all(regions == final for regions in seen)


def test_build_requirements_any_of():
    game = make_game()
    player = game.state.players[0]
    player.resources = {"wood": 20, "iron": 20, "gold": 20}
    assert "barracks" not in actions.buildable(game, 0, 3)
    player.built.append("warriors_guild")
    assert "barracks" in actions.buildable(game, 0, 3)
    assert "barracks" not in actions.buildable(game, 0, 2)    # уровень действия ниже уровня здания


def test_build_and_hire_after_cathedral():
    game = make_game()
    player = game.state.players[0]
    player.region = "Асандер"
    player.resources = {"wood": 0, "iron": 5, "gold": 4}
    player.built += ["town_hall", "illusion_tower", "warriors_guild", "barracks", "tavern"]
    turn = actions.BuildTurn(4)
    steps = [{"build": "cathedral"}, {"hire": {"knight": 2}}, "done"]
    assert actions.check_steps(game, 0, turn, steps) is None
    assert actions.run_steps(game, 0, turn, steps) is False       # «done» — действие закончено
    assert player.units["knight"] == 2 and player.resources["gold"] == 0   # латник уже по 2 золота


def test_foundation_is_built_automatically():
    game = make_game()
    player = game.state.players[0]
    player.region = "Есмор"
    player.resources = {"wood": 20, "iron": 20, "gold": 20}
    actions.build_building(game, 0, "market")          # ур. 2 → вписано 2
    actions.build_building(game, 0, "town_hall")       # ур. 1 — не понижает
    assert player.foundations == {"Есмор": 2}
    game.state.reserved["Есмор"] = 1                                       # бронь соперника
    actions.build_building(game, 0, "rangers_house")
    assert player.foundations == {"Есмор": 2}


def test_scoring_example():
    game = make_game()
    player = game.state.players[0]
    player.built += ["tavern", "barracks"]              # 2 + 3 ПО
    player.monsters[:3] = ["monster", "pvp", "monster"]  # 1 + 1 + 1 ПО, Таверна +3
    player.foundations = {"Асандер": 2, "Лорвельд": 3}
    player.resource_marks["iron"] = [14, 22]
    player.fortresses = ["Бринлор"]
    game.state.fortress_owner["Бринлор"] = 0
    game.state.artifact_holder["Содмур"] = 0            # +1 ПО за крепость
    s = scoring.score_player(game, 0)
    assert s == {"buildings": 8, "foundations": 5, "resources": 4, "battles": 7, "artifacts": 1, "total": 25}


def test_artifact_loss_removes_permanent_effect():
    game = make_game()
    combat.acquire_artifact(game, 0, "Золен")
    from heroes.engine.effects import move_cost
    assert move_cost(game, 0, "mountain") == 2
    game.state.artifact_holder["Золен"] = 1
    assert move_cost(game, 0, "mountain") == 3


@pytest.mark.parametrize("n", [2, 3, 4])
def test_random_games_complete(n):
    for seed in range(20):
        bots = [RandomBot(seed * 10 + i) for i in range(n)]
        game = Game(bots, first_player=seed % n)
        scores = game.run()
        assert game.state.round == 16 and len(scores) == n
        assert all(not b.invalid for b in bots)
        assert all(v >= 0 for p in game.state.players for v in [*p.resources.values(), *p.units.values()])


def test_ctrl_c_does_not_wait_for_players_thinking_in_threads():
    """Ctrl+C во время тайных решений: партия не ждёт игрока, который думает в потоке (браузер молчит,
    Claude перезапускается), и просит фоновые потоки не начинать новых дел."""
    release = threading.Event()

    class Silent(RandomBot):
        parallel_safe = True

        def decide(self, decision, view):
            release.wait(30)
            return decision.default

    class Host(RandomBot):
        def decide(self, decision, view):
            raise KeyboardInterrupt

    game = Game([Silent(0), Host(1)])
    decisions = [Decision("x", p, "", {}, lambda answer: None, None) for p in (0, 1)]
    started = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            game.ask_all(decisions)
        assert time.monotonic() - started < 5
        assert game.stopping.is_set()
    finally:
        release.set()
