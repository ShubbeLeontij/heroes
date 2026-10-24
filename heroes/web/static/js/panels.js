// Формы решений: на каждый вид решения своя, как в heroes/gui/panels.py окна (тексты, данные и ответы — те же).
// Форма собирает ответ в том виде, что ждёт движок, и отправляет его через app.sendAnswer. Окончательная
// проверка — у хозяина: если ответ отклонён, решение придёт снова с полем error, и форма покажет ошибку.
// Для пальцев: счётчики — с кнопками «−/+», регионы — списком крупных строк (и тапом по карте),
// порядок действий — тапами (на ПК — и перетаскиванием).

import { UI, h, mark, rich } from "./rich.js";
import { armyStrength, artifactText, buildingText, nameOf, objectText, plural, res, resLong, terrainOf,
  upgradeGains } from "./common.js";

const RESOURCES = ["wood", "iron", "gold"];
const MAX_COMBINATIONS = 50_000;   // больше заявок cheapestArmy не перебирает
const T = (key) => UI.terrains[key] || key;
const R = (key) => UI.resources[key] || key;

// --- общие элементы ------------------------------------------------------------------------------

// Поле для неотрицательного целого не больше maximum, с кнопками «−» и «+»; value == -1 — введено не число.
export class Counter {
  constructor(maximum, onChange = null) {
    this.maximum = maximum;
    this.onChange = onChange;
    this.input = h("input.count", { type: "text", inputmode: "numeric", pattern: "[0-9]*", value: "0",
      autocomplete: "off", oninput: () => this.changed(), onfocus: (e) => e.target.select() });
    this.minus = h("button.step", { type: "button", "aria-label": "меньше", onclick: () => this.set(Math.max(0, this.value - 1)) }, "−");
    this.plus = h("button.step", { type: "button", "aria-label": "больше", onclick: () => this.set(Math.min(this.maximum, Math.max(0, this.value) + 1)) }, "+");
    this.el = h("span.counter", this.minus, this.input, this.plus);
    this.el.counter = this;   // тестовый режим находит счётчики по разметке
    this.update();
  }

  get value() {
    const text = this.input.value.trim() || "0";
    return /^\d+$/.test(text) ? Number(text) : -1;
  }

  get valid() {
    return this.value >= 0 && this.value <= this.maximum;
  }

  set(n) {
    this.input.value = String(n);
    this.changed();
  }

  setMaximum(maximum) {
    this.maximum = maximum;
    this.update();
  }

  changed() {
    this.update();
    if (this.onChange) this.onChange();
  }

  update() {
    this.minus.disabled = this.value <= 0;
    this.plus.disabled = this.value >= this.maximum || this.value < 0;
    this.el.classList.toggle("invalid", !this.valid);
  }
}

// Группа переключателей: rows — [[значение, текст со значками, пояснение?], …]
export class Radios {
  constructor(rows, onChange = null, value = null) {
    this.name = `r${Math.random().toString(36).slice(2)}`;
    this.onChange = onChange;
    this.inputs = new Map();
    this.el = h("div.radios");
    this.el.radios = this;
    for (const [val, text, hint] of rows) this.add(val, text, hint);
    if (value !== null) this.set(value, false);
  }

  add(val, text, hint = null, parent = this.el) {
    const input = h("input", { type: "radio", name: this.name, value: val, onchange: () => this.onChange && this.onChange() });
    this.inputs.set(val, input);
    const row = h("label.radio", input, h("span.radio-text", rich(text), hint ? h("small.muted", hint) : null));
    parent.append(row);
    return row;
  }

  get value() {
    for (const [val, input] of this.inputs) if (input.checked) return val;
    return "";
  }

  set(val, notify = true) {
    for (const [v, input] of this.inputs) input.checked = v === val;
    if (notify && this.onChange) this.onChange();
  }
}

const MAX_KEY = [Infinity, Infinity, Infinity];
const less = (a, b) => {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i];
  return false;
};

// Самая слабая заявка, которой хватает для победы (сила больше need) — panels.cheapest_army окна.
// Возвращает [заявка, сколько единиц модифицировать] или null — не хватит и всех.
export function cheapestArmy(units, need, rules = null, apart = null, upgrade = null) {
  const most = (upgrade || {}).max || 0;
  const needed = (army) => {
    let strength = armyStrength(units, army, rules);
    const gains = upgradeGains(units, army, upgrade).slice(0, most);
    for (let n = 0; n <= gains.length; n++) {
      if (strength > need) return n;
      strength += n < gains.length ? gains[n] : 0;
    }
    return null;
  };
  const ids = Object.keys(units);
  const total = ids.reduce((t, u) => t * (units[u].count + 1), 1);
  if (total > MAX_COMBINATIONS) {   // слишком много вариантов: всех, кроме отдельной гидры
    const army = Object.fromEntries(ids.filter((u) => u !== apart && units[u].count).map((u) => [u, units[u].count]));
    const n = needed(army);
    return n === null ? null : [army, n];
  }
  let best = null, bestKey = MAX_KEY;
  const chosen = [];
  const walk = (i) => {
    if (i === ids.length) {
      const army = {};
      ids.forEach((u, j) => { if (chosen[j]) army[u] = chosen[j]; });
      if (apart in army && Object.keys(army).length > 1) return;
      const n = needed(army);
      if (n === null) return;
      const key = [n, armyStrength(units, army, rules, upgrade, n), chosen.reduce((s, x) => s + x, 0)];
      if (less(key, bestKey)) {
        best = [army, n];
        bestKey = key;
      }
      return;
    }
    for (let n = 0; n <= units[ids[i]].count; n++) {
      chosen[i] = n;
      walk(i + 1);
    }
  };
  walk(0);
  return best;
}

// Наибольшая сила заявки (panels.best_strength окна)
export function bestStrength(units, rules = null, apart = null, upgrade = null) {
  const most = (upgrade || {}).max || 0;
  if (!(apart in units)) return armyStrength(units, null, rules, upgrade, most);
  const army = Object.fromEntries(Object.entries(units).filter(([u]) => u !== apart).map(([u, v]) => [u, v.count]));
  return Math.max(armyStrength(units, army, rules, upgrade, most), armyStrength(units, { [apart]: 1 }, rules));
}

// Заявка войск: сколько единиц каждого вида; итоговая сила и хватит ли её (panels.ArmyPicker окна).
class ArmyPicker {
  constructor(units, need = null, onChange = null, rules = null, apart = null, upgrade = null) {
    Object.assign(this, { units, need, onChange, rules, apart, upgrade });
    this.counters = {};
    this.upgradeCounter = null;
    this.summary = h("p.summary");
    this.el = h("div.army");
    if (!Object.keys(units).length) {
      this.el.append(h("p.muted", "У вас нет войск."), this.summary);
      this.refresh();
      return;
    }
    const rows = Object.entries(units).map(([uid, u]) => {
      const better = ((upgrade || {}).strength || {})[uid] || 0;
      const strength = upgrade && upgrade.max && better > u.strength ? `${u.strength} » ${better}` : String(u.strength);
      this.counters[uid] = new Counter(u.count, () => this.refresh());
      return h("tr", h("th", u.name), h("td.num", String(u.count)), h("td.num", strength), h("td", this.counters[uid].el));
    });
    this.el.append(h("table.army-table", h("thead", h("tr", h("th", "Войско"), h("th", "Есть"), h("th", "Сила"), h("th", "Заявить"))),
      h("tbody", rows)));
    if (upgrade && upgrade.max && upgradeGains(units, Object.fromEntries(Object.entries(units).map(([u, v]) => [u, v.count])), upgrade).length) {
      this.upgradeCounter = new Counter(0, () => this.refresh());
      this.el.append(h("div.row", h("span", `Лавка бронника: модифицировать заявленных, ${res(upgrade.cost_per_unit)} за каждую`),
        this.upgradeCounter.el));
    }
    this.el.append(h("div.row.small-buttons",
      h("button.helper", { type: "button", onclick: () => this.all() }, "Все"),
      h("button.helper", { type: "button", onclick: () => this.none() }, "Никого")), this.summary);
    this.refresh();
  }

  all() {
    for (const [uid, c] of Object.entries(this.counters)) c.set(this.units[uid].count);
  }

  none() {
    for (const c of Object.values(this.counters)) c.set(0);
  }

  preset(army, upgrades = 0) {
    for (const [uid, c] of Object.entries(this.counters)) c.input.value = String(army[uid] || 0);
    if (this.upgradeCounter) this.upgradeCounter.input.value = String(upgrades);
    for (const c of Object.values(this.counters)) c.update();
    this.refresh();
  }

  get valid() {
    return Object.values(this.counters).every((c) => c.valid) && !this.mixed()
      && (this.upgradeCounter === null || this.upgradeCounter.valid);
  }

  // отдельная гидра заявлена вместе с войсками — так нельзя
  mixed() {
    const army = this.army();
    return this.apart in army && Object.keys(army).length > 1;
  }

  army() {
    return Object.fromEntries(Object.entries(this.counters).filter(([, c]) => c.value > 0).map(([uid, c]) => [uid, c.value]));
  }

  upgrades() {
    return this.upgradeCounter ? Math.max(0, this.upgradeCounter.value) : 0;
  }

  // ответ движку: заявка, а у эльфов с Лавкой бронника — {army, upgrade}
  answer() {
    return this.upgrade === null || this.upgrade === undefined ? this.army() : { army: this.army(), upgrade: this.upgrades() };
  }

  strength() {
    return this.valid ? armyStrength(this.units, this.army(), this.rules, this.upgrade, this.upgrades()) : 0;
  }

  enough() {
    return this.need === null || this.strength() > this.need;
  }

  setNeed(need) {
    this.need = need;
    this.refresh();
  }

  refresh() {
    if (this.upgradeCounter && Object.values(this.counters).every((c) => c.valid)) {
      // модифицировать можно не больше заявленных, кому это что-то даёт, и не больше, чем хватает ресурсов
      const most = Math.min(this.upgrade.max, upgradeGains(this.units, this.army(), this.upgrade).length);
      this.upgradeCounter.setMaximum(most);
      if (this.upgradeCounter.value > most) {
        this.upgradeCounter.set(most);   // вызовет refresh ещё раз
        return;
      }
    }
    let text, cls = "";
    if (this.mixed()) {
      text = `${this.units[this.apart].name} стоит в другом регионе и сражается только одна`;
      cls = "bad";
    } else if (!this.valid) {
      text = "Введите числа в пределах того, что у вас есть";
      cls = "bad";
    } else {
      const s = this.strength(), bonus = (this.rules || {}).bonus || 0;
      text = `Заявленная сила: ${s}` + (bonus ? ` (с прибавкой +${bonus} к итогу)` : "");
      if (this.upgrades()) {
        const cost = Object.fromEntries(Object.entries(this.upgrade.cost_per_unit).map(([k, v]) => [k, v * this.upgrades()]));
        text += `, модифицировано ${this.upgrades()} — заплатите ${resLong(cost)}`;
      }
      if (this.need !== null) {
        const enough = s > this.need;
        text += enough ? ` — хватает (нужно больше ${this.need})` : ` — НЕ хватает: нужно больше ${this.need}`;
        cls = enough ? "good" : "bad";
      }
    }
    this.summary.textContent = text;
    this.summary.className = "summary " + cls;
    if (this.onChange) this.onChange();
  }
}

// --- панели ------------------------------------------------------------------------------------

export class Panel {
  static title = "Решение";
  static secret = false;
  usesMap = false;   // выбор на карте: на телефоне вкладка «Карта» не закрывается при новом решении

  constructor(app, message) {
    this.app = app;
    this.message = message;
    this.data = message.data;
    this.state = message.state;
    this.static = app.static;
    this.me = message.player;
    this.player = this.state.players[this.me];
    this.body = h("div.panel-body");
    this.footer = h("div.panel-footer");
    this.el = h("div.panel",
      h("div.panel-head", h("h2", this.heading()), this.isSecret() ? h("span.secret", "тайный выбор") : null),
      message.error ? h("div.panel-error", "✗ Ответ не принят: " + message.error) : null,
      this.body, this.footer);
    this.el.panel = this;
    this.build();
  }

  heading() {
    return this.constructor.title;
  }

  isSecret() {
    return this.constructor.secret;
  }

  build() {
    throw new Error("build() not implemented");
  }

  destroy() {}

  note(text, cls = "muted", parent = this.body) {
    const el = h("p.note" + (cls ? "." + cls.split(" ").join(".") : ""), text);
    parent.append(el);
    return el;
  }

  // кнопка внизу формы; primary — главная (её же нажимает Enter)
  button(text, onclick, primary = false) {
    const b = h("button" + (primary ? ".primary" : ".secondary"), { type: "button", onclick }, text);
    this.footer.prepend(b);   // как pack(side="right") в окне: первая добавленная — правее всех
    return b;
  }

  send(answer) {
    this.app.sendAnswer(this.message, answer);
  }

  object(region) {
    return objectText(this.static, this.state, region, this.player.faction);
  }

  name(p, avatar = false) {
    return nameOf(this.static, Number(p), avatar);
  }

  // строка об игроке: его аватарка и «Имя (Фракция)…»
  playerNote(p, text, cls = "") {
    this.body.append(h("p.note.player" + (cls ? "." + cls : ""), `${this.name(p, true)}${text}`));
  }
}

class RegionChoice extends Panel {
  usesMap = true;

  regionList(options, labels, badges, selected = null) {
    this.options = options;
    this.choice = null;
    this.items = new Map();
    const list = h("div.region-list", { role: "listbox" });
    for (const r of options) {
      const item = h("button.region", { type: "button", role: "option", onclick: () => this.pick(r, true),
        ondblclick: () => this.confirm() }, labels[r]);
      this.items.set(r, item);
      list.append(item);
    }
    this.body.append(list);
    this.app.map.selectMode(badges, null, (r) => this.pick(r), this.me);
    this.app.showMapOf?.(this.me);
    if (selected) this.pick(selected);
  }

  pick(region, fromList = false) {
    this.choice = region;
    for (const [r, item] of this.items) item.classList.toggle("selected", r === region);
    if (!fromList) this.items.get(region)?.scrollIntoView({ block: "nearest" });
    this.app.map.setSelected(region);
    this.picked();
  }

  picked() {}

  confirm() {
    if (this.choice !== null) this.send(this.choice);
  }
}

class ChooseStart extends RegionChoice {
  static title = "Выберите стартовый регион";

  build() {
    const regions = this.static.regions, options = this.data.options;
    this.note("Стартовые регионы — с порталом. Ресурсы региона вы получите сразу. "
      + "Выберите регион на карте или в списке.");
    const labels = Object.fromEntries(options.map((r) => [r, `${r} — ${T(regions[r].terrain)}, ресурсы: ${resLong(regions[r].resources)}`]));
    this.ok = this.button("Начать здесь", () => this.confirm(), true);
    this.ok.disabled = true;
    this.regionList(options, labels, Object.fromEntries(options.map((r) => [r, res(regions[r].resources)])));
  }

  picked() {
    this.ok.disabled = false;
    this.ok.textContent = `Начать: ${this.choice}`;
  }
}

// Порядок действий: четыре места (уровни 1–4). Тап по действию ставит его на первое свободное место,
// повторный — убирает; тап по занятому месту — убрать; на ПК (и пальцем) действие можно перетащить на место.
class ActionOrder extends Panel {
  static title = "Вы первый игрок: выберите 4 действия";
  usesMap = true;

  build() {
    this.order = [null, null, null, null];
    this.note("Выберите 4 действия из 5 в том порядке, в каком они будут выполняться; пятое в этом раунде "
      + "не выполняет никто. Место в порядке — это уровень действия: на уровне N добывается N ресурса, "
      + "даётся N очков перемещения или все строят здание уровня не выше N.");
    this.note("Тап по действию — на первое свободное место, тап по месту — убрать; действие можно и перетащить на место. "
      + "Когда перемещение стоит на месте, на карте обведено, куда с этими ОП дойдёт её хозяин.");
    this.buttons = {};
    const row = h("div.actions");
    for (const [a, label] of Object.entries(UI.actions)) {
      const b = h("button.action", { type: "button" }, label);
      this.draggable(b, () => a, () => this.toggle(a));
      this.buttons[a] = b;
      row.append(b);
    }
    this.slots = [0, 1, 2, 3].map((i) => {
      const slot = h("button.slot", { type: "button" });
      slot.dataset.slot = i;
      this.draggable(slot, () => this.order[i], () => this.remove(this.order[i]));
      return slot;
    });
    this.skipped = h("p.muted.skipped");
    this.body.append(row, h("div.slots", this.slots), this.skipped);
    this.ok = this.button("Подтвердить порядок", () => this.send([...this.order]), true);
    this.button("Сбросить", () => this.clear());
    this.refresh();
  }

  toggle(action) {
    if (this.order.includes(action)) this.remove(action);
    else if (this.order.includes(null)) {
      this.order[this.order.indexOf(null)] = action;
      this.refresh();
    }
  }

  remove(action) {
    if (action && this.order.includes(action)) {
      this.order[this.order.indexOf(action)] = null;
      this.refresh();
    }
  }

  put(action, slot) {
    if (this.order.includes(action)) {
      const here = this.order.indexOf(action);
      [this.order[here], this.order[slot]] = [this.order[slot], action];
    } else {
      this.order[slot] = action;
    }
    this.refresh();
  }

  clear() {
    this.order = [null, null, null, null];
    this.refresh();
  }

  refresh() {
    this.slots.forEach((slot, i) => {
      const a = this.order[i];
      slot.textContent = `${i + 1}. ${a ? UI.actions[a] : "—"}`;
      slot.classList.toggle("filled", Boolean(a));
    });
    const full = !this.order.includes(null);
    for (const [a, b] of Object.entries(this.buttons)) b.classList.toggle("pressed", this.order.includes(a));
    this.ok.disabled = !full;
    const skipped = Object.keys(UI.actions).filter((a) => !this.order.includes(a));
    this.skipped.textContent = full ? `пропускается: ${UI.actions[skipped[0]].toLowerCase()}` : "";
    const reach = this.data.reach;
    if (reach !== undefined && reach !== null) {
      const level = this.order.includes("move") ? String(this.order.indexOf("move") + 1) : null;
      this.app.map.setPreview(level ? Object.fromEntries(Object.entries(reach).map(([q, byLevel]) => [q, byLevel[level]])) : null);
    }
  }

  // нажатие без сдвига — click(); со сдвигом — перетаскивание действия action() на место
  draggable(el, action, click) {
    el.addEventListener("pointerdown", (e) => {
      const a = action();
      const start = [e.clientX, e.clientY];
      let ghost = null, over = null;
      const move = (ev) => {
        if (!a) return;
        if (!ghost) {
          if (Math.abs(ev.clientX - start[0]) + Math.abs(ev.clientY - start[1]) < 8) return;
          ghost = h("div.drag-ghost", UI.actions[a]);
          document.body.append(ghost);
        }
        ghost.style.left = `${ev.clientX + 10}px`;
        ghost.style.top = `${ev.clientY + 10}px`;
        const target = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".slot");
        if (over) over.classList.remove("drop");
        over = this.slots.includes(target) ? target : null;
        if (over) over.classList.add("drop");
        ev.preventDefault();
      };
      const up = (ev) => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
        if (!ghost) {
          if (ev.type === "pointerup") click();
          return;
        }
        ghost.remove();
        if (over) {
          over.classList.remove("drop");
          this.put(a, Number(over.dataset.slot));
        } else {
          this.remove(a);   // утащили за пределы мест — убрать
        }
      };
      el.setPointerCapture(e.pointerId);
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
    });
  }
}

class DistributeResources extends Panel {
  static title = "Выберите ресурсы";

  build() {
    this.amount = this.data.amount;
    const allowed = RESOURCES.filter((r) => (this.data.allowed || RESOURCES).includes(r));
    const why = this.message.reason_ru ?? this.data.reason ?? "";
    if (why) this.note(why.charAt(0).toUpperCase() + why.slice(1) + ".", "");
    this.note(`Распределите ${this.amount}: ` + allowed.map(R).join(", ") + ".");
    this.counters = {};
    const grid = h("div.res-grid");
    for (const k of allowed) {
      this.counters[k] = new Counter(this.amount, () => this.refresh());
      grid.append(h("div.res-cell", h("b", mark(k), " ", R(k)), this.counters[k].el,
        h("button.helper", { type: "button", onclick: () => this.allTo(k) }, `всё — ${R(k)}`)));
    }
    this.summary = h("p.summary");
    this.body.append(grid, this.summary);
    this.ok = this.button("Подтвердить", () => this.send(Object.fromEntries(Object.entries(this.counters).map(([k, c]) => [k, c.value]))), true);
    this.refresh();
  }

  allTo(k) {
    for (const [key, c] of Object.entries(this.counters)) c.set(key === k ? this.amount : 0);
  }

  refresh() {
    const valid = Object.values(this.counters).every((c) => c.valid);
    const total = valid ? Object.values(this.counters).reduce((s, c) => s + c.value, 0) : -1;
    const ok = total === this.amount;
    this.summary.textContent = `Выбрано ${valid ? total : "?"} из ${this.amount}`;
    this.summary.className = "summary " + (ok ? "good" : "bad");
    if (this.ok) this.ok.disabled = !ok;
  }
}

// Строительство и наём — шагами в любом порядке: каждая кнопка сразу выполняет свой шаг, и форма приходит
// снова с новым состоянием, пока игрок не нажмёт «Закончить строительство».
class BuildRecruit extends Panel {
  heading() {
    return `Строительство и наём — уровень ${this.data.level}`;
  }

  build() {
    const data = this.data;
    this.stock = data.resources;
    this.faction = this.static.factions[this.player.faction];
    this.units = this.faction.units;
    this.note(`Склад: ${resLong(this.stock)}. Здание (одно за действие) и наём — в любом порядке, нанимать можно `
      + "несколько раз: каждый шаг выполняется сразу, и его плоды (ярость, наука, ресурсы, новое здание "
      + "для найма) доступны следующему.");
    if (data.steps.length) this.note("Уже сделано: " + data.steps.map((s) => this.stepText(s)).join("; ") + ".", "");
    if (data.rejected) this.note(`✗ Шаг не выполнен: ${data.rejected.error}`, "bad");
    const f = data.foundation;
    if (f && !data.built) {
      this.note(`Вы в регионе ${f.region} с фундаментом (вписан уровень ${f.level}): здание более `
        + "высокого уровня возводится на нём автоматически и приносит столько ПО, каков его уровень.", "hint");
    }
    const cols = h("div.columns");
    this.body.append(cols);
    this.buildingSection(cols);
    this.hireSection(cols);
    if (data.upgrade && Object.keys(data.upgrade.units).length) this.upgradeSection(cols);
    if (data.raise) this.raiseSection(cols);
    if (data.science) this.scienceSection(cols);
    this.button("Закончить строительство", () => this.send(["done"]), true);
  }

  destroy() {
    this.app.pickBuilding?.(null);
  }

  stepText(step) {
    const [[kind, value]] = Object.entries(step);
    if (kind === "build") return "построено: " + this.faction.buildings[value].name;
    if (kind === "hire") return "нанято: " + Object.entries(value).map(([u, n]) => `${this.units[u].name} ×${n}`).join(", ");
    if (kind === "upgrade") {
      return "модифицировано: " + Object.entries(value).map(([k, n]) => {
        const [a, b] = k.split(">");
        return `${this.units[a].name} → ${this.units[b].name} ×${n}`;
      }).join(", ");
    }
    if (kind === "raise") return "повышено: " + Object.entries(value).map(([a, x]) => `${UI.actions[a]} +${x}`).join(", ");
    return "куплена наука";
  }

  section(parent, title) {
    const frame = h("section.step-section", h("h3", title));
    parent.append(frame);
    return frame;
  }

  stepButton(parent, text, onclick) {
    const b = h("button.step-button", { type: "button", onclick }, text);
    parent.append(b);
    return b;
  }

  buildingSection(parent) {
    const data = this.data;
    const frame = this.section(parent, "Здание (или тапом по планшету)");
    if (data.built) {
      frame.append(h("p.muted", `Здание этого действия уже построено: ${this.faction.buildings[data.built].name}.`));
      return;
    }
    if (!Object.keys(data.options).length) {
      frame.append(h("p.muted", "Сейчас ничего построить нельзя: не хватает ресурсов, уровня действия или требуемых зданий."));
      return;
    }
    const f = data.foundation;
    this.choice = new Radios([], () => this.chosen());
    for (const [bid, o] of Object.entries(data.options)) {
      const extra = f && o.level > f.level ? ` (+${o.level} ПО на фундаменте)` : "";
      this.choice.add(bid, `${o.name} — уровень ${o.level}, ${o.vp} ПО${extra}, цена ${res(o.cost)}`,
        buildingText(this.static, this.player.faction, bid));
    }
    frame.append(this.choice.el);
    this.buildOk = this.stepButton(frame, "Построить", () => this.send([{ build: this.choice.value }]));
    this.buildOk.disabled = true;
    this.app.pickBuilding?.(this.me, new Set(Object.keys(data.options)), (bid) => this.boardPick(bid));
  }

  chosen() {
    this.app.board?.setPicked?.(this.choice.value || null);
    this.buildOk.disabled = !this.choice.value;
  }

  boardPick(bid) {
    this.choice.set(bid || "", false);
    this.buildOk.disabled = !bid;
  }

  hireSection(parent) {
    const frame = this.section(parent, "Наём");
    const hires = this.data.hire;
    if (!Object.keys(hires).length) {
      frame.append(h("p.muted", "Нанимать некого: нет здания для найма."));
      return;
    }
    this.hireCounters = {};
    const rows = Object.entries(hires).map(([uid, x]) => {
      const costs = Object.entries(x.cost).filter(([, v]) => v);
      const most = costs.length ? Math.min(...costs.map(([k, v]) => Math.floor((this.stock[k] || 0) / v))) : 0;
      this.hireCounters[uid] = new Counter(Math.max(0, most), () => this.refreshHire());
      return h("tr", h("th", `${x.name} (${res(x.cost)})`), h("td", this.hireCounters[uid].el));
    });
    this.hireSummary = h("p.summary");
    frame.append(h("table.form-table", h("tbody", rows)), this.hireSummary);
    this.hireOk = this.stepButton(frame, "Нанять", () => this.send([{ hire: Object.fromEntries(
      Object.entries(this.hireCounters).filter(([, c]) => c.value > 0).map(([u, c]) => [u, c.value])) }]));
    this.refreshHire();
  }

  hireCost() {
    const total = {};
    for (const [uid, c] of Object.entries(this.hireCounters)) {
      if (c.value < 0) return null;
      for (const [k, v] of Object.entries(this.data.hire[uid].cost)) total[k] = (total[k] || 0) + v * c.value;
    }
    return total;
  }

  refreshHire() {
    if (!this.hireOk) return;
    const total = this.hireCost();
    if (total === null) {
      this.hireSummary.textContent = "Введите целые числа";
      this.hireSummary.className = "summary bad";
      this.hireOk.disabled = true;
      return;
    }
    const ok = Object.entries(total).every(([k, v]) => (this.stock[k] || 0) >= v);
    const some = Object.values(this.hireCounters).some((c) => c.value);
    this.hireSummary.textContent = some ? `К оплате: ${resLong(total)}` : "";
    this.hireSummary.className = "summary " + (ok ? "" : "bad");
    this.hireOk.disabled = !(ok && some);
  }

  // модификация орков
  upgradeSection(parent) {
    const d = this.data.upgrade;
    const frame = this.section(parent, "Модификация войск за ярость");
    frame.append(h("p.muted", "Модификация — не наём: зданий для найма не нужно."));
    this.upgradeCounters = {};
    const rows = Object.entries(d.paths).map(([key, path]) => {
      // сколько можно модифицировать: свои плюс те, кого в этом же шаге модифицируют в них
      const most = (d.units[path.from] || 0) + Object.values(d.paths).filter((x) => x.to === path.from)
        .reduce((s, x) => s + (d.units[x.from] || 0), 0);
      this.upgradeCounters[key] = new Counter(most, () => this.refreshUpgrade());
      return h("tr", h("th", `${this.units[path.from].name} → ${this.units[path.to].name}, ${res(path.cost)} за каждого`),
        h("td", this.upgradeCounters[key].el));
    });
    this.upgradeSummary = h("p.summary");
    frame.append(h("table.form-table", h("tbody", rows)), this.upgradeSummary);
    this.upgradeOk = this.stepButton(frame, "Модифицировать", () => this.send([{ upgrade: this.checkUpgrade()[0] }]));
    this.refreshUpgrade();
  }

  checkUpgrade() {
    if (!Object.values(this.upgradeCounters).every((c) => c.value >= 0)) return [null, "Введите целые числа"];
    const d = this.data.upgrade;
    const units = { ...d.units }, spent = {};
    for (const [key, path] of Object.entries(d.paths)) {
      const n = this.upgradeCounters[key].value;
      if (n > (units[path.from] || 0)) return [null, `Не хватает: ${this.units[path.from].name}`];
      units[path.from] = (units[path.from] || 0) - n;
      units[path.to] = (units[path.to] || 0) + n;
      for (const [k, v] of Object.entries(path.cost)) spent[k] = (spent[k] || 0) + v * n;
    }
    if (Object.entries(spent).some(([k, v]) => v > (this.stock[k] || 0))) return [null, `Не хватает: нужно ${resLong(spent)}`];
    const answer = Object.fromEntries(Object.entries(this.upgradeCounters).filter(([, c]) => c.value).map(([k, c]) => [k, c.value]));
    return [answer, Object.values(spent).some(Boolean) ? `Потратите: ${resLong(spent)}` : ""];
  }

  refreshUpgrade() {
    if (!this.upgradeOk) return;
    const [answer, text] = this.checkUpgrade();
    this.upgradeSummary.textContent = text;
    this.upgradeSummary.className = "summary " + (answer !== null ? "" : "bad");
    this.upgradeOk.disabled = !(answer && Object.keys(answer).length);
  }

  // Дом вождей
  raiseSection(parent) {
    const frame = this.section(parent, "Дом вождей: повысить уровень действий");
    this.raiseForm = new RaiseForm(this.data.raise, () => this.refreshRaise());
    frame.append(this.raiseForm.el);
    this.raiseOk = this.stepButton(frame, "Повысить", () => this.send([{ raise: this.raiseForm.answer() }]));
    this.refreshRaise();
  }

  refreshRaise() {
    if (this.raiseOk) this.raiseOk.disabled = !this.raiseForm.ok();
  }

  // Библиотека гномов
  scienceSection(parent) {
    const d = this.data.science;
    const frame = this.section(parent, "Библиотека");
    frame.append(h("p", `Один раз за строительство: ${resLong(d.cost)} → +${d.value} науки (сейчас ${d.science}).`));
    this.stepButton(frame, "Купить науку", () => this.send([{ science: true }]));
  }
}

class ReserveObject extends RegionChoice {
  static title = "Башня иллюзий";

  build() {
    const options = Object.keys(this.data.options).sort();
    this.note(`Можно заплатить (${resLong(this.data.cost)}), чтобы объект в одном регионе — крепость, `
      + "артефакт, портал или фундамент — стал доступен только вам. Можно отказаться.");
    const labels = Object.fromEntries(options.map((r) => [r, `${r}: ${this.object(r)}`]));
    const badges = Object.fromEntries(options.map((r) => [r, `${UI.glyphs[this.data.options[r]] || ""} ${UI.objects[this.data.options[r]] || ""}`]));
    this.ok = this.button("Забронировать", () => this.confirm(), true);
    this.ok.disabled = true;
    this.button("Отказаться", () => this.send(null));
    this.regionList(options, labels, badges);
  }

  picked() {
    this.ok.disabled = false;
    this.ok.textContent = `Забронировать: ${this.choice}`;
  }
}

class Destination extends RegionChoice {
  static secret = true;

  heading() {
    return `Перемещение: ${this.data.mp} ОП из региона ${this.player.region}`;
  }

  build() {
    const opts = this.data.options, here = this.player.region;
    const options = Object.keys(opts).sort((a, b) => opts[a].cost - opts[b].cost || a.localeCompare(b));
    const others = {};
    this.state.players.forEach((pl, q) => {
      if (q !== this.me) (others[pl.region] ||= []).push(this.name(q));
      if (pl.hydra) (others[pl.hydra] ||= []).push("гидра игрока " + this.name(q));
    });
    const labels = {}, badges = {};
    for (const r of options) {
      const region = this.static.regions[r], info = opts[r];
      const parts = [`${r}${r === here ? " (остаться)" : ""}`, `цена ${info.cost}, останется ${info.left} ОП`,
        T(terrainOf(this.static, this.state, r)), this.object(r)];
      if (!this.player.visited.includes(r) && Object.keys(region.resources || {}).length) parts.push(`новый: ${res(region.resources)}`);
      if (others[r]) parts.push("сейчас там: " + others[r].join(", "));
      labels[r] = parts.join(" · ");
      badges[r] = r === here ? "остаться" : `${info.cost} ОП`;
    }
    this.note("Куда пошли остальные, станет известно, когда выберут все. Неистраченные очки перемещения "
      + "позволяют в этом раунде пропустить чудовищ в вашей линии.");
    this.ok = this.button("Остаться", () => this.confirm(), true);
    this.regionList(options, labels, badges, here);
  }

  picked() {
    this.ok.textContent = this.choice === this.player.region ? "Остаться" : `Идти: ${this.choice}`;
  }
}

class RegionStance extends Panel {
  static LABELS = { fight: "Сражаться", claim: "Претендовать на объект", pass: "Пас (уступить)" };

  heading() {
    return `Встреча в регионе ${this.data.region}`;
  }

  build() {
    const region = this.data.region, L = RegionStance.LABELS;
    this.note(`Объект: ${this.object(region)}.`, "");
    this.state.players.forEach((pl, q) => {
      if (pl.region === region && q !== this.me) {
        const units = this.static.factions[pl.faction].units;
        const army = Object.entries(pl.units).filter(([, v]) => v.count).map(([u, v]) => `${units[u].name} ×${v.count}`).join(", ") || "войск нет";
        this.playerNote(q, `: ${army} (общая сила ${pl.army_strength})`);
      }
    });
    const declared = this.data.declared || {};
    if (Object.keys(declared).length) {
      this.note("Уже заявили:", "bold");
      for (const [q, s] of Object.entries(declared)) this.playerNote(q, ` — ${L[s].toLowerCase()}`, "bold");
    }
    this.note(`Сила вашей армии: ${this.player.army_strength}. Сражение начнётся, если кто-то выберет `
      + "«сражаться» или на объект претендуют двое и больше. В сражении войска заявляются тайно, "
      + "все заявленные погибают.");
    for (const option of [...this.data.options].reverse()) {
      this.button(L[option] || option, () => this.send(option), option === "fight");
    }
  }
}

class CommitArmy extends Panel {
  static secret = true;

  heading() {
    return `Сражение в регионе ${this.data.region}`;
  }

  build() {
    const rules = this.data.opponent_rules || {};
    for (const [q, units] of Object.entries(this.data.opponents)) {
      const desc = Object.values(units).map((u) => `${u.name} ×${u.count} (${u.strength})`).join(", ") || "войск нет";
      this.playerNote(q, `: ${desc} — не больше ${armyStrength(units, null, rules[q])}`);
    }
    for (const [q, strength] of Object.entries(this.data.hydras || {})) {
      this.playerNote(q, `: отдельная гидра, сила ${strength}. Награды в этом сражении не получит никто.`);
    }
    this.note("Побеждает наибольшая заявленная сила; при равенстве проигрывают все. Все заявленные войска "
      + "погибают при любом исходе. Можно никого не заявлять.");
    this.picker = new ArmyPicker(this.data.units, null, () => this.refresh(), this.data.strength_rules, null, this.data.upgrade);
    this.body.append(this.picker.el);
    this.ok = this.button("Заявить", () => this.send(this.picker.answer()), true);
    this.refresh();
  }

  refresh() {
    if (this.ok) this.ok.disabled = !this.picker.valid;
  }
}

class ChooseReward extends Panel {
  static title = "Победа — выберите награду";

  build() {
    const opts = this.data.options;
    this.kind = new Radios([], () => this.refresh());
    if ("monster" in opts) this.kind.add("monster", `Награда обведённого чудовища: ${resLong(opts.monster)}`);
    this.counters = {};
    if ("resources" in opts) {
      const amount = opts.resources.amount;
      this.kind.add("resources", `Забрать у проигравших до ${amount} ресурсов`);
      const head = h("tr", h("th"), RESOURCES.map((k) => h("th", mark(k), " ", R(k))));
      const rows = Object.entries(opts.resources.from).map(([q, pool]) => {
        this.counters[q] = {};
        return h("tr", h("th", `${this.name(q, true)} (есть ${res(pool)})`), RESOURCES.map((k) => {
          const c = new Counter(Math.min(pool[k] || 0, amount), () => this.refresh());
          this.counters[q][k] = c;
          return h("td", c.el);
        }));
      });
      this.kind.el.append(h("div.indent.scroll-x", h("table.form-table", h("thead", head), h("tbody", rows))));
    }
    this.artifact = null;
    if ("artifact" in opts) {
      this.kind.add("artifact", "Отобрать артефакт:");
      const rows = [];
      for (const [q, arts] of Object.entries(opts.artifact)) {
        for (const a of arts) rows.push([JSON.stringify([q, a]), `${a} (у игрока ${this.name(q, true)})`]);
      }
      this.artifact = new Radios(rows, () => {
        this.kind.set("artifact", false);
        this.refresh();
      });
      this.artifact.el.classList.add("indent");
      this.kind.el.append(this.artifact.el);
    }
    this.kind.set(Object.keys(opts)[0], false);
    this.summary = h("p.summary");
    this.body.append(this.kind.el, this.summary);
    this.ok = this.button("Взять награду", () => this.confirm(), true);
    this.refresh();
  }

  taken() {
    const take = {};
    for (const [q, counters] of Object.entries(this.counters)) {
      if (!Object.values(counters).every((c) => c.valid)) return null;
      const got = Object.fromEntries(Object.entries(counters).filter(([, c]) => c.value).map(([k, c]) => [k, c.value]));
      if (Object.keys(got).length) take[q] = got;
    }
    return take;
  }

  refresh() {
    if (!this.ok) return;
    const kind = this.kind.value;
    let ok = true, text = "";
    if (kind === "resources") {
      const take = this.taken(), amount = this.data.options.resources.amount;
      const total = take === null ? null : Object.values(take).reduce((s, t) => s + Object.values(t).reduce((a, b) => a + b, 0), 0);
      ok = total !== null && total <= amount;
      text = `Берёте ${total ?? "?"} из ${amount} возможных`;
    } else if (kind === "artifact") {
      ok = Boolean(this.artifact.value);
      text = ok ? "" : "Выберите, какой артефакт";
    }
    this.summary.textContent = text;
    this.summary.className = "summary " + (ok ? "" : "bad");
    this.ok.disabled = !ok;
  }

  confirm() {
    const kind = this.kind.value;
    if (kind === "monster") this.send({ type: "monster" });
    else if (kind === "resources") this.send({ type: "resources", take: this.taken() });
    else {
      const [q, a] = JSON.parse(this.artifact.value);
      this.send({ type: "artifact", from: q, artifact: a });
    }
  }
}

class CaptureFortress extends Panel {
  heading() {
    return `Крепость в регионе ${this.data.region}`;
  }

  build() {
    const d = this.data, rules = d.strength_rules;
    this.note(`Сила ${d.strength}. Награда: ${d.vp} ПО и ${resLong(d.loot)}. Ваша сила должна быть `
      + `больше ${d.strength}; заявленные войска погибают при любом исходе.`, "");
    this.picker = new ArmyPicker(d.units, d.strength, () => this.refresh(), rules, null, d.upgrade);
    this.body.append(this.picker.el);
    this.picker.preset(...(cheapestArmy(d.units, d.strength, rules, null, d.upgrade) || [{}, 0]));
    this.ok = this.button("Штурмовать", () => this.send(this.picker.answer()), true);
    this.button("Не штурмовать", () => this.send(null));
    this.refresh();
  }

  refresh() {
    // заведомо проигрышный штурм движок не примет: кнопка — только когда сил хватает
    if (this.ok) this.ok.disabled = !(this.picker.valid && this.picker.enough());
  }
}

class FightMonster extends Panel {
  static title = "Бой с чудовищем";

  build() {
    const cands = this.data.candidates;
    this.rules = this.data.strength_rules;
    const alone = this.data.hydra_alone;
    this.apart = alone ? alone.unit : null;
    this.upgrade = this.data.upgrade;
    const top = bestStrength(this.data.units, this.rules, this.apart, this.upgrade);
    this.note("Не больше одного чудовища за раунд. Неистраченные очки перемещения позволяют пропустить "
      + `чудовищ в вашей линии. Ваша наибольшая сила: ${top}.`);
    if (alone) {
      this.note(`Гидра стоит отдельно, в регионе ${alone.region}: она может сразиться вместо войска — одна; `
        + "добыча чудовища тогда теряется, гидра погибает.", "hint");
    }
    const keys = Object.keys(cands).sort((a, b) => Number(a) - Number(b));
    this.choice = new Radios(keys.map((key) => {
      const c = cands[key];
      const reward = `${c.vp} ПО` + (Object.keys(c.loot || {}).length ? ` + ${resLong(c.loot)}` : "");
      return [key, `Сила ${c.strength} → ${reward}${c.strength < top ? "" : " (не хватает силы)"}`];
    }), () => this.picked());
    this.body.append(this.choice.el);
    this.note("Чудовище можно выбрать и тапом по его рисунку в линии на планшете.");
    this.picker = new ArmyPicker(this.data.units, null, () => this.refresh(), this.rules, this.apart, this.upgrade);
    this.body.append(this.picker.el);
    this.ok = this.button("Сражаться", () => this.confirm(), true);
    this.button("Не сражаться", () => this.send(null));
    const reachable = keys.filter((k) => cands[k].strength < top);
    this.app.pickMonster?.(this.me, new Set(keys.map(Number)), (index) => this.boardPick(index));
    if (reachable.length) this.choice.set(reachable[reachable.length - 1]);
    this.refresh();
  }

  destroy() {
    this.app.pickMonster?.(null);
  }

  picked() {
    const need = this.data.candidates[this.choice.value].strength;
    this.picker.setNeed(need);
    const [army, upgrades] = cheapestArmy(this.data.units, need, this.rules, this.apart, this.upgrade) || [{}, 0];
    this.picker.preset(army, upgrades);
    this.app.board?.setMonster?.(Number(this.choice.value));
  }

  boardPick(index) {
    if (index !== null && index !== undefined) this.choice.set(String(index));
  }

  refresh() {
    // заведомо проигрышный бой движок не примет: кнопка — только когда сил хватает
    if (this.ok) this.ok.disabled = !(this.choice.value && this.picker.valid && this.picker.enough());
  }

  confirm() {
    const answer = this.picker.answer();
    const withUpgrade = this.upgrade !== null && this.upgrade !== undefined;
    const army = withUpgrade ? answer.army : answer;
    this.send({ monster: Number(this.choice.value), army, ...(withUpgrade ? { upgrade: answer.upgrade } : {}) });
  }
}

class TakeArtifact extends Panel {
  heading() {
    return `Артефакт в регионе ${this.data.region}`;
  }

  build() {
    const cond = this.data.condition;
    this.note(artifactText(this.static, this.player.faction, this.data.region), "bold");
    this.note(cond.condition === "pay" ? `Цена: ${resLong(cond.cost)}.` : "Условие выполнено, платить не нужно.");
    this.button("Взять", () => this.send(true), true);
    this.button("Не брать", () => this.send(false));
  }
}

// --- особые решения фракций --------------------------------------------------------------------

// пары нот значками: обведённая нота — значок ноты, пустая — кружок
function notesText(notes) {
  return notes.map((n) => mark("note").repeat(n) + "○".repeat(2 - n)).join("   ");
}

// да/нет: Гейзер наг, Часовня нежити
class YesNo extends Panel {
  heading() {
    return { make_lake: "Гейзер", exchange_on_gain: "Часовня" }[this.message.kind];
  }

  build() {
    const d = this.data;
    let text, yes, no;
    if (this.message.kind === "make_lake") {
      const terrain = T(terrainOf(this.static, this.state, d.region));
      text = `Потратить ${d.cost_mp} ОП из ${d.left} неистраченных, чтобы регион ${d.region} (${terrain}) `
        + "стал озёрным — для всех игроков. Неистраченные ОП позволяют пропустить чудовищ в вашей линии.";
      [yes, no] = ["Сделать озером", "Не нужно"];
    } else {
      const unit = this.static.factions[this.player.faction].units[d.unit].name;
      text = `Вы получаете ${R(d.resource)}: ${d.gained}. Обменять ${d.pay} из них на ${unit} ×${d.count}? `
        + "Остальное попадёт на склад.";
      [yes, no] = ["Обменять", "Оставить ресурсы"];
    }
    this.note(text, "");
    this.button(yes, () => this.send(true), true);
    this.button(no, () => this.send(false));
  }
}

class BuyMovePoints extends Panel {
  static title = "Катафалк: дополнительные очки перемещения";

  build() {
    const d = this.data;
    this.note(`У вас ${d.mp}${mark("mp")}. Каждое дополнительное ОП стоит ${res(d.cost)}; можно купить до ${d.max}.`, "");
    this.counter = new Counter(d.max, () => this.refresh());
    this.summary = h("p.summary");
    this.body.append(h("div.row", h("span", "Купить ОП:"), this.counter.el), this.summary);
    this.ok = this.button("Купить", () => this.send(this.counter.value), true);
    this.button("Не покупать", () => this.send(0));
    this.refresh();
  }

  refresh() {
    if (!this.ok) return;
    const n = this.counter.value, ok = this.counter.valid;
    const cost = ok ? Object.fromEntries(Object.entries(this.data.cost).map(([k, v]) => [k, v * n])) : {};
    this.summary.textContent = ok ? `Будет ОП: ${this.data.mp + n}, заплатите: ${resLong(cost)}` : `Введите число от 0 до ${this.data.max}`;
    this.summary.className = "summary " + (ok ? "" : "bad");
    this.ok.disabled = !(ok && n);
  }
}

// Лавка бронника эльфов: модификация заявленных единиц перед сражением (в бою игроков — тайно)
class BattleUpgrade extends Panel {
  static title = "Лавка бронника";

  isSecret() {
    return Boolean(this.data.secret);
  }

  build() {
    const d = this.data;
    this.note(`Перед сражением можно заплатить ${resLong(d.cost_per_unit)} за каждую заявленную единицу, `
      + `чтобы усилить её в этом сражении. Усиливаются те, кому это даёт больше всего: ${d.gains.map((g) => `+${g}`).join(", ")}.`, "");
    this.counter = new Counter(d.max, () => this.refresh());
    this.summary = h("p.summary");
    this.body.append(h("div.row", h("span", "Модифицировать единиц:"), this.counter.el), this.summary);
    this.ok = this.button("Модифицировать", () => this.send(this.counter.value), true);
    this.button("Не нужно", () => this.send(0));
    this.refresh();
  }

  refresh() {
    if (!this.ok) return;
    const d = this.data, n = this.counter.value, ok = this.counter.valid;
    if (ok) {
      const cost = Object.fromEntries(Object.entries(d.cost_per_unit).map(([k, v]) => [k, v * n]));
      this.summary.textContent = `Сила заявки: ${d.strength} → ${d.strength + d.gains.slice(0, n).reduce((s, g) => s + g, 0)}; `
        + `заплатите: ${resLong(cost)}`;
      this.summary.className = "summary";
    } else {
      this.summary.textContent = `Введите число от 0 до ${d.max}`;
      this.summary.className = "summary bad";
    }
    this.ok.disabled = !(ok && n);
  }
}

// Дом вождей орков: на сколько поднять каждое из действий (текущее и уже выполненную добычу)
class RaiseForm {
  constructor(offer, onChange) {
    this.offer = offer;
    this.onChange = onChange;
    [[this.key, this.price]] = Object.entries(offer.cost_per_level);
    this.counters = {};
    this.after = {};
    const rows = Object.entries(offer.options).map(([action, level]) => {
      this.counters[action] = new Counter(offer.max, () => this.refresh());
      this.after[action] = h("td.muted");
      return h("tr", h("th", UI.actions[action] + (action === offer.current ? " (сейчас)" : "")), h("td.num", String(level)),
        h("td", this.counters[action].el), this.after[action]);
    });
    this.summary = h("p.summary");
    this.el = h("div.raise",
      h("p", `Каждый уровень стоит: ${resLong({ [this.key]: this.price })}; хватает на ${offer.max} ур. `
        + "Уже выполненная в этом раунде добыча сразу принесёт ещё столько ресурса, на сколько повышена."),
      h("table.form-table", h("thead", h("tr", h("th", "Действие"), h("th", "Уровень"), h("th", "Поднять на"), h("th"))), h("tbody", rows)),
      this.summary);
    this.ready = true;
    this.refresh();
  }

  answer() {
    if (!Object.values(this.counters).every((c) => c.valid)) return null;
    return Object.fromEntries(Object.entries(this.counters).filter(([, c]) => c.value).map(([a, c]) => [a, c.value]));
  }

  ok() {
    const answer = this.answer();
    if (answer === null) return false;
    const total = Object.values(answer).reduce((s, x) => s + x, 0);
    return total > 0 && total <= this.offer.max;
  }

  refresh() {
    if (!this.ready) return;
    const answer = this.answer();
    for (const [action, cell] of Object.entries(this.after)) {
      const x = (answer || {})[action] || 0;
      cell.textContent = x ? `→ уровень ${this.offer.options[action] + x}` : "";
    }
    const total = answer === null ? null : Object.values(answer).reduce((s, x) => s + x, 0);
    this.summary.textContent = total === null ? "Введите целые числа"
      : `Потратите ${R(this.key)}: ${total * this.price} из ${this.offer.max * this.price}`;
    this.summary.className = "summary " + (total === null || total > this.offer.max ? "bad" : "");
    this.onChange();
  }
}

class RaiseAction extends Panel {
  static title = "Дом вождей: повысить уровень действий";

  build() {
    this.form = new RaiseForm(this.data, () => this.refresh());
    this.body.append(this.form.el);
    this.ok = this.button("Повысить", () => this.send(this.form.answer()), true);
    this.button("Не повышать", () => this.send({}));
    this.refresh();
  }

  refresh() {
    if (this.ok) this.ok.disabled = !this.form.ok();
  }
}

// Шахта наг: откупиться от сражения — по 3 любых ресурса каждому сопернику в регионе
class AvoidBattle extends Panel {
  heading() {
    return `Шахта: избежать сражения в регионе ${this.data.region}?`;
  }

  build() {
    const d = this.data;
    this.amount = d.amount;
    this.have = d.resources;
    this.note(`Заплатите ${this.amount} ${plural(this.amount, "любой ресурс", "любых ресурса", "любых ресурсов")}, `
      + `и сражения не будет. Ресурсы поровну получат: ${d.to.map((q) => this.name(q)).join(", ")}. Если на объект претендовали несколько `
      + "игроков, в этом раунде он не достанется никому.", "");
    this.counters = {};
    const grid = h("div.res-grid");
    for (const k of RESOURCES) {
      this.counters[k] = new Counter(Math.min(this.amount, this.have[k] || 0), () => this.refresh());
      grid.append(h("div.res-cell", h("b", mark(k), ` ${R(k)} (есть ${this.have[k] || 0})`), this.counters[k].el));
    }
    this.summary = h("p.summary");
    this.body.append(grid, this.summary);
    this.ok = this.button("Заплатить и не сражаться", () => this.send(Object.fromEntries(
      Object.entries(this.counters).filter(([, c]) => c.value).map(([k, c]) => [k, c.value]))), true);
    this.button("Сражаться", () => this.send(null));
    this.refresh();
  }

  refresh() {
    if (!this.ok) return;
    const valid = Object.values(this.counters).every((c) => c.valid);
    const total = valid ? Object.values(this.counters).reduce((s, c) => s + c.value, 0) : null;
    const ok = total === this.amount;
    this.summary.textContent = `Выбрано ${valid ? total : "?"} из ${this.amount}`;
    this.summary.className = "summary " + (ok ? "good" : "bad");
    this.ok.disabled = !ok;
  }
}

class CircleNote extends Panel {
  static title = "Ноты гидры";

  build() {
    this.note("Ваши пары нот: " + notesText(this.data.notes), "");
    this.note("Закрыв начатую пару, вы сразу призываете гидру в любой озёрный регион (прежняя гидра, если она "
      + "есть, погибает). Начав новую пару, вы откладываете призыв.");
    const labels = { open: "Начать новую пару", close: "Закрыть пару и призвать гидру" };
    for (const option of [...this.data.options].reverse()) this.button(labels[option] || option, () => this.send(option), option === "close");
  }
}

class HydraDestination extends RegionChoice {
  static secret = true;

  heading() {
    return `Гидра: перемещение из региона ${this.data.from}`;
  }

  build() {
    const opts = this.data.options, here = this.data.from;
    const options = Object.keys(opts).sort((a, b) => opts[a] - opts[b] || a.localeCompare(b));
    const steps = (n) => (!n ? "остаться" : n === 1 ? `${n} шаг` : `${n} шага`);
    const labels = Object.fromEntries(options.map((r) => [r, [r, steps(opts[r]), T(terrainOf(this.static, this.state, r)), this.object(r)].join(" · ")]));
    this.note("Гидра идёт отдельно от вас — тайно и без очков перемещения. Стоя отдельно, она обязана сразиться "
      + "с игроками в своём регионе (наград тогда нет ни у кого), а без них может штурмовать крепость "
      + "и сразиться с чудовищем вашей линии — только ради ПО.");
    this.ok = this.button("Оставить на месте", () => this.confirm(), true);
    this.regionList(options, labels, Object.fromEntries(options.map((r) => [r, steps(opts[r])])), here);
  }

  picked() {
    this.ok.textContent = this.choice === this.data.from ? "Оставить на месте" : `Идти: ${this.choice}`;
  }
}

class SummonHydra extends RegionChoice {
  static title = "Призыв гидры";

  build() {
    const options = this.data.options;
    const here = Object.fromEntries(options.map((r) => [r, this.state.players.map((pl, q) => (pl.region === r ? this.name(q) : null)).filter(Boolean)]));
    const labels = Object.fromEntries(options.map((r) => [r, `${r}: ${this.object(r)}` + (here[r].length ? ` · там: ${here[r].join(", ")}` : "")]));
    this.note("Выберите озёрный регион. Если гидра уже на карте, она погибает и призывается новая.");
    this.ok = this.button("Призвать", () => this.confirm(), true);
    this.ok.disabled = true;
    this.regionList(options, labels, Object.fromEntries(options.map((r) => [r, "гидра"])));
  }

  picked() {
    this.ok.disabled = false;
    this.ok.textContent = `Призвать: ${this.choice}`;
  }
}

class CurseRegion extends RegionChoice {
  static title = "Проклятие региона";

  build() {
    const d = this.data, options = d.options;
    const here = Object.fromEntries(options.map((r) => [r, this.state.players.map((pl, q) => (pl.region === r && q !== this.me ? this.name(q) : null)).filter(Boolean)]));
    if (d.optional) {
      this.note(`Чумной столб: можно заплатить ${resLong(d.cost)}, чтобы проклясть свой текущий или соседний `
        + "регион. Можно отказаться.", "");
    } else {
      this.note("Прокляните любой регион (эффект артефакта).", "");
    }
    this.note("Соперник, посетивший проклятый регион или стоящий в нём сейчас, теряет самую слабую единицу; "
      + "каждый проклятый вами регион — +1 ПО в конце игры.");
    const labels = Object.fromEntries(options.map((r) => [r, `${r}: ${this.object(r)}` + (here[r].length ? ` · там: ${here[r].join(", ")}` : "")]));
    this.ok = this.button("Проклясть", () => this.confirm(), true);
    this.ok.disabled = true;
    if (d.optional) this.button("Не проклинать", () => this.send(null));
    this.regionList(options, labels, Object.fromEntries(options.map((r) => [r, here[r].join(", ") || "проклясть"])));
  }

  picked() {
    this.ok.disabled = false;
    this.ok.textContent = `Проклясть: ${this.choice}`;
  }
}

// Решение, для которого нет своей формы: текст движка и ответ в JSON.
class RawJson extends Panel {
  heading() {
    return this.message.kind;
  }

  build() {
    this.body.append(h("pre.engine-text", this.message.text));
    this.entry = h("textarea.json", { rows: 3, spellcheck: false, autocapitalize: "off", autocorrect: "off" });
    this.error = h("div.panel-error.hidden");
    this.body.append(this.entry, this.error);
    this.button("Отправить", () => this.confirm(), true);
  }

  confirm() {
    try {
      this.send(JSON.parse(this.entry.value));
    } catch (e) {
      this.error.textContent = "Это не JSON: " + e.message;
      this.error.classList.remove("hidden");
    }
  }
}

export const PANELS = {
  choose_start: ChooseStart,
  action_order: ActionOrder,
  distribute_resources: DistributeResources,
  build_recruit: BuildRecruit,
  reserve_object: ReserveObject,
  destination: Destination,
  region_stance: RegionStance,
  commit_army: CommitArmy,
  choose_reward: ChooseReward,
  capture_fortress: CaptureFortress,
  fight_monster: FightMonster,
  take_artifact: TakeArtifact,
  // особые решения фракций
  make_lake: YesNo,
  exchange_on_gain: YesNo,
  buy_move_points: BuyMovePoints,
  battle_upgrade: BattleUpgrade,
  raise_action: RaiseAction,
  avoid_battle: AvoidBattle,
  circle_note: CircleNote,
  hydra_destination: HydraDestination,
  summon_hydra: SummonHydra,
  curse_region: CurseRegion,
};

// тайное ли решение; модификация Лавки бронника тайная только в сражении между игроками
export function isSecret(message) {
  if (message.kind === "battle_upgrade") return Boolean(message.data.secret);
  return Boolean(PANELS[message.kind]?.secret);
}

export function makePanel(app, message) {
  return new (PANELS[message.kind] || RawJson)(app, message);
}
