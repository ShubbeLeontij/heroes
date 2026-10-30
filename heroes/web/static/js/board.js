// Планшет игрока и сводка «Все игроки» — HTML-карточками,
// которые сами перестраиваются под ширину экрана. На широком экране здания стоят колонками, как на печатном
// планшете, со стрелками зависимостей; на узком — по две в ряд, а вместо стрелок подписано, какое здание нужно.

import { UI, artifactMark, h, mark, markup, rich } from "./rich.js";
import { artifactCondition, color, CONDITION_MARKS, conditionIcons, monsterLine, nameOf, res, reward } from "./common.js";
import { tipAttr } from "./tips.js";

const WIDE_COLUMN = 128;     // уже — колонки печатного планшета не помещаются: карточки по две в ряд
const CATEGORIES = { buildings: "здания", foundations: "фундаменты", resources: "склад", battles: "сражения", artifacts: "артефакты" };
const CATEGORY_MARKS = { foundations: "foundation", battles: "victory", artifacts: "artifact" };   // остальные — словом
const picture = (kind) => `assets/icons/${encodeURIComponent(kind)}_64.png`;

// почему войско пока нельзя нанять: какое здание нужно
function hireNeeds(faction, uid, built) {
  const names = Object.entries(faction.buildings).filter(([bid, b]) => (b.hires || []).includes(uid) && !built.includes(bid)).map(([, b]) => b.name);
  return names.length ? `нужно «${names[0]}»` : "пока недоступен";
}

// сила войска, как на планшете: «1»2» — во что её превращают здания, артефакты и свойства фракции
function strengthParts(unit, base) {
  const power = mark("power"), mods = unit.mods || [];
  if (!mods.length) return [[`${unit.strength}${power}`, ""]];
  const replaced = mods.some((m) => m.active && m.kind === "set");
  const parts = [[`${base}${power}`, replaced ? "off" : ""]];
  for (const m of mods) {
    const note = { pay: ` за ${res(m.cost || {})}`, terrain: ` ${mark(m.terrain) || ""}`, fortress: " против крепости" }[m.kind] || "";
    parts.push([mark("modify"), ""], [`${m.value}${power}${note}`, m.active ? "" : "off"]);
  }
  return parts;
}

export class BoardView {
  constructor(container) {
    this.el = container;
    this.static = null;
    this.state = null;
    this.player = null;
    this.you = null;
    this.pick = null;      // выбор здания: {owner, options, selected, onPick}
    this.monsters = null;  // выбор чудовища: {owner, options, picked, onPick}
    new ResizeObserver(() => this.arrows()).observe(container);
  }

  show(static_, state, player, you = null) {
    Object.assign(this, { static: static_, state, player, you });
    this.redraw();
  }

  // здания игрока owner из options можно выбрать тапом; повторный тап снимает выбор (onPick(null))
  selectMode(owner, options = null, selected = null, onPick = null) {
    this.pick = owner === null ? null : { owner, options, selected, onPick };
    this.redraw();
    if (this.picking()) this.el.querySelector(".buildings")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  setPicked(bid) {
    if (this.pick) this.pick.selected = bid;
    this.redraw();
  }

  selectMonsters(owner, options = null, onPick = null) {
    this.monsters = owner === null ? null : { owner, options, picked: null, onPick };
    this.redraw();
  }

  setMonster(index) {
    if (this.monsters) this.monsters.picked = index;
    this.redraw();
  }

  picking() {
    return Boolean(this.pick && this.pick.options && this.pick.options.size && this.player === this.pick.owner);
  }

  pickingMonster() {
    return Boolean(this.monsters && this.monsters.options && this.monsters.options.size && this.player === this.monsters.owner);
  }

  redraw() {
    if (!this.static || !this.state || this.player === null) {
      this.el.replaceChildren();
      return;
    }
    const scroll = this.el.scrollTop;
    const static_ = this.static, pl = this.state.players[this.player];
    const faction = static_.factions[pl.faction];
    this.el.replaceChildren(h("div.board", this.stock(pl, faction), this.specials(pl, faction), this.rules(faction),
      this.units(pl, faction), this.buildings(pl, faction), this.artifacts(pl, faction), this.monsterLine(pl)));
    this.el.scrollTop = scroll;
    requestAnimationFrame(() => this.arrows());
  }

  // Склад; уникальные ресурсы фракции (ярость) — такими же ячейками
  stock(pl, faction) {
    const noVp = faction.no_resource_vp;
    const boxes = ["wood", "iron", "gold"].map((k) => [k, pl.resources[k], noVp ? [] : this.static.resource_vp]);
    for (const [k, steps] of Object.entries(faction.tokens || {})) boxes.push([k, (pl.tokens || {})[k] || 0, steps]);
    const cells = boxes.map(([k, amount, steps]) => {
      const marks = pl.resource_marks[k] || [];
      return h("div.stock-cell", { style: { "--res": UI.resource_colors[k] || "var(--muted)" }, ...tipAttr("stock", k) },
        h("span.stock-amount", mark(k), String(amount)),
        h("span.stock-steps", steps.map(([threshold, vp]) => h("span.step" + (marks.includes(threshold) ? ".done" : ""),
          h("span.threshold", String(threshold)), h("span.vp", `${vp}${mark("laurel")}`)))));
    });
    return h("section", h("h3", "Склад"), h("div.stock", cells),
      noVp ? h("p.muted.small", "За ресурсы на складе ПО не начисляются.") : null);
  }

  // особые шкалы фракции: наука гномов, ноты и гидра наг, проклятия нежити
  specials(pl, faction) {
    const out = h("section.specials");
    const science = faction.science;
    if (science) {
      const have = (pl.tokens || {}).science || 0;
      const free = Object.fromEntries(Object.entries(science.free_build || {}).map(([lvl, t]) => [t, UI.levels[lvl]]));
      const cells = [];
      for (let i = 0; i <= science.max; i++) {
        cells.push(h("span.science-cell" + (i <= have ? ".have" : "") + (i in free ? ".free" : "") + (i === have ? ".now" : ""),
          String(i), i in free ? h("small", free[i]) : null));
      }
      out.append(h("div.science", h("b", `Наука ${have}${mark("science")}`), h("div.science-scale", cells)));
    }
    if (faction.notes) {
      const pairs = (pl.notes || []).map((n) => mark("note").repeat(n) + "○".repeat(2 - n)).join("   ");
      out.append(h("p.bold", `${mark("hydra")} ${pairs}`));
    }
    if (faction.hydra) {
      const unit = faction.units[faction.hydra].name, where = pl.hydra;
      let text;
      if (where) {
        const apart = where !== pl.region ? "отдельно от вас" : "вместе с вами";
        text = `${mark("hydra")} ${unit}: в регионе ${where} (${apart}), ${pl.units[faction.hydra].strength}${mark("power")}`;
      } else {
        text = `${mark("hydra")} ${unit}: на карте нет`;
      }
      out.append(h("p", text + `; призвана раз: ${pl.hydra_summons || 0}`));
    }
    const cursed = Object.entries(this.state.cursed || {}).filter(([, q]) => q === this.player).map(([r]) => r).sort();
    if (cursed.length || faction.curses) out.append(h("p.bold", `${mark("curse")} Проклятые регионы: ${cursed.join(", ") || "пока нет"}`));
    return out.childNodes.length ? out : null;
  }

  // свойства фракции — блоки планшета, которые действуют всю партию (кроме тех, что уже показывает склад)
  rules(faction) {
    const rules = Object.values(faction.rules || {}).filter((rule) => !rule.in_stock);
    if (!rules.length) return null;
    return h("section", h("h3", "Свойства фракции"),
      rules.map((rule) => h("div.card.rule", h("b", rule.name), h("p", markup(rule.text_board)),
        this.upgradePrices(faction, rule.unit_upgrades || []))));
  }

  // цены модификации войск (орки) по ступеням: «Разведчик → Громила 1{rage}, Громила → Взрыватель 3{rage}»;
  // модификация через ступень стоит их сумму — её не пишем
  upgradePrices(faction, paths) {
    const order = Object.keys(faction.units);
    const steps = paths.filter((x) => order.indexOf(x.to) - order.indexOf(x.from) === 1);
    if (!steps.length) return null;
    return h("p", steps.map((x, i) => [i ? ", " : "",
      h("span.upgrade-step", `${faction.units[x.from].name} → ${faction.units[x.to].name} ${res(x.cost)}`)]));
  }

  units(pl, faction) {
    const rows = Object.entries(pl.units).map(([uid, u]) => {
      let status, cls = "muted";
      if ((faction.resource_units || []).includes(uid)) status = "обмен ресурсов в начале строительства";
      else if (uid === faction.hydra) status = `призыв: ${mark("note").repeat(2)}`;
      else if (u.hireable) {
        status = res(u.hire_cost);
        cls = "good";
      } else if (Object.keys(u.hire_cost || {}).length) status = res(u.hire_cost) + " — " + hireNeeds(faction, uid, pl.built);
      else status = "наём недоступен";
      return h("div.unit",
        h("b.unit-name", faction.units[uid].name), h("b.unit-count" + (u.count ? "" : ".muted"), `×${u.count}`),
        h("span.unit-strength", strengthParts(u, faction.units[uid].strength).map(([text, c]) => rich(text, "span", c))),
        h("span.unit-status." + cls, status));
    });
    return h("section", h("h3", `Войска — всего ${pl.army_strength}${mark("power")}`), h("div.units", rows));
  }

  buildings(pl, faction) {
    const buildings = faction.buildings, built = new Set(pl.built), can = new Set(pl.buildable);
    const layout = faction.building_layout;   // клетки печатного планшета: ряды сверху вниз, null — пустая клетка
    const cols = Math.max(1, ...layout.map((row) => row.length));
    const picking = this.picking();
    // клетки печатного планшета, если помещаются; иначе — по две в ряд в том же порядке
    const width = this.el.clientWidth || 360;
    const wide = cols * WIDE_COLUMN <= width - 24;
    const grid = h("div.building-grid" + (wide ? ".wide" : ".narrow"), { style: { "--cols": wide ? cols : 2 } });
    this.cards = {};
    layout.forEach((row, r) => row.forEach((bid, c) => {
      if (bid === null) return;
      const b = buildings[bid];
      const state = built.has(bid) ? "built" : can.has(bid) ? "can" : "locked";
      const selectable = picking && this.pick.options.has(bid);
      const needs = !wide && b.requires.length ? h("small.muted", "нужно: " + b.requires.map((q) => buildings[q].name).join(", ")) : null;
      const card = h(`div.card.building.${state}` + (selectable ? ".selectable" : "") + (selectable && bid === this.pick.selected ? ".picked" : ""),
        { onclick: selectable ? () => this.pickBuilding(bid) : null },
        h("b", `${UI.levels[b.level]} ${b.name}` + (built.has(bid) ? " ✓" : "")),
        h("small.muted", `${b.vp}${mark("laurel")} · цена ${res(b.cost)}`),
        h("p", markup(b.text_board || b.text_ru || b.text)), needs);
      if (wide) {
        card.style.gridColumn = String(c + 1);
        card.style.gridRow = String(r + 1);
      }
      this.cards[bid] = card;
      grid.append(card);
    }));
    this.arrowLayer = wide ? h("div.arrows") : null;
    this.arrowsFor = wide ? { buildings, built } : null;
    const wrapEl = h("div.building-wrap", grid, this.arrowLayer);
    return h("section.buildings", h("h3", "Здания", picking ? h("span.pick-hint", " — тапните здание, чтобы выбрать его для строительства") : null), wrapEl);
  }

  pickBuilding(bid) {
    this.pick.selected = bid === this.pick.selected ? null : bid;
    this.pick.onPick?.(this.pick.selected);
    this.redraw();
  }

  // стрелки зависимостей: от верха нужного здания к низу зависящего от него (после раскладки)
  arrows() {
    const layer = this.arrowLayer;
    if (!layer || !layer.isConnected || !this.arrowsFor) return;
    const base = layer.parentElement.getBoundingClientRect();
    const svgEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svgEl.setAttribute("width", base.width);
    svgEl.setAttribute("height", base.height);
    const { buildings, built } = this.arrowsFor;
    for (const [bid, b] of Object.entries(buildings)) {
      for (const req of b.requires) {
        const r = this.cards[req]?.getBoundingClientRect(), d = this.cards[bid]?.getBoundingClientRect();
        if (!r || !d) continue;
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        const attrs = { x1: r.left + r.width / 2 - base.left, y1: r.top - base.top, x2: d.left + d.width / 2 - base.left, y2: d.bottom - base.top + 1,
          stroke: built.has(req) ? "var(--good)" : "#a0957f", "stroke-width": 2, "marker-end": `url(#${built.has(req) ? "arrow-good" : "arrow-plain"})` };
        for (const [k, v] of Object.entries(attrs)) line.setAttribute(k, v);
        svgEl.append(line);
      }
    }
    svgEl.insertAdjacentHTML("afterbegin", `<defs>
      <marker id="arrow-good" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--good)"/></marker>
      <marker id="arrow-plain" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10z" fill="#a0957f"/></marker></defs>`);
    layer.replaceChildren(svgEl);
  }

  // артефакты — как на планшете: условие, рисунок, свойство; под ними — у кого артефакт
  artifacts(pl, faction) {
    const static_ = this.static, state = this.state;
    const board = faction.artifacts_board || faction.artifacts_ru || faction.artifacts;
    const cards = Object.keys(faction.artifacts).map((region) => {
      const holder = state.artifact_holder[region];
      let status;
      if (holder !== undefined) status = holder === this.you ? "ваш" : who(static_, holder);   // узкая карточка: аватарка и имя
      else if (region in state.reserved) status = `бронь: ${who(static_, state.reserved[region])}`;
      else status = "свободен";
      const card = h("div.card.artifact" + (holder !== undefined ? ".held" : ""), tipAttr("artifact", region),
        h("b.cond", artifactCost(static_, pl.faction, region)),
        h("img.artifact-picture", { src: picture(`artifact_${region}`), alt: region, draggable: false }),
        h("p", markup(board[region])), h("small.status" + (holder !== undefined ? ".bold" : ".muted"), status));
      if (holder !== undefined) card.style.setProperty("--holder", color(holder));
      return card;
    });
    return h("section", h("h3", "Артефакты"), h("div.artifacts", cards));
  }

  // линия чудовищ — как на планшете: сила, рисунок, ПО и добыча
  monsterLine(pl) {
    const line = monsterLine(this.static, pl.faction), picking = this.pickingMonster();
    const cards = line.map((m, i) => {
      const marked = pl.monsters[i];
      const selectable = picking && this.monsters.options.has(i);
      return h("div.card.monster" + (marked === "monster" ? ".beaten" : marked === "pvp" ? ".pvp" : "")
        + (selectable ? ".selectable" : "") + (selectable && i === this.monsters.picked ? ".picked" : ""),
      { onclick: selectable ? () => this.pickMonster(i) : null, ...tipAttr("monster", this.player, i) },
      h("b", `${m.strength}${mark("shield")}` + (marked === "monster" ? " ✓" : marked === "pvp" ? " ⚔" : "")),
      h("img.monster-picture", { src: picture(`monster_${i}`), alt: "", draggable: false }),
      h("span.monster-reward", h("b", `${m.vp}${mark("laurel")}`), h("span.loot", Object.entries(m.loot).map(([k, v]) => h("span", res({ [k]: v }))))));
    });
    return h("section.monster-section", h("h3", "Линия чудовищ (✓ — побеждено чудовище, ⚔ — побеждён игрок)",
      picking ? h("span.pick-hint", " — тапните чудовище, чтобы выбрать его для боя") : null), h("div.monsters", cards));
  }

  pickMonster(i) {
    this.monsters.picked = i;
    this.monsters.onPick?.(i);
    this.redraw();
  }
}

// цена или условие артефакта для фракции; у эльфов с Гнездовьем — и за дерево, через «/»
function artifactCost(static_, faction, region) {
  const cond = conditionIcons(artifactCondition(static_, faction, region));
  const wood = static_.factions[faction].artifact_wood;
  return wood ? `${cond} / ${res({ wood: wood.prices[region] })}` : cond;
}

// --- подсказки (tips.js): артефакт, чудовище, ресурсы ----------------------------------------------

export function tipContent(static_, state, kind, args, you = null) {
  if (kind === "artifact") return artifactTip(static_, state, args[0], you);
  if (kind === "object") return objectTip(static_, state, args[0], you);
  if (kind === "monster") return monsterTip(static_, state, Number(args[0]), Number(args[1]));
  if (kind === "stock") return stockTip(static_, state, args[0]);
  if (kind === "rounds") return roundsTip(static_, state, you);
  return null;
}

const who = (static_, p) => mark(`avatar${p}`) + static_.names[p];   // коротко: аватарка и имя
const tipRow = (static_, p, ...lines) => h("div.tip-row", { style: { "--seat": color(p) } }, h("b", nameOf(static_, p, true)), lines);

// регион, у кого артефакт, и для каждого игрока: цена или условие, сколько уже есть, что даёт
function artifactTip(static_, state, region, you) {
  if (!(region in static_.regions)) return null;
  const holder = state.artifact_holder[region], reserver = state.reserved[region];
  let status = h("div.muted", "свободен");   // ✗ — почему недоступен
  if (holder !== undefined) status = holder === you ? h("div.good", "✓ ваш") : h("div.bad", `✗ взят: ${who(static_, holder)}`);
  else if (reserver !== undefined) {
    status = reserver === you ? h("div.good", "✓ ваша бронь (Башня иллюзий)") : h("div.bad", `✗ бронь: ${who(static_, reserver)} (Башня иллюзий)`);
  }
  const rows = state.players.map((pl, p) => {
    const faction = static_.factions[pl.faction];
    const effect = (faction.artifacts_board || faction.artifacts_ru || faction.artifacts)[region];
    const kind = artifactCondition(static_, pl.faction, region).condition === "pay" ? "цена" : "условие";
    return tipRow(static_, p, h("div", `${kind} ${artifactCost(static_, pl.faction, region)} · ${conditionProgress(static_, state, p, region)}`),
      effect ? h("div.small", markup(effect)) : null);
  });
  return h("div", h("div.tip-head", h("img.tip-picture", { src: picture(`artifact_${region}`), alt: "" }),
    h("div", h("b", region), status)), rows);
}

// объект региона на карте и почему он недоступен: крепость захвачена (кем), объект забронирован Башней иллюзий (кем)
function objectTip(static_, state, region, you) {
  const r = static_.regions[region];
  if (!r) return null;
  const what = r.object === "fortress"
    ? `крепость ${r.fortress.strength}${mark("shield")} → ${reward(r.fortress.vp, r.fortress.loot)}`
    : `${mark(r.object)} ${UI.objects[r.object]}`;
  const lines = [];   // ✗ — почему недоступен
  if (r.object === "fortress" && region in state.fortress_owner) {
    lines.push(h("div.bad", `✗ захвачена: ${who(static_, state.fortress_owner[region])}`));
  }
  if (region in state.reserved) {
    const p = state.reserved[region];
    lines.push(p === you ? h("div.good", "✓ ваша бронь (Башня иллюзий): доступно только вам")
      : h("div.bad", `✗ бронь: ${who(static_, p)} (Башня иллюзий)`));
  }
  if (r.object === "foundation") {   // у кого что вписано
    const rows = state.players.map((pl, p) => (pl.foundations[region] ? tipRow(static_, p, h("div", `вписан ${UI.levels[pl.foundations[region]]}`)) : null));
    if (rows.some(Boolean)) lines.push(rows);
    else if (!lines.length) lines.push(h("div.muted", "ничего не вписано"));
  }
  return h("div", h("b", region), h("div", what), lines.length ? lines : h("div.muted", "свободно"));
}

// чудовище на этом месте линии: у кого оно побеждено и что даёт (у наг — своя линия)
function monsterTip(static_, state, owner, i) {
  const line = monsterLine(static_, state.players[owner].faction);
  if (!line[i]) return null;
  const rows = state.players.map((pl, p) => {
    const m = monsterLine(static_, pl.faction)[i], marked = pl.monsters[i];
    if (!m) return null;
    const status = marked === "monster" ? "✓ побеждено" : marked === "pvp" ? "⚔ обведено: побеждён игрок" : "не побеждено";
    return tipRow(static_, p, h("div", `${m.strength}${mark("shield")} → ${reward(m.vp, m.loot)}`),
      h("div.small" + (marked ? ".bold" : ".muted"), status));
  });
  return h("div", h("div.tip-head", h("img.tip-picture", { src: picture(`monster_${i}`), alt: "" }),
    h("div", h("b", `Чудовище ${i + 1} из ${line.length}`), h("div.muted", "у каждого — в своей линии"))), rows);
}

// кто первым ходит в этом и во всех следующих раундах: первый игрок сдвигается на одного по кругу каждый раунд
function roundsTip(static_, state, you) {
  const n = static_.names.length, from = Math.max(1, state.round);
  const rows = [];
  for (let r = from; r <= static_.rounds; r++) {
    const p = (static_.first_player + r - 1) % n, now = r === state.round ? ".now" : "";
    rows.push(h("tr", h("td" + now, `Раунд ${r}`),
      h("th.player" + now, { style: { "--seat": color(p) } }, nameOf(static_, p, true) + (p === you ? " (вы)" : ""))));
  }
  return h("div", h("b", "Кто ходит первым"), h("table.tip-table", h("tbody", rows)));
}

// ресурсы всех игроков; столбец того, на который навели, выделен
function stockTip(static_, state, key) {
  const players = state.players;
  const kinds = ["wood", "iron", "gold"];
  for (const pl of players) for (const k of Object.keys(static_.factions[pl.faction].tokens || {})) if (!kinds.includes(k)) kinds.push(k);
  const now = (k) => (k === key ? ".now" : "");
  return h("table.tip-table",
    h("thead", h("tr", h("th"), kinds.map((k) => h("th" + now(k), mark(k))))),
    h("tbody", players.map((pl, p) => h("tr", h("th.player", { style: { "--seat": color(p) } }, nameOf(static_, p, true)),
      kinds.map((k) => {
        const own = ["wood", "iron", "gold"].includes(k) || k in (static_.factions[pl.faction].tokens || {});
        return h("td" + now(k), own ? String((k in pl.resources ? pl.resources : pl.tokens || {})[k] || 0) : "—");
      })))));
}

// --- сводка «Все игроки» ------------------------------------------------------------------------

// насколько игрок выполнил условие артефакта (boardview.condition_progress)
export function conditionProgress(static_, state, p, region) {
  const pl = state.players[p], holder = state.artifact_holder[region];
  if (holder === p) return `${artifactMark(region)} взят`;
  const cond = artifactCondition(static_, pl.faction, region), kind = cond.condition;
  let have, need;
  if (kind === "pay") {
    const stock = pl.holdings || pl.resources;
    have = Math.min(...Object.keys(cond.cost).map((k) => stock[k] || 0));
    need = Math.max(...Object.values(cond.cost));
  } else {
    need = cond.count;
    have = {
      foundationsBuilt: () => Object.values(pl.foundations).filter(Boolean).length,
      battleWins: () => pl.victories,
      mountainsVisited: () => (pl.mountains ? pl.mountains.length : pl.visited.filter((r) => static_.regions[r].terrain === "mountain").length),
    }[kind]();
  }
  const icons = kind === "pay" ? Object.keys(cond.cost).map((k) => mark(k)).join("") : mark(CONDITION_MARKS[kind]);
  let met = have >= need;
  let text = `${have}/${need}${icons}`;
  const wood = static_.factions[pl.faction].artifact_wood;
  if (wood) {   // Гнездовье эльфов: «7/12(дерево) или 7/5(дерево), грифоны 3/5»
    const price = wood.prices[region], stock = pl.resources.wood, griffins = pl.units[wood.unit].count;
    const unitName = static_.factions[pl.faction].units[wood.unit].name.toLowerCase();
    text += ` или ${stock}/${price}${mark("wood")}, ${unitName}ы ${griffins}/${price}`;
    const builtNest = pl.built.includes(wood.building);
    if (!builtNest) text += " (нет Гнездовья)";
    met = met || (builtNest && stock >= price && griffins >= price);
  }
  return text + (met && holder === undefined ? " ✓" : "");
}

function specialsCell(static_, state, p) {
  const pl = state.players[p], faction = static_.factions[pl.faction];
  const parts = Object.entries(pl.tokens || {}).map(([k, v]) => `${mark(k)}${v}`);
  if (faction.notes) parts.push(`${mark("note")}${(pl.notes || []).reduce((s, n) => s + n, 0)}/${2 * faction.notes}`);
  if (faction.hydra) parts.push(`${mark("hydra")}${pl.hydra || "нет"}`);
  const cursed = Object.values(state.cursed || {}).filter((q) => q === p).length;
  if (cursed || faction.curses) parts.push(`${mark("curse")}${cursed}`);
  return parts.join("  ");
}

// Сводка по всем игрокам — пять таблиц; тап по строке — планшет и карта игрока.
export function playersSummary(static_, state, you, onOpen) {
  const players = state.players;
  const special = Object.values(static_.factions).some((f) => (f.tokens && Object.keys(f.tokens).length) || f.science || f.notes || f.curses);
  const artifacts = Object.entries(static_.regions).filter(([, info]) => info.object === "artifact").map(([r, info]) => [r, info.artifact]);
  const nameCell = (p) => h("th.player", { style: { "--seat": color(p) } },
    `${nameOf(static_, p, true)}` + (p === you ? " — вы" : "") + (p === state.first_player ? " ★" : ""));
  const table = (title, head, row) => h("section.summary-table", h("h3", title), h("div.scroll-x", h("table",
    h("thead", h("tr", h("th", "Игрок"), head)),
    h("tbody", players.map((pl, p) => h("tr", { onclick: () => onOpen(p) }, nameCell(p), row(pl, p).map((v) => h("td", v))))))));
  return h("div.players",
    table("Регион, ресурсы и войска",
      [h("th", "Регион"), h("th", mark("wood")), h("th", mark("iron")), h("th", mark("gold")), h("th", "Войска"), h("th", mark("power")),
        special ? h("th", "Особое") : null],
      (pl, p) => {
        const faction = static_.factions[pl.faction];
        const units = Object.entries(pl.units).filter(([, u]) => u.count).map(([uid, u]) => `${faction.units[uid].name} ${u.count}`).join(", ");
        return [pl.region || "—", ...["wood", "iron", "gold"].map((k) => String(pl.resources[k])), units || "нет", String(pl.army_strength),
          ...(special ? [specialsCell(static_, state, p)] : [])];
      }),
    table("Победные очки", [...Object.entries(CATEGORIES).map(([k, t]) => h("th", { title: t }, k in CATEGORY_MARKS ? mark(CATEGORY_MARKS[k]) : t[0].toUpperCase() + t.slice(1))), h("th", mark("laurel"))],
      (pl) => [...Object.keys(CATEGORIES).map((k) => String(pl.score[k])), h("b", String(pl.score.total))]),
    table("Здания по уровням", [1, 2, 3, 4].map((lvl) => h("th", UI.levels[lvl])), (pl) => {
      const faction = static_.factions[pl.faction];
      return [1, 2, 3, 4].map((lvl) => pl.built.filter((b) => faction.buildings[b].level === lvl).map((b) => faction.buildings[b].name).join(", ") || "—");
    }),
    table("Крепости и артефакты", [h("th", { title: "Победы в сражениях" }, mark("victory")), h("th", "Захваченные крепости"), h("th", "Артефакты")],
      (pl) => [String(pl.victories), pl.fortresses.join(", ") || "—",
        pl.artifacts.length ? h("span.art-list", pl.artifacts.map((r) => h("span", h("img.ic.art", { src: picture(`artifact_${r}`), alt: r, ...tipAttr("artifact", r) })))) : "—"]),
    table("Условия артефактов", artifacts.map(([r, a]) => h("th.art-head", h("img.ic.art", { src: picture(`artifact_${r}`), alt: r, ...tipAttr("artifact", r) }),
      h("small", conditionIcons(a)))), (pl, p) => artifacts.map(([r]) => conditionProgress(static_, state, p, r))),
    h("p.muted.small", "Тап по строке — планшет и карта игрока."));
}
