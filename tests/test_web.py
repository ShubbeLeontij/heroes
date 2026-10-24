"""Веб-клиент: страница и картинки по HTTP и партия через WebSocket на том же порту, что и TCP-гости."""
import base64
import contextlib
import io
import json
import os
import socket
import threading
from types import SimpleNamespace

import pytest

from heroes import cli
from heroes.engine.game import Game
from heroes.web.ru import Translator
from heroes.net import web
from heroes.net.protocol import VERSION
from heroes.players.random_bot import RandomBot

from browser import WsClient
from test_protocol import gui_client


def http_get(port: int, path: str, headers: str = "") -> tuple[int, dict, bytes]:
    with socket.create_connection(("127.0.0.1", port)) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n{headers}\r\n".encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    fields = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    return int(lines[0].split()[1]), fields, body


@contextlib.contextmanager
def open_server(seats=("Guest",)):
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    for name in seats:
        network.seat(name)
    network.server.start()
    try:
        yield network.server
    finally:
        network.server.close()


def test_websocket_accept_key_from_the_rfc():
    assert web.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_page_and_pictures_are_served():
    with open_server() as server:
        status, fields, body = http_get(server.port, "/")
        assert status == 200 and fields["content-type"].startswith("text/html") and b"main.js" in body
        status, fields, body = http_get(server.port, "/static/js/main.js")
        assert status == 200 and fields["content-type"].startswith("text/javascript")
        status, fields, body = http_get(server.port, "/assets/icons/wood_40.png")
        assert status == 200 and fields["content-type"] == "image/png" and body.startswith(b"\x89PNG")
        status, again, _ = http_get(server.port, "/assets/icons/wood_40.png", f"If-None-Match: {fields['etag']}\r\n")
        assert status == 304
        status, _, body = http_get(server.port, "/ui.json")
        ui = json.loads(body)
        assert status == 200 and ui["marks"][0] == "wood" and ui["actions"]["move"] == "Перемещение"
        status, fields, body = http_get(server.port, "/ping")   # им страница будит WebSocket в Safari
        assert status == 200 and body == b"" and fields["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/static/../net/web.py", "/assets/icons/../../../cli.py", "/static/%2e%2e/net/web.py",
                                  "/heroes/cli.py", "/static/", "/nope.png"])
def test_nothing_outside_the_page_is_served(path):
    with open_server() as server:
        status, _, _ = http_get(server.port, path)
        assert status == 404


def test_tcp_guests_still_connect_next_to_browsers():
    """Порт один: обычный TCP-гость получает отказ на неверный пароль, как раньше, — а не HTTP."""
    from heroes.net.protocol import Connection
    with open_server() as server:
        conn = Connection(socket.create_connection(("127.0.0.1", server.port)))
        conn.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "wrong", "mode": "gui"})
        assert conn.recv() == {"t": "error", "text": "Wrong password."}
        conn.close()


def test_websocket_refusal_is_translated():
    with open_server() as server:
        ws = WsClient(server.port)
        ws.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "wrong", "mode": "gui"})
        message = ws.recv()
        assert message["t"] == "error" and message["text_ru"] == Translator().line("Wrong password.")
        assert message["text_ru"] != message["text"]
        ws.close()


def test_page_ping_gets_pong_and_long_fragmented_messages_arrive():
    """{"t": "ping"} страницы — сразу {"t": "pong"}; hello с большой аватаркой — кадрами-фрагментами
    и длиной больше 64 КБ — доходит целиком."""
    with open_server() as server:
        ws = WsClient(server.port, fragment=50_000)
        ws.send({"t": "ping"})
        assert ws.recv() == {"t": "pong"}
        avatar = base64.b64encode(os.urandom(150_000)).decode()
        ws.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "secret", "mode": "gui", "avatar": avatar})
        assert ws.recv()["t"] == "welcome"
        threading.Event().wait(0.1)
        assert server.seats["Guest"].avatar == avatar
        ws.close()


def browser_game(factions, leave_after=None):
    """Партия: гость — клиент через WebSocket, как браузер (отвечает ботом), соперник — бот."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    names = ["Guest", "Bot"]
    meta = {"players": ["remote", "bot"], "seed": 4}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta, factions=factions)
    first, second = [], []
    server = network.server

    def guest():
        gui_client(server.port, "Guest", RandomBot(7), first, leave_after=leave_after, connect=WsClient)
        if leave_after:
            gui_client(server.port, "Guest", RandomBot(8), second, connect=WsClient)

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
    return game, first, second


@pytest.mark.parametrize("factions", [None, ["nagas", "orcs"], ["undead", "elves"], ["dwarves", "humans"]])
def test_whole_game_through_websocket(factions):
    game, received, _ = browser_game(factions)
    kinds = [m["t"] for m in received]
    assert kinds[:2] == ["welcome", "sync"] and received[1]["you"] == 0
    assert kinds[-1] == "end" and received[-2]["state"]["round"] == 16
    assert not any("the default was used" in line for line in game.state.log)
    # русские тексты — перевод ru.py
    tr = Translator(received[1]["static"])
    events = [m for m in received if m["t"] == "event"]
    assert events and all(m["text_ru"].startswith(tr.line(m["text"])[:3]) for m in events)
    assert len(received[1]["log_ru"]) == len(received[1]["log"])
    assert any(m["popup"] for m in events)
    assert any("reason_ru" in m for m in received if m["t"] == "decision" and m["kind"] == "distribute_resources") \
        or not any(m["t"] == "decision" and m["kind"] == "distribute_resources" for m in received)


def test_browser_reconnects_mid_game():
    game, first, second = browser_game(None, leave_after=6)
    assert first[-1]["t"] == "decision"
    resync = [m for m in second if m["t"] == "sync"]
    assert resync and resync[0]["log_ru"]
    repeated = next(m for m in second if m["t"] == "decision")
    assert repeated["id"] == first[-1]["id"]
    assert game.state.round == 16 and second[-1]["t"] == "end"


def test_closed_browser_frees_its_seat_at_once():
    """Вкладку закрыли или перезагрузили, пока игрок ждал хода (сервер его не читал): место сразу свободно,
    и та же страница садится за него снова, а не получает «места заняты»."""
    with open_server() as server:
        first = WsClient(server.port)
        first.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "secret", "mode": "gui"})
        assert first.recv()["t"] == "welcome"
        first.close()
        for _ in range(100):
            if not server.seats["Guest"].connected:
                break
            threading.Event().wait(0.02)
        second = WsClient(server.port)
        second.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "secret", "mode": "gui"})
        assert second.recv()["t"] == "welcome"
        second.close()


def test_silent_browser_is_dropped(monkeypatch):
    """Браузер пропал без закрытия соединения (телефон уснул): после SILENCE секунд тишины место освобождается."""
    monkeypatch.setattr(web, "PING", 0.05)
    monkeypatch.setattr(web, "SILENCE", 0.3)
    with open_server() as server:
        ws = WsClient(server.port)
        ws.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "secret", "mode": "gui"})
        assert ws.recv()["t"] == "welcome"
        seat = server.seats["Guest"]
        conn = seat.conn
        for _ in range(100):   # не читаем кадры ping — не отвечаем на них
            if conn.closed:
                break
            threading.Event().wait(0.05)
        assert conn.closed
        seat.send({"t": "state", "state": {}})   # как и с TCP-гостем, обрыв замечает следующая отправка
        assert not seat.connected
        ws.sock.close()
