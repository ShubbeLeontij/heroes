"""Фракции «Героев»: нежить, гномы, орки, эльфы, наги. Примеры из правил (с. 14–19) и FAQ."""
import random

import pytest

from heroes.players.random_bot import RandomBot
from heroes.engine import actions, combat, movement, scoring
from heroes.engine.data import load_faction
from heroes.engine.economy import exchange_for_units, gain
from heroes.engine.effects import Battle, army_strength, building_cost, hire_cost, step_cost, unit_strength
from heroes.engine.game import Game
from heroes.engine.special import curse
from heroes.players.base import Player

FACTIONS = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]


class Scripted(Player):
    """Отвечает по виду решения: значение или функция от data; остальное — ответ по умолчанию."""

    def __init__(self, **answers):
        self.answers = answers
        self.asked = []

    def decide(self, decision, view):
        self.asked.append(decision.kind)
        answer = self.answers.get(decision.kind, decision.default)
        return answer(decision.data) if callable(answer) else answer


def make(factions, players=None):
    players = players or [Scripted() for _ in factions]
    game = Game(players, factions=factions)
    for p, region in enumerate(["Асандер", "Есмор", "Чарил", "Карракс"][: len(factions)]):
        game.state.players[p].region = region
    return game


@pytest.mark.parametrize("faction", FACTIONS)
def test_faction_data_loads(faction):
    f = load_faction(faction)
    assert len(f.buildings) == 10 and len(f.artifacts) == 6


@pytest.mark.parametrize("seed", range(12))
def test_random_games_with_all_factions(seed):
    rng = random.Random(seed)
    n = rng.choice([2, 3, 4])
    game = Game([RandomBot(seed * 10 + i) for i in range(n)], factions=rng.sample(FACTIONS, n))
    scores = game.run()
    assert all(s["total"] >= 0 for s in scores)


def test_old_bot_plays_new_factions_with_defaults():
    """Клиент, не знающий новых решений, отвечает по умолчанию — партия всё равно доигрывается."""
    class OldBot(RandomBot):
        def decide(self, decision, view):
            handler = getattr(RandomBot, f"_{decision.kind}")
            return decision.default if handler.__name__ in NEW_KINDS else super().decide(decision, view)

    NEW_KINDS = {"_yes_no", "_count", "_curse_region", "_circle_note", "_summon_hydra", "_hydra_destination",
                 "_raise_action", "_avoid_battle"}
    Game([OldBot(i) for i in range(4)], factions=["undead", "dwarves", "orcs", "nagas"]).run()


# --- Гномы -----------------------------------------------------------------------------------

def test_mechanics_double_strongest_units():
    """Пример Матвея (с. 14): 2 механика, стреколёт, 2 автоматона (6), мехозавр при 7 науке → 34."""
    game = make(["dwarves", "humans"])
    player = game.state.players[0]
    player.built += ["academy", "factory"]
    player.tokens["science"] = 7
    game.state.artifact_holder["Цурт"] = 0           # автоматоны → 6
    army = {"mechanic": 2, "flyer": 1, "automaton": 2, "mechasaur": 1}
    assert unit_strength(game, 0, "mechasaur") == 7
    assert army_strength(game, 0, army) == 2 + 6 + 12 + 14


def test_science_makes_buildings_free_except_level_one():
    game = make(["dwarves", "humans"])
    game.state.players[0].tokens["science"] = 4
    assert building_cost(game, 0, "gatherers") == {}
    assert building_cost(game, 0, "academy") == {"wood": 2, "iron": 2}
    assert building_cost(game, 0, "conveyor") == {"wood": 2, "iron": 5}


def test_generator_and_conveyor():
    game = make(["dwarves", "humans"])
    player = game.state.players[0]
    player.units["mechanic"] = 2
    player.tokens["science"] = 2
    player.resources = {"wood": 0, "iron": 3, "gold": 2}
    actions.build_building(game, 0, "generator")
    assert player.tokens["science"] == 4           # пример Саши: 2 + 2 механика
    player.built += ["conveyor", "manufactory"]
    player.tokens["science"] = 18
    assert hire_cost(game, 0, "flyer") == {"gold": 1}       # −2 на 18 науке
    assert hire_cost(game, 0, "mechanic") == {"gold": 2}    # механиков не касается
    gain(game, 0, {"science": 5}, "test")
    assert player.tokens["science"] == 18          # сверх шкалы наука пропадает


def test_dwarves_hire_mechanics_before_generator():
    """Генератор даёт науку за механиков в войске в момент постройки: выгодно сначала нанять."""
    game = make(["dwarves", "humans"])
    player = game.state.players[0]
    player.built.append("academy")
    player.resources = {"wood": 0, "iron": 3, "gold": 6}
    actions.run_steps(game, 0, actions.BuildTurn(1), [{"hire": {"mechanic": 2}}, {"build": "generator"}])
    assert player.units["mechanic"] == 2 and player.tokens["science"] == 2


def test_dwarf_workshop_science_makes_building_free_then_gatherers_pay_for_hiring():
    """Автоматоны Цеха доводят науку до 4 — Сборщики бесплатны, а их 9 ресурсов сразу идут на наём."""
    game = make(["dwarves", "humans"], [Scripted(distribute_resources={"gold": 9}), Scripted()])
    player = game.state.players[0]
    player.built.append("workshop")
    player.tokens["science"] = 2
    player.resources = {"wood": 0, "iron": 0, "gold": 8}
    turn = actions.BuildTurn(2)
    assert actions.check_step(game, 0, turn, {"build": "gatherers"})    # пока наука 2 — платно, железа нет
    actions.run_steps(game, 0, turn, [{"hire": {"automaton": 2}}, {"build": "gatherers"}, {"hire": {"automaton": 2}}])
    assert player.units["automaton"] == 4 and "gatherers" in player.built
    assert player.tokens["science"] == 2 + 2 + 1 + 2 and player.resources["gold"] == 1 and turn.rejected is None


def test_conveyor_discount_grows_while_hiring():
    """Наём по единице: каждый автоматон Цеха даёт науку, и на 9-й науке Конвейер удешевляет следующих."""
    game = make(["dwarves", "humans"])
    player = game.state.players[0]
    player.built += ["conveyor", "workshop", "manufactory"]
    player.tokens["science"] = 8
    assert actions.hire_price(game, 0, {"flyer": 1, "automaton": 2}) == {"gold": 4 + 3 + 2}   # автоматоны — первыми
    player.resources["gold"] = 9
    actions.hire_units(game, 0, {"flyer": 1, "automaton": 2})
    assert player.resources["gold"] == 0 and player.tokens["science"] == 10


def test_library_is_a_step_once_per_action():
    game = make(["dwarves", "humans"])
    player = game.state.players[0]
    player.built.append("library")
    player.resources = {"wood": 8, "iron": 0, "gold": 0}
    turn = actions.BuildTurn(1)
    actions.run_steps(game, 0, turn, [{"science": True}])
    assert player.tokens["science"] == 1 and build_data_science(game, turn) is None
    assert "already bought" in actions.check_step(game, 0, turn, {"science": True})


def build_data_science(game, turn):
    return actions.build_data(game, 0, turn)["science"]


# --- Наги ------------------------------------------------------------------------------------

def test_krak_exchange_and_strength():
    """Пример Луизы (с. 16): 6 дерева, 5 железа, 9 золота → 4 крака; сила каждого = числу краков в бою."""
    game = make(["nagas", "humans"])
    player = game.state.players[0]
    player.resources = {"wood": 6, "iron": 5, "gold": 9}
    player.units["krak"] = 0
    exchange_for_units(game, 0)
    assert player.units["krak"] == 4 and player.resources == {"wood": 1, "iron": 0, "gold": 1}
    assert army_strength(game, 0, {"krak": 5}) == 25


def test_buildings_cost_kraks_and_give_notes():
    nagas = Scripted(circle_note="close", summon_hydra="Флисс")
    game = make(["nagas", "humans"], [nagas, Scripted()])
    player = game.state.players[0]
    player.units["krak"] = 10
    actions.build_building(game, 0, "ocean_heart")
    assert player.units["krak"] == 7 and player.notes[0] == 1     # первая нота всегда начинает пару
    actions.build_building(game, 0, "dam")
    assert player.notes[0] == 2 and player.hydra == "Флисс" and player.units["hydra"] == 1
    assert unit_strength(game, 0, "hydra") == 25                  # Сердце океана, гидра не рядом с нагой


def test_hydra_alone_must_fight_and_gives_no_rewards():
    """Пример (с. 17): гидра одна во Флиссе, Вова 15 и Лена 17 — гидра (18) побеждает, ресурсов нет,
    гидра погибает, крепость во Флиссе в этом раунде не берёт никто."""
    game = make(["nagas", "humans", "humans"],
                [Scripted(), Scripted(commit_army={"knight": 5}), Scripted(commit_army={"archmage": 3, "soldier": 1})])
    nagas, vova, lena = game.state.players
    nagas.region, nagas.hydra, nagas.units["hydra"] = "Золен", "Флисс", 1
    vova.region = lena.region = "Флисс"
    vova.units, lena.units = {"knight": 5}, {"archmage": 3, "soldier": 1}
    combat.resolve_encounters(game, {0: 0, 1: 0, 2: 0})
    assert nagas.monsters[0] == "pvp" and nagas.hydra is None and not nagas.units["hydra"]
    assert "Флисс" not in game.state.fortress_owner
    assert nagas.resources == {"wood": 0, "iron": 0, "gold": 0}


def test_hydra_next_to_nagas_is_weakened_by_kraks():
    game = make(["nagas", "humans"])
    player = game.state.players[0]
    player.hydra = player.region
    player.units.update(hydra=1, krak=3)
    assert unit_strength(game, 0, "hydra") == 18 - 6
    player.built.append("reef")
    assert unit_strength(game, 0, "hydra") == 18


def test_mine_pays_off_the_battle():
    nagas = Scripted(avoid_battle={"wood": 3, "iron": 0, "gold": 0})
    human = Scripted(region_stance="fight")
    game = make(["nagas", "humans"], [nagas, human])
    for pl in game.state.players:
        pl.region = "Икара"
    game.state.players[0].built.append("mine")
    game.state.players[0].resources["wood"] = 4
    combat.resolve_encounters(game, {0: 0, 1: 0})
    assert "commit_army" not in human.asked
    assert game.state.players[1].resources["wood"] == 3 and game.state.players[0].resources["wood"] == 1


def test_geyser_turns_region_into_lake_and_dam_counts_it():
    nagas = Scripted(make_lake=True)
    game = make(["nagas", "humans"], [nagas, Scripted()])
    player = game.state.players[0]
    player.built += ["geyser", "dam"]
    region = player.region
    left = {0: 1, 1: 0}
    movement.make_lake(game, 0, left)
    assert game.terrain(region) == "lake" and left[0] == 0
    lakes = sum(1 for r in game.state.available if game.terrain(r) == "lake")
    assert scoring.score_player(game, 0)["buildings"] == 2 + lakes


def test_nagas_score_no_resource_vp():
    game = make(["nagas", "humans"])
    gain(game, 0, {"wood": 15}, "test")
    assert game.state.players[0].resource_marks["wood"] == []
    assert scoring.score_player(game, 0)["resources"] == 0


# --- Нежить -----------------------------------------------------------------------------------

def test_curse_hits_opponents_and_scores():
    game = make(["undead", "humans"])
    human = game.state.players[1]
    human.units = {"soldier": 2, "knight": 1}
    curse(game, 0, human.region, "test")
    assert human.units == {"soldier": 1, "knight": 1}           # самая слабая — солдат
    assert scoring.score_player(game, 0)["artifacts"] == 1


def test_visiting_a_cursed_region_costs_a_unit():
    game = make(["undead", "humans"], [Scripted(), Scripted(destination="Бринлор")])
    game.state.cursed["Бринлор"] = 0
    game.state.players[1].units = {"soldier": 2}
    game.state.players[1].region = "Асандер"
    game.state.players[0].region = "Чарил"
    movement.move_action(game, 4)
    assert game.state.players[1].units["soldier"] == 1


def test_barrow_resurrects_skeletons():
    game = make(["undead", "humans"])
    player = game.state.players[0]
    player.built.append("barrow")
    combat.lose_units(game, 0, {"skeleton": 4})
    assert player.units["skeleton"] == 2


def test_executioner_strength_is_round_mp_and_altar_allows_any_monster():
    undead = Scripted(destination=lambda d: {"destination": next(r for r, o in d["options"].items() if o["cost"] == 0),
                                             "raise": {}, "buy": 1},
                      fight_monster=lambda d: {"monster": 3,
                                               "army": {"executioner": 3}})
    game = make(["undead", "humans"], [undead, Scripted()])
    player = game.state.players[0]
    player.built += ["hearse", "altar", "castle_ruins"]
    player.units = {"skeleton": 0, "executioner": 3, "lich": 0}
    player.resources["wood"] = 2
    movement.move_action(game, 4)
    assert player.move_points == 5
    assert player.monsters[3] == "monster"        # осталась на месте: любое чудовище; 3 палача × 5 = 15 > 8


# --- Орки -------------------------------------------------------------------------------------

def test_orc_upgrade_example():
    """Пример Матвея (с. 18): 3 разведчика, 5 ярости: 1 → громила, 4 → взрыватель."""
    game = make(["orcs", "humans"])
    player = game.state.players[0]
    player.units = {"scout": 3, "brute": 0, "blaster": 0}
    player.tokens["rage"] = 5
    actions.run_steps(game, 0, actions.BuildTurn(1), [{"upgrade": {"scout>brute": 1, "scout>blaster": 1}}])
    assert player.units == {"scout": 1, "brute": 1, "blaster": 1} and player.tokens["rage"] == 0


def test_orc_hires_brutes_for_rage_then_builds_workshop_and_hires_blasters():
    """Строительство и наём в любом порядке (с. 6): громилы через Арену дают ярость на Мастерскую,
    а Мастерская сразу позволяет нанять взрывателей — в одном действии."""
    steps = [{"hire": {"brute": 4}}, {"build": "workshop"}, {"hire": {"blaster": 1}}, "done"]
    orc = Scripted(build_recruit=steps)
    game = make(["orcs", "humans"], [orc, Scripted()])
    player = game.state.players[0]
    player.built += ["war_idol", "arena", "camp", "gym"]
    player.resources = {"wood": 4, "iron": 4, "gold": 17}
    turn = actions.BuildTurn(4)
    assert actions.check_step(game, 0, turn, {"build": "workshop"})      # сразу строить не на что: ярости 0
    actions.build_turn(game, 0, 4)
    assert "workshop" in player.built
    assert player.units["brute"] == 4 and player.units["blaster"] == 1
    assert player.tokens["rage"] == 0 and player.resources == {"wood": 0, "iron": 0, "gold": 0}
    assert orc.asked == ["build_recruit"]                                # весь план — одним ответом


def test_chiefs_house_inside_build_raises_level_with_rage_from_hiring():
    orc = Scripted(build_recruit=lambda d: ["done"] if d["built"] else
                   [{"hire": {"brute": 4}}, {"raise": {"build": 2}}, {"build": "trophy_hall"}])
    game = make(["orcs", "humans"], [orc, Scripted()])
    st = game.state
    st.action_order, st.phase = ["wood", "build", "iron", "gold"], 2
    player = st.players[0]
    player.built += ["war_idol", "arena", "camp", "gym", "chiefs_house"]
    player.resources = {"wood": 0, "iron": 6, "gold": 12}
    actions.build_turn(game, 0, 2)
    assert "trophy_hall" in player.built and player.raised == {"build": 2}    # здание 4-го уровня в действии 2-го
    assert player.tokens["rage"] == 0
    assert orc.asked == ["build_recruit"]       # без «done» спросили бы снова, но делать больше нечего


def test_one_building_per_action_and_failed_step_keeps_earlier_ones():
    game = make(["humans", "orcs"])
    player = game.state.players[0]
    player.built.append("warriors_guild")
    player.resources = {"wood": 20, "iron": 20, "gold": 2}
    turn = actions.BuildTurn(4)
    steps = [{"build": "market"}, {"hire": {"soldier": 1}}, {"hire": {"soldier": 1}}, {"build": "town_hall"}]
    assert actions.check_steps(game, 0, turn, steps) is None            # по существу проверяется только первый
    assert actions.run_steps(game, 0, turn, steps) is True               # не «done»: спросить снова
    assert player.built[-1] == "market" and player.units["soldier"] == 3   # 2 стартовых + 1
    assert turn.rejected["step"] == {"hire": {"soldier": 1}} and "not enough" in turn.rejected["error"]
    assert "NOT PERFORMED" in actions.build_text(game, 0, turn)
    assert "one building per action" in actions.check_step(game, 0, turn, {"build": "town_hall"})
    assert actions.check_steps(game, 0, turn, ["done", {"build": "town_hall"}]) == '"done" must be the last step'


def test_orc_strength_by_battle_and_rage_vp():
    game = make(["orcs", "humans"])
    player = game.state.players[0]
    player.built += ["camp", "workshop", "metal_shop"]
    assert army_strength(game, 0, {"scout": 1}, Battle("monster", "Карракс")) == 4 + 4     # горы, Металка
    assert army_strength(game, 0, {"blaster": 1}, Battle("fortress", "Икара")) == 12 + 4
    gain(game, 0, {"rage": 19}, "test")
    assert scoring.score_player(game, 0)["resources"] == 4


def test_asterisk_properties_work_without_the_building():
    """Правила, с. 4: эффект под звёздочкой — свойство войск, оно действует и без здания."""
    game = make(["orcs", "humans"])
    assert game.state.players[0].built == []
    army = {"blaster": 1, "scout": 1}
    assert army_strength(game, 0, army, Battle("fortress", "Бринлор")) == 12 + 4
    assert combat.check_commit(game, 0, army, army, Battle("fortress", "Бринлор"), 14) is None
    assert unit_strength(game, 0, "blaster", Battle("monster", "Бринлор")) == 9
    assert "blaster" not in actions.hireable_units(game, 0)


def test_chiefs_house_raises_current_and_past_actions():
    orc = Scripted(raise_action={"build": 2, "wood": 1})
    game = make(["orcs", "humans"], [orc, Scripted()])
    st = game.state
    st.action_order, st.phase = ["wood", "build", "iron", "move"], 2
    player = st.players[0]
    player.built.append("chiefs_house")
    player.tokens["rage"] = 3
    from heroes.engine.special import raise_actions
    assert raise_actions(game, 0, "build") == 2
    assert player.resources["wood"] == 1 and player.tokens["rage"] == 0


def far_region(game, p, mp):
    """Регион, до которого игроку p не хватает ровно 1–2 ОП из mp."""
    from heroes.engine.movement import reachable
    near = reachable(game, p, mp)
    return next(r for r, c in reachable(game, p, mp + 2).items() if r not in near)


def test_chiefs_house_raise_and_destination_are_one_secret_answer():
    """Дом вождей: повышение перемещения и регион выбираются вместе; регион должен быть достижим с итоговыми ОП."""
    def answer(game, d):
        far = far_region(game, 0, d["mp"])
        extra = next(x for x in (1, 2) if far in d["extra"][str(x)])
        return {"destination": far, "raise": {"move": extra}, "buy": 0}
    orc = Scripted()
    game = make(["orcs", "humans"], [orc, Scripted()])
    orc.answers["destination"] = lambda d: answer(game, d)
    st = game.state
    st.action_order, st.phase = ["wood", "iron", "move", "build"], 3
    player = st.players[0]
    player.built.append("chiefs_house")
    player.tokens["rage"] = 4
    movement.move_action(game, 3)
    assert "raise_action" not in orc.asked and orc.asked.count("destination") == 1
    assert player.region == far_region(make(["orcs", "humans"]), 0, 3) and player.tokens["rage"] < 4
    assert player.move_points > 3


def test_move_answer_must_be_reachable_with_the_chosen_extra_mp():
    game = make(["undead", "humans"])
    player = game.state.players[0]
    player.built.append("hearse")
    player.resources["wood"] = 3
    from heroes.engine.movement import base_move_points, buy_offer, check_move, reach_by_extra
    buy = buy_offer(game, 0)
    reach = reach_by_extra(game, 0, base_move_points(game, 0, 1), buy["max"])
    far = far_region(game, 0, 1)
    need = next(x for x in range(buy["max"] + 1) if far in reach[x])
    assert check_move({"destination": far, "buy": need}, reach, None, buy) is None
    if need:
        assert check_move({"destination": far, "buy": need - 1}, reach, None, buy)
    assert check_move({"destination": far, "buy": buy["max"] + 1}, reach, None, buy)
    assert check_move({"destination": far, "raise": {"move": 1}}, reach, None, buy)
    assert check_move(far, reach, None, buy)


def test_old_saves_with_separate_raise_and_buy_questions_still_replay():
    """Раньше Дом вождей и Катафалк спрашивали отдельно и открыто до региона: такие сохранения загружаются."""
    game = make(["undead", "humans"], [Scripted(), Scripted()])
    player = game.state.players[0]
    player.built.append("hearse")
    player.resources["wood"] = 4
    here = player.region
    game.replay_queue.extend([{"round": 1, "player": 0, "kind": "buy_move_points", "answer": 2},
                              {"round": 1, "player": 0, "kind": "destination", "answer": here}])
    movement.move_action(game, 1)
    assert player.move_points == 3 and player.resources["wood"] == 0


def test_army_preview_counts_metal_shop_and_scouts_in_mountains():
    """Сводка силы на планшете считает, как в сражении в регионе игрока: Металка и разведчики в горах."""
    from heroes.protocol.snapshot import snapshot
    game = make(["orcs", "humans"])
    player = game.state.players[0]
    player.units = {"scout": 3, "brute": 0, "blaster": 0}
    total = lambda: snapshot(game)["players"][0]["army_strength"]
    mountain = next(r for r in game.state.available if game.terrain(r) == "mountain"
                    and game.map.regions[r].object != "fortress")
    player.region = next(r for r in game.state.available if game.terrain(r) != "mountain"
                         and game.map.regions[r].object != "fortress")
    assert total() == 6
    player.built.append("metal_shop")
    assert total() == 10
    player.region = mountain
    assert total() == 16


# --- Эльфы ------------------------------------------------------------------------------------

def test_armorer_example():
    """Пример Лены (с. 19): 2 энта, грифон, пикси, 2 дерева. Лена модифицирует энта и грифона:
    10 + 6 + 5 + 1 = 22. Движок модифицирует самые выгодные единицы — обоих энтов: 10 + 10 + 3 + 1 = 24."""
    game = make(["elves", "humans"])
    game.state.players[0].built.append("armorer")
    army = {"ent": 2, "griffin": 1, "pixie": 1}
    assert army_strength(game, 0, army, Battle("pvp", "Икара"), upgrades=2) == 24
    assert army_strength(game, 0, {"ent": 1, "griffin": 1, "pixie": 1}, Battle("pvp", "Икара"), upgrades=2) == 16


def test_aerie_takes_artifact_for_wood():
    """Пример (с. 19): 4 грифона, Золен — артефакт за 2 дерева."""
    game = make(["elves", "humans"], [Scripted(take_artifact=True), Scripted()])
    player = game.state.players[0]
    player.built.append("aerie")
    player.units["griffin"] = 4
    player.resources["wood"] = 2
    player.region = "Золен"
    combat.try_take_artifact(game, 0)
    assert game.state.artifact_holder["Золен"] == 0 and player.resources["wood"] == 0


def test_elves_bonuses():
    game = make(["elves", "humans"])
    player = game.state.players[0]
    player.built += ["forge", "wooded_cliff"]
    game.state.artifact_holder.update({"Золен": 0, "Цурт": 0})
    assert army_strength(game, 0, {}, Battle("monster", "Икара")) == 1 + 3      # и к нулевой силе
    assert army_strength(game, 0, {}, Battle("pvp", "Икара")) == 1
    assert building_cost(game, 0, "observatory") == {"iron": 2}
    actions.gather(game, "gold", 2)
    assert player.resources["gold"] == 4


def test_undead_tascar_makes_foundations_cheap():
    game = make(["undead", "humans"])
    game.state.artifact_holder["Таскар"] = 0
    assert step_cost(game, 0, "Есмор") == 1         # фундамент
    assert step_cost(game, 0, "Икара") == 2


def test_armorer_upgrade_is_part_of_the_commitment():
    """Лавка бронника: модификацию называют вместе с заявкой — отдельного вопроса нет, оплачивается только
    то, что заявленные единицы могут использовать."""
    elves = Scripted(capture_fortress={"army": {"ent": 1}, "upgrade": 2})
    game = make(["elves", "humans"], [elves, Scripted()])
    player = game.state.players[0]
    player.built.append("armorer")
    player.units.update({"pixie": 0, "ent": 1})
    player.resources["wood"] = 3
    player.region = "Икара"   # крепость силы 7: энт 6 не возьмёт, модифицированный (10) — возьмёт
    combat.try_capture_fortress(game, 0)
    assert elves.asked == ["capture_fortress"]
    assert game.state.fortress_owner["Икара"] == 0
    assert player.resources["wood"] == 2 + game.map.regions["Икара"].fortress.loot.get("wood", 0)   # одна модификация


def test_armorer_asks_separately_after_a_plain_commitment():
    """Заявка без поля upgrade (старые сохранения, простые боты): о модификации движок спрашивает отдельно."""
    elves = Scripted(capture_fortress={"ent": 1}, battle_upgrade=1)
    game = make(["elves", "humans"], [elves, Scripted()])
    player = game.state.players[0]
    player.built.append("armorer")
    player.units.update({"pixie": 0, "ent": 1})
    player.resources["wood"] = 3
    player.region = "Икара"
    combat.try_capture_fortress(game, 0)
    assert elves.asked == ["capture_fortress", "battle_upgrade"]
    assert game.state.fortress_owner["Икара"] == 0


class Answers(Player):
    """Отвечает по очереди из списка (на каждое решение — следующий ответ) и запоминает отклонения."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.invalid = []

    def decide(self, decision, view):
        return self.answers.pop(0)

    def on_invalid(self, decision, answer, error):
        self.invalid.append(error)


def test_hopeless_fortress_attack_is_rejected():
    human = Answers({"soldier": 1}, None)   # сила 2 против крепости 7 — не принимается; потом отказ
    game = make(["humans", "orcs"], [human, Scripted()])
    player = game.state.players[0]
    player.units.update({"soldier": 4, "knight": 0, "archmage": 0})
    player.region = "Икара"
    combat.try_capture_fortress(game, 0)
    assert len(human.invalid) == 1 and "does not exceed 7" in human.invalid[0]
    assert "Икара" not in game.state.fortress_owner and player.units["soldier"] == 4   # никто не погиб


def test_hopeless_monster_fight_is_rejected_but_old_saves_replay():
    human = Answers({"monster": 0, "army": {"soldier": 1}}, {"monster": 0, "army": {"soldier": 2}})
    game = make(["humans", "orcs"], [human, Scripted()])
    player = game.state.players[0]
    player.units.update({"soldier": 3, "knight": 0, "archmage": 0})
    combat.fight_monster(game, 0, leftover=0)
    assert len(human.invalid) == 1 and "certain to be lost" in human.invalid[0]   # 2 против чудовища силы 2
    assert player.monsters[0] == "monster" and player.units["soldier"] == 1
    # в сохранении старой версии такой бой мог быть записан — он проигрывается, как был
    old = make(["humans", "orcs"], [Scripted(), Scripted()])
    old.replay_queue.append({"round": 0, "player": 0, "kind": "fight_monster",
                             "answer": {"monster": 0, "army": {"soldier": 1}}})
    old.state.players[0].units.update({"soldier": 3, "knight": 0, "archmage": 0})
    combat.fight_monster(old, 0, leftover=0)
    assert old.state.players[0].monsters[0] is None and old.state.players[0].units["soldier"] == 2


def test_start_region_is_shown_as_soon_as_it_is_chosen():
    seen = []

    class Picker(Player):
        def decide(self, decision, view):
            if decision.kind != "choose_start":
                return decision.default
            seen.append([pl.region for pl in view.game.state.players])
            return decision.data["options"][0]
    game = Game([Picker(), Picker(), Picker()], factions=["humans", "orcs", "elves"])
    game.setup()
    # первым выбирает игрок справа от первого (место 2), потом место 1; место 0 получает последний регион
    assert seen[0] == [None, None, None]
    assert seen[1][2] is not None and seen[1][:2] == [None, None]


def stance_options(first, factions=("humans", "orcs"), setup=None):
    """Варианты второго игрока в Икаре после заявления первого (None — второго не спросили)."""
    seen = []
    second = Scripted(region_stance=lambda d: seen.append(d["options"]) or d["options"][0])
    game = make(list(factions), [Scripted(region_stance=first), second])
    for pl in game.state.players:
        pl.region = "Икара"
    if setup:
        setup(game)
    combat.resolve_encounters(game, {0: 0, 1: 0})
    return seen[0] if seen else None, game


def test_region_stance_offers_only_options_with_different_outcomes():
    # первый: каждый вариант что-то значит
    first = []
    stance_options(lambda d: first.append(d["options"]) or "pass")
    assert first == [["fight", "pass", "claim"]]
    # первый пасует: «претендовать» лишь даёт второму право на объект (от него можно отказаться) — «пас» не нужен
    assert stance_options("pass")[0] == ["fight", "claim"]
    # первый претендует: «сражаться» и «претендовать» одинаково начинают сражение
    assert stance_options("claim")[0] == ["fight", "pass"]
    # первый сражается: сражение будет при любом ответе — второго не спрашивают
    options, game = stance_options("fight")
    assert options is None and any("in Икара: fight" in m for m in game.state.log[-20:])


def test_region_stance_keeps_claim_when_the_battle_can_be_bought_off():
    def mine(game):
        game.state.players[0].built.append("mine")
        game.state.players[0].resources["wood"] = 4
    # наги могут откупиться — тогда объект получит единственный претендент; «пас» равносилен «сражаться»
    options, _ = stance_options("fight", ("nagas", "humans"), mine)
    assert options == ["fight", "claim"]


def test_old_save_with_a_recorded_stance_that_is_now_forced_replays():
    game = make(["humans", "orcs"])
    for pl in game.state.players:
        pl.region = "Икара"
    game.recorded_forced = frozenset({"region_stance"})   # сохранение версии 2: заявления записаны всегда
    game.replay_queue.extend([{"round": 0, "player": 0, "kind": "region_stance", "answer": "fight"},
                              {"round": 0, "player": 1, "kind": "region_stance", "answer": "pass"}])
    combat.resolve_encounters(game, {0: 0, 1: 0})
    assert not game.replay_queue
