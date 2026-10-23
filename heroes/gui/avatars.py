"""Аватарки: своя картинка профиля, Claude и бот (в цвете места за столом), человек без аватарки.

Каждая аватарка хранится одной круглой RGBA-картинкой MASTER×MASTER, а в нужном размере (строка текста,
фишка на карте, экран профиля) её даёт image(): уменьшает с усреднением и запоминает. Tk сам
масштабировать не умеет, а Pillow в проекте нет, поэтому PNG читается и пишется на чистом Python.

  assets/avatars/<вид>_<цвет>.png — готовые (рисует tools/build_avatars.py): вид — claude (человечек
      из Claude Code), bot (робот), human (силуэт); цвет — номер места 0–3 или neutral;
  profiles/<id>/avatar.png — своя картинка профиля (import_avatar);
  static["avatars"][место] — своя картинка игрока в партии, PNG в base64 (её шлёт хозяин: местные профили
      и аватарки, присланные гостями в hello); для остальных мест — готовая по виду игрока.
"""
from __future__ import annotations

import base64
import binascii
import math
import struct
import tkinter as tk
import zlib
from pathlib import Path

from ..profiles import Profile

ASSETS = Path(__file__).resolve().parent / "assets" / "avatars"
MASTER = 128
KINDS = ("claude", "bot", "human")
WORK = 384          # до такой стороны свою картинку сначала грубо уменьшает Tk, дальше — усреднение
MAX_ENCODED = 300_000   # длиннее аватарку в base64 по сети не принимаем


# --- PNG без внешних библиотек ---------------------------------------------------------------------

def png_bytes(size: int, rgba: bytes | bytearray) -> bytes:
    """Квадратная RGBA-картинка size×size в PNG."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    stride = size * 4
    raw = b"".join(b"\0" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def write_png(path: str | Path, size: int, rgba: bytes | bytearray) -> None:
    Path(path).write_bytes(png_bytes(size, rgba))


def read_png(data: bytes) -> tuple[int, int, bytearray]:
    """PNG 8 бит RGB/RGBA без чересстрочности (такие пишет png_bytes) → ширина, высота, RGBA.
    ValueError — другой или испорченный файл."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, header = 8, [], None
    while pos + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + length
    if header is None:
        raise ValueError("no IHDR")
    w, h, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace or not 0 < w * h <= 1024 * 1024:
        raise ValueError("unsupported PNG")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise ValueError(str(e)) from None
    bpp = 4 if color == 6 else 3
    stride = w * bpp
    if len(raw) < h * (stride + 1):
        raise ValueError("truncated PNG")
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    for y in range(h):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):   # фильтры строк PNG: None, Sub, Up, Average, Paeth
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        for x in range(w):
            px = line[x * bpp:(x + 1) * bpp]
            out[(y * w + x) * 4:(y * w + x + 1) * 4] = px if bpp == 4 else px + b"\xff"
        prev = line
    return w, h, out


def resize(w: int, h: int, rgba: bytearray, size: int) -> bytearray:
    """Квадрат size×size из картинки w×h: усреднение по площади (с учётом прозрачности)."""
    out = bytearray(size * size * 4)
    xs = [(i * w // size, max(i * w // size + 1, (i + 1) * w // size)) for i in range(size)]
    ys = [(i * h // size, max(i * h // size + 1, (i + 1) * h // size)) for i in range(size)]
    for y, (y0, y1) in enumerate(ys):
        for x, (x0, x1) in enumerate(xs):
            r = g = b = a = 0
            for sy in range(y0, y1):
                row = sy * w * 4
                for i in range(row + x0 * 4, row + x1 * 4, 4):
                    pa = rgba[i + 3]
                    r, g, b, a = r + rgba[i] * pa, g + rgba[i + 1] * pa, b + rgba[i + 2] * pa, a + pa
            n = (y1 - y0) * (x1 - x0)
            i = (y * size + x) * 4
            out[i:i + 4] = bytes((r // a, g // a, b // a, a // n)) if a else bytes(4)
    return out


def circle_alpha(size: int, x: int, y: int) -> float:
    """Доля пикселя (x, y) внутри круга во весь квадрат — сглаженный край."""
    r = size / 2
    return max(0.0, min(1.0, r - math.hypot(x + 0.5 - r, y + 0.5 - r) + 0.5))


# --- картинки для окна -------------------------------------------------------------------------------

def _photo(widget: tk.Misc, key: tuple, load, size: int, alpha: float = 1.0) -> tk.PhotoImage:
    """Аватарка size×size из источника key (load() → байты PNG), одна на окно, размер и прозрачность
    (alpha < 1 — полупрозрачная: фишка на карте под указателем мыши)."""
    root = widget._root()
    cache = root.__dict__.setdefault("_avatars", {})
    if (key, size, alpha) not in cache:
        if key not in cache:
            cache[key] = read_png(load())
        w, h, rgba = cache[key]
        pixels = bytearray(rgba if (w, h) == (size, size) else resize(w, h, rgba, size))
        if alpha < 1:
            pixels[3::4] = bytes(round(a * alpha) for a in pixels[3::4])
        png = png_bytes(size, pixels)
        cache[key, size, alpha] = tk.PhotoImage(master=root, data=base64.b64encode(png).decode(), format="png")
    return cache[key, size, alpha]


def _file(widget: tk.Misc, path: Path, size: int, alpha: float = 1.0) -> tk.PhotoImage:
    return _photo(widget, ("file", str(path), path.stat().st_mtime_ns), path.read_bytes, size, alpha)


def builtin(widget: tk.Misc, kind: str, color: int | str, size: int, alpha: float = 1.0) -> tk.PhotoImage:
    """Готовая аватарка: kind — claude, bot или human; color — номер места или "neutral"."""
    color = color % 4 if isinstance(color, int) else color
    return _file(widget, ASSETS / f"{kind}_{color}.png", size, alpha)


def for_profile(widget: tk.Misc, profile: Profile | None, size: int) -> tk.PhotoImage:
    """Аватарка профиля; без своей картинки — силуэт человека."""
    path = profile.avatar if profile else None
    try:
        return _file(widget, path, size) if path else builtin(widget, "human", "neutral", size)
    except (OSError, ValueError):   # картинка испорчена — силуэт
        return builtin(widget, "human", "neutral", size)


def for_player(widget: tk.Misc, player: dict, seat: int, size: int, profiles: dict[str, Profile]) -> tk.PhotoImage:
    """Аватарка игрока из записи о партии: Claude и бот — готовые в цвете места, человек — своя из профиля
    этого компьютера, если он за ним играл, иначе силуэт в цвете места."""
    kind = player.get("kind", "human")
    if kind in ("claude", "bot"):
        return builtin(widget, kind, seat, size)
    profile = profiles.get(player.get("profile") or "")
    return for_profile(widget, profile, size) if profile else builtin(widget, "human", seat, size)


def use_game(widget: tk.Misc, static: dict | None) -> None:
    """Запомнить, кто сидит за столом текущей партии: по этим данным seat() рисует аватарки мест."""
    widget._root().__dict__["_seat_avatars"] = static


def seat(widget: tk.Misc, p: int, size: int, alpha: float = 1.0) -> tk.PhotoImage:
    """Аватарка места p в текущей партии: своя картинка игрока (из static) или готовая по его виду;
    alpha < 1 — полупрозрачная."""
    static = widget._root().__dict__.get("_seat_avatars") or {}
    own = static.get("avatars") or []   # у хозяина прежней версии этого поля нет
    encoded = own[p] if p < len(own) else None
    if encoded:
        try:
            return _photo(widget, ("b64", encoded), lambda: decode(encoded), size, alpha)
        except (ValueError, tk.TclError):
            pass   # испорченная картинка гостя — готовая аватарка
    kinds = static.get("kinds") or []
    kind = kinds[p] if p < len(kinds) else "human"
    return builtin(widget, kind if kind in ("claude", "bot") else "human", p, size, alpha)


# --- своя картинка профиля -----------------------------------------------------------------------------

def decode(encoded: str) -> bytes:
    """Аватарка из base64 (присланная по сети); ValueError — не она."""
    if not isinstance(encoded, str) or len(encoded) > MAX_ENCODED:
        raise ValueError("bad avatar")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("bad avatar") from None


def _pixels(photo: tk.PhotoImage, background: str) -> list[list[tuple[int, int, int]]]:
    """Цвета пикселей построчно; прозрачные — цветом background."""
    rows = photo.tk.splitlist(photo.tk.call(photo, "data", "-background", background))
    return [[(int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)) for c in photo.tk.splitlist(row)] for row in rows]


def import_avatar(widget: tk.Misc, source: str | Path, profile: Profile, background: str = "#ffffff") -> None:
    """Сделать картинку source (PNG, GIF, PPM) аватаркой профиля: квадрат по центру, усреднение до MASTER,
    круглая маска. ValueError — файл не читается."""
    try:
        photo = tk.PhotoImage(master=widget._root(), file=str(source))
    except tk.TclError as e:
        raise ValueError(f"Не удалось открыть картинку: {e}. Подойдёт PNG или GIF.") from None
    w, h = photo.width(), photo.height()
    side = min(w, h)
    if side == 0:
        raise ValueError("Картинка пустая.")
    x0, y0 = (w - side) // 2, (h - side) // 2
    k = max(1, side // WORK)
    square = tk.PhotoImage(master=widget._root())
    square.tk.call(square, "copy", photo, "-from", x0, y0, x0 + side, y0 + side, "-subsample", k, k)
    pixels = _pixels(square, background)
    side = min(len(pixels), min(len(row) for row in pixels))   # после subsample может выйти на пиксель меньше
    rgba = bytearray(b"".join(bytes((*px, 255)) for row in pixels[:side] for px in row[:side]))
    out = resize(side, side, rgba, MASTER)
    for y in range(MASTER):
        for x in range(MASTER):
            out[(y * MASTER + x) * 4 + 3] = round(255 * circle_alpha(MASTER, x, y))
    remove_avatar(profile)
    profile.path.mkdir(parents=True, exist_ok=True)
    write_png(profile.path / "avatar.png", MASTER, out)


def remove_avatar(profile: Profile) -> None:
    for path in [profile.path / "avatar.png", *profile.path.glob("avatar_*.png")]:   # avatar_<N> — прежний формат
        path.unlink(missing_ok=True)
