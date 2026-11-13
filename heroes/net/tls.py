"""TLS для сервера партии: страница по https, соединение партии по wss.

Сертификат — свои PEM-файлы хозяина (--cert, --key; например, от Let's Encrypt) или самоподписанный: его один раз
выпускает команда openssl и кладёт в saves/tls/. На самоподписанный браузер в первый раз предупреждает.

Сервер узнаёт TLS по первому байту соединения (0x16 — запись рукопожатия). Расшифровывает TlsSocket: он
сделан на MemoryBIO, а не на ssl.SSLSocket, потому что соединение браузера читает один поток, а пишут другие
(web.WebSocketConnection), а один объект SSL OpenSSL не разрешает трогать из нескольких потоков сразу.
"""
from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Callable

CERT_DIR = Path("saves") / "tls"
HANDSHAKE = 0x16   # первый байт TLS-соединения: тип записи «рукопожатие»


class TlsError(Exception):
    """Не удалось подготовить сертификат."""


def context(cert: str | Path | None = None, key: str | Path | None = None,
            log_fn: Callable[[str], None] = print) -> ssl.SSLContext:
    """Контекст сервера. Без cert — самоподписанный сертификат из CERT_DIR (выпускается при первом запуске).
    key не нужен, если ключ лежит в том же PEM-файле, что и сертификат."""
    if cert is None:
        cert, key = self_signed(log_fn=log_fn)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as e:
        raise TlsError(f"cannot load the certificate {cert}: {e}") from None
    return ctx


def self_signed(folder: Path | None = None, log_fn: Callable[[str], None] = print) -> tuple[Path, Path]:
    """Самоподписанный сертификат и ключ в folder (по умолчанию CERT_DIR); если их ещё нет — выпустить командой openssl."""
    folder = folder or CERT_DIR
    cert, key = folder / "cert.pem", folder / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    openssl = shutil.which("openssl")
    if openssl is None:
        raise TlsError("the openssl command is not found, so a self-signed certificate cannot be made")
    folder.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "3650",
                        "-subj", "/CN=heroes", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        for f in (cert, key):
            f.unlink(missing_ok=True)
        detail = getattr(e, "stderr", b"") or b""
        raise TlsError(f"openssl failed: {detail.decode(errors='replace').strip() or e}") from None
    log_fn(f"Made a self-signed certificate: {cert}")
    return cert, key


def is_tls(sock: socket.socket) -> bool:
    """Соединение начинается с рукопожатия TLS. Байт остаётся в сокете (MSG_PEEK)."""
    return sock.recv(1, socket.MSG_PEEK) == bytes([HANDSHAKE])


class TlsSocket:
    """Серверная сторона TLS поверх обычного сокета: recv/sendall/settimeout/shutdown/close — как у socket.
    Читать и писать можно из разных потоков одновременно. Рукопожатие — в конструкторе."""

    def __init__(self, sock: socket.socket, ctx: ssl.SSLContext):
        self.sock = sock
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = ctx.wrap_bio(self.incoming, self.outgoing, server_side=True)
        self.lock = threading.Lock()         # состояние self.tls
        self.write_lock = threading.Lock()   # порядок записей в сокете; берётся раньше self.lock
        self.read_lock = threading.Lock()    # один читатель сокета за раз
        while True:
            with self.lock:
                try:
                    self.tls.do_handshake()
                    done = True
                except ssl.SSLWantReadError:
                    done = False
            self._flush()
            if done:
                return
            self._fill()

    def recv(self, n: int) -> bytes:
        """До n расшифрованных байт; b"" — соединение закрыто."""
        with self.read_lock:
            while True:
                with self.lock:
                    try:
                        data = self.tls.read(n)
                    except ssl.SSLWantReadError:
                        data = None
                    except (ssl.SSLZeroReturnError, ssl.SSLEOFError):   # закрыл TLS или просто оборвал соединение
                        data = b""
                    pending = self.outgoing.pending
                if pending:   # чтение тоже может что-то отправить (например, после KeyUpdate)
                    self._flush()
                if data is not None:
                    return data
                if not self._fill():
                    return b""

    def sendall(self, data: bytes) -> None:
        with self.write_lock:
            with self.lock:
                self.tls.write(data)
                out = self.outgoing.read()
            self.sock.sendall(out)

    def _flush(self) -> None:
        with self.write_lock:
            with self.lock:
                out = self.outgoing.read()
            if out:
                self.sock.sendall(out)

    def _fill(self) -> bool:
        """Прочитать из сокета зашифрованные байты. False — сокет закрыт."""
        chunk = self.sock.recv(65536)
        with self.lock:
            if chunk:
                self.incoming.write(chunk)
            else:
                self.incoming.write_eof()
        return bool(chunk)

    def settimeout(self, timeout: float | None) -> None:
        self.sock.settimeout(timeout)

    def setsockopt(self, *args) -> None:
        self.sock.setsockopt(*args)

    def shutdown(self, how: int) -> None:
        self.sock.shutdown(how)

    def close(self) -> None:
        self.sock.close()
