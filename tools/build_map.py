"""Готовит картинки карты для страницы (heroes/web/assets/map/) из оригинальной карты «Героев».

Запускается один раз, вручную; игре и странице ничего из этого не нужно — только результат.
Нужны Pillow, numpy и OpenCV с модулями contrib:
    pip install pillow numpy opencv-contrib-python-headless
    python tools/build_map.py

Источники (docs/boards/):
  map.jpg     — карта без подписей из правил «Героев II» (с. 11), 1420×953;
  map-ink.png — канал чёрной краски той же карты из CMYK-версии в правилах «Героев» (с. 9).
                На нём объекты (крепости, порталы, артефакты, фундаменты) — плотная чёрная
                графика, а местность — редкие линии; по нему объекты и вырезаются.

Что получается (heroes/web/assets/map/):
  base_<w>.png    — карта без объектов (места под ними дорисованы по окружающему фону),
                    вне карты прозрачно; w — ширина, несколько масштабов;
  objects_<w>.png — слой объектов того же размера: прозрачный, кроме самих объектов;
  shade_<w>.png   — атлас силуэтов регионов: верхняя половина — полупрозрачная светлая
                    (притушить регион при выборе), нижняя — непрозрачная цвета фона карты
                    (стереть регион, вычеркнутый при таком числе игроков);
  map.json        — размеры, масштабы, границы регионов (многоугольники в координатах
                    исходной карты), рамки объектов, места подписей, центры регионов (для стрелок
                    маршрута) и раскладка атласов.
Страница (heroes/web/static/js/map.js) рисует основу, а поверх — объекты, которые ещё на месте:
захваченная крепость и взятый артефакт с карты исчезают.

Границы регионов найдены водоразделом (watershed) по сглаженной картинке от затравок:
сам объект региона и несколько точек внутри (SEEDS). Скрипт сверяет полученные соседства
с data/map.json и печатает расхождения.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "boards"
OUT = ROOT / "heroes" / "web" / "assets" / "map"
WASH = (255, 255, 255, 150)   # притушенный регион
GONE = (239, 232, 214, 255)    # вычеркнутый регион: стёрт до фона карты
SCALES = [1.0, 0.9, 0.8, 0.72, 0.64, 0.56, 0.48, 0.4]

# Рамки объектов (x0, y0, x1, y1) на map.jpg; внутри рамки объект ищется по чёрной краске.
OBJECTS = {
    "Асандер": (105, 100, 210, 170), "Бринлор": (385, 115, 470, 215), "Вернесс": (805, 65, 855, 145),
    "Гленнфор": (1095, 72, 1178, 165), "Дорталь": (155, 235, 222, 310), "Есмор": (530, 205, 635, 270),
    "Золен": (925, 185, 980, 265), "Мослин": (728, 240, 822, 355), "Нокс": (1072, 305, 1175, 370),
    "Карракс": (335, 310, 380, 390), "Лорвельд": (615, 385, 715, 450), "Икара": (200, 400, 275, 475),
    "Таскар": (925, 405, 1000, 480), "Флисс": (1186, 436, 1260, 499), "Содмур": (448, 465, 522, 510),
    "Пиклин": (230, 525, 335, 592), "Ролгой": (695, 520, 755, 610), "Улин": (878, 558, 975, 622),
    "Оррид": (112, 538, 160, 616), "Шог Ид": (1155, 590, 1210, 660), "Цурт": (398, 716, 475, 766),
    "Чарил": (870, 668, 918, 746), "Яскер": (1140, 713, 1245, 781), "Хагмат": (182, 793, 283, 858),
    "Эльри": (750, 818, 853, 881), "Юноро": (980, 748, 1085, 826), "Щерс": (465, 803, 560, 906),
}

# Точки внутри регионов — затравки водораздела вдобавок к объекту.
SEEDS = {
    "Оррид": [(60, 620), (200, 640), (300, 680), (100, 700), (250, 600)],
    "Хагмат": [(60, 790), (250, 760), (300, 830), (150, 860)],
    "Икара": [(40, 450), (120, 400), (160, 500), (290, 470), (330, 445), (400, 445), (450, 440), (470, 425)],
    "Дорталь": [(100, 260), (260, 260), (330, 250), (60, 300)],
    "Асандер": [(60, 150), (300, 150), (500, 90), (250, 60)],
    "Бринлор": [(330, 180), (470, 220), (520, 160)],
    "Вернесс": [(600, 100), (700, 80), (900, 90), (870, 150)],
    "Есмор": [(520, 205), (450, 240), (650, 190), (800, 170), (560, 260)],
    "Гленнфор": [(1300, 120), (1380, 150), (1100, 100), (1000, 160)],
    "Золен": [(900, 180), (1050, 200), (1150, 200), (850, 230), (1000, 280)],
    "Нокс": [(1340, 330), (1400, 300), (1250, 280), (1000, 330), (1150, 330), (1300, 350), (1400, 400),
             (1350, 420)],
    "Мослин": [(650, 290), (850, 300), (950, 320), (600, 320)],
    "Карракс": [(300, 330), (450, 340), (500, 380), (270, 420)],
    "Лорвельд": [(530, 400), (520, 430), (560, 380), (700, 400), (800, 390), (650, 440)],
    "Таскар": [(760, 450), (900, 420), (1050, 420), (800, 480), (1000, 470)],
    "Ролгой": [(790, 530), (600, 600), (450, 640), (760, 500), (800, 540), (700, 620)],
    "Содмур": [(420, 480), (600, 470), (680, 500), (350, 500)],
    "Пиклин": [(200, 540), (350, 550), (450, 570), (300, 590)],
    "Улин": [(850, 540), (1000, 540), (1150, 520), (950, 600), (800, 630)],
    "Флисс": [(1400, 450), (1170, 480), (1150, 505), (1100, 460), (1150, 430), (1300, 420)],
    "Шог Ид": [(1150, 600), (1300, 560), (1400, 580), (1100, 640), (1250, 640)],
    "Цурт": [(350, 720), (500, 700), (650, 720), (550, 760)],
    "Чарил": [(800, 690), (900, 700), (1000, 670), (780, 730)],
    "Яскер": [(1250, 720), (1350, 700), (1400, 760), (1300, 780), (1150, 760)],
    "Юноро": [(900, 790), (1050, 800), (1200, 815)],
    "Эльри": [(700, 800), (800, 850), (860, 800), (750, 900)],
    "Щерс": [(400, 830), (550, 780), (600, 880), (350, 900)],
}
OUTSIDE = [(3, 3), (1416, 949), (700, 949), (1300, 900), (3, 949), (40, 60)]  # белое поле вокруг карты

# Места подписей (центр названия) — как на заполненной карте планшета людей
# (пересчитаны с планшета: x' = 1.066·x + 22, y' = 1.0615·y + 6).
LABELS = {
    "Асандер": (275, 83), "Вернесс": (698, 94), "Гленнфор": (1216, 96), "Бринлор": (510, 151),
    "Есмор": (685, 184), "Золен": (1061, 215), "Дорталь": (266, 256), "Мослин": (877, 312),
    "Нокс": (1221, 296), "Карракс": (448, 321), "Лорвельд": (569, 375), "Икара": (329, 441),
    "Таскар": (861, 431), "Флисс": (1302, 442), "Содмур": (587, 484), "Пиклин": (420, 563),
    "Ролгой": (546, 611), "Улин": (1035, 537), "Оррид": (200, 614), "Шог Ид": (1262, 605),
    "Цурт": (604, 699), "Чарил": (1014, 668), "Яскер": (1288, 717), "Хагмат": (136, 777),
    "Эльри": (727, 781), "Юноро": (1131, 797), "Щерс": (602, 855),
}


def load():
    rgb = np.asarray(Image.open(SRC / "map.jpg").convert("RGB"))
    h, w = rgb.shape[:2]
    ink = 255 - np.asarray(Image.open(SRC / "map-ink.png").convert("L"))
    ink = cv2.resize(ink, (w, h), interpolation=cv2.INTER_LINEAR)
    return rgb, ink


def object_masks(rgb, ink):
    """Маска каждого объекта: плотная чёрная графика в рамке + белая обводка-«наклейка» вокруг."""
    h, w = rgb.shape[:2]
    mn, mx = rgb.min(2).astype(int), rgb.max(2).astype(int)
    white = ((mn > 190) & (mx - mn < 45)).astype(np.uint8)
    disk = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r))
    masks = {}
    for name, (x0, y0, x1, y1) in OBJECTS.items():
        sub = (ink[y0:y1, x0:x1] > 70).astype(np.uint8)
        sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, disk(7))
        blob = fill([c for c in contours(sub) if cv2.contourArea(c) > 40], sub.shape)
        full = np.zeros((h, w), np.uint8)
        full[y0:y1, x0:x1] = blob
        full |= cv2.dilate(full, disk(9)) & white
        full = cv2.morphologyEx(full, cv2.MORPH_CLOSE, disk(5))
        masks[name] = fill(contours(full), full.shape)
    return masks


def contours(mask):
    return cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]


def fill(cnts, shape):
    out = np.zeros(shape, np.uint8)
    cv2.drawContours(out, cnts, -1, 1, -1)
    return out


def regions(rgb, masks):
    """Разметка регионов: 1..27 — регионы в порядке OBJECTS, 28 — поле вокруг карты."""
    h, w = rgb.shape[:2]
    names = list(OBJECTS)
    outside_id = len(names) + 1
    pure = (rgb.min(2) > 245).astype(np.uint8)
    _, lab = cv2.connectedComponents(pure, connectivity=4)
    outside = np.zeros((h, w), np.uint8)
    for x, y in OUTSIDE:
        if lab[y, x]:
            outside |= (lab == lab[y, x]).astype(np.uint8)
    markers = np.zeros((h, w), np.int32)
    for i, name in enumerate(names, 1):
        markers[cv2.erode(masks[name], np.ones((5, 5), np.uint8)) > 0] = i
        for x, y in SEEDS[name]:
            cv2.circle(markers, (x, y), 5, i, -1)
    markers[cv2.erode(outside, np.ones((9, 9), np.uint8)) > 0] = outside_id
    smooth = cv2.pyrMeanShiftFiltering(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), 12, 30)
    ws = cv2.watershed(smooth, markers)
    # у региона — одна связная область, поле вокруг карты касается края картинки;
    # осколки и линии раздела отходят соседям
    ws[ws == -1] = 0
    n, cc = cv2.connectedComponents((ws == outside_id).astype(np.uint8), connectivity=4)
    edge = set(np.unique(np.concatenate([cc[1], cc[-2], cc[:, 1], cc[:, -2]])))  # край watershed метит -1
    for k in range(1, n):
        if k not in edge:
            ws[cc == k] = 0
    for i in range(1, outside_id):
        n, cc, stats, _ = cv2.connectedComponentsWithStats((ws == i).astype(np.uint8), connectivity=4)
        if n > 2:
            keep = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
            ws[(cc > 0) & (cc != keep)] = 0
    while (ws == 0).any():
        grown = cv2.dilate(ws.astype(np.uint16), np.ones((3, 3), np.uint8)).astype(np.int32)
        ws[ws == 0] = grown[ws == 0]
    return ws, outside_id


def check_adjacency(ws, names):
    """Соседства по разметке против data/map.json (без порталов: они соседи не по границе)."""
    data = json.loads((ROOT / "data" / "map.json").read_text(encoding="utf-8"))
    found = set()
    for i, a in enumerate(names, 1):
        grown = cv2.dilate((ws == i).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        for j, b in enumerate(names, 1):
            if j > i and np.count_nonzero(grown & (ws == j)) >= 20:
                found.add(frozenset((a, b)))
    expected = {frozenset(pair) for pair in data["adjacent"]}
    problems = 0
    for pair in sorted(expected - found, key=sorted):
        print("нет общей границы на картинке:", " – ".join(sorted(pair)))
        problems += 1
    for pair in sorted(found - expected, key=sorted):
        print("лишняя граница на картинке:", " – ".join(sorted(pair)))
        problems += 1
    return problems


def shade_atlas(ws, names, size):
    """Силуэты регионов в масштабе size, уложенные полками в атлас; раскладка — [ax, ay, w, h, x, y]."""
    width = size[0]
    items = []
    for i, name in enumerate(names, 1):
        alpha = cv2.resize((ws == i).astype(np.float32), size, interpolation=cv2.INTER_AREA)
        ys, xs = np.nonzero(alpha > 0.02)
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        items.append((name, alpha[y0:y1, x0:x1], int(x0), int(y0)))
    items.sort(key=lambda it: -it[1].shape[0])
    layout, x, y, shelf = {}, 0, 0, 0
    for name, a, x0, y0 in items:
        h, w = a.shape
        if x + w > width:
            x, y, shelf = 0, y + shelf + 1, 0
        layout[name] = [x, y, w, h, x0, y0]
        x, shelf = x + w + 1, max(shelf, h)
    half = y + shelf
    atlas = np.zeros((half * 2, width, 4), np.uint8)
    for name, a, _, _ in items:
        ax, ay, w, h, _, _ = layout[name]
        for top, color in ((0, WASH), (half, GONE)):
            block = atlas[top + ay:top + ay + h, ax:ax + w]
            block[..., :3] = color[:3]
            block[..., 3] = np.maximum(block[..., 3], (a * color[3]).round().astype(np.uint8))
    atlas[atlas[..., 3] == 0] = 0
    return Image.fromarray(atlas, "RGBA"), {"half": half, "regions": layout}


def center(mask) -> list[int]:
    """Самая «глубокая» точка региона (дальше всего от границы) — сюда приходят стрелки маршрута."""
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    y, x = np.unravel_index(np.argmax(dist), dist.shape)
    return [int(x), int(y)]


def polygon(mask):
    cnt = max(contours(mask), key=cv2.contourArea)
    return [[int(x), int(y)] for [[x, y]] in cv2.approxPolyDP(cnt, 1.2, True)]


def main():
    rgb, ink = load()
    h, w = rgb.shape[:2]
    masks = object_masks(rgb, ink)
    names = list(OBJECTS)
    ws, outside_id = regions(rgb, masks)
    problems = check_adjacency(ws, names)

    # основа: объекты убраны, места под ними дорисованы по окружающему фону
    hole = np.zeros((h, w), np.uint8)
    for m in masks.values():
        hole |= m
    hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    base = bgr.copy()
    cv2.xphoto.inpaint(bgr, (1 - hole) * 255, base, cv2.xphoto.INPAINT_SHIFTMAP)
    base = cv2.cvtColor(base, cv2.COLOR_BGR2RGB)
    # вне карты — прозрачно (белое поле; айсберги и вершины у края остаются)
    clear = (ws == outside_id) & (rgb.min(2) > 225)
    base_alpha = cv2.GaussianBlur(np.where(clear, 0, 255).astype(np.uint8), (3, 3), 0)
    # слой объектов с мягким краем
    layer_alpha = np.zeros((h, w), np.uint8)
    for m in masks.values():
        layer_alpha = np.maximum(layer_alpha, cv2.GaussianBlur(m * 255, (3, 3), 0))

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    rgb = rgb.copy()
    rgb[layer_alpha == 0] = 0
    base[base_alpha == 0] = 0
    base_img = Image.fromarray(np.dstack([base, base_alpha]), "RGBA")
    layer_img = Image.fromarray(np.dstack([rgb, layer_alpha]), "RGBA")
    widths, shades = [], {}
    for s in SCALES:
        size = (round(w * s), round(h * s))
        widths.append(size[0])
        for img, stem in ((base_img, "base"), (layer_img, "objects")):
            out = img if s == 1 else img.resize(size, Image.LANCZOS)
            out.save(OUT / f"{stem}_{size[0]}.png", optimize=True)
        atlas, shades[str(size[0])] = shade_atlas(ws, names, size)
        atlas.save(OUT / f"shade_{size[0]}.png", optimize=True)

    info = {
        "_comment": "Сделано tools/build_map.py. Координаты — в пикселях исходной карты (size).",
        "size": [w, h],
        "widths": widths,
        "shades": shades,
        "regions": {},
    }
    for i, name in enumerate(names, 1):
        ys, xs = np.nonzero(masks[name])
        info["regions"][name] = {
            "polygon": polygon((ws == i).astype(np.uint8)),
            "object": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "label": list(LABELS[name]),
            "center": center((ws == i).astype(np.uint8)),
        }
    (OUT / "map.json").write_text(json.dumps(info, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"готово: {OUT} ({total // 1024} КБ), расхождений с соседствами: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
