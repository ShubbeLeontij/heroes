// Планшет игрока и сводка «Все игроки» — то же, что heroes/gui/boardview.py окна, но HTML-карточками,
// которые сами перестраиваются под ширину экрана. На широком экране здания стоят колонками, как на печатном
// планшете, со стрелками зависимостей; на узком — по две в ряд, а вместо стрелок подписано, какое здание нужно.

import { UI, h, mark, markup, rich } from "./rich.js";
import { artifactCondition, color, monsterLine, nameOf, res } from "./common.js";

const MAX_COLUMNS = 5;
const WIDE_COLUMN = 128;     // уже — колонки печатного планшета не помещаются: карточки по две в ряд
const CATEGORIES = { buildings: "здания", foundations: "фундаменты", resources: "склад", battles: "сражения", artifacts: "артефакты" };
const RESOURCE_GENITIVE = { wood: "дерева", iron: "железа", gold: "золота" };
const picture = (kind) => `assets/icons/${encodeURIComponent(kind)}_64.png`;

// условие артефакта значками, как на планшете
function conditionIcons(cond) {
  if (cond.condition === "pay") return res(cond.cost);
  return `${cond.count}` + mark({ foundationsBuilt: "foundation", battleWins: "victory", mountainsVisited: "mountain" }[cond.condition]);
}

// почему войско пока нельзя нанять: какое здание нужно
function hireNeeds(faction, uid, built) {
  const names = Object.entries(faction.buildings).filter(([bid, b]) => (b.hires || []).includes(uid) && !built.includes(bid)).map(([, b]) => b.name);
  return names.length ? `нужно здание «${names[0]}»` : "пока недоступен";
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

// колонки зданий, как на печатном планшете (boardview.building_columns)
export function buildingColumns(buildings) {
  const columns = {};
  for (const level of [4, 3, 2, 1]) {
    const taken = new Set();
    const row = Object.keys(buildings).filter((bid) => buildings[bid].level === level);
    for (const bid of row) {
      const wanted = Object.entries(buildings).filter(([d, b]) => b.requires.includes(bid) && d in columns).map(([d]) => columns[d]);
      const free = wanted.filter((col) => !taken.has(col));
      if (free.length) {
        columns[bid] = free[0];
        taken.add(free[0]);
      }
    }
    for (const bid of row) {
      if (bid in columns) continue;
      let col = 0;
      while (taken.has(col)) col++;
      columns[bid] = col;
      taken.add(col);
    }
  }
  return columns;
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
      return h("div.stock-cell", { style: { "--res": UI.resource_colors[k] || "var(--muted)" } },
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
          String(i), i in free ? h("small", `${free[i]}${mark("build")}`) : null));
      }
      out.append(h("div.science", h("b", `Наука ${have}${mark("science")}`), h("div.science-scale", cells)));
    }
    if (faction.notes) {
      const pairs = (pl.notes || []).map((n) => mark("note").repeat(n) + "○".repeat(2 - n)).join("   ");
      out.append(h("p.bold", `Ноты гидры:  ${pairs}`));
    }
    if (faction.hydra) {
      const unit = faction.units[faction.hydra].name, where = pl.hydra;
      let text;
      if (where) {
        const apart = where !== pl.region ? "отдельно от вас" : "вместе с вами";
        text = `${mark("hydra")} ${unit}: в регионе ${where} (${apart}), сила ${pl.units[faction.hydra].strength}${mark("power")}`;
      } else {
        text = `${mark("hydra")} ${unit}: на карте нет`;
      }
      out.append(h("p", text + `; призвана раз: ${pl.hydra_summons || 0}`));
    }
    const cursed = Object.entries(this.state.cursed || {}).filter(([, q]) => q === this.player).map(([r]) => r).sort();
    if (cursed.length || faction.curses) out.append(h("p.bold", `${mark("curse")} Проклятые регионы: ${cursed.join(", ") || "пока нет"}`));
    return out.childNodes.length ? out : null;
  }

  // свойства фракции — блоки планшета, которые действуют всю партию
  rules(faction) {
    const rules = Object.values(faction.rules || {});
    if (!rules.length) return null;
    return h("section", h("h3", "Свойства фракции"),
      rules.map((rule) => h("div.card.rule", h("b", rule.name), h("p", markup(rule.text_board)))));
  }

  units(pl, faction) {
    const rows = Object.entries(pl.units).map(([uid, u]) => {
      let status, cls = "muted";
      if ((faction.resource_units || []).includes(uid)) status = "обмен ресурсов в начале строительства";
      else if (uid === faction.hydra) status = "призыв нотами";
      else if (u.hireable) {
        status = "наём " + res(u.hire_cost);
        cls = "good";
      } else if (Object.keys(u.hire_cost || {}).length) status = "наём " + res(u.hire_cost) + " — " + hireNeeds(faction, uid, pl.built);
      else status = "наём недоступен";
      return h("div.unit",
        h("b.unit-name", faction.units[uid].name), h("b.unit-count" + (u.count ? "" : ".muted"), `×${u.count}`),
        h("span.unit-strength", strengthParts(u, faction.units[uid].strength).map(([text, c]) => rich(text, "span", c))),
        h("span.unit-status." + cls, status));
    });
    return h("section", h("h3", `Войска — общая сила ${pl.army_strength}${mark("power")}`), h("div.units", rows));
  }

  buildings(pl, faction) {
    const buildings = faction.buildings, built = new Set(pl.built), can = new Set(pl.buildable);
    const columns = buildingColumns(buildings);
    const cols = Math.max(0, ...Object.values(columns)) + 1;
    const picking = this.picking();
    // колонки печатного планшета, если помещаются; иначе — по две в ряд
    const width = this.el.clientWidth || 360;
    const wide = Math.min(cols, MAX_COLUMNS) * WIDE_COLUMN <= width - 24;
    const wrap = cols > MAX_COLUMNS;
    const grid = h("div.building-grid" + (wide ? ".wide" : ".narrow"), { style: { "--cols": wide ? Math.min(cols, MAX_COLUMNS) : 2 } });
    this.cards = {};
    let row = 1;
    for (const level of [4, 3, 2, 1]) {
      const ids = Object.keys(buildings).filter((bid) => buildings[bid].level === level).sort((a, b) => columns[a] - columns[b]);
      const step = wide && wrap ? MAX_COLUMNS : ids.length || 1;
      for (let start = 0; start < ids.length; start += step) {
        ids.slice(start, start + step).forEach((bid, i) => {
          const b = buildings[bid];
          const state = built.has(bid) ? "built" : can.has(bid) ? "can" : "locked";
          const selectable = picking && this.pick.options.has(bid);
          const needs = !wide && b.requires.length ? h("small.muted", "нужно: " + b.requires.map((r) => buildings[r].name).join(", ")) : null;
          const card = h(`div.card.building.${state}` + (selectable ? ".selectable" : "") + (selectable && bid === this.pick.selected ? ".picked" : ""),
            { onclick: selectable ? () => this.pickBuilding(bid) : null },
            h("b", `${UI.levels[level]} · ${b.name}` + (built.has(bid) ? " ✓" : "")),
            h("small.muted", `${b.vp}${mark("laurel")} · цена ${res(b.cost)}`),
            h("p", markup(b.text_board || b.text_ru || b.text)), needs);
          if (wide) {
            card.style.gridColumn = String((wrap ? i : columns[bid]) + 1);
            card.style.gridRow = String(row);
          }
          this.cards[bid] = card;
          grid.append(card);
        });
        row++;
      }
    }
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
      if (holder !== undefined) status = holder === this.you ? "ваш" : `у игрока ${nameOf(static_, holder, true)}`;
      else if (region in state.reserved) status = `бронь: ${nameOf(static_, state.reserved[region], true)}`;
      else status = "свободен";
      let cond = conditionIcons(artifactCondition(static_, pl.faction, region));
      const wood = faction.artifact_wood;
      if (wood) cond += " / " + res({ wood: wood.prices[region] });   // Гнездовье эльфов: и за дерево, через «/»
      const card = h("div.card.artifact" + (holder !== undefined ? ".held" : ""), h("b.cond", cond),
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
      { onclick: selectable ? () => this.pickMonster(i) : null },
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

// --- сводка «Все игроки» ------------------------------------------------------------------------

const CONDITION_SHORT = {
  pay: (a) => "плата: " + Object.entries(a.cost).map(([k, v]) => `${v} ${RESOURCE_GENITIVE[k] || UI.resources[k] || k}`).join(", "),
  foundationsBuilt: (a) => `${a.count} фундамента со зданиями`,
  battleWins: (a) => `${a.count} побед в сражениях`,
  mountainsVisited: (a) => `${a.count} горных региона`,
};

// насколько игрок выполнил условие артефакта (boardview.condition_progress)
function conditionProgress(static_, state, p, region) {
  const pl = state.players[p], holder = state.artifact_holder[region];
  if (holder === p) return "✦ взят";
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
  const unit = kind !== "pay" || Object.keys(cond.cost).every((k) => ["wood", "iron", "gold"].includes(k)) ? ""
    : " (" + Object.keys(cond.cost).map((k) => UI.resources[k] || k).join(", ") + ")";
  let met = have >= need;
  let text = `${have} / ${need}${unit}`;
  const wood = static_.factions[pl.faction].artifact_wood;
  if (wood) {   // Гнездовье эльфов: «дерево 7/12 или 7/5, грифоны 3/5»
    const price = wood.prices[region], stock = pl.resources.wood, griffins = pl.units[wood.unit].count;
    const unitName = static_.factions[pl.faction].units[wood.unit].name.toLowerCase();
    const same = kind === "pay" && Object.keys(cond.cost).length === 1 && "wood" in cond.cost;
    if (kind === "pay") text = `${UI.resources[Object.keys(cond.cost)[0]]} ${have}/${need}`;
    text += ` или ${same ? "" : "дерево "}${stock}/${price}, ${unitName}ы ${griffins}/${price}`;
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

// Сводка по всем игрокам — пять таблиц, как вкладка «Все игроки» окна; тап по строке — планшет и карта игрока.
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
    table("Победные очки", [...Object.values(CATEGORIES).map((t) => h("th", t[0].toUpperCase() + t.slice(1))), h("th", mark("laurel"))],
      (pl) => [...Object.keys(CATEGORIES).map((k) => String(pl.score[k])), h("b", String(pl.score.total))]),
    table("Здания по уровням", [1, 2, 3, 4].map((lvl) => h("th", UI.levels[lvl])), (pl) => {
      const faction = static_.factions[pl.faction];
      return [1, 2, 3, 4].map((lvl) => pl.built.filter((b) => faction.buildings[b].level === lvl).map((b) => faction.buildings[b].name).join(", ") || "—");
    }),
    table("Крепости и артефакты", [h("th", mark("victory"), " Победы"), h("th", "Захваченные крепости"), h("th", "Артефакты")],
      (pl) => [String(pl.victories), pl.fortresses.join(", ") || "—",
        pl.artifacts.length ? h("span.art-list", pl.artifacts.map((r) => h("span", h("img.ic.art", { src: picture(`artifact_${r}`), alt: "" }), r))) : "—"]),
    table("Условия артефактов", artifacts.map(([r, a]) => h("th.art-head", h("img.ic.art", { src: picture(`artifact_${r}`), alt: "" }),
      h("div", r), h("small", CONDITION_SHORT[a.condition](a)))), (pl, p) => artifacts.map(([r]) => conditionProgress(static_, state, p, r))),
    h("p.muted.small", "Тап по строке — планшет и карта игрока."));
}
