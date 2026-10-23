"""Планшет игрока: склад с отметками ПО, войска, здания по уровням, артефакты, линия чудовищ."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .common import (ACCENT, GOOD, INK, LEVELS, MUTED, PANEL_BG, RESOURCE_COLORS, RESOURCE_NAMES, artifact_condition,
                     color_of, font, monster_line, name_of, res)
from .icons import AVATARS, MARKS, canvas_text, icon, markup

BUILT, CAN_BUILD, LOCKED = "#cfe8c4", "#fff1c2", "#eeeae2"
PICKED = "#ffd8a8"   # здание, выбранное щелчком для строительства
MAX_COLUMNS = 5   # больше карточек зданий в ряд не помещается
CATEGORIES = {"buildings": "здания", "foundations": "фундаменты", "resources": "склад",
              "battles": "сражения", "artifacts": "артефакты"}


def condition_icons(cond: dict) -> str:
    """Условие артефакта значками, как на планшете: «12» + золото, «5» + победа в сражении…"""
    kind = cond["condition"]
    if kind == "pay":
        return res(cond["cost"])
    return f"{cond['count']}" + MARKS[{"foundationsBuilt": "foundation", "battleWins": "victory",
                                        "mountainsVisited": "mountain"}[kind]]


def hire_needs(faction: dict, uid: str, built: list[str]) -> str:
    """Почему войско пока нельзя нанять: какое здание для этого нужно."""
    names = [b["name"] for bid, b in faction["buildings"].items() if uid in b.get("hires", ()) and bid not in built]
    return f"нужно здание «{names[0]}»" if names else "пока недоступен"


def strength_parts(unit: dict, base: int) -> list[tuple[str, str]]:
    """Сила войска, как на планшете: «1»2» — во что её превращают здания, артефакты и свойства фракции
    (с условием: за ресурс, в горах, против крепости). Яркое — то, что можно применить прямо сейчас;
    исходная сила бледнеет, когда её заменило построенное здание. Без модификаций — нынешняя сила."""
    power = MARKS["power"]
    mods = unit.get("mods") or []
    if not mods:
        return [(f"{unit['strength']}{power}", INK)]
    replaced = any(m["active"] and m["kind"] == "set" for m in mods)
    parts = [(f"{base}{power}", MUTED if replaced else INK)]
    for m in mods:
        note = {"pay": f" за {res(m.get('cost', {}))}", "terrain": f" {MARKS.get(m.get('terrain'), '')}",
                "fortress": " против крепости"}.get(m["kind"], "")
        parts += [(MARKS["modify"], INK), (f"{m['value']}{power}{note}", INK if m["active"] else MUTED)]
    return parts


def building_columns(buildings: dict) -> dict[str, int]:
    """Колонки зданий на планшете, как на печатном: здание уровнем ниже ставится под то, которому
    оно нужно, — тогда стрелки зависимостей идут почти вертикально. Уровень IV — по порядку."""
    columns: dict[str, int] = {}
    for level in (4, 3, 2, 1):
        taken: set[int] = set()
        row = [bid for bid, b in buildings.items() if b["level"] == level]
        for bid in row:  # сначала — под первым зданием, которому это нужно, если там свободно
            wanted = [columns[d] for d, b in buildings.items() if bid in b["requires"] and d in columns]
            free = [col for col in wanted if col not in taken]
            if free:
                columns[bid] = free[0]
                taken.add(free[0])
        for bid in row:  # остальные — в первые свободные колонки
            if bid not in columns:
                col = next(i for i in range(len(buildings)) if i not in taken)
                columns[bid] = col
                taken.add(col)
    return columns


class BoardView(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, background=PANEL_BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.canvas.bind(seq, self._wheel)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.static: dict | None = None
        self.state: dict | None = None
        self.player: int | None = None
        self.you: int | None = None
        self.outdated = False   # состояние новее нарисованного (скрытая вкладка)
        # выбор здания щелчком (панель строительства): чьи здания, какие можно выбрать, какое выбрано
        self.pick_owner: int | None = None
        self.pick_options: set[str] | None = None
        self.picked: str | None = None
        self.on_pick: Callable[[str | None], None] | None = None
        self.pick_boxes: dict[str, tuple[float, float, float, float]] = {}
        self.buildings_top = 0.0
        # выбор чудовища щелчком по линии (бой с чудовищем): чья линия, какие можно выбрать, какое выбрано
        self.monster_owner: int | None = None
        self.monster_options: set[int] | None = None
        self.monster_picked: int | None = None
        self.on_monster: Callable[[int], None] | None = None
        self.monster_boxes: dict[int, tuple[float, float, float, float]] = {}
        self.monsters_top = 0.0

    def _wheel(self, event) -> None:
        step = -1 if event.num == 4 or getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")

    def select_mode(self, owner: int | None, options: set[str] | None = None, selected: str | None = None,
                    on_pick: Callable[[str | None], None] | None = None) -> None:
        """Здания игрока owner из options можно выбрать щелчком; повторный щелчок снимает выбор (on_pick(None)).
        None — выбора нет."""
        if owner is None and self.pick_owner is None:
            return   # выбора и не было — перерисовывать нечего
        self.pick_owner, self.pick_options, self.picked, self.on_pick = owner, options, selected, on_pick
        self.redraw()
        if options and self.player == owner:   # прокрутить к зданиям
            total = float(self.canvas.cget("scrollregion").split()[3] or 1)
            self.canvas.yview_moveto(max(0.0, self.buildings_top - 30) / total)

    def set_picked(self, bid: str | None) -> None:
        self.picked = bid
        self.redraw()

    def select_monsters(self, owner: int | None, options: set[int] | None = None,
                        on_pick: Callable[[int], None] | None = None) -> None:
        """Чудовища линии игрока owner с номерами из options можно выбрать щелчком. None — выбора нет."""
        if owner is None and self.monster_owner is None:
            return
        self.monster_owner, self.monster_options, self.on_monster = owner, options, on_pick
        self.monster_picked = None
        self.redraw()
        if options and self.player == owner:   # прокрутить к линии чудовищ
            total = float(self.canvas.cget("scrollregion").split()[3] or 1)
            self.canvas.yview_moveto(max(0.0, self.monsters_top - 30) / total)

    def set_monster(self, index: int | None) -> None:
        self.monster_picked = index
        self.redraw()

    def picking_monster(self) -> bool:
        return bool(self.monster_options) and self.player is not None and self.player == self.monster_owner

    def monster_at(self, event) -> int | None:
        if not self.picking_monster():
            return None
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        return next((i for i, (x0, y0, x1, y1) in self.monster_boxes.items() if x0 <= x <= x1 and y0 <= y <= y1), None)

    def picking(self) -> bool:
        return bool(self.pick_options) and self.player is not None and self.player == self.pick_owner

    def building_at(self, event) -> str | None:
        if not self.picking():
            return None
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        return next((bid for bid, (x0, y0, x1, y1) in self.pick_boxes.items() if x0 <= x <= x1 and y0 <= y <= y1), None)

    def _click(self, event) -> None:
        bid = self.building_at(event)
        if bid is not None and self.on_pick:
            self.picked = None if bid == self.picked else bid
            self.on_pick(self.picked)
            self.redraw()
        index = self.monster_at(event)
        if index is not None and self.on_monster:
            self.monster_picked = index
            self.on_monster(index)
            self.redraw()

    def _motion(self, event) -> None:
        self.canvas.configure(cursor="hand2" if self.building_at(event) or self.monster_at(event) is not None else "")

    def show(self, static: dict, state: dict, player: int | None, you: int | None = None) -> None:
        """player — чей планшет показать, you — кто смотрит."""
        self.static, self.state, self.player, self.you = static, state, player, you
        self.outdated = False
        self.redraw()

    def stale(self, static: dict, state: dict, player: int | None, you: int | None = None) -> None:
        """Новое состояние для скрытого планшета (вкладка не открыта): нарисуется, когда её откроют."""
        self.static, self.state, self.player, self.you = static, state, player, you
        self.outdated = True

    def text(self, x, y, text, size=10, *styles, fill=INK, anchor="nw", width=None):
        return self.canvas.create_text(x, y, text=text, font=font(size, *styles), fill=fill, anchor=anchor,
                                       width=width)

    def rich(self, x, y, text, size=10, *styles, fill=INK, anchor="nw", width=None) -> tuple[float, float, float, float]:
        """Текст со значками (метки icons.MARKS); width — переносить строки. Возвращает рамку текста."""
        return canvas_text(self.canvas, x, y, text, font(size, *styles), fill, anchor=anchor, width=width)

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        if not self.static or not self.state or self.player is None:
            return
        static, state, p = self.static, self.state, self.player
        pl = state["players"][p]
        faction = static["factions"][pl["faction"]]
        width = max(360, c.winfo_width())
        x, y = 12, 10

        # чей планшет — в списке над ним; регион, ПО и победы — в сводке «Все игроки»
        # Склад; уникальные ресурсы фракции (ярость) — такими же ячейками во втором ряду
        self.text(x, y, "Склад", 10, "bold")
        y += 18
        col = (width - 24) / 3
        no_vp = faction.get("no_resource_vp")
        boxes = [(k, pl["resources"][k], [] if no_vp else static["resource_vp"]) for k in ("wood", "iron", "gold")]
        boxes += [(k, pl.get("tokens", {}).get(k, 0), steps) for k, steps in faction.get("tokens", {}).items()]
        for start in range(0, len(boxes), 3):
            for i, (k, amount, steps) in enumerate(boxes[start:start + 3]):
                self.stock_box(x + i * col, y, col, k, amount, steps, pl["resource_marks"].get(k, []))
            y += 52
        if no_vp:
            self.text(x, y - 4, "За ресурсы на складе ПО не начисляются.", 8, fill=MUTED)
            y += 14
        y = self.specials(x, y + 4, width, pl, faction)

        # Свойства фракции — блоки планшета, которые действуют всю партию
        rules = faction.get("rules", {})
        if rules:
            self.text(x, y, "Свойства фракции", 10, "bold")
            y += 20
            for rule in rules.values():
                top = y
                card = c.create_rectangle(x, top, width - 12, top + 10, fill="white", outline="#cbbfa9")
                self.text(x + 6, top + 4, rule["name"], 9, "bold")
                bottom = self.rich(x + 6, top + 22, markup(rule["text_board"]), 8, width=width - 30)[3]
                c.coords(card, x, top, width - 12, bottom + 5)
                y = bottom + 11
            y += 6

        # Войска
        self.rich(x, y, f"Войска — общая сила {pl['army_strength']}{MARKS['power']}", 10, "bold")
        y += 20
        for uid, u in pl["units"].items():
            name = faction["units"][uid]["name"]
            if uid in faction.get("resource_units", ()):
                status = "обмен ресурсов в начале строительства"
            elif uid == faction.get("hydra"):
                status = "призыв нотами"
            elif u["hireable"]:
                status = "наём " + res(u["hire_cost"])
            elif u["hire_cost"]:   # цена видна заранее; нанимать можно после постройки здания
                status = "наём " + res(u["hire_cost"]) + " — " + hire_needs(faction, uid, pl["built"])
            else:
                status = "наём недоступен"
            self.text(x, y, f"{name}", 10, "bold")
            self.text(x + 110, y, f"×{u['count']}", 10, "bold", fill=INK if u["count"] else MUTED)
            tx = x + 160
            for text, fill in strength_parts(u, faction["units"][uid]["strength"]):
                tx = self.rich(tx, y, text, 9, "bold", fill=fill)[2] + 3
            sx = max(x + 250, tx + 12)
            bottom = self.rich(sx, y, status, 9, fill=GOOD if u["hireable"] else MUTED, width=width - sx - 12)[3]
            y = max(y + 18, bottom + 3)
        y += 10

        # Здания
        # цвет карточки: построено / хватает ресурсов / пока нет; стрелка — от нужного здания к следующему
        self.buildings_top = y
        self.text(x, y, "Здания", 10, "bold")
        picking = self.picking()
        if picking:
            self.text(x + 70, y + 1, "щёлкните здание, чтобы выбрать его для строительства", 9, "bold", fill=ACCENT)
        y += 20
        self.pick_boxes = {}
        built, can = set(pl["built"]), set(pl["buildable"])
        buildings = faction["buildings"]
        columns = building_columns(buildings)
        cols = max(columns.values(), default=0) + 1
        wrap = cols > MAX_COLUMNS   # много зданий одного уровня (у наг все десять — I): ряды по MAX_COLUMNS
        cols = min(cols, MAX_COLUMNS)
        cw = (width - 24) / cols
        boxes: dict[str, tuple[float, float, float, float]] = {}
        for level, chunk in self.building_rows(buildings, columns, cols if wrap else None):
            heights = []
            for i, bid in enumerate(chunk):
                b = buildings[bid]
                bx = x + (i if wrap else columns[bid]) * cw
                fill = BUILT if bid in built else CAN_BUILD if bid in can else LOCKED
                top = y
                card = c.create_rectangle(bx, top, bx + cw - 8, top + 10, fill=fill, outline="#cbbfa9")
                ty = top + 5
                head = f"{LEVELS[level]} · {b['name']}" + (" ✓" if bid in built else "")
                ty = c.bbox(self.text(bx + 6, ty, head, 9, "bold", width=cw - 20))[3] + 2   # название бывает в 2 строки
                ty = self.rich(bx + 6, ty, f"{b['vp']}{MARKS['laurel']} · цена {res(b['cost'])}", 8, fill=MUTED)[3] + 2
                text = markup(b.get("text_board") or b.get("text_ru") or b["text"])
                ty = self.rich(bx + (cw - 8) / 2, ty, text, 8, anchor="n", width=cw - 20)[3] + 6  # по центру, как на планшете
                c.coords(card, bx, top, bx + cw - 8, ty)
                boxes[bid] = (bx, top, bx + cw - 8, ty)
                if picking and bid in self.pick_options:   # можно выбрать щелчком: оранжевая рамка
                    c.itemconfigure(card, outline=ACCENT, width=3 if bid == self.picked else 2,
                                    fill=PICKED if bid == self.picked else fill)
                    self.pick_boxes[bid] = boxes[bid]
                heights.append(ty)
            y = max(heights, default=y) + 22  # зазор под стрелки
        # Стрелки: от верха нужного здания (уровнем ниже) к низу зависящего от него.
        for bid, b in buildings.items():
            for req in b["requires"]:
                rx0, ry0, rx1, _ = boxes[req]
                dx0, _, dx1, dy1 = boxes[bid]
                c.create_line((rx0 + rx1) / 2, ry0, (dx0 + dx1) / 2, dy1 + 1, arrow="last", arrowshape=(9, 11, 4),
                              width=2, fill=GOOD if req in built else "#a0957f")
        y -= 8

        # Артефакты — как на планшете: условие, рисунок, свойство; под ними — у кого артефакт
        self.text(x, y, "Артефакты", 10, "bold")
        y += 20
        regions = list(faction["artifacts"])
        per_row = max(1, min(len(regions), int((width - 24) // 150)))
        aw = (width - 24) / per_row
        board = faction.get("artifacts_board") or faction.get("artifacts_ru") or faction["artifacts"]
        for start in range(0, len(regions), per_row):
            bottom = y
            cells = []
            for i, region in enumerate(regions[start:start + per_row]):
                ax, mid = x + i * aw, x + i * aw + (aw - 6) / 2
                holder = state["artifact_holder"].get(region)
                if holder is not None:
                    status = "ваш" if holder == self.you else f"у игрока {name_of(static, holder, avatar=True)}"
                elif region in state["reserved"]:
                    status = f"бронь: {name_of(static, state['reserved'][region], avatar=True)}"
                else:
                    status = "свободен"
                card = c.create_rectangle(ax, y, ax + aw - 6, y + 10, fill="white", outline="#cbbfa9")
                cond = condition_icons(artifact_condition(static, pl["faction"], region))
                wood = faction.get("artifact_wood")
                if wood:   # Гнездовье эльфов: артефакт и за дерево — как на планшете, через «/»
                    cond += " / " + res({"wood": wood["prices"][region]})
                ty = self.rich(mid, y + 4, cond, 9, "bold", anchor="n")[3] + 2
                picture = icon(c, f"artifact_{region}", 44)
                c.create_image(mid, ty, image=picture, anchor="n")
                ty += picture.height() + 3
                ty = self.rich(mid, ty, markup(board[region]), 8, anchor="n", width=aw - 18)[3] + 3
                cells.append((card, ax, holder, status, region))
                bottom = max(bottom, ty)
            for card, ax, holder, status, region in cells:  # подпись у всех карточек ряда — на одной высоте
                fill = color_of(holder) if holder is not None else MUTED
                self.rich(ax + (aw - 6) / 2, bottom + 1, status, 8, "bold" if holder is not None else "normal",
                          fill=fill, anchor="n", width=aw - 14)
                c.coords(card, ax, y, ax + aw - 6, bottom + 20)
                if holder is not None:
                    c.itemconfigure(card, outline=fill, width=2)
            y = bottom + 30

        # Линия чудовищ — как на планшете: сила, рисунок, ПО и добыча
        self.monsters_top = y
        self.text(x, y, "Линия чудовищ (обведено: ✓ — побеждено чудовище, ⚔ — побеждён игрок)", 10, "bold")
        picking = self.picking_monster()
        if picking:
            self.text(x, y + 17, "щёлкните чудовище, чтобы выбрать его для боя", 9, "bold", fill=ACCENT)
            y += 16
        y += 20
        self.monster_boxes = {}
        line = monster_line(static, pl["faction"])
        # рисунки чудовищ у всех линий одни и те же (с планшета людей), у наг другие только сила и награды
        n = len(line)
        mw = (width - 24) / n
        rows = max((len(m["loot"]) for m in line), default=0)
        pic_h = max(24, min(48, int(mw * 0.75)))
        mh = 44 + pic_h + 15 * max(0, rows - 1)
        for i, m in enumerate(line):
            mx = x + i * mw
            mid = mx + mw / 2 - 2
            mark = pl["monsters"][i]
            fill = "#cfe8c4" if mark == "monster" else "#d6e2f5" if mark == "pvp" else "white"
            card = c.create_rectangle(mx, y, mx + mw - 4, y + mh, fill=fill, outline="#cbbfa9")
            if picking and i in self.monster_options:   # можно выбрать щелчком: оранжевая рамка
                chosen = i == self.monster_picked
                c.itemconfigure(card, outline=ACCENT, width=3 if chosen else 2, fill=PICKED if chosen else fill)
                self.monster_boxes[i] = (mx, y, mx + mw - 4, y + mh)
            self.rich(mid, y + 3, f"{m['strength']}{MARKS['shield']}"
                      + (" ✓" if mark == "monster" else " ⚔" if mark == "pvp" else ""), 10, "bold", anchor="n")
            c.create_image(mid, y + 24 + pic_h / 2, image=icon(c, f"monster_{i}", pic_h))
            ty = y + 28 + pic_h
            self.rich(mid, ty, f"{m['vp']}{MARKS['laurel']}", 8, "bold", anchor="ne" if m["loot"] else "n")
            for j, (k, v) in enumerate(m["loot"].items()):  # добыча столбиком справа от ПО, как на планшете
                self.rich(mid + 4, ty + 15 * j, res({k: v}), 8, "bold", anchor="nw")
        y += mh + 12
        # стоимость перемещения, фундаменты и захваченные крепости — на карте этого игрока
        c.configure(scrollregion=(0, 0, width, y + 20))

    @staticmethod
    def building_rows(buildings: dict, columns: dict[str, int], per_row: int | None):
        """Ряды карточек сверху вниз: (уровень, здания ряда). per_row — переносить по столько в ряд."""
        for level in (4, 3, 2, 1):
            row = sorted((bid for bid, b in buildings.items() if b["level"] == level), key=columns.get)
            step = per_row or max(1, len(row))
            for start in range(0, len(row), step):
                yield level, row[start:start + step]

    def stock_box(self, bx: float, y: float, col: float, k: str, amount: int, steps: list, marks: list) -> None:
        """Ячейка склада: значок ресурса, сколько есть и пороги ПО (обведённые — зелёным кружком)."""
        c = self.canvas
        c.create_rectangle(bx, y, bx + col - 8, y + 44, fill="white", outline="#d8d0c0")
        c.create_rectangle(bx, y, bx + 5, y + 44, fill=RESOURCE_COLORS.get(k, MUTED), outline="")
        img = icon(c, k, 24)  # иконка вместо названия ресурса
        c.create_image(bx + 12, y + 22, image=img, anchor="w")
        self.text(bx + 18 + img.width(), y + 22, f"{amount}", 15, "bold", fill=RESOURCE_COLORS.get(k, INK), anchor="w")
        mx = bx + col - 14
        for threshold, vp in reversed(steps):
            done = threshold in marks
            item = self.text(mx, y + 6, f"{threshold}", 8, "bold" if done else "normal",
                             fill=GOOD if done else "#b8b0a2", anchor="ne")
            if done:
                x0, y0, x1, y1 = c.bbox(item)
                c.create_oval(x0 - 3, y0 - 2, x1 + 3, y1 + 2, outline=GOOD)
            self.rich(mx, y + 24, f"{vp}{MARKS['laurel']}", 7, fill=MUTED, anchor="ne")
            mx -= 30

    def specials(self, x: float, y: float, width: float, pl: dict, faction: dict) -> float:
        """Особые шкалы фракции под складом: наука гномов, ноты и гидра наг, проклятия нежити."""
        c, state = self.canvas, self.state
        science = faction.get("science")
        if science:
            have = pl.get("tokens", {}).get("science", 0)
            free = {t: LEVELS[int(lvl)] for lvl, t in science.get("free_build", {}).items()}
            self.rich(x, y, f"Наука {have}{MARKS['science']}", 9, "bold")
            cells = science["max"] + 1
            cw = min(22, (width - 24 - 90) / cells)
            for i in range(cells):
                cx = x + 90 + i * cw
                fill = "#f6d9b8" if i <= have else "white"
                c.create_rectangle(cx, y, cx + cw - 2, y + 16, fill=fill, outline=GOOD if i in free else "#d8d0c0",
                                   width=2 if i in free else 1)
                self.text(cx + (cw - 2) / 2, y + 8, str(i), 7, "bold" if i == have else "normal", anchor="center",
                          fill=INK if i <= have else MUTED)
                if i in free:   # здания этого уровня — бесплатно
                    self.rich(cx + (cw - 2) / 2, y + 18, f"{free[i]}{MARKS['build']}", 7, fill=GOOD, anchor="n")
            y += 40
        if faction.get("notes"):
            pairs = "   ".join(MARKS["note"] * n + "○" * (2 - n) for n in pl.get("notes", []))
            self.rich(x, y, f"Ноты гидры:  {pairs}", 9, "bold")
            y += 20
        if faction.get("hydra"):
            unit = faction["units"][faction["hydra"]]["name"]
            where = pl.get("hydra")
            if where:
                apart = "отдельно от вас" if where != pl["region"] else "вместе с вами"
                strength = pl["units"][faction["hydra"]]["strength"]
                text = f"{MARKS['hydra']} {unit}: в регионе {where} ({apart}), сила {strength}{MARKS['power']}"
            else:
                text = f"{MARKS['hydra']} {unit}: на карте нет"
            self.rich(x, y, text + f"; призвана раз: {pl.get('hydra_summons', 0)}", 9)
            y += 22
        cursed = sorted(r for r, q in state.get("cursed", {}).items() if q == self.player)
        if cursed or faction.get("curses"):
            bottom = self.rich(x, y, f"{MARKS['curse']} Проклятые регионы: {', '.join(cursed) or 'пока нет'}", 9, "bold",
                               width=width - 30)[3]
            y = bottom + 8
        return y + 4


CONDITION_SHORT = {   # условие артефакта в заголовке сводки
    "pay": lambda a: "плата: " + ", ".join(f"{v} {RESOURCE_GENITIVE[k]}" for k, v in a["cost"].items()),
    "foundationsBuilt": lambda a: f"{a['count']} фундамента со зданиями",
    "battleWins": lambda a: f"{a['count']} побед в сражениях",
    "mountainsVisited": lambda a: f"{a['count']} горных региона",
}
RESOURCE_GENITIVE = {"wood": "дерева", "iron": "железа", "gold": "золота"}


def condition_progress(static: dict, state: dict, p: int, region: str) -> str:
    """Насколько игрок выполнил условие артефакта (своей фракции: у наг часть условий своя): «3 / 4»,
    «✓» — выполнено (можно взять), «взят» — у него."""
    pl = state["players"][p]
    holder = state["artifact_holder"].get(region)
    if holder == p:
        return "✦ взят"
    cond = artifact_condition(static, pl["faction"], region)
    kind = cond["condition"]
    if kind == "pay":
        stock = pl.get("holdings") or pl["resources"]
        have, need = min(stock.get(k, 0) for k in cond["cost"]), max(cond["cost"].values())
    else:
        need = cond["count"]
        mountains = pl.get("mountains")   # горные в момент посещения (Гейзер мог превратить их в озёра)
        have = {"foundationsBuilt": sum(1 for lvl in pl["foundations"].values() if lvl),
                "battleWins": pl["victories"],
                "mountainsVisited": len(mountains) if mountains is not None else
                sum(1 for r in pl["visited"] if static["regions"][r]["terrain"] == "mountain"),
                }[kind]
    unit = "" if kind != "pay" or set(cond["cost"]) <= {"wood", "iron", "gold"} else " (" + ", ".join(
        RESOURCE_NAMES.get(k, k) for k in cond["cost"]) + ")"   # краки наг вместо 12 ресурсов
    met = have >= need
    text = f"{have} / {need}{unit}"
    wood = static["factions"][pl["faction"]].get("artifact_wood")
    if wood:   # Гнездовье эльфов: «дерево 7/12 или 7/5, грифоны 3/5» (короткие слова: столбцы узкие)
        price, stock = wood["prices"][region], pl["resources"]["wood"]
        griffins = pl["units"][wood["unit"]]["count"]
        unit_name = static["factions"][pl["faction"]]["units"][wood["unit"]]["name"].lower()
        same = kind == "pay" and set(cond["cost"]) == {"wood"}
        if kind == "pay":
            text = f"{RESOURCE_NAMES[next(iter(cond['cost']))]} {have}/{need}"
        text += f" или {'' if same else 'дерево '}{stock}/{price}, {unit_name}ы {griffins}/{price}"
        built = wood["building"] in pl["built"]
        if not built:
            text += " (нет Гнездовья)"
        met = met or built and stock >= price and griffins >= price
    return text + (" ✓" if met and holder is None else "")


def specials_cell(static: dict, state: dict, p: int) -> list[tuple[str | None, str]]:
    """Особенности фракции игрока для сводки: ярость, наука, ноты и гидра, проклятые регионы."""
    pl = state["players"][p]
    faction = static["factions"][pl["faction"]]
    parts: list[tuple[str | None, str]] = [(k, str(v)) for k, v in pl.get("tokens", {}).items()]
    if faction.get("notes"):
        parts.append(("note", f"{sum(pl.get('notes', []))}/{2 * faction['notes']}"))
    if faction.get("hydra"):
        parts.append(("hydra", pl.get("hydra") or "нет"))
    cursed = [r for r, q in state.get("cursed", {}).items() if q == p]
    if cursed or faction.get("curses"):
        parts.append(("curse", str(len(cursed))))
    return parts


ROW_COLORS = ("#fde2e2", "#dfe9fb", "#dff3e5", "#fbefd7")
HEAD_BG, LINE = "#efe6d2", "#cbbfa9"


def fit_wrap(label: tk.Label, wrap: int) -> None:
    """Перенос строк по ширине, которая подписи досталась: в узком окне столбцы уже wrap, и текст
    обрезался бы по краям."""
    if wrap:
        label.bind("<Configure>", lambda e: e.width > 30 and label.configure(wraplength=min(wrap, e.width - 8)), add="+")


class SummaryTable(tk.Frame):
    """Таблица из подписей в сетке (в отличие от Treeview, ячейка может переносить строки и показывать
    картинки). Ячейка — текст или список картинок с подписями [(значок, подпись)]; заголовок — текст
    и/или значок. Ячейки создаются один раз, а при обновлении меняются только те, где значение другое."""

    def __init__(self, master, title: str, columns: list[tuple[str, str | None, int]], rows: int, on_open):
        super().__init__(master, background=PANEL_BG)
        tk.Label(self, text=title, font=font(10, "bold"), background=PANEL_BG, fg=INK, anchor="w").pack(fill="x", pady=(8, 2))
        grid = tk.Frame(self, background=LINE, padx=1, pady=1)   # фон в просветах сетки — линии таблицы
        grid.pack(fill="x")
        self.columns = columns
        self.values: dict[tuple[int, int], object] = {}
        self.cells: dict[tuple[int, int], tk.Widget] = {}
        for c, (text, kind, wrap) in enumerate(columns):
            picture = bool(kind) and kind.startswith("artifact_")   # рисунок артефакта — крупно, над подписью
            image = icon(self, kind, 32 if picture else 18) if kind else ""
            head = tk.Label(grid, text=text, image=image, compound="top" if picture else "left",
                            font=font(8 if picture else 9, "bold"), background=HEAD_BG,
                            fg=INK, padx=4, pady=2, wraplength=wrap or 0)
            head.grid(row=0, column=c, sticky="nsew", padx=(0, 1) if c < len(columns) - 1 else 0)
            fit_wrap(head, wrap)
            grid.columnconfigure(c, weight=1)   # таблица — во всю ширину вкладки
        for r in range(rows):
            for c, (_, _, wrap) in enumerate(columns):
                cell = tk.Frame(grid, background=ROW_COLORS[r % 4])
                cell.grid(row=r + 1, column=c, sticky="nsew", pady=(1, 0), padx=(0, 1) if c < len(columns) - 1 else 0)
                cell.bind("<Double-1>", lambda e, r=r: on_open(r))
                self.cells[r, c] = cell
        self.on_open = on_open

    def set(self, r: int, c: int, value) -> None:
        if self.values.get((r, c)) == value:
            return
        self.values[r, c] = value
        cell = self.cells[r, c]
        for w in cell.winfo_children():
            w.destroy()
        bg, wrap = cell.cget("background"), self.columns[c][2]
        parts = [(None, value)] if isinstance(value, str) else value or [(None, "—")]
        for kind, text in parts:
            # рисунок артефакта — крупно над подписью; аватарка и значки (ярость, ноты…) — слева, в строку
            picture = bool(kind) and kind.startswith("artifact_")
            label = tk.Label(cell, text=text if picture else f" {text}", compound="top" if picture else "left",
                             image=icon(cell, kind, 32 if picture else 22 if kind in AVATARS else 18) if kind else "",
                             font=font(8 if picture else 9, "bold" if c == 0 else ""), background=bg, fg=INK,
                             wraplength=wrap or 0, justify="left", padx=4 if picture or not kind else 2, pady=2)
            label.pack(side="left", anchor="w")
            label.bind("<Double-1>", lambda e: self.on_open(r))
            if not kind:
                fit_wrap(label, wrap)


class PlayersTable(ttk.Frame):
    """Сводка по всем игрокам — пять таблиц: где стоит и что у него есть; ПО по категориям;
    здания по уровням; крепости и артефакты; условия артефактов и насколько каждый к ним подошёл.
    ★ — первый игрок раунда; двойной щелчок — его планшет."""

    def __init__(self, master, on_open):
        super().__init__(master)
        self.on_open = on_open
        self.canvas = tk.Canvas(self, background=PANEL_BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, background=PANEL_BG, padx=10, pady=4)
        window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.tables: list[SummaryTable] = []
        self.special = False

    def build(self, static: dict, rows: int) -> None:
        for t in self.tables:
            t.destroy()
        # ярость, наука, ноты и гидра, проклятия — если за столом есть такие фракции
        self.special = any(f.get("tokens") or f.get("science") or f.get("notes") or f.get("curses")
                           for f in static["factions"].values())
        name = ("Игрок", None, 0)
        specs = [
            # у фракций разные войска, поэтому они — одним столбцом: «Скелет 4, Лич 1»
            ("Регион, ресурсы и войска", [name, ("Регион", None, 0), ("", "wood", 0), ("", "iron", 0), ("", "gold", 0),
                                          ("Войска", None, 170), ("Сила", "power", 0)]
             + ([("Особое", None, 150)] if self.special else [])),
            ("Победные очки", [name] + [(t.capitalize(), None, 90) for t in CATEGORIES.values()]
             + [("Всего", "laurel", 0)]),
            ("Здания по уровням", [name] + [(LEVELS[lvl], None, 110) for lvl in (1, 2, 3, 4)]),
            ("Крепости и артефакты", [name, ("Победы", "victory", 0), ("Захваченные крепости", None, 160),
                                      ("Артефакты", None, 220)]),
            ("Условия артефактов", [name] + [(f"{r}\n{CONDITION_SHORT[a['condition']](a)}", f"artifact_{r}", 90)
                                             for r, a in self.artifacts(static)]),
        ]
        self.tables = [SummaryTable(self.inner, title, cols, rows, self.on_open) for title, cols in specs]
        for t in self.tables:
            t.pack(fill="x")
        tk.Label(self.inner, text="Двойной щелчок по строке — планшет и карта игрока.", font=font(8),
                 background=PANEL_BG, fg=MUTED).pack(anchor="w", pady=(6, 0))

    @staticmethod
    def artifacts(static: dict) -> list[tuple[str, dict]]:
        return [(r, info["artifact"]) for r, info in static["regions"].items() if info["object"] == "artifact"]

    def show(self, static: dict, state: dict, you: int | None) -> None:
        players = state["players"]
        if len(self.tables) != 5 or len(self.tables[0].cells) != len(players) * len(self.tables[0].columns):
            self.build(static, len(players))
        located, scores, levels, trophies, conditions = self.tables
        for p, pl in enumerate(players):
            faction = static["factions"][pl["faction"]]
            name = name_of(static, p) + (" — вы" if p == you else "") + (" ★" if p == state["first_player"] else "")
            for t in self.tables:
                t.set(p, 0, [(f"avatar{p}", name)])
            units = ", ".join(f"{faction['units'][uid]['name']} {u['count']}" for uid, u in pl["units"].items() if u["count"])
            row = [pl["region"] or "—", *(str(pl["resources"][k]) for k in ("wood", "iron", "gold")),
                   units or "нет", str(pl["army_strength"])]
            if self.special:
                row.append(specials_cell(static, state, p))
            for c, v in enumerate(row, 1):
                located.set(p, c, v)
            for c, v in enumerate([*(pl["score"][k] for k in CATEGORIES), pl["score"]["total"]], 1):
                scores.set(p, c, str(v))
            for c, lvl in enumerate((1, 2, 3, 4), 1):
                built = [faction["buildings"][b]["name"] for b in pl["built"] if faction["buildings"][b]["level"] == lvl]
                levels.set(p, c, ", ".join(built) or "—")
            trophies.set(p, 1, str(pl["victories"]))
            trophies.set(p, 2, ", ".join(pl["fortresses"]) or "—")
            trophies.set(p, 3, [(f"artifact_{r}", r) for r in pl["artifacts"]])
            for c, (r, _) in enumerate(self.artifacts(static), 1):
                conditions.set(p, c, condition_progress(static, state, p, r))
