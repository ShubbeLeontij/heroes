"""Протокол: снимки состояния и решения данными, по сети."""
import contextlib
import io
import json
import pytest
import socket
import threading
from types import SimpleNamespace

from heroes import cli
from heroes.engine.game import Game
from heroes.net.protocol import VERSION, Connection
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import decision_message, static_data, sync_message


class Recorder(RandomBot):
    """Бот, который заодно проверяет, что всё, что увидел бы интерфейс, сериализуется в JSON."""

    def decide(self, decision, view):
        json.dumps(decision_message(view.game, decision, 1))
        return super().decide(decision, view)


@pytest.mark.parametrize("factions", [None, ["nagas", "orcs", "dwarves"], ["undead", "elves", "humans"]])
def test_snapshots_are_json_through_a_whole_game(factions):
    game = Game([Recorder(1), RandomBot(2), RandomBot(3)], factions=factions)
    json.dumps(sync_message(game, 0))
    game.run()
    message = json.loads(json.dumps(sync_message(game, 0)))
    assert message["state"]["round"] == 16
    assert set(message["static"]["regions"]) == set(game.map.regions)


def test_build_decision_message_is_json():
    game = Game([RandomBot(0), RandomBot(1)])
    player = game.state.players[0]
    player.region = "Карракс"
    player.resources.update(wood=10, iron=10, gold=10)
    from heroes.engine.actions import BuildTurn, build_data
    from heroes.engine.decisions import Decision
    decision = Decision("build_recruit", 0, "", build_data(game, 0, BuildTurn(1)), lambda a: None, None)
    data = json.loads(json.dumps(decision_message(game, decision, 1)))["data"]
    assert "warriors_guild" in data["options"] and data["hire"] == {} and data["steps"] == []


def gui_client(port: int, name: str, answers_from: RandomBot, received: list, leave_after: int | None = None,
               avatar: str | None = None, connect=None) -> None:
    """Клиент без страницы: на каждое решение отвечает ботом по присланным данным.
    leave_after — оборвать связь, не ответив на столько-то решений (и вернуть управление); avatar — своя аватарка;
    connect(port) — своё соединение (по умолчанию TCP; test_web — WebSocket, как у браузера)."""
    conn = connect(port) if connect else Connection(socket.create_connection(("127.0.0.1", port)))
    hello = {"t": "hello", "version": VERSION, "name": name, "password": "secret", "mode": "gui"}
    if avatar:
        hello["avatar"] = avatar
    conn.send(hello)
    decisions = 0
    while (message := conn.recv()) is not None:
        received.append(message)
        if message["t"] == "decision":
            decisions += 1
            if decisions == leave_after:
                break
            decision = SimpleNamespace(kind=message["kind"], data=message["data"], default=None)
            answer = answers_from.decide(decision, None)
            conn.send({"t": "answer", "id": message["id"], "answer": answer})
        elif message["t"] == "end":
            break
    conn.close()


@pytest.mark.parametrize("factions", [None, ["nagas", "orcs"]])
def test_gui_client_over_network(factions):
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    names = ["Guest", "Bot"]
    meta = {"players": ["remote", "bot"], "seed": 4}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta, factions=factions)
    received: list[dict] = []
    server = network.server
    with contextlib.redirect_stdout(io.StringIO()):
        game.players[0].bind(game, 0)  # иначе гость может подключиться раньше, чем место привязано к партии
        server.start()
        client = threading.Thread(target=gui_client, args=(server.port, "Guest", RandomBot(7), received), daemon=True)
        client.start()
        assert cli.open_table(game, server, SimpleNamespace(password="secret"), out=lambda text: None)
        game.run()
        server.broadcast_end("game over")
        client.join(timeout=30)
        server.close()
    kinds = [m["t"] for m in received]
    assert kinds[:2] == ["welcome", "sync"] and received[1]["you"] == 0
    assert "decision" in kinds and "event" in kinds and kinds[-1] == "end"
    assert received[-2]["state"]["round"] == 16
    assert not any("the default was used" in line for line in game.state.log)


def test_guest_avatar_reaches_everyone():
    """Аватарка из hello гостя попадает в static["avatars"] — и ему, и другим гостям, подключившимся раньше."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    names = ["Аня", "Боря"]
    meta = {"players": ["remote", "remote"], "seed": 1}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta)
    server = network.server
    got = {name: [] for name in names}
    with contextlib.redirect_stdout(io.StringIO()):
        for p, player in enumerate(game.players):
            player.bind(game, p)
        server.start()
        first = threading.Thread(target=gui_client, args=(server.port, "Аня", RandomBot(1), got["Аня"]), daemon=True)
        first.start()
        while not any(m["t"] == "sync" for m in got["Аня"]):   # Аня — раньше Бори, ещё без его аватарки
            threading.Event().wait(0.02)
        second = threading.Thread(target=gui_client, args=(server.port, "Боря", RandomBot(2), got["Боря"]),
                                  kwargs={"avatar": "QUJD"}, daemon=True)
        second.start()
        assert cli.open_table(game, server, SimpleNamespace(password="secret"), out=lambda text: None)
        server.broadcast_end("game over")
        first.join(timeout=10)
        second.join(timeout=10)
        server.close()
    for name in names:
        syncs = [m for m in got[name] if m["t"] == "sync"]
        assert syncs[-1]["static"]["avatars"] == [None, "QUJD"], name
    assert [m for m in got["Аня"] if m["t"] == "sync"][0]["static"]["avatars"] == [None, None]


def test_static_data_matches_map():
    game = Game([RandomBot(0), RandomBot(1), RandomBot(2), RandomBot(3)], factions=["humans", "orcs", "elves", "nagas"])
    data = static_data(game)
    assert data["regions"]["Бринлор"]["fortress"]["strength"] == 14
    assert "Бринлор" in data["adjacent"]["Асандер"]
    assert data["factions"]["humans"]["units"]["soldier"]["name"] == "Солдат"
    # модификация войск орков за ярость — карточка свойства на планшете показывает цены
    assert data["factions"]["orcs"]["rules"]["upgrade"]["unit_upgrades"] == [
        {"from": "scout", "to": "brute", "cost": {"rage": 1}}, {"from": "brute", "to": "blaster", "cost": {"rage": 3}},
        {"from": "scout", "to": "blaster", "cost": {"rage": 4}}]
    assert data["factions"]["orcs"]["rules"]["rage"]["unit_upgrades"] == []
    # пороги ПО за ярость показывает склад — карточки свойства на планшете нет
    assert [rid for f in data["factions"].values() for rid, r in f["rules"].items() if r["in_stock"]] == ["rage"]


def test_gui_client_reconnects_mid_game():
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    names = ["Guest", "Bot"]
    meta = {"players": ["remote", "bot"], "seed": 5}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta)
    first, second = [], []
    server = network.server

    def guest():
        gui_client(server.port, "Guest", RandomBot(1), first, leave_after=6)
        gui_client(server.port, "Guest", RandomBot(2), second)

    with contextlib.redirect_stdout(io.StringIO()):
        game.players[0].bind(game, 0)
        server.start()
        client = threading.Thread(target=guest, daemon=True)
        client.start()
        assert cli.open_table(game, server, SimpleNamespace(password="secret"), out=lambda text: None)
        game.run()
        server.broadcast_end("game over")
        client.join(timeout=30)
        server.close()
    assert first[-1]["t"] == "decision"
    resync = [m for m in second if m["t"] == "sync"]
    assert resync and resync[0]["log"]                      # после переподключения — вся партия
    repeated = next(m for m in second if m["t"] == "decision")
    assert repeated["id"] == first[-1]["id"]                # и тот же вопрос, что остался без ответа
    assert game.state.round == 16 and second[-1]["t"] == "end"


def test_unit_mods_show_what_applies_now():
    """Сила войск «как на планшете»: модификации и можно ли их применить прямо сейчас (Лавка — если есть дерево)."""
    from heroes.protocol.snapshot import snapshot
    game = Game([RandomBot(1), RandomBot(2)], factions=["elves", "orcs"])
    elves, orcs = game.state.players
    elves.built.append("armorer")
    elves.resources["wood"] = 0
    orcs.region = "Икара"   # горы и крепость
    units = snapshot(game)["players"][0]["units"]
    assert units["ent"]["mods"] == [{"value": 10, "kind": "pay", "cost": {"wood": 1}, "active": False}]
    elves.resources["wood"] = 1
    assert snapshot(game)["players"][0]["units"]["ent"]["mods"][0]["active"]
    # Лагерь: разведчик в горах — 4; Мастерская: подрывник против крепости — 12. Это свойства войск
    # (звёздочка на планшете): действуют и без зданий
    orc_units = snapshot(game)["players"][1]["units"]
    assert orc_units["scout"]["mods"] == [{"value": 4, "kind": "terrain", "terrain": "mountain", "active": True}]
    assert orc_units["scout"]["mods"][0]["active"] and orc_units["blaster"]["mods"][0]["active"]
    orcs.region = "Есмор"   # поле, крепости нет
    orc_units = snapshot(game)["players"][1]["units"]
    assert not orc_units["scout"]["mods"][0]["active"] and not orc_units["blaster"]["mods"][0]["active"]


def test_action_order_carries_move_preview():
    """Пока выбирают порядок действий: куда каждый дойдёт, если перемещение встанет на место 1–4."""
    from heroes.engine.decisions import Decision
    from heroes.engine.movement import reachable
    game = Game([RandomBot(1), RandomBot(2)], factions=["humans", "orcs"])
    game.state.players[0].region, game.state.players[1].region = "Асандер", "Есмор"
    d = Decision("action_order", 0, "", {"actions": []}, lambda a: None, None)
    reach = decision_message(game, d, 1)["data"]["reach"]
    assert set(reach) == {"0", "1"} and set(reach["0"]) == {"1", "2", "3", "4"}
    assert reach["1"]["3"] == reachable(game, 1, 3)
