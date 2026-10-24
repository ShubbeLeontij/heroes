// Подсказки: полная информация об артефакте, чудовище или ресурсах — при наведении мышью или по тапу
// (телефон, планшет). Элемент с подсказкой помечен data-tip="вид|ключ|…"; что в ней показать, решает
// поставщик (setTipProvider в main.js), — по свежему снимку партии на момент показа.

import { h } from "./rich.js";

const GAP = 6;               // от края элемента до подсказки, px
const TAP_GRACE_MS = 400;    // прокрутка сразу после показа подсказку не закрывает

let provider = () => null;
let box = null;
let anchor = null;           // элемент (или "map"), к которому сейчас относится подсказка
let zone = null;             // где тапы решает сам владелец подсказки (карта), а не щелчок по документу
let shownAt = 0;
let lastPointer = "mouse";

export function setTipProvider(fn) {
  provider = fn;
}

export function tipAttr(...parts) {
  return { "data-tip": parts.join("|") };
}

function content(spec) {
  const [kind, ...args] = spec.split("|");
  try {
    return provider(kind, args);
  } catch (err) {
    console.error(err);
    return null;
  }
}

function place(rect) {
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  box.style.left = "0px";
  box.style.top = "0px";
  const w = box.offsetWidth, ht = box.offsetHeight;
  const left = Math.max(GAP, Math.min(vw - w - GAP, rect.left + rect.width / 2 - w / 2));
  let top = rect.bottom + GAP;
  if (top + ht > vh - GAP && rect.top - GAP - ht >= GAP) top = rect.top - GAP - ht;   // снизу не помещается — сверху
  box.style.left = `${left}px`;
  box.style.top = `${Math.max(GAP, Math.min(top, vh - ht - GAP))}px`;
}

function show(spec, rect, owner) {
  const body = content(spec);
  if (!body) {
    hideTip();
    return;
  }
  if (!box) {
    box = h("div.tip", { role: "tooltip" });
    document.body.append(box);
  }
  box.replaceChildren(body);
  box.classList.remove("hidden");
  anchor = owner;
  shownAt = performance.now();
  place(rect);
}

export function hideTip() {
  if (box) box.classList.add("hidden");
  anchor = null;
  zone = null;
}

// подсказка к точке экрана (карта: артефакт нарисован на ней, а не отдельным элементом); тапы внутри
// area она обрабатывает сама — щелчок, который браузер присылает следом, подсказку не закрывает
export function showTipAt(spec, x, y, area, owner = "map") {
  if (anchor === owner && box?.dataset.spec === spec) return;
  show(spec, { left: x, right: x, top: y - 12, bottom: y + 12, width: 0, height: 24 }, owner);
  if (box) box.dataset.spec = spec;
  if (anchor === owner) zone = area;
}

export function tipOwner() {
  return anchor;
}

function target(e) {
  return e.target instanceof Element ? e.target.closest("[data-tip]") : null;
}

function showFor(el) {
  if (box) delete box.dataset.spec;
  show(el.dataset.tip, el.getBoundingClientRect(), el);
}

document.addEventListener("pointerdown", (e) => {
  lastPointer = e.pointerType;
}, true);

document.addEventListener("pointerover", (e) => {
  if (e.pointerType !== "mouse") return;
  const el = target(e);
  if (el && el !== anchor) showFor(el);
  else if (!el && anchor && anchor !== "map" && !box?.contains(e.target)) hideTip();
});

// касание: тап по элементу с подсказкой — показать (повторный — убрать), тап мимо — убрать
document.addEventListener("click", (e) => {
  if (lastPointer === "mouse") return;
  const el = target(e);
  if (el) {
    if (el === anchor) hideTip();
    else showFor(el);
  } else if (anchor && !box?.contains(e.target) && !zone?.contains(e.target)) {
    hideTip();
  }
});

// прокрутка уводит элемент из-под подсказки — убрать; прокрутка в другом месте (журнал пополнился) — не мешает
window.addEventListener("scroll", (e) => {
  if (!(anchor instanceof Element) || performance.now() - shownAt < TAP_GRACE_MS) return;
  if (e.target === document || (e.target instanceof Node && e.target.contains(anchor))) hideTip();
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideTip();
});
