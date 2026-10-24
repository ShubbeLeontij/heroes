"""Готовит значки и картинки для страницы (heroes/web/assets/icons/): ресурсы, сила, ПО, местность,
действия, чудовища, артефакты.

Запускается один раз, вручную; нужны Pillow, numpy и OpenCV, а для памятки — pdftoppm (poppler-utils):
    pip install pillow numpy opencv-python-headless
    python tools/build_icons.py

Источники:
  docs/boards/elementals.png (планшет в 1000 dpi): дерево, железо и золото — из блока склада,
      «любой ресурс» (сундук) — из линии чудовищ; фон убирается заливкой от краёв рамки;
  docs/boards/humans.png: чудовища из линии чудовищ (monster_<номер>, по порядку силы);
  docs/rules/heroes1-pamyatka.pdf, с. 2 «Обозначения» (векторные, рисуются в 600 dpi): щит силы
      крепости, лавровый венок ПО, значки местности — лес, поле, озеро, горы, — конь (действие
      перемещения), подкова (очки перемещения), молот (строительство), скрещённые мечи (сила войск),
      кубок (любой артефакт), фундамент, победа в сражении, модификация (»), портал, гвозди уровня
      здания (level1–level4); из блока «Фракции» — наука
      гномов, ярость орков, нота и крак наг, проклятый регион нежити;
  docs/rules/heroes1-rules.pdf, с. 16 (600 dpi): гидра наг — голова с рисунка планшета, в круге (hydra);
  heroes/web/assets/map (делает tools/build_map.py): артефакты — их рисунки с карты (artifact_<регион>).
Для ресурсов есть и бледные варианты (<ресурс>_off): ресурсы уже посещённого региона.
Каждый значок — в нескольких высотах: <имя>_<высота>.png.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "docs" / "boards" / "elementals.png"
MEMO = ROOT / "docs" / "rules" / "heroes1-pamyatka.pdf"
OUT = ROOT / "heroes" / "web" / "assets" / "icons"
BOARD_BOXES = {"wood": (1826, 1284, 1906, 1343), "iron": (1826, 1397, 1904, 1454),
               "gold": (1826, 1514, 1890, 1572), "any": (1567, 3359, 1616, 3408)}
# рамки на с. 2 памятки в 600 dpi; glyph — чёрный значок (прозрачность по темноте), иначе цветной
MEMO_BOXES = {"shield": ((2680, 3790, 2840, 3925), False), "laurel": ((2680, 4220, 2850, 4365), False),
              "forest": ((370, 4895, 550, 5075), True), "field": ((360, 5075, 570, 5205), True),
              "lake": ((1100, 4920, 1285, 5040), True), "mountain": ((1100, 5050, 1285, 5180), True),
              "move": ((395, 3915, 530, 4060), False), "mp": ((400, 4075, 520, 4195), False),
              "build": ((405, 4220, 515, 4345), False),
              "power": ((2695, 3655, 2825, 3765), False), "artifact": ((2665, 3335, 2855, 3455), False),
              "foundation": ((2645, 3485, 2885, 3615), False), "victory": ((2685, 3935, 2835, 4065), False),
              "modify": ((2700, 4095, 2825, 4205), False),
              "science": ((2690, 4900, 2840, 5045), False), "rage": ((2690, 5050, 2840, 5200), False),
              "note": ((2690, 5205, 2840, 5340), False), "krak": ((2680, 5345, 2850, 5492), False),
              "curse": ((2680, 5495, 2850, 5645), False), "portal": ((2715, 4385, 2830, 4575), False),
              "level1": ((345, 4345, 385, 4470), False), "level2": ((443, 4345, 515, 4470), False),
              "level3": ((568, 4345, 672, 4470), False), "level4": ((725, 4345, 857, 4470), False)}
RULES = ROOT / "docs" / "rules" / "heroes1-rules.pdf"
HYDRA_BOX = (1880, 3930, 2390, 4440)   # с. 16 правил в 600 dpi: средняя голова гидры
HUMANS = ROOT / "docs" / "boards" / "humans.png"
# чудовища на планшете людей: столбцы рисунков (слева — рамка планшета, её отрезаем) и полоса по высоте
MONSTER_COLUMNS = [(641, 744), (776, 886), (919, 1061), (1104, 1207), (1255, 1353), (1400, 1524), (1553, 1684),
                   (1704, 1848)]
MONSTER_ROWS = (2650, 2773)
MAP = ROOT / "heroes" / "web" / "assets" / "map"
HEIGHTS = [10, 12, 14, 16, 18, 20, 22, 24, 28, 32, 40]
PICTURE_HEIGHTS = [24, 32, 40, 48, 56, 64]   # чудовища и артефакты
MAGIC = (255, 0, 255)


def cut_board(board: Image.Image, box) -> Image.Image:
    """Значок с планшета: фон — всё светлое, до чего доходит заливка от краёв рамки."""
    img = board.crop(box).convert("RGB")
    w, h = img.size
    for xy in [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for x in (0, w - 1) for y in range(h)]:
        if img.getpixel(xy) != MAGIC and min(img.getpixel(xy)) > 200:
            ImageDraw.floodfill(img, xy, MAGIC, thresh=45)
    a = np.asarray(img.convert("RGBA")).copy()
    a[(a[..., :3] == MAGIC).all(-1)] = 0
    return trim(a)


def cut_memo(page: Image.Image, box, glyph: bool) -> Image.Image:
    """Значок из памятки на пергаменте. Чёрный — прозрачность по темноте (края гладкие);
    цветной — всё, что заметно отличается от пергамента, вместе с тем, что внутри контура."""
    a = np.asarray(page.crop(box).convert("RGB")).astype(float)
    paper = np.median(np.concatenate([a[:4].reshape(-1, 3), a[-4:].reshape(-1, 3)]), axis=0)
    if glyph:
        lum = a.mean(-1)
        alpha = np.clip((paper.mean() - lum) / (paper.mean() - 45), 0, 1)
        alpha[alpha < 0.25] = 0
        rgba = np.dstack([np.full(lum.shape + (3,), 30.0), alpha * 255])
        return trim(rgba.astype(np.uint8))
    img = Image.fromarray(a.astype(np.uint8))
    w, h = img.size
    for xy in [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for x in (0, w - 1) for y in range(h)]:
        if img.getpixel(xy) != MAGIC and np.abs(np.array(img.getpixel(xy)) - paper).max() < 40:
            ImageDraw.floodfill(img, xy, MAGIC, thresh=40)
    b = np.asarray(img.convert("RGBA")).copy()
    b[(b[..., :3] == MAGIC).all(-1)] = 0
    # крапинки пергамента, до которых заливка не дошла, — мелкие отдельные пятна: убрать
    n, labels, stats, _ = cv2.connectedComponentsWithStats((b[..., 3] > 0).astype(np.uint8), connectivity=8)
    if n > 1:
        big = stats[1:, cv2.CC_STAT_AREA].max()
        for k in range(1, n):
            if stats[k, cv2.CC_STAT_AREA] < big * 0.05:
                b[labels == k] = 0
    return trim(b)


def trim(a: np.ndarray) -> Image.Image:
    """Обрезать по непрозрачному, отбросив редкие крапинки пергамента по краям."""
    img = Image.fromarray(a, "RGBA")
    solid = a[..., 3] > 0
    rows, cols = np.nonzero(solid.sum(1) > 1)[0], np.nonzero(solid.sum(0) > 1)[0]
    box = (cols.min(), rows.min(), cols.max() + 1, rows.max() + 1)
    return img.crop(box)


def cut_monster(board: Image.Image, x0: int, x1: int) -> Image.Image:
    """Чудовище: всё, что отличается от фона планшета, кроме мелких крапинок."""
    y0, y1 = MONSTER_ROWS
    a = np.asarray(board.crop((x0 - 4, y0, x1 + 4, y1)).convert("RGB")).astype(int)
    paper = np.median(a[:4].reshape(-1, 3), axis=0)
    solid = (np.abs(a - paper).max(-1) > 30).astype(np.uint8)
    solid = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    big = stats[1:, cv2.CC_STAT_AREA].max()
    # под чудовищем справа начинается его добыча (цифры и значки) — пятна, которые начинаются так низко, отбросить
    low = solid.shape[0] - 12
    keep = np.isin(labels, [k for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] >= big * 0.03
                            and stats[k, cv2.CC_STAT_TOP] < low])
    # внутри силуэта светлые места (белки глаз, блики) тоже рисунок: заливаем дыры
    filled = np.zeros_like(solid)
    cnts, _ = cv2.findContours(keep.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    alpha = cv2.GaussianBlur(filled.astype(np.float32) * 255, (3, 3), 0).astype(np.uint8)
    return trim(np.dstack([a.astype(np.uint8), alpha]))


def artifacts() -> dict[str, Image.Image]:
    """Рисунки артефактов с карты: слой объектов в полном размере, рамки — из map.json."""
    info = json.loads((MAP / "map.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "data" / "map.json").read_text(encoding="utf-8"))
    layer = Image.open(MAP / f"objects_{info['size'][0]}.png")
    return {f"artifact_{name}": layer.crop(tuple(info["regions"][name]["object"]))
            for name, r in data["regions"].items() if "artifact" in r["objects"]}


def faded(icon: Image.Image) -> Image.Image:
    """Бледный вариант: почти без цвета и светлее."""
    rgb = ImageEnhance.Color(icon.convert("RGB")).enhance(0.15)
    rgb = Image.blend(rgb, Image.new("RGB", rgb.size, (255, 255, 255)), 0.45)
    rgb.putalpha(icon.getchannel("A").point(lambda v: v * 0.85))
    return rgb


def pdf_page(pdf: Path, number: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-r", "600", "-png", str(pdf), f"{tmp}/page"],
                       check=True)
        return Image.open(next(Path(tmp).glob("page*.png"))).convert("RGB")


def hydra() -> Image.Image:
    """Гидра наг: голова с рисунка в правилах, вписанная в круг (фон рисунка — вода, его не убрать)."""
    img = pdf_page(RULES, 16).crop(HYDRA_BOX).convert("RGBA")
    size = img.width * 4   # маска крупнее и потом уменьшается — край гладкий
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask.resize(img.size, Image.LANCZOS))
    return img


def main() -> None:
    board, page = Image.open(BOARD), pdf_page(MEMO, 2)
    icons = {name: cut_board(board, box) for name, box in BOARD_BOXES.items()}
    icons.update({f"{name}_off": faded(icons[name]) for name in BOARD_BOXES})
    icons.update({name: cut_memo(page, box, glyph) for name, (box, glyph) in MEMO_BOXES.items()})
    humans = Image.open(HUMANS)
    icons.update({f"monster_{i}": cut_monster(humans, x0, x1) for i, (x0, x1) in enumerate(MONSTER_COLUMNS)})
    icons.update(artifacts())
    icons["hydra"] = hydra()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    for name, icon in icons.items():
        heights = PICTURE_HEIGHTS if name.startswith(("monster_", "artifact_")) else HEIGHTS
        for h in sorted(set(heights + PICTURE_HEIGHTS)) if name == "hydra" else heights:   # гидра — и фишка на карте
            w = max(1, round(icon.width * h / icon.height))
            icon.resize((w, h), Image.LANCZOS).save(OUT / f"{name}_{h}.png", optimize=True)
    print(f"готово: {OUT}")


if __name__ == "__main__":
    main()
