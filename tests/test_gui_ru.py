"""Русский перевод журнала в окне: каждая строка движка переводится, английских слов не остаётся."""
import random
import re

from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import static_data
from heroes.gui.ru import Translator, popup, reason

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
    assert t.line("Боря connected (192.168.1.7)") == "Боря подключился (192.168.1.7)"
    assert t.line("Waiting for network players: Боря") == "Ждём сетевых игроков: Боря"
    assert t.line("\nThe game is open for network players. They should run:").endswith("Им нужно выполнить:")
    assert reason("strength-5 monster defeated: resources of your choice") == "побеждено чудовище силы 5: ресурсы на выбор"
    assert t.line("something new") == "something new"   # незнакомое — как есть


def test_faction_lines():
    t = Translator()
    assert t.line("Аня's Гидра moves to Флисс") == "Гидра игрока Аня перемещается в регион Флисс"
    assert t.line("Battle in Флисс: Боря — strength 5, Аня's Гидра — strength 18") == \
        "Сражение в регионе Флисс: Боря — сила 5, Гидра игрока Аня — сила 18"
    assert t.line("Аня reached 12 rage — 2 VP circled") == "Аня: ярость достигла 12 — обведено 2 ПО"
    assert t.line("Аня gains rage 2 (Идол войны: battle won)") == "Аня получает: ярость 2 (Идол войны: победа в сражении)"
    assert t.line("Боря loses Скелет ×1 (the curse of Цурт)") == "Боря теряет: Скелет ×1 (проклятие региона Цурт)"


def test_lessons_outcome_lines():
    t = Translator()
    assert t.line("Claude 1: strategy notes updated (general, reminders): strategy/m/high/orcs.md; "
                  "scripts changed: sim_tools.py; committed to git") == (
        "Claude 1: заметки по стратегии обновлены (general, reminders): strategy/m/high/orcs.md; "
        "изменены скрипты: sim_tools.py; сохранено в git")
    assert t.line("Claude 2: no changes to the strategy notes") == "Claude 2: заметки по стратегии без изменений"


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
