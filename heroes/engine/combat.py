"""Всё, что происходит после перемещения: сражения между игроками, захват крепостей,
бои с чудовищами и получение артефактов.

Порядок (правила + эррата): сражения между игроками → захват крепостей → чудовища → артефакты.
Артефакт можно получить только после всех сражений, включая бой с чудовищем.

Гидра наг, стоящая отдельно от наги, — самостоятельная боевая единица: она обязана сразиться
с игроками в своём регионе, а если их нет — может штурмовать крепость и драться с чудовищем.
Её победа даёт только ПО, ресурсы и награды теряются.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product
from typing import TYPE_CHECKING, Any

from .data import RESOURCES
from .decisions import Decision, check_army, check_choice, check_resource_split
from .economy import can_pay, fmt_res, gain, gain_science, pay, stock
from .effects import (Battle, army_strength, battle_bonus, effects_of, hydra_unit, source_name, unit_strength,
                      upgrade_gains)
from .special import circle_notes, curse_any, hydra_died
from .state import OBJECT_NAMES

if TYPE_CHECKING:
    from .game import Game

STANCES = {"fight": "fight", "claim": "claim the object", "pass": "pass"}


# --- Войска -----------------------------------------------------------------------------

def available_units(game: Game, p: int) -> dict[str, int]:
    """Войска при игроке. Гидра, стоящая в другом регионе, сражается отдельно (lone_hydra)."""
    units = dict(game.state.players[p].units)
    if lone_hydra(game, p) is None:
        return units
    units.pop(hydra_unit(game, p), None)
    return units


def lone_hydra(game: Game, p: int) -> str | None:
    """Регион гидры наги, если она жива и стоит отдельно от наги."""
    player = game.state.players[p]
    if player.hydra and player.hydra != player.region and player.units.get(hydra_unit(game, p)):
        return player.hydra
    return None


def hydra_army(game: Game, p: int) -> dict[str, int]:
    return {hydra_unit(game, p): 1}


def army_data(game: Game, p: int, battle: Battle | None = None, units: dict[str, int] | None = None) -> dict:
    faction = game.faction_of(p)
    units = available_units(game, p) if units is None else units
    return {
        unit: {"name": faction.units[unit].name, "count": n, "strength": unit_strength(game, p, unit, battle)}
        for unit, n in units.items() if n
    }


def army_text(game: Game, p: int, battle: Battle | None = None, units: dict[str, int] | None = None) -> str:
    text = ", ".join(f"{unit} ({u['name']}) ×{u['count']}, strength {u['strength']}"
                     for unit, u in army_data(game, p, battle, units).items()) or "no units"
    bonus = battle_bonus(game, p, battle)
    return text + (f"; +{bonus} to total strength" if bonus else "")


def has_units(game: Game, p: int) -> bool:
    return any(available_units(game, p).values())


def can_fight(game: Game, p: int, battle: Battle) -> bool:
    """Есть кого заявить — или есть прибавка к силе, которая действует и при нулевой силе."""
    return has_units(game, p) or battle_bonus(game, p, battle) > 0


def max_strength(game: Game, p: int, battle: Battle, units: dict[str, int] | None = None) -> int:
    """Сила всех войск с лучшими модификациями Лавки бронника, на которые хватает ресурсов."""
    units = available_units(game, p) if units is None else units
    return army_strength(game, p, units, battle, max_upgrades(game, p, units, battle))


def affordable_upgrades(game: Game, p: int) -> int:
    """Лавка бронника: на сколько модификаций хватает ресурсов (без Лавки — 0)."""
    most = 0
    for e in effects_of(game, p, "pay_to_upgrade_units"):
        key, price = next(iter(e["cost_per_unit"].items()))
        most = stock(game, p, key) // price
    return most


def max_upgrades(game: Game, p: int, army: dict[str, int], battle: Battle) -> int:
    return min(len(upgrade_gains(game, p, army, battle)), affordable_upgrades(game, p))


def upgrade_data(game: Game, p: int) -> dict | None:
    """Лавка бронника в данных решения о заявке войск: модификацию называют вместе с заявкой (поле upgrade).
    strength — во что модифицируется каждое войско; max — на сколько единиц хватает ресурсов."""
    for e in effects_of(game, p, "pay_to_upgrade_units"):
        return {"cost_per_unit": e["cost_per_unit"], "strength": e["strength"], "max": affordable_upgrades(game, p)}
    return None


def upgrade_text(game: Game, p: int) -> str:
    """Подсказка о модификации Лавкой бронника для текста решения о заявке."""
    data = upgrade_data(game, p)
    if not data or not data["max"]:
        return ""
    units = game.faction_of(p).units
    return (f"\n{source_name(game, p, 'pay_to_upgrade_units')}: together with the commitment you may upgrade committed "
            f"units for this battle (field upgrade), {fmt_res(data['cost_per_unit'])} each, you can afford {data['max']}: "
            + ", ".join(f"{units[u].name} → {v}" for u, v in data["strength"].items())
            + ". The units that gain most are upgraded.")


def split_commit(answer: Any) -> tuple[Any, Any]:
    """Заявка войск — {unit: n} или вместе с модификацией Лавки бронника {"army": {unit: n}, "upgrade": n}
    (в бою с чудовищем — {"monster": i, "army": …, "upgrade": n}). Возвращает войска и upgrade (None — не назван:
    тогда движок спросит о модификации отдельно, как в старых сохранениях)."""
    if isinstance(answer, dict) and "army" in answer:
        return answer["army"], answer.get("upgrade")
    return answer, None


def check_upgrade(game: Game, p: int, upgrade: Any) -> str | None:
    if upgrade is None:
        return None
    if isinstance(upgrade, bool) or not isinstance(upgrade, int) or upgrade < 0:
        return "upgrade must be a non-negative integer"
    most = affordable_upgrades(game, p)
    if upgrade > most:
        return f"upgrade: you can upgrade at most {most} unit(s)"
    return None


def check_commit(game: Game, p: int, answer: Any, available: dict[str, int], battle: Battle | None = None,
                 beat: int | None = None) -> str | None:
    """Заявка войск вместе с модификацией Лавки бронника. beat — сила крепости или чудовища: заявку, которой
    её заведомо не превзойти, движок не принимает (отказаться от боя можно всегда)."""
    if isinstance(answer, dict) and "army" in answer and set(answer) - {"army", "upgrade"}:
        return "Unknown field(s): " + ", ".join(sorted(set(answer) - {"army", "upgrade"}))
    army, upgrade = split_commit(answer)
    error = check_army(army, available) or check_upgrade(game, p, upgrade)
    if error or beat is None:
        return error
    return check_beats(game, p, army, upgrade, battle, beat)


def check_beats(game: Game, p: int, army: dict[str, int], upgrade: int | None, battle: Battle, beat: int) -> str | None:
    """Хватит ли заявки, чтобы превзойти beat. Модификация не названа — значит, о ней спросят отдельно:
    считаем лучшую, на какую хватает ресурсов."""
    most = max_upgrades(game, p, army, battle)
    strength = army_strength(game, p, army, battle, most if upgrade is None else min(upgrade, most))
    if strength > beat:
        return None
    return (f"Your strength would be {strength}, which does not exceed {beat}: this battle is certain to be lost. "
            "Commit more units or decline the battle.")


def lose_units(game: Game, p: int, army: dict[str, int]) -> None:
    """Все заявленные в сражение единицы погибают при любом исходе; нежить потом воскрешает часть из них."""
    player = game.state.players[p]
    units = player.units
    for unit, n in army.items():
        units[unit] -= n
    raised: dict[str, int] = {}
    for e in effects_of(game, p, "resurrect_after_battle"):        # Курган, Склеп
        raised[e["unit"]] = raised.get(e["unit"], 0) + e["count"]
    for e in effects_of(game, p, "resurrect_if_present"):          # Костница: лич воскрешает всех скелетов
        if army.get(e["trigger_unit"]):
            raised[e["unit"]] = army.get(e["unit"], 0)
    raised = {u: min(n, army.get(u, 0)) for u, n in raised.items() if min(n, army.get(u, 0)) > 0}
    for unit, n in raised.items():
        units[unit] += n
    if raised:
        faction = game.faction_of(p)
        game.log(f"{player.name} resurrects " + ", ".join(f"{faction.units[u].name} ×{n}" for u, n in raised.items()))
    hydra_died(game, p)


def on_battle_win(game: Game, p: int) -> None:
    for e in effects_of(game, p, "on_battle_win"):  # Идол войны орков
        gain(game, p, e["gain"], f"{source_name(game, p, 'on_battle_win')}: battle won")


def ask_upgrades(game: Game, battle: Battle, armies: dict[int, dict[str, int]], secret: bool,
                 planned: dict[int, int | None] | None = None) -> dict[int, int]:
    """Лавка бронника эльфов: перед сражением можно заплатить за модификацию силы заявленных единиц.
    В сражении между игроками это часть тайной заявки. Модифицируются те единицы, кому это даёт больше всего.
    planned — сколько модифицировать, названо вместе с заявкой (лишнее не оплачивается); кто не назвал
    (старые сохранения, простые боты), того движок спрашивает отдельно."""
    planned = planned or {}
    decisions = []
    result = {p: 0 for p in armies}
    for p, army in armies.items():
        for e in effects_of(game, p, "pay_to_upgrade_units"):
            most = max_upgrades(game, p, army, battle)
            if planned.get(p) is not None:
                result[p] = min(planned[p], most)
                continue
            gains = upgrade_gains(game, p, army, battle)
            source = source_name(game, p, "pay_to_upgrade_units")
            decisions.append(Decision(
                "battle_upgrade", p,
                f"{source}: pay {fmt_res(e['cost_per_unit'])} per unit to upgrade the strength of committed units for "
                f"this battle (up to {most}; the units that gain most are upgraded: +{', +'.join(map(str, gains))}). "
                f"Your committed strength without it: {army_strength(game, p, army, battle)}. Answer how many, 0 — none.",
                {"cost_per_unit": e["cost_per_unit"], "max": most, "gains": gains[:most],
                 "strength": army_strength(game, p, army, battle), "battle": battle.kind, "secret": secret},
                lambda a, m=most: None if isinstance(a, int) and not isinstance(a, bool) and 0 <= a <= m
                else f"Expected an integer from 0 to {m}",
                0,
                forced=most == 0,
                reason=f"you could not upgrade any committed unit for this battle ({source}: not enough resources "
                       "or nothing to upgrade)",
            ))
    if decisions:
        answers = game.ask_all(decisions) if secret else [game.resolve(d) for d in decisions]
        for d, n in zip(decisions, answers):
            result[d.player] = n
    for p, n in result.items():
        if n:
            cost = {k: v * n for k, v in effects_of(game, p, "pay_to_upgrade_units")[0]["cost_per_unit"].items()}
            pay(game, p, cost)
            game.log(f"{game.state.players[p].name} pays {fmt_res(cost)} to upgrade {n} unit(s) "
                     f"({source_name(game, p, 'pay_to_upgrade_units')})")
    return result


# --- Объекты в регионе --------------------------------------------------------------------

def claimable(game: Game, p: int, region: str) -> str | None:
    """Тип объекта (fortress/artifact), на который игрок может претендовать в регионе."""
    st = game.state
    obj = game.map.regions[region].object
    if st.reserved.get(region, p) != p:
        return None
    if obj == "fortress" and region not in st.fortress_owner:
        return obj
    if obj == "artifact" and region not in st.artifact_holder:
        return obj
    return None


def resolve_encounters(game: Game, leftover: dict[int, int], moved: set[int] | None = None) -> None:
    """moved — кто действительно сменил регион (для Алтаря нежити); None — считать, что все."""
    st = game.state
    order = st.turn_order()
    moved = set(order) if moved is None else moved
    by_region: dict[str, list[int]] = defaultdict(list)
    for p in order:
        by_region[st.players[p].region].append(p)
    hydras: dict[str, list[int]] = defaultdict(list)  # регион -> чьи отдельные гидры там стоят
    for p in order:
        if lone_hydra(game, p):
            hydras[lone_hydra(game, p)].append(p)
    entitled: set[int] = set()   # кто может захватить крепость / взять артефакт в своём регионе
    stole: set[int] = set()      # кто отобрал артефакт в бою и поэтому не берёт артефакт региона
    contested = [(r, ps) for r, ps in by_region.items() if len(ps) > 1 or hydras.get(r)]
    contested.sort(key=lambda item: order.index(item[1][0]))
    for region, players in contested:
        if hydras.get(region):
            # гидра обязана сразиться; в её регионе после этого никто ничего не получает
            player_battle(game, region, players, hydras[region])
        else:
            resolve_contest(game, region, players, entitled, stole)
    entitled |= {ps[0] for r, ps in by_region.items() if len(ps) == 1 and not hydras.get(r)}
    for p in order:
        if p in entitled:
            try_capture_fortress(game, p)
        hydra_capture_fortress(game, p)
    for p in order:
        fight_monster(game, p, leftover[p], stayed=p not in moved)
    for p in order:
        if p in entitled and p not in stole:
            try_take_artifact(game, p)


# --- Сражение между игроками ------------------------------------------------------------

def resolve_contest(game: Game, region: str, players: list[int], entitled: set[int], stole: set[int]) -> None:
    st = game.state
    stances: dict[int, str] = {}
    buy_off = can_avoid_battle(game, players)
    for i, p in enumerate(players):
        allowed = stance_choices(game, region, p)
        options, default = distinct_stances(game, region, list(stances.items()), p, players[i + 1:], buy_off)
        declared = "; ".join(f"{st.players[q].name}: {STANCES[s]}" for q, s in stances.items()) or "nobody yet"
        others = ", ".join(f"{st.players[q].name} ({army_text(game, q)})" for q in players if q != p)
        certain = "fight" in stances.values() or list(stances.values()).count("claim") > 1
        stances[p] = game.ask(
            p, "region_stance",
            f"In {region} together with you: {others}. Already declared: {declared}.\n"
            f"Region object: {OBJECT_NAMES[game.map.regions[region].object]}. Your units: {army_text(game, p)}.\n"
            "A battle starts if anyone chooses fight or more than one player claims the object.\n"
            + ("The battle here is already certain.\n" if certain else "")
            + "Options: " + "; ".join(f"{o} — {STANCES[o]}" for o in options) + ".",
            {"region": region, "options": options, "declared": {str(q): s for q, s in stances.items()}},
            lambda a, o=options: check_choice(a, o),
            default,
            forced=len(options) == 1,  # все варианты ведут к одному и тому же
            recorded=lambda a, o=allowed: check_choice(a, o),  # старые сохранения: равносильные варианты
            reason=(f"the battle in {region} was already certain, so every stance led to it" if certain
                    else f"every stance in {region} led to the same outcome"),
        )
        game.log(f"{st.players[p].name} in {region}: {STANCES[stances[p]]}")
    claims = [p for p, s in stances.items() if s == "claim"]
    if "fight" not in stances.values() and len(claims) <= 1:
        entitled.update(claims)
        return
    if avoid_battle(game, region, players):
        if len(claims) == 1:  # при нескольких претендентах объект в этом раунде не достаётся никому
            entitled.update(claims)
        return
    winner, reward = player_battle(game, region, players)
    if winner is not None:
        entitled.add(winner)
        if reward == "artifact":
            stole.add(winner)


def stance_choices(game: Game, region: str, p: int) -> list[str]:
    return ["fight", "pass"] + (["claim"] if claimable(game, p, region) else [])


def stance_outcome(stances: list[tuple[int, str]], buy_off: bool) -> tuple:
    """Чем кончатся заявления: мир (и кому объект) или сражение (и кому объект, если от него откупятся)."""
    claims = [q for q, s in stances if s == "claim"]
    sole = claims[0] if len(claims) == 1 else None
    if not any(s == "fight" for _, s in stances) and len(claims) <= 1:
        return "peace", sole
    return "battle", sole if buy_off else None


def distinct_stances(game: Game, region: str, declared: list[tuple[int, str]], p: int, later: list[int],
                     buy_off: bool) -> tuple[list[str], str]:
    """Варианты игрока p, ведущие к разным исходам при любых заявлениях тех, кто решает после него,
    и вариант, равносильный «пасу» (ответ по умолчанию).

    Например, если кто-то уже выбрал «сражаться», «пас» ничего не меняет — остаётся «сражаться»
    (и «претендовать», если от сражения можно откупиться: тогда объект получит единственный претендент).
    «Пас» не нужен и тогда, когда «претендовать» лишь даёт право на объект: от крепости и артефакта
    можно потом отказаться."""
    continuations = list(product(*[[(q, s) for s in stance_choices(game, region, q)] for q in later]))
    choices = stance_choices(game, region, p)
    outcomes = {o: tuple(stance_outcome(declared + [(p, o)] + list(c), buy_off) for c in continuations)
                for o in choices}
    rep: dict[str, str] = {}
    for option in ("fight", "claim", "pass"):  # из равносильных остаётся первый
        if option in choices:
            rep[option] = next((o for o in rep if outcomes[o] == outcomes[option]), option)
    if "claim" in choices and rep["pass"] == "pass":
        waived = tuple((kind, None if who == p else who) for kind, who in outcomes["claim"])
        if waived == outcomes["pass"]:
            rep["pass"] = rep["claim"]
    kept = set(rep.values())
    return [o for o in choices if o in kept], rep["pass"]


def avoid_amount(e: dict, players: list[int]) -> int:
    return sum(e["cost_per_player"].values()) * (len(players) - 1)


def can_avoid_battle(game: Game, players: list[int]) -> bool:
    """Может ли кто-то в регионе откупиться от сражения (Шахта наг)."""
    st = game.state
    return any(sum(st.players[p].resources[k] for k in RESOURCES) >= avoid_amount(e, players)
               for p in players for e in effects_of(game, p, "pay_to_avoid_battle"))


def avoid_battle(game: Game, region: str, players: list[int]) -> bool:
    """Шахта наг: заплатить каждому сопернику в регионе по 3 любых ресурса — и сражения нет."""
    st = game.state
    for p in players:
        for e in effects_of(game, p, "pay_to_avoid_battle"):
            others = [q for q in players if q != p]
            amount = avoid_amount(e, players)
            have = {k: st.players[p].resources[k] for k in RESOURCES}
            if sum(have.values()) < amount:
                continue
            source = source_name(game, p, "pay_to_avoid_battle")
            answer = game.ask(
                p, "avoid_battle",
                f"{source}: a battle is about to start in {region}. You may pay {amount} resources of your choice "
                f"({e['cost_per_player'].get('any', 0)} to each opponent: "
                + ", ".join(st.players[q].name for q in others) + ") to cancel it. "
                f"Your stock: {fmt_res(have)}. Answer {{\"wood\": n, \"iron\": n, \"gold\": n}} or null to fight.",
                {"region": region, "amount": amount, "to": [str(q) for q in others], "resources": have},
                lambda a, n=amount, h=have: None if a is None else check_payment(a, n, h),
                None,
            )
            if answer is None:
                continue
            pool = [k for k in RESOURCES for _ in range(answer.get(k, 0))]
            pay(game, p, {k: v for k, v in answer.items() if v})
            game.log(f"{st.players[p].name} pays to avoid the battle in {region} ({source})")
            share = amount // len(others)
            for i, q in enumerate(others):
                part: dict[str, int] = {}
                for k in pool[i * share:(i + 1) * share]:
                    part[k] = part.get(k, 0) + 1
                gain(game, q, part, f"paid by {st.players[p].name}")
            return True
    return False


def check_payment(answer: Any, amount: int, have: dict[str, int]) -> str | None:
    error = check_resource_split(answer, amount)
    if error:
        return error
    for k, v in answer.items():
        if v > have[k]:
            return f"You have only {have[k]} {k}"
    return None


def player_battle(game: Game, region: str, players: list[int], hydras: list[int] = ()) -> tuple[int | None, str | None]:
    """Возвращает победителя (или None при ничьей) и тип выбранной им награды.
    hydras — наги, чьи отдельные гидры сражаются здесь; тогда наград нет ни у кого."""
    st = game.state
    battle = Battle("pvp", region)
    hydra_power = {o: unit_strength(game, o, hydra_unit(game, o), battle) for o in hydras}
    hydra_text = "".join(f" {st.players[o].name}'s Гидра (strength {s}) fights here too: rewards are lost "
                         "whatever the outcome." for o, s in hydra_power.items())
    decisions = [
        Decision(
            "commit_army", p,
            f"Battle in {region} between: {', '.join(st.players[q].name for q in players)}. "
            "Commitment is secret. All committed units die whatever the outcome; "
            f"the highest strength wins, on a tie everyone loses.{hydra_text}\n"
            f"Your units: {army_text(game, p, battle)}. You may commit nobody.{upgrade_text(game, p)}",
            {"region": region, "units": army_data(game, p, battle),
             "opponents": {str(q): army_data(game, q, battle) for q in players if q != p},
             **({"hydras": {str(o): s for o, s in hydra_power.items()}} if hydras else {}),
             **({"upgrade": upgrade_data(game, p)} if upgrade_data(game, p) else {})},
            lambda a, p=p: check_commit(game, p, a, available_units(game, p)),
            {},
            forced=not has_units(game, p),  # заявить некого
            reason=f"you had no units to commit to the battle in {region}",
        )
        for p in players
    ]
    answers = dict(zip(players, map(split_commit, game.ask_all(decisions))))
    armies = {p: army for p, (army, _) in answers.items()}
    upgrades = ask_upgrades(game, battle, armies, secret=True, planned={p: n for p, (_, n) in answers.items()})
    strengths = {p: army_strength(game, p, army, battle, upgrades[p]) for p, army in armies.items()}
    for p, army in armies.items():
        lose_units(game, p, army)
    game.log(f"Battle in {region}: " + ", ".join(
        [f"{st.players[p].name} — strength {s}" for p, s in strengths.items()]
        + [f"{st.players[o].name}'s Гидра — strength {s}" for o, s in hydra_power.items()]))
    for o in hydras:
        lose_units(game, o, hydra_army(game, o))  # гидра погибает при любом исходе
    sides = {("player", p): s for p, s in strengths.items()} | {("hydra", o): s for o, s in hydra_power.items()}
    best = max(sides.values())
    leaders = [side for side, s in sides.items() if s == best]
    if len(leaders) > 1:
        game.log("Tie — everyone loses")
        return None, None
    kind, winner = leaders[0]
    monsters = st.players[winner].monsters
    circled = monsters.index(None) if None in monsters else None
    if circled is not None:
        monsters[circled] = "pvp"
    if hydras:
        who = st.players[winner].name + ("'s Гидра" if kind == "hydra" else "")
        game.log(f"{who} wins: a monster is circled, but there are no rewards")
        return None, None
    losers = [p for p in players if p != winner]
    game.log(f"{st.players[winner].name} wins")
    reward = choose_reward(game, winner, losers, strengths, circled)
    on_battle_win(game, winner)
    return winner, reward


def choose_reward(game: Game, winner: int, losers: list[int], strengths: dict[int, int], circled: int | None) -> str | None:
    st = game.state
    options: dict[str, Any] = {}
    if circled is not None:
        options["monster"] = game.monster_line(winner)[circled].loot
    pool = {str(q): dict(st.players[q].resources) for q in losers}
    amount = min(strengths[winner] - min(strengths[q] for q in losers), sum(sum(r.values()) for r in pool.values()))
    if amount > 0:
        options["resources"] = {"amount": amount, "from": pool}
    loser_artifacts = {str(q): st.artifacts_of(q) for q in losers if st.artifacts_of(q)}
    if loser_artifacts:
        options["artifact"] = loser_artifacts
    if not options:
        return None
    lines = ["You won. Choose one reward:"]
    if "monster" in options:
        lines.append(f'  monster — the circled monster\'s loot: {fmt_res(options["monster"])}')
    if "resources" in options:
        lines.append(f'  resources — take up to {amount} resources of your choice from the losers: '
                     + "; ".join(f"player {q} ({st.players[int(q)].name}): {fmt_res(r)}" for q, r in pool.items()))
    if "artifact" in options:
        lines.append('  artifact — take an artifact: '
                     + "; ".join(f"player {q}: {', '.join(a)}" for q, a in loser_artifacts.items()))
    default = {"type": "monster"} if "monster" in options else {"type": next(iter(options)), "take": {}}
    if default["type"] == "artifact":
        q, arts = next(iter(loser_artifacts.items()))
        default = {"type": "artifact", "from": q, "artifact": arts[0]}
    only = only_reward(options)
    answer = game.ask(winner, "choose_reward", "\n".join(lines), {"options": options},
                      lambda a: check_reward(a, options), only or default, forced=only is not None,
                      reason="only one reward was possible")
    apply_reward(game, winner, answer, circled)
    return answer["type"]


def only_reward(options: dict) -> dict | None:
    """Награда, если выбора по факту нет: единственный вид награды и внутри него один вариант.
    Ресурсы проигравших брать выгодно все, сколько позволено, поэтому выбора нет, когда можно
    забрать всё или когда есть только один вид ресурса у одного проигравшего."""
    if len(options) != 1:
        return None
    if "monster" in options:
        return {"type": "monster"}
    if "artifact" in options:
        arts = [(q, a) for q, held in options["artifact"].items() for a in held]
        return {"type": "artifact", "from": arts[0][0], "artifact": arts[0][1]} if len(arts) == 1 else None
    amount, pool = options["resources"]["amount"], options["resources"]["from"]
    positive = [(q, k, v) for q, res in pool.items() for k, v in res.items() if v]
    if amount >= sum(v for _, _, v in positive):
        take: dict[str, dict[str, int]] = {}
        for q, k, v in positive:
            take.setdefault(q, {})[k] = v
        return {"type": "resources", "take": take}
    if len(positive) == 1:
        q, k, _ = positive[0]
        return {"type": "resources", "take": {q: {k: amount}}}
    return None


def check_reward(answer: Any, options: dict) -> str | None:
    if not isinstance(answer, dict) or answer.get("type") not in options:
        return f"Expected an object with type from {list(options)}"
    if answer["type"] == "resources":
        take = answer.get("take", {})
        if not isinstance(take, dict):
            return "take must be an object {\"<player number>\": {\"wood\": n}}"
        total = 0
        pool = options["resources"]["from"]
        for q, res in take.items():
            if q not in pool:
                return f"Player {q} is not among the losers"
            error = check_resource_split(res, sum(res.values()) if isinstance(res, dict) else -1)
            if error:
                return error
            for k, v in res.items():
                if v > pool[q].get(k, 0):
                    return f"Player {q} does not have {v} {k}"
            total += sum(res.values())
        if total > options["resources"]["amount"]:
            return f"You may take at most {options['resources']['amount']} resources"
    if answer["type"] == "artifact":
        arts = options["artifact"].get(str(answer.get("from")))
        if not arts or answer.get("artifact") not in arts:
            return "That player does not have this artifact"
    return None


def apply_reward(game: Game, winner: int, answer: dict, circled: int | None) -> None:
    st = game.state
    name = st.players[winner].name
    if answer["type"] == "monster":
        game.log(f"{name} takes the monster's loot")
        gain(game, winner, game.monster_line(winner)[circled].loot, "battle reward")
    elif answer["type"] == "resources":
        taken: dict[str, int] = {}
        for q, res in answer.get("take", {}).items():
            for k, v in res.items():
                st.players[int(q)].resources[k] -= v
                taken[k] = taken.get(k, 0) + v
            game.log(f"{name} takes from {st.players[int(q)].name}: {fmt_res(res)}")
        gain(game, winner, taken, "battle reward")
    else:
        art = answer["artifact"]
        game.log(f"{name} takes the {art} artifact from {st.players[int(answer['from'])].name}")
        acquire_artifact(game, winner, art)


# --- Крепости, чудовища, артефакты ----------------------------------------------------------

def try_capture_fortress(game: Game, p: int) -> None:
    st = game.state
    region = st.players[p].region
    battle = Battle("fortress", region)
    if claimable(game, p, region) != "fortress" or not can_fight(game, p, battle):
        return
    fortress = game.map.regions[region].fortress

    def declined(a: Any) -> bool:
        """Отказ от штурма: null или пустая заявка без прибавки к силе."""
        army, _ = split_commit(a)
        return a is None or isinstance(army, dict) and not any(army.values()) and not battle_bonus(game, p, battle)

    answer = game.ask(
        p, "capture_fortress",
        f"Fortress in {region}: strength {fortress.strength}, reward {fmt_res(fortress.loot)} and {fortress.vp} VP. "
        f"To capture it your strength must exceed {fortress.strength}; committed units die. A commitment that "
        "cannot win is not accepted.\n"
        f"Your units: {army_text(game, p, battle)}. You may decline to attack.{upgrade_text(game, p)}",
        {"region": region, "strength": fortress.strength, "loot": fortress.loot, "vp": fortress.vp,
         "units": army_data(game, p, battle), **({"upgrade": upgrade_data(game, p)} if upgrade_data(game, p) else {})},
        lambda a: None if declined(a) else check_commit(game, p, a, available_units(game, p), battle, fortress.strength),
        None,
        forced=max_strength(game, p, battle) <= fortress.strength,  # не взять и всеми
        recorded=lambda a: None if a is None else check_commit(game, p, a, available_units(game, p)),
        reason=f"even all your units (strength {max_strength(game, p, battle)}) could not beat the fortress in {region} "
               f"(strength {fortress.strength}), so you did not attack",
    )
    if declined(answer):
        return
    army, planned = split_commit(answer)
    upgrades = ask_upgrades(game, battle, {p: army}, secret=False, planned={p: planned})[p]
    strength = army_strength(game, p, army, battle, upgrades)
    lose_units(game, p, army)
    name = st.players[p].name
    if strength <= fortress.strength:
        game.log(f"{name} attacks the fortress in {region} with strength {strength} and fails")
        return
    st.fortress_owner[region] = p
    st.players[p].fortresses.append(region)
    game.log(f"{name} captures the fortress in {region} (strength {strength} vs {fortress.strength})")
    gain(game, p, fortress.loot, f"fortress captured: {region}")
    on_battle_win(game, p)


def hydra_capture_fortress(game: Game, p: int) -> None:
    """Отдельная гидра может штурмовать крепость в своём регионе; ресурсы крепости теряются."""
    st = game.state
    region = lone_hydra(game, p)
    if region is None or claimable(game, p, region) != "fortress":
        return
    battle = Battle("fortress", region)
    army = hydra_army(game, p)
    strength = army_strength(game, p, army, battle)
    fortress = game.map.regions[region].fortress
    answer = game.ask(
        p, "capture_fortress",
        f"Your Гидра is alone in {region} with a fortress: strength {fortress.strength}, {fortress.vp} VP "
        f"(its loot {fmt_res(fortress.loot)} would be lost). Гидра's strength: {strength}; she dies after the battle. "
        "Attack with Гидра?",
        {"region": region, "strength": fortress.strength, "loot": {}, "vp": fortress.vp,
         "units": army_data(game, p, battle, army)},
        lambda a: None if a is None else check_army(a, army),
        None,
        forced=strength <= fortress.strength,
        reason=f"Гидра's strength {strength} could not beat the fortress in {region} (strength {fortress.strength}), "
               "so she did not attack",
    )
    if not answer:
        return
    lose_units(game, p, answer)
    name = st.players[p].name
    if strength <= fortress.strength:
        game.log(f"{name}'s Гидра attacks the fortress in {region} with strength {strength} and fails")
        return
    st.fortress_owner[region] = p
    st.players[p].fortresses.append(region)
    game.log(f"{name}'s Гидра captures the fortress in {region} (strength {strength} vs {fortress.strength}); "
             "the loot is lost")


def fight_monster(game: Game, p: int, leftover: int, stayed: bool = False) -> None:
    """Бой с чудовищем своей линии. Наги могут драться отдельной гидрой вместо войска (тогда без ресурсов);
    нежить с Алтарём, оставшись на месте, выбирает любое чудовище линии."""
    st = game.state
    player = st.players[p]
    line = game.monster_line(p)
    monsters = player.monsters
    uncircled = [i for i, m in enumerate(monsters) if m is None]
    any_monster = stayed and effects_of(game, p, "fight_any_monster_if_stayed")
    candidates = uncircled if any_monster else uncircled[: leftover + 1]
    battle = Battle("monster", player.region)
    hydra_region = lone_hydra(game, p)
    units = available_units(game, p)
    pool = {**units, **(hydra_army(game, p) if hydra_region else {})}
    if not candidates or not (any(pool.values()) or battle_bonus(game, p, battle)):
        return
    best = max(max_strength(game, p, battle, units),
               army_strength(game, p, hydra_army(game, p), Battle("monster", hydra_region)) if hydra_region else 0)
    rows = [f"  {i}: strength {m.strength}, reward: {m.vp} VP" + (f" and {fmt_res(m.loot)}" if m.loot else "")
            for i, m in ((i, line[i]) for i in candidates)]
    skip = ("Altar: you stayed, so you may fight any monster of your line" if any_monster
            else f"Unspent MP: {leftover} — you may skip that many monsters")
    hydra_note = (f"\nYour Гидра is alone in {hydra_region}: she may fight instead of your army (alone, "
                  "the monster's loot is lost, she dies)." if hydra_region else "")
    answer = game.ask(
        p, "fight_monster",
        f"Monster fight (at most one per round). {skip}.\n"
        + "\n".join(rows)
        + f"\nYour units: {army_text(game, p, battle, pool)}. To win, your strength must exceed the monster's; "
        f"committed units die. A commitment that cannot win is not accepted. You may decline to fight."
        f"{hydra_note}{upgrade_text(game, p)}",
        {"candidates": {str(i): {"strength": line[i].strength, "loot": line[i].loot, "vp": line[i].vp}
                        for i in candidates},
         "units": army_data(game, p, battle, pool), **({"upgrade": upgrade_data(game, p)} if upgrade_data(game, p) else {})},
        lambda a: check_monster(game, p, a, candidates, pool, hydra_region),
        None,
        # ни одно доступное чудовище не победить даже всеми войсками
        forced=all(line[i].strength >= best for i in candidates),
        recorded=lambda a: check_monster(game, p, a, candidates, pool, hydra_region, strict=False),
        reason=f"even all your units (strength {best}) could not beat any monster you could reach, so you did not fight",
    )
    if not answer:
        return
    monster = line[answer["monster"]]
    army = answer["army"]
    alone = bool(hydra_region and army.get(hydra_unit(game, p)))
    if alone:
        battle = Battle("monster", hydra_region)
    upgrades = ask_upgrades(game, battle, {p: army}, secret=False, planned={p: answer.get("upgrade")})[p]
    strength = army_strength(game, p, army, battle, upgrades)
    lose_units(game, p, army)
    name = player.name
    if strength <= monster.strength:
        game.log(f"{name} loses to a strength-{monster.strength} monster (army strength {strength})")
        return
    monsters[answer["monster"]] = "monster"
    game.log(f"{name} defeats a strength-{monster.strength} monster (army strength {strength})")
    if alone:
        game.log(f"{name}'s Гидра fought alone: the monster's loot is lost")
        return
    gain(game, p, monster.loot, f"strength-{monster.strength} monster defeated")
    for e in effects_of(game, p, "on_monster_win"):
        gain(game, p, e["gain"], "building effect for defeating a monster")
    on_battle_win(game, p)


def check_monster(game: Game, p: int, answer: Any, candidates: list[int], pool: dict[str, int] | None = None,
                  hydra_region: str | None = None, strict: bool = True) -> str | None:
    """strict — не принимать заявку, которой чудовище заведомо не победить (в старых сохранениях такие есть)."""
    if answer is None:
        return None
    if not isinstance(answer, dict) or not {"monster", "army"} <= set(answer) <= {"monster", "army", "upgrade"}:
        return 'Expected {"monster": number, "army": {...}} or null'
    error = check_choice(answer["monster"], candidates)
    error = error or check_army(answer["army"], available_units(game, p) if pool is None else pool)
    error = error or check_upgrade(game, p, answer.get("upgrade"))
    if error:
        return error
    alone = False
    if hydra_region:
        hydra = hydra_unit(game, p)
        alone = bool(answer["army"].get(hydra))
        if alone and any(n for u, n in answer["army"].items() if u != hydra):
            return f"Your Гидра is in {hydra_region}, apart from your army: she can only fight alone"
    if not strict:
        return None
    battle = Battle("monster", hydra_region if alone else game.state.players[p].region)
    return check_beats(game, p, answer["army"], answer.get("upgrade"), battle,
                       game.monster_line(p)[answer["monster"]].strength)


def artifact_condition(game: Game, p: int, region: str) -> dict:
    """Условие получения артефакта для фракции игрока (у наг часть условий своя)."""
    return game.faction_of(p).artifact_conditions.get(region) or game.map.regions[region].artifact


def artifact_condition_met(game: Game, p: int, region: str) -> bool:
    cond = artifact_condition(game, p, region)
    player = game.state.players[p]
    kind = cond["condition"]
    if kind == "pay":
        return can_pay(game, p, cond["cost"])
    if kind == "foundationsBuilt":
        return sum(1 for level in player.foundations.values() if level) >= cond["count"]
    if kind == "battleWins":
        return player.victories >= cond["count"]
    if kind == "mountainsVisited":
        return len(player.mountains) >= cond["count"]
    raise ValueError(kind)


def wood_price(game: Game, p: int, region: str) -> dict[str, int] | None:
    """Гнездовье эльфов: артефакт за дерево, если грифонов в войске не меньше его цены в дереве."""
    for e in effects_of(game, p, "artifact_for_wood"):
        price = e["prices"][region]
        if game.state.players[p].units.get(e["unit"], 0) >= price and stock(game, p, "wood") >= price:
            return {"wood": price}
    return None


def try_take_artifact(game: Game, p: int) -> None:
    region = game.state.players[p].region
    if claimable(game, p, region) != "artifact":
        return
    cond = artifact_condition(game, p, region)
    met = artifact_condition_met(game, p, region)
    alternative = wood_price(game, p, region)
    if not met and alternative is None:
        return
    if alternative is not None and (cond["condition"] == "pay" or not met):
        cond = {"condition": "pay", "cost": alternative}  # за дерево дешевле, чем 12 ресурсов
    effect = game.faction_of(p).artifacts[region]
    price = f"Cost: {fmt_res(cond['cost'])}. " if cond["condition"] == "pay" else "Condition met, nothing to pay. "
    take = game.ask(
        p, "take_artifact",
        f"Artifact in {region}: {effect.text} {price}Take the artifact?",
        {"region": region, "condition": cond, "effect": effect.text},
        lambda a: None if isinstance(a, bool) else "The answer must be true or false",
        False,
    )
    if not take:
        return
    if cond["condition"] == "pay":
        pay(game, p, cond["cost"])
    acquire_artifact(game, p, region)


def acquire_artifact(game: Game, p: int, region: str) -> None:
    """Артефакт переходит к игроку; разовые эффекты срабатывают при каждом получении."""
    st = game.state
    st.artifact_holder[region] = p
    effects = game.faction_of(p).artifacts[region].effects
    game.log(f"{st.players[p].name} gains the {region} artifact: {game.faction_of(p).artifacts[region].text}")
    for e in effects:
        if e["type"] == "gain_units":
            units = st.players[p].units
            units[e["unit"]] = units.get(e["unit"], 0) + e["count"]
        elif e["type"] == "gain":
            gain(game, p, e["resources"], f"{region} artifact")
        elif e["type"] == "curse_regions":
            curse_any(game, p, e["count"], f"{region} artifact")
        elif e["type"] == "gain_science":
            gain_science(game, p, e["value"], f"{region} artifact")
    # Шог Ид орков: +2 ярости за этот и все имеющиеся артефакты, потом +2 за каждый новый
    for e in effects_of(game, p, "on_artifact_gain"):
        times = len(st.artifacts_of(p)) if e in effects and e.get("retroactive") else 1
        gain(game, p, {k: v * times for k, v in e["gain"].items()}, f"{source_name(game, p, 'on_artifact_gain')}")
    for e in effects_of(game, p, "note_on_artifact"):  # Корабль наг
        circle_notes(game, p, e["value"])
