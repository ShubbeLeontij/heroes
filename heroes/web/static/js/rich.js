// Значки в тексте: метки частной области Юникода (heroes/web/marks.py) → картинки из assets/icons.
// Порядок меток и всё справочное приходит с /ui.json.

export let UI = null;
let KINDS = new Map();       // символ метки → вид значка
let MARK = {};               // вид значка → символ метки
let SPLIT = null;
let seatAvatar = () => "";

const ICON_HEIGHT = 40;      // берём самый крупный размер: браузер сам уменьшит под строку

export async function loadUI() {
  UI = await (await fetch("ui.json")).json();
  UI.marks.forEach((kind, i) => {
    const mark = String.fromCharCode(UI.mark_base + i);
    KINDS.set(mark, kind);
    MARK[kind] = mark;
  });
  SPLIT = new RegExp(`([${[...KINDS.keys()].join("")}])`, "u");
  return UI;
}

export function mark(kind) {
  return MARK[kind] || "";
}

// артефакт региона его рисунком (marks.artifact)
export function artifactMark(region) {
  return MARK[`artifact_${region}`] || `${MARK.artifact || ""} ${region}`;
}

// «4{wood}» → «4» + метка дерева (тексты зданий и артефактов «как на планшете», marks.markup)
export function markup(text) {
  return text.replace(/\{(\w+)\}/g, (all, word) => MARK[word] || all);
}

// откуда брать аватарку места p: задаёт main.js по данным партии
export function setSeatAvatar(fn) {
  seatAvatar = fn;
}

export function iconSrc(kind) {
  return `assets/icons/${encodeURIComponent(kind)}_${ICON_HEIGHT}.png`;
}

export function icon(kind, cls = "") {
  const img = document.createElement("img");
  const artifact = kind.startsWith("artifact_") ? kind.slice(9) : null;   // рисунок артефакта: его регион — подсказкой
  img.className = `ic ic-${artifact !== null ? "art" : kind.replace(/_off$/, "")} ${cls}`.trim();
  img.alt = artifact || "";
  if (artifact) img.title = artifact;
  img.draggable = false;
  if (kind.startsWith("avatar")) {
    img.src = seatAvatar(Number(kind.slice(6)));
    img.classList.add("ic-avatar");
  } else {
    img.src = iconSrc(kind);
    if (kind.endsWith("_off")) img.classList.add("ic-off");
  }
  return img;
}

// Текст с метками → элемент (span по умолчанию) с картинками вместо меток.
export function rich(text, tag = "span", cls = "") {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  appendRich(el, text);
  return el;
}

export function appendRich(el, text) {
  text = String(text ?? "");
  if (!SPLIT) {
    el.append(text);
    return el;
  }
  for (const part of text.split(SPLIT)) {
    if (!part) continue;
    const kind = KINDS.get(part);
    el.append(kind ? icon(kind) : part);
  }
  return el;
}

// Короткий помощник для разметки: h("div.cls", {attrs}, ...children); строка-ребёнок — текст со значками.
export function h(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") {
      for (const [prop, value] of Object.entries(v)) {
        if (prop.startsWith("--")) el.style.setProperty(prop, value);   // CSS-переменные — только так
        else el.style[prop] = value;
      }
    }
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    if (child instanceof Node) el.append(child);
    else appendRich(el, child);
  }
  return el;
}
