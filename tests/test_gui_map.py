"""Карта в окне: картинки и разметка регионов (heroes/gui/assets/map, делает tools/build_map.py)."""
import copy
import time
import tkinter as tk

import pytest

from heroes.engine.data import load_map
from heroes.engine.game import Game
from heroes.gui import mapview
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import snapshot, static_data


def test_geometry_covers_every_region():
    geo = mapview.load_geometry()
    regions = geo["regions"]
    assert set(regions) == set(load_map().regions)
    for width in geo["widths"]:
        for stem in ("base", "objects", "shade"):
            assert (mapview.ASSETS / f"{stem}_{width}.png").exists()
        assert set(geo["shades"][str(width)]["regions"]) == set(regions)
    for name, r in regions.items():
        x0, y0, x1, y1 = r["object"]
        assert mapview.inside(*r["label"], r["polygon"]), name
        assert mapview.inside((x0 + x1) / 2, (y0 + y1) / 2, r["polygon"]), name
        # подпись и объект не попадают ни в какой другой регион
        others = [n for n, o in regions.items() if n != name and mapview.inside(*r["label"], o["polygon"])]
        assert not others, (name, others)


def test_object_disappears_when_taken():
    state = {"fortress_owner": {"Мослин": 0}, "artifact_holder": {"Таскар": 1}}
    assert not mapview.object_shown(state, "Мослин", "fortress")
    assert mapview.object_shown(state, "Ролгой", "fortress")
    assert not mapview.object_shown(state, "Таскар", "artifact")
    assert mapview.object_shown(state, "Мослин", "foundation")


def test_map_view_draws_and_picks_regions():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        root.geometry("900x650")
        game = Game([RandomBot(1), RandomBot(2)])
        game.run()
        picked = []
        view = mapview.MapView(root)
        view.pack(fill="both", expand=True)
        root.update()
        static, state = static_data(game), snapshot(game)
        view.show(static, state, 0, 0)
        view.select_mode({"Карракс": "1 ОП"}, on_select=picked.append, owner=0)
        root.update()
        assert view.width_px in mapview.load_geometry()["widths"]
        x, y = view._xy(*view.regions["Карракс"]["label"])
        assert view.region_at(x, y) == "Карракс"
        view.event_generate("<Motion>", x=int(x), y=int(y))
        assert view.hover == "Карракс" and view.find_withtag("hover")
        view.event_generate("<Button-1>", x=int(x), y=int(y))
        root.update()
        assert picked == ["Карракс"]
        # карта соперника: щелчки не выбирают, обведено, куда может пойти он сам
        state["players"][1]["reach"] = {state["players"][1]["region"]: 0, "Мослин": 2}
        view.show(static, state, 1, 0)
        root.update()
        assert view.shown == {state["players"][1]["region"]: "остаться", "Мослин": "2 ОП"}
        view.event_generate("<Button-1>", x=int(x), y=int(y))
        root.update()
        assert picked == ["Карракс"]
        view.show(static, state, 0, 0)   # своя карта — снова свой выбор
        assert view.shown == {"Карракс": "1 ОП"}
    finally:
        root.destroy()


def test_snapshot_shows_reach_while_choosing_destination():
    seen = []

    class Watcher(RandomBot):
        def decide(self, decision, view):
            if decision.kind == "destination":
                seen.append((decision.player, snapshot(view.game)["players"][decision.player]["reach"],
                             set(decision.data["options"])))
            return super().decide(decision, view)

    game = Game([Watcher(1), Watcher(2)])
    game.run()
    assert seen and all(set(reach) == options for _, reach, options in seen)
    assert all(not pl["reach"] for pl in snapshot(game)["players"])


def test_routes_are_one_arrow_per_move():
    for seed in range(4):
        game = Game([RandomBot(seed * 5 + i) for i in range(2 + seed % 3)])
        game.run()
        for pl in game.state.players:
            moves = [line for line in game.state.log if line.startswith(f"{pl.name} moves to ")]
            assert len(pl.routes) == len(moves)
            assert all(len(r) == 2 and r[0] != r[1] for r in pl.routes)
            # перемещения идут цепочкой и кончаются в текущем регионе
            assert all(prev[1] == nxt[0] for prev, nxt in zip(pl.routes, pl.routes[1:]))
            assert not pl.routes or pl.routes[-1][1] == pl.region


def test_personal_marks():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        root.geometry("900x650")
        game = Game([RandomBot(1), RandomBot(2)])
        game.run()
        view = mapview.MapView(root)
        view.pack(fill="both", expand=True)
        root.update()
        static, state = static_data(game), snapshot(game)
        state["fortress_owner"] = {"Мослин": 1}
        state["players"][0]["foundations"] = {"Асандер": 3}
        state["players"][1]["foundations"] = {"Асандер": 4}
        levels = lambda: {view.itemcget(i, "text") for i in view.find_withtag("level")}
        view.show(static, state, 0)
        root.update()
        assert levels() == {"3"}                      # свой уровень фундамента, чужого не видно
        assert not view.find_withtag("flag")          # чужая крепость просто исчезла
        assert view.find_withtag("route")             # свой маршрут
        view.show(static, state, 1)
        root.update()
        assert levels() == {"4"} and view.find_withtag("flag")   # у захватчика — флажок
    finally:
        root.destroy()


def test_moved_token_slides_to_new_region():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        root.geometry("900x650")
        game = Game([RandomBot(1), RandomBot(2)])
        game.run()
        view = mapview.MapView(root)
        view.pack(fill="both", expand=True)
        root.update()
        static, state = static_data(game), snapshot(game)
        view.show(static, state, 0, 0)
        root.update()
        start = view.spots[("player", 1)]
        moved = copy.deepcopy(state)
        dest = next(r for r in state["available"] if r != state["players"][1]["region"])
        moved["players"][1]["region"] = dest
        view.show(static, moved, 0, 0)
        root.update()
        assert ("player", 1) in view.moving and ("player", 0) not in view.moving
        assert view.find_withtag("moving")
        assert view._spot_at(("player", 1), view.moving[("player", 1)][2]) == start   # едет со старого места
        view.moving[("player", 1)][2] -= mapview.MOVE_SECONDS
        view._step()
        assert not view.moving and not view.find_withtag("moving")   # доехала и стоит на новом месте
        assert view.spots[("player", 1)] != start
        view.show(static_data(game), moved, 0, 0)   # другая партия — не едет
        assert not view.moving
    finally:
        root.destroy()


def test_events_pop_up_over_tokens():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        root.geometry("900x650")
        game = Game([RandomBot(1), RandomBot(2)])
        game.run()
        view = mapview.MapView(root)
        view.pack(fill="both", expand=True)
        root.update()
        static, state = static_data(game), snapshot(game)
        view.show(static, state, 0, 0)
        a, b = static["names"]
        view.event(f"{b} builds Ратуша (level 1)")
        view.event(f"{a} gains wood 3, gold 1 (gather wood)")
        view.event(f"{a} stays in {state['players'][0]['region']}")   # без таблички
        root.update()
        assert [(key, text) for key, text, _ in view.popups] == [
            (("player", 1), "Ратуша"), (("player", 0), f"+3{mapview.MARKS['wood']} +1{mapview.MARKS['gold']}")]
        texts = [i for i in view.find_withtag("popup") if view.type(i) == "text"]
        assert "Ратуша" in [view.itemcget(i, "text") for i in texts]
        assert all(view.bbox(i)[3] < view._xy(*view.spots[("player", 1)])[1] for i in texts
                   if view.itemcget(i, "text") == "Ратуша")   # над фишкой
        assert any(view.type(i) == "image" for i in view.find_withtag("popup"))   # ресурсы — значками
        for pop in view.popups:
            pop[2] = time.monotonic() - 0.65 * mapview.POPUP_SECONDS
        view._step()   # тает: текст растром, значки бледные
        assert {view.itemcget(i, "stipple") for i in view.find_withtag("popup") if view.type(i) == "text"} == {"gray75"}
        for pop in view.popups:
            pop[2] -= mapview.POPUP_SECONDS
        view._step()
        assert not view.popups and not view.find_withtag("popup")   # растаяли
        # событие там, куда фишка едет, — табличка, когда доедет
        moved = copy.deepcopy(state)
        moved["players"][1]["region"] = next(r for r in state["available"] if r != state["players"][1]["region"])
        view.event(f"{b} gains iron 1 (first visit to {moved['players'][1]['region']})")
        view.show(static, moved, 0, 0)
        (_, _, start), = view.popups
        assert start == view.moving[("player", 1)][2] + mapview.MOVE_SECONDS
        assert not view.find_withtag("popup")
    finally:
        root.destroy()


def test_popup_stack():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        root.geometry("900x650")
        game = Game([RandomBot(1), RandomBot(2)])
        game.run()
        view = mapview.MapView(root)
        view.pack(fill="both", expand=True)
        root.update()
        static, state = static_data(game), snapshot(game)
        view.show(static, state, 0, 0)
        a = static["names"][0]
        for k in range(6):
            view.event(f"{a} builds Здание {k} (level 1)")
        starts = [start for _, _, start in view.popups]
        assert all(b - a == pytest.approx(mapview.POPUP_GAP) for a, b in zip(starts, starts[1:]))   # друг за другом
        now = time.monotonic()
        for k, pop in enumerate(view.popups):   # все уже всплыли
            pop[2] = now - 1.2 + k * 0.1

        def drawn():
            view._animate()
            texts = [i for i in view.find_withtag("popup") if view.type(i) == "text"]
            return {view.itemcget(i, "text"): view.bbox(i)[1] for i in texts}

        tops = drawn()
        assert sorted(tops, key=tops.get) == [f"Здание {k}" for k in (2, 3, 4, 5)]   # 4 новых, старшие — выше
        y = view._xy(*view.spots[("player", 0)])[1]
        assert max(tops.values()) < y
        view.spots[("player", 0)] = (view.spots[("player", 0)][0], 20)   # у верхнего края — стопка под фишкой
        tops = drawn()
        assert min(tops.values()) > view._xy(0, 20)[1]
        assert sorted(tops, key=tops.get) == [f"Здание {k}" for k in (5, 4, 3, 2)]
    finally:
        root.destroy()
