"""Русский перевод для браузера: каждая строка движка переводится, английских слов не остаётся."""
import random
import re

from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import static_data
from heroes.web.common import name_of, with_factions
from heroes.web.marks import MARKS as M
from heroes.web.ru import Translator, popup, reason

ENGLISH = re.compile(r"\b(gains?|wood|iron|gold|VP|MP|strength|artifact|monster|fortress|round|wins|takes|in|rage|"
                     r"science|pays|curses|lake|note|pair|summons|dies|moves|loses|resurrects|exchanges|upgrades|buys|"
                     r"by|of|the)\b|'s\b", re.I)
FACTIONS = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]


def test_whole_game_logs_are_translated():
    """Партии всеми фракциями: строки их особых механик тоже переводятся."""
    untranslated = set()
    for seed in range(40):
        n = 2 + seed % 3
        names = ["Аня", "Bot 1", "Player 3", "X: Y"][:n]   # имена с пробелами и двоеточием тоже
        factions = ["humans"] * n if seed < 10 else random.Random(seed).sample(FACTIONS, n)
        game = Game([RandomBot(seed * 7 + i) for i in range(n)], names=names, factions=factions)
        game.run()
        t = Translator(static_data(game))
        for line in game.state.log:
            ru = t.line(line)
            for name in names:
                ru = ru.replace(name, "")
            if ru == line or ENGLISH.search(ru):
                untranslated.add(line)
    assert not untranslated, sorted(untranslated)[:10]


def test_server_messages_and_reasons():
    t = Translator()
    assert t.line("Wrong password.") == "Неверный пароль."
    assert t.line("The host interrupted the game. It is saved and can be continued.").startswith("Хозяин прервал партию")
    assert reason("strength-5 monster defeated: resources of your choice") == f"побеждено чудовище 5{M['shield']}: ресурсы на выбор"
    assert t.line("something new") == "something new"   # незнакомое — как есть


def test_faction_lines():
    t = Translator()
    assert t.line("Аня's Гидра moves to Флисс") == "Гидра игрока Аня перемещается в регион Флисс"
    assert t.line("Battle in Флисс: Боря — strength 5, Аня's Гидра — strength 18") == \
        f"Сражение в регионе Флисс: Боря — 5{M['power']}, Гидра игрока Аня — 18{M['power']}"
    assert t.line("Аня reached 12 rage — 2 VP circled") == f"Аня: на складе 12{M['rage']} — обведено 2{M['laurel']}"
    assert t.line("Аня gains rage 2 (Идол войны: battle won)") == f"Аня получает: 2{M['rage']} (Идол войны: победа в сражении)"
    assert t.line("Боря loses Скелет ×1 (the curse of Цурт)") == "Боря теряет: Скелет ×1 (проклятие региона Цурт)"


def test_icons_in_log():
    """Ресурсы, ОП, ПО, сила и действия раунда — значками, как в памятке (перемещение — конь, ОП — подкова)."""
    t = Translator()
    assert t.line("Round actions: 1. move, 2. gather wood, 3. build & hire, 4. gather gold") == \
        f"Действия раунда: 1. {M['move']}, 2. {M['wood']}, 3. {M['build']}, 4. {M['gold']}"
    assert t.line("Аня moves to Флисс (spent 2 of 3 MP)") == f"Аня перемещается в регион Флисс (потрачено 2 из 3{M['mp']})"
    assert t.line("Аня gains iron 1, gold 2 (gather gold)") == f"Аня получает: 1{M['iron']} 2{M['gold']} (добыча {M['gold']})"
    assert t.line("Аня defeats a strength-5 monster (army strength 7)") == \
        f"Аня побеждает чудовище 5{M['shield']} (войска 7{M['power']})"
    assert t.line("Аня builds Таверна (level 2)") == f"Аня строит: Таверна ({M['level2']})"
    assert t.line("Аня: 12 VP {'buildings': 5, 'foundations': 2, 'resources': 1, 'battles': 3, 'artifacts': 1}") == \
        f"Аня: 12{M['laurel']} (здания 5, 2{M['foundation']}, склад 1, 3{M['victory']}, 1{M['artifact']})"
    assert t.line("Аня circles a note and completes a pair") == f"Аня обводит {M['note']} и закрывает пару"
    assert reason("gather iron") == f"добыча {M['iron']}"


def test_names_with_faction():
    static = {"names": ["Аня", "Боря"], "player_factions": ["humans", "nagas"],
              "factions": {"humans": {"name": "Люди"}, "nagas": {"name": "Наги"}}}
    assert name_of(static, 1) == "Боря (Наги)"
    assert with_factions(static, "Аня атакует Боря; Аня (Люди) уже") == "Аня (Люди) атакует Боря (Наги); Аня (Люди) уже"


def test_popups_for_whole_games():
    """Таблички над фишками: у каждой — своя фишка и непустая подпись; постройки, наём и ресурсы — всегда."""
    for seed in range(12):
        n = 2 + seed % 3
        names = ["Аня", "Bot 1", "Player 3", "X: Y"][:n]   # имена с пробелами и двоеточием тоже
        game = Game([RandomBot(seed * 7 + i) for i in range(n)], names=names,
                    factions=random.Random(seed).sample(FACTIONS, n))
        game.run()
        for line in game.state.log:
            found = popup(names, line)
            if found:
                (kind, p), text = found
                assert kind in ("player", "hydra") and 0 <= p < len(names) and text, line
            if re.fullmatch(r".+ (builds .+ \(level \d\)|hires: .+)", line):
                assert found, line
            if " moves to " in line or " stays in " in line:
                assert found is None, line
