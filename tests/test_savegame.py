"""Сохранения разложены по папкам вида (auto, manual, interrupted, final); в имени — номер раунда, дата и время.
Автосохранение — в начале каждого раунда, хранятся все; доигранная партия сохраняется ещё раз — с итогами."""
import re

from heroes import savegame
from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot

NAME = re.compile(r"round(\d{2})(_[a-z]+)?_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json")


def test_names_have_date_and_round(tmp_path):
    game = Game([RandomBot(1), RandomBot(2)], autosave=str(tmp_path))
    game.run()
    autosaves = sorted((tmp_path / "auto").glob("*.json"), key=lambda f: NAME.fullmatch(f.name)[1])
    assert len(autosaves) == 16   # в начале каждого раунда — своё, прежние не удаляются
    rounds = [NAME.fullmatch(f.name) for f in autosaves]
    assert [(m[1], m[2]) for m in rounds] == [(f"{r:02d}", "_auto") for r in range(1, 17)]
    autosaves = autosaves[-1:]   # сохранено в начале 16-го раунда
    manual = savegame.save(game, savegame.default_path(game, folder=tmp_path))
    m = NAME.fullmatch(manual.name)
    assert manual.parent == tmp_path / "manual" and m and m[1] == "16" and m[2] is None
    interrupted = savegame.default_path(game, savegame.INTERRUPTED, tmp_path)
    assert interrupted.parent == tmp_path / "interrupted" and NAME.fullmatch(interrupted.name)[2] == "_interrupted"
    assert savegame.latest(tmp_path) == manual
    loaded = savegame.load(autosaves[0], lambda meta, names: [RandomBot(1), RandomBot(2)])
    assert loaded.replay_queue


def test_finished_game_is_saved_with_its_results(tmp_path):
    game = Game([RandomBot(1), RandomBot(2)], autosave=str(tmp_path))
    scores = game.run()
    final = game.final_save
    assert final.parent == tmp_path / "final" and NAME.fullmatch(final.name).groups() == ("16", "_final")
    assert savegame.latest(tmp_path).name.startswith("round16_auto_")   # --continue: доигранную продолжать нечего
    loaded = savegame.load(final, lambda meta, names: [RandomBot(7), RandomBot(8)], autosave=str(tmp_path))
    assert loaded.run() == scores and loaded.state.round == 16   # итоги — те же, ничего не спрашивая
    assert loaded.final_save is None and len(list((tmp_path / "final").glob("*.json"))) == 1
