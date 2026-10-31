"""Запуск партии из терминала. Партия идёт на этом компьютере; люди играют в браузере
(на телефоне, планшете или этом же компьютере): страницу раздаёт сервер партии (heroes/net/web.py).

  python -m heroes                                  # мастер настройки: число игроков, кто за кем
  python -m heroes --players human,bot,bot          # без мастера: человек (в браузере) против двух ботов
  python -m heroes --players human,claude,claude    # человек против двух Claude
  python -m heroes --players human,bot --factions orcs,random   # человек за орков, бот — за случайную фракцию
  python -m heroes --players claude,claude --show-thoughts   # Claude против Claude, с замыслами
  python -m heroes --players claude,claude --watch  # то же, а смотреть партию можно в браузере
  python -m heroes --players human,human --names Аня,Боря --password сова
  python -m heroes --load saves/auto/<файл>.json    # продолжить сохранённую партию
  python -m heroes --continue                       # продолжить самую свежую
  python -m heroes --profiles                       # только страница «Профили и статистика»
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import threading
import time
from functools import cache
from typing import Callable
from urllib.parse import quote

from . import profiles, savegame
from .engine import scoring
from .engine.data import DATA_DIR
from .engine.game import Background, Game
from .net.protocol import DEFAULT_PORT
from .net.server import Server, local_addresses
from .players.base import Player
from .players.claude_cli import DEFAULT_EFFORT, DEFAULT_MODEL, ClaudePlayer
from .players.random_bot import RandomBot
from .players.remote import RemotePlayer, Spectators
from .protocol import journal
from .protocol import render_text as R
from .protocol.snapshot import snapshot, static_data, sync_message

PLAYER_TYPES = ("human", "claude", "bot")
HUMANS = profiles.HUMANS   # «remote» — человек по сети в сохранениях прежних версий; играет так же, как «human»
TYPE_NAMES = {"human": "human (in a browser)", "claude": "Claude", "bot": "random bot"}
MODELS = {"claude-opus-5-5": "Opus 5.5 — strongest, ≈$3–4 per game per player",
          "claude-sonnet-5-5": "Sonnet 5.5 — faster and cheaper",
          "claude-haiku-5-5": "Haiku 5.5 — very cheap, weaker"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
AUTOSAVE = "saves"   # папка автосохранений (в ней auto/ и final/)
# Фракции первой коробки в порядке правил; у каждой один планшет, поэтому за столом они не повторяются.
FACTIONS = ("humans", "undead", "dwarves", "orcs", "elves", "nagas")
RANDOM = "random"


@cache
def faction_names() -> dict[str, str]:
    """Названия фракций («Люди», «Нежить», …) по их id."""
    return {f: json.loads((DATA_DIR / "factions" / f"{f}.json").read_text(encoding="utf-8"))["name"] for f in FACTIONS}


def resolve_factions(choices: list[str], rng: random.Random | None = None) -> list[str]:
    """Выбор фракций по местам: id или «random» (из тех, что никто не выбрал). Повторы — ошибка."""
    unknown = [f for f in choices if f not in FACTIONS and f != RANDOM]
    if unknown:
        raise ValueError(f"Unknown faction: {unknown[0]}. Valid: {', '.join(FACTIONS)}, {RANDOM}")
    chosen = [f for f in choices if f != RANDOM]
    if len(set(chosen)) != len(chosen):
        raise ValueError("Each faction can be played by one player only")
    free = [f for f in FACTIONS if f not in chosen]
    (rng or random.Random()).shuffle(free)
    return [free.pop() if f == RANDOM else f for f in choices]


def shuffle_seats(kinds: list[str], names: list[str], factions: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Случайный первый игрок — и случайная рассадка: места перемешиваются вместе с типом, именем и фракцией,
    первым ходит место 0."""
    seats = list(zip(kinds, names, factions))
    random.shuffle(seats)
    return [k for k, _, _ in seats], [n for _, n, _ in seats], [f for _, _, f in seats]


def print_thought(name: str, kind: str, comment: str) -> None:
    print(f"💭 {name} [{kind}]: {comment}")


class Network:
    """Сервер партии; создаётся, только если за столом есть люди или партию открыли для зрителей."""

    def __init__(self, port: int, password: str | None, log_fn: Callable[[str], None] = print):
        self.port = port
        self.password = password
        self.log_fn = log_fn
        self.server: Server | None = None

    def open(self) -> Server:
        if self.server is None:
            self.server = Server(self.port, self.password, self.log_fn)
        return self.server

    def seat(self, name: str):
        return self.open().seat(name)


def make_players(meta: dict, names: list[str], show_thoughts: bool | None = None,
                 network: Network | None = None,
                 on_thought: Callable[[str, str, str], None] = print_thought) -> list[Player]:
    """show_thoughts: None — показывать замыслы Claude, только если за столом нет людей."""
    seed = meta.get("seed")
    claude = meta.get("claude", {})
    sessions = meta.get("claude_sessions", {})
    if show_thoughts is None:
        show_thoughts = not set(HUMANS) & set(meta["players"])
    players: list[Player] = []
    for i, kind in enumerate(meta["players"]):
        if kind in HUMANS:
            if network is None:
                raise SystemExit("Human players need a server")
            players.append(RemotePlayer(network.seat(names[i])))
        elif kind == "bot":
            players.append(RandomBot(None if seed is None else seed + i))
        elif kind == "claude":
            players.append(ClaudePlayer(model=claude.get("model", DEFAULT_MODEL), effort=claude.get("effort"),
                                        session_id=sessions.get(str(i)), tools=claude.get("tools"),
                                        on_comment=on_thought if show_thoughts else None))
        else:
            raise SystemExit(f"Unknown player type: {kind}. Valid: {', '.join(PLAYER_TYPES)}")
    return players


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m heroes",
                                 description="«Герои» — host a game; humans play in a browser")
    ap.add_argument("--players",
                    help="comma-separated player types, clockwise: human, claude, bot (2–4 players); "
                         "without it the game is set up interactively")
    ap.add_argument("--names", help="comma-separated player names (a human joins the game with their name)")
    ap.add_argument("--factions", help=f"comma-separated factions in seat order: {', '.join(FACTIONS)} or {RANDOM} "
                                       f"(default: {RANDOM} for everyone; a faction can be played by one player only)")
    ap.add_argument("--first", default="0", help=f"first player's number (from 0) or {RANDOM} (also shuffles the seats), "
                                                 "with --players")
    ap.add_argument("--seed", type=int, help="seed for random bots")
    ap.add_argument("--load", metavar="FILE", help="continue a saved game")
    ap.add_argument("--continue", dest="resume", action="store_true", help="continue the most recent saved game")
    ap.add_argument("--model", help=f"model for Claude players (default {DEFAULT_MODEL})")
    ap.add_argument("--effort", choices=EFFORTS, help=f"Claude's thinking effort (default: {DEFAULT_EFFORT})")
    ap.add_argument("--tools", action=argparse.BooleanOptionalAction, default=None,
                    help="give Claude tools: its own scripts and game simulation in a bwrap sandbox "
                         "(default: when bwrap is installed, i.e. on Linux)")
    ap.add_argument("--show-thoughts", action=argparse.BooleanOptionalAction, default=None,
                    help="print Claude's plans in this terminal (default: only when no humans are playing)")
    ap.add_argument("--autosave", default=AUTOSAVE, help=f"folder for the autosaves made at the start of each round (its auto/) and for the final save "
                    f"of a finished game (its final/); default {AUTOSAVE}; '' disables both")
    net = ap.add_argument_group("browser")
    net.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"game server port (default {DEFAULT_PORT})")
    net.add_argument("--password", help="game password (humans enter it in the browser)")
    net.add_argument("--watch", action="store_true",
                     help="open the game for spectators in a browser even when no humans are playing")
    net.add_argument("--profiles", action="store_true",
                     help="serve only the «Profiles and statistics» page, without a game")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.profiles:
        return serve_profiles(args)
    autosave = args.autosave or None
    network = Network(args.port, args.password)
    if args.resume:
        found = [f for f in {savegame.latest(), autosave and savegame.latest(autosave)} if f]
        args.load = max(found, key=lambda f: f.stat().st_mtime, default=None)
        if args.load is None:
            raise SystemExit(f"No saved games in {savegame.SAVE_DIR}/")
        print(f"Continuing {args.load}")
    if args.load:
        game = savegame.load(args.load, lambda meta, names: make_players(meta, names, args.show_thoughts, network),
                             autosave=autosave)
    else:
        if args.players:
            kinds = [k.strip() for k in args.players.split(",")]
            names = [n.strip() for n in args.names.split(",")] if args.names else default_names(kinds)
            factions = [f.strip() for f in args.factions.split(",")] if args.factions else [RANDOM] * len(kinds)
            model = args.model or DEFAULT_MODEL
            if args.first == RANDOM:
                first = None   # рассадка перемешивается после проверок: первым — место 0
            elif args.first.isdigit() and int(args.first) < len(kinds):
                first = int(args.first)
            else:
                raise SystemExit(f"--first: a player's number from 0 to {len(kinds) - 1} or {RANDOM}")
        else:
            try:
                setup = setup_wizard(args)
            except (EOFError, KeyboardInterrupt):
                print("\nGame not started.")
                return 0
            if setup is None:
                print("Game not started.")
                return 0
            kinds, names, factions, first, model = setup
            network.port, network.password = args.port, args.password
        kinds = ["human" if k == "remote" else k for k in kinds]
        if unknown := [k for k in kinds if k not in PLAYER_TYPES]:
            raise SystemExit(f"Unknown player type: {unknown[0]}. Valid: {', '.join(PLAYER_TYPES)}")
        if len(names) != len(kinds):
            raise SystemExit("The number of names must match the number of players")
        if not 2 <= len(kinds) <= 4:
            raise SystemExit("«Герои» is played by 2 to 4 players")
        if not all(names) or len({n.casefold() for n in names}) != len(names):
            raise SystemExit("Player names must be non-empty and distinct")
        if len(factions) != len(kinds):
            raise SystemExit("The number of factions must match the number of players")
        if first is None:
            kinds, names, factions = shuffle_seats(kinds, names, factions)
            first = 0
        try:
            factions = resolve_factions(factions)
        except ValueError as e:
            raise SystemExit(str(e)) from None
        if "claude" in kinds and shutil.which("claude") is None:
            raise SystemExit("Claude players need the Claude Code CLI (`claude`) installed and logged in")
        meta = {"players": kinds, "seed": args.seed}
        if "claude" in kinds:
            meta["claude"] = {"model": model, "effort": args.effort or DEFAULT_EFFORT, "tools": args.tools}
        game = Game(make_players(meta, names, args.show_thoughts, network), names=names, factions=factions,
                    first_player=first, meta=meta, autosave=autosave)
    for seat, profile_id in profiles.seat_profiles(game.meta, [p.name for p in game.state.players]).items():
        if avatar := profiles.avatar_b64(profiles.get(profile_id)):   # аватарки из профилей — всем за столом
            game.avatars[seat] = avatar
    server = network.open() if args.watch else network.server
    try:
        if server and not open_table(game, server, args):
            return 0
        game.listeners.append(lambda message: None if game.replaying else print(message))
        report_claude_setup(game)
        ended = threading.Event()
        commands = None
        if sys.stdin.isatty():
            print("Type `save` and Enter to save the game now; Ctrl+C saves it and stops.")
            commands = threading.Thread(target=host_commands, args=(game,), kwargs={"ended": ended},
                                        daemon=True, name="commands")
            commands.start()
        try:
            scores = game.run()
        except KeyboardInterrupt:
            path = savegame.save(game, savegame.default_path(game, savegame.INTERRUPTED))
            print(f"\nInterrupted. Game saved: {path}")
            if server:
                server.broadcast_end("The host interrupted the game. It is saved and can be continued.")
            return 130
        result = R.final_scores(game, scores, scoring.winners(game, scores))
        print()
        print(result)
        if game.final_save:
            print(f"Final save (load it to see the results again): {game.final_save}")
        record_profiles(game)
        if server:
            server.broadcast_end("\n" + result)
        record_lessons(game)
        report_claude_usage(game)
        ended.set()
        if server and server.seats and commands:
            linger(server, commands)
        return 0
    finally:
        if server:
            server.close()


def host_commands(game: Game, input_fn: Callable[[], str] = input, out: Callable[[str], None] = print,
                  ended: threading.Event | None = None) -> None:
    """Команды хозяина в терминале, пока идёт партия: save [файл] — сохранить, не прерывая её.
    Строка, введённая после конца партии (ended), завершает поток: ей хозяин закрывает страницу (linger)."""
    while True:
        try:
            line = input_fn().strip()
        except (EOFError, OSError):
            return
        if ended is not None and ended.is_set():
            return
        command, _, rest = line.partition(" ")
        if command == "save":
            try:
                out(f"Game saved: {savegame.save(game, rest.strip() or savegame.default_path(game))}")
            except (OSError, RuntimeError, ValueError) as e:   # RuntimeError: партия менялась, пока писали
                out(f"Could not save the game: {e}")
        elif line:
            out("Commands: `save [file]` — save the game; Ctrl+C — save and stop it.")


def linger(server: Server, commands: threading.Thread) -> None:
    """После партии страница ещё открыта: люди смотрят итоги и свою статистику (профили). Enter хозяина
    читает поток команд (commands) — он и завершается."""
    print(f"\nThe page stays open for the results and the profile statistics (http://{local_addresses()[0]}:{server.port}).")
    print("Press Enter to close it.")
    try:
        while commands.is_alive():
            commands.join(0.5)
    except KeyboardInterrupt:
        print()


def serve_profiles(args: argparse.Namespace) -> int:
    """Страница «Профили и статистика» без партии — до Ctrl+C."""
    server = Server(args.port, args.password)
    try:
        server.start()
    except OSError as e:
        raise SystemExit(f"Could not open port {server.port}: {e}. Choose another one with --port") from None
    print(f"Profiles and statistics: http://{local_addresses()[0]}:{server.port}/#profiles   (Ctrl+C — stop)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        server.close()


def join_link(address: str, port: int, name: str | None = None, password: str | None = None) -> str:
    """Адрес страницы; с именем и паролем браузер сразу садится за это место."""
    query = "&".join(f"{k}={quote(v)}" for k, v in (("name", name), ("password", password)) if v)
    return f"http://{address}:{port}/" + (f"?{query}" if query else "")


def open_table(game: Game, server: Server, args: argparse.Namespace, out: Callable[[str], None] = print) -> bool:
    """Запустить сервер и дождаться всех людей. False — хозяин передумал."""
    for p, player in enumerate(game.players):  # до старта сервера: первый же гость должен получить партию
        if isinstance(player, RemotePlayer):
            player.bind(game, p)
    Spectators(game, server.audience)
    try:
        server.start()
    except OSError as e:
        raise SystemExit(f"Could not open port {server.port}: {e}. Choose another one with --port") from None
    password = getattr(args, "password", None)
    address = local_addresses()[0]
    if server.seats:
        out("\nThe game is open. Each human opens their link in a browser — on a phone, tablet or computer:")
        for name in server.seats:
            out(f"  {name}: {join_link(address, server.port, name, password)}")
        out(f"  (or open http://{address}:{server.port} and enter the name"
            + (" and the password)" if password else ")"))
        out(f"Spectators: http://{address}:{server.port} → «Смотреть партию». Over the internet you need to forward "
            f"port {server.port} or share a VPN network (e.g. Tailscale).")
    else:
        out(f"\nSpectators can watch the game in a browser: http://{address}:{server.port} → «Смотреть партию».")
    try:
        server.wait_all()
    except KeyboardInterrupt:
        out("\nGame not started.")
        return False
    if server.seats:
        out("Everyone is here — let's start!")
    for p, player in enumerate(game.players):   # пока ждали, подключились другие — с их аватарками
        if isinstance(player, RemotePlayer):
            player.seat.send(sync_message(game, p))
    return True


def record_profiles(game: Game, out: Callable[[str], None] = print) -> None:
    """Доигранную партию — в статистику профилей людей за столом (профиль — по имени; нет — заводится),
    с аватаркой, с которой человек играл."""
    try:
        seats = profiles.seat_profiles(game.meta, [p.name for p in game.state.players], create_missing=True)
        for seat, profile_id in seats.items():
            if (avatar := game.avatars.get(seat)) and (profile := profiles.get(profile_id)):
                try:
                    profiles.set_avatar(profile, avatar)
                except ValueError:
                    pass
        save = str(game.final_save) if game.final_save else None
        names = profiles.record_game(static_data(game), snapshot(game), seats, save=save) if seats else []
    except OSError as e:
        out(f"Could not record the game in the profiles: {e}")
        return
    if names:
        out("Recorded in the profile statistics: " + ", ".join(names))


def report_claude_setup(game: Game, out: Callable[[str], None] = print) -> None:
    """В начале партии: модель, усилия и инструменты каждого Claude-игрока."""
    for p, player in enumerate(game.players):
        if isinstance(player, ClaudePlayer):
            out(f"{game.state.players[p].name}: Claude {player.model}, effort {player.effort or 'default'}, "
                f"{player.tools_status()}")


def report_claude_usage(game: Game, out: Callable[[str], None] = print) -> None:
    for p, player in enumerate(game.players):
        if isinstance(player, ClaudePlayer) and player.calls:
            t = player.tokens
            out(f"{game.state.players[p].name}: {player.calls} Claude calls, ≈ ${player.cost_usd:.2f}, "
                f"tokens: {t['input']:,} in, {t['output']:,} out (thinking {t['thinking']:,}; "
                f"≈ {t['output'] // player.calls:,} out per call)\n  transcript: logs/claude/{player.session_id}.jsonl, "
                f"story of the game: logs/claude/{player.session_id}.story.md")


def record_lessons(game: Game, out: Callable[[str], None] = print,
                   on_thought: Callable[[str, str, str], None] = print_thought) -> None:
    """После партии: результат каждого Claude-игрока — в strategy/results.csv; затем каждый разбирает
    свою игру (разбор — в журнал, как замыслы) и может дописать выводы в заметки своей фракции
    и поправить скрипты. Все Claude — параллельно."""
    claudes = [(p, player) for p, player in enumerate(game.players) if isinstance(player, ClaudePlayer) and player.started]
    if not claudes:
        return
    for p, player in claudes:
        try:
            journal.record_result(player.result_row(game, p))
        except OSError as e:
            out(f"Could not record the result in {journal.root() / 'results.csv'}: {e}")
    for p, player in claudes:
        out(player.tools_report(game, p))    # сначала — что Claude запускал за партию, потом — разбор
    out("Claude players are reviewing the game for their strategy notes…")
    workers = [Background(lambda p=p, player=player: player.reflect(game, p), f"reflect-{p}") for p, player in claudes]
    try:
        results = [worker.result() for worker in workers]
    except KeyboardInterrupt:
        game.stopping.set()
        raise
    for (p, _), result in zip(claudes, results):
        if result:
            review, outcome = result
            name = game.state.players[p].name
            if review:
                on_thought(name, "game_review", review)
            out(f"{name}: {outcome}")
            if game.players[p].reported < len(game.players[p].tool_log):
                out(game.players[p].tools_report(game, p, review=True))


def setup_wizard(args: argparse.Namespace, input_fn: Callable[[str], str] = input,
                 output_fn: Callable[[str], None] = print) -> tuple[list[str], list[str], list[str], int, str] | None:
    """Диалог перед партией: число игроков, кто играет на каждом месте, имена, фракции, первый игрок;
    для Claude — модель, усилие и показ замыслов; для людей — порт и пароль. Ответы о Claude и сети
    записываются в args (model, effort, show_thoughts, port, password). Фракция может остаться
    «случайной» — её выбирает resolve_factions. None — хозяин передумал."""

    def ask(prompt: str, options: list[str], default: int) -> int:
        """Номер варианта (с 1); Enter — вариант по умолчанию."""
        while True:
            line = input_fn(f"{prompt} [{default}] ").strip()
            if not line:
                return default
            if line.isdigit() and 1 <= int(line) <= len(options):
                return int(line)
            output_fn(f"  Enter a number from 1 to {len(options)}")

    def yes(prompt: str, default: bool) -> bool:
        line = input_fn(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not line else line in ("y", "yes")

    output_fn("«Герои» — new game. Enter picks the option in brackets.\n")
    while True:
        line = input_fn("How many players (2–4)? [3] ").strip() or "3"
        if line in ("2", "3", "4"):
            n = int(line)
            break
        output_fn("  Enter 2, 3 or 4")
    order = list(PLAYER_TYPES)
    menu = ", ".join(f"{i} — {TYPE_NAMES[k]}" for i, k in enumerate(order, 1))
    fids = [*FACTIONS, RANDOM]
    faction_menu = ", ".join(f"{i} — {faction_names().get(f, f)}" for i, f in enumerate(fids, 1))
    known = [p.name for p in profiles.all_profiles()]
    if known:
        output_fn("Player profiles on this computer: " + ", ".join(known)
                  + ". A human with one of these names gets the game in that profile's statistics.")
    kinds, names, factions = [], [], []
    for i in range(n):
        kind = order[ask(f"Player {i + 1} — who plays? ({menu})", order, 1 if i == 0 else 3) - 1]
        default = default_names(kinds + [kind])[-1]
        hint = " (they join the game with it)" if kind == "human" else ""
        while True:
            name = input_fn(f"  Name{hint} [{default}] ").strip() or default
            if name.casefold() not in (other.casefold() for other in names):
                break
            output_fn("  That name is taken")
        while True:
            faction = fids[ask(f"  Faction ({faction_menu})", fids, len(fids)) - 1]
            if faction == RANDOM or faction not in factions:
                break
            output_fn("  That faction is taken: each faction can be played by one player only")
        kinds.append(kind)
        names.append(name)
        factions.append(faction)
    model = args.model or DEFAULT_MODEL
    if "claude" in kinds:
        if not args.model:
            models = list(MODELS)
            output_fn("Claude model: " + "; ".join(f"{i} — {MODELS[m]}" for i, m in enumerate(models, 1)))
            model = models[ask("Which model?", models, 1) - 1]
        if not args.effort:
            choice = ask("Claude's effort: " + ", ".join(f"{i} — {e}" for i, e in enumerate(EFFORTS, 1)) + "?",
                         list(EFFORTS), EFFORTS.index(DEFAULT_EFFORT) + 1)
            args.effort = EFFORTS[choice - 1]
        if args.show_thoughts is None and "human" in kinds:
            args.show_thoughts = yes("Print Claude's plans in this terminal (a hint if you play against it)?", False)
    if "human" in kinds:
        while True:
            line = input_fn(f"Server port [{args.port}] ").strip()
            if not line or line.isdigit():
                args.port = int(line or args.port)
                break
            output_fn("  The port is a number")
        args.password = input_fn("Game password (Enter — none) " + (f"[{args.password}] " if args.password else "")).strip() \
            or args.password
    output_fn("Who goes first? " + ", ".join(f"{i} — {name}" for i, name in enumerate(names, 1))
              + f", {n + 1} — random (the seats are shuffled too)")
    choice = ask("First player:", names + ["random"], n + 1)
    first = choice - 1
    if choice == n + 1:
        kinds, names, factions = shuffle_seats(kinds, names, factions)
        first = 0

    output_fn("\nPlayers (clockwise):")
    for i, (kind, name, faction) in enumerate(zip(kinds, names, factions)):
        mark = " — goes first" if i == first else ""
        side = "random faction" if faction == RANDOM else faction_names()[faction]
        output_fn(f"  {i + 1}. {name} ({TYPE_NAMES[kind]}, {side}){mark}")
    if "claude" in kinds:
        output_fn(f"Claude players: {MODELS[model] if model in MODELS else model}"
                  + (f", effort {args.effort}." if args.effort else "."))
    if "human" in kinds:
        output_fn("After the start the game opens for the browsers and waits until every human joins.")
    if not yes("Start?", True):
        return None
    return kinds, names, factions, first, model


def default_names(kinds: list[str]) -> list[str]:
    names = []
    counters = dict.fromkeys(PLAYER_TYPES, 0)
    base = {"human": "Player", "claude": "Claude", "bot": "Bot"}
    for kind in kinds:
        kind = "human" if kind == "remote" else kind
        counters[kind] += 1
        names.append(f"{base[kind]} {counters[kind]}")
    return names


if __name__ == "__main__":
    sys.exit(main())
