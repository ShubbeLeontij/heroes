"""TLS: страница по https, партия по wss, http перенаправляется на https (heroes/net/tls.py)."""
import contextlib
import io
import shutil
import socket
import ssl
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from heroes import cli
from heroes.engine.game import Game
from heroes.net import tls
from heroes.net.protocol import DEFAULT_PORT, VERSION, Connection
from heroes.net.server import Server
from heroes.players.random_bot import RandomBot

from browser import WsClient
from test_protocol import gui_client

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    return tls.self_signed(tmp_path_factory.mktemp("tls"), log_fn=lambda text: None)


@pytest.fixture
def client():
    """Клиент, который верит самоподписанному сертификату, — как браузер после «Всё равно перейти»."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@contextlib.contextmanager
def tls_server(certificate, seats=("Guest",)):
    network = cli.Network(0, "secret", log_fn=lambda text: None, tls=lambda: tls.context(*certificate))
    for name in seats:
        network.seat(name)
    network.server.start()
    try:
        yield network.server
    finally:
        network.server.close()


def test_default_port_is_https():
    assert DEFAULT_PORT == 443
    assert Server(443, tls=object()).url("10.0.0.2") == "https://10.0.0.2"
    assert Server(7000, tls=object()).url("10.0.0.2") == "https://10.0.0.2:7000"
    assert Server(443).url("10.0.0.2") == "http://10.0.0.2:443"


def test_self_signed_certificate_is_made_once(tmp_path):
    made = []
    cert, key = tls.self_signed(tmp_path, log_fn=made.append)
    assert cert.read_text().startswith("-----BEGIN CERTIFICATE-----") and key.is_file() and len(made) == 1
    before = cert.read_bytes()
    assert tls.self_signed(tmp_path, log_fn=made.append) == (cert, key)
    assert cert.read_bytes() == before and len(made) == 1
    assert isinstance(tls.context(cert, key), ssl.SSLContext)


def test_bad_certificate_is_reported(tmp_path):
    (tmp_path / "bad.pem").write_text("not a certificate")
    with pytest.raises(tls.TlsError):
        tls.context(tmp_path / "bad.pem")
    with pytest.raises(SystemExit):
        cli.tls_context(SimpleNamespace(cert=str(tmp_path / "bad.pem"), key=None))
    with pytest.raises(SystemExit):
        cli.tls_context(SimpleNamespace(cert=None, key="key.pem"))


def test_without_openssl_the_page_goes_over_http(monkeypatch, tmp_path):
    monkeypatch.setattr(tls, "CERT_DIR", tmp_path / "tls")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert cli.tls_context(SimpleNamespace(cert=None, key=None)) is None
    assert "TLS is off" in out.getvalue()


def test_page_over_https_and_http_redirects(certificate, client):
    with tls_server(certificate) as server:
        page = urllib.request.urlopen(f"https://127.0.0.1:{server.port}/", context=client).read()
        assert b"main.js" in page
        with socket.create_connection(("127.0.0.1", server.port)) as sock:
            sock.sendall(f"GET /?name=Guest HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n\r\n".encode())
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        assert data.startswith(b"HTTP/1.1 302 ")
        assert f"Location: https://127.0.0.1:{server.port}/?name=Guest\r\n".encode() in data


def test_plain_connections_and_bad_handshakes_are_refused(certificate, client):
    with tls_server(certificate) as server:
        conn = Connection(socket.create_connection(("127.0.0.1", server.port)))
        conn.send({"t": "hello", "version": VERSION, "name": "Guest", "password": "secret", "mode": "gui"})
        try:   # сервер закрыл соединение, не дочитав hello, — бывает и RST
            assert conn.recv() is None
        except ConnectionResetError:
            pass
        conn.close()
        strict = ssl.create_default_context()   # не верит самоподписанному: рукопожатие обрывается
        with socket.create_connection(("127.0.0.1", server.port)) as sock, pytest.raises(ssl.SSLError):
            strict.wrap_socket(sock, server_hostname="127.0.0.1")
        assert server.free_seats() == ["Guest"]
        # сервер жив и после этого
        assert b"main.js" in urllib.request.urlopen(f"https://127.0.0.1:{server.port}/", context=client).read()


def test_whole_game_over_wss(certificate, client):
    """Вся партия через wss: поток чтения и отправки одного TLS-соединения работают одновременно."""
    network = cli.Network(0, "secret", log_fn=lambda text: None, tls=lambda: tls.context(*certificate))
    names = ["Guest", "Bot"]
    meta = {"players": ["remote", "bot"], "seed": 4}
    game = Game(cli.make_players(meta, names, False, network), names=names, meta=meta)
    received: list[dict] = []
    server = network.server
    with contextlib.redirect_stdout(io.StringIO()):
        game.players[0].bind(game, 0)
        server.start()
        thread = threading.Thread(target=gui_client, args=(server.port, "Guest", RandomBot(7), received),
                                  kwargs={"connect": lambda port: WsClient(port, tls=client)}, daemon=True)
        thread.start()
        assert cli.open_table(game, server, SimpleNamespace(password="secret"), out=lambda text: None)
        game.run()
        server.broadcast_end("game over")
        thread.join(timeout=30)
        server.close()
    kinds = [m["t"] for m in received]
    assert kinds[:2] == ["welcome", "sync"] and "text_ru" in received[0]
    assert kinds[-1] == "end" and received[-2]["state"]["round"] == 16
