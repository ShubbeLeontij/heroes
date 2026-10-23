"""Главное окно: стартовый экран и экран партии.

  python -m heroes --gui                    # стартовый экран: новая партия, загрузка, подключение
  python -m heroes --gui --join 192.168.1.5 --name Боря   # сразу подключиться к чужой партии
  python -m heroes --gui --load saves/<файл>.json          # сразу продолжить сохранённую

Экран партии: карта и панель текущего решения слева, справа — вкладки с планшетом каждого игрока
и сводкой «Все игроки»; журнал событий — отдельное окно (кнопка «События», Ctrl+E).
Окно получает сообщения из Link (links.py) и опрашивает их таймером в главном потоке Tk.
Всё на экране — по-русски; английские тексты движка переводит ru.py.
"""
from __future__ import annotations

import argparse
import json
import queue
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import cli, profiles, savegame
from ..engine.game import Game
from .boardview import BoardView, PlayersTable
from . import avatars, common
from .common import (ACCENT, ACTION_NAMES, BAD, BG, GOOD, INK, MUTED, PANEL_BG, SCORE_NAMES, color_of, final_table,
                     font, name_of, name_spans, next_first_players, thinking_text, turn_order, winners,
                     with_factions)
from .icons import RichLabel
from .dialogs import ChooseDialog, JoinDialog, NetworkDialog, NewGameDialog
from .links import Link, LocalLink, RemoteLink
from .mapview import MapView
from .panels import is_secret, make_panel
from .profileview import ProfileView
from .ru import Translator

POLL_MS = 60
AVATAR = 20   # аватарка перед именем игрока в строке (журнал, полоса вверху, список планшетов)
DECISION_HEIGHT = 300  # высота панели решения: постоянная, чтобы карта не прыгала от хода к ходу
MAX_MESSAGES = 300  # за один опрос: чтобы окно не замирало, когда события сыплются потоком (боты)


def setup_style(root: tk.Tk) -> None:
    if common.FAMILY not in tkfont.families(root):  # нет DejaVu — шрифт системы по умолчанию
        common.FAMILY = tkfont.nametofont("TkDefaultFont").actual("family")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family=common.FAMILY, size=10)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=PANEL_BG, foreground=INK)
    style.configure("TFrame", background=PANEL_BG)
    style.configure("TLabel", background=PANEL_BG)
    style.configure("TRadiobutton", background=PANEL_BG)
    style.configure("TCheckbutton", background=PANEL_BG)
    style.configure("Title.TLabel", font=font(12, "bold"))
    style.configure("Big.TLabel", font=font(28, "bold"), foreground="#7a2e0e")
    style.configure("Bold.TLabel", font=font(10, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Hint.TLabel", foreground="#7b3fb5")
    style.configure("Secret.TLabel", foreground="#7b3fb5", font=font(9, "bold"))
    style.configure("Status.TLabel", background="#efe6d2")
    style.configure("Bar.TFrame", background="#efe6d2")
    style.configure("Round.TLabel", background="#efe6d2", font=font(11, "bold"))
    style.configure("Chip.TLabel", background="#fff8e6", relief="solid", borderwidth=1, padding=(6, 1))
    style.configure("Done.TLabel", background="#efe6d2", foreground=MUTED, relief="solid", borderwidth=1, padding=(6, 1))
    style.configure("Current.TLabel", background=ACCENT, foreground="white", relief="solid", borderwidth=1,
                    padding=(6, 1), font=font(10, "bold"))
    style.configure("Slot.TLabel", background="#ffe8d6", relief="solid", borderwidth=1, padding=(6, 3), font=font(10, "bold"))
    style.configure("Drop.TLabel", background="#ffc9a3", relief="solid", borderwidth=2, padding=(6, 3), font=font(10, "bold"))
    style.configure("EmptySlot.TLabel", background="white", relief="solid", borderwidth=1, padding=(6, 3), foreground=MUTED)
    style.configure("Primary.TButton", font=font(10, "bold"), foreground="white", background=ACCENT)
    style.map("Primary.TButton", background=[("disabled", "#e5c9b8"), ("active", "#9a3412")],
              foreground=[("disabled", "#fbf8f0")])
    style.configure("Small.TButton", padding=(4, 0), font=font(8))
    style.configure("Start.TButton", font=font(12), padding=(20, 8))
    style.configure("TNotebook", background=PANEL_BG)
    style.configure("Treeview", rowheight=22)
    style.configure("Profiles.Treeview", rowheight=40)
    style.configure("Card.TFrame", background="white", relief="solid", borderwidth=1)
    # строка очерёдности под полосой раунда: игроки фишками, кто решает сейчас — подсвечен
    style.configure("Order.TFrame", background="#f7f0e0")
    style.configure("Order.TLabel", background="#f7f0e0", foreground=MUTED)
    style.configure("OrderChip.TFrame", background="#fff8e6", relief="solid", borderwidth=1)
    style.configure("OrderChip.TLabel", background="#fff8e6")
    style.configure("OrderNow.TFrame", background=ACCENT, relief="solid", borderwidth=1)
    style.configure("OrderNow.TLabel", background=ACCENT, foreground="white", font=font(10, "bold"))
    style.configure("Card.TLabel", background="white")
    style.configure("CardBold.TLabel", background="white", font=font(10, "bold"))
    style.configure("CardMuted.TLabel", background="white", foreground=MUTED, font=font(9))
    root.configure(background=BG)


class App:
    def __init__(self, root: tk.Tk, args: argparse.Namespace | None = None):
        self.root = root
        self.args = args
        self.link: Link | None = None
        self.static: dict | None = None
        self.state: dict | None = None
        self.viewer: int | None = None       # чьими глазами смотрим (отметки «посещено», свой планшет)
        self.board_player: int | None = None
        self.pending: dict | None = None     # решение, которое ждёт ответа
        self.handover: dict | None = None    # решение, ждущее передачи экрана (несколько людей за окном)
        self.ended = False
        self.dirty = False
        self.seat_profiles: dict[int, str] = {}   # места за этим окном → профили (в них пишется партия)
        self.join_profile: str | None = None       # гость: профиль, под которым подключились
        self.tr = Translator()
        self.screen: ttk.Frame | None = None
        root.title("«Герои»")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(1600, int(sw * 0.94))}x{min(1000, int(sh * 0.9))}")
        root.minsize(1100, 700)
        self.build_menu()
        self.start_screen()
        root.after(POLL_MS, self.poll)

    # --- меню и экраны -----------------------------------------------------------------------

    def build_menu(self) -> None:
        bar = tk.Menu(self.root)
        game = tk.Menu(bar, tearoff=False)
        game.add_command(label="Новая партия…", command=self.new_game)
        game.add_command(label="Загрузить партию…", command=self.load_game)
        game.add_command(label="Подключиться к сетевой партии…", command=self.join_game)
        game.add_command(label="Профили игроков…", command=self.profiles_screen)
        game.add_separator()
        game.add_command(label="Сохранить", command=self.save, accelerator="Ctrl+S")
        game.add_command(label="Сохранить как…", command=self.save_as)
        game.add_command(label="События партии", command=self.toggle_log, accelerator="Ctrl+E")
        game.add_separator()
        game.add_command(label="Выход", command=self.quit, accelerator="Ctrl+Q")
        bar.add_cascade(label="Игра", menu=game)
        self.root.configure(menu=bar)
        self.root.bind("<Control-s>", lambda e: self.save())
        self.root.bind("<Control-q>", lambda e: self.quit())
        self.root.bind("<Control-e>", lambda e: self.toggle_log())

    def set_screen(self, frame: ttk.Frame) -> None:
        if self.screen is not None:
            self.screen.destroy()
        self.screen = frame
        frame.pack(fill="both", expand=True)

    def start_screen(self) -> None:
        self.root.title("«Герои»")
        frame = ttk.Frame(self.root)
        inner = ttk.Frame(frame)
        inner.place(relx=0.5, rely=0.45, anchor="center")
        ttk.Label(inner, text="«Герои»", style="Big.TLabel").pack(pady=(0, 4))
        ttk.Label(inner, text="настольная игра Hobby World — против людей, ботов и Claude",
                  style="Muted.TLabel").pack(pady=(0, 16))
        active = profiles.get_active()
        chip = ttk.Frame(inner)
        chip.pack(pady=(0, 16))
        ttk.Label(chip, image=avatars.for_profile(chip, active, 48)).pack(side="left", padx=(0, 10))
        ttk.Label(chip, text=active.name if active else "Профиль не выбран",
                  style="Title.TLabel" if active else "Muted.TLabel").pack(side="left")
        for text, command in (("Новая партия", self.new_game), ("Загрузить партию", self.load_game),
                              ("Подключиться к сетевой партии", self.join_game), ("Профили игроков", self.profiles_screen)):
            ttk.Button(inner, text=text, command=command, style="Start.TButton", width=30).pack(pady=5)
        last = savegame.latest()
        if last:
            ttk.Button(inner, text="Продолжить последнюю партию", style="Start.TButton", width=30,
                       command=lambda: self.load_game(str(last))).pack(pady=5)
        self.set_screen(frame)

    def game_screen(self) -> None:
        frame = ttk.Frame(self.root)
        bar = ttk.Frame(frame, style="Bar.TFrame", padding=(10, 0), height=38)
        bar.pack_propagate(False)  # постоянная высота: иначе фишки действий раунда сдвигают карту
        bar.pack(fill="x")
        self.round_label = ttk.Label(bar, style="Round.TLabel")
        self.round_label.pack(side="left", fill="y")
        self.actions_bar = ttk.Frame(bar, style="Bar.TFrame")
        self.actions_bar.pack(side="left", padx=16, fill="y")
        self.status = ttk.Frame(bar, style="Bar.TFrame")   # подпись с аватарками (set_status)
        self.status.pack(side="right", fill="y")
        self.status_text: str | None = None
        self.log_button = ttk.Button(bar, text="События", command=self.toggle_log)
        self.log_button.pack(side="right", padx=12)
        self.order_bar = ttk.Frame(frame, style="Order.TFrame", padding=(10, 0), height=30)
        self.order_bar.pack_propagate(False)
        self.order_bar.pack(fill="x")

        main = ttk.PanedWindow(frame, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.PanedWindow(main, orient="vertical")
        main.add(left, weight=3)

        # Размеры карты и панели решения задают только разделители (их можно двигать мышью):
        # содержимое панели меняется от хода к ходу, а карта остаётся на месте.
        map_frame = ttk.Frame(left)
        self.map = MapView(map_frame)
        self.map.pack(fill="both", expand=True)
        self.decision_area = ttk.Frame(left, relief="groove", borderwidth=1, height=DECISION_HEIGHT)
        self.decision_area.pack_propagate(False)
        left.add(map_frame, weight=1)
        left.add(self.decision_area, weight=0)

        def place_sashes(event=None):
            main.sashpos(0, int(main.winfo_width() * 0.62))
            left.sashpos(0, max(200, left.winfo_height() - DECISION_HEIGHT))
        main.bind("<Map>", lambda e: self.root.after(50, place_sashes))

        # вкладки: планшет каждого игрока (вкладки игроков появляются с началом партии) и сводка «Все игроки»;
        # карта слева — того игрока, чья вкладка открыта последней
        tabs = ttk.Notebook(main)
        self.boards: list[BoardView] = []
        self.players = PlayersTable(tabs, self.open_board)
        # прозрачная полоска высотой с аватарку — чтобы вкладка была одной высоты с вкладками игроков
        self.tab_strut = tk.PhotoImage(master=self.root, width=1, height=AVATAR)
        tabs.add(self.players, text="Все игроки", image=self.tab_strut, compound="left")
        tabs.bind("<<NotebookTabChanged>>", lambda e: self.tab_changed())
        self.tabs = tabs
        main.add(tabs, weight=2)

        self.build_log(frame)
        self.set_screen(frame)
        self.waiting(idle="Партия начинается…")

    # --- запуск партий ----------------------------------------------------------------------

    def confirm_leave(self) -> bool:
        """Можно ли бросить текущую партию (новая партия, выход)."""
        if self.link is None or self.ended:
            return True
        if isinstance(self.link, LocalLink):
            answer = messagebox.askyesnocancel("«Герои»", "Сохранить текущую партию перед выходом из неё?", parent=self.root)
            if answer is None:
                return False
            if answer:
                self.save()
            return True
        return messagebox.askokcancel("«Герои»", "Покинуть сетевую партию? Потом можно подключиться снова.", parent=self.root)

    def attach(self, link: Link) -> None:
        if self.link is not None:
            self.link.close()
        self.link = link
        self.static = self.state = self.pending = self.handover = None
        self.viewer = self.board_player = None
        self.ended = False
        self.seat_profiles, self.join_profile = {}, None
        self.game_screen()

    def new_game(self) -> None:
        if not self.confirm_leave():
            return
        config = NewGameDialog(self.root, getattr(self.args, "model", None), profiles.all_profiles(),
                               profiles.get_active()).run()
        if not config:
            return
        meta = {"players": config["kinds"], "seed": None, "profiles": {str(s): pid for s, pid in config["profiles"].items()}}
        if "claude" in config["kinds"]:
            meta["claude"] = {"model": config["model"], "effort": config["effort"]}
        self.start_local(lambda link, network: Game(
            self.make_players(link, network, meta, config["names"], config["show_thoughts"]),
            names=config["names"], factions=config["factions"], first_player=config["first"], meta=meta,
            autosave=cli.AUTOSAVE),
            config["port"], config["password"])

    def load_game(self, path: str | None = None) -> None:
        if not self.confirm_leave():
            return
        path = path or filedialog.askopenfilename(parent=self.root, title="Загрузить партию", initialdir="saves",
                                                  filetypes=[("Сохранённые партии", "*.json"), ("Все файлы", "*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            meta, names = data["meta"], data["names"]
        except (OSError, ValueError, KeyError) as e:
            messagebox.showerror("Загрузка", f"Не удалось прочитать {path}: {e}", parent=self.root)
            return
        port = password = None
        seats = [n for n, k in zip(names, meta["players"]) if k == "remote"]
        if seats:
            net = NetworkDialog(self.root, seats).run()
            if not net:
                return
            port, password = net["port"], net["password"]
        thoughts = not {"human", "remote"} & set(meta["players"])
        self.start_local(lambda link, network: savegame.load(
            path, lambda m, n: self.make_players(link, network, m, n, thoughts), autosave=cli.AUTOSAVE),
            port, password)

    def make_players(self, link: LocalLink, network: cli.Network, meta: dict, names: list[str], thoughts: bool):
        return cli.make_players(meta, names, thoughts, network, make_human=link.human, on_thought=link.thought)

    def start_local(self, build, port: int | None, password: str | None) -> None:
        link = LocalLink()
        network = cli.Network(port or 0, password, log_fn=link.status)
        try:
            game = build(link, network)
        except Exception as e:  # повреждённое сохранение и т. п.
            messagebox.showerror("«Герои»", f"Не удалось начать партию: {e}", parent=self.root)
            return
        self.attach(link)
        self.seat_profiles = profiles.seat_profiles(game.meta, [p.name for p in game.state.players])
        for seat, profile_id in self.seat_profiles.items():   # свои аватарки местных профилей — в static
            if avatar := profiles.avatar_b64(profiles.get(profile_id)):
                game.avatars[seat] = avatar
        link.start(game, network.server, password)

    def join_game(self) -> None:
        if not self.confirm_leave():
            return
        a = self.args
        active = profiles.get_active()
        config = JoinDialog(self.root, getattr(a, "join", None) or "",
                            getattr(a, "name", None) or (active.name if active else ""),
                            getattr(a, "password", None) or "").run()
        if config:
            self.connect(config["address"], config["name"], config["password"])

    def connect(self, address: str, name: str | None, password: str | None) -> None:
        active = profiles.get_active()
        link = RemoteLink(address, name, password, avatar=profiles.avatar_b64(active))
        self.attach(link)
        self.join_profile = active.id if active else None   # партия запишется в активный профиль гостя
        self.waiting(idle=f"Подключение к {address}…")
        link.start()

    # --- сообщения партии --------------------------------------------------------------------

    def poll(self) -> None:
        link = self.link
        if link is not None:
            try:
                for _ in range(MAX_MESSAGES):
                    self.handle(link.inbox.get_nowait())
                    if self.link is not link:
                        break
            except queue.Empty:
                pass
            # пока фишки едут, окно не перерисовывается: доедут — и сразу покажутся последствия хода
            if self.dirty and self.state and self.static and not self.map_moving():
                self.dirty = False
                self.refresh()
        self.root.after(POLL_MS, self.poll)

    def map_moving(self) -> bool:
        view = getattr(self, "map", None)
        return bool(view and view.winfo_exists() and view.moving)

    def handle(self, m: dict) -> None:
        t = m.get("t")
        if "state" in m:
            self.state = m["state"]
            self.dirty = True
        if t == "sync":
            self.static = m["static"]
            self.tr = Translator(self.static)
            avatars.use_game(self.root, self.static)
            if m.get("you") is not None:
                self.viewer = self.board_player = m["you"]
                if isinstance(self.link, RemoteLink) and self.join_profile:
                    self.seat_profiles = {m["you"]: self.join_profile}
            if self.board_player is None:
                self.board_player = 0
            self.build_board_tabs()
            self.clear_log()
            for line in m["log"]:
                self.add_log(line)
            self.title()
        elif t == "event":
            self.add_log(m["text"])
            self.map.event(m["text"])
        elif t == "decision":
            self.decision(m)
        elif t == "status":
            text = self.tr.line(m["text"])
            self.add_log(text, "status")
            self.set_status(text.strip().splitlines()[0] if text.strip() else "")
        elif t == "thought":
            self.add_log(f"💭 {m['name']} [{m['kind']}]: {m['text']}", "thought")
        elif t == "welcome":
            self.set_status(f"Вы за столом: {m['name']}")
            self.root.title(f"«Герои» — {m['name']}")
            self.waiting(idle=f"Вы подключены как {m['name']}. Ждём начала партии…")
        elif t == "choose":
            seat = ChooseDialog(self.root, "Выберите место", "Какое место за столом ваше?", m["seats"]).run()
            if seat and isinstance(self.link, RemoteLink):
                self.link.choose_seat(seat)
            else:
                self.link.close()
                self.waiting("Нет подключения.")
        elif t == "end":
            self.finish(m.get("text", ""))
        elif t == "error":
            text = self.tr.line(m["text"])
            self.add_log(text, "warn")
            if m.get("fatal"):
                self.failed(text)
        elif t == "disconnected":
            self.disconnected()

    def refresh(self) -> None:
        st, static = self.state, self.static
        self.round_label.configure(text=f"Раунд {st['round']}/{static['rounds']}" if st["round"] else "Подготовка")
        for w in self.actions_bar.winfo_children():
            w.destroy()
        # фазы раунда фишками; текущая подсвечена, прошедшие — бледные
        phase = None if self.ended else st.get("phase")   # у хозяина старой версии фазы нет
        chip = lambda text, style: ttk.Label(self.actions_bar, text=text, style=style).pack(side="left", padx=2, pady=6)
        if st["round"]:
            if phase == 0:
                chip("Выбор порядка действий", "Current.TLabel")
            else:
                for i, a in enumerate(st["action_order"], 1):
                    style = "Chip.TLabel" if phase is None or i > phase else "Current.TLabel" if i == phase else "Done.TLabel"
                    chip(f"{i}. {ACTION_NAMES[a]}", style)
        elif not self.ended:
            chip("Выбор стартовых регионов", "Current.TLabel")
        self.show_order()
        self.map.show(static, st, self.board_player, self.viewer)  # карта — того же игрока, чей планшет открыт
        for p, board in enumerate(self.boards):   # невидимые планшеты перерисуются, когда их откроют
            board.show(static, st, p, self.viewer) if p == self.board_player else board.stale(static, st, p, self.viewer)
        self.select_board_tab()
        self.players.show(static, st, self.viewer)
        self.show_thinking()

    def show_order(self) -> None:
        """Строка очерёдности: игроки по порядку (кто сейчас решает — подсвечен) и первые игроки
        следующих раундов."""
        st, static = self.state, self.static
        bar = self.order_bar
        for w in bar.winfo_children():
            w.destroy()
        if self.ended:
            return
        label = lambda text: ttk.Label(bar, text=text, style="Order.TLabel").pack(side="left", padx=2)
        label("Очерёдность:" if st["round"] else "Выбор стартовых регионов:")
        now = {p for p, _ in st.get("thinking", [])}
        for i, p in enumerate(turn_order(static, st)):
            if i:
                label("→")
            kind = "OrderNow" if p in now else "OrderChip"
            chip = ttk.Frame(bar, style=f"{kind}.TFrame", padding=(5, 0))
            chip.pack(side="left", pady=4)
            role = " · первый, выбирает действия" if st["round"] and i == 0 else ""
            RichLabel(chip, f"{i + 1}. {name_of(static, p, avatar=True)}{role}" + (" — решает" if p in now else ""),
                      style=f"{kind}.TLabel", frame_style=f"{kind}.TFrame").pack(side="left")
        upcoming = next_first_players(static, st, len(static["names"]))
        if upcoming:
            label("   Первым будет:")
            for j, (r, p) in enumerate(upcoming):
                RichLabel(bar, ("раунд " if j == 0 else "") + f"{r} — {name_of(static, p, avatar=True)}"
                          + ("," if j < len(upcoming) - 1 else ""),
                          style="Order.TLabel", frame_style="Order.TFrame").pack(side="left", padx=2)
        else:
            label("   Последний раунд.")

    def title(self) -> None:
        if self.viewer is not None and self.static:
            self.root.title(f"«Герои» — {name_of(self.static, self.viewer)}")

    @property
    def board(self) -> BoardView:
        """Планшет игрока, чья вкладка открыта (или открыта последней)."""
        return self.boards[self.board_player or 0]

    def build_board_tabs(self) -> None:
        """Вкладка на каждого игрока партии — аватарка и «Имя (Фракция)»; «Все игроки» — последней."""
        for board in self.boards:
            board.destroy()
        self.boards = []
        for p in range(len(self.static["names"])):
            board = BoardView(self.tabs)
            self.tabs.insert(p, board, text=" " + name_of(self.static, p), image=avatars.seat(self.root, p, AVATAR),
                             compound="left")
            self.boards.append(board)
        if self.board_player is not None and self.board_player < len(self.boards):
            self.tabs.select(self.boards[self.board_player])

    def select_board_tab(self) -> None:
        """Открыть вкладку того, чья карта показана (если открыта вкладка другого игрока)."""
        p = self.board_player
        if p is None or p >= len(self.boards) or str(self.tabs.select()) == str(self.players):
            return
        if str(self.tabs.select()) != str(self.boards[p]):
            self.tabs.select(self.boards[p])

    def tab_changed(self) -> None:
        """Открыта вкладка игрока — его планшет и карта; «Все игроки» — карта остаётся прежней."""
        current = str(self.tabs.select())
        p = next((q for q, board in enumerate(self.boards) if str(board) == current), None)
        if p is None:
            return
        if p != self.board_player:
            self.board_player = p
            self.dirty = True
        elif self.board.outdated and self.static and self.state:
            self.board.show(self.static, self.state, p, self.viewer)   # был скрыт — дорисовать

    def show_board(self, p: int) -> None:
        if p >= 0:
            self.board_player = p
            self.dirty = True

    def open_board(self, p: int) -> None:
        self.show_board(p)
        if p < len(self.boards):
            self.tabs.select(self.boards[p])

    def set_status(self, text: str) -> None:
        """Строка справа на полосе; имена игроков — с аватарками (name_of(…, avatar=True))."""
        if text == self.status_text:
            return
        self.status_text = text
        for w in self.status.winfo_children():
            w.destroy()
        RichLabel(self.status, text, style="Status.TLabel", frame_style="Bar.TFrame").pack(side="right", fill="y", pady=8)

    # --- журнал ------------------------------------------------------------------------------

    def build_log(self, screen: ttk.Frame) -> None:
        """Журнал событий — отдельное окно поверх главного: открывается кнопкой «События» (Ctrl+E),
        закрывается крестиком, Esc или той же кнопкой. Пока окно скрыто, события в него всё равно пишутся,
        а на кнопке растёт число новых. Окно — дочернее экрану партии и исчезает вместе с ним."""
        win = tk.Toplevel(screen)
        win.withdraw()
        win.title("События партии")
        win.transient(self.root)   # поверх главного окна и сворачивается вместе с ним
        win.configure(background=PANEL_BG)
        win.protocol("WM_DELETE_WINDOW", self.toggle_log)
        win.bind("<Escape>", lambda e: self.toggle_log())
        win.bind("<Control-e>", lambda e: self.toggle_log())
        self.log_window = win
        self.log_placed = False
        self.unread = 0
        self.log = tk.Text(win, wrap="word", width=70, height=34, background="white", relief="flat",
                           font=font(9), padx=8, pady=4, state="disabled")
        scroll = ttk.Scrollbar(win, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("round", font=font(10, "bold"), foreground="#7a2e0e", spacing1=6)
        self.log.tag_configure("status", foreground="#2f6fd6")
        self.log.tag_configure("thought", foreground="#7b3fb5", font=font(9, "italic"))
        self.log.tag_configure("warn", foreground=BAD)
        self.log.tag_configure("end", font="TkFixedFont")
        for p in range(4):
            self.log.tag_configure(f"p{p}", foreground=color_of(p))

    def toggle_log(self) -> None:
        win = getattr(self, "log_window", None)
        if win is None or not win.winfo_exists():
            return
        if win.winfo_viewable():
            win.withdraw()
            return
        if not self.log_placed:   # в первый раз — у правого края главного окна, дальше — где оставили
            self.root.update_idletasks()
            x = self.root.winfo_rootx() + max(0, self.root.winfo_width() - 620)
            win.geometry(f"+{x}+{self.root.winfo_rooty() + 60}")
            self.log_placed = True
        win.deiconify()
        win.lift()
        win.focus_set()
        self.log.see("end")
        self.unread = 0
        self.log_button.configure(text="События")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def add_log(self, line: str, tag: str | None = None) -> None:
        if tag is None:
            line = self.tr.line(line)
            if line.startswith("==="):
                tag = "round"
            elif line.startswith("⚠"):
                tag = "warn"
            elif self.static:
                tag = next((f"p{p}" for p, n in enumerate(self.static["names"]) if line.startswith(n + " ")), None)
        if self.static and tag != "end":
            line = with_factions(self.static, line)
        at_end = self.log.yview()[1] > 0.98
        self.log.configure(state="normal")
        start = 0
        if self.static and tag != "end":   # итоговая таблица — моноширинная, картинки сбили бы столбцы
            for pos, p in name_spans(self.static, line):
                self.log.insert("end", line[start:pos], tag or ())
                self.log.image_create("end", image=avatars.seat(self.root, p, AVATAR - 2), padx=2, align="center")
                start = pos
        self.log.insert("end", line[start:] + "\n", tag or ())
        self.log.configure(state="disabled")
        if at_end:
            self.log.see("end")
        if not self.log_window.winfo_viewable():
            self.unread += 1
            self.log_button.configure(text=f"События ({self.unread})")

    # --- решения -----------------------------------------------------------------------------

    def clear_decision(self) -> None:
        for w in self.decision_area.winfo_children():
            w.destroy()
        self.map.select_mode(None)
        self.map.set_preview(None)
        for board in self.boards:
            board.select_mode(None)
            board.select_monsters(None)

    def open_own_board(self, player: int) -> None:
        if self.board_player != player:
            self.board_player = player
            self.refresh()
        self.tabs.select(self.boards[player])

    def pick_building(self, player: int, options: set[str], on_pick) -> None:
        """Панель строительства: здания можно выбрать щелчком на планшете игрока player."""
        self.open_own_board(player)
        self.board.select_mode(player, options, None, on_pick)

    def pick_monster(self, player: int, options: set[int], on_pick) -> None:
        """Бой с чудовищем: чудовище можно выбрать щелчком по линии на планшете игрока player."""
        self.open_own_board(player)
        self.board.select_monsters(player, options, on_pick)

    def waiting(self, text: str | None = None, idle: str = "Ждём остальных игроков…") -> None:
        """Панель ожидания. text — своё пояснение (например, «передайте ход»); под ним — кто сейчас
        что решает (show_thinking обновляет это при каждом новом состоянии); idle — пока это неизвестно."""
        self.clear_decision()
        frame = ttk.Frame(self.decision_area, padding=16)
        frame.pack(fill="both", expand=True)
        if text:
            RichLabel(frame, text, style="Muted.TLabel").pack(anchor="w")
        self.wait_box = ttk.Frame(frame)   # кто сейчас что решает — строками с аватарками
        self.wait_box.pack(anchor="w", pady=(8 if text else 0, 0))
        self.wait_note = bool(text)
        self.wait_text = None
        self.set_wait("" if text else idle)
        self.show_thinking()

    def set_wait(self, text: str) -> None:
        if text == self.wait_text:
            return
        self.wait_text = text
        for w in self.wait_box.winfo_children():
            w.destroy()
        for line in text.splitlines():
            RichLabel(self.wait_box, line, style="Title.TLabel").pack(anchor="w")

    def show_thinking(self) -> None:
        box = getattr(self, "wait_box", None)
        if box is None or not box.winfo_exists() or self.pending or self.handover or self.ended or not self.state:
            return
        text = thinking_text(self.static, self.state, avatar=True)
        if text:
            self.set_wait(text)
            self.set_status("Ждём: " + ", ".join(name_of(self.static, p, avatar=True) for p, _ in self.state["thinking"]))
        elif "thinking" in self.state:   # никто не решает: партия считает ход (или ждём сеть)
            self.set_wait("" if self.wait_note else "Ждём остальных игроков…")
            self.set_status("Ждём остальных игроков…")

    def hotseat(self) -> bool:
        return isinstance(self.link, LocalLink) and len(self.link.humans) > 1

    def decision(self, m: dict) -> None:
        self.pending = m
        if self.hotseat() and m["player"] != self.viewer:
            self.pass_screen(m)
            return
        self.open_decision(m)

    def pass_screen(self, m: dict) -> None:
        """Несколько людей за одним окном: перед ходом другого игрока экран передаётся ему."""
        self.handover = m
        self.clear_decision()
        name = name_of(self.static, m["player"])
        frame = ttk.Frame(self.decision_area, padding=20)
        frame.pack(fill="both", expand=True)
        RichLabel(frame, "Ход игрока " + name_of(self.static, m["player"], avatar=True), style="Title.TLabel",
                  foreground=color_of(m["player"])).pack(anchor="w")
        ttk.Label(frame, text="Остальные — не смотрите на экран.", style="Muted.TLabel").pack(anchor="w", pady=4)
        ttk.Button(frame, text=f" Я — {name}, показать моё решение", style="Primary.TButton", compound="left",
                   image=avatars.seat(self.root, m["player"], AVATAR + 4),
                   command=lambda: self.open_decision(m)).pack(anchor="w", pady=8)
        self.set_status("Ждём игрока " + name_of(self.static, m["player"], avatar=True))

    def open_decision(self, m: dict) -> None:
        self.handover = None
        if self.viewer != m["player"]:
            self.viewer = self.board_player = m["player"]
            self.title()
        if self.state:
            self.refresh()
        self.clear_decision()
        panel = make_panel(self.decision_area, self, m)
        panel.pack(fill="both", expand=True)
        self.set_status("Ваше решение" + (f", {name_of(self.static, m['player'], avatar=True)}" if self.hotseat() else ""))
        self.root.bell()

    def send_answer(self, m: dict, answer) -> None:
        if self.pending is not m:
            return
        self.pending = None
        self.link.answer(m["id"], answer)
        if self.hotseat() and is_secret(m):
            self.waiting(f"{name_of(self.static, m['player'], avatar=True)}: выбор сделан и скрыт. Передайте ход.")
        else:
            self.waiting()
        self.set_status("Ждём остальных игроков…")

    def finish(self, text: str) -> None:
        """text — итоги от хозяина (по-английски); таблицу строим сами по последнему состоянию."""
        self.ended = True
        self.pending = None
        self.clear_decision()
        finished = "FINAL SCORES" in text and self.state is not None
        usage = [line for line in text.splitlines() if "Claude calls" in line]   # расход Claude — как есть
        recorded: list[str] = []
        if finished:
            text = final_table(self.state, self.static) + "".join("\n" + line for line in usage)
            recorded = self.record_game()
        else:
            text = self.tr.line(text.strip()) or "Партия прервана."
        self.add_log("ИТОГИ\n" + text, "end")
        frame = ttk.Frame(self.decision_area, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Партия окончена", style="Title.TLabel").pack(anchor="w")
        if finished:
            self.score_table(frame).pack(anchor="w", pady=6)
            for line in usage:
                ttk.Label(frame, text=line, style="Muted.TLabel").pack(anchor="w")
        else:
            tk.Label(frame, text=text, font="TkFixedFont", justify="left", anchor="w",
                     background=PANEL_BG).pack(anchor="w", pady=6)
        if recorded:
            ttk.Label(frame, text="Партия записана в статистику профил" + ("я " if len(recorded) == 1 else "ей ")
                      + ", ".join(recorded), style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Button(frame, text="На стартовый экран", command=self.back_to_start).pack(anchor="w")
        self.set_status("Партия окончена")

    def score_table(self, master) -> ttk.Frame:
        """Итоговые очки: столбец на игрока (аватарка и имя), строки — категории ПО; победитель — ★."""
        st, static = self.state, self.static
        table = ttk.Frame(master)
        win = winners(st)
        for p, pl in enumerate(st["players"]):
            head = ttk.Label(table, text=name_of(static, p) + (" ★" if p in win else ""), compound="top",
                             image=avatars.seat(self.root, p, 36), foreground=GOOD if p in win else color_of(p),
                             style="Bold.TLabel")
            head.grid(row=0, column=p + 1, padx=10)
            for row, key in enumerate([*SCORE_NAMES, "total"], 1):
                ttk.Label(table, text=str(pl["score"][key]), style="Bold.TLabel" if key == "total" else "TLabel").grid(
                    row=row, column=p + 1)
        for row, title in enumerate([*SCORE_NAMES.values(), "ИТОГО"], 1):
            ttk.Label(table, text=title, style="Bold.TLabel" if row > len(SCORE_NAMES) else "TLabel").grid(
                row=row, column=0, sticky="w")
        return table

    def record_game(self) -> list[str]:
        """Доигранную партию — в статистику профилей за этим окном. Возвращает их имена."""
        if not self.seat_profiles or self.static is None:
            return []
        try:
            return profiles.record_game(self.static, self.state, self.seat_profiles)
        except (OSError, KeyError, ValueError) as e:
            self.add_log(f"⚠ Не удалось записать партию в профиль: {e}", "warn")
            return []

    def failed(self, text: str) -> None:
        self.ended = True
        self.clear_decision()
        frame = ttk.Frame(self.decision_area, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Партию нельзя продолжить", style="Title.TLabel", foreground=BAD).pack(anchor="w")
        ttk.Label(frame, text=text[-1500:], wraplength=760, justify="left").pack(anchor="w", pady=6)
        ttk.Button(frame, text="На стартовый экран", command=self.back_to_start).pack(anchor="w")
        if self.screen is not None:
            self.set_status("Ошибка")

    def disconnected(self) -> None:
        self.pending = None
        self.clear_decision()
        frame = ttk.Frame(self.decision_area, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Связь с хозяином потеряна", style="Title.TLabel", foreground=BAD).pack(anchor="w")
        ttk.Label(frame, text="Партия ждёт вас на компьютере хозяина.", style="Muted.TLabel").pack(anchor="w", pady=4)
        ttk.Button(frame, text="Подключиться снова", style="Primary.TButton", command=self.reconnect).pack(anchor="w", pady=6)
        ttk.Button(frame, text="На стартовый экран", command=self.back_to_start).pack(anchor="w")
        self.set_status("Нет связи")

    def reconnect(self) -> None:
        link = self.link
        if isinstance(link, RemoteLink):
            self.connect(link.address, link.name, link.password)

    def back_to_start(self) -> None:
        if not self.confirm_leave():
            return
        if self.link:
            self.link.close()
        self.link = None
        self.start_screen()

    def profiles_screen(self) -> None:
        if not self.confirm_leave():
            return
        if self.link:
            self.link.close()
        self.link = None
        self.root.title("«Герои» — профили игроков")
        self.set_screen(ProfileView(self.root, on_back=self.start_screen))

    # --- сохранение и выход -------------------------------------------------------------------

    def save(self, path: str | None = None) -> None:
        if not isinstance(self.link, LocalLink) or self.link.game is None:
            messagebox.showinfo("Сохранение", "Сохранить партию может только хозяин.", parent=self.root)
            return
        try:
            saved = self.link.save(path)
        except OSError as e:
            messagebox.showerror("Сохранение", str(e), parent=self.root)
            return
        self.add_log(f"Партия сохранена: {saved}", "status")
        self.set_status(f"Сохранено: {saved}")

    def save_as(self) -> None:
        if not isinstance(self.link, LocalLink):
            return self.save()
        default = savegame.default_path(self.link.game)
        path = filedialog.asksaveasfilename(parent=self.root, title="Сохранить партию", initialdir=str(default.parent),
                                            initialfile=default.name, defaultextension=".json",
                                            filetypes=[("Сохранённые партии", "*.json")])
        if path:
            self.save(path)

    def quit(self) -> None:
        if not self.confirm_leave():
            return
        if self.link:
            self.link.close()
        self.root.destroy()


def run_app(args: argparse.Namespace | None = None) -> int:
    root = tk.Tk(className="heroes")
    setup_style(root)
    app = App(root, args)
    if args is not None and getattr(args, "join", None):
        root.after(100, lambda: app.connect(args.join, args.name, args.password))
    elif args is not None and getattr(args, "load", None):
        root.after(100, lambda: app.load_game(args.load))
    root.mainloop()
    return 0
