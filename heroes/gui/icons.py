"""Значки в тексте окна: ресурсы, сила, ПО, местность, действия.

common.res() записывает ресурс не буквой, а меткой — символом из частной области Юникода
(«2» + метка дерева); так же помечаются сила (щит), ПО (венок), местность и прочее. Тексты
зданий и артефактов «как на планшете» (поле text_board в JSON фракции) пишут значки словами
в фигурных скобках — «4{wood}», — markup() превращает их в метки. Всё, что выводит такие
строки, рисует вместо меток картинки: на холсте — canvas_text() (умеет и переносить строки),
в панелях — RichLabel и rich_radio(). Картинки (готовит tools/build_icons.py) лежат
в assets/icons в нескольких высотах: берётся ближайшая не выше нужной.
Ярость, наука и краки — тоже ресурсы: common.res() пишет их такими же метками ({rage}, {science}, {krak}).
Метки avatar0–avatar3 — аватарки мест за столом (avatars.seat()): common.name_of(…, avatar=True)
ставит такую метку перед именем игрока.
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

from . import avatars

ASSETS = Path(__file__).resolve().parent / "assets" / "icons"
NAMES = ["wood", "iron", "gold", "any", "shield", "laurel", "forest", "field", "lake", "mountain",
         "wood_off", "iron_off", "gold_off", "any_off", "mp", "build", "power", "artifact", "foundation", "victory",
         "modify", "avatar0", "avatar1", "avatar2", "avatar3",
         "science", "rage", "note", "krak", "curse", "hydra"]   # особенности фракций (из памятки и правил)
MARKS = {name: chr(0xE100 + i) for i, name in enumerate(NAMES)}   # _off — бледные ресурсы
KINDS = {mark: kind for kind, mark in MARKS.items()}
CLASS = "[" + "".join(MARKS.values()) + "]"
SPLIT = re.compile(f"({CLASS})")
TOKEN = re.compile(r"\{(\w+)\}")
_heights: dict[str, list[int]] = {}
GAP = 2   # просвет после иконки
AVATARS = {f"avatar{p}" for p in range(4)}
SCALE = {"shield": 1.3} | dict.fromkeys(AVATARS, 1.3)   # щит с мечами крупнее строки, как на планшете; аватарки тоже


def markup(text: str) -> str:
    """«4{wood}» → «4» + метка дерева (неизвестное слово в скобках остаётся как есть)."""
    return TOKEN.sub(lambda m: MARKS.get(m[1], m[0]), text)


def heights(kind: str) -> list[int]:
    """Высоты, в которых есть картинка kind."""
    if kind not in _heights:
        _heights[kind] = sorted(int(p.stem[len(kind) + 1:]) for p in ASSETS.glob(f"{kind}_*.png")
                                if p.stem[len(kind) + 1:].isdigit())
    return _heights[kind]


def has_icons(text: str) -> bool:
    return bool(SPLIT.search(text))


def segments(text: str) -> list[str]:
    """Текст и метки по очереди: «2», метка, « 1», метка…"""
    return [s for s in SPLIT.split(text) if s]


def icon(widget: tk.Misc, kind: str, height: int) -> tk.PhotoImage:
    """Картинка kind высотой не больше height (из имеющихся), одна на окно."""
    if kind in AVATARS:
        return avatars.seat(widget, int(kind[6:]), height)
    root = widget._root()
    cache = root.__dict__.setdefault("_resource_icons", {})
    have = heights(kind)
    h = max([x for x in have if x <= height] or have[:1])
    if (kind, h) not in cache:
        cache[kind, h] = tk.PhotoImage(master=root, file=str(ASSETS / f"{kind}_{h}.png"))
    return cache[kind, h]


class Measurer(tkfont.Font):
    """Шрифт, который помнит размеры: каждое измерение — вызов Tk (доли миллисекунды), а подписи
    карты и планшета перерисовываются с каждым событием партии и меряют одни и те же строки."""

    def __init__(self, **options):
        super().__init__(**options)
        self._widths: dict[str, int] = {}
        self._metrics: dict[tuple, object] = {}

    def measure(self, text, displayof=None):
        if displayof is not None:
            return super().measure(text, displayof)
        if text not in self._widths:
            self._widths[text] = super().measure(text)
        return self._widths[text]

    def metrics(self, *options, **kw):
        if kw:
            return super().metrics(*options, **kw)
        if options not in self._metrics:
            self._metrics[options] = super().metrics(*options)
        return self._metrics[options]


def measurer(widget: tk.Misc, font) -> tkfont.Font:
    """Шрифт для измерений (один на окно и описание шрифта)."""
    cache = widget._root().__dict__.setdefault("_measure_fonts", {})
    key = font if isinstance(font, str) else tuple(font)
    if key not in cache:
        cache[key] = Measurer(root=widget._root(), font=font)
    return cache[key]


def icon_height(widget: tk.Misc, font) -> int:
    """Высота иконки для строки этим шрифтом: чуть меньше межстрочного расстояния."""
    return round(measurer(widget, font).metrics("linespace") * 0.9)


def canvas_text(canvas: tk.Canvas, x: float, y: float, text: str, font, fill: str, anchor: str = "nw",
                halo: str | None = None, tags=(), width: float | None = None) -> tuple[float, float, float, float]:
    """Текст на холсте со значками вместо меток; возвращает его рамку. width — переносить строки по словам
    (число со значком за ним не разрываются); halo — цвет ореола вокруг букв (для подписей на рисунке карты)."""
    offsets = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)] if halo else []
    if not has_icons(text):
        kw = {"width": width} if width else {}
        for dx, dy in offsets:
            canvas.create_text(x + dx, y + dy, text=text, font=font, fill=halo, anchor=anchor, tags=tags, **kw)
        item = canvas.create_text(x, y, text=text, font=font, fill=fill, anchor=anchor, tags=tags, **kw)
        return canvas.bbox(item)
    f = measurer(canvas, font)
    line = f.metrics("linespace")
    h = icon_height(canvas, font)

    def layout(chunk: str) -> list[tuple[object, float]]:
        parts = []
        for seg in segments(chunk):
            if seg in KINDS:
                img = icon(canvas, KINDS[seg], round(h * SCALE.get(KINDS[seg], 1)))
                parts.append((img, img.width() + GAP))
            else:
                parts.append((seg, f.measure(seg)))
        return parts

    # строки: слова (вместе со значками) раскладываются, пока помещаются в width
    lines: list[list[tuple[object, float]]] = [[]]
    used = 0.0
    space = f.measure(" ")
    for word in text.split(" "):
        parts = layout(word)
        w = sum(pw for _, pw in parts)
        if width and lines[-1] and used + space + w > width:
            lines.append([])
            used = 0.0
        if lines[-1]:
            lines[-1].append((" ", space))
            used += space
        lines[-1].extend(parts)
        used += w
    for i, parts in enumerate(lines):  # соседние кусочки текста — одним элементом холста
        merged: list[tuple[object, float]] = []
        for part, pw in parts:
            if merged and isinstance(part, str) and isinstance(merged[-1][0], str):
                merged[-1] = (merged[-1][0] + part, f.measure(merged[-1][0] + part))
            else:
                merged.append((part, pw))
        lines[i] = merged
    widths = [sum(pw for _, pw in parts) for parts in lines]
    block_w, block_h = max(widths), line * len(lines)
    side = "" if anchor == "center" else anchor   # «center» — без сторон света (в слове есть «e» и «n»)
    x0 = x - block_w if "e" in side else x if "w" in side else x - block_w / 2
    y0 = y - block_h if "s" in side else y if "n" in side else y - block_h / 2
    for i, (parts, w) in enumerate(zip(lines, widths)):
        cx = x0 if "w" in side else x0 + block_w - w if "e" in side else x0 + (block_w - w) / 2
        mid = y0 + line * i + line / 2
        for part, pw in parts:
            if isinstance(part, str):
                for dx, dy in offsets:
                    canvas.create_text(cx + dx, mid + dy, text=part, font=font, fill=halo, anchor="w", tags=tags)
                canvas.create_text(cx, mid, text=part, font=font, fill=fill, anchor="w", tags=tags)
            else:
                canvas.create_image(cx, mid, image=part, anchor="w", tags=tags)
            cx += pw
    return x0, y0, x0 + block_w, y0 + block_h


class RichLabel(ttk.Frame):
    """Подпись с иконками вместо меток: кусочки текста и картинки в ряд. Щелчок — on_click.
    frame_style — стиль подложки (под фон, на котором стоит подпись); foreground — цвет текста."""

    def __init__(self, master, text: str, style: str = "TLabel", on_click=None, frame_style: str = "TFrame",
                 foreground: str | None = None):
        super().__init__(master, style=frame_style)
        style_font = ttk.Style(master).lookup(style, "font") or "TkDefaultFont"
        h = icon_height(master, style_font)
        colored = {"foreground": foreground} if foreground else {}
        for seg in segments(text):
            if seg in KINDS:
                kind = KINDS[seg]
                size = round(h * SCALE[kind]) if kind in AVATARS else h
                part = ttk.Label(self, image=icon(master, kind, size), style=style, padding=(0, 0, GAP, 0))
            else:
                part = ttk.Label(self, text=seg, style=style, padding=0, **colored)
            part.pack(side="left")
            if on_click:
                part.bind("<Button-1>", lambda e: on_click())


def rich_radio(master, text: str, **options) -> ttk.Frame:
    """Переключатель, в подписи которого есть иконки: начало подписи — у самого переключателя,
    продолжение с первой иконки (вместе с числом перед ней) — RichLabel рядом; щелчок по ней тоже выбирает."""
    row = ttk.Frame(master)
    m = re.search(rf"\d*{CLASS}", text)
    head, tail = (text[:m.start()], text[m.start():]) if m else (text, "")
    radio = ttk.Radiobutton(row, text=head.rstrip(), **options)
    radio.pack(side="left")
    if tail:
        if head.endswith(" "):
            ttk.Label(row, text=" ", padding=0).pack(side="left")
        RichLabel(row, tail, on_click=radio.invoke).pack(side="left")
    return row
