"""Минимальная проверка игры по сети: партия через localhost, сетевой игрок против бота."""
import contextlib
import io
import random
import threading
from types import SimpleNamespace

from heroes import cli
from heroes.engine.game import Game
from heroes.net.client import run_client


def test_network_game_over_localhost():
    network = cli.Network(0, "secret")
    names = ["Guest", "Bot"]
    meta = {"players": ["remote", "bot"], "seed": 1}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta)
    rng = random.Random(0)
    pool = ["1", "2", "0", "", "y", "n", "w", "g", "all", "w g b m", "1 0 0"]
    out, code = [], {}
    with contextlib.redirect_stdout(io.StringIO()):
        server = network.server
        server.start()
        client = threading.Thread(target=lambda: code.setdefault("client", run_client(
            f"127.0.0.1:{server.port}", "Guest", "secret",
            input_fn=lambda prompt: rng.choice(pool), output_fn=out.append, color=False)), daemon=True)
        client.start()
        assert cli.open_table(game, server, SimpleNamespace(password="secret"))
        game.run()
        server.broadcast_end("game over")
        client.join(timeout=30)
        server.close()
    assert game.state.round == 16
    assert code["client"] == 0 and out[-1] == "game over"
    assert any("=== Round 16" in line for line in out)       # события доходят до сетевого игрока


def test_wrong_password_is_rejected():
    network = cli.Network(0, "secret")
    network.seat("Guest")
    network.server.start()
    out = []
    try:
        assert run_client(f"127.0.0.1:{network.server.port}", "Guest", "wrong", output_fn=out.append) == 1
    finally:
        network.server.close()
    assert "Wrong password" in out[-1]


def test_close_does_not_hang_while_another_thread_reads():
    """Закрытие окна посреди партии: поток связи ждёт в recv(), а close() зовёт главный поток."""
    import socket
    from heroes.net.protocol import Connection
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
