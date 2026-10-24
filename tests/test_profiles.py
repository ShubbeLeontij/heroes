"""Профили: хранение, профиль по имени человека, запись доигранной партии, аватарки, запросы страницы."""
import base64
import json
import socket
import struct

import pytest

from heroes import cli, profiles, savegame
from heroes.engine.game import Game
from heroes.net import web
from heroes.players.random_bot import RandomBot
from heroes.protocol.snapshot import snapshot, static_data

AVATARS = web.ASSETS / "avatars"
# PNG 1×1 (белый пиксель)
PIXEL = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f8ffffff7f0009fb03fd2a86e38a0000000049454e44ae426082")).decode()


def finished_game(n=3, first=1, kinds=("human", "claude", "bot", "remote")):
    game = Game([RandomBot(i) for i in range(n)], names=["Аня", "Claude", "Бот", "Гость"][:n], first_player=first,
                meta={"players": list(kinds[:n]), "seed": 0})
    game.run()
    return game


def test_create_rename_delete(tmp_path):
    anya = profiles.create("Аня", tmp_path)
    borya = profiles.create("Боря", tmp_path)
    assert [p.name for p in profiles.all_profiles(tmp_path)] == ["Аня", "Боря"]
    with pytest.raises(ValueError):
        profiles.create("аня", tmp_path)                  # имена различаются без учёта регистра
    with pytest.raises(ValueError):
        profiles.rename(borya, "  ")
    profiles.rename(borya, "Борис")
    assert profiles.get(borya.id, tmp_path).name == "Борис"
    assert profiles.by_name("борис ", tmp_path).id == borya.id
    profiles.delete(anya)
    profiles.delete(borya)
    assert profiles.all_profiles(tmp_path) == []


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


def test_seat_profiles_by_name_or_from_meta(tmp_path):
    anya = profiles.create("Аня", tmp_path)
    names = ["аня", "Боря"]
    assert profiles.seat_profiles({"players": ["human", "remote"]}, names, tmp_path) == {0: anya.id}
    assert profiles.seat_profiles({"players": ["claude", "human"]}, names, tmp_path) == {}   # Claude не профиль
    # партия из прежнего окна: места записаны в meta
    assert profiles.seat_profiles({"players": ["bot", "human"], "profiles": {"1": anya.id}}, names, tmp_path) == {1: anya.id}
    # в конце партии профиль заводится сам; имя, которое не годится для профиля, — без профиля
    seats = profiles.seat_profiles({"players": ["human", "remote", "human"]}, [*names, "Я" * 30], tmp_path,
                                   create_missing=True)
    assert seats[0] == anya.id and profiles.get(seats[1], tmp_path).name == "Боря" and 2 not in seats


def test_set_avatar(tmp_path):
    anya = profiles.create("Аня", tmp_path)
    profiles.set_avatar(anya, PIXEL)
    assert profiles.avatar_b64(anya) == PIXEL
    with pytest.raises(ValueError):
        profiles.set_avatar(anya, base64.b64encode(b"not a png").decode())
    with pytest.raises(ValueError):
        profiles.set_avatar(anya, "!!!")
    profiles.set_avatar(anya, None)
    assert anya.avatar is None


def test_builtin_avatars_exist():
    for kind in ("claude", "bot", "human"):
        for color in ["0", "1", "2", "3", "neutral"]:
            data = (AVATARS / f"{kind}_{color}.png").read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24]) == (128, 128)


def test_host_records_game_with_avatar(tmp_path, monkeypatch):
    """Конец партии у хозяина: человеку заводится профиль по имени, туда — партия и его аватарка."""
    monkeypatch.chdir(tmp_path)
    game = finished_game(n=2, kinds=("human", "bot"))
    game.avatars[0] = PIXEL
    out = []
    cli.record_profiles(game, out=out.append)
    anya = profiles.by_name("Аня")
    assert out == ["Recorded in the profile statistics: Аня"]
    assert len(anya.games) == 1 and profiles.avatar_b64(anya) == PIXEL
    assert profiles.by_name("Бот") is None


def post(port: int, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    with socket.create_connection(("127.0.0.1", port)) as sock:
        sock.sendall(f"POST /profiles HTTP/1.1\r\nHost: x\r\nConnection: close\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        reply = b""
        while chunk := sock.recv(65536):
            reply += chunk
    head, _, payload = reply.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(payload)


def test_profile_requests_from_the_page(tmp_path, monkeypatch):
    """Страница «Профили и статистика»: список, новый профиль, имя, аватарка, удаление — с паролем партии."""
    monkeypatch.chdir(tmp_path)
    network = cli.Network(0, "secret", log_fn=lambda text: None)
    network.seat("Гость")
    server = network.server
    server.start()
    try:
        assert post(server.port, {"action": "list"})[0] == 403
        assert post(server.port, {"action": "list", "password": "secret"}) == (200, {"profiles": []})
        status, reply = post(server.port, {"action": "create", "name": "Аня", "password": "secret"})
        assert status == 200 and reply["profile"]["name"] == "Аня" and reply["profile"]["summary"]["games"] == 0
        pid = reply["profile"]["id"]
        status, reply = post(server.port, {"action": "create", "name": "аня", "password": "secret"})
        assert status == 400 and "уже есть" in reply["error"]
        status, reply = post(server.port, {"action": "rename", "id": pid, "name": "Анна", "password": "secret"})
        assert status == 200 and reply["profile"]["name"] == "Анна"
        status, reply = post(server.port, {"action": "avatar", "id": pid, "avatar": PIXEL, "password": "secret"})
        assert status == 200 and reply["profile"]["avatar"] == PIXEL
        assert post(server.port, {"action": "avatar", "id": pid, "avatar": "AAAA", "password": "secret"})[0] == 400
        listed = post(server.port, {"action": "list", "password": "secret"})[1]["profiles"]
        assert [p["name"] for p in listed] == ["Анна"] and listed[0]["avatar"] == PIXEL
        assert post(server.port, {"action": "delete", "id": pid, "password": "secret"}) == (200, {})
        assert post(server.port, {"action": "rename", "id": pid, "name": "X", "password": "secret"})[0] == 400
        assert profiles.all_profiles() == []
    finally:
        server.close()
