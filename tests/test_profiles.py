"""Профили: хранение, запись доигранной партии, аватарки, экран профилей."""
import tkinter as tk
from tkinter import ttk

import pytest

from heroes import profiles, savegame
from heroes.engine.game import Game
from heroes.gui import avatars
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import snapshot, static_data


def finished_game(n=3, first=1, kinds=("human", "claude", "bot", "remote")):
    game = Game([RandomBot(i) for i in range(n)], names=["Аня", "Claude", "Бот", "Гость"][:n], first_player=first,
                meta={"players": list(kinds[:n]), "seed": 0})
    game.run()
    return game


def test_create_rename_delete_and_active(tmp_path):
    assert profiles.get_active(tmp_path) is None
    anya = profiles.create("Аня", tmp_path)
    assert profiles.get_active(tmp_path).id == anya.id   # первый профиль — активный
    borya = profiles.create("Боря", tmp_path)
    assert profiles.get_active(tmp_path).id == anya.id
    assert [p.name for p in profiles.all_profiles(tmp_path)] == ["Аня", "Боря"]
    with pytest.raises(ValueError):
        profiles.create("аня", tmp_path)                  # имена различаются без учёта регистра
    with pytest.raises(ValueError):
        profiles.rename(borya, "  ")
    profiles.rename(borya, "Борис")
    assert profiles.get(borya.id, tmp_path).name == "Борис"
    assert profiles.unique_name(folder=tmp_path) == "Игрок 1"
    profiles.delete(anya)
    assert profiles.get_active(tmp_path).id == borya.id   # активным становится оставшийся
    profiles.delete(borya)
    assert profiles.get_active(tmp_path) is None and profiles.all_profiles(tmp_path) == []


def test_game_record_from_finished_game(tmp_path):
    game = finished_game()
    static, state = static_data(game), snapshot(game)
    anya = profiles.create("Аня", tmp_path)
    assert profiles.record_game(static, state, {0: anya.id}, tmp_path) == ["Аня"]
    record = profiles.get(anya.id, tmp_path).games[0]
    assert record["you"] == 0 and record["game_id"] == game.meta["game_id"]
    players = record["players"]
    assert [p["kind"] for p in players] == ["human", "claude", "bot"]
    assert [p["turn"] for p in players] == [3, 1, 2]       # первый игрок первого раунда — место 1
    assert {p["faction_name"] for p in players} == {"Люди"}
    assert [p["vp"] for p in players] == [s["score"]["total"] for s in state["players"]]
    assert [p for p, pl in enumerate(players) if pl["winner"]] == profiles.winners(state)
    assert players[0]["profile"] == anya.id and players[1]["profile"] is None

    # та же партия, доигранная ещё раз из сохранения, не дублируется
    profiles.record_game(static, state, {0: anya.id}, tmp_path)
    assert len(profiles.get(anya.id, tmp_path).games) == 1
    s = profiles.summary(profiles.get(anya.id, tmp_path))
    assert s["games"] == 1 and s["best_vp"] == players[0]["vp"]


def test_game_id_survives_save_and_load(tmp_path):
    game = Game([RandomBot(0), RandomBot(1)], meta={"players": ["bot", "bot"], "seed": 0})
    path = savegame.save(game, tmp_path / "g.json")
    loaded = savegame.load(path, lambda meta, names: [RandomBot(0), RandomBot(1)])
    assert loaded.meta["game_id"] == game.meta["game_id"]


def test_seat_profiles_from_meta_or_by_name(tmp_path):
    anya = profiles.create("Аня", tmp_path)
    names = ["Аня", "Боря"]
    assert profiles.seat_profiles({"players": ["human", "human"]}, names, tmp_path) == {0: anya.id}
    assert profiles.seat_profiles({"players": ["claude", "human"]}, names, tmp_path) == {}   # Claude не профиль
    # в meta записано явно (новая партия из окна) — по именам не ищем
    assert profiles.seat_profiles({"players": ["human", "human"], "profiles": {}}, names, tmp_path) == {}
    assert profiles.seat_profiles({"players": ["human"], "profiles": {"1": anya.id}}, names, tmp_path) == {1: anya.id}


def test_builtin_avatars_exist():
    for kind in avatars.KINDS:
        for color in ["0", "1", "2", "3", "neutral"]:
            w, h, _ = avatars.read_png((avatars.ASSETS / f"{kind}_{color}.png").read_bytes())
            assert (w, h) == (avatars.MASTER, avatars.MASTER)


def test_png_roundtrip_and_resize():
    size = 8
    rgba = bytearray()
    for y in range(size):
        for x in range(size):
            rgba += bytes((255, 0, 0, 255) if x < size // 2 else (0, 0, 255, 0))   # справа — прозрачное
    w, h, back = avatars.read_png(avatars.png_bytes(size, rgba))
    assert (w, h, back) == (size, size, rgba)
    small = avatars.resize(size, size, back, 2)
    assert small[0:4] == bytes((255, 0, 0, 255)) and small[7] == 0
    half = avatars.resize(size, size, back, 1)
    assert half == bytearray((255, 0, 0, 127))   # прозрачные пиксели не подмешивают свой цвет
    with pytest.raises(ValueError):
        avatars.read_png(b"not a png")


@pytest.fixture(scope="module")
def root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()


def test_import_avatar(root, tmp_path):
    source = tk.PhotoImage(master=root, width=300, height=200)
    source.put("#ff0000", to=(0, 0, 150, 200))
    source.put("#0000ff", to=(150, 0, 300, 200))
    source.write(str(tmp_path / "pic.png"), format="png")
    profile = profiles.create("Аня", tmp_path / "profiles")
    avatars.import_avatar(root, tmp_path / "pic.png", profile)
    for size in (avatars.MASTER, 48, 20):
        img = avatars.for_profile(root, profile, size)
        assert (img.width(), img.height()) == (size, size)
        assert img.transparency_get(0, 0)                          # угол — вне круга
        assert img.get(size // 4, size // 2) == (255, 0, 0)       # квадрат вырезан по центру
        assert img.get(size * 3 // 4, size // 2) == (0, 0, 255)
    with pytest.raises(ValueError):
        (tmp_path / "bad.png").write_text("not an image")
        avatars.import_avatar(root, tmp_path / "bad.png", profile)
    avatars.remove_avatar(profile)
    assert profile.avatar is None


def test_seat_avatars(root, tmp_path):
    """Аватарка места: своя (из static, по сети — base64), иначе готовая по виду игрока."""
    profile = profiles.create("Аня", tmp_path)
    avatars.write_png(profile.path / "avatar.png", 4, bytes((10, 20, 30, 255)) * 16)
    static = {"kinds": ["human", "claude", "bot", "human"], "avatars": [profiles.avatar_b64(profile), None, None, "!bad"]}
    avatars.use_game(root, static)
    assert avatars.seat(root, 0, 4).get(1, 1) == (10, 20, 30)
    assert avatars.seat(root, 1, 32) is avatars.builtin(root, "claude", 1, 32)
    assert avatars.seat(root, 2, 32) is avatars.builtin(root, "bot", 2, 32)
    assert avatars.seat(root, 3, 32) is avatars.builtin(root, "human", 3, 32)   # испорченная — силуэт
    avatars.use_game(root, {})   # хозяин прежней версии: ни видов, ни аватарок
    assert avatars.seat(root, 1, 32) is avatars.builtin(root, "human", 1, 32)


def test_new_game_creates_profiles(root, tmp_path, monkeypatch):
    from heroes.gui.dialogs import NewGameDialog

    monkeypatch.chdir(tmp_path)
    anya = profiles.create("Аня")
    dialog = NewGameDialog(root, None, profiles.all_profiles(), profiles.get_active())
    assert dialog.names[0].get() == "Аня"   # активный профиль — на первом месте «Человек»
    dialog.count.set(2)
    dialog.kinds[1].set("Человек (за этим окном)")
    dialog.update_seats()
    dialog.names[1].set("Боря")
    dialog.ok()
    borya = profiles.by_name("Боря")
    assert borya is not None and dialog.result["profiles"] == {0: anya.id, 1: borya.id}
    dialog = NewGameDialog(root, None, profiles.all_profiles(), profiles.get_active())
    dialog.count.set(2)
    dialog.names[0].set("боря")   # регистр не важен: это тот же профиль
    dialog.update_seats()
    dialog.ok()
    assert dialog.result["names"][0] == "Боря" and dialog.result["profiles"] == {0: borya.id}
    assert len(profiles.all_profiles()) == 2


def test_profile_view_shows_stats(root, tmp_path):
    from heroes.gui.app import setup_style
    from heroes.gui.profileview import ProfileView

    setup_style(root)
    game = finished_game(4)
    anya = profiles.create("Аня", tmp_path)
    gost = profiles.create("Гость", tmp_path)
    profiles.record_game(static_data(game), snapshot(game), {0: anya.id, 3: gost.id}, tmp_path)
    view = ProfileView(root, on_back=lambda: None, folder=tmp_path)
    view.pack()
    root.update_idletasks()
    assert view.selected.id == anya.id
    assert set(view.tree.get_children()) == {anya.id, gost.id}
    texts = []

    def collect(w):
        for child in w.winfo_children():
            if isinstance(child, ttk.Label):
                texts.append(str(child.cget("text")))
            collect(child)
    collect(view.detail)
    assert "Аня (вы)" in texts and "Номер хода" in texts and "Claude" in texts
    view.new_profile()
    assert len(view.tree.get_children()) == 3
    view.destroy()


def test_app_records_finished_game(root, tmp_path, monkeypatch):
    from heroes.gui.app import App
    from heroes.gui.links import Link

    monkeypatch.chdir(tmp_path)   # профили и сохранения — в папке теста
    anya = profiles.create("Аня")
    game = finished_game(2)
    app = App(root)
    app.attach(Link())
    app.seat_profiles = {0: anya.id}
    app.handle({"t": "sync", "you": 0, "static": static_data(game), "log": [], "state": snapshot(game)})
    app.handle({"t": "end", "text": "FINAL SCORES\n..."})
    assert len(profiles.get(anya.id).games) == 1
    app.attach(Link())
    app.seat_profiles = {0: anya.id}
    app.handle({"t": "sync", "you": 0, "static": static_data(game), "log": [], "state": snapshot(game)})
    app.handle({"t": "end", "text": "The game was interrupted."})   # прерванная партия не пишется
    assert len(profiles.get(anya.id).games) == 1
    app.link = None
    app.profiles_screen()
    app.screen.destroy()
    app.screen = None


def test_game_screen_shows_avatars(root, tmp_path, monkeypatch):
    """Экран партии с аватарками: журнал, карта, передача хода, ожидание, итоги — без ошибок."""
    from heroes.gui.app import App
    from heroes.gui.links import Link
    from heroes.protocol.snapshot import decision_message, sync_message

    monkeypatch.chdir(tmp_path)
    decisions = []

    class Catch(RandomBot):
        def decide(self, decision, view):
            if not decisions and decision.kind == "destination":
                decisions.append(decision_message(view.game, decision, 1))
            return super().decide(decision, view)

    game = Game([Catch(0), RandomBot(1), RandomBot(2)], names=["Аня", "Claude", "Бот"],
                meta={"players": ["human", "claude", "bot"], "seed": 0})
    game.run()
    app = App(root)
    app.attach(Link())
    app.handle(sync_message(game, 0))
    assert len(app.log.image_names()) > 0                 # аватарки перед именами в журнале
    app.handle(decisions[0])
    monkeypatch.setattr(app.map, "winfo_width", lambda: 1000)   # окно теста скрыто: у холста нет размера
    monkeypatch.setattr(app.map, "winfo_height", lambda: 800)
    app.refresh()
    seat_images = {str(img) for img in root.__dict__["_avatars"].values() if isinstance(img, tk.PhotoImage)}
    tokens = [i for i in app.map.find_withtag("label")
              if app.map.type(i) == "image" and app.map.itemcget(i, "image") in seat_images]
    assert len(tokens) == 3                               # фишки всех трёх игроков — аватарки
    assert int(root.tk.call("image", "width", app.map.itemcget(tokens[0], "image"))) >= 36   # вдвое крупнее прежних 18
    app.pass_screen(decisions[0])
    app.pending = None
    app.handover = None
    app.state = {**app.state, "thinking": [[1, "action_order"]]}
    app.waiting()
    app.handle({"t": "end", "text": "FINAL SCORES"})
    app.link = None
    app.screen.destroy()
    app.screen = None
