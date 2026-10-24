"""Сетевой протокол: JSON-объекты поверх соединения с браузером (WebSocket, web.py) или по одному
на строку поверх TCP (Connection; им пользуются тесты). Сервер шлёт состояние партии и решения данными.

Сервер → клиент:
  {"t": "choose", "seats": [...]}         — указанного места нет: выберите из свободных
  {"t": "welcome", "name": ..., "text", "watch"?: true}   — вы подключены к месту name (или как зритель)
  {"t": "error", "text": ...}             — отказ в подключении
  {"t": "end", "text": ...}               — партия закончилась или прервана
  {"t": "sync", "you", "static", "log", "state"}  — вся партия: при подключении и после загрузки (у зрителя you — null)
  {"t": "event", "text", "state"}                  — новое событие лога и состояние после него
  {"t": "state", "state"}                          — кто-то начал или закончил решать (state.thinking)
  {"t": "decision", "id", "player", "kind", "text", "data", "state", "error"?}
                                          — нужно решение (ответ — "answer"); error — прошлый ответ отклонён
Клиент → сервер:
  {"t": "hello", "version": 5, "name": ..., "password": ..., "mode": "gui", "avatar"?: PNG в base64, "watch"?: true}
                                          — avatar (необязательно) хозяин рассылает всем в static["avatars"];
                                            watch — смотреть партию, не занимая места
  {"t": "answer", "id": ..., "answer": <JSON>}
"""
from __future__ import annotations

import json
import socket

# 3 — фракции: особые решения и данные о них (старый клиент их не понял бы);
# 4 — модификация Лавкой бронника — часть заявки войск, запрет заведомо проигрышного штурма и боя с чудовищем;
# 5 — без консольного клиента (только данные), зрители
VERSION = 5
MODES = ("gui",)
DEFAULT_PORT = 7777


class Connection:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        # Сообщения маленькие и идут вопрос-ответ: без этого каждое ждёт ~40 мс (алгоритм Нейгла).
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = sock.makefile("r", encoding="utf-8", newline="\n")

    def send(self, message: dict) -> None:
        self.sock.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def recv(self) -> dict | None:
        """Следующее сообщение или None, если соединение закрыто."""
        line = self.reader.readline()
        if not line:
            return None
        return json.loads(line)

    def close(self) -> None:
        # Сначала shutdown: он будит поток, ждущий в recv(). Закрыть reader раньше нельзя —
        # close() ждёт его внутреннюю блокировку, которую держит readline(), то есть вечно.
        for closer in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.reader.close, self.sock.close):
            try:
                closer()
            except OSError:
                pass
