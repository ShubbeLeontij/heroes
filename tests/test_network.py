"""Хост в терминале: сервер партии, зрители, команды хозяина и мастер новой партии."""
import contextlib
import io
import socket
import threading
from argparse import Namespace
from types import SimpleNamespace

from heroes import cli
from heroes.engine.game import Game
from heroes.net.protocol import VERSION, Connection
from heroes.players.random_bot import RandomBot


def hello(port: int, **fields) -> Connection:
    conn = Connection(socket.create_connection(("127.0.0.1", port)))
    conn.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "secret", "mode": "gui", **fields})
    return conn


def test_wrong_password_and_unknown_mode_are_rejected():
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Guest")
    network.server.start()
    try:
        assert hello(network.server.port, password="wrong").recv()["text"] == "Wrong password."
        assert hello(network.server.port, mode="console").recv()["text"].startswith("Unknown client mode")
    finally:
        network.server.close()


def test_spectator_watches_bots():
    """--watch: партия ботов открыта для зрителей; зритель получает всю партию, события и конец."""
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    names = ["Бот1", "Бот2"]
    meta = {"players": ["bot", "bot"], "seed": 3}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta)
    server = network.open()
    assert cli.open_table(game, server, SimpleNamespace(password="secret"), out=lambda text: None)
    received = []
    conn = hello(server.port, name=None, watch=True)
    welcome = conn.recv()
    assert welcome["watch"] and welcome["name"] is None
    sync = conn.recv()
    assert sync["t"] == "sync" and sync["you"] is None
    reader = threading.Thread(target=lambda: received.extend(iter(conn.recv, None)), daemon=True)
    reader.start()
    try:
        game.run()
        server.broadcast_end("game over")
        reader.join(timeout=0.5)
    finally:
        server.close()
        reader.join(timeout=5)
    assert not any(m["t"] == "decision" for m in received)
    assert any(m["t"] == "event" and "=== Round 16" in m["text"] for m in received)
    assert received[-1] == {"t": "end", "text": "game over"}


def test_spectator_with_human_seat_does_not_take_it():
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    seat = network.seat("Guest")
    network.server.start()
    try:
        assert hello(network.server.port, watch=True).recv()["watch"]
        assert network.server.free_seats() == ["Guest"] and not seat.connected
    finally:
        network.server.close()


def test_host_save_command(tmp_path):
    game = Game([RandomBot(0), RandomBot(1)], meta={"players": ["bot", "bot"], "seed": 0})
    lines = iter(["save " + str(tmp_path / "now.json"), "help", ""])
    out = []

    def read():
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    cli.host_commands(game, read, out.append)
    assert (tmp_path / "now.json").exists() and out[0].startswith("Game saved:")
    assert out[1].startswith("Commands:")
    # после конца партии строка хозяина (Enter) только завершает поток — это закрывает страницу
    ended = threading.Event()
    ended.set()
    cli.host_commands(game, lambda: "save", out.append, ended=ended)
    assert len(out) == 2


def args(**kw) -> Namespace:
    return Namespace(**({"model": None, "effort": None, "show_thoughts": None, "port": 7777, "password": None} | kw))


def test_wizard_asks_everything_the_new_game_window_did(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    answers = iter(["3",
                    "1", "Аня", "4",      # человек, орки
                    "2", "", "",          # Claude, имя по умолчанию, случайная фракция
                    "3", "аня", "Бот", "4", "",   # бот: занятое имя (без учёта регистра), занятая фракция
                    "3",                  # модель — Haiku
                    "3",                  # effort — high
                    "y",                  # замыслы Claude — печатать
                    "7000", "сова",       # порт и пароль
                    "2",                  # первым ходит Claude 1
                    ""])                  # начинаем
    out = []
    a = args()
    kinds, names, factions, first, model = cli.setup_wizard(a, lambda prompt: next(answers), out.append)
    assert kinds == ["human", "claude", "bot"] and names == ["Аня", "Claude 1", "Бот"]
    assert factions == ["orcs", "random", "random"] and first == 1 and model == "claude-haiku-5-5"
    assert (a.effort, a.show_thoughts, a.port, a.password) == ("high", True, 7000, "сова")
    assert "  That name is taken" in out and any("That faction is taken" in line for line in out)


def test_wizard_can_be_cancelled():
    answers = iter(["2", "3", "", "", "3", "", "", "", "n"])
    assert cli.setup_wizard(args(), lambda prompt: next(answers), lambda text: None) is None


def test_join_links_and_first_player_flag(monkeypatch):
    assert cli.join_link("10.0.0.2", 7777, "Аня", "с ова") == "http://10.0.0.2:7777/?name=%D0%90%D0%BD%D1%8F&password=%D1%81%20%D0%BE%D0%B2%D0%B0"
    assert cli.join_link("10.0.0.2", 7000) == "http://10.0.0.2:7000/"
    started = {}

    class FakeGame:
        def __init__(self, players, **kw):
            started.update(kw)
            raise SystemExit("stop")

    monkeypatch.setattr(cli, "Game", FakeGame)
    with contextlib.redirect_stdout(io.StringIO()):
        for first in ("random", "1"):
            try:
                cli.main(["--players", "bot,bot", "--first", first, "--autosave", ""])
            except SystemExit as e:
                assert str(e) == "stop"
            assert started["first_player"] in (0, 1)
        try:
            cli.main(["--players", "bot,bot", "--first", "5"])
        except SystemExit as e:
            assert "--first" in str(e)


def test_close_does_not_hang_while_another_thread_reads():
    """Закрытие посреди партии: поток связи ждёт в recv(), а close() зовёт главный поток."""
    with socket.create_server(("127.0.0.1", 0)) as listener:
        sock = socket.create_connection(listener.getsockname())
        peer, _ = listener.accept()
    conn = Connection(sock)
    reader = threading.Thread(target=conn.recv, daemon=True)
    reader.start()
    reader.join(0.2)
    closer = threading.Thread(target=conn.close, daemon=True)
    closer.start()
    closer.join(3)
    peer.close()
    assert not closer.is_alive() and not reader.is_alive()
