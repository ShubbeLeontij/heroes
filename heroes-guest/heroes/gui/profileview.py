"""Экран «Профили игроков» (из главного меню): список профилей этого компьютера, имя и аватарка
выбранного, его статистика — по карточке на каждую доигранную партию.

Активный профиль садится на первое место «Человек» в новой партии и подключается под своим именем
к сетевым; партии записываются в профиль в конце игры (heroes/profiles.py, App.finish).
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .. import profiles
from ..profiles import Profile
from . import avatars
from .common import BAD, GOOD, PANEL_BG, color_of, font

MAX_CARDS = 100   # больше карточек партий не рисуем: окно начинает тормозить


def place_text(game: dict) -> tuple[str, str]:
    """«Победа» или «2-е место из 3» и цвет надписи."""
    players = game["players"]
    me = players[game["you"]]
    if me["winner"]:
        return "Победа", GOOD
    # выше меня — у кого больше ПО, и победители при равных ПО (у них больше ресурсов на складе)
    place = 1 + sum(1 for o in players if o["vp"] > me["vp"] or (o["vp"] == me["vp"] and o["winner"]))
    return f"{place}-е место из {len(players)}", BAD


def date_text(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return iso


class ScrollFrame(ttk.Frame):
    """Прокручиваемая по вертикали область: виджеты кладутся в .inner."""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, background=PANEL_BG, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        # колесо мыши — пока указатель над областью (Linux: Button-4/5, Windows/macOS: MouseWheel)
        self.bind("<Enter>", lambda e: self._wheel(True))
        self.bind("<Leave>", lambda e: self._wheel(False))

    def _wheel(self, on: bool) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            if on:
                self.bind_all(seq, self._scroll)
            else:
                self.unbind_all(seq)

    def _scroll(self, event) -> None:
        step = -1 if event.num == 4 or getattr(event, "delta", 0) > 0 else 1
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(step * 3, "units")

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


class ProfileView(ttk.Frame):
    def __init__(self, master, on_back: Callable[[], None], folder=profiles.PROFILE_DIR):
        super().__init__(master)
        self.folder = folder
        self.selected: Profile | None = None
        bar = ttk.Frame(self, style="Bar.TFrame", padding=(10, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="← На стартовый экран", command=on_back).pack(side="left")
        ttk.Label(bar, text="Профили игроков", style="Round.TLabel").pack(side="left", padx=16)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse", style="Profiles.Treeview", height=12)
        self.tree.column("#0", width=240)
        self.tree.tag_configure("active", font=font(10, "bold"))
        self.tree.pack(fill="y", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.select(self.tree.focus()))
        ttk.Button(left, text="Новый профиль", command=self.new_profile).pack(fill="x", pady=(8, 0))
        ttk.Label(left, text="★ — активный профиль: с ним вы\nначинаете партии и подключаетесь\nк сетевым.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        ttk.Separator(body, orient="vertical").pack(side="left", fill="y", padx=12)
        self.detail = ttk.Frame(body)
        self.detail.pack(side="left", fill="both", expand=True)
        self.refresh()

    # --- список ---------------------------------------------------------------------------------

    def refresh(self, select: str | None = None) -> None:
        """Перечитать профили и показать select (по умолчанию — прежний выбранный или активный)."""
        items = profiles.all_profiles(self.folder)
        active = profiles.get_active(self.folder)
        self.tree.delete(*self.tree.get_children())
        for p in items:
            is_active = active is not None and p.id == active.id
            self.tree.insert("", "end", iid=p.id, text=f"  {p.name}" + ("  ★" if is_active else ""),
                             image=avatars.for_profile(self, p, 32), tags=("active",) if is_active else ())
        wanted = select or (self.selected.id if self.selected else None) or (active.id if active else None)
        ids = [p.id for p in items]
        if ids:
            wanted = wanted if wanted in ids else ids[0]
            self.tree.selection_set(wanted)
            self.tree.focus(wanted)
            self.select(wanted)
        else:
            self.selected = None
            self.show_empty()

    def select(self, profile_id: str) -> None:
        profile = profiles.get(profile_id, self.folder)
        if profile is None:
            return
        self.selected = profile
        self.show_profile(profile)

    def new_profile(self) -> None:
        profile = profiles.create(profiles.unique_name(folder=self.folder), self.folder)
        self.refresh(select=profile.id)
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")

    # --- выбранный профиль ----------------------------------------------------------------------

    def clear_detail(self) -> None:
        for w in self.detail.winfo_children():
            w.destroy()

    def show_empty(self) -> None:
        self.clear_detail()
        ttk.Label(self.detail, text="Профилей пока нет", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.detail, text="В профиле — имя, аватарка и статистика ваших партий на этом компьютере.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))
        ttk.Button(self.detail, text="Создать профиль", style="Primary.TButton", command=self.new_profile).pack(anchor="w")

    def show_profile(self, profile: Profile) -> None:
        self.clear_detail()
        active = profiles.get_active(self.folder)
        is_active = active is not None and active.id == profile.id

        head = ttk.Frame(self.detail)
        head.pack(fill="x")
        ttk.Label(head, image=avatars.for_profile(self, profile, 96)).grid(row=0, column=0, rowspan=4, sticky="n", padx=(0, 16))

        ttk.Label(head, text="Имя:").grid(row=0, column=1, sticky="w")
        self.name_var = tk.StringVar(value=profile.name)
        self.name_entry = ttk.Entry(head, textvariable=self.name_var, width=26)
        self.name_entry.grid(row=0, column=2, sticky="w", padx=6)
        self.name_entry.bind("<Return>", lambda e: self.rename())
        ttk.Button(head, text="Сохранить имя", command=self.rename).grid(row=0, column=3, sticky="w")

        pics = ttk.Frame(head)
        pics.grid(row=1, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(pics, text="Выбрать картинку…", command=self.choose_avatar).pack(side="left")
        if profile.avatar:
            ttk.Button(pics, text="Убрать картинку", command=self.remove_avatar).pack(side="left", padx=6)

        acts = ttk.Frame(head)
        acts.grid(row=2, column=1, columnspan=3, sticky="w", pady=(8, 0))
        if is_active:
            ttk.Label(acts, text="★ Активный профиль", style="Bold.TLabel").pack(side="left")
        else:
            ttk.Button(acts, text="Сделать активным", style="Primary.TButton", command=self.make_active).pack(side="left")
        ttk.Button(acts, text="Удалить профиль", command=self.delete).pack(side="left", padx=12)

        ttk.Separator(self.detail).pack(fill="x", pady=12)
        s = profiles.summary(profile)
        ttk.Label(self.detail, text="Статистика", style="Title.TLabel").pack(anchor="w")
        if s["games"]:
            line = (f"Партий: {s['games']}   ·   Побед: {s['wins']}   ·   "
                    f"Средние ПО: {s['avg_vp']:.1f}   ·   Лучший результат: {s['best_vp']} ПО")
        else:
            line = "Доигранных партий пока нет. Партия попадает сюда после подсчёта очков в конце 16-го раунда."
        ttk.Label(self.detail, text=line, style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        if not s["games"]:
            return
        cards = ScrollFrame(self.detail)
        cards.pack(fill="both", expand=True)
        known = {p.id: p for p in profiles.all_profiles(self.folder)}
        games = list(reversed(profile.games))
        for game in games[:MAX_CARDS]:
            self.game_card(cards.inner, game, known).pack(fill="x", pady=(0, 8), padx=(0, 6))
        if len(games) > MAX_CARDS:
            ttk.Label(cards.inner, text=f"Показаны последние {MAX_CARDS} партий из {len(games)}.",
                      style="Muted.TLabel").pack(anchor="w")

    def game_card(self, master, game: dict, known: dict[str, Profile]) -> ttk.Frame:
        """Карточка партии: дата, итог; строки — вы и соперники: аватарка, имя, фракция, номер хода, ПО."""
        card = ttk.Frame(master, style="Card.TFrame", padding=(10, 6))
        players = game["players"]
        place, color = place_text(game)
        ttk.Label(card, text=f"{date_text(game['date'])}   ·   игроков: {len(players)}",
                  style="CardMuted.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(card, text=place, style="CardBold.TLabel", foreground=color).grid(row=0, column=6, sticky="e")
        for col, title in enumerate(("", "Игрок", "Фракция", "Номер хода", "ПО", ""), 0):
            ttk.Label(card, text=title, style="CardMuted.TLabel").grid(row=1, column=col, sticky="w", padx=(0, 14))
        order = [game["you"]] + sorted((p for p in range(len(players)) if p != game["you"]), key=lambda p: players[p]["turn"])
        for row, p in enumerate(order, 2):
            pl = players[p]
            you = p == game["you"]
            style = "CardBold.TLabel" if you else "Card.TLabel"
            ttk.Label(card, image=avatars.for_player(self, pl, p, 32, known), style="Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=1)
            ttk.Label(card, text=pl["name"] + (" (вы)" if you else ""), style=style,
                      foreground=color_of(p)).grid(row=row, column=1, sticky="w", padx=(0, 14))
            ttk.Label(card, text=pl["faction_name"], style=style).grid(row=row, column=2, sticky="w", padx=(0, 14))
            ttk.Label(card, text=str(pl["turn"]), style=style).grid(row=row, column=3, sticky="w", padx=(0, 14))
            ttk.Label(card, text=str(pl["vp"]), style=style).grid(row=row, column=4, sticky="w", padx=(0, 14))
            ttk.Label(card, text="★ победа" if pl["winner"] else "", style="CardBold.TLabel",
                      foreground=GOOD).grid(row=row, column=5, sticky="w")
        card.columnconfigure(6, weight=1)   # таблица — плотно слева, итог партии — у правого края
        return card

    # --- действия -------------------------------------------------------------------------------

    def rename(self) -> None:
        profile = self.selected
        if profile is None or self.name_var.get().strip() == profile.name:
            return
        try:
            profiles.rename(profile, self.name_var.get())
        except ValueError as e:
            messagebox.showerror("Профиль", str(e), parent=self)
            return
        self.refresh(select=profile.id)

    def choose_avatar(self) -> None:
        profile = self.selected
        path = filedialog.askopenfilename(parent=self, title="Аватарка профиля",
                                          filetypes=[("Картинки", "*.png *.gif *.ppm *.pgm"), ("Все файлы", "*")])
        if not path or profile is None:
            return
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            avatars.import_avatar(self, path, profile, PANEL_BG)
        except (ValueError, OSError) as e:
            messagebox.showerror("Аватарка", str(e), parent=self)
        finally:
            self.configure(cursor="")
        self.refresh(select=profile.id)

    def remove_avatar(self) -> None:
        if self.selected:
            avatars.remove_avatar(self.selected)
            self.refresh(select=self.selected.id)

    def make_active(self) -> None:
        if self.selected:
            profiles.set_active(self.selected.id, self.folder)
            self.refresh(select=self.selected.id)

    def delete(self) -> None:
        profile = self.selected
        if profile is None:
            return
        games = f" и статистику ({len(profile.games)} партий)" if profile.games else ""
        if not messagebox.askyesno("Профиль", f"Удалить профиль «{profile.name}»{games}? Это нельзя отменить.",
                                   parent=self):
            return
        profiles.delete(profile)
        self.selected = None
        self.refresh()
