"""Рисует готовые аватарки страницы (heroes/web/assets/avatars/): Claude — человечек, которого Claude Code
показывает в начале новой сессии, бот — голова робота, человек без своей картинки — силуэт. Каждая —
в четырёх цветах мест за столом (common.PLAYER_COLORS) и нейтральном, одной картинкой
MASTER×MASTER: <вид>_<цвет>.png (нужные размеры браузер делает сам).

Внешних зависимостей нет. Робот и силуэт заданы функциями расстояния до края (SDF), край сглаживается
по расстоянию в пикселях; человечек Claude — пиксельный, его клетки закрашиваются по точной площади.
Запускается один раз, вручную:
    python tools/build_avatars.py
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from heroes.web.common import PLAYER_COLORS  # noqa: E402

ASSETS = Path(__file__).resolve().parents[1] / "heroes" / "web" / "assets" / "avatars"
MASTER = 128

NEUTRAL = "#8a7660"
EYES = "#2b2620"
TINT = 0.84    # фон — цвет, смешанный с белым
RING = 0.06    # ободок цветом знака по краю круга



def png_bytes(size: int, rgba: bytes | bytearray) -> bytes:
    """Квадратная RGBA-картинка size×size в PNG (без внешних библиотек)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    stride = size * 4
    raw = b"".join(b"\0" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def write_png(path: str | Path, size: int, rgba: bytes | bytearray) -> None:
    Path(path).write_bytes(png_bytes(size, rgba))

# --- функции расстояния: точка (x, y) в круге радиуса 1, y вверх; < 0 — внутри фигуры ----------

def circle(x, y, cx, cy, r):
    return math.hypot(x - cx, y - cy) - r


def rounded_box(x, y, cx, cy, hw, hh, r):
    qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def segment(x, y, ax, ay, bx, by, r):
    px, py, dx, dy = x - ax, y - ay, bx - ax, by - ay
    t = max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - dx * t, py - dy * t) - r


def bot(x, y):
    body = min(rounded_box(x, y, 0, -0.04, 0.46, 0.36, 0.14),
               rounded_box(x, y, -0.55, -0.04, 0.07, 0.14, 0.05),
               rounded_box(x, y, 0.55, -0.04, 0.07, 0.14, 0.05),
               segment(x, y, 0, 0.3, 0, 0.5, 0.035),
               circle(x, y, 0, 0.56, 0.08))
    holes = min(circle(x, y, -0.19, 0.04, 0.1), circle(x, y, 0.19, 0.04, 0.1),
                rounded_box(x, y, 0, -0.2, 0.2, 0.045, 0.04))
    return max(body, -holes)


def human(x, y):
    return min(circle(x, y, 0, 0.3, 0.27), circle(x, y, 0, -0.95, 0.68))


# --- человечек Claude Code ------------------------------------------------------------------------
# В терминале он нарисован символами-четвертушками:
#    ▐▛███▜▌
#   ▝▜█████▛▘
#     ▘▘ ▝▝
# Четвертушка — клетка вдвое выше ширины. Строки — клетки слева направо (столбцы 1–16), «e» — глаз.
CLAWD = [
    "  ############  ",
    "  ##e######e##  ",
    "################",
    "  ############  ",
    "   # #    # #   ",
]
CELL = 1.5 / 16   # ширина клетки: человечек шириной 1.5 радиуса круга


def clawd_cells(mark: str) -> list[tuple[float, float, float, float]]:
    """Клетки с этим знаком: (x0, y0, x1, y1), y вверх, человечек — по центру круга."""
    cells = []
    for r, row in enumerate(CLAWD):
        for c, ch in enumerate(row):
            if ch == mark:
                x0, top = (c - 8) * CELL, (5 - 2 * r) * CELL
                cells.append((x0, top - 2 * CELL, x0 + CELL, top))
    return cells


def area(cells, x0, y0, x1, y1) -> float:
    """Какая доля квадрата (x0, y0)–(x1, y1) закрыта клетками (они не перекрываются)."""
    covered = sum(max(0.0, min(x1, cx1) - max(x0, cx0)) * max(0.0, min(y1, cy1) - max(y0, cy0))
                  for cx0, cy0, cx1, cy1 in cells)
    return covered / ((x1 - x0) * (y1 - y0))


def rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix(a, b, t):
    return tuple(p + (q - p) * t for p, q in zip(a, b))


def render(kind: str, color: str, size: int = MASTER) -> bytearray:
    fg = rgb(color)
    bg = mix(fg, (255, 255, 255), TINT)
    half = size / 2
    body, eyes = clawd_cells("#"), clawd_cells("e")
    shape = {"bot": bot, "human": human}.get(kind)
    out = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            x, y = (px + 0.5 - half) / half, (half - py - 0.5) / half
            edge = math.hypot(x, y) - 1
            disc = max(0.0, min(1.0, 0.5 - edge * half))
            ring = max(0.0, min(1.0, 0.5 + (edge + RING) * half))
            pixel = bg
            if shape:
                pixel = mix(pixel, fg, max(0.0, min(1.0, 0.5 - shape(x, y) * half)))
            else:   # пиксельный человечек: доля пикселя под клетками
                box = ((px - half) / half, (half - py - 1) / half, (px + 1 - half) / half, (half - py) / half)
                pixel = mix(mix(pixel, fg, area(body, *box)), rgb(EYES), area(eyes, *box))
            pixel = mix(pixel, fg, ring)
            i = (py * size + px) * 4
            out[i:i + 4] = bytes([round(v) for v in pixel] + [round(255 * disc)])
    return out


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for old in ASSETS.glob("*_*_*.png"):   # прежний формат: каждая аватарка в нескольких размерах
        old.unlink()
    colors = dict(enumerate(PLAYER_COLORS)) | {"neutral": NEUTRAL}
    for kind in ("claude", "bot", "human"):
        for name, color in colors.items():
            write_png(ASSETS / f"{kind}_{name}.png", MASTER, render(kind, color))
    print(f"Готово: {ASSETS}")


if __name__ == "__main__":
    main()
