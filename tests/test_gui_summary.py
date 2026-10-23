"""Сводка «Все игроки»: условия артефактов и фракция при имени игрока."""
from heroes.gui.boardview import condition_progress
from heroes.gui.common import name_of, with_factions

STATIC = {"names": ["Аня", "Боря", "Вера"], "player_factions": ["humans", "humans", "nagas"],
          "factions": {"humans": {"name": "Люди"},
                       "nagas": {"name": "Наги", "artifact_conditions": {"Цурт": {"condition": "pay", "cost": {"krak": 3}}}}},
          "regions": {"Цурт": {"terrain": "mountain", "artifact": {"condition": "pay", "cost": {"iron": 12}}},
                      "Таскар": {"terrain": "field", "artifact": {"condition": "pay", "cost": {"wood": 12}}},
                      "Шог Ид": {"terrain": "mountain", "artifact": {"condition": "mountainsVisited", "count": 4}},
                      "Содмур": {"terrain": "field", "artifact": {"condition": "battleWins", "count": 5}}}}


def player(**kw):
    pl = {"resources": {"wood": 0, "iron": 0, "gold": 0}, "foundations": {}, "victories": 0, "visited": [],
          "faction": "humans"}
    pl.update(kw)
    return pl


def test_condition_progress():
    state = {"players": [player(resources={"wood": 0, "iron": 13, "gold": 0}, visited=["Цурт", "Таскар"]),
                         player(victories=5),
                         player(faction="nagas", resources={"wood": 0, "iron": 13, "gold": 0},
                                holdings={"wood": 0, "iron": 13, "gold": 0, "krak": 2}, mountains=["Цурт", "Шог Ид"])],
             "artifact_holder": {}}
    assert condition_progress(STATIC, state, 0, "Цурт") == "13 / 12 ✓"
    assert condition_progress(STATIC, state, 0, "Шог Ид") == "1 / 4"
    assert condition_progress(STATIC, state, 1, "Содмур") == "5 / 5 ✓"
    state["artifact_holder"] = {"Содмур": 1}
    assert condition_progress(STATIC, state, 1, "Содмур") == "✦ взят"
    assert condition_progress(STATIC, state, 0, "Содмур") == "0 / 5"
    # наги: свои условия (3 крака) и горы — по отметкам в момент посещения
    assert condition_progress(STATIC, state, 2, "Цурт") == "2 / 3 (краки)"
    assert condition_progress(STATIC, state, 2, "Шог Ид") == "2 / 4"


def test_names_with_faction():
    assert name_of(STATIC, 1) == "Боря (Люди)"
    assert with_factions(STATIC, "Аня атакует Боря; Аня (Люди) уже") == "Аня (Люди) атакует Боря (Люди); Аня (Люди) уже"


def test_turn_order_and_next_first_players():
    from heroes.gui.common import next_first_players, turn_order
    static = {"names": ["A", "B", "C"], "rounds": 16}
    setup = {"round": 0, "first_player": 1}
    assert turn_order(static, setup) == [0, 2, 1]                 # старт: справа от первого, против часовой
    assert next_first_players(static, setup, 3) == [(1, 1), (2, 2), (3, 0)]
    middle = {"round": 5, "first_player": 2}
    assert turn_order(static, middle) == [2, 0, 1]
    assert next_first_players(static, middle, 3) == [(6, 0), (7, 1), (8, 2)]   # первым становится сосед слева
    assert next_first_players(static, {"round": 15, "first_player": 0}, 3) == [(16, 1)]
    assert next_first_players(static, {"round": 16, "first_player": 0}, 3) == []


def test_condition_progress_of_elves_with_aerie():
    """Эльфы: артефакт и за дерево при достаточном числе грифонов — «дерево 7/12 или 7/5, грифоны 3/5»."""
    static = {**STATIC, "factions": {**STATIC["factions"], "elves": {
        "name": "Эльфы", "units": {"griffin": {"name": "Грифон"}},
        "artifact_wood": {"building": "aerie", "unit": "griffin", "prices": {"Таскар": 5, "Цурт": 4, "Содмур": 3}}}}}
    elf = player(faction="elves", resources={"wood": 7, "iron": 1, "gold": 0}, built=[],
                 units={"griffin": {"count": 3}})
    state = {"players": [elf], "artifact_holder": {}}
    assert condition_progress(static, state, 0, "Таскар") == "дерево 7/12 или 7/5, грифоны 3/5 (нет Гнездовья)"
    elf["built"] = ["aerie"]
    assert condition_progress(static, state, 0, "Таскар") == "дерево 7/12 или 7/5, грифоны 3/5"
    assert condition_progress(static, state, 0, "Цурт") == "железо 1/12 или дерево 7/4, грифоны 3/4"
    assert condition_progress(static, state, 0, "Содмур") == "0 / 5 или дерево 7/3, грифоны 3/3 ✓"
