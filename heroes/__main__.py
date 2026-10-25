import os
import sys
import traceback

from .cli import main


def run() -> int:
    """Выйти сразу, не дожидаясь завершения интерпретатора: поток команд хозяина стоит в чтении терминала,
    и при обычном выходе интерпретатор вечно ждёт блокировку stdin, так что Ctrl+C «не срабатывал»."""
    try:
        code = main()
    except SystemExit as e:
        code = e.code
    except BaseException:   # noqa: BLE001 — показать ошибку и всё равно выйти
        traceback.print_exc()
        code = 1
    if code is not None and not isinstance(code, int):
        print(code, file=sys.stderr)
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    return code or 0


if __name__ == "__main__":
    os._exit(run())
