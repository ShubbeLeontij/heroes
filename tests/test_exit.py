"""Ctrl+C в терминале хозяина: партия сохраняется и программа завершается, а не зависает."""
import os
import pty
import select
import signal
import socket
import sys
import time
from pathlib import Path

from heroes.net.protocol import VERSION, Connection

ROOT = Path(__file__).resolve().parent.parent


def read_until(fd: int, text: bytes, seconds: float) -> bytes:
    out, end = b"", time.monotonic() + seconds
    while text not in out and time.monotonic() < end:
        if select.select([fd], [], [], 0.2)[0]:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


def test_ctrl_c_stops_host_with_terminal(tmp_path):
    """В терминале у хозяина работает поток команд (`save`); на выходе из программы он не должен её держать."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(tmp_path)
        os.environ["PYTHONPATH"] = str(ROOT)
        os.execv(sys.executable, [sys.executable, "-m", "heroes", "--players", "human,bot", "--names", "A,B",
                                  "--port", str(port), "--autosave", ""])
    guest = None
    try:
        out = read_until(fd, b"Waiting for network players", 20)
        guest = Connection(socket.create_connection(("127.0.0.1", port)))
        guest.send({"t": "hello", "version": VERSION, "name": "A", "mode": "gui"})
        out += read_until(fd, b"=== Round 1", 30)
        assert b"=== Round 1" in out, out
        os.write(fd, b"\x03")
        out += read_until(fd, b"Game saved", 10)
        assert b"Interrupted" in out, out
        deadline, status = time.monotonic() + 10, None
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("the host did not exit after Ctrl+C")
        assert os.WEXITSTATUS(status) == 130
    finally:
        if guest:
            guest.close()
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass
        os.close(fd)
