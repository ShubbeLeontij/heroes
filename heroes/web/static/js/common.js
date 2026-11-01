// Общее для частей страницы: имена игроков, подписи ресурсов и объектов, сила заявки войск
// (повторяет effects.army_strength движка). Страница работает только с данными снимков
// (static и state из heroes/protocol/snapshot.py), без движка.

import { UI, artifactMark, h, icon, mark } from "./rich.js";

export const color = (p) => (p === null || p === undefined ? "var(--muted)" : UI.colors[p % UI.colors.length]);

// «2» + значок дерева… faded — бледные значки (ресурсы уже посещённого региона)
export function res(resources, faded = false) {
  return Object.entries(resources || {}).filter(([, v]) => v)
    .map(([k, v]) => `${v}${mark(faded ? k + "_off" : k)}`).join(" ") || "—";
}

// награда крепости или чудовища: «3» + лавр ПО и добыча значками
export function reward(vp, loot) {
  return `${vp}${mark("laurel")}` + (Object.values(loot || {}).some(Boolean) ? ` + ${res(loot)}` : "");
}

export function plural(n, one, few, many) {
  if (n % 10 === 1 && n % 100 !== 11) return one;
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return few;
  return many;
}

// победные очки по категориям (heroes/scoring.py): заголовок — значком (склад — ресурсы треугольником:
// дерево сверху, железо слева, золото справа), title — словом
export const SCORE_CATEGORIES = { buildings: "здания", foundations: "фундаменты", resources: "склад", battles: "сражения", artifacts: "артефакты" };
const SCORE_MARKS = { buildings: "build", foundations: "foundation", battles: "victory", artifacts: "artifact" };

export function scoreHeads() {
  return Object.entries(SCORE_CATEGORIES).map(([k, t]) =>
    h("th", { title: t }, k === "resources" ? h("span.res-triangle", ["wood", "iron", "gold"].map((r) => icon(r))) : mark(SCORE_MARKS[k])));
}

// условие артефакта значками, как на планшете: цена или сколько нужно фундаментов, побед, гор
export const CONDITION_MARKS = { foundationsBuilt: "foundation", battleWins: "victory", mountainsVisited: "mountain" };

export function conditionIcons(cond) {
  if (cond.condition === "pay") return res(cond.cost);
  return `${cond.count}${mark(CONDITION_MARKS[cond.condition])}`;
}

// «Аня (Люди)»; avatar — с меткой аватарки впереди
export function nameOf(static_, p, avatar = false) {
  if (p === null || p === undefined) return "—";
  const factions = static_.player_factions;
  const name = factions ? `${static_.names[p]} (${static_.factions[factions[p]].name})` : static_.names[p];
  return avatar ? mark(`avatar${p}`) + name : name;
}

export function thinkingText(static_, state, avatar = false) {
  return (state.thinking || []).map(([p, kind, region]) => {   // region — из вопроса (гидра наг стоит не там, где наги)
    const doing = (UI.doing[kind] || "принимает решение").replace("{region}", region || state.players[p].region || "—");
    return `${nameOf(static_, p, avatar)} ${doing}…`;
  }).join("\n");
}

// очерёдность сейчас (common.turn_order)
export function turnOrder(static_, state) {
  const n = static_.names.length, first = state.first_player;
  if (!state.round) return [...Array(n).keys()].map((i) => (((first - 1 - i) % n) + n) % n);
  return [...Array(n).keys()].map((i) => (first + i) % n);
}

// кто будет первым игроком в следующих раундах: [[раунд, место], …] (common.next_first_players)
export function nextFirstPlayers(static_, state, count) {
  const n = static_.names.length, first = state.first_player, now = state.round;
  const shift = now ? 1 : 0, out = [];
  for (let r = now + 1, i = 0; r <= Math.min(now + count, static_.rounds); r++, i++) out.push([r, (first + shift + i) % n]);
  return out;
}

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// где в строке упомянуты игроки: [[начало имени, место], …] — перед ними журнал ставит аватарки.
// Без lookbehind (его нет в Safari до 16.4): граница слова слева — захваченный символ или начало строки.
export function nameSpans(static_, line) {
  const names = [...static_.names].sort((a, b) => b.length - a.length).map(escape);
  if (!names.length) return [];
  const pattern = new RegExp(`(^|[^\\p{L}\\p{N}_])(${names.join("|")})(?![\\p{L}\\p{N}_])`, "gu");
  const spans = [];
  for (let m; (m = pattern.exec(line));) {
    spans.push([m.index + m[1].length, static_.names.indexOf(m[2])]);
    pattern.lastIndex = m.index + m[0].length;
  }
  return spans;
}

export function terrainOf(static_, state, region) {
  return (state.terrain || {})[region] || static_.regions[region].terrain;
}

export function artifactCondition(static_, faction, region) {
  return (static_.factions[faction].artifact_conditions || {})[region] || static_.regions[region].artifact;
}

export function monsterLine(static_, faction) {
  return static_.factions[faction].monsters || static_.monsters;
}

export function buildingText(static_, faction, bid) {
  const b = static_.factions[faction].buildings[bid];
  return b.text_ru || b.text;
}

export function artifactText(static_, faction, region) {
  const f = static_.factions[faction];
  return (f.artifacts_ru || {})[region] || f.artifacts[region];
}

// объект региона с его нынешним положением (common.object_text)
export function objectText(static_, state, region, faction = null) {
  const r = static_.regions[region], obj = r.object;
  let text;
  if (obj === "fortress") {
    const f = r.fortress;
    text = `крепость ${f.strength}${mark("shield")} → ${reward(f.vp, f.loot)}`;
    if (region in state.fortress_owner) text += `; захвачена: ${nameOf(static_, state.fortress_owner[region])}`;
  } else if (obj === "artifact") {
    text = `${artifactMark(region)} ${conditionIcons(faction ? artifactCondition(static_, faction, region) : r.artifact)}`;
    if (region in state.artifact_holder) text += `; у игрока ${nameOf(static_, state.artifact_holder[region])}`;
  } else if (obj === "foundation") {
    const levels = state.players.map((pl, p) => (pl.foundations[region] ? `${nameOf(static_, p)} ${UI.levels[pl.foundations[region]]}` : null))
      .filter(Boolean);
    text = mark("foundation") + (levels.length ? ` ${levels.join(", ")}` : "");
  } else {
    text = `${mark("portal")} (все порталы соседствуют друг с другом)`;
  }
  if (region in state.reserved) text += `; бронь: ${nameOf(static_, state.reserved[region])} (Башня иллюзий)`;
  if (region in (state.cursed || {})) text += `; ${mark("curse")} ${nameOf(static_, state.cursed[region])}`;
  return text;
}

// Лавка бронника: на сколько вырастет сила каждой заявленной единицы при модификации (по убыванию)
export function upgradeGains(units, army, upgrade) {
  const gains = [];
  for (const [unit, n] of Object.entries(army)) {
    const gain = ((upgrade || {}).strength || {})[unit] - units[unit].strength;
    if (gain > 0) for (let i = 0; i < n; i++) gains.push(gain);
  }
  return gains.sort((a, b) => b - a);
}

// Сила заявки army (по умолчанию — всех войск). Повторяет common.army_strength и engine.effects.army_strength.
export function armyStrength(units, army = null, rules = null, upgrade = null, upgrades = 0) {
  army = army || Object.fromEntries(Object.entries(units).map(([u, v]) => [u, v.count]));
  rules = rules || {};
  const doublers = rules.doublers || {};
  const counted = new Set(rules.count_units || []);
  const one = (unit) => (counted.has(unit) ? army[unit] || 0 : units[unit].strength);
  const values = [];
  for (const [u, n] of Object.entries(army)) if (!(u in doublers)) for (let i = 0; i < n; i++) values.push(one(u));
  values.sort((a, b) => b - a);
  const boosts = Object.entries(doublers).reduce((s, [u, targets]) => s + (army[u] || 0) * targets, 0);
  const sum = (xs) => xs.reduce((s, x) => s + x, 0);
  let total = sum(values) + sum(values.slice(0, boosts));
  for (const u of Object.keys(doublers)) if (u in units) total += one(u) * (army[u] || 0);
  if (upgrades) total += sum(upgradeGains(units, army, upgrade).slice(0, upgrades));
  return total + (rules.bonus || 0);
}

// как profiles.winners: больше всего ПО, при равенстве — больше ресурсов на складе
export function winners(state) {
  const players = state.players;
  const best = Math.max(...players.map((pl) => pl.score.total));
  const leaders = players.map((pl, p) => p).filter((p) => players[p].score.total === best);
  const stock = Object.fromEntries(leaders.map((p) => [p, Object.values(players[p].resources).reduce((s, x) => s + x, 0)]));
  const most = Math.max(...Object.values(stock));
  return leaders.filter((p) => stock[p] === most);
}
