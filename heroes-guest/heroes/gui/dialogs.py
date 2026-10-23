"""Диалоги перед партией: новая партия, подключение к чужой, параметры сети при загрузке."""
from __future__ import annotations

import random
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from ..cli import MODELS, PLAYER_TYPES, RANDOM, faction_names, resolve_factions
from ..net.protocol import DEFAULT_PORT
from .. import profiles as store
from ..profiles import Profile

SEAT_TYPES = {"human": "Человек (за этим окном)", "remote": "Человек по сети", "claude": "Claude",
              "bot": "Случайный бот"}
MODEL_NOTES = {"claude-opus-5-5": "Opus 5.5 — сильнейшая, ≈$3–4 за партию на игрока",
               "claude-sonnet-5-5": "Sonnet 5.5 — быстрее и дешевле",
               "claude-haiku-5-5": "Haiku 5.5 — очень дёшево, слабее"}
EFFORTS = {"default": "по умолчанию", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
DEFAULT_NAMES = {"human": "Игрок", "claude": "Claude", "bot": "Бот", "remote": "Гость"}
RANDOM_FACTION = "случайная"


def default_names(kinds: list[str]) -> list[str]:
    counters = dict.fromkeys(PLAYER_TYPES, 0)
    names = []
    for kind in kinds:
        counters[kind] += 1
        names.append(f"{DEFAULT_NAMES[kind]} {counters[kind]}")
    return names


class Dialog(tk.Toplevel):
    """Модальный диалог: result — None, если отменён."""

    def __init__(self, master, title: str):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.frame = ttk.Frame(self, padding=16)
        self.frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda e: self.destroy())

    def run(self):
        self.wait_visibility()
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result

    def buttons(self, ok_text: str, row: int) -> None:
        bar = ttk.Frame(self.frame)
        bar.grid(row=row, column=0, columnspan=4, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text=ok_text, style="Primary.TButton", command=self.ok).pack(side="right")
        self.bind("<Return>", lambda e: self.ok())

    def ok(self) -> None:
        raise NotImplementedError


class NewGameDialog(Dialog):
    """profiles — профили этого компьютера: людям за окном имя выбирается из них или вписывается новое —
    тогда профиль с этим именем создаётся при старте; active садится на первое место «Человек».
    Партия попадает в статистику профилей всех людей за окном.
    Фракцию каждого места (и сетевого гостя) выбирает хозяин; одна фракция — у одного игрока,
    «случайная» берётся из тех, что никто не выбрал."""

    def __init__(self, master, model: str | None = None, profiles: list[Profile] = (), active: Profile | None = None):
        super().__init__(master, "Новая партия")
        self.profiles = list(profiles)
        self.active = active
        f = self.frame
        ttk.Label(f, text="«Герои» — новая партия", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="Игроки сидят по часовой стрелке в этом порядке.", style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="Игроков:").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.count = tk.IntVar(value=3)
        counts = ttk.Frame(f)
        counts.grid(row=2, column=1, columnspan=3, sticky="w", pady=(10, 4))
        for n in (2, 3, 4):
            ttk.Radiobutton(counts, text=str(n), value=n, variable=self.count, command=self.update_seats).pack(side="left", padx=(0, 10))

        self.seat_frame = ttk.Frame(f)
        self.seat_frame.grid(row=3, column=0, columnspan=4, sticky="we")
        self.kinds = [tk.StringVar(value=SEAT_TYPES["human" if i == 0 else "bot"]) for i in range(4)]
        self.names = [tk.StringVar() for _ in range(4)]
        for var in self.names:
            var.trace_add("write", lambda *_: self.update_first())
        self.auto_names = [""] * 4
        self.faction_labels = faction_names() | {RANDOM: RANDOM_FACTION}
        self.factions = [tk.StringVar(value=RANDOM_FACTION) for _ in range(4)]
        self.rows = []
        for i in range(4):
            label = ttk.Label(self.seat_frame, text=f"{i + 1}.")
            kind = ttk.Combobox(self.seat_frame, textvariable=self.kinds[i], values=list(SEAT_TYPES.values()),
                                state="readonly", width=24)
            kind.bind("<<ComboboxSelected>>", lambda e: self.update_seats())
            name = ttk.Entry(self.seat_frame, textvariable=self.names[i], width=18)
            pick = ttk.Combobox(self.seat_frame, textvariable=self.names[i], width=16,
                                values=[p.name for p in self.profiles])
            faction = ttk.Combobox(self.seat_frame, textvariable=self.factions[i], state="readonly", width=12,
                                   values=list(self.faction_labels.values()))
            self.rows.append((label, kind, name, pick, faction))
        ttk.Label(self.seat_frame, text="Человеку за этим окном выберите профиль или впишите новое имя — профиль создастся сам.\n"
                  "Каждой фракцией играет только один игрок; «случайная» — из тех, что никто не выбрал.",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Separator(f).grid(row=4, column=0, columnspan=4, sticky="we", pady=10)
        ttk.Label(f, text="Первый игрок:").grid(row=5, column=0, sticky="w")
        self.first = ttk.Combobox(f, state="readonly", width=24)
        self.first.grid(row=5, column=1, columnspan=2, sticky="w")

        self.claude_label = ttk.Label(f, text="Модель Claude:")
        self.claude_label.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.model = ttk.Combobox(f, state="readonly", width=48, values=[f"{m} — {MODEL_NOTES.get(m, '')}" for m in MODELS])
        models = list(MODELS)
        self.model.current(models.index(model) if model in models else 0)
        self.model.grid(row=6, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Усилие (effort):").grid(row=7, column=0, sticky="w")
        self.effort = ttk.Combobox(f, state="readonly", width=14, values=list(EFFORTS.values()))
        self.effort.current(0)
        self.effort.grid(row=7, column=1, sticky="w")
        self.thoughts = tk.BooleanVar(value=False)
        self.thoughts_box = ttk.Checkbutton(f, text="Показывать замыслы Claude в журнале (подсказка, если играете против него)",
                                            variable=self.thoughts)
        self.thoughts_box.grid(row=8, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Label(f, text="Сетевой порт:").grid(row=9, column=0, sticky="w", pady=(6, 0))
        self.port = tk.StringVar(value=str(DEFAULT_PORT))
        self.port_entry = ttk.Entry(f, textvariable=self.port, width=8)
        self.port_entry.grid(row=9, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Пароль:").grid(row=10, column=0, sticky="w")
        self.password = tk.StringVar()
        self.password_entry = ttk.Entry(f, textvariable=self.password, width=18)
        self.password_entry.grid(row=10, column=1, sticky="w")
        self.buttons("Начать", 11)
        self.update_seats()

    def kind(self, i: int) -> str:
        label = self.kinds[i].get()
        return next(k for k, v in SEAT_TYPES.items() if v == label)

    def update_seats(self) -> None:
        n = self.count.get()
        kinds = [self.kind(i) for i in range(n)]
        defaults = default_names(kinds)
        if self.active and "human" in kinds:   # активный профиль — на первое место «Человек»
            defaults[kinds.index("human")] = self.active.name
        for i, (label, kind, name, pick, faction) in enumerate(self.rows):
            if i < n:
                label.grid(row=i, column=0, sticky="w", padx=(0, 6), pady=2)
                kind.grid(row=i, column=1, sticky="w", padx=(0, 6), pady=2)
                use, hide = (pick, name) if kinds[i] == "human" else (name, pick)
                hide.grid_remove()
                use.grid(row=i, column=2, sticky="w", pady=2)
                faction.grid(row=i, column=3, sticky="w", padx=(6, 0), pady=2)
                if self.names[i].get() in ("", self.auto_names[i]):  # имя не меняли руками — подставить новое
                    self.names[i].set(defaults[i])
                self.auto_names[i] = defaults[i]
            else:
                for w in (label, kind, name, pick, faction):
                    w.grid_remove()
        self.update_first()
        claude = "claude" in kinds
        for w in (self.model, self.effort):
            w.state(["!disabled" if claude else "disabled"])
        self.thoughts_box.state(["!disabled" if claude else "disabled"])
        if claude and not {"human", "remote"} & set(kinds):
            self.thoughts.set(True)
        remote = "remote" in kinds
        for w in (self.port_entry, self.password_entry):
            w.state(["!disabled" if remote else "disabled"])

    def update_first(self) -> None:
        if not hasattr(self, "first"):
            return
        n = self.count.get()
        firsts = [self.names[i].get() or f"Игрок {i + 1}" for i in range(n)] + ["случайно"]
        index = self.first.current()
        self.first.configure(values=firsts)
        if 0 <= index < n:
            self.first.current(index)
        else:
            self.first.set("случайно")

    def ok(self) -> None:
        n = self.count.get()
        kinds = [self.kind(i) for i in range(n)]
        names = [self.names[i].get().strip() for i in range(n)]
        if not all(names):
            return self.complain("У каждого игрока должно быть имя.")
        if len(set(names)) != len(names):
            return self.complain("Имена игроков не должны повторяться.")
        if "claude" in kinds and shutil.which("claude") is None:
            return self.complain("Для игроков-Claude нужен установленный Claude Code CLI («claude») с выполненным входом.")
        # люди за окном: имя — профиль (без учёта регистра, имя берётся из профиля) или новый профиль
        known = {p.name.casefold(): p for p in self.profiles}
        for i, kind in enumerate(kinds):
            if kind == "human" and names[i].casefold() in known:
                names[i] = known[names[i].casefold()].name
            elif kind == "human" and (error := store.check_name(names[i])):
                return self.complain(error)
        if len(set(names)) != len(names):
            return self.complain("Имена игроков не должны повторяться.")
        by_label = {label: f for f, label in self.faction_labels.items()}
        try:
            factions = resolve_factions([by_label[self.factions[i].get()] for i in range(n)])
        except ValueError:
            return self.complain("Каждой фракцией может играть только один игрок.")
        port = None
        if "remote" in kinds:
            if not self.port.get().isdigit():
                return self.complain("Порт должен быть числом.")
            port = int(self.port.get())
        first = self.first.current()
        if first < 0 or first >= n:
            first = random.randrange(n)
        effort = list(EFFORTS)[self.effort.current()]
        seats = {}
        for i, kind in enumerate(kinds):
            if kind == "human":
                profile = known.get(names[i].casefold())
                try:
                    seats[i] = (profile or store.create(names[i])).id
                except (OSError, ValueError) as e:
                    return self.complain(f"Не удалось создать профиль «{names[i]}»: {e}")
        self.result = {
            "kinds": kinds, "names": names, "factions": factions, "first": first,
            "profiles": seats,
            "model": list(MODELS)[self.model.current()],
            "effort": None if effort == "default" else effort,
            "show_thoughts": self.thoughts.get(),
            "port": port, "password": self.password.get() or None,
        }
        self.destroy()

    def complain(self, text: str) -> None:
        messagebox.showerror("Новая партия", text, parent=self)


class JoinDialog(Dialog):
    """Подключение к партии на другом компьютере."""

    def __init__(self, master, address: str = "", name: str = "", password: str = ""):
        super().__init__(master, "Подключиться к сетевой партии")
        f = self.frame
        ttk.Label(f, text="Партия на другом компьютере", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.vars = {}
        for row, (key, label, value, hint) in enumerate((
                ("address", "Адрес хозяина:", address, f"например, 192.168.1.5 или 192.168.1.5:{DEFAULT_PORT}"),
                ("name", "Ваше имя:", name, "как его задал хозяин (пусто — выбрать из свободных мест)"),
                ("password", "Пароль:", password, "если хозяин его задал")), 1):
            ttk.Label(f, text=label).grid(row=row * 2 - 1, column=0, sticky="w")
            self.vars[key] = tk.StringVar(value=value)
            entry = ttk.Entry(f, textvariable=self.vars[key], width=30)
            entry.grid(row=row * 2 - 1, column=1, sticky="w")
            if row == 1:
                entry.focus_set()
            ttk.Label(f, text=hint, style="Muted.TLabel").grid(row=row * 2, column=1, sticky="w", pady=(0, 4))
        self.buttons("Подключиться", 10)

    def ok(self) -> None:
        values = {k: v.get().strip() for k, v in self.vars.items()}
        if not values["address"]:
            messagebox.showerror("Подключение", "Введите адрес хозяина.", parent=self)
            return
        self.result = {"address": values["address"], "name": values["name"] or None,
                       "password": values["password"] or None}
        self.destroy()


class NetworkDialog(Dialog):
    """Параметры сервера для загруженной партии с сетевыми игроками."""

    def __init__(self, master, seats: list[str]):
        super().__init__(master, "Сетевые игроки")
        f = self.frame
        ttk.Label(f, text=f"В этой партии есть сетевые игроки: {', '.join(seats)}.", wraplength=360).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(f, text="Порт:").grid(row=1, column=0, sticky="w")
        self.port = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(f, textvariable=self.port, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(f, text="Пароль:").grid(row=2, column=0, sticky="w")
        self.password = tk.StringVar()
        ttk.Entry(f, textvariable=self.password, width=18).grid(row=2, column=1, sticky="w")
        self.buttons("Открыть партию", 3)

    def ok(self) -> None:
        if not self.port.get().isdigit():
            messagebox.showerror("Сеть", "Порт должен быть числом.", parent=self)
            return
        self.result = {"port": int(self.port.get()), "password": self.password.get() or None}
        self.destroy()


class ChooseDialog(Dialog):
    """Выбор одного варианта из списка (например, своего места за столом)."""

    def __init__(self, master, title: str, prompt: str, options: list[str]):
        super().__init__(master, title)
        ttk.Label(self.frame, text=prompt).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.options = options
        self.listbox = tk.Listbox(self.frame, height=min(8, len(options)), exportselection=False)
        for o in options:
            self.listbox.insert("end", o)
        self.listbox.selection_set(0)
        self.listbox.grid(row=1, column=0, sticky="we")
        self.listbox.bind("<Double-1>", lambda e: self.ok())
        self.buttons("OK", 2)

    def ok(self) -> None:
        sel = self.listbox.curselection()
        if sel:
            self.result = self.options[sel[0]]
            self.destroy()
