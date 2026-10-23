"""Имена сохранений: дата, время и номер раунда; автосохранение — в начале каждого раунда, хранятся все."""
import re

from heroes import savegame
from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot

NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_round(\d{2})(_\w+)?\.json")


def test_names_have_date_and_round(tmp_path):
    game = Game([RandomBot(1), RandomBot(2)], autosave=str(tmp_path))
    game.run()
    autosaves = sorted(tmp_path.glob("*.json"), key=lambda f: NAME.fullmatch(f.name)[1])
    assert len(autosaves) == 16   # в начале каждого раунда — своё, прежние не удаляются
    rounds = [NAME.fullmatch(f.name) for f in autosaves]
    assert [(m[1], m[2]) for m in rounds] == [(f"{r:02d}", "_auto") for r in range(1, 17)]
    autosaves = autosaves[-1:]   # сохранено в начале 16-го раунда
    manual = savegame.save(game, savegame.default_path(game, folder=tmp_path))
    m = NAME.fullmatch(manual.name)
    assert m and m[1] == "16" and m[2] is None
    assert savegame.latest(tmp_path) == manual
    loaded = savegame.load(autosaves[0], lambda meta, names: [RandomBot(1), RandomBot(2)])
    assert loaded.replay_queue
