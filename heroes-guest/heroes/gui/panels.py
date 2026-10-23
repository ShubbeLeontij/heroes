"""Панели решений: на каждый вид Decision — своя форма.

Панель собирает ответ в том же виде, что ждёт движок (как консольный игрок и Claude),
и отправляет его через app.send_answer. Окончательная проверка — движком: если ответ
отклонён, решение придёт снова с полем error, и панель покажет ошибку.

Высота панели постоянна (её задаёт окно), чтобы карта над ней не меняла размер от хода
к ходу: заголовок и кнопки на месте, а содержимое, если не помещается, прокручивается.
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from .common import (ACTION_NAMES, BAD, GOOD, OBJECT_GLYPHS, OBJECT_NAMES, PANEL_BG, RESOURCE_NAMES, TERRAIN_NAMES,
                     army_strength, artifact_text, building_text, font, name_of, object_text, plural, res, res_long,
                     terrain_of)
from . import avatars
from .icons import MARKS, RichLabel, icon, rich_radio
from .ru import reason

RESOURCES = ("wood", "iron", "gold")


# --- Общие элементы -------------------------------------------------------------------------

class Scrolled(ttk.Frame):
    """Рамка с вертикальной прокруткой; виджеты кладутся в .inner."""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=PANEL_BG, height=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self._update())
        self.canvas.bind("<Configure>", lambda e: (self.canvas.itemconfigure(self.window, width=e.width),
                                                   self._update()))
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", lambda e: self._wheel(True))
            w.bind("<Leave>", lambda e: self._wheel(False))

    def _update(self) -> None:
        height = self.inner.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), height))
        if height > self.canvas.winfo_height() > 1:
            self.bar.pack(side="right", fill="y")
        else:
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, on: bool) -> None:
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            if on:
                self.bind_all(seq, self._scroll)
            else:
                self.unbind_all(seq)

    def _scroll(self, event) -> None:
        if self.bar.winfo_ismapped():
            self.canvas.yview_scroll(-1 if event.num == 4 or getattr(event, "delta", 0) > 0 else 1, "units")


class Counter(ttk.Spinbox):
    """Поле для неотрицательного целого не больше maximum; value == -1, если введено не число."""

    def __init__(self, master, maximum: int, on_change: Callable[[], None] | None = None, width: int = 4):
        self.var = tk.StringVar(value="0")
        super().__init__(master, from_=0, to=max(0, maximum), textvariable=self.var, width=width, increment=1)
        self.maximum = maximum
        self.var.trace_add("write", lambda *_: on_change and on_change())

    @property
    def value(self) -> int:
        text = self.var.get().strip() or "0"
        return int(text) if text.isdigit() else -1

    @property
    def valid(self) -> bool:
        return 0 <= self.value <= self.maximum

    def set(self, n: int) -> None:
        self.var.set(str(n))

    def set_maximum(self, maximum: int) -> None:
        self.maximum = maximum
        self.configure(to=max(0, maximum))


MAX_COMBINATIONS = 50_000   # больше заявок cheapest_army не перебирает (окно не должно замирать)


def cheapest_army(units: dict, need: int, rules: dict | None = None, apart: str | None = None) -> dict[str, int] | None:
    """Самая слабая заявка, которой хватает для победы (сила больше need): все заявленные погибнут,
    так что лишних посылать незачем. При равной силе — меньше единиц. None — не хватит и всех.
    rules — как считать силу (common.army_strength); apart — гидра в другом регионе: она сражается только одна."""
    best, best_key = None, None
    ranges = [range(u["count"] + 1) for u in units.values()]
    total = 1
    for r in ranges:
        total *= len(r)
    if total > MAX_COMBINATIONS:   # слишком много вариантов: всех, кроме отдельной гидры
        army = {u: v["count"] for u, v in units.items() if u != apart and v["count"]}
        return army if army_strength(units, army, rules) > need else None

    def walk(i: int, chosen: list[int]) -> None:
        nonlocal best, best_key
        if i == len(ranges):
            army = {uid: n for uid, n in zip(units, chosen) if n}
            if apart in army and len(army) > 1:
                return
            strength = army_strength(units, army, rules)
            key = (strength, sum(chosen))
            if strength > need and (best_key is None or key < best_key):
                best, best_key = army, key
            return
        for n in ranges[i]:
            walk(i + 1, chosen + [n])

    walk(0, [])
    return best


def best_strength(units: dict, rules: dict | None = None, apart: str | None = None) -> int:
    """Наибольшая сила заявки: все войска, а если гидра стоит отдельно — либо войска без неё, либо она одна."""
    if apart not in units:
        return army_strength(units, None, rules)
    army = {u: v["count"] for u, v in units.items() if u != apart}
    return max(army_strength(units, army, rules), army_strength(units, {apart: 1}, rules))


class ArmyPicker(ttk.Frame):
    """Заявка войск: сколько единиц каждого вида; итоговая сила и хватит ли её.
    rules — как считать силу (краки, механики, прибавки); apart — гидра, которая стоит отдельно и сражается одна."""

    def __init__(self, master, units: dict, need: int | None = None, on_change: Callable[[], None] | None = None,
                 rules: dict | None = None, apart: str | None = None):
        super().__init__(master)
        self.units, self.need, self.on_change = units, need, on_change
        self.rules, self.apart = rules, apart
        self.counters: dict[str, Counter] = {}
        self.summary = ttk.Label(self, style="Bold.TLabel")
        if not units:
            ttk.Label(self, text="У вас нет войск.", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            self.summary.grid(row=1, column=0, sticky="w")
            self.refresh()
            return
        for col, title in enumerate(("Войско", "Есть", "Сила", "Заявить")):
            ttk.Label(self, text=title, style="Muted.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 14))
        for row, (uid, u) in enumerate(units.items(), 1):
            ttk.Label(self, text=u["name"], style="Bold.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14))
            ttk.Label(self, text=str(u["count"])).grid(row=row, column=1, sticky="w")
            ttk.Label(self, text=str(u["strength"])).grid(row=row, column=2, sticky="w")
            self.counters[uid] = Counter(self, u["count"], self.refresh)
            self.counters[uid].grid(row=row, column=3, sticky="w", pady=1)
        row = len(units) + 1
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Button(buttons, text="Все", command=self.all, style="Small.TButton").pack(side="left")
        ttk.Button(buttons, text="Никого", command=self.none, style="Small.TButton").pack(side="left", padx=4)
        self.summary.grid(row=row + 1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.refresh()

    def all(self) -> None:
        for uid, c in self.counters.items():
            c.set(self.units[uid]["count"])

    def none(self) -> None:
        for c in self.counters.values():
            c.set(0)

    def preset(self, army: dict[str, int]) -> None:
        for uid, c in self.counters.items():
            c.set(army.get(uid, 0))

    @property
    def valid(self) -> bool:
        return all(c.valid for c in self.counters.values()) and not self.mixed()

    def mixed(self) -> bool:
        """Отдельная гидра заявлена вместе с войсками — так нельзя."""
        army = {uid: c.value for uid, c in self.counters.items() if c.value > 0}
        return self.apart in army and len(army) > 1

    def army(self) -> dict[str, int]:
        return {uid: c.value for uid, c in self.counters.items() if c.value > 0}

    def strength(self) -> int:
        return army_strength(self.units, self.army(), self.rules) if self.valid else 0

    def set_need(self, need: int | None) -> None:
        self.need = need
        self.refresh()

    def refresh(self) -> None:
        if self.mixed():
            self.summary.configure(text=f"{self.units[self.apart]['name']} стоит в другом регионе и сражается только одна",
                                   foreground=BAD)
        elif not self.valid:
            self.summary.configure(text="Введите числа в пределах того, что у вас есть", foreground=BAD)
        else:
            s = self.strength()
            bonus = (self.rules or {}).get("bonus", 0)
            text, color = f"Заявленная сила: {s}" + (f" (с прибавкой +{bonus} к итогу)" if bonus else ""), ""
            if self.need is not None:
                enough = s > self.need
                text += f" — хватает (нужно больше {self.need})" if enough else f" — НЕ хватает: нужно больше {self.need}"
                color = GOOD if enough else BAD
            self.summary.configure(text=text, foreground=color)
        if self.on_change:
            self.on_change()


# --- Панели -----------------------------------------------------------------------------------

class Panel(ttk.Frame):
    title = "Решение"
    secret = False

    def __init__(self, master, app, message: dict):
        super().__init__(master, padding=(12, 8))
        self.app, self.message = app, message
        self.data, self.state, self.static = message["data"], message["state"], app.static
        self.me = message["player"]
        self.player = self.state["players"][self.me]
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text=self.heading(), style="Title.TLabel").pack(side="left")
        if self.secret:
            ttk.Label(head, text="  тайный выбор", style="Secret.TLabel").pack(side="left")
        if message.get("error"):
            ttk.Label(self, text="✗ Ответ не принят: " + message["error"], foreground=BAD,
                      wraplength=700).pack(fill="x", pady=(4, 0))
        self.footer = ttk.Frame(self)
        self.footer.pack(fill="x", side="bottom", pady=(8, 0))
        scrolled = Scrolled(self)
        scrolled.pack(fill="both", expand=True, pady=(6, 0))
        self.body = scrolled.inner
        self.build()

    def heading(self) -> str:
        return self.title

    def build(self) -> None:
        raise NotImplementedError

    def note(self, text: str, style: str = "Muted.TLabel", master=None) -> ttk.Label:
        label = ttk.Label(master or self.body, text=text, style=style, wraplength=700, justify="left")
        label.pack(fill="x", anchor="w", pady=(0, 4))
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - 8)))
        return label

    def button(self, text: str, command: Callable[[], None], primary: bool = False) -> ttk.Button:
        b = ttk.Button(self.footer, text=text, command=command, style="Primary.TButton" if primary else "TButton")
        b.pack(side="right", padx=(6, 0))
        return b

    def send(self, answer: Any) -> None:
        self.app.send_answer(self.message, answer)

    def object(self, region: str) -> str:
        """Объект региона глазами решающего (условие артефакта — его фракции)."""
        return object_text(self.static, self.state, region, self.player["faction"])

    def name(self, p: int | str, avatar: bool = False) -> str:
        return name_of(self.static, int(p), avatar)

    def player_note(self, p: int | str, text: str, style: str = "TLabel") -> None:
        """Строка об игроке: его аватарка, затем «Имя (Фракция): text» с переносом строк."""
        row = ttk.Frame(self.body)
        row.pack(fill="x", anchor="w", pady=(0, 4))
        ttk.Label(row, image=avatars.seat(self, int(p), 20)).pack(side="left", anchor="n", padx=(0, 4))
        label = ttk.Label(row, text=f"{self.name(p)}{text}", style=style, justify="left", wraplength=660)
        label.pack(side="left", fill="x", expand=True, anchor="w")
        # ширину переноса задаёт строка (она во всю ширину панели), а не сама подпись: иначе подпись сжимается
        row.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - 40)))


class RegionChoice(Panel):
    """Выбор региона списком и щелчком по карте."""

    def region_list(self, options: list[str], labels: dict[str, str], badges: dict[str, str],
                    selected: str | None = None) -> None:
        self.options, self.choice = options, None
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, height=min(7, max(3, len(options))), activestyle="none", exportselection=False,
                                  font=font(10), selectbackground="#e8590c", relief="flat", highlightthickness=1)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=bar.set)
        for r in options:
            self.listbox.insert("end", labels[r])
        self.listbox.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._listbox_pick)
        self.listbox.bind("<Double-1>", lambda e: self.confirm())
        self.app.map.select_mode(badges, None, self.pick)
        if selected:
            self.pick(selected)

    def _listbox_pick(self, event=None) -> None:
        sel = self.listbox.curselection()
        if sel:
            self.pick(self.options[sel[0]], from_list=True)

    def pick(self, region: str, from_list: bool = False) -> None:
        self.choice = region
        if not from_list:
            i = self.options.index(region)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(i)
            self.listbox.see(i)
        self.app.map.set_selected(region)
        self.picked()

    def picked(self) -> None:
        pass

    def confirm(self) -> None:
        if self.choice is not None:
            self.send(self.choice)


class ChooseStart(RegionChoice):
    title = "Выберите стартовый регион"

    def build(self):
        regions = self.static["regions"]
        options = self.data["options"]
        self.note("Стартовые регионы — с порталом. Ресурсы региона вы получите сразу. "
                  "Щёлкните регион на карте или выберите его в списке.")
        labels = {r: f"{r} — {TERRAIN_NAMES[regions[r]['terrain']]}, ресурсы: {res_long(regions[r]['resources'])}"
                  for r in options}
        self.ok = self.button("Начать здесь", self.confirm, primary=True)
        self.ok.state(["disabled"])
        self.region_list(options, labels, {r: res(regions[r]["resources"]) for r in options})

    def picked(self):
        self.ok.state(["!disabled"])
        self.ok.configure(text=f"Начать: {self.choice}")


class ActionOrder(Panel):
    """Порядок действий: четыре места (уровни 1–4). Щелчок по действию ставит его на первое свободное
    место, повторный — убирает; действие можно перетащить мышью на любое место (с места на место —
    поменять местами, за пределы мест — убрать); правая кнопка по выбранному действию — убрать."""
    title = "Вы первый игрок: выберите 4 действия"
    DRAG_START = 5   # на сколько пикселей сдвинуть мышь, чтобы щелчок стал перетаскиванием

    def build(self):
        self.order: list[str | None] = [None] * 4
        self.drag: dict | None = None
        self.note("Выберите 4 действия из 5 в том порядке, в каком они будут выполняться; пятое в этом раунде "
                  "не выполняет никто. Место в порядке — это уровень действия: на уровне N добывается N ресурса, "
                  "даётся N очков перемещения или все строят здание уровня не выше N.")
        self.note("Щелчок — на первое свободное место, перетаскивание — на любое; правая кнопка "
                  "или перетаскивание за пределы мест — убрать.")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=4)
        self.buttons = {}
        right = "<Button-2>" if self.tk.call("tk", "windowingsystem") == "aqua" else "<Button-3>"
        for a, label in ACTION_NAMES.items():
            b = ttk.Button(row, text=label, width=15)
            b.pack(side="left", padx=(0, 6))
            self.draggable(b, lambda a=a: a, lambda a=a: self.toggle(a))
            b.bind(right, lambda e, a=a: self.remove(a))
            self.buttons[a] = b
        slots = ttk.Frame(self.body)
        slots.pack(fill="x", pady=(8, 0))
        self.slot_labels = []
        for i in range(4):
            label = ttk.Label(slots, width=18)
            label.pack(side="left", padx=(0, 6))
            self.draggable(label, lambda i=i: self.order[i], lambda: None)
            label.bind(right, lambda e, i=i: self.remove(self.order[i]))
            self.slot_labels.append(label)
        self.skipped = ttk.Label(slots, style="Muted.TLabel")
        self.skipped.pack(side="left", padx=8)
        self.ok = self.button("Подтвердить порядок", lambda: self.send(list(self.order)), primary=True)
        self.button("Сбросить", self.clear)
        self.refresh()

    # --- выбор ---------------------------------------------------------------------------

    def toggle(self, action: str) -> None:
        if action in self.order:
            self.remove(action)
        elif None in self.order:
            self.order[self.order.index(None)] = action
            self.refresh()

    def remove(self, action: str | None) -> None:
        if action in self.order:
            self.order[self.order.index(action)] = None
            self.refresh()

    def put(self, action: str, slot: int) -> None:
        """Поставить action на место slot. Если оно было на другом месте — поменять местами с тем,
        кто стоит на slot; если пришло из ряда действий — вытесненное действие возвращается в ряд."""
        if action in self.order:
            here = self.order.index(action)
            self.order[here], self.order[slot] = self.order[slot], action
        else:
            self.order[slot] = action
        self.refresh()

    def clear(self) -> None:
        self.order = [None] * 4
        self.refresh()

    def refresh(self) -> None:
        for i, label in enumerate(self.slot_labels):
            a = self.order[i]
            label.configure(text=f"{i + 1}. {ACTION_NAMES[a] if a else '—'}",
                            style="Slot.TLabel" if a else "EmptySlot.TLabel")
        full = None not in self.order
        for a, b in self.buttons.items():
            b.state(["pressed"] if a in self.order else ["!pressed"])
        self.ok.state(["!disabled"] if full else ["disabled"])
        skipped = [a for a in ACTION_NAMES if a not in self.order]
        self.skipped.configure(text=f"пропускается: {ACTION_NAMES[skipped[0]].lower()}" if full else "")

    # --- перетаскивание -----------------------------------------------------------------

    def draggable(self, widget: tk.Widget, action: Callable[[], str | None], click: Callable[[], None]) -> None:
        """Нажатие и отпускание без сдвига — click(); со сдвигом — перетаскивание действия action()."""
        widget.bind("<ButtonPress-1>", lambda e: self._press(e, action()))
        widget.bind("<B1-Motion>", self._motion)
        widget.bind("<ButtonRelease-1>", lambda e: self._release(e, click))

    def _press(self, event, action: str | None) -> None:
        self.drag = {"action": action, "x": event.x_root, "y": event.y_root, "ghost": None, "over": None}

    def _motion(self, event) -> None:
        d = self.drag
        if not d or not d["action"]:
            return
        if d["ghost"] is None:
            if abs(event.x_root - d["x"]) + abs(event.y_root - d["y"]) < self.DRAG_START:
                return
            top = self.winfo_toplevel()
            d["ghost"] = ttk.Label(top, text=ACTION_NAMES[d["action"]], style="Slot.TLabel")
            top.configure(cursor="fleur")
        top = self.winfo_toplevel()
        d["ghost"].place(x=event.x_root - top.winfo_rootx() + 12, y=event.y_root - top.winfo_rooty() + 8)
        d["ghost"].lift()
        over = self._slot_at(event.x_root, event.y_root)
        if over != d["over"]:
            if d["over"] is not None:
                self.refresh()
            if over is not None:
                self.slot_labels[over].configure(style="Drop.TLabel")
            d["over"] = over

    def _release(self, event, click: Callable[[], None]) -> None:
        d, self.drag = self.drag, None
        if not d or d["ghost"] is None:   # не тащили — обычный щелчок
            if d and str(event.widget.winfo_containing(event.x_root, event.y_root)) == str(event.widget):
                click()
            self.after_idle(self.refresh)   # кнопка сама снимает «нажатость» после отпускания
            return
        d["ghost"].destroy()
        self.winfo_toplevel().configure(cursor="")
        slot = self._slot_at(event.x_root, event.y_root)
        if slot is not None:
            self.put(d["action"], slot)
        else:
            self.remove(d["action"])   # выбранное действие утащили за пределы мест — убрать его
        self.after_idle(self.refresh)

    def _slot_at(self, x: int, y: int) -> int | None:
        widget = self.winfo_containing(x, y)
        return next((i for i, label in enumerate(self.slot_labels) if widget is label), None)


class DistributeResources(Panel):
    title = "Выберите ресурсы"

    def build(self):
        self.amount = self.data["amount"]
        allowed = [r for r in RESOURCES if r in self.data.get("allowed", RESOURCES)]
        self.note(reason(self.data.get("reason", "")).capitalize() + ".", "TLabel")
        self.note(f"Распределите {self.amount}: " + ", ".join(RESOURCE_NAMES[r] for r in allowed) + ".")
        grid = ttk.Frame(self.body)
        grid.pack(anchor="w")
        self.counters = {}
        for col, k in enumerate(allowed):
            ttk.Label(grid, text=" " + RESOURCE_NAMES[k], image=icon(grid, k, 18), compound="left",
                      style="Bold.TLabel").grid(row=0, column=col, padx=(0, 16), sticky="w")
            self.counters[k] = Counter(grid, self.amount, self.refresh)
            self.counters[k].grid(row=1, column=col, padx=(0, 16), sticky="w")
            ttk.Button(grid, text=f"всё — {RESOURCE_NAMES[k]}", style="Small.TButton",
                       command=lambda k=k: self.all_to(k)).grid(row=2, column=col, padx=(0, 16), pady=2, sticky="w")
        self.summary = ttk.Label(self.body, style="Bold.TLabel")
        self.summary.pack(anchor="w", pady=(6, 0))
        self.ok = self.button("Подтвердить", self.confirm, primary=True)
        self.refresh()

    def all_to(self, k: str) -> None:
        for key, c in self.counters.items():
            c.set(self.amount if key == k else 0)

    def refresh(self) -> None:
        valid = all(c.valid for c in self.counters.values())
        total = sum(c.value for c in self.counters.values()) if valid else -1
        ok = total == self.amount
        self.summary.configure(text=f"Выбрано {total if valid else '?'} из {self.amount}",
                               foreground=GOOD if ok else BAD)
        if hasattr(self, "ok"):
            self.ok.state(["!disabled"] if ok else ["disabled"])

    def confirm(self) -> None:
        self.send({k: c.value for k, c in self.counters.items()})


class BuildRecruit(Panel):
    def heading(self):
        return f"Строительство и наём — уровень {self.data['level']}"

    def build(self):
        data = self.data
        faction = self.player["faction"]
        self.stock = data["resources"]
        self.note(f"Склад: {res_long(self.stock)}. Можно построить одно здание уровня не выше {data['level']}, "
                  "затем нанять любые войска, на которые хватит ресурсов.")
        f = data.get("foundation")
        if f:
            self.note(f"Вы в регионе {f['region']} с фундаментом (вписан уровень {f['level']}): здание более "
                      "высокого уровня возводится на нём автоматически и приносит столько ПО, каков его уровень.",
                      "Hint.TLabel")
        cols = ttk.Frame(self.body)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True)
        self.right = ttk.Frame(cols)
        self.right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.choice = tk.StringVar(value="")
        ttk.Label(left, text="Здание (или щелчком на планшете справа)", style="Bold.TLabel").pack(anchor="w")
        ttk.Radiobutton(left, text="Ничего не строить", value="", variable=self.choice,
                        command=self.chosen).pack(anchor="w")
        for bid, o in data["options"].items():
            extra = f" (+{o['level']} ПО на фундаменте)" if f and o["level"] > f["level"] else ""
            rich_radio(left, f"{o['name']} — уровень {o['level']}, {o['vp']} ПО{extra}, цена {res(o['cost'])}",
                       value=bid, variable=self.choice, command=self.chosen).pack(anchor="w")
            ttk.Label(left, text=building_text(self.static, faction, bid), style="Muted.TLabel",
                      wraplength=360).pack(anchor="w", padx=(24, 0))
        if not data["options"]:
            ttk.Label(left, text="Сейчас ничего построить нельзя.", style="Muted.TLabel").pack(anchor="w")
        self.ok = self.button("Подтвердить", self.confirm, primary=True)
        self.counters: dict[str, Counter] = {}
        self.rebuild_hire()
        if data["options"]:   # те же здания можно выбрать щелчком по карточке на планшете
            self.app.pick_building(self.message["player"], set(data["options"]), self.board_pick)

    def chosen(self) -> None:
        """Выбрано в списке: отметить и на планшете."""
        self.app.board.set_picked(self.choice.get() or None)
        self.rebuild_hire()

    def board_pick(self, bid: str | None) -> None:
        """Выбрано щелчком на планшете (повторный щелчок — ничего не строить)."""
        self.choice.set(bid or "")
        self.rebuild_hire()

    def building_cost(self) -> dict:
        bid = self.choice.get()
        return self.data["options"][bid]["cost"] if bid else {}

    def rebuild_hire(self) -> None:
        old = {u: c.value for u, c in self.counters.items()}
        for w in self.right.winfo_children():
            w.destroy()
        ttk.Label(self.right, text="Наём", style="Bold.TLabel").grid(row=0, column=0, sticky="w")
        hires = self.data.get("hire_by_build", {}).get(self.choice.get())
        if hires is None:  # сервер без подсказок: только то, что можно нанять уже сейчас
            hires = self.data.get("hire", {})
        left = {k: self.stock.get(k, 0) - self.building_cost().get(k, 0) for k in self.stock}
        self.hires = hires
        self.counters = {}
        for row, (uid, h) in enumerate(hires.items(), 1):
            most = min((left.get(k, 0) // v for k, v in h["cost"].items() if v), default=0)
            RichLabel(self.right, f"{h['name']} ({res(h['cost'])})").grid(row=row, column=0, sticky="w", padx=(0, 10))
            c = Counter(self.right, max(0, most), self.refresh)
            c.grid(row=row, column=1, sticky="w", pady=1)
            if old.get(uid, 0) > 0:
                c.set(old[uid])
            self.counters[uid] = c
        if not hires:
            ttk.Label(self.right, text="Нанимать некого: нет здания для найма.",
                      style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        self.summary = ttk.Label(self.right, wraplength=320)
        self.summary.grid(row=len(hires) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.refresh()

    def total_cost(self) -> dict[str, int] | None:
        total = dict(self.building_cost())
        for uid, c in self.counters.items():
            if c.value < 0:
                return None
            for k, v in self.hires[uid]["cost"].items():
                total[k] = total.get(k, 0) + v * c.value
        return total

    def refresh(self) -> None:
        total = self.total_cost()
        if total is None:
            self.summary.configure(text="Введите целые числа", foreground=BAD)
            self.ok.state(["disabled"])
            return
        left = {k: self.stock.get(k, 0) - total.get(k, 0) for k in {**self.stock, **total}}
        ok = all(v >= 0 for v in left.values())
        self.summary.configure(text=f"Всего к оплате: {res_long(total)}.\nОстанется: "
                                    + ", ".join(f"{RESOURCE_NAMES.get(k, k)} {v}" for k, v in left.items()),
                               foreground="" if ok else BAD)
        self.ok.state(["!disabled"] if ok else ["disabled"])

    def confirm(self) -> None:
        hire = {u: c.value for u, c in self.counters.items() if c.value > 0}
        self.send({"build": self.choice.get() or None, "hire": hire})


class ReserveObject(RegionChoice):
    title = "Башня иллюзий"

    def build(self):
        options = sorted(self.data["options"])
        self.note(f"Можно заплатить ({res_long(self.data['cost'])}), чтобы объект в одном регионе — крепость, "
                  "артефакт, портал или фундамент — стал доступен только вам. Можно отказаться.")
        labels = {r: f"{r}: {self.object(r)}" for r in options}
        badges = {r: OBJECT_GLYPHS.get(self.data["options"][r], "") + " " + OBJECT_NAMES.get(self.data["options"][r], "")
                  for r in options}
        self.ok = self.button("Забронировать", self.confirm, primary=True)
        self.ok.state(["disabled"])
        self.button("Отказаться", lambda: self.send(None))
        self.region_list(options, labels, badges)

    def picked(self):
        self.ok.state(["!disabled"])
        self.ok.configure(text=f"Забронировать: {self.choice}")


class Destination(RegionChoice):
    secret = True

    def heading(self):
        return f"Перемещение: {self.data['mp']} ОП из региона {self.player['region']}"

    def build(self):
        opts = self.data["options"]
        here = self.player["region"]
        options = sorted(opts, key=lambda r: (opts[r]["cost"], r))
        others = {}
        for q, pl in enumerate(self.state["players"]):
            if q != self.me:
                others.setdefault(pl["region"], []).append(name_of(self.static, q))
            if pl.get("hydra"):   # отдельная гидра наги сражается со всеми, кто окажется в её регионе
                others.setdefault(pl["hydra"], []).append("гидра игрока " + name_of(self.static, q))
        labels, badges = {}, {}
        for r in options:
            region = self.static["regions"][r]
            info = opts[r]
            parts = [f"{r}{' (остаться)' if r == here else ''}", f"цена {info['cost']}, останется {info['left']} ОП",
                     TERRAIN_NAMES[terrain_of(self.static, self.state, r)], self.object(r)]
            if r not in self.player["visited"] and region["resources"]:
                parts.append(f"новый: {res_long(region['resources'])}")  # в списке картинок нет — словами
            if r in others:
                parts.append("сейчас там: " + ", ".join(others[r]))
            labels[r] = " · ".join(parts)
            badges[r] = "остаться" if r == here else f"{info['cost']} ОП"
        self.note("Куда пошли остальные, станет известно, когда выберут все. Неистраченные очки перемещения "
                  "позволяют в этом раунде пропустить чудовищ в вашей линии.")
        self.ok = self.button("Остаться", self.confirm, primary=True)
        self.region_list(options, labels, badges, selected=here)

    def picked(self):
        here = self.player["region"]
        self.ok.configure(text="Остаться" if self.choice == here else f"Идти: {self.choice}")


class RegionStance(Panel):
    LABELS = {"fight": "Сражаться", "claim": "Претендовать на объект", "pass": "Пас (уступить)"}

    def heading(self):
        return f"Встреча в регионе {self.data['region']}"

    def build(self):
        region = self.data["region"]
        self.note(f"Объект: {self.object(region)}.", "TLabel")
        for q, pl in enumerate(self.state["players"]):
            if pl["region"] == region and q != self.me:
                army = ", ".join(f"{self.static['factions'][pl['faction']]['units'][u]['name']} ×{v['count']}"
                                 for u, v in pl["units"].items() if v["count"]) or "войск нет"
                self.player_note(q, f": {army} (общая сила {pl['army_strength']})")
        declared = self.data.get("declared", {})
        if declared:
            self.note("Уже заявили:", "Bold.TLabel")
            for q, s in declared.items():
                self.player_note(q, f" — {self.LABELS[s].lower()}", "Bold.TLabel")
        self.note(f"Сила вашей армии: {self.player['army_strength']}. Сражение начнётся, если кто-то выберет "
                  "«сражаться» или на объект претендуют двое и больше. В сражении войска заявляются тайно, "
                  "все заявленные погибают.")
        for option in reversed(self.data["options"]):
            self.button(self.LABELS.get(option, option), lambda o=option: self.send(o), primary=option == "fight")


class CommitArmy(Panel):
    secret = True

    def heading(self):
        return f"Сражение в регионе {self.data['region']}"

    def build(self):
        rules = self.data.get("opponent_rules", {})
        for q, units in self.data["opponents"].items():
            desc = ", ".join(f"{u['name']} ×{u['count']} ({u['strength']})" for u in units.values()) or "войск нет"
            self.player_note(q, f": {desc} — не больше {army_strength(units, None, rules.get(q))}")
        for q, strength in self.data.get("hydras", {}).items():
            self.player_note(q, f": отдельная гидра, сила {strength}. Награды в этом сражении не получит никто.")
        self.note("Побеждает наибольшая заявленная сила; при равенстве проигрывают все. Все заявленные войска "
                  "погибают при любом исходе. Можно никого не заявлять.")
        self.picker = ArmyPicker(self.body, self.data["units"], on_change=self.refresh,
                                 rules=self.data.get("strength_rules"))
        self.picker.pack(anchor="w")
        self.ok = self.button("Заявить", lambda: self.send(self.picker.army()), primary=True)
        self.refresh()

    def refresh(self):
        if hasattr(self, "ok"):
            self.ok.state(["!disabled"] if self.picker.valid else ["disabled"])


class ChooseReward(Panel):
    title = "Победа — выберите награду"

    def build(self):
        opts = self.data["options"]
        self.kind = tk.StringVar(value=next(iter(opts)))
        if "monster" in opts:
            ttk.Radiobutton(self.body, text=f"Награда обведённого чудовища: {res_long(opts['monster'])}",
                            value="monster", variable=self.kind, command=self.refresh).pack(anchor="w")
        self.counters: dict[str, dict[str, Counter]] = {}
        if "resources" in opts:
            amount = opts["resources"]["amount"]
            ttk.Radiobutton(self.body, text=f"Забрать у проигравших до {amount} ресурсов",
                            value="resources", variable=self.kind, command=self.refresh).pack(anchor="w")
            grid = ttk.Frame(self.body)
            grid.pack(anchor="w", padx=(24, 0))
            for col, k in enumerate(RESOURCES, 1):
                ttk.Label(grid, text=" " + RESOURCE_NAMES[k], image=icon(grid, k, 16), compound="left",
                          style="Muted.TLabel").grid(row=0, column=col, sticky="w")
            for row, (q, pool) in enumerate(opts["resources"]["from"].items(), 1):
                RichLabel(grid, f"{self.name(q, avatar=True)} (есть {res(pool)})").grid(row=row, column=0, sticky="w",
                                                                                     padx=(0, 10))
                self.counters[q] = {}
                for col, k in enumerate(RESOURCES, 1):
                    c = Counter(grid, min(pool.get(k, 0), amount), self.refresh)
                    c.grid(row=row, column=col, padx=(0, 6), pady=1)
                    self.counters[q][k] = c
        self.artifact = tk.StringVar()
        if "artifact" in opts:
            ttk.Radiobutton(self.body, text="Отобрать артефакт:", value="artifact", variable=self.kind,
                            command=self.refresh).pack(anchor="w")
            for q, arts in opts["artifact"].items():
                for a in arts:
                    rich_radio(self.body, f"{a} (у игрока {self.name(q, avatar=True)})", value=json.dumps([q, a]),
                               variable=self.artifact, command=self.refresh).pack(anchor="w", padx=(24, 0))
        self.summary = ttk.Label(self.body)
        self.summary.pack(anchor="w", pady=(6, 0))
        self.ok = self.button("Взять награду", self.confirm, primary=True)
        self.refresh()

    def taken(self) -> dict | None:
        take = {}
        for q, counters in self.counters.items():
            if not all(c.valid for c in counters.values()):
                return None
            got = {k: c.value for k, c in counters.items() if c.value}
            if got:
                take[q] = got
        return take

    def refresh(self) -> None:
        if not hasattr(self, "ok"):
            return
        kind, ok, text = self.kind.get(), True, ""
        if kind == "resources":
            take = self.taken()
            amount = self.data["options"]["resources"]["amount"]
            total = sum(sum(t.values()) for t in take.values()) if take is not None else None
            ok = total is not None and total <= amount
            text = f"Берёте {total if total is not None else '?'} из {amount} возможных"
        elif kind == "artifact":
            ok = bool(self.artifact.get())
            text = "" if ok else "Выберите, какой артефакт"
        self.summary.configure(text=text, foreground="" if ok else BAD)
        self.ok.state(["!disabled"] if ok else ["disabled"])

    def confirm(self) -> None:
        kind = self.kind.get()
        if kind == "monster":
            self.send({"type": "monster"})
        elif kind == "resources":
            self.send({"type": "resources", "take": self.taken()})
        else:
            q, a = json.loads(self.artifact.get())
            self.send({"type": "artifact", "from": q, "artifact": a})


class CaptureFortress(Panel):
    def heading(self):
        return f"Крепость в регионе {self.data['region']}"

    def build(self):
        d = self.data
        self.note(f"Сила {d['strength']}. Награда: {d['vp']} ПО и {res_long(d['loot'])}. Ваша сила должна быть "
                  f"больше {d['strength']}; заявленные войска погибают при любом исходе.", "TLabel")
        rules = d.get("strength_rules")
        self.picker = ArmyPicker(self.body, d["units"], need=d["strength"], on_change=self.refresh, rules=rules)
        self.picker.pack(anchor="w")
        self.picker.preset(cheapest_army(d["units"], d["strength"], rules) or {})
        self.ok = self.button("Штурмовать", lambda: self.send(self.picker.army()), primary=True)
        self.button("Не штурмовать", lambda: self.send(None))
        self.refresh()

    def refresh(self):
        if hasattr(self, "ok"):
            ok = self.picker.valid and (self.picker.army() or self.picker.strength() > 0)   # прибавка к силе — и без войск
            self.ok.state(["!disabled"] if ok else ["disabled"])


class FightMonster(Panel):
    title = "Бой с чудовищем"

    def build(self):
        cands = self.data["candidates"]
        self.rules = self.data.get("strength_rules")
        alone = self.data.get("hydra_alone")
        self.apart = alone["unit"] if alone else None
        top = best_strength(self.data["units"], self.rules, self.apart)
        self.note(f"Не больше одного чудовища за раунд. Неистраченные очки перемещения позволяют пропустить "
                  f"чудовищ в вашей линии. Ваша наибольшая сила: {top}.")
        if alone:
            self.note(f"Гидра стоит отдельно, в регионе {alone['region']}: она может сразиться вместо войска — одна; "
                      "добыча чудовища тогда теряется, гидра погибает.", "Hint.TLabel")
        self.choice = tk.StringVar(value="")
        for key in sorted(cands, key=int):
            c = cands[key]
            reward = f"{c['vp']} ПО" + (f" + {res_long(c['loot'])}" if c["loot"] else "")
            hint = "" if c["strength"] < top else " (не хватает силы)"
            ttk.Radiobutton(self.body, text=f"Сила {c['strength']} → {reward}{hint}", value=key,
                            variable=self.choice, command=self.picked).pack(anchor="w")
        self.picker = ArmyPicker(self.body, self.data["units"], on_change=self.refresh, rules=self.rules, apart=self.apart)
        self.picker.pack(anchor="w", pady=(6, 0))
        self.ok = self.button("Сражаться", self.confirm, primary=True)
        self.button("Не сражаться", lambda: self.send(None))
        reachable = [k for k in sorted(cands, key=int) if cands[k]["strength"] < top]
        if reachable:
            self.choice.set(reachable[-1])
            self.picked()
        self.refresh()

    def picked(self):
        need = self.data["candidates"][self.choice.get()]["strength"]
        self.picker.set_need(need)
        self.picker.preset(cheapest_army(self.data["units"], need, self.rules, self.apart) or {})

    def refresh(self):
        if hasattr(self, "ok"):
            ok = (bool(self.choice.get()) and self.picker.valid
                  and (bool(self.picker.army()) or self.picker.strength() > 0))
            self.ok.state(["!disabled"] if ok else ["disabled"])

    def confirm(self):
        self.send({"monster": int(self.choice.get()), "army": self.picker.army()})


class TakeArtifact(Panel):
    def heading(self):
        return f"Артефакт в регионе {self.data['region']}"

    def build(self):
        cond = self.data["condition"]
        self.note(artifact_text(self.static, self.player["faction"], self.data["region"]), "Bold.TLabel")
        self.note(f"Цена: {res_long(cond['cost'])}." if cond["condition"] == "pay" else "Условие выполнено, платить не нужно.")
        self.button("Взять", lambda: self.send(True), primary=True)
        self.button("Не брать", lambda: self.send(False))


# --- Особые решения фракций ---------------------------------------------------------------------

def notes_text(notes: list[int]) -> str:
    """Пары нот значками: обведённая нота — значок ноты, пустая — кружок."""
    return "   ".join(MARKS["note"] * n + "○" * (2 - n) for n in notes)


class YesNo(Panel):
    """Да/нет: Гейзер наг, Библиотека гномов, Часовня нежити."""

    def heading(self):
        return {"make_lake": "Гейзер", "buy_science": "Библиотека", "exchange_on_gain": "Часовня"}[self.message["kind"]]

    def build(self):
        d, kind = self.data, self.message["kind"]
        if kind == "make_lake":
            terrain = TERRAIN_NAMES[terrain_of(self.static, self.state, d["region"])]
            text = (f"Потратить {d['cost_mp']} ОП из {d['left']} неистраченных, чтобы регион {d['region']} ({terrain}) "
                    "стал озёрным — для всех игроков. Неистраченные ОП позволяют пропустить чудовищ в вашей линии.")
            yes, no = "Сделать озером", "Не нужно"
        elif kind == "buy_science":
            text = (f"Купить науку (+{d['value']}) за: {res_long(d['cost'])}? Наука покупается до выбора здания, "
                    f"так что может сразу сделать его бесплатным. Сейчас науки: {d['science']}.")
            yes, no = "Купить науку", "Не покупать"
        else:
            unit = self.static["factions"][self.player["faction"]]["units"][d["unit"]]["name"]
            name = RESOURCE_NAMES[d["resource"]]
            text = (f"Вы получаете {name}: {d['gained']}. Обменять {d['pay']} из них на {unit} ×{d['count']}? "
                    "Остальное попадёт на склад.")
            yes, no = "Обменять", "Оставить ресурсы"
        self.note(text, "TLabel")
        self.button(yes, lambda: self.send(True), primary=True)
        self.button(no, lambda: self.send(False))


class BuyMovePoints(Panel):
    title = "Катафалк: дополнительные очки перемещения"

    def build(self):
        d = self.data
        RichLabel(self.body, f"У вас {d['mp']}{MARKS['mp']}. Каждое дополнительное ОП стоит {res(d['cost'])}; "
                             f"можно купить до {d['max']}.").pack(anchor="w")
        row = ttk.Frame(self.body)
        row.pack(anchor="w", pady=6)
        ttk.Label(row, text="Купить ОП:").pack(side="left")
        self.counter = Counter(row, d["max"], self.refresh)
        self.counter.pack(side="left", padx=6)
        self.summary = ttk.Label(self.body, style="Bold.TLabel")
        self.summary.pack(anchor="w")
        self.ok = self.button("Купить", lambda: self.send(self.counter.value), primary=True)
        self.button("Не покупать", lambda: self.send(0))
        self.refresh()

    def refresh(self):
        if not hasattr(self, "ok"):
            return
        n = self.counter.value
        ok = self.counter.valid
        cost = {k: v * n for k, v in self.data["cost"].items()} if ok else {}
        self.summary.configure(text=f"Будет ОП: {self.data['mp'] + n}, заплатите: {res_long(cost)}" if ok
                               else f"Введите число от 0 до {self.data['max']}", foreground="" if ok else BAD)
        self.ok.state(["!disabled"] if ok and n else ["disabled"])


class BattleUpgrade(Panel):
    """Лавка бронника эльфов: модификация заявленных единиц перед сражением (в бою игроков — тайно)."""
    title = "Лавка бронника"

    @property
    def secret(self) -> bool:
        return bool(self.data.get("secret"))

    def build(self):
        d = self.data
        gains = ", ".join(f"+{g}" for g in d["gains"])
        self.note(f"Перед сражением можно заплатить {res_long(d['cost_per_unit'])} за каждую заявленную единицу, "
                  f"чтобы усилить её в этом сражении. Усиливаются те, кому это даёт больше всего: {gains}.", "TLabel")
        row = ttk.Frame(self.body)
        row.pack(anchor="w", pady=6)
        ttk.Label(row, text="Модифицировать единиц:").pack(side="left")
        self.counter = Counter(row, d["max"], self.refresh)
        self.counter.pack(side="left", padx=6)
        self.summary = ttk.Label(self.body, style="Bold.TLabel")
        self.summary.pack(anchor="w")
        self.ok = self.button("Модифицировать", lambda: self.send(self.counter.value), primary=True)
        self.button("Не нужно", lambda: self.send(0))
        self.refresh()

    def refresh(self):
        if not hasattr(self, "ok"):
            return
        d, n = self.data, self.counter.value
        ok = self.counter.valid
        if ok:
            cost = {k: v * n for k, v in d["cost_per_unit"].items()}
            self.summary.configure(text=f"Сила заявки: {d['strength']} → {d['strength'] + sum(d['gains'][:n])}; "
                                        f"заплатите: {res_long(cost)}", foreground="")
        else:
            self.summary.configure(text=f"Введите число от 0 до {d['max']}", foreground=BAD)
        self.ok.state(["!disabled"] if ok and n else ["disabled"])


class RaiseAction(Panel):
    """Дом вождей орков: яростью поднять уровень текущего действия и уже выполненной добычи."""
    title = "Дом вождей: повысить уровень действий"

    def build(self):
        d = self.data
        key, self.price = next(iter(d["cost_per_level"].items()))
        self.key = key
        self.note(f"Каждый уровень стоит: {res_long({key: self.price})}; хватает на {d['max']} ур. Можно повысить "
                  "текущее действие и добычу, уже выполненную в этом раунде: она сразу принесёт ещё столько ресурса, "
                  "на сколько повышена.", "TLabel")
        grid = ttk.Frame(self.body)
        grid.pack(anchor="w", pady=6)
        for col, title in enumerate(("Действие", "Уровень", "Поднять на", "")):
            ttk.Label(grid, text=title, style="Muted.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 14))
        self.counters, self.after = {}, {}
        for row, (action, level) in enumerate(d["options"].items(), 1):
            current = " (сейчас)" if action == d["current"] else ""
            ttk.Label(grid, text=ACTION_NAMES[action] + current, style="Bold.TLabel").grid(row=row, column=0, sticky="w",
                                                                                          padx=(0, 14))
            ttk.Label(grid, text=str(level)).grid(row=row, column=1, sticky="w")
            self.counters[action] = Counter(grid, d["max"], self.refresh)
            self.counters[action].grid(row=row, column=2, sticky="w", pady=1)
            self.after[action] = ttk.Label(grid, style="Muted.TLabel")
            self.after[action].grid(row=row, column=3, sticky="w")
        self.summary = ttk.Label(self.body, style="Bold.TLabel")
        self.summary.pack(anchor="w")
        self.ok = self.button("Повысить", self.confirm, primary=True)
        self.button("Не повышать", lambda: self.send({}))
        self.refresh()

    def answer(self) -> dict[str, int] | None:
        if not all(c.valid for c in self.counters.values()):
            return None
        return {a: c.value for a, c in self.counters.items() if c.value}

    def refresh(self):
        if not hasattr(self, "ok"):
            return
        answer = self.answer()
        for action, label in self.after.items():
            x = (answer or {}).get(action, 0)
            label.configure(text=f"→ уровень {self.data['options'][action] + x}" if x else "")
        total = sum(answer.values()) if answer is not None else None
        ok = total is not None and 0 < total <= self.data["max"]
        text = ("Введите целые числа" if total is None else
                f"Потратите {RESOURCE_NAMES.get(self.key, self.key)}: {total * self.price} из {self.data['max'] * self.price}")
        self.summary.configure(text=text, foreground=BAD if total is None or total > self.data["max"] else "")
        self.ok.state(["!disabled"] if ok else ["disabled"])

    def confirm(self):
        self.send(self.answer())


class UpgradeUnits(Panel):
    """Модификация войск орков за ярость: пути выполняются по порядку, модифицированных можно модифицировать снова."""
    title = "Модификация войск за ярость"

    def build(self):
        d = self.data
        names = self.static["factions"][self.player["faction"]]["units"]
        self.note("Есть: " + ", ".join(f"{names[u]['name']} ×{n}" for u, n in d["units"].items())
                  + f". Склад: {res_long(d['stock'])}. Модификация — не наём: зданий для найма не нужно.", "TLabel")
        grid = ttk.Frame(self.body)
        grid.pack(anchor="w", pady=6)
        self.counters = {}
        for row, (key, path) in enumerate(d["paths"].items()):
            RichLabel(grid, f"{names[path['from']]['name']} → {names[path['to']]['name']}, "
                            f"{res(path['cost'])} за каждого").grid(row=row, column=0, sticky="w", padx=(0, 10))
            self.counters[key] = Counter(grid, 99, self.refresh)
            self.counters[key].grid(row=row, column=1, sticky="w", pady=1)
        self.summary = ttk.Label(self.body, style="Bold.TLabel")
        self.summary.pack(anchor="w")
        self.ok = self.button("Модифицировать", self.confirm, primary=True)
        self.button("Не модифицировать", lambda: self.send({}))
        self.refresh()

    def check(self) -> tuple[dict | None, str]:
        """Ответ и пояснение; None — так нельзя (не хватает войск или ярости)."""
        if not all(c.value >= 0 for c in self.counters.values()):
            return None, "Введите целые числа"
        units, spent = dict(self.data["units"]), {}
        names = self.static["factions"][self.player["faction"]]["units"]
        for key, path in self.data["paths"].items():
            n = self.counters[key].value
            if n > units.get(path["from"], 0):
                return None, f"Не хватает: {names[path['from']]['name']}"
            units[path["from"]] = units.get(path["from"], 0) - n
            units[path["to"]] = units.get(path["to"], 0) + n
            for k, v in path["cost"].items():
                spent[k] = spent.get(k, 0) + v * n
        if any(v > self.data["stock"].get(k, 0) for k, v in spent.items()):
            return None, f"Не хватает: нужно {res_long(spent)}"
        return {k: c.value for k, c in self.counters.items() if c.value}, f"Потратите: {res_long(spent)}"

    def refresh(self):
        if not hasattr(self, "ok"):
            return
        answer, text = self.check()
        self.summary.configure(text=text, foreground="" if answer is not None else BAD)
        self.ok.state(["!disabled"] if answer else ["disabled"])

    def confirm(self):
        self.send(self.check()[0])


class AvoidBattle(Panel):
    """Шахта наг: откупиться от сражения — по 3 любых ресурса каждому сопернику в регионе."""

    def heading(self):
        return f"Шахта: избежать сражения в регионе {self.data['region']}?"

    def build(self):
        d = self.data
        self.amount, self.have = d["amount"], d["resources"]
        others = ", ".join(self.name(q) for q in d["to"])
        self.note(f"Заплатите {self.amount} {plural(self.amount, 'любой ресурс', 'любых ресурса', 'любых ресурсов')}, "
                  f"и сражения не будет. Ресурсы поровну получат: {others}. Если на объект претендовали несколько "
                  "игроков, в этом раунде он не достанется никому.", "TLabel")
        grid = ttk.Frame(self.body)
        grid.pack(anchor="w", pady=4)
        self.counters = {}
        for col, k in enumerate(RESOURCES):
            ttk.Label(grid, text=f" {RESOURCE_NAMES[k]} (есть {self.have.get(k, 0)})", image=icon(grid, k, 18),
                      compound="left").grid(row=0, column=col, padx=(0, 16), sticky="w")
            self.counters[k] = Counter(grid, min(self.amount, self.have.get(k, 0)), self.refresh)
            self.counters[k].grid(row=1, column=col, padx=(0, 16), sticky="w")
        self.summary = ttk.Label(self.body, style="Bold.TLabel")
        self.summary.pack(anchor="w", pady=(4, 0))
        self.ok = self.button("Заплатить и не сражаться", self.confirm, primary=True)
        self.button("Сражаться", lambda: self.send(None))
        self.refresh()

    def refresh(self):
        if not hasattr(self, "ok"):
            return
        valid = all(c.valid for c in self.counters.values())
        total = sum(c.value for c in self.counters.values()) if valid else None
        ok = total == self.amount
        self.summary.configure(text=f"Выбрано {total if valid else '?'} из {self.amount}", foreground=GOOD if ok else BAD)
        self.ok.state(["!disabled"] if ok else ["disabled"])

    def confirm(self):
        self.send({k: c.value for k, c in self.counters.items() if c.value})


class CircleNote(Panel):
    title = "Ноты гидры"

    def build(self):
        RichLabel(self.body, "Ваши пары нот: " + notes_text(self.data["notes"])).pack(anchor="w")
        self.note("Закрыв начатую пару, вы сразу призываете гидру в любой озёрный регион (прежняя гидра, если она "
                  "есть, погибает). Начав новую пару, вы откладываете призыв.")
        labels = {"open": "Начать новую пару", "close": "Закрыть пару и призвать гидру"}
        for option in reversed(self.data["options"]):
            self.button(labels.get(option, option), lambda o=option: self.send(o), primary=option == "close")


class HydraDestination(RegionChoice):
    secret = True

    def heading(self):
        return f"Гидра: перемещение из региона {self.data['from']}"

    def build(self):
        opts = self.data["options"]
        here = self.data["from"]
        options = sorted(opts, key=lambda r: (opts[r], r))
        steps = lambda n: "остаться" if not n else f"{n} шаг" if n == 1 else f"{n} шага"
        labels = {r: " · ".join([r, steps(opts[r]), TERRAIN_NAMES[terrain_of(self.static, self.state, r)],
                                 self.object(r)]) for r in options}
        self.note("Гидра идёт отдельно от вас — тайно и без очков перемещения. Стоя отдельно, она обязана сразиться "
                  "с игроками в своём регионе (наград тогда нет ни у кого), а без них может штурмовать крепость "
                  "и сразиться с чудовищем вашей линии — только ради ПО.")
        self.ok = self.button("Оставить на месте", self.confirm, primary=True)
        self.region_list(options, labels, {r: steps(opts[r]) for r in options}, selected=here)

    def picked(self):
        self.ok.configure(text="Оставить на месте" if self.choice == self.data["from"] else f"Идти: {self.choice}")


class SummonHydra(RegionChoice):
    title = "Призыв гидры"

    def build(self):
        options = self.data["options"]
        here = {r: [name_of(self.static, q) for q, pl in enumerate(self.state["players"]) if pl["region"] == r]
                for r in options}
        labels = {r: f"{r}: {self.object(r)}" + (f" · там: {', '.join(here[r])}" if here[r] else "")
                  for r in options}
        self.note("Выберите озёрный регион. Если гидра уже на карте, она погибает и призывается новая.")
        self.ok = self.button("Призвать", self.confirm, primary=True)
        self.ok.state(["disabled"])
        self.region_list(options, labels, {r: "гидра" for r in options})

    def picked(self):
        self.ok.state(["!disabled"])
        self.ok.configure(text=f"Призвать: {self.choice}")


class CurseRegion(RegionChoice):
    title = "Проклятие региона"

    def build(self):
        d = self.data
        options = d["options"]
        here = {r: [name_of(self.static, q) for q, pl in enumerate(self.state["players"])
                    if pl["region"] == r and q != self.me] for r in options}
        if d["optional"]:
            self.note(f"Чумной столб: можно заплатить {res_long(d['cost'])}, чтобы проклясть свой текущий или соседний "
                      "регион. Можно отказаться.", "TLabel")
        else:
            self.note("Прокляните любой регион (эффект артефакта).", "TLabel")
        self.note("Соперник, посетивший проклятый регион или стоящий в нём сейчас, теряет самую слабую единицу; "
                  "каждый проклятый вами регион — +1 ПО в конце игры.")
        labels = {r: f"{r}: {self.object(r)}" + (f" · там: {', '.join(here[r])}" if here[r] else "")
                  for r in options}
        self.ok = self.button("Проклясть", self.confirm, primary=True)
        self.ok.state(["disabled"])
        if d["optional"]:
            self.button("Не проклинать", lambda: self.send(None))
        self.region_list(options, labels, {r: ", ".join(here[r]) or "проклясть" for r in options})

    def picked(self):
        self.ok.state(["!disabled"])
        self.ok.configure(text=f"Проклясть: {self.choice}")


class RawJson(Panel):
    """Решение, для которого нет своей формы: текст движка и ответ в JSON."""

    def heading(self):
        return self.message["kind"]

    def build(self):
        self.note(self.message["text"], "TLabel")
        self.entry = ttk.Entry(self.body, width=60)
        self.entry.pack(anchor="w")
        self.button("Отправить", self.confirm, primary=True)

    def confirm(self):
        try:
            self.send(json.loads(self.entry.get()))
        except json.JSONDecodeError as e:
            messagebox.showerror("Это не JSON", str(e), parent=self)


PANELS: dict[str, type[Panel]] = {
    "choose_start": ChooseStart,
    "action_order": ActionOrder,
    "distribute_resources": DistributeResources,
    "build_recruit": BuildRecruit,
    "reserve_object": ReserveObject,
    "destination": Destination,
    "region_stance": RegionStance,
    "commit_army": CommitArmy,
    "choose_reward": ChooseReward,
    "capture_fortress": CaptureFortress,
    "fight_monster": FightMonster,
    "take_artifact": TakeArtifact,
    # особые решения фракций
    "make_lake": YesNo,
    "buy_science": YesNo,
    "exchange_on_gain": YesNo,
    "buy_move_points": BuyMovePoints,
    "battle_upgrade": BattleUpgrade,
    "raise_action": RaiseAction,
    "upgrade_units": UpgradeUnits,
    "avoid_battle": AvoidBattle,
    "circle_note": CircleNote,
    "hydra_destination": HydraDestination,
    "summon_hydra": SummonHydra,
    "curse_region": CurseRegion,
}


def is_secret(message: dict) -> bool:
    """Тайное ли решение: после него при игре нескольких людей за окном экран скрывается.
    Модификация Лавки бронника тайная только в сражении между игроками."""
    if message["kind"] == "battle_upgrade":
        return bool(message["data"].get("secret"))
    return bool(getattr(PANELS.get(message["kind"]), "secret", False))


def make_panel(master, app, message: dict) -> Panel:
    return PANELS.get(message["kind"], RawJson)(master, app, message)
