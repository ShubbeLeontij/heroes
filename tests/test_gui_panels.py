"""Панели решений окна: ответы, собранные из случайно заполненных форм, движок принимает.

Нужен дисплей (Tk); без него тесты пропускаются.
"""
import gc
import random
import tkinter as tk
from tkinter import ttk

import pytest

from heroes.engine.game import Game
from heroes.gui import panels
from heroes.players.base import Player
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import decision_message, static_data


@pytest.fixture(scope="module")
def root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()


class StubMap:
    def select_mode(self, *args, **kwargs):
        pass

    def set_selected(self, region):
        pass

    def set_preview(self, preview):
        self.preview = preview


class StubBoard:
    def set_picked(self, bid):
        pass

    def set_monster(self, index):
        pass


def widgets(w):
    for child in w.winfo_children():
        yield child
        yield from widgets(child)


class PanelPlayer(Player):
    """Человек, который тыкает в форму наугад и жмёт главную кнопку (или «отказаться»)."""

    def __init__(self, root, seed):
        self.root, self.rng = root, random.Random(seed)
        self.map = StubMap()
        self.board = StubBoard()
        self.board_picks = []
        self.static = None
        self.invalid = []
        self.kinds = set()

    def pick_building(self, player, options, on_pick):
        self.board_picks.append(on_pick)
        on_pick(self.rng.choice(sorted(options) + [None]))   # щелчок по карточке на планшете

    def pick_monster(self, player, options, on_pick):
        if self.rng.random() < 0.5:
            on_pick(self.rng.choice(sorted(options)))   # щелчок по чудовищу в линии на планшете

    def send_answer(self, message, answer):
        self.answers.append(answer)
        if message["kind"] == "build_recruit":   # шаги строительства — как отдельные «виды» для проверки охвата
            self.kinds |= {f"step:{next(iter(step))}" for step in answer if isinstance(step, dict)}
        if isinstance(answer, dict) and answer.get("upgrade"):   # Лавка бронника — вместе с заявкой войск
            self.kinds |= {f"{message['kind']}:upgrade", "upgrade"}

    def decide(self, decision, view):
        self.static = self.static or static_data(view.game)
        self.answers = []
        panel = panels.make_panel(self.root, self, decision_message(view.game, decision, 1))
        self.kinds.add(decision.kind)
        self.fiddle(panel)
        primary = [w for w in widgets(panel.footer) if str(w.cget("style")) == "Primary.TButton"]
        others = [w for w in widgets(panel.footer) if isinstance(w, ttk.Button) and w not in primary]
        steps = [w for w in widgets(panel.body) if isinstance(w, ttk.Button) and "disabled" not in w.state()]
        if isinstance(panel, panels.BuildRecruit) and steps and self.rng.random() < 0.7:
            self.rng.choice(steps).invoke()      # шаг строительства; «Закончить» — главная кнопка внизу
        elif primary and "disabled" not in primary[0].state() and self.rng.random() < 0.85:
            primary[0].invoke()
        elif others:
            self.rng.choice(others).invoke()
        elif primary and "disabled" not in primary[0].state():
            primary[0].invoke()
        panel.destroy()
        gc.collect()  # переменные Tk освобождать в главном потоке: иначе их подберёт поток сетевого теста
        if not self.answers:  # форма не дала ответить (например, не хватает войск) — как отказ
            return decision.default
        return self.answers[0]

    def fiddle(self, panel):
        rng = self.rng
        if isinstance(panel, panels.RegionChoice):
            panel.pick(rng.choice(panel.options))
        if isinstance(panel, panels.ActionOrder):
            for a in rng.sample(list(panel.buttons), 4):
                panel.toggle(a)
        radios = {}
        for w in widgets(panel):
            if isinstance(w, ttk.Radiobutton):
                radios.setdefault(str(w.cget("variable")), []).append(w)
        for group in radios.values():
            rng.choice(group).invoke()
        for w in widgets(panel):  # после радиокнопок: они перестраивают поля найма
            if isinstance(w, panels.Counter):
                w.set(rng.randint(0, max(0, w.maximum)) if rng.random() < 0.7 else 0)

    def on_invalid(self, decision, answer, error):
        self.invalid.append((decision.kind, answer, error))


@pytest.mark.parametrize("seed", range(4))
def test_random_forms_give_valid_answers(root, seed):
    human = PanelPlayer(root, seed)
    others = [RandomBot(seed * 10 + i) for i in range(1 + seed % 3)]
    game = Game([human, *others])
    game.run()
    assert game.state.round == 16
    assert human.invalid == []
    # стартовый регион место 0 выбирает последним — ему достаётся единственный оставшийся, вопроса нет
    assert {"action_order", "build_recruit", "destination"} <= human.kinds


FACTIONS = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]


@pytest.mark.parametrize("faction", FACTIONS[1:])
def test_faction_forms_give_valid_answers(root, faction):
    """Формы особых решений фракций: человек за этой фракцией, соперники — боты за остальными."""
    special = {"undead": {"curse_region", "buy_move_points", "exchange_on_gain"}, "dwarves": {"step:science"},
               "orcs": {"step:upgrade", "step:raise", "raise_action"}, "elves": {"upgrade"},
               "nagas": {"circle_note", "summon_hydra", "hydra_destination", "make_lake"}}[faction]
    others = [f for f in FACTIONS if f != faction]
    kinds = set()
    for seed in range(20):   # до тех пор, пока не встретятся все особые решения фракции
        human = PanelPlayer(root, seed)
        game = Game([human, RandomBot(seed), RandomBot(seed + 1)], factions=[faction, *others[seed % 4:seed % 4 + 2]])
        game.run()
        assert human.invalid == [], human.invalid
        kinds |= human.kinds
        if special <= kinds:
            break
    assert special <= kinds, special - kinds


def test_cheapest_army():
    units = {"soldier": {"count": 3, "strength": 2}, "knight": {"count": 2, "strength": 3}}
    assert panels.cheapest_army(units, 4) == ({"soldier": 1, "knight": 1}, 0)
    assert panels.cheapest_army(units, 11) == ({"soldier": 3, "knight": 2}, 0)
    assert panels.cheapest_army(units, 12) is None


def test_cheapest_army_upgrades_only_when_needed():
    """Лавка бронника: модификации — только если без них не хватает, и как можно меньше."""
    units = {"pixie": {"count": 3, "strength": 1}, "ent": {"count": 1, "strength": 6}}
    upgrade = {"cost_per_unit": {"wood": 1}, "strength": {"pixie": 2, "ent": 10}, "max": 2}
    assert panels.cheapest_army(units, 6, upgrade=upgrade) == ({"pixie": 1, "ent": 1}, 0)
    assert panels.cheapest_army(units, 9, upgrade=upgrade) == ({"ent": 1}, 1)      # энт 6 → 10
    assert panels.cheapest_army(units, 13, upgrade=upgrade) == ({"pixie": 3, "ent": 1}, 2)   # 3 + 6 + 4 + 1 = 14
    assert panels.cheapest_army(units, 14, upgrade=upgrade) is None   # модификаций всего две
    assert panels.best_strength(units, upgrade=upgrade) == 14


def test_building_columns_follow_the_printed_board():
    from heroes.engine.data import load_faction
    from heroes.gui.boardview import building_columns
    faction = load_faction("humans")
    buildings = {b.id: {"level": b.level, "requires": list(b.requires)} for b in faction.buildings.values()}
    columns = building_columns(buildings)
    # как на планшете людей: каждое здание стоит под тем, которому оно нужно
    assert [columns[b] for b in ("arsenal", "cathedral", "mages_guild")] == [0, 1, 2]
    assert [columns[b] for b in ("rangers_house", "tavern", "barracks")] == [0, 1, 2]
    assert [columns[b] for b in ("market", "illusion_tower")] == [0, 1]
    assert [columns[b] for b in ("town_hall", "warriors_guild")] == [1, 2]


@pytest.mark.parametrize("faction", FACTIONS)
def test_window_strength_matches_engine(faction):
    """common.army_strength по данным решения (strength_rules) считает так же, как движок: краки, механики,
    прибавки к силе; так окно верно пишет «хватает / не хватает»."""
    from heroes.engine.combat import army_data
    from heroes.engine.effects import Battle, army_strength as engine_strength
    from heroes.gui.common import army_strength
    from heroes.protocol.snapshot import strength_rules
    rng = random.Random(faction)
    game = Game([RandomBot(0), RandomBot(1)], factions=[faction, "humans"])
    player = game.state.players[0]
    player.region = "Цурт"
    for _ in range(40):
        player.built = rng.sample(list(game.faction_of(0).buildings), rng.randint(0, 10))
        player.tokens["science"] = rng.randint(0, 18) if faction == "dwarves" else 0
        player.units = {u: rng.randint(0, 4) for u in game.faction_of(0).units}
        player.hydra = None
        battle = Battle(rng.choice(["pvp", "monster", "fortress"]), "Цурт")
        units, rules = army_data(game, 0, battle), strength_rules(game, 0, battle)
        army = {u: rng.randint(0, v["count"]) for u, v in units.items()}
        assert army_strength(units, army, rules) == engine_strength(game, 0, army, battle), (player.built, army)
