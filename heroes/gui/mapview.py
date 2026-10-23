"""Карта — оригинальный рисунок из правил, поверх — объекты, подписи и фишки.

Картинки готовит tools/build_map.py (heroes/gui/assets/map/): основа без объектов и
отдельный слой объектов в нескольких масштабах, а в map.json — границы регионов,
рамки объектов и места подписей. Объект рисуется, пока он на месте: захваченная
крепость и взятый артефакт с карты исчезают (на месте крепости — флажок захватчика).

Подписи как на планшете: название, ресурсы первого посещения, сила и добыча крепости,
условие артефакта; фишки игроков — их аватарки (avatars.seat), гидра наг — своя фишка в цвете владельца.
Когда фишка сменила регион, она за секунду плавно едет по прямой со старого места на новое;
о каждом событии партии (постройка, наём, ресурсы, сражение, крепость, артефакт, проклятие…) над фишкой
игрока или его гидры всплывает и тает табличка (подписи — ru.popup); у едущей фишки — когда доедет.
Проклятый регион (нежить) помечен значком проклятия, регион, ставший озёрным (Гейзер наг), — голубой штриховкой
и значком озера в подписи. В режиме выбора (старт, перемещение, Башня иллюзий) доступные
регионы обведены и подписаны, остальные притушены; щелчок выбирает регион. Выбор — на карте того,
кто решает: на карте соперника щелчки не действуют, а в фазе перемещения на ней так же обведено,
куда может пойти он сам. Пока первый игрок выбирает порядок действий и поставил перемещение на место N,
на каждой карте так же обведено, куда её хозяин дойдёт с N ОП (set_preview).
Фишка игрока под указателем мыши становится полупрозрачной: под ней видно подписи карты.
Регионы, вычеркнутые при таком числе игроков, стёрты с карты целиком: вместо них — фон окна,
без объекта и подписи.
"""
from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path
from typing import Callable

from . import avatars
from .common import INK, MUTED, color_of, font, res, terrain_of
from .icons import MARKS, canvas_text, icon, measurer
from .ru import popup

ASSETS = Path(__file__).resolve().parent / "assets" / "map"
SELECT = "#e8590c"
CHOSEN = "#a61e1e"
CANVAS_BG = "#efe8d6"
MARKER = "#3a3631"      # «маркер», которым игрок пишет на своей карте
LAKE = "#3d8fd1"        # регион, ставший озёрным
OLD_ROUTE = "#8f887c"   # прежние перемещения
M = MARKS
TERRAINS = ("field", "forest", "mountain", "lake")   # порядок, как на дорожке перемещения планшета
MOVE_SECONDS = 1.0      # сколько едет фишка при перемещении
POPUP_SECONDS = 2.5     # сколько видна табличка события
POPUP_GAP = 0.35        # таблички одной фишки появляются друг за другом
POPUP_STACK = 4         # больше над фишкой не помещается: старшие уходят
POPUP_APPEAR = 0.3      # за сколько табличка всплывает (и подталкивает старшие вверх)
FADE = ((0.95, "gray12"), (0.87, "gray25"), (0.75, "gray50"), (0.6, "gray75"))   # таяние: с какой доли — какой растр
FADED = str.maketrans({MARKS[k]: MARKS[f"{k}_off"] for k in ("wood", "iron", "gold", "any")})   # картинки не тают — бледнеют
FRAME_MS = 16
TOKEN_FADED = 0.35      # прозрачность фишки под указателем мыши


def load_geometry() -> dict:
    return json.loads((ASSETS / "map.json").read_text(encoding="utf-8"))


def inside(x: float, y: float, polygon: list[list[int]]) -> bool:
    """Точка внутри многоугольника (подсчёт пересечений луча)."""
    hit = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def object_shown(state: dict, region: str, kind: str) -> bool:
    """Объект ещё на карте: крепость не захвачена, артефакт никем не взят."""
    if kind == "fortress":
        return region not in state["fortress_owner"]
    if kind == "artifact":
        return region not in state["artifact_holder"]
    return True


class MapView(tk.Canvas):
    def __init__(self, master):
        super().__init__(master, background=CANVAS_BG, highlightthickness=0)
        self.geometry = load_geometry()
        self.world_w, self.world_h = self.geometry["size"]
        self.regions = self.geometry["regions"]
        self.boxes = {name: bounds(r["polygon"]) for name, r in self.regions.items()}
        self.static: dict | None = None
        self.state: dict | None = None
        self.you: int | None = None   # чья это карта: его маршрут, фундаменты, флажки
        self.viewer: int | None = None   # кто смотрит
        self.options: dict[str, str] | None = None   # режим выбора: регион -> подпись
        self.owner: int | None = None   # чей это выбор: щёлкать можно только на его карте
        self.shown: dict[str, str] | None = None   # что обведено на этой карте (выбор или чужие возможности)
        self.selected: str | None = None
        self.on_select: Callable[[str], None] | None = None
        self.hover: str | None = None
        self.preview: dict[str, dict[str, int]] | None = None   # порядок действий: игрок -> регион -> цена в ОП
        self.token_hover: tuple[str, int] | None = None   # фишка под указателем — полупрозрачная
        self.tokens: dict[tuple[str, int], tuple[int, int, int]] = {}   # фишка -> её картинка, обводка, размер
        self.images: dict[int, Sprites] = {}   # ширина картинки -> её основа и кусочки
        self.width_px = 0
        self.scale, self.ox, self.oy = 1.0, 0.0, 0.0
        self._pending: str | None = None
        self.spots: dict[tuple[str, int], tuple[float, float]] = {}   # где нарисована фишка (координаты рисунка)
        self.moving: dict[tuple[str, int], list] = {}   # едущая фишка -> [откуда, куда (None — ещё не знаем), начало]
        self.popups: list[list] = []   # всплывающие таблички: [фишка, текст, начало]
        self._fresh: list[list] = []   # таблички, пришедшие после прошлого show(): их фишка, может, сейчас поедет
        self._tick: str | None = None
        self.bind("<Configure>", lambda e: self._schedule())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: (self._set_hover(None), self._set_token_hover(None)))

    def destroy(self) -> None:
        if self._tick:   # иначе кадр сработает уже без холста
            self.after_cancel(self._tick)
        super().destroy()

    # --- данные ------------------------------------------------------------------------

    def show(self, static: dict, state: dict, you: int | None, viewer: int | None = None) -> None:
        """you — чья это карта, viewer — кто смотрит."""
        if static is self.static and self.state is not None:
            self._start_moves(self.state, state)
            live = {id(pop) for pop in self.popups}
            fresh = [pop for pop in self._fresh if id(pop) in live]
            for key in {pop[0] for pop in fresh if pop[0] in self.moving}:
                # события случились там, куда фишка едет: таблички — когда доедет, в том же порядке
                pops = [pop for pop in fresh if pop[0] == key]
                delay = self.moving[key][2] + MOVE_SECONDS - pops[0][2]
                for pop in pops:
                    pop[2] += max(0.0, delay)
        else:   # другая партия — ехать и всплывать нечему
            self.moving.clear()
            self.popups.clear()
            self.spots = {}
        self._fresh = []
        self.static, self.state, self.you, self.viewer = static, state, you, viewer
        self.redraw()

    def select_mode(self, options: dict[str, str] | None, selected: str | None = None,
                    on_select: Callable[[str], None] | None = None, owner: int | None = None) -> None:
        """Регионы из options (регион -> подпись) можно выбрать щелчком на карте игрока owner. None — выбора нет."""
        self.options, self.selected, self.on_select, self.owner = options, selected, on_select, owner
        self.redraw()

    def set_preview(self, preview: dict[str, dict[str, int]] | None) -> None:
        """Порядок действий: куда сможет дойти каждый игрок (игрок -> регион -> цена в ОП); None — не показывать."""
        if preview != self.preview:
            self.preview = preview
            self.redraw()

    def picking(self) -> bool:
        return self.options is not None and self.you == self.owner

    def _shown(self) -> dict[str, str] | None:
        """Что обвести: свой выбор — на своей карте; на чужой в фазе перемещения — куда может пойти её хозяин;
        пока выбирают порядок действий — куда дойдёт хозяин карты, если перемещение будет на выбранном месте."""
        if self.picking():
            return self.options
        if self.you is None or not self.state:
            return None
        if self.preview is not None:
            reach = self.preview.get(str(self.you))
        elif self.you == self.viewer:
            return None
        else:
            reach = self.state["players"][self.you].get("reach")   # у хозяина старой версии его нет
        return {r: "остаться" if not cost else f"{cost} ОП" for r, cost in reach.items()} if reach else None

    def set_selected(self, region: str | None) -> None:
        self.selected = region
        self.redraw()

    # --- события ------------------------------------------------------------------------

    def region_at(self, x: float, y: float) -> str | None:
        """Регион под точкой холста."""
        wx, wy = (x - self.ox) / self.scale, (y - self.oy) / self.scale
        for name, r in self.regions.items():
            x0, y0, x1, y1 = self.boxes[name]
            if x0 <= wx <= x1 and y0 <= wy <= y1 and inside(wx, wy, r["polygon"]):
                return name
        return None

    def _click(self, event) -> None:
        region = self.region_at(event.x, event.y)
        if region is None or not self.static:
            return
        if self.picking() and region in self.options and self.on_select:
            self.selected = region
            self.on_select(region)
            self.redraw()

    def _motion(self, event) -> None:
        self._set_hover(self.region_at(event.x, event.y) if self.static else None)
        self._set_token_hover(self.token_at(event.x, event.y) if self.static else None)

    def token_at(self, x: float, y: float) -> tuple[str, int] | None:
        """Фишка игрока под точкой холста (едущие не в счёт)."""
        rad = max(18, 24 * self.scale)
        for key, (wx, wy) in self.spots.items():
            if key[0] == "player" and key not in self.moving:
                sx, sy = self._xy(wx, wy)
                if (x - sx) ** 2 + (y - sy) ** 2 <= rad * rad:
                    return key
        return None

    def _set_token_hover(self, key: tuple[str, int] | None) -> None:
        if key == self.token_hover:
            return
        old, self.token_hover = self.token_hover, key
        for k in (old, key):
            if k in self.tokens:
                image, ring, size = self.tokens[k]
                faded = k == key
                self.itemconfigure(image, image=avatars.seat(self, k[1], size, TOKEN_FADED if faded else 1.0))
                self.itemconfigure(ring, outlinestipple="gray50" if faded else "")

    def _set_hover(self, region: str | None) -> None:
        if region == self.hover:
            return
        self.hover = region
        selectable = region is not None and self.picking() and region in self.options
        self.configure(cursor="hand2" if selectable else "")
        self._draw_hover()

    # --- картинки -----------------------------------------------------------------------

    def _pick_width(self, w: int, h: int) -> int:
        """Самый крупный готовый масштаб, который помещается в холст."""
        widths = sorted(self.geometry["widths"], reverse=True)
        for width in widths:
            if width <= w and width * self.world_h / self.world_w <= h:
                return width
        return widths[-1]

    def _images(self, width: int) -> Sprites:
        if width not in self.images:
            self.images[width] = Sprites(self, width, self.geometry)
        return self.images[width]

    # --- рисование ----------------------------------------------------------------------

    def _schedule(self) -> None:
        """Перерисовать после изменения размера — один раз, когда перетаскивание затихнет."""
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(60, self._resized)

    def _resized(self) -> None:
        self._pending = None
        self.redraw()

    def _xy(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + x * self.scale, self.oy + y * self.scale

    def _poly(self, name: str) -> list[float]:
        s, ox, oy = self.scale, self.ox, self.oy
        return [c for x, y in self.regions[name]["polygon"] for c in (ox + x * s, oy + y * s)]

    def _text(self, x: float, y: float, text: str, size: int, *styles: str, fill: str = INK,
              anchor: str = "center", tags=()) -> tuple[float, float, float, float]:
        """Текст (с иконками ресурсов) в белом ореоле, чтобы читался на любом месте рисунка; рамка текста."""
        return canvas_text(self, x, y, text, font(size, *styles), fill, anchor=anchor, halo="white", tags=tags)

    def redraw(self) -> None:
        self.delete("all")
        if not self.static or not self.state:
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        self.width_px = width = self._pick_width(w, h)
        self.scale = s = width / self.world_w
        self.ox, self.oy = (w - width) // 2, (h - round(self.world_h * s)) // 2
        self.spots = {}
        self.tokens = {}
        art = self._images(width)
        self.create_image(self.ox, self.oy, image=art.base, anchor="nw")
        state = self.state
        available = set(state["available"])
        kinds = {name: self.static["regions"][name]["object"] for name in self.regions}
        for name in self.regions:
            if name in available and object_shown(state, name, kinds[name]):
                sprite, x, y = art.objects[name]
                self.create_image(self.ox + x, self.oy + y, image=sprite, anchor="nw")
        for name, terrain in state.get("terrain", {}).items():   # Гейзер: регион теперь озёрный
            if name in available and terrain != self.static["regions"][name]["terrain"]:
                self.create_polygon(self._poly(name), fill=LAKE, stipple="gray25", outline=LAKE, width=max(1, 2 * s))
        # вычеркнутые — стереть (силуэт цвета фона), при выборе — притушить недоступные
        self.shown = shown = self._shown()
        selected = self.selected if self.picking() else None
        for name in self.regions:
            shade = art.dark if name not in available else art.wash if shown is not None and name not in shown else None
            if shade:
                sprite, x, y = shade[name]
                self.create_image(self.ox + x, self.oy + y, image=sprite, anchor="nw")
        if shown is not None:
            for name in shown:
                if name != selected:
                    self.create_polygon(self._poly(name), fill="", outline=SELECT, width=max(2, 3 * s), joinstyle="round")
        if selected:
            self.create_polygon(self._poly(selected), fill="", outline=CHOSEN, width=max(3, 4.5 * s),
                                joinstyle="round")
        for name in self.regions:
            self._marks(name, name in available, kinds[name])
        self._routes()
        for name in [n for n in self.regions if n in available]:
            self._label(name)
        self._movement()
        self._draw_hover()
        self._animate()
        # фишка могла уехать из-под указателя (или приехать под него)
        px, py = self.winfo_pointerx() - self.winfo_rootx(), self.winfo_pointery() - self.winfo_rooty()
        self._set_token_hover(self.token_at(px, py) if 0 <= px < w and 0 <= py < h else None)

    def _draw_hover(self) -> None:
        self.delete("hover")
        if self.hover and self.static:
            item = self.create_polygon(self._poly(self.hover), fill="", outline="white", width=max(2, 2.5 * self.scale),
                                       joinstyle="round", tags="hover")
            self.tag_lower(item, "label")

    def _label(self, name: str) -> None:
        """Подпись как на планшете: значок местности (и точки — с какого числа игроков регион в игре),
        название, ресурсы первого посещения; у крепости — сила и награда; фишки игроков над подписью."""
        s = self.scale
        static, state = self.static, self.state
        r = static["regions"][name]
        cx, cy = self._xy(*self.regions[name]["label"])
        shown = self.shown
        ink = INK if shown is None or name in shown else MUTED
        size = max(9, round(15 * s))
        small = max(8, round(13 * s))
        tags = ("label",)
        x0, top, x1, bottom = self._text(cx, cy, name, size, "bold", fill=ink, tags=tags)
        # над названием: точки числа игроков и значок местности
        y = top
        if r["min_players"] > 2:
            dot, gap = max(1.8, 2.8 * s), max(5, 7.5 * s)
            y -= dot + 3 * s
            xs = [-1, 0, 1] if r["min_players"] == 3 else [-1.9, -0.9, 0.9, 1.9]
            for k in xs:
                self.create_oval(cx + k * gap - dot, y - dot, cx + k * gap + dot, y + dot, fill=ink, outline="", tags=tags)
            y -= dot
        line = measurer(self, font(size, "bold")).metrics("linespace")
        terrain = terrain_of(static, state, name)
        glyph = icon(self, terrain, round(line * (0.55 if terrain == "lake" else 0.75)))  # волны широкие
        lift = (7 if r["min_players"] > 2 else 2) * s
        self.create_image(cx, y - lift, image=glyph, anchor="s", tags=tags)
        head = y - lift - glyph.height()
        # под названием: ресурсы первого посещения (бледные — если вы их уже получили)
        y = bottom
        you = state["players"][self.you] if self.you is not None else None
        if r["resources"]:
            got = you is not None and name in you["visited"]
            y = self._text(cx, y, res(r["resources"], faded=got), small, "bold", fill=MUTED if got else ink,
                           anchor="n", tags=tags)[3]
        if r["object"] == "fortress" and object_shown(state, name, "fortress"):
            self._fortress(name, r["fortress"], tags)
        # фишки игроков — их аватарки (обводка: чёрная — чья это карта, белая — остальные); гидры наг —
        # рисунок гидры в обводке цвета владельца
        here = [("player", p) for p, pl in enumerate(state["players"]) if pl["region"] == name]
        here += [("hydra", p) for p, pl in enumerate(state["players"]) if pl.get("hydra") == name]
        if here:
            rad = max(18, 24 * s)
            step = rad * 2 + 3
            tx = cx - step * (len(here) - 1) / 2
            ty = head - rad - 2
            for i, key in enumerate(here):
                x = tx + i * step
                spot = self.spots[key] = ((x - self.ox) / s, (ty - self.oy) / s)
                if key in self.moving:   # едет сюда: место занято, рисует её _animate
                    self.moving[key][1] = spot
                else:
                    self._token(*key, x, ty, tags)
        # подпись в режиме выбора
        if shown and shown.get(name):
            own = f"badge:{name}"
            bx0, by0, bx1, by1 = canvas_text(self, cx, y + 4 * s, shown[name], font(max(7, round(9.5 * s)), "bold"),
                                             "white", anchor="n", tags=(*tags, own))
            badge = self.create_rectangle(bx0 - 6, by0 - 1, bx1 + 6, by1 + 1, fill=SELECT, outline="", tags=tags)
            self.tag_lower(badge, own)

    def _token(self, kind: str, p: int, x: float, y: float, tags) -> None:
        """Фишка игрока (аватарка; обводка чёрная — чья это карта, белая — остальные) или гидры наг
        (рисунок гидры в обводке цвета владельца)."""
        rad = max(18, 24 * self.scale)
        if kind == "hydra":
            self.create_image(x, y, image=icon(self, "hydra", round(rad * 2)), tags=tags)
            self.create_oval(x - rad, y - rad, x + rad, y + rad, outline=color_of(p), width=3, tags=tags)
            return
        faded = (kind, p) == self.token_hover and "moving" not in tags
        image = self.create_image(x, y, image=avatars.seat(self, p, round(rad * 2), TOKEN_FADED if faded else 1.0),
                                  tags=tags)
        ring = self.create_oval(x - rad, y - rad, x + rad, y + rad, outline="black" if p == self.you else "white",
                                width=2, outlinestipple="gray50" if faded else "", tags=tags)
        if "moving" not in tags:
            self.tokens[kind, p] = (image, ring, round(rad * 2))

    # --- перемещение фишек и таблички построек ----------------------------------------

    def _start_moves(self, old: dict, new: dict) -> None:
        """Фишки, сменившие регион, едут с того места, где стоят сейчас (или куда успели доехать)."""
        now = time.monotonic()
        for key in list(self.spots):
            kind, p = key
            field = "region" if kind == "player" else "hydra"
            was, dest = old["players"][p].get(field), new["players"][p].get(field)
            if was and dest and was != dest:
                self.moving[key] = [self._spot_at(key, now), None, now]

    def _spot_at(self, key: tuple[str, int], now: float) -> tuple[float, float]:
        """Где фишка в момент now (координаты рисунка): едущая — на прямой между местами, плавно."""
        move = self.moving.get(key)
        if move is None or move[1] is None:
            return self.spots[key]
        (x0, y0), (x1, y1), start = move
        t = min(1.0, (now - start) / MOVE_SECONDS)
        t = t * t * (3 - 2 * t)   # разгон и торможение
        return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t

    def event(self, line: str) -> None:
        """Строка журнала партии: если о ней нужна табличка — над чьей фишкой и какая (ru.popup)."""
        found = popup(self.static["names"], line) if self.static else None
        if found is None:
            return
        key, text = found
        now = time.monotonic()
        move = self.moving.get(key)
        last = max((pop[2] for pop in self.popups if pop[0] == key), default=0.0)
        pop = [key, text, max(move[2] + MOVE_SECONDS if move else now, last + POPUP_GAP)]
        self.popups.append(pop)
        if move is None:
            self._fresh.append(pop)
        if self._tick is None:
            self._animate()

    def _draw_popups(self, now: float) -> None:
        """Таблички стопкой над фишкой (и едут вместе с ней): новая всплывает у фишки и плавно подталкивает
        старшие вверх, под конец каждая тает. Над фишкой у верхнего края места нет — стопка под ней."""
        self.popups = [pop for pop in self.popups if now - pop[2] < POPUP_SECONDS]
        s = self.scale
        rad = max(18, 24 * s)
        face = font(max(9, round(13 * s)), "bold")
        line = measurer(self, face).metrics("linespace")
        step = line + 9   # табличка с просветом
        stacks: dict[tuple[str, int], list[list]] = {}
        for pop in self.popups:
            if pop[0] in self.spots and pop[2] <= now:
                stacks.setdefault(pop[0], []).insert(0, pop)   # новые — первыми, у самой фишки
        n = 0
        for key, pops in stacks.items():
            x, y = self._xy(*self._spot_at(key, now))
            placed, below = [], 0.0
            for _, text, start in pops[:POPUP_STACK]:
                grow = min(1.0, (now - start) / POPUP_APPEAR)
                grow = 1 - (1 - grow) ** 2   # быстро всплывает и замирает
                placed.append((text, start, below + 10 * s * grow))
                below += step * grow
            up = y - rad - 6 - placed[-1][2] - line >= 0
            for text, start, lift in placed:
                bottom = y - rad - 6 - lift if up else y + rad + 6 + line + lift
                t = (now - start) / POPUP_SECONDS
                stipple = next((pattern for edge, pattern in FADE if t >= edge), "")
                own = f"popup:{n}"
                n += 1
                x0, y0, x1, y1 = canvas_text(self, x, bottom - 3, text.translate(FADED) if stipple else text, face,
                                             "white", anchor="s", tags=("popup", own))
                for item in self.find_withtag(own):
                    if self.type(item) == "text":
                        self.itemconfigure(item, stipple=stipple)
                badge = self.create_rectangle(x0 - 7, y0 - 2, x1 + 7, y1 + 1, fill=color_of(key[1]), outline="white",
                                              width=2, stipple=stipple, outlinestipple=stipple, tags="popup")
                self.tag_lower(badge, own)

    def _animate(self) -> None:
        """Кадр: едущие фишки и таблички — поверх карты; пока есть что двигать, следующий кадр по таймеру."""
        self.delete("moving", "popup")
        now = time.monotonic()
        for key, move in self.moving.items():
            if move[1] is not None:
                self._token(*key, *self._xy(*self._spot_at(key, now)), ("moving",))
        self._draw_popups(now)
        if (self.moving or self.popups) and self._tick is None:
            self._tick = self.after(FRAME_MS, self._step)

    def _step(self) -> None:
        self._tick = None
        now = time.monotonic()
        arrived = [key for key, (_, to, start) in self.moving.items() if to is None or now - start >= MOVE_SECONDS]
        for key in arrived:
            del self.moving[key]
        if arrived:
            self.redraw()   # доехавшие встают на свои места
        else:
            self._animate()

    def _fortress(self, name: str, fortress: dict, tags) -> None:
        """Слева от крепости, как на планшете: над чертой — сила, под ней — ПО и добыча (лишняя добыча — столбиком)."""
        s = self.scale
        ox0, oy0, _, oy1 = self.regions[name]["object"]
        right, mid = self._xy(ox0 - 4, (oy0 + oy1) / 2 + 2)
        big, small = max(9, round(16 * s)), max(8, round(13 * s))
        x0, *_ = self._text(right, mid - 2 * s, f"{fortress['strength']}{MARKS['shield']}", big, "bold", anchor="se",
                            tags=tags)
        loot = [res({k: v}) for k, v in fortress["loot"].items()]
        bx0, _, _, y = self._text(right, mid + 3 * s, f"{fortress['vp']}{MARKS['laurel']}  {loot[0]}", small, "bold",
                                  anchor="ne", tags=tags)
        for more in loot[1:]:
            y = self._text(right, y, more, small, "bold", anchor="ne", tags=tags)[3]
        self.create_line(min(x0, bx0) - 2 * s, mid, right + 2 * s, mid, fill=INK, width=max(1, 2 * s), tags=tags)

    def _marks(self, name: str, live: bool, kind: str) -> None:
        """Отметки у объекта: флажок захватчика, бронь, уровни фундаментов."""
        if not live:
            return
        s = self.scale
        state = self.state
        x0, y0, x1, y1 = self.regions[name]["object"]
        (ax, ay), (bx, by) = self._xy(x0, y0), self._xy(x1, y1)
        mid = (ax + bx) / 2
        owner = state["fortress_owner"].get(name) if kind == "fortress" else None
        if owner is not None and self.you in (None, owner):  # флажок — только у захватчика (и у зрителя)
            foot = by - 4 * s
            top = foot - 34 * s
            self.create_line(mid, foot, mid, top, fill="#3b2f22", width=max(2, 2.5 * s), tags="flag")
            self.create_polygon(mid, top, mid + 22 * s, top + 7 * s, mid, top + 14 * s, fill=color_of(owner),
                                outline="white", width=1, tags="flag")
            self.create_oval(mid - 6 * s, foot - 2 * s, mid + 6 * s, foot + 2 * s, fill="#3b2f22", outline="",
                             tags="flag")
        if name in state.get("cursed", {}):   # проклятие нежити: значок в обводке цвета проклявшего
            p = state["cursed"][name]
            r = max(9, 13 * s)
            cx, cy = ax + 2 * s, ay + 8 * s
            self.create_image(cx, cy, image=icon(self, "curse", round(r * 2)), tags="curse")
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color_of(p), width=max(2, 3 * s), tags="curse")
        if name in state["reserved"]:
            p = state["reserved"][name]
            rx, ry, r = bx - 2 * s, ay + 8 * s, max(8, 10 * s)
            self.create_rectangle(rx - r, ry - r, rx + r, ry + r, fill=color_of(p), outline="white", width=2)
            self.create_text(rx, ry, text="Б", font=font(max(7, round(9 * s)), "bold"), fill="white")
        if kind == "foundation" and self.you is not None:
            # уровень своего здания вписан в фундамент, как маркером на планшете; чужие не видны
            level = state["players"][self.you]["foundations"].get(name)
            if level:
                self._text(mid, (ay + by) / 2, str(level), max(12, round(26 * s)), "bold", fill=MARKER,
                           tags=("level",))
        elif kind == "foundation":  # зритель видит всех: уровни цветом игроков
            levels = [(p, pl["foundations"][name]) for p, pl in enumerate(state["players"])
                      if pl["foundations"].get(name)]
            r = max(7, 9 * s)
            step = r * 2 + 3
            lx = mid - step * (len(levels) - 1) / 2
            for i, (p, level) in enumerate(levels):
                x, y = lx + i * step, by + r
                self.create_rectangle(x - r, y - r, x + r, y + r, fill=color_of(p), outline="white", width=2)
                self.create_text(x, y, text=str(level), font=font(max(7, round(9 * s)), "bold"), fill="white")

    def _routes(self) -> None:
        """Свой маршрут, как маркером на планшете: одна изогнутая стрелка на перемещение — из региона,
        откуда вышел, в регион назначения. Последнее перемещение — ярче, прежние — бледнее."""
        if self.you is None:
            return
        routes = self.state["players"][self.you].get("routes", [])
        s = self.scale
        for k, (a, b) in enumerate(routes):
            color, width = (MARKER, max(3, 4.5 * s)) if k == len(routes) - 1 else (OLD_ROUTE, max(2, 3 * s))
            (x0, y0), (x1, y1) = self._xy(*self.regions[a]["center"]), self._xy(*self.regions[b]["center"])
            dx, dy = x1 - x0, y1 - y0
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / length, dy / length
            trim = min(length / 4, 16 * s)   # концы не упираются в центр: стрелки соседних ходов не сливаются
            x0, y0, x1, y1 = x0 + ux * trim, y0 + uy * trim, x1 - ux * trim, y1 - uy * trim
            bend = min(0.2 * length, 70 * s)   # изгиб влево по ходу, как дуга маркером
            cx, cy = (x0 + x1) / 2 - uy * bend, (y0 + y1) / 2 + ux * bend
            self.create_line(x0, y0, cx, cy, x1, y1, smooth=True, splinesteps=24, fill=color, width=width,
                             capstyle="round", arrow="last", arrowshape=(5 * width, 5.5 * width, 2.2 * width),
                             tags="route")

    def _movement(self) -> None:
        """Дорожка стоимости перемещения, как на планшете (в правом нижнем углу рисунка пусто): значки
        местности над чертой, под ней — сколько стоит войти в регион; «2»1» — что можно получить
        зданием или артефактом. У того, что действует для вас сейчас, — полная яркость."""
        static, state = self.static, self.state
        viewer = self.you if self.you is not None else 0
        me = state["players"][viewer]
        faction = static["factions"][me["faction"]]
        base, mods = faction["movement"], faction.get("movement_mods", {})
        current = me["move_cost"] if self.you is not None else base
        s = self.scale
        x0, y0 = self._xy(905, 858)
        x1, y1 = self._xy(1405, 936)
        self.create_rectangle(x0, y0, x1, y1, fill="#fbf6ea", outline=INK, width=max(1, 2.5 * s))
        mid = (y0 + y1) / 2 - 2 * s
        self.create_line(x0 + 10 * s, mid, x1 - 10 * s, mid, fill=INK, width=max(1, 1.5 * s))
        col = (x1 - x0) / len(TERRAINS)
        size = max(8, round(16 * s))
        glyph_h = round(measurer(self, font(size, "bold")).metrics("linespace") * 0.9)
        for i, terrain in enumerate(TERRAINS):
            cx = x0 + col * (i + 0.5)
            self.create_image(cx, mid - 4 * s, image=icon(self, terrain, glyph_h), anchor="s")
            was, now = base[terrain], current[terrain]
            if terrain in mods and mods[terrain] < was:
                active = now <= mods[terrain]
                parts = [(f"{was}{M['mp']}", MUTED if active else INK), (M["modify"], INK),
                         (f"{mods[terrain]}{M['mp']}", INK if active else MUTED)]
            else:
                parts = [(f"{was}{M['mp']}", INK)]
            boxes = []
            for text, fill in parts:  # сначала мерим на месте, потом выравниваем по центру колонки
                boxes.append(canvas_text(self, 0, 0, text, font(size, "bold"), fill, tags="measure"))
            self.delete("measure")
            total = sum(b[2] - b[0] for b in boxes) + 3 * s * (len(parts) - 1)
            tx = cx - total / 2
            for (text, fill), (bx0, _, bx1, _) in zip(parts, boxes):
                canvas_text(self, tx, mid + 4 * s, text, font(size, "bold"), fill, anchor="nw")
                tx += bx1 - bx0 + 3 * s


class Sprites:
    """Картинки одного масштаба: основа, объекты и тени регионов (кусочки слоёв и атласа)."""

    def __init__(self, master: tk.Misc, width: int, geometry: dict):
        self.master = master
        self.base = tk.PhotoImage(master=master, file=str(ASSETS / f"base_{width}.png"))
        layer = tk.PhotoImage(master=master, file=str(ASSETS / f"objects_{width}.png"))
        s = width / geometry["size"][0]
        self.objects = {}
        for name, r in geometry["regions"].items():
            x0, y0, x1, y1 = r["object"]
            x0, y0 = max(0, int(x0 * s) - 1), max(0, int(y0 * s) - 1)
            x1, y1 = min(layer.width(), int(x1 * s) + 2), min(layer.height(), int(y1 * s) + 2)
            self.objects[name] = (self._cut(layer, x0, y0, x1 - x0, y1 - y0), x0, y0)
        atlas = tk.PhotoImage(master=master, file=str(ASSETS / f"shade_{width}.png"))
        shades = geometry["shades"][str(width)]
        self.wash, self.dark = {}, {}
        for name, (ax, ay, w, h, x, y) in shades["regions"].items():
            self.wash[name] = (self._cut(atlas, ax, ay, w, h), x, y)
            self.dark[name] = (self._cut(atlas, ax, ay + shades["half"], w, h), x, y)

    def _cut(self, source: tk.PhotoImage, x: int, y: int, w: int, h: int) -> tk.PhotoImage:
        piece = tk.PhotoImage(master=self.master, width=w, height=h)
        piece.tk.call(piece, "copy", source, "-from", x, y, x + w, y + h, "-to", 0, 0)
        return piece


def bounds(polygon: list[list[int]]) -> tuple[int, int, int, int]:
    xs = [x for x, _ in polygon]
    ys = [y for _, y in polygon]
    return min(xs), min(ys), max(xs), max(ys)
