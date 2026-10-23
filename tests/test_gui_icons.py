"""Иконки ресурсов вместо букв: метки в тексте и их отрисовка."""
import tkinter as tk

import pytest

from heroes.gui import icons
from heroes.gui.common import res


def test_res_uses_marks_not_letters():
    text = res({"wood": 2, "gold": 1, "iron": 0})
    assert text == f"2{icons.MARKS['wood']} 1{icons.MARKS['gold']}"
    assert icons.segments(text) == ["2", icons.MARKS["wood"], " 1", icons.MARKS["gold"]]
    assert not icons.has_icons("дерево 2")
    assert res({"wood": 1}, faded=True) == f"1{icons.MARKS['wood_off']}"
    for kind in icons.MARKS.keys() - icons.AVATARS:   # аватарки — не из assets/icons
        assert len(icons.heights(kind)) >= 5, kind
    assert icons.markup("4{wood}, {laurel} {nope}") == f"4{icons.MARKS['wood']}, {icons.MARKS['laurel']} {{nope}}"


def test_canvas_text_and_labels():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        canvas = tk.Canvas(root)
        text = "цена " + res({"wood": 3, "iron": 2, "any": 1})
        x0, y0, x1, y1 = icons.canvas_text(canvas, 200, 100, text, ("DejaVu Sans", 10), "black", anchor="center")
        assert abs((x0 + x1) / 2 - 200) < 1 and abs((y0 + y1) / 2 - 100) < 1
        assert len(canvas.find_withtag("all")) == 6   # три куска текста и три иконки
        x0, y0, _, _ = icons.canvas_text(canvas, 10, 20, text, ("DejaVu Sans", 10), "black", anchor="nw")
        assert (x0, y0) == (10, 20)
        label = icons.RichLabel(root, "Солдат (" + res({"gold": 2}) + ")")
        assert sum(1 for w in label.winfo_children() if str(w.cget("image"))) == 1
        chosen = tk.StringVar(root, "")
        row = icons.rich_radio(root, "Арсенал, цена " + res({"wood": 3}), value="a", variable=chosen)
        rich = [w for w in row.winfo_children() if isinstance(w, icons.RichLabel)][0]
        rich.winfo_children()[0].event_generate("<Button-1>")
        assert chosen.get() == "a"
    finally:
        root.destroy()


def test_board_texts_and_pictures():
    from heroes.engine.game import Game
    from heroes.players.random_bot import RandomBot
    from heroes.protocol.snapshot import snapshot, static_data
    game = Game([RandomBot(1), RandomBot(2), RandomBot(3)])
    game.run()
    static = static_data(game)
    for faction in static["factions"].values():
        texts = [b["text_board"] for b in faction["buildings"].values()] + list(faction["artifacts_board"].values())
        for text in texts:   # все значки в фигурных скобках известны
            assert "{" not in icons.markup(text), text
        for region in faction["artifacts"]:
            assert icons.heights(f"artifact_{region}"), region
    assert all(icons.heights(f"monster_{i}") for i in range(len(static["monsters"])))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        from heroes.gui.boardview import BoardView
        root.geometry("640x900")
        board = BoardView(root)
        board.pack(fill="both", expand=True)
        root.update()
        board.show(static, snapshot(game), 0, 0)
        root.update()
        assert board.canvas.find_withtag("all")
    finally:
        root.destroy()
