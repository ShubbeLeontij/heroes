"""Человек за терминалом.

На каждый тип решения — свой короткий диалог: номера вариантов, количества, y/n.
Ответ проверяется движковой функцией validate ещё до отправки, поэтому человек
никогда не получает «вариант по умолчанию» из-за опечатки.

В любом вопросе доступны команды, начинающиеся с «/»: /board, /map, /opponents,
/log, /save [файл], /quit, /help.
"""
from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any, Callable

from .. import savegame
from ..engine import actions, combat, effects
from ..engine.effects import Battle
from ..engine.state import ACTION_NAMES, ACTIONS, TERRAIN_NAMES
from ..protocol import render_text as R
from .base import Player

if TYPE_CHECKING:
    from ..engine.decisions import Decision
    from ..engine.game import Game, GameView

HELP = """Commands (at any prompt):
  /board (/b)       — your board         /map (/m)    — the map
  /opponents (/o)   — opponents          /log [N]     — last N events
  /save [file]      — save the game      /quit        — leave the game
Resources: w — wood, i — iron, g — gold, a — any. Pick options by number or name."""

RESOURCE_KEYS = {"w": "wood", "i": "iron", "g": "gold"}
ACTION_KEYS = {**RESOURCE_KEYS, "m": "move", "b": "build"}
STANCE_LABELS = {"fight": "fight", "claim": "claim the fortress/artifact", "pass": "pass (yield)"}
# Тайные решения: при игре нескольких людей за одним терминалом экран после них очищается.
SECRET_KINDS = {"destination", "commit_army", "hydra_destination"}
NOTE_LABELS = {"open": "start a new pair (summon the hydra later)",
               "close": "complete a started pair and summon the hydra now"}


def is_secret(decision: Decision) -> bool:
    """Модификация Лавки бронника тайная только в сражении между игроками."""
    return decision.kind in SECRET_KINDS or decision.kind == "battle_upgrade" and decision.data.get("secret", False)


class Terminal:
    """Общий терминал для нескольких людей (hotseat): кто сейчас за экраном."""

    def __init__(self):
        self.humans = 0
        self.owner: ConsolePlayer | None = None

    @property
    def hotseat(self) -> bool:
        return self.humans > 1


def reward(vp: int, loot: dict[str, int]) -> str:
    return f"{vp} VP" + (f" + {R.res(loot)}" if loot else "")


class QuitGame(Exception):
    pass


class InputError(ValueError):
    pass


class ConsolePlayer(Player):
    def __init__(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print,
                 color: bool | None = None, terminal: Terminal | None = None):
        """terminal — общий для всех людей за одним компьютером; без него игрок за экраном один."""
        self.terminal = terminal or Terminal()
        self.terminal.humans += 1
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR") if color is None else color
        self.seen_log: int | None = None
        self.game: Game | None = None
        self.me = 0

    # --- вывод/ввод ---------------------------------------------------------------------

    def out(self, text: str = "") -> None:
        self.output_fn(text)

    def styled(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def header(self, text: str) -> None:
        who = f"{self.name}: " if self.terminal.hotseat else ""
        self.out(self.styled(f"\n── {who}{text} ──", "1;36"))

    def error(self, text: str) -> None:
        self.out(self.styled(f"✗ {text}", "31"))

    def read(self, prompt: str) -> str:
        """Строка ввода; команды «/…» выполняются здесь же и не возвращаются."""
        while True:
            try:
                line = self.input_fn(self.styled(prompt, "1") + " ").strip()
            except EOFError:
                raise QuitGame from None
            if not line.startswith("/"):
                return line
            self.command(line[1:].split())

    def command(self, args: list[str]) -> None:
        game, me = self.game, self.me
        cmd = args[0].lower() if args else ""
        if cmd in ("b", "board"):
            self.out(R.board(game, me))
        elif cmd in ("m", "map"):
            self.out(R.game_map(game, me))
        elif cmd in ("o", "opponents"):
            self.out(R.opponents(game, me))
        elif cmd in ("l", "log"):
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
            self.out("\n".join(game.state.log[-n:]))
        elif cmd in ("s", "save"):
            path = savegame.save(game, args[1] if len(args) > 1 else savegame.default_path(game))
            self.out(f"Saved: {path}. To load: python -m heroes --load {path}")
        elif cmd in ("q", "quit", "exit"):
            if self.yes_no("Save the game before quitting?", True):
                self.command(["save"])
            raise QuitGame
        else:
            self.out(HELP)

    def yes_no(self, prompt: str, default: bool | None = None) -> bool:
        hint = {True: "[Y/n]", False: "[y/N]", None: "[y/n]"}[default]
        while True:
            line = self.read(f"{prompt} {hint}").lower()
            if not line and default is not None:
                return default
            if line in ("y", "yes", "1"):
                return True
            if line in ("n", "no", "0"):
                return False
            self.error("Answer y or n")

    def pick(self, prompt: str, options: list[Any], labels: list[str], zero: str | None = None,
             empty: Any = ...) -> Any:
        """Выбор из списка по номеру или названию. zero — подпись варианта «0» (возвращает None)."""
        if len(options) == 1 and not zero and empty is ...:
            self.out(f"{prompt} {labels[0]} — the only option.")
            return options[0]
        for i, label in enumerate(labels, 1):
            self.out(f"  {i}. {label}")
        if zero:
            self.out(f"  0. {zero}")
        while True:
            line = self.read(prompt)
            if not line and empty is not ...:
                return empty
            if line.isdigit():
                i = int(line)
                if i == 0 and zero:
                    return None
                if 1 <= i <= len(options):
                    return options[i - 1]
            matches = [o for o in options if str(o).lower().startswith(line.lower())] if line else []
            exact = [o for o in options if str(o).lower() == line.lower()]
            if exact or len(matches) == 1:
                return (exact or matches)[0]
            self.error("No such option — enter a number from the list")

    def numbers(self, line: str, count: int) -> list[int]:
        parts = line.replace(",", " ").split()
        if len(parts) != count or not all(p.isdigit() for p in parts):
            raise InputError(f"Enter {count} non-negative numbers separated by spaces")
        return [int(p) for p in parts]

    def validated(self, decision: Decision, build: Callable[[], Any]) -> Any:
        """Повторять диалог build(), пока движок не примет ответ."""
        while True:
            try:
                answer = build()
            except ValueError as e:  # InputError и ошибки разбора JSON
                self.error(str(e))
                continue
            error = decision.validate(answer)
            if error is None:
                return answer
            self.error(error)

    # --- точка входа ----------------------------------------------------------------------

    def decide(self, decision: Decision, view: GameView) -> Any:
        self.game, self.me = view.game, view.player
        if self.terminal.hotseat and self.terminal.owner is not self:
            self.hand_over()
        self.show_new_events()
        handler = getattr(self, f"_{decision.kind}", None)
        if handler is None:
            self.out(decision.text)
            answer = self.validated(decision, lambda: json.loads(self.read("JSON>")))
        else:
            answer = handler(decision)
        if self.terminal.hotseat and is_secret(decision):
            self.clear_screen()
            self.out(f"{self.name}: choice made and hidden.")
            self.terminal.owner = None
        return answer

    @property
    def name(self) -> str:
        return self.game.state.players[self.me].name

    def clear_screen(self) -> None:
        self.out("\033[2J\033[H" if self.color else "\n" * 60)

    def hand_over(self) -> None:
        """Экран передачи хода: предыдущий игрок не должен видеть чужие решения, и наоборот."""
        self.clear_screen()
        self.out(self.styled(f"{self.name}'s turn.", "1;35") + " Everyone else — look away from the screen.")
        self.read("Press Enter when ready.")
        self.terminal.owner = self

    def on_invalid(self, decision, answer, error):
        self.error(error)

    def show_new_events(self) -> None:
        log = self.game.state.log
        if self.seen_log is None:  # первое появление (в т. ч. после загрузки) — только хвост
            self.seen_log = max(0, len(log) - 25)
            if self.seen_log:
                self.out("…")
        for line in log[self.seen_log:]:
            self.out(self.styled(line, "1;33") if line.startswith("===") else line)
        self.seen_log = len(log)

    def army(self, units: dict, battle: Battle | Callable[[dict[str, int]], Battle], need: int | None = None,
             allow_empty: bool = True) -> tuple[dict[str, int], int | None]:
        """Ввод заявки войск: числа по порядку видов, «all» или пусто (никого); с Лавкой бронника — и сколько
        заявленных единиц модифицировать. Сила — по правилам движка (краки, механики, прибавки к силе).
        battle — сражение или функция заявка -> сражение (отдельная гидра наг дерётся в своём регионе).
        Возвращает заявку и модификацию (None — модифицировать нечего)."""
        order = list(units)
        names = ", ".join(f"{units[u]['name']} (have {units[u]['count']}, strength {units[u]['strength']})" for u in order)
        self.out(f"Units: {names}")
        while True:
            line = self.read(f"How many to commit ({', '.join(units[u]['name'] for u in order)}; \"all\""
                             + (", Enter — none" if allow_empty else "") + ")?").lower()
            if line == "all":
                army = {u: units[u]["count"] for u in order}
            elif not line and allow_empty:
                army = {}
            else:
                try:
                    counts = self.numbers(line, len(order))
                except InputError as e:
                    self.error(str(e))
                    continue
                army = {u: n for u, n in zip(order, counts) if n}
            if any(army.get(u, 0) > units[u]["count"] for u in order):
                self.error("You cannot commit more than you have")
                continue
            where = battle(army) if callable(battle) else battle
            most = combat.max_upgrades(self.game, self.me, army, where)
            upgrade = None
            if most:
                cost = effects.effects_of(self.game, self.me, "pay_to_upgrade_units")[0]["cost_per_unit"]
                gains = effects.upgrade_gains(self.game, self.me, army, where)[:most]
                upgrade = self.count(f"{effects.source_name(self.game, self.me, 'pay_to_upgrade_units')}: upgrade how "
                                     f"many committed units for this battle ({R.res(cost)} each; gains "
                                     f"+{', +'.join(map(str, gains))})?", most)
            total = combat.army_strength(self.game, self.me, army, where, upgrade or 0)
            verdict = ""
            if need is not None:
                verdict = " — enough to win" if total > need else f" — NOT enough: need more than {need}"
            if self.yes_no(f"Committed strength: {total}{verdict}. Confirm?", True):
                return army, upgrade

    @staticmethod
    def commitment(army: dict[str, int], upgrade: int | None) -> Any:
        """Ответ движку: заявка, а с модификацией Лавки бронника — {"army": …, "upgrade": n}."""
        return army if upgrade is None else {"army": army, "upgrade": upgrade}

    def count(self, prompt: str, most: int) -> int:
        """Число от 0 до most; Enter — 0."""
        while True:
            line = self.read(f"{prompt} (0–{most}) [0]")
            if not line:
                return 0
            if line.isdigit() and int(line) <= most:
                return int(line)
            self.error(f"Enter a number from 0 to {most}")

    # --- решения ---------------------------------------------------------------------------

    def _choose_start(self, d):
        self.header("Choose your starting region")
        regions = self.game.map.regions
        labels = [f"{r} [{TERRAIN_NAMES[regions[r].terrain]}] resources: {R.res(regions[r].resources)}"
                  for r in d.data["options"]]
        return self.pick("Starting region:", d.data["options"], labels)

    def _action_order(self, d):
        self.header(f"Round {self.game.state.round}: you are the first player — choose 4 actions")
        keys = {v: k for k, v in ACTION_KEYS.items()}
        for i, a in enumerate(ACTIONS, 1):
            self.out(f"  {i} ({keys[a]}). {ACTION_NAMES[a]}")
        self.out("Order = action level: the first is performed at level 1, the fourth at level 4.")

        def build():
            tokens = self.read("4 actions in order (numbers or letters, e.g. \"w g b m\"):").lower().replace(",", " ").split()
            order = []
            for t in tokens:
                if t.isdigit() and 1 <= int(t) <= len(ACTIONS):
                    order.append(ACTIONS[int(t) - 1])
                elif t in ACTION_KEYS:
                    order.append(ACTION_KEYS[t])
                else:
                    raise InputError(f"Did not understand {t!r}")
            return order
        return self.validated(d, build)

    def _distribute_resources(self, d):
        amount = d.data["amount"]
        self.out(f"{d.data['reason']}: split {amount} between wood, iron and gold.")

        def build():
            line = self.read(f"Wood iron gold (sum {amount}), or one letter w/i/g — all of it there:").lower()
            if line in RESOURCE_KEYS:
                return {RESOURCE_KEYS[line]: amount}
            return dict(zip(("wood", "iron", "gold"), self.numbers(line, 3)))
        return self.validated(d, build)

    def _build_recruit(self, d):
        """Один шаг за вопрос: движок применяет его и спрашивает снова, пока не выбрано «закончить»."""
        game, me = self.game, self.me
        data = d.data
        self.header(f"Build & hire (level {data['level']})")
        self.out(f"Stock: {R.holdings_line(game, me)}")
        if data["steps"]:
            self.out("Done in this action: " + "; ".join(actions.step_text(game, me, s) for s in data["steps"]))
        if data["rejected"]:
            self.error(f"Not performed: {data['rejected']['error']}")
        f = data["foundation"]
        if f and not data["built"]:
            self.out(f"You are in {f['region']}, which has a foundation (current level: {f['level']}): "
                     "a higher-level building will be placed on it automatically.")
        menu = {}
        if data["options"]:
            menu["build"] = "build a building (one per action)"
        if data["hire"]:
            menu["hire"] = "hire units"
        if data["upgrade"] and data["upgrade"]["units"]:
            menu["upgrade"] = "upgrade units for rage"
        if data["raise"]:
            menu["raise"] = "raise action levels (Дом вождей)"
        if data["science"]:
            menu["science"] = f"buy {data['science']['value']} science for {R.res(data['science']['cost'])} (Библиотека)"

        def build():
            what = self.pick("What next?", list(menu), list(menu.values()), zero="finish building")
            if what is None:
                return ["done"]
            step = {"build": self.build_step, "hire": self.hire_step, "upgrade": self.upgrade_step,
                    "raise": lambda d: self.raise_step(d.data["raise"]), "science": lambda d: True}[what](d)
            if not step:
                raise InputError("Nothing chosen")
            return [{what: step}]
        return self.validated(d, build)

    def build_step(self, d):
        data, f = d.data, d.data["foundation"]
        labels = [f"{o['name']} — level {o['level']}, {o['vp']} VP"
                  + (f" (+{o['level']} VP on the foundation)" if f and o["level"] > f["level"] else "")
                  + f", cost {R.res(o['cost'])}. {o['text']}"
                  for o in data["options"].values()]
        return self.pick("What to build?", list(data["options"]), labels, zero="back")

    def hire_step(self, d):
        game, me = self.game, self.me
        left = dict(d.data["resources"])
        hire = {}
        for unit, h in d.data["hire"].items():
            cost = h["cost"]
            most = min(left.get(k, 0) // v for k, v in cost.items()) if cost else 0
            if most == 0:
                continue
            n = self.count(f"Hire {h['name']} (cost {R.res(cost)}, strength {effects.unit_strength(game, me, unit)})", most)
            if n:
                hire[unit] = n
                for k, v in cost.items():
                    left[k] -= v * n
        return hire

    def upgrade_step(self, d):
        units, answer = dict(d.data["upgrade"]["units"]), {}
        names = self.game.faction_of(self.me).units
        for key, path in d.data["upgrade"]["paths"].items():
            if not units.get(path["from"]):
                continue
            n = self.count(f"{names[path['from']].name} → {names[path['to']].name} for {R.res(path['cost'])} each",
                           units[path["from"]])
            if n:
                answer[key] = n
                units[path["from"]] -= n
                units[path["to"]] = units.get(path["to"], 0) + n
        return answer

    def raise_step(self, offer):
        left, answer = offer["max"], {}
        for action, level in offer["options"].items():
            if left == 0:
                break
            x = self.count(f"Raise {ACTION_NAMES[action]} (level {level}) by", left)
            if x:
                answer[action] = x
                left -= x
        return answer

    def _reserve_object(self, d):
        if not self.yes_no(f"Башня иллюзий: pay {R.res(d.data['cost'])} to reserve an object?", False):
            return None
        options = sorted(d.data["options"])
        labels = [f"{r}: {R.object_text(self.game, self.me, r)}" for r in options]
        return self.pick("Which object?", options, labels, zero="changed my mind")

    def _destination(self, d):
        game, me = self.game, self.me
        player = game.state.players[me]
        mp = d.data["mp"]
        self.header(f"Movement: {mp} MP, you are in {player.region} (secret choice)")
        others = {pl.region: pl.name for q, pl in enumerate(game.state.players) if q != me}
        options = sorted(d.data["options"], key=lambda r: (d.data["options"][r]["cost"], r))
        labels = []
        for r in options:
            region = game.map.regions[r]
            info = d.data["options"][r]
            new = "" if r in player.visited else f", new: {R.res(region.resources)}"
            who = f", {others[r]} is there now" if r in others else ""
            stay = " (stay)" if r == player.region else ""
            labels.append(f"{r}{stay} [{R.terrain_text(game, r)}] {R.object_text(game, me, r)}"
                          f" — cost {info['cost']}, {info['left']} MP left{new}{who}")
        return self.pick("Where to (Enter — stay)?", options, labels, empty=player.region)

    def _region_stance(self, d):
        self.header(f"Encounter in {d.data['region']}")
        self.out(d.text)
        options = d.data["options"]
        return self.pick("Your decision:", options, [STANCE_LABELS[o] for o in options])

    def _commit_army(self, d):
        self.header(f"Battle in {d.data['region']} — secret commitment, all committed units die")
        battle = Battle("pvp", d.data["region"])
        for q, units in d.data["opponents"].items():
            desc = ", ".join(f"{u['name']} ×{u['count']} (strength {u['strength']})" for u in units.values()) or "no units"
            self.out(f"  {R.name_of(self.game, int(q))}: {desc}; at most {combat.max_strength(self.game, int(q), battle)}")
        for o, s in d.data.get("hydras", {}).items():
            self.out(f"  {R.name_of(self.game, int(o))}'s hydra: strength {s} (rewards are lost whatever the outcome)")
        return self.validated(d, lambda: self.commitment(*self.army(d.data["units"], battle)))

    def _choose_reward(self, d):
        self.header("You won — choose a reward")
        opts = d.data["options"]
        kinds, labels = [], []
        if "monster" in opts:
            kinds.append("monster")
            labels.append(f"the circled monster's loot: {R.res(opts['monster'])}")
        if "resources" in opts:
            kinds.append("resources")
            labels.append(f"take up to {opts['resources']['amount']} resources from the losers")
        if "artifact" in opts:
            kinds.append("artifact")
            labels.append("take an artifact: " + "; ".join(
                f"{R.name_of(self.game, int(q))} — {', '.join(a)}" for q, a in opts["artifact"].items()))

        def build():
            kind = self.pick("Reward:", kinds, labels)
            if kind == "monster":
                return {"type": "monster"}
            if kind == "artifact":
                pairs = [(q, a) for q, arts in opts["artifact"].items() for a in arts]
                q, a = self.pick("Which artifact?", pairs, [f"{a} (held by {R.name_of(self.game, int(q))})" for q, a in pairs])
                return {"type": "artifact", "from": q, "artifact": a}
            left = opts["resources"]["amount"]
            take = {}
            for q, pool in opts["resources"]["from"].items():
                if left == 0:
                    break
                line = self.read(f"From {R.name_of(self.game, int(q))} ({R.stock(pool)}) take wood iron gold "
                                 f"(up to {left} left, Enter — nothing):")
                if not line:
                    continue
                got = dict(zip(("wood", "iron", "gold"), self.numbers(line, 3)))
                take[q] = got
                left -= sum(got.values())
            return {"type": "resources", "take": take}
        return self.validated(d, build)

    def _capture_fortress(self, d):
        self.header(f"Fortress in {d.data['region']}: strength {d.data['strength']}, "
                    f"reward {reward(d.data['vp'], d.data['loot'])}")
        battle = Battle("fortress", d.data["region"])
        total = combat.max_strength(self.game, self.me, battle)

        def build():   # заявку, которой не взять крепость, движок не примет: тогда вопрос начинается заново
            if not self.yes_no(f"Attack? (your maximum strength is {total})", total > d.data["strength"]):
                return None
            return self.commitment(*self.army(d.data["units"], battle, need=d.data["strength"], allow_empty=False))
        return self.validated(d, build)

    def _fight_monster(self, d):
        self.header("Monster fight")
        game, me = self.game, self.me
        units = {u: info["count"] for u, info in d.data["units"].items()}
        alone = combat.lone_hydra(game, me)
        hydra = effects.hydra_unit(game, me) if alone else None
        here = Battle("monster", game.state.players[me].region)
        armies = [{u: n for u, n in units.items() if u != hydra}] + ([{hydra: 1}] if alone else [])
        total = max(combat.max_strength(game, me, Battle("monster", alone) if hydra in a else here, a) for a in armies)

        def battle(army):
            return Battle("monster", alone) if army.get(hydra) else here
        options = sorted(d.data["candidates"], key=int)
        labels = [f"strength-{c['strength']} monster → {reward(c['vp'], c['loot'])}"
                  + ("" if c["strength"] < total else " (not enough strength)")
                  for c in (d.data["candidates"][i] for i in options)]
        self.out(f"Your maximum strength: {total}. Unspent MP let you skip monsters.")
        if alone:
            self.out(f"Your hydra is alone in {alone}: she can fight only alone (the monster's loot is lost, she dies).")

        def build():   # заявку, которой не победить, движок не примет: тогда выбор начинается заново
            choice = self.pick("Which one to fight?", options, labels, zero="don't fight", empty=None)
            if choice is None:
                return None
            need = d.data["candidates"][choice]["strength"]
            army, upgrade = self.army(d.data["units"], battle, need=need, allow_empty=False)
            return {"monster": int(choice), "army": army, **({"upgrade": upgrade} if upgrade is not None else {})}
        return self.validated(d, build)

    def _take_artifact(self, d):
        self.header(f"Artifact in {d.data['region']}")
        cond = d.data["condition"]
        price = f"cost {R.res(cond['cost'])}" if cond["condition"] == "pay" else "condition met, nothing to pay"
        return self.yes_no(f"{d.data['effect']} ({price}). Take it?", True)

    # --- особые решения фракций -------------------------------------------------------------

    def _ask_yes_no(self, d):
        self.header(d.text.split(":")[0])
        self.out(d.text)
        return self.yes_no("Your choice?", False)

    _make_lake = _exchange_on_gain = _ask_yes_no

    def _buy_move_points(self, d):
        self.header("Buy movement points")
        self.out(d.text)
        return self.count("How many MP to buy?", d.data["max"])

    def _battle_upgrade(self, d):
        self.header("Upgrade committed units" + (" (secret)" if d.data.get("secret") else ""))
        self.out(d.text)
        return self.count("How many units to upgrade?", d.data["max"])

    def _raise_action(self, d):
        self.header("Raise action levels")
        self.out(d.text)
        return self.validated(d, lambda: self.raise_step(d.data))

    def _curse_region(self, d):
        self.header("Curse a region")
        self.out(d.text)
        options = d.data["options"]
        labels = [f"{r}: {R.object_text(self.game, self.me, r)}" + "".join(
            f", {pl.name} is there" for q, pl in enumerate(self.game.state.players) if q != self.me and pl.region == r)
            for r in options]
        if d.data["optional"]:
            return self.pick("Which region?", options, labels, zero="curse nothing")
        return self.pick("Which region?", options, labels)

    def _circle_note(self, d):
        self.header("Circle a note")
        pairs = " ".join("●●" if n == 2 else "●○" if n == 1 else "○○" for n in d.data["notes"])
        self.out(f"Your notes (pairs): {pairs}")
        return self.pick("Which?", d.data["options"], [NOTE_LABELS[o] for o in d.data["options"]])

    def _summon_hydra(self, d):
        self.header("Summon the hydra into a lake region")
        game, me = self.game, self.me
        options = d.data["options"]
        labels = [f"{r}: {R.object_text(game, me, r)}" + "".join(
            f", {pl.name} is there" for pl in game.state.players if pl.region == r) for r in options]
        return self.pick("Where?", options, labels)

    def _hydra_destination(self, d):
        self.header(f"Your hydra moves from {d.data['from']} (secret choice)")
        game, me = self.game, self.me
        options = sorted(d.data["options"], key=lambda r: (d.data["options"][r], r))
        labels = [f"{r}" + (" (stay)" if not d.data["options"][r] else f" — {d.data['options'][r]} step(s)")
                  + f" [{R.terrain_text(game, r)}] {R.object_text(game, me, r)}" for r in options]
        return self.pick("Where to (Enter — stay)?", options, labels, empty=d.data["from"])

    def _avoid_battle(self, d):
        self.header(f"Avoid the battle in {d.data['region']}?")
        self.out(d.text)
        if not self.yes_no(f"Pay {d.data['amount']} resources to cancel the battle?", False):
            return None
        amount = d.data["amount"]

        def build():
            line = self.read(f"Wood iron gold to pay (sum {amount}):")
            return {k: v for k, v in zip(("wood", "iron", "gold"), self.numbers(line, 3)) if v}
        return self.validated(d, build)
