"""Клиент сетевой игры: показывает то, что присылает сервер, и отправляет ввод игрока.

  python -m heroes --join 192.168.1.5 --name Боря
"""
from __future__ import annotations

import re
import socket
import sys

from .protocol import VERSION, Connection, parse_address

ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def run_client(address: str, name: str | None = None, password: str | None = None,
               input_fn=input, output_fn=print, color: bool | None = None) -> int:
    host, port = parse_address(address)
    color = sys.stdout.isatty() if color is None else color
    show = (lambda text: output_fn(text)) if color else (lambda text: output_fn(ANSI.sub("", text)))
    try:
        sock = socket.create_connection((host, port), timeout=10)
    except OSError as e:
        output_fn(f"Could not connect to {host}:{port}: {e}")
        return 1
    sock.settimeout(None)
    conn = Connection(sock)
    try:
        conn.send({"t": "hello", "version": VERSION, "name": name, "password": password, "mode": "console"})
        while True:
            message = conn.recv()
            if message is None:
                output_fn("\nConnection to the server lost. Reconnect with the same command.")
                return 1
            kind = message.get("t")
            if kind == "out":
                show(message["text"])
            elif kind == "in":
                prompt = message["prompt"] if color else ANSI.sub("", message["prompt"])
                try:
                    line = input_fn(prompt)
                except EOFError:  # Ctrl+D: просто отключаемся, партия подождёт
                    output_fn("\nInput closed — disconnecting. The game waits for you: reconnect with the same command.")
                    return 1
                conn.send({"t": "line", "text": line})
            elif kind == "choose":
                output_fn("Free seats: " + ", ".join(f"{i} — {s}" for i, s in enumerate(message["seats"], 1)))
                choice = input_fn("Which seat is yours? ").strip()
                seats = message["seats"]
                picked = seats[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(seats) else choice
                conn.send({"t": "hello", "version": VERSION, "name": picked, "password": password, "mode": "console"})
            elif kind == "welcome":
                output_fn(message.get("text", f"You are at the table as {message['name']}.") + " Waiting for the game…")
            elif kind == "error":
                output_fn(f"The server refused: {message['text']}")
                return 1
            elif kind == "end":
                show(message["text"])
                return 0
    except KeyboardInterrupt:
        output_fn("\nDisconnected. The game waits for you: reconnect with the same command.")
        return 130
    finally:
        conn.close()
