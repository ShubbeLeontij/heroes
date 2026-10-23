"""Перевод на русский того, что окно получает от движка по-английски.

Движок, протокол и Claude работают по-английски (их тексты читают Claude и консольные клиенты),
а окно показывает всё по-русски: строки журнала, служебные сообщения сервера, причины
получения ресурсов. Шаблоны повторяют f-строки движка (heroes/engine/*, net/server.py, cli.py);
строка, не подошедшая ни к одному шаблону, показывается как есть. Тест проверяет, что
журнал целых партий переводится без остатка.
"""
from __future__ import annotations

import ast
import re

from .common import res
from .icons import MARKS as M

RESOURCES = {"wood": "дерево", "iron": "железо", "gold": "золото", "any": "любой", "rage": "ярость", "Крак": "крак",
             "science": "наука"}
RESOURCES_GEN = {"wood": "дерева", "iron": "железа", "gold": "золота", "rage": "ярости"}
ACTIONS = {"gather wood": "добыча дерева", "gather iron": "добыча железа", "gather gold": "добыча золота",
           "move": "перемещение", "build & hire": "строительство и наём"}
OBJECTS = {"fortress": "крепость", "foundation": "фундамент", "portal": "портал", "artifact": "артефакт"}
STANCES = {"fight": "сражаться", "claim the object": "претендует на объект", "pass": "пас"}
SCORE = {"buildings": "здания", "foundations": "фундаменты", "resources": "склад", "battles": "сражения",
         "artifacts": "артефакты"}
NAME = r"(?P<n>.+?)"
HYDRA = NAME + r"'s (?P<h>\S+)"   # «Аня's Гидра» — отдельная гидра наги
_KEY = r"(?:wood|iron|gold|rage|Крак)"
RES = rf"(?P<res>nothing|{_KEY} \d+(?:, {_KEY} \d+)*)"  # economy.fmt_res


def resources(text: str) -> str:
    """«iron 1, gold 2» (economy.fmt_res) → «железо 1, золото 2»."""
    if text == "nothing":
        return "ничего"
    return ", ".join(f"{RESOURCES.get(k, k)} {v}" for k, _, v in (part.rpartition(" ") for part in text.split(", ")))


def reason(text: str) -> str:
    """Причина получения ресурсов (economy.gain и ask_split)."""
    m = re.fullmatch(r"(.+): instead of (\d+) wood you may take iron and/or gold", text)
    if m:
        return f"{reason(m[1])}: вместо {m[2]} дерева можно взять железо и/или золото"
    m = re.fullmatch(r"(.+): resources of your choice", text)
    if m:
        return f"{reason(m[1])}: ресурсы на выбор"
    for pattern, render in REASONS:
        m = re.fullmatch(pattern, text)
        if m:
            return render(m)
    return text


REASONS = [
    (r"gather (wood|iron|gold)", lambda m: f"добыча {RESOURCES_GEN[m[1]]}"),
    (r"first visit to (.+)", lambda m: f"первое посещение: {m[1]}"),
    (r"new region visited: (.+)", lambda m: f"новый регион: {m[1]}"),
    (r"battle reward", lambda m: "награда за победу в сражении"),
    (r"fortress captured: (.+)", lambda m: f"захвачена крепость: {m[1]}"),
    (r"strength-(\d+) monster defeated", lambda m: f"побеждено чудовище силы {m[1]}"),
    (r"building effect for defeating a monster", lambda m: "эффект здания за победу над чудовищем"),
    (r"(.+): building on a foundation", lambda m: f"{reason(m[1])}: здание на фундаменте"),
    (r"(.+): battle won", lambda m: f"{reason(m[1])}: победа в сражении"),
    (r"(gather (?:wood|iron|gold)) raised by (.+)", lambda m: f"{reason(m[1])} повышена: {reason(m[2])}"),
    (r"paid by (.+)", lambda m: f"плата от игрока {m[1]}"),
    (r"the curse of (.+)", lambda m: f"проклятие региона {m[1]}"),
    (r"(.+) artifact", lambda m: f"артефакт {m[1]}"),
]


class Translator:
    """Переводчик журнала; static нужен для русских текстов артефактов."""

    def __init__(self, static: dict | None = None):
        self.artifacts: dict[str, str] = {}
        if static:
            for f in static["factions"].values():
                ru = f.get("artifacts_ru", {})
                self.artifacts.update({text: ru.get(region, text) for region, text in f["artifacts"].items()})

    def line(self, text: str) -> str:
        lead = text[: len(text) - len(text.lstrip("\n"))]
        body = text.lstrip("\n")
        for pattern, render in RULES:
            m = re.fullmatch(pattern, body, flags=re.S)
            if m:
                return lead + render(self, m)
        return text


def _res_gain(t, m):
    return f"{m['n']} получает: {resources(m['res'])} ({reason(m['why'])})"


def _score(t, m):
    parts = ast.literal_eval("{" + m["d"] + "}")
    return f"{m['n']}: {m['vp']} ПО (" + ", ".join(f"{SCORE.get(k, k)} {v}" for k, v in parts.items()) + ")"


LESSONS = [
    (r"no changes to the strategy notes", lambda m: "заметки по стратегии без изменений"),
    (r"strategy notes updated \((?P<s>.+?)\): (?P<f>.+)", lambda m: f"заметки по стратегии обновлены ({m['s']}): {m['f']}"),
    (r"strategy notes left unchanged — no valid answer \((?P<e>.+)\)",
     lambda m: f"заметки по стратегии не изменены — нет правильного ответа ({m['e']})"),
    (r"could not write the strategy notes: (?P<e>.+)", lambda m: f"не удалось записать заметки по стратегии: {m['e']}"),
    (r"scripts changed: (?P<f>.+)", lambda m: f"изменены скрипты: {m['f']}"),
    (r"committed to git", lambda m: "сохранено в git"),
]


def _lessons(t, m):
    """Итог разбора партии Claude: части через «; »."""
    parts = []
    for part in m["o"].split("; "):
        for pattern, render in LESSONS:
            if x := re.fullmatch(pattern, part):
                parts.append(render(x))
                break
        else:
            parts.append(part)
    return f"{m['n']}: " + "; ".join(parts)


RULES = [
    (r"=== Round (?P<k>\d+), first player: " + NAME + " ===", lambda t, m: f"=== Раунд {m['k']}, первый игрок: {m['n']} ==="),
    (r"Round actions: (?P<a>.+)", lambda t, m: "Действия раунда: " + re.sub(
        r"(\d+)\. ([a-z& ]+?)(?=, \d+\.|$)", lambda x: f"{x[1]}. {ACTIONS.get(x[2], x[2])}", m["a"])),
    (NAME + r" starts in (?P<r>.+)", lambda t, m: f"{m['n']} начинает в регионе {m['r']}"),
    (NAME + r" gains the (?P<r>.+?) artifact: (?P<x>.+)",
     lambda t, m: f"{m['n']} получает артефакт {m['r']}: {t.artifacts.get(m['x'], m['x'])}"),
    (NAME + r" gains " + RES + r" \((?P<why>.+)\)", _res_gain),
    (NAME + r" reached (?P<t>\d+) (?P<r>wood|iron|gold|rage) — (?P<vp>\d+) VP circled",
     lambda t, m: f"{m['n']}: {RESOURCES[m['r']]} {'достигла' if m['r'] == 'rage' else 'достигло'} {m['t']} — "
                  f"обведено {m['vp']} ПО"),
    (NAME + r" stays in (?P<r>.+)", lambda t, m: f"{m['n']} остаётся в регионе {m['r']}"),
    (NAME + r" moves to (?P<r>.+) \(spent (?P<a>\d+) of (?P<b>\d+) MP\)",
     lambda t, m: f"{m['n']} перемещается в регион {m['r']} (потрачено {m['a']} из {m['b']} ОП)"),
    (r"Battle in (?P<r>.+?): (?P<rest>.+)", lambda t, m: f"Сражение в регионе {m['r']}: " + ", ".join(
        re.sub(r"^(.+?)'s (\S+) — strength ", r"\2 игрока \1 — сила ", side).replace(" — strength ", " — сила ")
        for side in m["rest"].split(", "))),
    (NAME + r" in (?P<r>.+): (?P<s>fight|claim the object|pass)",
     lambda t, m: f"{m['n']} в регионе {m['r']}: {STANCES[m['s']]}"),
    (r"Tie — everyone loses", lambda t, m: "Ничья — проигрывают все"),
    (HYDRA + r" wins: a monster is circled, but there are no rewards",
     lambda t, m: f"{m['h']} игрока {m['n']} побеждает: чудовище обведено, но наград нет"),
    (NAME + r" wins: a monster is circled, but there are no rewards",
     lambda t, m: f"{m['n']} побеждает: чудовище обведено, но наград нет"),
    (NAME + r" wins", lambda t, m: f"{m['n']} побеждает"),
    (NAME + r" takes the monster's loot", lambda t, m: f"{m['n']} берёт награду обведённого чудовища"),
    (NAME + r" takes the (?P<a>.+) artifact from (?P<q>.+)", lambda t, m: f"{m['n']} отбирает артефакт {m['a']} у игрока {m['q']}"),
    (NAME + r" takes from (?P<q>.+): " + RES, lambda t, m: f"{m['n']} забирает у игрока {m['q']}: {resources(m['res'])}"),
    (HYDRA + r" attacks the fortress in (?P<r>.+) with strength (?P<s>\d+) and fails",
     lambda t, m: f"{m['h']} игрока {m['n']} штурмует крепость в регионе {m['r']} силой {m['s']} — неудачно"),
    (HYDRA + r" captures the fortress in (?P<r>.+) \(strength (?P<s>\d+) vs (?P<f>\d+)\); the loot is lost",
     lambda t, m: f"{m['h']} игрока {m['n']} захватывает крепость в регионе {m['r']} (сила {m['s']} против {m['f']}); "
                  "добыча теряется"),
    (HYDRA + r" fought alone: the monster's loot is lost",
     lambda t, m: f"{m['h']} игрока {m['n']} сражалась одна: добыча чудовища теряется"),
    (NAME + r" attacks the fortress in (?P<r>.+) with strength (?P<s>\d+) and fails",
     lambda t, m: f"{m['n']} штурмует крепость в регионе {m['r']} силой {m['s']} — неудачно"),
    (NAME + r" captures the fortress in (?P<r>.+) \(strength (?P<s>\d+) vs (?P<f>\d+)\)",
     lambda t, m: f"{m['n']} захватывает крепость в регионе {m['r']} (сила {m['s']} против {m['f']})"),
    (NAME + r" loses to a strength-(?P<k>\d+) monster \(army strength (?P<s>\d+)\)",
     lambda t, m: f"{m['n']} проигрывает чудовищу силы {m['k']} (сила армии {m['s']})"),
    (NAME + r" defeats a strength-(?P<k>\d+) monster \(army strength (?P<s>\d+)\)",
     lambda t, m: f"{m['n']} побеждает чудовище силы {m['k']} (сила армии {m['s']})"),
    (NAME + r" builds (?P<b>.+) \(level (?P<l>\d+)\)", lambda t, m: f"{m['n']} строит: {m['b']} (уровень {m['l']})"),
    (NAME + r" places the building on the foundation in (?P<r>.+) \((?P<l>\d+) VP\)",
     lambda t, m: f"{m['n']} возводит здание на фундаменте в регионе {m['r']} ({m['l']} ПО)"),
    (NAME + r" hires: (?P<u>.+)", lambda t, m: f"{m['n']} нанимает: {m['u']}"),
    (NAME + r" reserves the (?P<o>\w+) in (?P<r>.+) with Башня иллюзий",
     lambda t, m: f"{m['n']} бронирует {OBJECTS.get(m['o'], m['o'])} в регионе {m['r']} (Башня иллюзий)"),
    (r"⚠ " + NAME + r": no valid answer to '(?P<k>\w+)', the default was used",
     lambda t, m: f"⚠ {m['n']}: нет допустимого ответа ({m['k']}), выбран вариант по умолчанию"),
    (NAME + r": (?P<vp>\d+) VP \{(?P<d>.*)\}", _score),
    # особые механики фракций
    (NAME + r" pays " + RES + r" for science \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} платит {resources(m['res'])} за науку ({m['s']})"),
    (NAME + r" gains (?P<k>\d+) science \((?P<why>.+)\), now (?P<x>\d+)",
     lambda t, m: f"{m['n']} получает науку: {m['k']} ({reason(m['why'])}), теперь {m['x']}"),
    (NAME + r" upgrades (?P<u>.+) ×(?P<k>\d+) to (?P<v>.+)",
     lambda t, m: f"{m['n']} модифицирует: {m['u']} ×{m['k']} → {m['v']}"),
    (NAME + r" exchanges (?P<k>\d+) (?P<r>wood|iron|gold) for (?P<u>.+) \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} обменивает {m['k']} {RESOURCES_GEN[m['r']]} на: {m['u']} ({m['s']})"),
    (NAME + r" exchanges " + RES + r" for (?P<u>.+)", lambda t, m: f"{m['n']} обменивает {resources(m['res'])} на: {m['u']}"),
    (NAME + r" buys (?P<k>\d+) MP for " + RES + r" \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} покупает {m['k']} ОП за: {resources(m['res'])} ({m['s']})"),
    (HYDRA + r" in (?P<r>.+) dies: a new one is summoned",
     lambda t, m: f"{m['h']} игрока {m['n']} в регионе {m['r']} погибает: призвана новая"),
    (HYDRA + r" moves to (?P<r>.+)", lambda t, m: f"{m['h']} игрока {m['n']} перемещается в регион {m['r']}"),
    (HYDRA + r" dies", lambda t, m: f"{m['h']} игрока {m['n']} погибает"),
    (NAME + r" turns (?P<r>.+) into a lake \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} превращает регион {m['r']} в озёрный ({m['s']})"),
    (NAME + r" curses (?P<r>.+?) \((?P<why>.+)\)", lambda t, m: f"{m['n']} проклинает регион {m['r']} ({reason(m['why'])})"),
    (NAME + r" loses (?P<u>.+) \((?P<why>.+)\)", lambda t, m: f"{m['n']} теряет: {m['u']} ({reason(m['why'])})"),
    (NAME + r" circles a note and starts a new pair", lambda t, m: f"{m['n']} обводит ноту и начинает новую пару"),
    (NAME + r" circles a note and completes a pair", lambda t, m: f"{m['n']} обводит ноту и закрывает пару"),
    (NAME + r" summons (?P<h>.+) into (?P<r>.+)", lambda t, m: f"{m['n']} призывает в регион {m['r']}: {m['h']}"),
    (NAME + r" pays " + RES + r" to raise (?P<a>gather (?:wood|iron|gold)|move|build & hire) by (?P<x>\d+) \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} платит {resources(m['res'])}: уровень действия «{ACTIONS[m['a']]}» +{m['x']} ({m['s']})"),
    (NAME + r" resurrects (?P<u>.+)", lambda t, m: f"{m['n']} воскрешает: {m['u']}"),
    (NAME + r" pays " + RES + r" to upgrade (?P<k>\d+) unit\(s\) \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} платит {resources(m['res'])} за модификацию единиц: {m['k']} ({m['s']})"),
    (NAME + r" pays to avoid the battle in (?P<r>.+) \((?P<s>.+)\)",
     lambda t, m: f"{m['n']} платит, чтобы избежать сражения в регионе {m['r']} ({m['s']})"),
    (r"Winner: (?P<w>.+)", lambda t, m: f"Победитель: {m['w']}"),
    # сервер и запуск сетевой партии (net/server.py, cli.open_table)
    (r"⚠ " + NAME + r" disconnected — waiting for them to reconnect", lambda t, m: f"⚠ {m['n']} отключился — ждём переподключения"),
    (NAME + r" connected \((?P<a>[^)]*)\)", lambda t, m: f"{m['n']} подключился ({m['a']})"),
    (r"Waiting for network players: (?P<s>.+)", lambda t, m: f"Ждём сетевых игроков: {m['s']}"),
    (r"The game is open for network players\. They should run:",
     lambda t, m: "Партия открыта для сетевых игроков. Им нужно выполнить:"),
    (r"  \(or open the graphical client: python -m heroes --gui → Join, address (?P<a>.+)\)",
     lambda t, m: f"  (или в окне: python -m heroes --gui → «Подключиться к сетевой партии», адрес {m['a']})"),
    (r"Seats: (?P<s>.+)\. Over the internet you need to forward port (?P<p>\d+) or share a VPN network \(e\.g\. Tailscale\)\.",
     lambda t, m: f"Места: {m['s']}. Через интернет нужен проброс порта {m['p']} или общая VPN-сеть (например, Tailscale)."),
    (r"Game not started\.", lambda t, m: "Партия не начата."),
    # отказы сервера и конец партии (net/server.py, cli.main)
    (r"Wrong password\.", lambda t, m: "Неверный пароль."),
    (r"All network seats are taken\.", lambda t, m: "Все сетевые места заняты."),
    (r"Protocol version mismatch \(server has (?P<v>\d+)\)\. Update the game\.",
     lambda t, m: f"Версии протокола не совпадают (у хозяина — {m['v']}). Обновите игру."),
    (r"Unknown client mode .*", lambda t, m: "Сервер не знает такого вида клиента."),
    (r"The game was interrupted\.", lambda t, m: "Партия прервана."),
    (r"The host interrupted the game\. It is saved and can be continued\.",
     lambda t, m: "Хозяин прервал партию. Она сохранена, её можно продолжить."),
    (r"Everyone is here — let's start!", lambda t, m: "Все в сборе — начинаем!"),
    (r"Claude players are reviewing the game for their strategy notes…",
     lambda t, m: "Claude-игроки разбирают партию и записывают выводы — не закрывайте окно…"),
    (NAME + r": (?P<o>(?:no changes to|strategy notes|could not write the strategy notes).*)", _lessons),
]


# --- таблички над фишками (mapview) ---------------------------------------------------------------
# Короткая подпись к событию журнала: над чьей фишкой (игрока или его гидры) и что написать.
# Перемещения, «остаётся», итоги и служебные строки табличек не дают.

def _gain(text: str) -> str:
    """«wood 1, gold 2» → «+1(дерево) +2(золото)» значками."""
    if text == "nothing":
        return ""
    return " ".join(f"+{v}{M['krak' if k == 'Крак' else k]}" for k, _, v in (p.rpartition(" ") for p in text.split(", ")))


def _spent(text: str) -> str:
    return "" if text == "nothing" else res({("krak" if k == "Крак" else k): int(v)
                                              for k, _, v in (p.rpartition(" ") for p in text.split(", "))})


STANCE_POPUPS = {"fight": "Сражается", "claim the object": "Претендует на объект", "pass": "Пас"}
POPUPS = [   # (шаблон, чья фишка, подпись); гидры — раньше игроков: «Аня's Гидра wins» подходит и под NAME
    (HYDRA + r" wins: a monster is circled, but there are no rewards", "hydra", lambda m: "Победа (без наград)"),
    (HYDRA + r" attacks the fortress in (?P<r>.+) with strength (?P<s>\d+) and fails", "hydra",
     lambda m: f"Штурм не удался: {m['r']}"),
    (HYDRA + r" captures the fortress in (?P<r>.+) \(strength (?P<s>\d+) vs (?P<f>\d+)\); the loot is lost", "hydra",
     lambda m: f"Крепость {m['r']} захвачена!"),
    (HYDRA + r" fought alone: the monster's loot is lost", "hydra", lambda m: "Добыча теряется"),
    (HYDRA + r" in (?P<r>.+) dies: a new one is summoned", "hydra", lambda m: "Погибает — призвана новая"),
    (HYDRA + r" dies", "hydra", lambda m: "Погибает"),
    (NAME + r" gains the (?P<r>.+?) artifact: (?P<x>.+)", "player", lambda m: f"{M['artifact']} {m['r']}"),
    (NAME + r" gains " + RES + r" \((?P<why>.+)\)", "player", lambda m: _gain(m["res"])),
    (NAME + r" reached (?P<t>\d+) (?P<r>wood|iron|gold|rage) — (?P<vp>\d+) VP circled", "player",
     lambda m: f"{m['t']}{M[m['r']]}: обведено {m['vp']}{M['laurel']}"),
    (NAME + r" in (?P<r>.+): (?P<s>fight|claim the object|pass)", "player", lambda m: STANCE_POPUPS[m["s"]]),
    (NAME + r" wins: a monster is circled, but there are no rewards", "player", lambda m: "Победа (без наград)"),
    (NAME + r" wins", "player", lambda m: "Победа!"),
    (NAME + r" takes the monster's loot", "player", lambda m: "Награда чудовища"),
    (NAME + r" takes the (?P<a>.+) artifact from (?P<q>.+)", "player", lambda m: f"{M['artifact']} {m['a']} — у {m['q']}"),
    (NAME + r" takes from (?P<q>.+): " + RES, "player", lambda m: f"{_gain(m['res'])} — у {m['q']}" if _gain(m["res"]) else ""),
    (NAME + r" attacks the fortress in (?P<r>.+) with strength (?P<s>\d+) and fails", "player",
     lambda m: f"Штурм не удался: {m['r']}"),
    (NAME + r" captures the fortress in (?P<r>.+) \(strength (?P<s>\d+) vs (?P<f>\d+)\)", "player",
     lambda m: f"Крепость {m['r']} захвачена!"),
    (NAME + r" loses to a strength-(?P<k>\d+) monster \(army strength (?P<s>\d+)\)", "player",
     lambda m: f"Поражение: чудовище {m['k']}{M['shield']}"),
    (NAME + r" defeats a strength-(?P<k>\d+) monster \(army strength (?P<s>\d+)\)", "player",
     lambda m: f"Побеждено чудовище {m['k']}{M['shield']}"),
    (NAME + r" builds (?P<b>.+) \(level (?P<l>\d+)\)", "player", lambda m: m["b"]),
    (NAME + r" places the building on the foundation in (?P<r>.+) \((?P<l>\d+) VP\)", "player",
     lambda m: f"{M['foundation']} {m['r']}: {m['l']}{M['laurel']}"),
    (NAME + r" hires: (?P<u>.+)", "player", lambda m: m["u"]),
    (NAME + r" reserves the (?P<o>\w+) in (?P<r>.+) with Башня иллюзий", "player", lambda m: f"Бронь: {m['r']}"),
    (NAME + r" gains (?P<k>\d+) science \((?P<why>.+)\), now (?P<x>\d+)", "player", lambda m: f"+{m['k']}{M['science']}"),
    (NAME + r" upgrades (?P<u>.+) ×(?P<k>\d+) to (?P<v>.+)", "player", lambda m: f"{m['u']} → {m['v']} ×{m['k']}"),
    (NAME + r" exchanges (?P<k>\d+) (?P<r>wood|iron|gold) for (?P<u>.+) \((?P<s>.+)\)", "player",
     lambda m: f"{m['k']}{M[m['r']]} → {m['u']}"),
    (NAME + r" exchanges " + RES + r" for (?P<u>.+)", "player", lambda m: f"{_spent(m['res'])} → {m['u']}"),
    (NAME + r" buys (?P<k>\d+) MP for " + RES + r" \((?P<s>.+)\)", "player", lambda m: f"+{m['k']}{M['mp']}"),
    (NAME + r" turns (?P<r>.+) into a lake \((?P<s>.+)\)", "player", lambda m: f"{M['lake']} {m['r']}"),
    (NAME + r" curses (?P<r>.+?) \((?P<why>.+)\)", "player", lambda m: f"{M['curse']} {m['r']}"),
    (NAME + r" loses (?P<u>.+) \((?P<why>.+)\)", "player", lambda m: f"Потери: {m['u']}"),
    (NAME + r" circles a note and starts a new pair", "player", lambda m: f"{M['note']} Нота"),
    (NAME + r" circles a note and completes a pair", "player", lambda m: f"{M['note']}{M['note']} Пара нот"),
    (NAME + r" summons (?P<h>.+) into (?P<r>.+)", "player", lambda m: f"Призыв: {m['h']}"),
    (NAME + r" pays " + RES + r" to raise (?P<a>gather (?:wood|iron|gold)|move|build & hire) by (?P<x>\d+) \((?P<s>.+)\)",
     "player", lambda m: f"{ACTIONS[m['a']].capitalize()} +{m['x']}"),
    (NAME + r" resurrects (?P<u>.+)", "player", lambda m: f"Воскрешение: {m['u']}"),
    (NAME + r" pays to avoid the battle in (?P<r>.+) \((?P<s>.+)\)", "player", lambda m: "Откуп от сражения"),
]


def popup(names: list[str], line: str) -> tuple[tuple[str, int], str] | None:
    """Строка журнала → ((\"player\" или \"hydra\", место), подпись) или None, если табличка не нужна."""
    for pattern, kind, render in POPUPS:
        m = re.fullmatch(pattern, line, flags=re.S)
        if m and m["n"] in names:
            text = render(m)
            return ((kind, names.index(m["n"])), text) if text else None
    return None
