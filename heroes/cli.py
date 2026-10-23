"""Запуск партии из терминала.

  python -m heroes                                  # мастер настройки: число игроков, кто за кем
  python -m heroes --players human,bot,bot          # без мастера: вы против двух случайных ботов
  python -m heroes --players human,claude,claude    # вы против двух Claude
  python -m heroes --players human,bot --factions orcs,random   # вы за орков, бот — за случайную фракцию
  python -m heroes --players claude,claude --show-thoughts   # Claude против Claude, с замыслами
  python -m heroes --players human,remote --names Аня,Боря   # Аня здесь, Боря — с другого компьютера
  python -m heroes --join 192.168.1.5 --name Боря   # подключиться к чужой партии
  python -m heroes --load saves/<файл>.json         # продолжить сохранённую партию
  python -m heroes --gui                            # графический интерфейс (heroes/gui/)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from typing import Callable

from . import profiles, savegame
from .engine import scoring
from .engine.data import DATA_DIR
from .engine.game import Game
from .net.client import run_client
from .net.protocol import DEFAULT_PORT
from .net.server import Server, local_addresses
from .players.base import Player
from .players.claude_cli import DEFAULT_MODEL, ClaudePlayer
from .players.console import ConsolePlayer, QuitGame, Terminal
from .players.random_bot import RandomBot
from .players.remote import RemotePlayer
from .protocol import journal
from .protocol import render_text as R
from .protocol.snapshot import snapshot, static_data, sync_message

PLAYER_TYPES = ("human", "claude", "bot", "remote")
TYPE_NAMES = {"human": "human", "claude": "Claude", "bot": "random bot", "remote": "human over the network"}
MODELS = {"claude-opus-5-5": "Opus 5.5 — strongest, ≈$3–4 per game per player",
          "claude-sonnet-5-5": "Sonnet 5.5 — faster and cheaper",
          "claude-haiku-5-5": "Haiku 5.5 — very cheap, weaker"}
AUTOSAVE = "saves"   # папка автосохранений
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


def print_thought(name: str, kind: str, comment: str) -> None:
    print(f"💭 {name} [{kind}]: {comment}")


class Network:
    """Сервер для сетевых игроков; создаётся, только если за столом есть «человек по сети»."""

    def __init__(self, port: int, password: str | None, log_fn: Callable[[str], None] = print):
        self.port = port
        self.password = password
        self.log_fn = log_fn
        self.server: Server | None = None

    def seat(self, name: str):
        if self.server is None:
            self.server = Server(self.port, self.password, self.log_fn)
        return self.server.seat(name)


def make_players(meta: dict, names: list[str], show_thoughts: bool | None = None,
                 network: Network | None = None, make_human: Callable[[], Player] | None = None,
                 on_thought: Callable[[str, str, str], None] = print_thought) -> list[Player]:
    """show_thoughts: None — показывать замыслы Claude, только если за столом нет людей.
    make_human — кем сажать местного человека (по умолчанию — консольный игрок)."""
    seed = meta.get("seed")
    claude = meta.get("claude", {})
    sessions = meta.get("claude_sessions", {})
    if show_thoughts is None:
        show_thoughts = not {"human", "remote"} & set(meta["players"])
    players: list[Player] = []
    terminal = Terminal()  # все местные люди сидят за одним компьютером
    for i, kind in enumerate(meta["players"]):
        if kind == "human":
            players.append(make_human() if make_human else ConsolePlayer(terminal=terminal))
        elif kind == "remote":
            if network is None:
                raise SystemExit("Network players need a server")
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
    ap = argparse.ArgumentParser(prog="python -m heroes", description="«Герои» — a game in the terminal")
    ap.add_argument("--players",
                    help="comma-separated player types, clockwise: human, claude, bot, remote (2–4 players); "
                         "without it the game is set up interactively")
    ap.add_argument("--names", help="comma-separated player names")
    ap.add_argument("--factions", help=f"comma-separated factions in seat order: {', '.join(FACTIONS)} or {RANDOM} "
                                       f"(default: {RANDOM} for everyone; a faction can be played by one player only)")
    ap.add_argument("--first", type=int, default=0, help="first player's number (from 0), with --players")
    ap.add_argument("--seed", type=int, help="seed for random bots")
    ap.add_argument("--load", metavar="FILE", help="continue a saved game")
    ap.add_argument("--model", help=f"model for Claude players (default {DEFAULT_MODEL})")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                    help="Claude's thinking effort (default: the model's default)")
    ap.add_argument("--tools", action=argparse.BooleanOptionalAction, default=None,
                    help="give Claude tools: its own scripts and game simulation in a bwrap sandbox "
                         "(default: when bwrap is installed, i.e. on Linux)")
    ap.add_argument("--show-thoughts", action=argparse.BooleanOptionalAction, default=None,
                    help="print Claude's plans (default: only when no humans are playing)")
    ap.add_argument("--gui", action="store_true", help="open the graphical interface")
    ap.add_argument("--autosave", default=AUTOSAVE, help=f"folder for the autosave made at the start of each round (default {AUTOSAVE}; '' disables)")
    net = ap.add_argument_group("network play")
    net.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"game server port (default {DEFAULT_PORT})")
    net.add_argument("--password", help="game password (host sets it, guests enter it)")
    net.add_argument("--join", metavar="ADDRESS[:PORT]", help="join a game hosted on another computer")
    net.add_argument("--name", help="with --join: your name (seat at the table)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.gui:
        from .gui.app import run_app
        return run_app(args)
    if args.join:
        return run_client(args.join, args.name, args.password)
    autosave = args.autosave or None
    network = Network(args.port, args.password)
    if args.load:
        game = savegame.load(args.load, lambda meta, names: make_players(meta, names, args.show_thoughts, network),
                             autosave=autosave)
    else:
        if args.players:
            kinds = [k.strip() for k in args.players.split(",")]
            names = [n.strip() for n in args.names.split(",")] if args.names else default_names(kinds)
            factions = [f.strip() for f in args.factions.split(",")] if args.factions else [RANDOM] * len(kinds)
            first, model = args.first, args.model or DEFAULT_MODEL
        else:
            try:
                kinds, names, factions, first, model = setup_wizard(args.model)
            except (EOFError, KeyboardInterrupt, QuitGame):
                print("\nGame not started.")
                return 0
        if len(names) != len(kinds):
            raise SystemExit("The number of names must match the number of players")
        if not 2 <= len(kinds) <= 4:
            raise SystemExit("«Герои» is played by 2 to 4 players")
        if len(set(names)) != len(names):
            raise SystemExit("Player names must be distinct")
        if len(factions) != len(kinds):
            raise SystemExit("The number of factions must match the number of players")
        try:
            factions = resolve_factions(factions)
        except ValueError as e:
            raise SystemExit(str(e)) from None
        meta = {"players": kinds, "seed": args.seed}
        if "claude" in kinds:
            meta["claude"] = {"model": model, "effort": args.effort, "tools": args.tools}
        game = Game(make_players(meta, names, args.show_thoughts, network), names=names, factions=factions,
                    first_player=first, meta=meta, autosave=autosave)
    for seat, profile_id in profiles.seat_profiles(game.meta, [p.name for p in game.state.players]).items():
        if avatar := profiles.avatar_b64(profiles.get(profile_id)):   # аватарки хозяина — для окон гостей
            game.avatars[seat] = avatar
    server = network.server
    try:
        if server and not open_table(game, server, args):
            return 0
        local = [p for p in game.players if type(p) is ConsolePlayer]
        if not local:  # хозяин без своего места — смотрит партию как зритель
            game.listeners.append(lambda message: None if game.replaying else print(message))
        try:
            scores = game.run()
        except QuitGame:
            print("\nGame interrupted.")
            report_claude_usage(game)
            if server:
                server.broadcast_end("The game was interrupted.")
            return 0
        except KeyboardInterrupt:
            path = savegame.save(game, savegame.default_path(game, "interrupted"))
            print(f"\nInterrupted. Game saved: {path}")
            if server:
                server.broadcast_end("The host interrupted the game. It is saved and can be continued.")
            return 130
        if local:
            local[0].show_new_events()
        result = R.final_scores(game, scores, scoring.winners(game, scores))
        print()
        print(result)
        record_profiles(game)
        if server:
            server.broadcast_end("\n" + result)
        record_lessons(game)
        report_claude_usage(game)
        return 0
    finally:
        if server:
            server.close()


def open_table(game: Game, server: Server, args: argparse.Namespace, out: Callable[[str], None] = print) -> bool:
    """Запустить сервер и дождаться всех сетевых игроков. False — хозяин передумал."""
    for p, player in enumerate(game.players):  # до старта сервера: первый же гость должен получить партию
        if isinstance(player, RemotePlayer):
            player.bind(game, p)
    try:
        server.start()
    except OSError as e:
        raise SystemExit(f"Could not open port {server.port}: {e}. Choose another one with --port") from None
    password = f" --password {args.password}" if args.password else ""
    port = "" if server.port == DEFAULT_PORT else f":{server.port}"
    out("\nThe game is open for network players. They should run:")
    for address in local_addresses():
        out(f"  python -m heroes --join {address}{port}{password} --name <name>")
    out(f"  (or open the graphical client: python -m heroes --gui → Join, address {local_addresses()[0]}{port})")
    out("Seats: " + ", ".join(server.seats) + ". Over the internet you need to forward port "
        f"{server.port} or share a VPN network (e.g. Tailscale).")
    try:
        server.wait_all()
    except KeyboardInterrupt:
        out("\nGame not started.")
        return False
    out("Everyone is here — let's start!")
    for p, player in enumerate(game.players):   # пока ждали, подключились другие — с их аватарками
        if isinstance(player, RemotePlayer) and player.seat.mode == "gui":
            player.seat.send(sync_message(game, p))
    return True


def record_profiles(game: Game, out: Callable[[str], None] = print) -> None:
    """Доигранную партию — в статистику профилей (их заводят в окне): люди за этим компьютером,
    чьё имя совпадает с именем профиля."""
    seats = profiles.seat_profiles(game.meta, [p.name for p in game.state.players])
    if not seats:
        return
    try:
        names = profiles.record_game(static_data(game), snapshot(game), seats)
    except OSError as e:
        out(f"Could not record the game in the profiles: {e}")
        return
    if names:
        out("Recorded in the profile statistics: " + ", ".join(names))


def report_claude_usage(game: Game, out: Callable[[str], None] = print) -> None:
    for p, player in enumerate(game.players):
        if isinstance(player, ClaudePlayer) and player.calls:
            out(f"{game.state.players[p].name}: {player.calls} Claude calls, ≈ ${player.cost_usd:.2f} "
                f"(transcript: logs/claude/{player.session_id}.jsonl)")


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
    out("Claude players are reviewing the game for their strategy notes…")
    with ThreadPoolExecutor(len(claudes)) as pool:
        results = list(pool.map(lambda c: c[1].reflect(game, c[0]), claudes))
    for (p, _), result in zip(claudes, results):
        if result:
            review, outcome = result
            name = game.state.players[p].name
            if review:
                on_thought(name, "game_review", review)
            out(f"{name}: {outcome}")


def setup_wizard(model: str | None, input_fn: Callable[[str], str] = input,
                 output_fn: Callable[[str], None] = print) -> tuple[list[str], list[str], list[str], int, str]:
    """Диалог перед партией: число игроков, кто играет на каждом месте, имена, фракции, модель, первый игрок.
    Фракция может остаться «случайной» — её выбирает resolve_factions."""

    def ask(prompt: str, options: list[str], default: int) -> int:
        """Номер варианта (с 1); Enter — вариант по умолчанию."""
        while True:
            line = input_fn(f"{prompt} [{default}] ").strip()
            if not line:
                return default
            if line.isdigit() and 1 <= int(line) <= len(options):
                return int(line)
            output_fn(f"  Enter a number from 1 to {len(options)}")

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
    kinds, names, factions = [], [], []
    for i in range(n):
        kind = order[ask(f"Player {i + 1} — who plays? ({menu})", order, 1 if i == 0 else 2) - 1]
        default = default_names(kinds + [kind])[-1]
        hint = " (the guest joins with it)" if kind == "remote" else ""
        while True:
            name = input_fn(f"  Name{hint} [{default}] ").strip() or default
            if name not in names:
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
    if "claude" in kinds and not model:
        models = list(MODELS)
        output_fn("Claude model: " + "; ".join(f"{i} — {MODELS[m]}" for i, m in enumerate(models, 1)))
        model = models[ask("Which model?", models, 1) - 1]
    model = model or DEFAULT_MODEL
    output_fn("Who goes first? " + ", ".join(f"{i} — {name}" for i, name in enumerate(names, 1))
              + f", {n + 1} — random")
    choice = ask("First player:", names + ["random"], n + 1)
    first = random.randrange(n) if choice == n + 1 else choice - 1

    output_fn("\nPlayers (clockwise):")
    for i, (kind, name, faction) in enumerate(zip(kinds, names, factions)):
        mark = " — goes first" if i == first else ""
        side = "random faction" if faction == RANDOM else faction_names()[faction]
        output_fn(f"  {i + 1}. {name} ({TYPE_NAMES[kind]}, {side}){mark}")
    if "claude" in kinds:
        output_fn(f"Claude players: {MODELS[model] if model in MODELS else model}.")
    if kinds.count("human") > 1:
        output_fn("Humans share one computer: the screen is hidden before each turn, secret choices stay secret.")
    if "remote" in kinds:
        output_fn("After the start the game opens for network players and waits until everyone joins.")
    if input_fn("Start? [Y/n] ").strip().lower() in ("n", "no"):
        raise QuitGame
    return kinds, names, factions, first, model


def default_names(kinds: list[str]) -> list[str]:
    names = []
    counters = dict.fromkeys(PLAYER_TYPES, 0)
    base = {"human": "Player", "claude": "Claude", "bot": "Bot", "remote": "Guest"}
    for kind in kinds:
        counters[kind] += 1
        names.append(f"{base[kind]} {counters[kind]}")
    return names


if __name__ == "__main__":
    sys.exit(main())
