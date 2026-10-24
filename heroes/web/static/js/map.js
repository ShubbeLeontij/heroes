// Карта — оригинальный рисунок из правил, поверх — объекты, подписи и фишки (картинки готовит tools/build_map.py).
// Всё лежит в координатах рисунка (1 px CSS = 1 px карты шириной 1420)
// в слое, который масштабируется и сдвигается жестами — двумя пальцами, колесом мыши, двойным тапом.
// Подписи и фишки на мелком масштабе не меньше читаемого размера на экране (max(9, 15 * s)):
// после каждого жеста карта перерисовывается под новый масштаб.

import { UI, h, icon, mark, rich } from "./rich.js";
import { color, res, terrainOf } from "./common.js";
import { hideTip, showTipAt, tipOwner } from "./tips.js";

const ASSETS = "assets/map";
const WIDTH = 1420;               // берём самый крупный из готовых масштабов: браузер уменьшит сам
const SELECT = "#e8590c";
const CHOSEN = "#a61e1e";
const MARKER = "#3a3631";         // «маркер», которым игрок пишет на своей карте
const LAKE = "#3d8fd1";
const OLD_ROUTE = "#8f887c";
const TERRAINS = ["field", "forest", "mountain", "lake"];   // порядок, как на дорожке перемещения планшета
const MOVE_SECONDS = 1.0;
const POPUP_SECONDS = 2.5;
const POPUP_GAP = 0.35;
const POPUP_STACK = 4;
const POPUP_APPEAR = 0.3;
const TOKEN_FADED = 0.35;
const MAX_ZOOM = 5;
const TAP_MOVE = 8;               // сдвиг пальца больше этого — уже не тап, а перетаскивание
const LONG_PRESS_MS = 450;        // долгое нажатие — фишки полупрозрачны, пока палец на карте
const SVG = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  return el;
}

// точка внутри многоугольника (подсчёт пересечений луча)
function inside(x, y, polygon) {
  let hit = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

export function objectShown(state, region, kind) {
  if (kind === "fortress") return !(region in state.fortress_owner);
  if (kind === "artifact") return !(region in state.artifact_holder);
  return true;
}

const now = () => performance.now() / 1000;
const points = (poly) => poly.map(([x, y]) => `${x},${y}`).join(" ");

export class MapView {
  constructor(container) {
    this.container = container;
    this.static = null;
    this.state = null;
    this.you = null;        // чья это карта: его маршрут, фундаменты, флажки
    this.viewer = null;     // кто смотрит
    this.options = null;    // режим выбора: регион → подпись
    this.owner = null;      // чей это выбор: тыкать можно только на его карте
    this.selected = null;
    this.onSelect = null;
    this.preview = null;
    this.hover = null;
    this.tokenHover = null;
    this.peek = false;      // долгое нажатие: все фишки полупрозрачны
    this.spots = new Map(); // фишка ("player:0", "hydra:1") → где нарисована (координаты рисунка)
    this.moving = new Map();// едущая фишка → {from, to, start}
    this.popups = [];       // таблички: {key, text, start}
    this.fresh = [];
    this.tick = null;
    this.zoom = 1;
    this.k = 1;             // пикселей экрана на пиксель рисунка
    this.tx = 0;
    this.ty = 0;
    this.pointers = new Map();
    this.ready = this.load();
    this.build();
  }

  async load() {
    this.geometry = await (await fetch(`${ASSETS}/map.json`)).json();
    [this.worldW, this.worldH] = this.geometry.size;
    this.regions = this.geometry.regions;
    this.boxes = Object.fromEntries(Object.entries(this.regions).map(([name, r]) => {
      const xs = r.polygon.map((p) => p[0]), ys = r.polygon.map((p) => p[1]);
      return [name, [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]];
    }));
    this.scaleFactor = WIDTH / this.worldW;   // map.json — в пикселях исходной карты (size), рисуем в WIDTH
    this.shades = this.geometry.shades[String(WIDTH)];
    this.fit();
    this.redraw();
  }

  build() {
    const c = this.container;
    c.classList.add("mapview");
    this.world = h("div.world");
    this.base = h("img.base", { src: `${ASSETS}/base_${WIDTH}.png`, alt: "", draggable: false });
    this.svg = svg("svg", { class: "layer" });
    this.html = h("div.layer.labels");
    this.anim = h("div.layer.anim");
    this.world.append(this.base, this.svg, this.html, this.anim);
    this.zoomOut = h("button.map-reset.hidden", { type: "button", "aria-label": "Вся карта", onclick: () => this.resetZoom() }, "⤢");
    c.replaceChildren(this.world, this.zoomOut);
    new ResizeObserver(() => {
      if (!this.geometry) return;
      this.fit();
      this.redrawSoon(0);
    }).observe(c);
    c.addEventListener("pointerdown", (e) => this.down(e));
    c.addEventListener("pointermove", (e) => this.move(e));
    c.addEventListener("pointerup", (e) => this.up(e));
    c.addEventListener("pointercancel", (e) => this.up(e, true));
    c.addEventListener("pointerleave", (e) => {
      if (e.pointerType === "mouse") {
        this.setHover(null, null);
        if (tipOwner() === "map") hideTip();
      }
    });
    c.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    c.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  // --- данные ------------------------------------------------------------------------------

  show(static_, state, you, viewer = null) {
    if (static_ === this.static && this.state) {
      this.startMoves(this.state, state);
      const live = new Set(this.popups);
      const fresh = this.fresh.filter((pop) => live.has(pop));
      for (const key of new Set(fresh.map((pop) => pop.key).filter((key) => this.moving.has(key)))) {
        // события случились там, куда фишка едет: таблички — когда доедет, в том же порядке
        const pops = fresh.filter((pop) => pop.key === key);
        const delay = this.moving.get(key).start + MOVE_SECONDS - pops[0].start;
        for (const pop of pops) pop.start += Math.max(0, delay);
      }
    } else {
      this.moving.clear();
      this.popups = [];
      this.spots = new Map();
    }
    this.fresh = [];
    Object.assign(this, { static: static_, state, you, viewer });
    this.redraw();
  }

  selectMode(options, selected = null, onSelect = null, owner = null) {
    Object.assign(this, { options, selected, onSelect, owner });
    this.redraw();
  }

  setPreview(preview) {
    if (JSON.stringify(preview) === JSON.stringify(this.preview)) return;
    this.preview = preview;
    this.redraw();
  }

  setSelected(region) {
    this.selected = region;
    this.redraw();
  }

  picking() {
    return this.options !== null && this.you === this.owner;
  }

  // что обвести (mapview._shown)
  shownOptions() {
    if (this.picking()) return this.options;
    if (this.you === null || !this.state) return null;
    let reach;
    if (this.preview !== null) reach = this.preview[String(this.you)];
    else if (this.you === this.viewer) return null;
    else reach = this.state.players[this.you].reach;
    if (!reach) return null;
    return Object.fromEntries(Object.entries(reach).map(([r, cost]) => [r, cost ? `${cost}${mark("mp")}` : "остаться"]));
  }

  // табличка над фишкой: popup — [вид ("player" | "hydra"), место, подпись] от сервера (ru.popup)
  event(popup) {
    if (!popup || !this.static) return;
    const [kind, p, text] = popup;
    const key = `${kind}:${p}`;
    const t = now();
    const move = this.moving.get(key);
    const last = Math.max(0, ...this.popups.filter((pop) => pop.key === key).map((pop) => pop.start));
    const pop = { key, text, start: Math.max(move ? move.start + MOVE_SECONDS : t, last + POPUP_GAP) };
    this.popups.push(pop);
    if (!move) this.fresh.push(pop);
    this.animate();
  }

  // --- масштаб и жесты ---------------------------------------------------------------------

  fit() {
    const w = this.container.clientWidth, hh = this.container.clientHeight;
    if (!w || !hh) return;
    this.k0 = Math.min(w / WIDTH, hh / (this.worldH * this.scaleFactor));
    this.setZoom(this.zoom, null);
  }

  // zoom — во сколько раз крупнее «всей карты»; at — точка экрана, которая остаётся на месте
  setZoom(zoom, at, tx = null, ty = null) {
    const w = this.container.clientWidth, hh = this.container.clientHeight;
    zoom = Math.min(MAX_ZOOM, Math.max(1, zoom));
    const k = this.k0 * zoom;
    if (tx === null) {
      if (at) {
        const [wx, wy] = [(at[0] - this.tx) / this.k, (at[1] - this.ty) / this.k];
        tx = at[0] - wx * k;
        ty = at[1] - wy * k;
      } else {
        tx = this.tx;
        ty = this.ty;
      }
    }
    // рисунок не уезжает с экрана: меньше экрана — по центру, больше — края не отходят от краёв
    const mw = WIDTH * k, mh = this.worldH * this.scaleFactor * k;
    tx = mw <= w ? (w - mw) / 2 : Math.min(0, Math.max(w - mw, tx));
    ty = mh <= hh ? (hh - mh) / 2 : Math.min(0, Math.max(hh - mh, ty));
    const rescaled = Math.abs(k - this.k) > 1e-6;
    Object.assign(this, { zoom, k, tx, ty });
    this.world.style.transform = `translate(${tx}px, ${ty}px) scale(${k})`;
    this.zoomOut.classList.toggle("hidden", zoom <= 1.01);
    if (rescaled) this.redrawSoon();
  }

  resetZoom() {
    this.setZoom(1, null);
  }

  redrawSoon(ms = 120) {
    clearTimeout(this.redrawTimer);
    this.redrawTimer = setTimeout(() => this.redraw(), ms);
  }

  local(e) {
    const r = this.container.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  toWorld([x, y]) {
    return [(x - this.tx) / this.k, (y - this.ty) / this.k];
  }

  down(e) {
    this.container.setPointerCapture?.(e.pointerId);
    const at = this.local(e);
    this.pointers.set(e.pointerId, at);
    if (this.pointers.size === 1) {
      this.gesture = { start: at, tx: this.tx, ty: this.ty, moved: false, time: performance.now(), type: e.pointerType };
      clearTimeout(this.pressTimer);
      if (e.pointerType !== "mouse") this.pressTimer = setTimeout(() => this.setPeek(true), LONG_PRESS_MS);
    } else if (this.pointers.size === 2) {
      clearTimeout(this.pressTimer);
      const [a, b] = [...this.pointers.values()];
      this.pinch = { dist: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2],
        zoom: this.zoom, tx: this.tx, ty: this.ty };
      this.gesture.moved = true;
    }
  }

  move(e) {
    const at = this.local(e);
    if (!this.pointers.has(e.pointerId)) {
      if (e.pointerType === "mouse") {
        this.setHover(this.regionAt(at), this.tokenAt(at));
        this.artifactTip(at);
      }
      return;
    }
    this.pointers.set(e.pointerId, at);
    if (this.pointers.size >= 2 && this.pinch) {
      const [a, b] = [...this.pointers.values()];
      const dist = Math.hypot(a[0] - b[0], a[1] - b[1]);
      const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const p = this.pinch;
      const zoom = Math.min(MAX_ZOOM, Math.max(1, p.zoom * dist / p.dist));
      const k = this.k0 * zoom, k0 = this.k0 * p.zoom;
      // точка рисунка под серединой пальцев в начале жеста остаётся под их серединой
      const wx = (p.mid[0] - p.tx) / k0, wy = (p.mid[1] - p.ty) / k0;
      this.setZoom(zoom, null, mid[0] - wx * k, mid[1] - wy * k);
      return;
    }
    const g = this.gesture;
    if (!g) return;
    const dx = at[0] - g.start[0], dy = at[1] - g.start[1];
    if (!g.moved && Math.hypot(dx, dy) > TAP_MOVE) {
      g.moved = true;
      clearTimeout(this.pressTimer);
    }
    if (g.moved && this.zoom > 1) this.setZoom(this.zoom, null, g.tx + dx, g.ty + dy);
    if (e.pointerType === "mouse") this.setHover(this.regionAt(at), null);
  }

  up(e, cancelled = false) {
    const at = this.local(e);
    this.pointers.delete(e.pointerId);
    clearTimeout(this.pressTimer);
    if (this.pointers.size < 2) this.pinch = null;
    if (this.pointers.size) {
      // второй палец поднят: оставшийся продолжает сдвиг с того места, где он сейчас
      const [rest] = this.pointers.values();
      this.gesture = { start: rest, tx: this.tx, ty: this.ty, moved: true, time: 0 };
      return;
    }
    const g = this.gesture;
    this.gesture = null;
    if (this.peek) {
      this.setPeek(false);
      return;
    }
    if (cancelled || !g || g.moved) return;
    const t = performance.now();
    if (this.lastTap && t - this.lastTap.time < 320 && Math.hypot(at[0] - this.lastTap.at[0], at[1] - this.lastTap.at[1]) < 30) {
      this.lastTap = null;
      this.setZoom(this.zoom > 1.01 ? 1 : 2.5, at);   // двойной тап: крупнее в этом месте или вся карта
      return;
    }
    this.lastTap = { time: t, at };
    this.tap(at);
  }

  wheel(e) {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015));
    this.setZoom(this.zoom * factor, this.local(e));
  }

  tap(at) {
    const region = this.regionAt(at);
    if (region && this.picking() && region in this.options && this.onSelect) {
      this.selected = region;
      this.onSelect(region);
      this.redraw();
    } else {
      this.artifactTip(at);   // касание: тап по рисунку артефакта — подсказка о нём
    }
  }

  // рисунок артефакта, который сейчас лежит на карте, под точкой at
  artifactAt(at) {
    if (!this.geometry || !this.static || !this.state) return null;
    const [x, y] = this.toWorld(at).map((v) => v / this.scaleFactor);
    for (const [name, r] of Object.entries(this.regions)) {
      if (this.static.regions[name].object !== "artifact" || !this.state.available.includes(name)
        || !objectShown(this.state, name, "artifact")) continue;
      const [x0, y0, x1, y1] = r.object;
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) return name;
    }
    return null;
  }

  artifactTip(at) {
    const region = this.artifactAt(at);
    if (region) {
      const box = this.container.getBoundingClientRect();
      showTipAt(`artifact|${region}`, box.left + at[0], box.top + at[1], this.container);
    } else if (tipOwner() === "map") {
      hideTip();
    }
  }

  regionAt(at) {
    if (!this.geometry || !this.static) return null;
    const [x, y] = this.toWorld(at).map((v) => v / this.scaleFactor);
    for (const [name, r] of Object.entries(this.regions)) {
      const [x0, y0, x1, y1] = this.boxes[name];
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1 && inside(x, y, r.polygon)) return name;
    }
    return null;
  }

  tokenAt(at) {
    const [x, y] = this.toWorld(at);
    const rad = this.tokenRadius();
    for (const [key, [sx, sy]] of this.spots) {
      if (key.startsWith("player") && !this.moving.has(key) && (x - sx) ** 2 + (y - sy) ** 2 <= rad * rad) return key;
    }
    return null;
  }

  setHover(region, token) {
    if (token !== this.tokenHover) {
      this.tokenHover = token;
      for (const el of this.html.querySelectorAll(".token[data-key]")) {
        el.classList.toggle("faded", el.dataset.key === token);
      }
    }
    if (region === this.hover) return;
    this.hover = region;
    this.container.style.cursor = region && this.picking() && region in this.options ? "pointer" : "";
    this.drawHover();
  }

  setPeek(on) {
    this.peek = on;
    this.container.classList.toggle("peek", on);
    if (on) navigator.vibrate?.(15);
  }

  // --- размеры: в пикселях рисунка, но не меньше min пикселей экрана ----------------------------

  px(screen) {
    return screen / this.k;
  }

  size(base, min) {
    return Math.max(base, min / this.k);
  }

  tokenRadius() {
    return this.size(24, 15);
  }

  xy([x, y]) {
    return [x * this.scaleFactor, y * this.scaleFactor];
  }

  poly(name) {
    return points(this.regions[name].polygon.map((p) => this.xy(p)));
  }

  // текст со значками в белом ореоле; anchor — как в Tk: center, n, s, ne, se, nw
  text(x, y, content, size, anchor = "center", cls = "") {
    const el = rich(content, "div", "maptext halo" + (cls ? " " + cls : ""));
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.style.fontSize = `${size}px`;
    el.dataset.anchor = anchor;
    this.html.append(el);
    return el;
  }

  // --- рисование ---------------------------------------------------------------------------

  redraw() {
    clearTimeout(this.redrawTimer);
    if (!this.geometry || !this.static || !this.state || !this.k0) return;
    this.svg.replaceChildren();
    this.html.replaceChildren();
    this.world.style.setProperty("--halo", `${this.px(1.6)}px`);
    this.spots = new Map();
    const state = this.state, static_ = this.static;
    const available = new Set(state.available);
    const kinds = Object.fromEntries(Object.keys(this.regions).map((name) => [name, static_.regions[name].object]));
    const W = WIDTH, H = this.worldH * this.scaleFactor;
    this.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    this.svg.setAttribute("width", W);
    this.svg.setAttribute("height", H);
    // объекты, которые ещё на месте: кусочки слоя объектов
    const clip = svg("clipPath", { id: "objects-clip" });
    for (const name of Object.keys(this.regions)) {
      if (available.has(name) && objectShown(state, name, kinds[name])) {
        const [x0, y0, x1, y1] = this.regions[name].object.map((v) => v * this.scaleFactor);
        clip.append(svg("rect", { x: x0 - 1, y: y0 - 1, width: x1 - x0 + 3, height: y1 - y0 + 3 }));
      }
    }
    this.svg.append(svg("defs"), svg("image", { href: `${ASSETS}/objects_${WIDTH}.png`, width: W, height: H, "clip-path": "url(#objects-clip)" }));
    this.svg.firstChild.append(clip);
    for (const [name, terrain] of Object.entries(state.terrain || {})) {   // Гейзер: регион теперь озёрный
      if (available.has(name) && terrain !== static_.regions[name].terrain) {
        this.svg.append(svg("polygon", { points: this.poly(name), fill: LAKE, "fill-opacity": 0.3, stroke: LAKE,
          "stroke-width": this.px(2) }));
      }
    }
    // вычеркнутые — стереть, при выборе — притушить недоступные (кусочки атласа теней)
    this.shownNow = this.shownOptions();
    const shown = this.shownNow;
    const selected = this.picking() ? this.selected : null;
    for (const name of Object.keys(this.regions)) {
      const dark = !available.has(name), wash = shown !== null && !(name in shown);
      if (!dark && !wash) continue;
      const [ax, ay, w, hh, x, y] = this.shades.regions[name];
      const piece = svg("svg", { x, y, width: w, height: hh, viewBox: `${ax} ${ay + (dark ? this.shades.half : 0)} ${w} ${hh}` });
      piece.append(svg("image", { href: `${ASSETS}/shade_${WIDTH}.png`, width: W, height: this.shades.half * 2 }));
      this.svg.append(piece);
    }
    if (shown !== null) {
      for (const name of Object.keys(shown)) {
        if (name !== selected) this.svg.append(svg("polygon", { class: "outline", points: this.poly(name), stroke: SELECT,
          "stroke-width": this.size(3, 2.5) }));
      }
    }
    if (selected) this.svg.append(svg("polygon", { class: "outline", points: this.poly(selected), stroke: CHOSEN,
      "stroke-width": this.size(4.5, 3.5) }));
    this.hoverEl = svg("polygon", { class: "outline hover", stroke: "white", "stroke-width": this.size(2.5, 2), points: "" });
    this.svg.append(this.hoverEl);
    for (const name of Object.keys(this.regions)) if (available.has(name)) this.marks(name, kinds[name]);
    this.routes();
    for (const name of Object.keys(this.regions)) if (available.has(name)) this.label(name);
    this.movement();
    this.drawHover();
    this.animate();
  }

  drawHover() {
    if (!this.hoverEl) return;
    this.hoverEl.setAttribute("points", this.hover ? this.poly(this.hover) : "");
  }

  label(name) {
    const static_ = this.static, state = this.state, r = static_.regions[name];
    const [cx, cy] = this.xy(this.regions[name].label);
    const shown = this.shownNow;
    const dim = shown !== null && !(name in shown);
    const size = this.size(20, 11), small = this.size(17, 10);
    const line = size * 1.2;
    const cls = dim ? "dim" : "";
    this.text(cx, cy, name, size, "center", "name " + cls);
    let y = cy - line / 2;
    // над названием: точки числа игроков и значок местности
    if (r.min_players > 2) {
      const dot = this.size(2.8, 1.8), gap = this.size(7.5, 5);
      y -= dot + 3;
      const xs = r.min_players === 3 ? [-1, 0, 1] : [-1.9, -0.9, 0.9, 1.9];
      for (const k of xs) this.svg.append(svg("circle", { cx: cx + k * gap, cy: y, r: dot, fill: dim ? "var(--muted)" : "var(--ink)" }));
      y -= dot;
    }
    const terrain = terrainOf(static_, state, name);
    const glyph = line * (terrain === "lake" ? 0.55 : 0.75);
    const lift = r.min_players > 2 ? 7 : 2;
    const img = h("img.glyph", { src: `assets/icons/${terrain}_40.png`, alt: "", draggable: false });
    Object.assign(img.style, { left: `${cx}px`, top: `${y - lift}px`, height: `${glyph}px` });
    this.html.append(img);
    const head = y - lift - glyph;
    // под названием: ресурсы первого посещения (бледные — если уже получены)
    y = cy + line / 2;
    const you = this.you !== null ? state.players[this.you] : null;
    if (Object.keys(r.resources || {}).length) {
      const got = you !== null && you.visited.includes(name);
      this.text(cx, y, res(r.resources, got), small, "n", got ? "faded" : cls);
      y += small * 1.25;
    }
    if (r.object === "fortress" && objectShown(state, name, "fortress")) this.fortress(name, r.fortress);
    // фишки игроков — аватарки (обводка: чёрная — чья это карта, белая — остальные); гидры — рисунок гидры
    const here = [];
    state.players.forEach((pl, p) => { if (pl.region === name) here.push(`player:${p}`); });
    state.players.forEach((pl, p) => { if (pl.hydra === name) here.push(`hydra:${p}`); });
    if (here.length) {
      const rad = this.tokenRadius(), step = rad * 2 + this.px(3);
      const tx = cx - (step * (here.length - 1)) / 2, ty = head - rad - this.px(2);
      here.forEach((key, i) => {
        const spot = [tx + i * step, ty];
        this.spots.set(key, spot);
        const move = this.moving.get(key);
        if (move) move.to = spot;   // едет сюда: рисует animate
        else this.html.append(this.token(key, spot));
      });
    }
    // подпись в режиме выбора
    if (shown && shown[name]) {
      this.text(cx, y + 4, shown[name], this.size(13, 9), "n", "badge").style.background = SELECT;
    }
  }

  token(key, [x, y], movingNow = false) {
    const [kind, p] = [key.split(":")[0], Number(key.split(":")[1])];
    const rad = this.tokenRadius();
    const el = h("div.token" + (kind === "hydra" ? ".hydra" : ""));
    Object.assign(el.style, { left: `${x}px`, top: `${y}px`, width: `${rad * 2}px`, height: `${rad * 2}px`,
      borderWidth: `${this.px(kind === "hydra" ? 3 : 2)}px` });
    if (kind === "hydra") {
      el.style.borderColor = color(p);
      el.append(h("img", { src: "assets/icons/hydra_40.png", alt: "", draggable: false }));
    } else {
      el.style.borderColor = p === this.you ? "black" : "white";
      el.append(icon(`avatar${p}`));
      if (!movingNow) {
        el.dataset.key = key;
        if (key === this.tokenHover) el.classList.add("faded");
      }
    }
    return el;
  }

  // слева от крепости, как на планшете: над чертой — сила, под ней — ПО и добыча
  fortress(name, fortress) {
    const [ox0, oy0, , oy1] = this.regions[name].object;
    const [right, mid] = this.xy([ox0 - 4, (oy0 + oy1) / 2 + 2]);
    const big = this.size(21, 11), small = this.size(17, 10);
    const top = this.text(right, mid - 2, `${fortress.strength}${mark("shield")}`, big, "se", "strong");
    const loot = Object.entries(fortress.loot).map(([k, v]) => res({ [k]: v }));
    let y = mid + 3;
    const bottom = this.text(right, y, `${fortress.vp}${mark("laurel")}  ${loot[0] || ""}`, small, "ne", "strong");
    for (const more of loot.slice(1)) {
      y += small * 1.25;
      this.text(right, y, more, small, "ne", "strong");
    }
    // черта — по ширине надписей: её дорисуем, когда браузер их разместит
    requestAnimationFrame(() => {
      if (!top.isConnected) return;
      const width = Math.max(top.offsetWidth, bottom.offsetWidth);
      this.svg.append(svg("line", { x1: right - width - 2, y1: mid, x2: right + 2, y2: mid, stroke: "var(--ink)",
        "stroke-width": this.size(2, 1) }));
    });
  }

  // отметки у объекта: флажок захватчика, проклятие, бронь, уровни фундаментов
  marks(name, kind) {
    const state = this.state;
    const [x0, y0, x1, y1] = this.regions[name].object;
    const [ax, ay] = this.xy([x0, y0]), [bx, by] = this.xy([x1, y1]);
    const mid = (ax + bx) / 2;
    const owner = kind === "fortress" ? state.fortress_owner[name] : undefined;
    if (owner !== undefined && (this.you === null || this.you === owner)) {   // флажок — только у захватчика
      const foot = by - 4, top = foot - 34;
      this.svg.append(svg("line", { x1: mid, y1: foot, x2: mid, y2: top, stroke: "#3b2f22", "stroke-width": 2.5 }),
        svg("polygon", { points: `${mid},${top} ${mid + 22},${top + 7} ${mid},${top + 14}`, fill: color(owner), stroke: "white", "stroke-width": 1 }),
        svg("ellipse", { cx: mid, cy: foot, rx: 6, ry: 2, fill: "#3b2f22" }));
    }
    if (name in (state.cursed || {})) {   // проклятие нежити: значок в обводке цвета проклявшего
      const p = state.cursed[name], r = this.size(13, 9);
      const cx = ax + 2, cy = ay + 8;
      this.svg.append(svg("image", { href: "assets/icons/curse_40.png", x: cx - r, y: cy - r, width: r * 2, height: r * 2 }),
        svg("circle", { cx, cy, r, fill: "none", stroke: color(p), "stroke-width": this.size(3, 2) }));
    }
    if (name in state.reserved) {
      const p = state.reserved[name], r = this.size(10, 8);
      const rx = bx - 2, ry = ay + 8;
      this.svg.append(svg("rect", { x: rx - r, y: ry - r, width: r * 2, height: r * 2, fill: color(p), stroke: "white", "stroke-width": this.px(2) }));
      this.text(rx, ry, "Б", r * 1.2, "center", "square");
    }
    if (kind === "foundation" && this.you !== null) {
      // уровень своего здания вписан в фундамент, как маркером на планшете; чужие не видны
      const level = state.players[this.you].foundations[name];
      if (level) this.text(mid, (ay + by) / 2, String(level), this.size(34, 14), "center", "level");
    } else if (kind === "foundation") {   // зритель видит всех: уровни цветом игроков
      const levels = state.players.map((pl, p) => [p, pl.foundations[name]]).filter(([, level]) => level);
      const r = this.size(9, 7), step = r * 2 + this.px(3);
      const lx = mid - (step * (levels.length - 1)) / 2;
      levels.forEach(([p, level], i) => {
        const x = lx + i * step, y = by + r;
        this.svg.append(svg("rect", { x: x - r, y: y - r, width: r * 2, height: r * 2, fill: color(p), stroke: "white", "stroke-width": this.px(2) }));
        this.text(x, y, String(level), r * 1.2, "center", "square");
      });
    }
  }

  // свой маршрут: изогнутая стрелка на каждое перемещение, последнее — ярче
  routes() {
    if (this.you === null) return;
    const routes = this.state.players[this.you].routes || [];
    if (!routes.length) return;
    const defs = this.svg.querySelector("defs");
    routes.forEach(([a, b], k) => {
      const last = k === routes.length - 1;
      const col = last ? MARKER : OLD_ROUTE, width = last ? this.size(4.5, 3) : this.size(3, 2);
      const id = `arrow-${k}`;
      const marker = svg("marker", { id, viewBox: "0 0 10 10", refX: 6, refY: 5, markerWidth: 4, markerHeight: 4, orient: "auto-start-reverse" });
      marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: col }));
      defs.append(marker);
      let [x0, y0] = this.xy(this.regions[a].center), [x1, y1] = this.xy(this.regions[b].center);
      const dx = x1 - x0, dy = y1 - y0, length = Math.max(1, Math.hypot(dx, dy));
      const ux = dx / length, uy = dy / length;
      const trim = Math.min(length / 4, 16);
      x0 += ux * trim; y0 += uy * trim; x1 -= ux * trim; y1 -= uy * trim;
      const bend = Math.min(0.2 * length, 70);
      const cx = (x0 + x1) / 2 - uy * bend, cy = (y0 + y1) / 2 + ux * bend;
      this.svg.append(svg("path", { d: `M ${x0} ${y0} Q ${cx} ${cy} ${x1} ${y1}`, fill: "none", stroke: col, "stroke-width": width,
        "stroke-linecap": "round", "marker-end": `url(#${id})` }));
    });
  }

  // дорожка стоимости перемещения, как на планшете (правый нижний угол рисунка)
  movement() {
    const static_ = this.static, state = this.state;
    const me = state.players[this.you !== null ? this.you : 0];
    const faction = static_.factions[me.faction];
    const base = faction.movement, mods = faction.movement_mods || {};
    const current = this.you !== null ? me.move_cost : base;
    const [x0, y0] = this.xy([905, 858]), [x1, y1] = this.xy([1405, 936]);
    this.svg.append(svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: "#fbf6ea", stroke: "var(--ink)", "stroke-width": 2.5 }));
    const mid = (y0 + y1) / 2 - 2;
    this.svg.append(svg("line", { x1: x0 + 10, y1: mid, x2: x1 - 10, y2: mid, stroke: "var(--ink)", "stroke-width": 1.5 }));
    const col = (x1 - x0) / TERRAINS.length;
    TERRAINS.forEach((terrain, i) => {
      const cx = x0 + col * (i + 0.5);
      this.svg.append(svg("image", { href: `assets/icons/${terrain}_40.png`, x: cx - 16, y: mid - 4 - 30, width: 32, height: 30,
        preserveAspectRatio: "xMidYMax meet" }));
      const was = base[terrain], now_ = current[terrain];
      const el = h("div.maptext.track");
      if (terrain in mods && mods[terrain] < was) {
        const active = now_ <= mods[terrain];
        el.append(rich(`${was}${mark("mp")}`, "span", active ? "off" : ""), rich(mark("modify")),
          rich(`${mods[terrain]}${mark("mp")}`, "span", active ? "" : "off"));
      } else {
        el.append(rich(`${was}${mark("mp")}`));
      }
      Object.assign(el.style, { left: `${cx}px`, top: `${mid + 4}px`, fontSize: "21px" });
      el.dataset.anchor = "n";
      this.html.append(el);
    });
  }

  // --- фишки в пути и таблички -----------------------------------------------------------------

  startMoves(old, fresh) {
    const t = now();
    for (const key of this.spots.keys()) {
      const [kind, p] = key.split(":");
      const field = kind === "player" ? "region" : "hydra";
      const was = old.players[p][field], dest = fresh.players[p][field];
      if (was && dest && was !== dest) this.moving.set(key, { from: this.spotAt(key, t), to: null, start: t });
    }
  }

  spotAt(key, t) {
    const move = this.moving.get(key);
    if (!move || !move.to) return this.spots.get(key);
    let s = Math.min(1, (t - move.start) / MOVE_SECONDS);
    s = s * s * (3 - 2 * s);
    return [move.from[0] + (move.to[0] - move.from[0]) * s, move.from[1] + (move.to[1] - move.from[1]) * s];
  }

  animate() {
    if (this.tick === null) this.tick = requestAnimationFrame(() => this.step());
  }

  step() {
    this.tick = null;
    const t = now();
    let arrived = false;
    for (const [key, move] of [...this.moving]) {
      if (!move.to || t - move.start >= MOVE_SECONDS) {
        this.moving.delete(key);
        arrived = true;
      }
    }
    if (arrived) {
      this.redraw();   // доехавшие встают на свои места
      return;
    }
    this.anim.replaceChildren();
    for (const [key, move] of this.moving) if (move.to) this.anim.append(this.token(key, this.spotAt(key, t), true));
    this.drawPopups(t);
    if (this.moving.size || this.popups.length) this.animate();
  }

  // таблички стопкой над фишкой: новая всплывает у фишки и подталкивает старшие вверх, под конец тают
  drawPopups(t) {
    this.popups = this.popups.filter((pop) => t - pop.start < POPUP_SECONDS);
    const rad = this.tokenRadius(), face = this.size(17, 11), line = face * 1.25, step = line + this.px(9);
    const stacks = new Map();
    for (const pop of this.popups) {
      if (this.spots.has(pop.key) && pop.start <= t) {
        if (!stacks.has(pop.key)) stacks.set(pop.key, []);
        stacks.get(pop.key).unshift(pop);   // новые — первыми, у самой фишки
      }
    }
    for (const [key, pops] of stacks) {
      const [x, y] = this.spotAt(key, t);
      const placed = [];
      let below = 0;
      for (const pop of pops.slice(0, POPUP_STACK)) {
        let grow = Math.min(1, (t - pop.start) / POPUP_APPEAR);
        grow = 1 - (1 - grow) ** 2;
        placed.push([pop, below + 10 * grow]);
        below += step * grow;
      }
      const up = y - rad - 6 - placed[placed.length - 1][1] - line >= 0;
      for (const [pop, lift] of placed) {
        const bottom = up ? y - rad - this.px(6) - lift : y + rad + this.px(6) + line + lift;
        const age = (t - pop.start) / POPUP_SECONDS;
        const el = rich(pop.text, "div", "maptext popup");
        Object.assign(el.style, { left: `${x}px`, top: `${bottom}px`, fontSize: `${face}px`, background: color(Number(key.split(":")[1])),
          opacity: age > 0.6 ? Math.max(0, 1 - (age - 0.6) / 0.4) : 1, borderWidth: `${this.px(2)}px` });
        el.dataset.anchor = "s";
        this.anim.append(el);
      }
    }
  }
}
