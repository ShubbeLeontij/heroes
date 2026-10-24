"""Веб-клиент в настоящем браузере (headless Chromium): формы, заполненные наугад, дают ответы, которые
движок принимает, — как test_gui_panels.py для окна. Нет Chromium — тесты пропускаются."""
import contextlib
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest

from heroes import cli
from heroes.engine import scoring
from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot
from heroes.protocol import render_text as R

from browser import Browser

GAME_SECONDS = 120   # партия против ботов в браузере идёт секунды; дольше — страница зависла
FACTIONS = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]
SPECIAL = {"undead": {"curse_region", "buy_move_points", "exchange_on_gain"}, "dwarves": {"step:science"},
           "orcs": {"step:upgrade", "step:raise", "raise_action"}, "elves": {"upgrade"},
           "nagas": {"circle_note", "summon_hydra", "hydra_destination", "make_lake"}}


@pytest.fixture(scope="module")
def browser():
    exe = Browser.find()
    if exe is None:
        pytest.skip("no Chromium")
    b = Browser(exe)
    b.screen(390, 844)   # телефон вертикально: формы должны работать и в тесной раскладке
    yield b
    b.close()


def play(browser: Browser, seed: int, factions: list[str] | None, opponents: int = 2, drops: tuple[int, ...] = ()) -> dict:
    """Партия: место 0 — страница в тестовом режиме, остальные — боты. Возвращает window.testLog.
    drops — после стольких ответов оборвать соединение страницы (она должна подключиться сама)."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    names = ["Гость", *[f"Бот{i}" for i in range(opponents)]]
    meta = {"players": ["remote", *["bot"] * opponents], "seed": seed}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta, factions=factions)
    server = network.server
    finished = threading.Event()
    failure = []

    def host():
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                assert cli.open_table(game, server, SimpleNamespace(password="secret"), out=lambda text: None)
                scores = game.run()
                server.broadcast_end("\n" + R.final_scores(game, scores, scoring.winners(game, scores)))   # как cli.main
        except BaseException as e:   # noqa: BLE001 — ошибку движка покажет тест
            failure.append(e)
        finally:
            finished.set()

    server.start()
    try:
        threading.Thread(target=host, daemon=True).start()
        browser.errors.clear()
        browser.open(f"http://127.0.0.1:{server.port}/?name=Гость&password=secret&test=random&seed={seed}")
        deadline = time.monotonic() + GAME_SECONDS
        while not finished.wait(0.5):
            assert time.monotonic() < deadline, browser.evaluate(
                "JSON.stringify({log: window.testLog, pending: app.pending && app.pending.kind, round: app.state && app.state.round})")
            log = browser.evaluate("JSON.stringify(window.testLog || null)")
            log = json.loads(log) if log else None
            if log and drops and log["answers"] >= drops[0]:
                browser.evaluate("app.link.ws && app.link.ws.close()")
                drops = drops[1:]
            assert not browser.errors, browser.errors
            assert not (log and log["stuck"]), log["stuck"]
            assert not (log and log["errors"]), log["errors"]
        assert not failure, failure
        assert game.state.round == 16
        log = json.loads(browser.evaluate("JSON.stringify(window.testLog)"))
        assert browser.wait_for("document.querySelector('.finished') !== null", timeout=10)
    finally:
        server.close()
    assert not browser.errors, browser.errors
    assert log["errors"] == [] and log["stuck"] == [], log
    return log


@pytest.mark.parametrize("seed", range(3))
def test_random_forms_give_valid_answers(browser, seed):
    log = play(browser, seed, None, opponents=1 + seed % 3)
    assert {"action_order", "build_recruit", "destination"} <= set(log["kinds"])


def test_page_reconnects_by_itself(browser):
    """Связь рвётся посреди партии (телефон уснул, сменилась сеть): страница подключается снова,
    получает партию и тот же вопрос, партия доигрывается."""
    log = play(browser, 9, None, drops=(5, 20, 40))
    assert log["answers"] > 40


@pytest.mark.parametrize("faction", FACTIONS[1:])
def test_faction_forms_give_valid_answers(browser, faction):
    """Формы особых решений фракций: гость за этой фракцией, соперники — боты за остальными."""
    others = [f for f in FACTIONS if f != faction]
    kinds = set()
    for seed in range(20):   # до тех пор, пока не встретятся все особые решения фракции (как в тесте окна)
        log = play(browser, seed, [faction, *others[seed % 4:seed % 4 + 2]])
        kinds |= set(log["kinds"])
        if SPECIAL[faction] <= kinds:
            break
    assert SPECIAL[faction] <= kinds, SPECIAL[faction] - kinds


def test_army_strength_matches_python(browser):
    """armyStrength, cheapestArmy и bestStrength на JS считают так же, как common.py и panels.py окна."""
    import random
    from heroes.gui.common import army_strength
    from heroes.gui.panels import best_strength, cheapest_army
    rng = random.Random(5)
    cases = []
    for _ in range(60):
        units = {u: {"count": rng.randint(0, 3), "strength": rng.randint(1, 6)} for u in ("a", "b", "c")}
        rules = rng.choice([None, {"bonus": 2}, {"count_units": ["b"]}, {"doublers": {"c": 1}}])
        upgrade = rng.choice([None, {"cost_per_unit": {"wood": 1}, "strength": {"a": 5, "b": 7}, "max": rng.randint(0, 3)}])
        apart = rng.choice([None, "a"])
        need = rng.randint(0, 20)
        army = {u: rng.randint(0, v["count"]) for u, v in units.items()}
        cases.append({"units": units, "rules": rules, "upgrade": upgrade, "apart": apart, "need": need, "army": army,
                      "strength": army_strength(units, army, rules),
                      "cheapest": cheapest_army(units, need, rules, apart, upgrade),
                      "best": best_strength(units, rules, apart, upgrade)})
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        got = json.loads(browser.evaluate(f"""(async () => {{
          const C = await import("./static/js/common.js"), P = await import("./static/js/panels.js");
        return JSON.stringify({json.dumps(cases)}.map((c) => ({{
          strength: C.armyStrength(c.units, c.army, c.rules),
          cheapest: P.cheapestArmy(c.units, c.need, c.rules, c.apart, c.upgrade),
          best: P.bestStrength(c.units, c.rules, c.apart, c.upgrade)}})));
      }})()"""))
    finally:
        network.server.close()
    for case, js in zip(cases, got):
        expected = list(case["cheapest"]) if case["cheapest"] else None
        assert js == {"strength": case["strength"], "cheapest": expected, "best": case["best"]}, case


class Stop(Exception):
    pass


class DestinationRecorder(RandomBot):
    """Бот, который на первом решении о перемещении запоминает, что увидела бы страница, и останавливает партию."""

    def decide(self, decision, view):
        if decision.kind == "destination" and len(decision.data["options"]) > 2:
            from heroes.protocol.snapshot import decision_message, sync_message
            self.sync = sync_message(view.game, 0)
            self.decision = decision_message(view.game, decision, 1)
            raise Stop
        return super().decide(decision, view)


def test_map_gestures(browser):
    """Карта на телефоне: тап по региону выбирает его в форме, два пальца и двойной тап масштабируют,
    сдвиг пальцем двигает увеличенную карту."""
    recorder = DestinationRecorder(1)
    with pytest.raises(Stop):
        Game([recorder, RandomBot(2)]).run()
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.screen(390, 844)
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.evaluate(f"app.handle({json.dumps(recorder.sync)}); app.handle({json.dumps(recorder.decision)}); 1")
        browser.wait_for("app.map.geometry && app.panel && app.panel.options")
        options = recorder.decision["data"]["options"]
        here = recorder.decision["state"]["players"][0]["region"]
        target = next(r for r in sorted(options) if r != here)
        where = f"""(() => {{ const m = app.map, r = m.container.getBoundingClientRect();
            const [x, y] = m.regions[{json.dumps(target)}].center;
            return [r.left + m.tx + x * m.k, r.top + m.ty + y * m.k]; }})()"""

        def touch(kind, *points):
            browser.call("Input.dispatchTouchEvent", type=kind, touchPoints=[{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(points)])

        x, y = browser.evaluate(where)
        touch("touchStart", (x, y))
        touch("touchEnd")
        assert browser.wait_for(f"app.panel.choice === {json.dumps(target)}", timeout=3)
        assert browser.evaluate("app.map.selected") == target
        # два пальца врозь — крупнее
        rect = browser.evaluate("(() => { const r = app.map.container.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
        cx, cy = rect
        touch("touchStart", (cx - 30, cy), (cx + 30, cy))
        for step in range(1, 6):
            touch("touchMove", (cx - 30 - step * 20, cy), (cx + 30 + step * 20, cy))
        touch("touchEnd")
        zoom = browser.evaluate("app.map.zoom")
        assert zoom > 2, zoom
        # сдвиг одним пальцем — карта едет
        tx = browser.evaluate("app.map.tx")
        touch("touchStart", (cx, cy))
        for step in range(1, 5):
            touch("touchMove", (cx + step * 15, cy))
        touch("touchEnd")
        assert browser.evaluate("app.map.tx") != tx
        assert browser.evaluate("app.panel.choice") == target   # сдвиг — не тап: выбор прежний
        # двойной тап — снова вся карта
        touch("touchStart", (cx, cy))
        touch("touchEnd")
        touch("touchStart", (cx, cy))
        touch("touchEnd")
        assert browser.wait_for("app.map.zoom === 1", timeout=3)
        assert not browser.errors, browser.errors
    finally:
        network.server.close()
