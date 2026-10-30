"""Веб-клиент в настоящем браузере (headless Chromium): формы, заполненные наугад, дают ответы, которые
движок принимает; зритель; профили; расчёты страницы. Нет Chromium — тесты пропускаются."""
import contextlib
import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from heroes import cli, profiles
from heroes.engine import scoring
from heroes.engine.game import Game
from heroes.players.random_bot import RandomBot
from heroes.protocol import render_text as R
from heroes.web.marks import MARKS as M

from browser import Browser

GAME_SECONDS = 120   # партия против ботов в браузере идёт секунды; дольше — страница зависла
FACTIONS = ["humans", "undead", "dwarves", "orcs", "elves", "nagas"]
SPECIAL = {"undead": {"curse_region", "destination:extra", "exchange_on_gain"}, "dwarves": {"step:science"},
           "orcs": {"step:upgrade", "step:raise", "destination:extra"}, "elves": {"upgrade"},
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
    meta = {"players": ["human", *["bot"] * opponents], "seed": seed}
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
    for seed in range(20):   # до тех пор, пока не встретятся все особые решения фракции
        log = play(browser, seed, [faction, *others[seed % 4:seed % 4 + 2]])
        kinds |= set(log["kinds"])
        if SPECIAL[faction] <= kinds:
            break
    assert SPECIAL[faction] <= kinds, SPECIAL[faction] - kinds


def test_army_strength_matches_python(browser):
    """armyStrength, cheapestArmy и bestStrength на JS считают так же, как считало окно на Python
    (tests/data/army_cases.json — его ответы на случайные заявки)."""
    cases = json.loads((Path(__file__).parent / "data" / "army_cases.json").read_text(encoding="utf-8"))
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
        assert js == {"strength": case["strength"], "cheapest": case["cheapest"], "best": case["best"]}, case


SUMMARY_STATIC = {"names": ["Аня", "Боря", "Вера"], "player_factions": ["humans", "humans", "nagas"],
                  "factions": {"humans": {"name": "Люди"},
                               "nagas": {"name": "Наги", "artifact_conditions": {"Цурт": {"condition": "pay", "cost": {"krak": 3}}}},
                               "elves": {"name": "Эльфы", "units": {"griffin": {"name": "Грифон"}},
                                         "artifact_wood": {"building": "aerie", "unit": "griffin",
                                                           "prices": {"Таскар": 5, "Цурт": 4, "Содмур": 3}}}},
                  "regions": {"Цурт": {"terrain": "mountain", "artifact": {"condition": "pay", "cost": {"iron": 12}}},
                              "Таскар": {"terrain": "field", "artifact": {"condition": "pay", "cost": {"wood": 12}}},
                              "Шог Ид": {"terrain": "mountain", "artifact": {"condition": "mountainsVisited", "count": 4}},
                              "Содмур": {"terrain": "field", "artifact": {"condition": "battleWins", "count": 5}}},
                  "rounds": 16}


def summary_player(**kw):
    pl = {"resources": {"wood": 0, "iron": 0, "gold": 0}, "foundations": {}, "victories": 0, "visited": [],
          "faction": "humans", "built": [], "units": {}}
    pl.update(kw)
    return pl


def test_summary_helpers(browser):
    """Сводка «Все игроки» и строка очерёдности: условия артефактов (у наг — свои, у эльфов — и за дерево),
    имя с фракцией, кто первый в следующих раундах."""
    state = {"players": [summary_player(resources={"wood": 0, "iron": 13, "gold": 0}, visited=["Цурт", "Таскар"]),
                         summary_player(victories=5),
                         summary_player(faction="nagas", resources={"wood": 0, "iron": 13, "gold": 0},
                                        holdings={"wood": 0, "iron": 13, "gold": 0, "krak": 2}, mountains=["Цурт", "Шог Ид"])],
             "artifact_holder": {}}
    held = {**state, "artifact_holder": {"Содмур": 1}}
    elf = summary_player(faction="elves", resources={"wood": 7, "iron": 1, "gold": 0}, units={"griffin": {"count": 3}})
    elves = {"players": [elf], "artifact_holder": {}}
    elves_aerie = {"players": [{**elf, "built": ["aerie"]}], "artifact_holder": {}}
    checks = [  # [выражение JS над S (static) и состояниями, ожидаемое]
        ["B.conditionProgress(S, st, 0, 'Цурт')", f"13/12{M['iron']} ✓"],
        ["B.conditionProgress(S, st, 0, 'Шог Ид')", f"1/4{M['mountain']}"],
        ["B.conditionProgress(S, st, 1, 'Содмур')", f"5/5{M['victory']} ✓"],
        ["B.conditionProgress(S, held, 1, 'Содмур')", f"{M['artifact_Содмур']} взят"],
        ["B.conditionProgress(S, held, 0, 'Содмур')", f"0/5{M['victory']}"],
        ["B.conditionProgress(S, st, 2, 'Цурт')", f"2/3{M['krak']}"],
        ["B.conditionProgress(S, st, 2, 'Шог Ид')", f"2/4{M['mountain']}"],
        ["B.conditionProgress(S, elves, 0, 'Таскар')", f"7/12{M['wood']} или 7/5{M['wood']}, грифоны 3/5 (нет Гнездовья)"],
        ["B.conditionProgress(S, elvesAerie, 0, 'Таскар')", f"7/12{M['wood']} или 7/5{M['wood']}, грифоны 3/5"],
        ["B.conditionProgress(S, elvesAerie, 0, 'Цурт')", f"1/12{M['iron']} или 7/4{M['wood']}, грифоны 3/4"],
        ["B.conditionProgress(S, elvesAerie, 0, 'Содмур')", f"0/5{M['victory']} или 7/3{M['wood']}, грифоны 3/3 ✓"],
        ["C.nameOf(S, 1)", "Боря (Люди)"],
        ["C.turnOrder(S, {round: 0, first_player: 1})", [0, 2, 1]],   # старт: справа от первого, против часовой
        ["C.nextFirstPlayers(S, {round: 0, first_player: 1}, 3)", [[1, 1], [2, 2], [3, 0]]],
        ["C.turnOrder(S, {round: 5, first_player: 2})", [2, 0, 1]],
        ["C.nextFirstPlayers(S, {round: 5, first_player: 2}, 3)", [[6, 0], [7, 1], [8, 2]]],   # первым — сосед слева
        ["C.nextFirstPlayers(S, {round: 15, first_player: 0}, 3)", [[16, 1]]],
        ["C.nextFirstPlayers(S, {round: 16, first_player: 0}, 3)", []],
    ]
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app")
        got = json.loads(browser.evaluate(f"""(async () => {{
          const C = await import("./static/js/common.js"), B = await import("./static/js/board.js");
          const S = {json.dumps(SUMMARY_STATIC)}, st = {json.dumps(state)}, held = {json.dumps(held)},
                elves = {json.dumps(elves)}, elvesAerie = {json.dumps(elves_aerie)};
          return JSON.stringify([{", ".join(expr for expr, _ in checks)}]);
        }})()"""))
    finally:
        network.server.close()
    for (expr, expected), value in zip(checks, got):
        assert value == expected, expr


def test_object_look(browser):
    """Забранный артефакт с карты исчезает; захваченная крепость и чужая бронь остаются полупрозрачными."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app")
        got = json.loads(browser.evaluate("""(async () => {
          const { objectLook } = await import("./static/js/map.js");
          const st = { fortress_owner: { Ф: 0 }, artifact_holder: { А: 1 }, reserved: { Б: 2 } };
          return JSON.stringify([objectLook(st, "А", "artifact", 0), objectLook(st, "Ф", "fortress", 0),
            objectLook(st, "Б", "artifact", 0), objectLook(st, "Б", "artifact", 2), objectLook(st, "Я", "artifact", 0)]);
        })()"""))
    finally:
        network.server.close()
    assert got == [None, "faded", "faded", "shown", "shown"]


def test_other_player_map_not_washed_outside_movement(browser):
    """Карта другого игрока вне фазы перемещения (нет досягаемости) не светлеет; в фазу перемещения — обводка досягаемости."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app")
        got = json.loads(browser.evaluate("""(async () => {
          const { MapView } = await import("./static/js/map.js");
          const shown = (reach) => MapView.prototype.shownOptions.call({ you: 1, viewer: 0, options: null, preview: null,
            picking: () => false, state: { players: [{}, { reach }] } });
          return JSON.stringify([shown({}), shown({ Золен: 0, Цурт: 3 })]);
        })()"""))
    finally:
        network.server.close()
    assert got == [None, {"Золен": "остаться", "Цурт": "3" + M["mp"]}]


def test_spectator_watches_in_browser(browser):
    """«Смотреть партию»: место за столом не занято, страница показывает партию ботов до конца."""
    network = cli.Network(0, None, log_fn=lambda text: None)
    names = ["Бот1", "Бот2"]
    meta = {"players": ["bot", "bot"], "seed": 4}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta)
    server = network.open()
    go = threading.Event()

    class Slow(RandomBot):   # партия ждёт, пока зритель подключится
        def decide(self, decision, view):
            go.wait(20)
            return super().decide(decision, view)

    game.players = [Slow(1), Slow(2)]
    assert cli.open_table(game, server, SimpleNamespace(password=None), out=lambda text: None)
    host = threading.Thread(target=lambda: (game.run(), server.broadcast_end("game over")), daemon=True)
    try:
        host.start()
        browser.errors.clear()
        browser.open(f"http://127.0.0.1:{server.port}/")
        browser.wait_for("window.app !== undefined")   # кнопки оживают, когда загрузится скрипт страницы
        browser.evaluate("document.getElementById('join-watch').click(); 1")
        assert browser.wait_for("app.static && app.viewer === null && document.body.dataset.screen === 'game'")
        go.set()
        host.join(60)
        assert browser.wait_for("document.querySelector('.finished') !== null", timeout=20)
        assert browser.evaluate("document.title") == "«Герои» — зритель"
        assert browser.evaluate("app.pending") is None
        assert not browser.errors, browser.errors
    finally:
        go.set()
        server.close()


def test_join_offers_seats_without_name(browser):
    """Подключение без имени: поля имени нет, хозяин сразу предлагает свободные места."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Аня")
    network.seat("Боря")
    network.server.start()
    try:
        browser.errors.clear()
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app !== undefined")
        assert browser.evaluate("document.getElementById('join-name').type") == "hidden"
        browser.evaluate("document.getElementById('join-password').value = 'secret'; document.getElementById('join-submit').click(); 1")
        assert browser.wait_for("[...document.querySelectorAll('#join-seats .seat')].map((b) => b.textContent).join()") == "Аня,Боря"
        assert not browser.errors, browser.errors
    finally:
        network.server.close()


def test_join_open_session_lists_seats_at_once(browser):
    """Партия без пароля: ни поля пароля, ни кнопки; свободные места сразу, даже если оно одно."""
    network = cli.Network(0, None, log_fn=lambda text: None)
    network.seat("Аня")
    network.server.start()
    try:
        browser.errors.clear()
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app !== undefined")
        assert browser.wait_for("[...document.querySelectorAll('#join-seats .seat')].map((b) => b.textContent).join()") == "Аня"
        assert browser.evaluate("document.getElementById('join-password').offsetParent === null")
        assert browser.evaluate("document.getElementById('join-submit').offsetParent === null")
        assert browser.evaluate("document.body.dataset.screen") == "join"
        assert not browser.errors, browser.errors
    finally:
        network.server.close()


def test_profiles_screen(browser, tmp_path, monkeypatch):
    """«Профили и статистика»: список с партиями, переименование, «Играть под этим профилем», удаление."""
    monkeypatch.chdir(tmp_path)
    game = Game([RandomBot(1), RandomBot(2)], names=["Аня", "Бот"], meta={"players": ["human", "bot"], "seed": 1})
    game.run()
    cli.record_profiles(game, out=lambda text: None)
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.errors.clear()
        browser.open(f"http://127.0.0.1:{network.server.port}/#profiles")
        browser.wait_for("window.app !== undefined")
        # без пароля партии хозяин профили не показывает
        assert "пароль" in browser.wait_for("document.querySelector('#profiles .bad')?.textContent")
        browser.evaluate("document.getElementById('join-password').value = 'secret'; app.profilesScreen(); 1")
        assert browser.wait_for("document.querySelector('.profile-row')?.textContent").startswith("Аня")
        browser.evaluate("document.querySelector('.profile-row').click(); 1")
        assert browser.wait_for("document.querySelectorAll('.game-card').length") == 1
        assert "(вы)" in browser.evaluate("document.querySelector('.game-card tr.you').textContent")
        browser.evaluate("""(() => { const i = document.querySelector('.profile-edit input'); i.value = 'Анна';
            i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); return 1; })()""")
        assert browser.wait_for("app.profiles.list[0]?.name === 'Анна'")
        assert profiles.by_name("Анна") is not None
        browser.evaluate("[...document.querySelectorAll('#profiles button')].find((b) => b.textContent.startsWith('Играть')).click(); 1")
        assert browser.wait_for("document.body.dataset.screen === 'join' && document.getElementById('join-name').value") == "Анна"
        browser.evaluate("app.profilesScreen(); 1")
        browser.wait_for("document.querySelector('.profile-row') !== null")
        browser.evaluate("window.confirm = () => true; document.querySelector('.profile-row').click(); 1")
        browser.wait_for("document.querySelector('.game-card') !== null")
        browser.evaluate("[...document.querySelectorAll('#profiles button')].find((b) => b.textContent === 'Удалить профиль').click(); 1")
        assert browser.wait_for("document.querySelector('#profiles .card p:not(.muted)')?.textContent") == "Профилей пока нет."
        assert profiles.all_profiles() == []
        assert not browser.errors, browser.errors
    finally:
        network.server.close()


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


def test_map_popup_icons(browser):
    """Табличка над фишкой со значком («+1» и наука): элемент не пересоздаётся каждый кадр, значок успевает загрузиться
    (на телефоне новый <img> на каждом кадре не показывался — оставалось одно «+1»)."""
    recorder = DestinationRecorder(1)
    with pytest.raises(Stop):
        Game([recorder, RandomBot(2)]).run()
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    try:
        browser.screen(390, 844)
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app")
        browser.evaluate(f"app.handle({json.dumps(recorder.sync)}); 1")
        browser.wait_for("app.map.geometry && app.map.spots.has('player:0')")
        browser.evaluate(f"app.map.event(['player', 0, {json.dumps('+1' + M['science'])}]); 1")
        browser.wait_for("!!document.querySelector('.maptext.popup img.ic')", timeout=3)
        got = browser.evaluate("""(async () => {
          const img = () => document.querySelector(".maptext.popup img.ic");
          const first = img();
          for (let i = 0; i < 10; i++) await new Promise(requestAnimationFrame);
          return [img() === first, first.complete && first.naturalWidth > 0, first.parentElement.textContent];
        })()""")
        assert got == [True, True, "+1"]
        assert not browser.errors, browser.errors
    finally:
        network.server.close()


def test_splitters(browser):
    """Широкий экран: разделительные черты тянутся мышью (не дальше наименьших размеров частей), положение
    запоминается и после перезагрузки, двойной щелчок возвращает как было. На телефоне черт нет."""
    recorder = DestinationRecorder(1)
    with pytest.raises(Stop):
        Game([recorder, RandomBot(2)]).run()
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    network.server.start()
    url = f"http://127.0.0.1:{network.server.port}/"
    rect = """(() => {{ const r = document.getElementById({!r}).getBoundingClientRect();
        return [r.left, r.top, r.width, r.height]; }})()""".format
    show = f"app.handle({json.dumps(recorder.sync)}); 1"

    def mouse(kind, x, y, clicks=1):
        browser.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", buttons=0 if kind == "mouseReleased" else 1,
                     clickCount=clicks)

    def drag(splitter, dx, dy):
        x, y, w, hh = browser.evaluate(rect(splitter))
        x, y = x + w / 2, y + hh / 2
        mouse("mousePressed", x, y)
        for step in range(1, 6):
            mouse("mouseMoved", x + dx * step / 5, y + dy * step / 5)
        mouse("mouseReleased", x + dx, y + dy)

    try:
        browser.screen(1400, 900, mobile=False)
        browser.open(url)
        browser.wait_for("window.app")
        browser.evaluate("localStorage.removeItem('heroes.split.x'); localStorage.removeItem('heroes.split.y'); 1")
        browser.evaluate(show)
        browser.wait_for("app.map.geometry")
        _, _, map_w, map_h = browser.evaluate(rect("map-pane"))
        _, _, _, decision_h = browser.evaluate(rect("decision-pane"))
        drag("split-x", -200, 0)   # карта уже
        drag("split-y", 0, -100)   # решение выше
        _, _, w, hh = browser.evaluate(rect("map-pane"))
        assert abs(w - (map_w - 200)) < 3 and abs(hh - (map_h - 100)) < 3, (w, hh)
        assert abs(browser.evaluate(rect("decision-pane"))[3] - (decision_h + 100)) < 3
        x, _, split_w, _ = browser.evaluate(rect("split-x"))
        assert abs(x + split_w / 2 - (browser.evaluate(rect("map-pane"))[0] + w)) < 2   # полоса — на черте
        # перезагрузка — черты там же
        browser.open(url)
        browser.wait_for("window.app")
        browser.evaluate(show)
        assert abs(browser.evaluate(rect("map-pane"))[2] - w) < 3
        # не дальше наименьших размеров: планшетам остаётся 360px
        drag("split-x", 2000, 0)
        assert abs(browser.evaluate(rect("boards-pane"))[2] - 360) < 3
        # двойной щелчок — как было
        for splitter in ("split-x", "split-y"):
            x, y, sw, sh = browser.evaluate(rect(splitter))
            mouse("mousePressed", x + sw / 2, y + sh / 2)
            mouse("mouseReleased", x + sw / 2, y + sh / 2)
            mouse("mousePressed", x + sw / 2, y + sh / 2, 2)
            mouse("mouseReleased", x + sw / 2, y + sh / 2, 2)
        _, _, w, hh = browser.evaluate(rect("map-pane"))
        assert abs(w - map_w) < 3 and abs(hh - map_h) < 3, (w, hh)
        assert browser.evaluate("localStorage.getItem('heroes.split.x') === null && localStorage.getItem('heroes.split.y') === null")
        browser.screen(390, 844)
        assert browser.evaluate("getComputedStyle(document.getElementById('split-x')).display") == "none"
        assert not browser.errors, browser.errors
    finally:
        network.server.close()
        browser.screen(390, 844)


def test_tips(browser):
    """Подсказки: наведение мышью на артефакт, чудовище и склад планшета — полная информация по всем игрокам;
    на телефоне — по тапу (и по рисунку артефакта на карте); тап мимо — подсказка закрывается."""
    recorder = DestinationRecorder(1)
    with pytest.raises(Stop):
        Game([recorder, RandomBot(2)], names=["Аня", "Боря"]).run()
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Аня")
    network.server.start()
    tip = "(() => { const t = document.querySelector('.tip'); return t && !t.classList.contains('hidden') ? t.textContent : null; })()"
    center = """((sel) => { const r = document.querySelector(sel).getBoundingClientRect();
        return [r.left + r.width / 2, r.top + r.height / 2]; })"""

    def hover(selector):
        browser.evaluate(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'center'}}); 1")
        time.sleep(0.2)   # прокрутка закрывает подсказку — пусть закончится до наведения
        x, y = browser.evaluate(f"{center}({json.dumps(selector)})")
        browser.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        return browser.evaluate(tip)

    try:
        # телефон: тап по рисунку артефакта на карте — подсказка, тап мимо — закрыть
        browser.screen(390, 844)
        browser.open(f"http://127.0.0.1:{network.server.port}/")
        browser.wait_for("window.app")
        browser.evaluate(f"app.handle({json.dumps(recorder.sync)}); app.handle({json.dumps(recorder.decision)}); 1")
        browser.wait_for("app.map.geometry")
        browser.evaluate("app.setView('decision'); 1")   # карта на телефоне видна всегда
        browser.wait_for("app.map.k > 0")
        time.sleep(0.5)   # карта подстраивается под размер панели
        # тап по рисунку артефакта: касание (pointerdown), тап карты, следом щелчок браузера
        # (настоящие касания эмулятора после других тестов приходят без pointer-событий — шлём их сами)
        tap_map = """(() => { const m = app.map;
            const name = Object.keys(m.regions).find((n) => app.static.regions[n].object === "artifact" && app.state.available.includes(n));
            const [x0, y0, x1, y1] = m.regions[name].object.map((v) => v * m.scaleFactor);
            document.body.dispatchEvent(new PointerEvent("pointerdown", { pointerType: "touch", bubbles: true }));
            m.tap([m.tx + (x0 + x1) / 2 * m.k, m.ty + (y0 + y1) / 2 * m.k]);
            m.container.dispatchEvent(new MouseEvent("click", { bubbles: true }));
            return name; })()"""
        name = browser.evaluate(tap_map)
        assert name in (browser.evaluate(tip) or ""), browser.evaluate(tip)
        browser.evaluate("document.body.dispatchEvent(new PointerEvent('pointerdown', { pointerType: 'touch', bubbles: true }));"
                         "document.getElementById('order').click(); 1")   # тап мимо — закрыть
        assert browser.evaluate(tip) is None
        # захваченная крепость и чужая бронь — полупрозрачные, подсказка — почему недоступны; взятого артефакта нет
        blocked = """(() => { const m = app.map, avail = app.state.available;
            const find = (kind) => Object.keys(m.regions).find((n) => app.static.regions[n].object === kind && avail.includes(n));
            const fort = find("fortress"), found = find("foundation"), art = find("artifact");
            for (const st of new Set([app.state, m.state])) {
              st.fortress_owner = { ...st.fortress_owner, [fort]: 1 };
              st.reserved = { ...st.reserved, [found]: 1 };
              st.artifact_holder = { ...st.artifact_holder, [art]: 1 };
            }
            m.redraw();
            const at = (n) => { const [x0, y0, x1, y1] = m.regions[n].object.map((v) => v * m.scaleFactor);
              return [m.tx + (x0 + x1) / 2 * m.k, m.ty + (y0 + y1) / 2 * m.k]; };
            const faded = document.querySelectorAll("#objects-faded rect").length;
            m.objectTip(at(fort));
            const fortTip = document.querySelector(".tip").textContent;
            m.objectTip(at(found));
            const foundTip = document.querySelector(".tip").textContent;
            return [faded, fortTip, foundTip, m.tipAt(at(art))]; })()"""
        faded, fort_tip, found_tip, art_tip = browser.evaluate(blocked)
        assert faded == 2
        assert art_tip is None   # взятого артефакта на карте нет
        # наведение на аватарку — карта глазами этого игрока, уход курсора возвращает свою
        glance = browser.evaluate("""(() => { const m = app.map, own = m.you, other = own === 1 ? 0 : 1;
            m.setHover(null, `player:${other}`); const during = m.you === other && m.own === own;
            m.setHover(null, null); return [during, m.you === own]; })()""")
        assert glance == [True, True], glance
        assert "✗ захвачена: Боря" in fort_tip, fort_tip
        assert "✗ бронь: Боря (Башня иллюзий)" in found_tip, found_tip
        # своя бронь: объект не полупрозрачный (он доступен), значка нет, но подсказка говорит, чья бронь
        mine = browser.evaluate("""(() => { const m = app.map, avail = app.state.available;
            const found = Object.keys(m.regions).find((n) => app.static.regions[n].object === "foundation" && avail.includes(n));
            for (const st of new Set([app.state, m.state])) Object.assign(st, { reserved: { [found]: m.you }, fortress_owner: {}, artifact_holder: {} });
            m.redraw();
            const [x0, y0, x1, y1] = m.regions[found].object.map((v) => v * m.scaleFactor);
            const tip = m.tipAt([m.tx + (x0 + x1) / 2 * m.k, m.ty + (y0 + y1) / 2 * m.k]);
            return [tip, document.querySelectorAll("#objects-faded rect").length,
                    [...m.svg.querySelectorAll("text")].some((t) => t.textContent === "Б")]; })()""")
        assert mine[0].startswith("object|") and mine[1] == 0 and mine[2] is False, mine
        browser.evaluate("document.body.dispatchEvent(new PointerEvent('pointerdown', { pointerType: 'touch', bubbles: true }));"
                         "document.getElementById('order').click(); 1")
        touch_click = "document.body.dispatchEvent(new PointerEvent('pointerdown', { pointerType: 'touch', bubbles: true }));" \
                      "document.querySelector('#board .card.monster').click(); 1"
        browser.evaluate("app.setView('boards'); 1")
        browser.evaluate(touch_click)
        assert "Чудовище 1 из 8" in (browser.evaluate(tip) or "")
        browser.evaluate(touch_click)   # повторный тап — убрать
        assert browser.evaluate(tip) is None

        # ПК: наведение мышью на карточки планшета
        browser.screen(1400, 900, mobile=False)
        browser.wait_for("!!document.querySelector('#board .card.artifact')")
        region = browser.evaluate("document.querySelector('#board .card.artifact').dataset.tip.split('|')[1]")
        text = hover("#board .card.artifact")
        assert region in text and "Аня" in text and "Боря" in text and "свободен" in text, text
        text = hover("#board .card.monster")
        assert "Чудовище 1 из 8" in text and "не побеждено" in text, text
        assert browser.evaluate("document.querySelectorAll('.tip .tip-table tbody tr').length") == 0
        hover("#board .stock-cell")
        assert browser.evaluate("document.querySelectorAll('.tip .tip-table tbody tr').length") == 2
        assert browser.evaluate("document.querySelector('.tip .tip-table th.now img').className").startswith("ic ic-wood")
        hover("#order")
        assert browser.evaluate(tip) is None   # мышь ушла — подсказки нет
        assert not browser.errors, browser.errors
    finally:
        network.server.close()
        browser.screen(390, 844)
