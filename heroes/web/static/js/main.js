// Веб-клиент «Героев»: подключение к партии хозяина (за своё место или зрителем), экран партии
// и профили со статистикой — для браузера на телефоне, планшете и ПК. Партия идёт у хозяина
// (python -m heroes); сюда приходят снимки состояния и решения данными, ответы проверяет его движок.

import { Link } from "./net.js";
import { UI, loadUI, h, mark, rich, appendRich, icon, setSeatAvatar } from "./rich.js";
import * as C from "./common.js";
import { makePanel, isSecret } from "./panels.js";
import { MapView } from "./map.js";
import { BoardView, playersSummary, tipContent } from "./board.js";
import { setTipProvider } from "./tips.js";
import { ProfilesScreen, squareAvatar } from "./profiles.js";

// localStorage может быть недоступен (приватный режим): тогда просто ничего не помним
const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try {
      if (value === null || value === undefined) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch { /* без памяти */ }
  },
};
const session = {
  get(key) {
    try { return sessionStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch { /* без памяти */ }
  },
};

const $ = (id) => document.getElementById(id);

// Разделительные черты широкого экрана (index.html, .splitter): тянутся мышью, двойной щелчок — как было.
// Положение — доля #main, запоминается в браузере; наименьшие размеры частей — в пикселях.
const SPLITS = {
  "split-x": { key: "heroes.split.x", rows: false, min: 300, rest: 360,   // ширина карты; планшетам — не уже 360px (style.css)
    props: (share) => ({ "--map-fr": `${share}fr`, "--boards-fr": `${100 - share}fr` }) },
  "split-y": { key: "heroes.split.y", rows: true, min: 100, rest: 120,    // высота решения; карте — не ниже 120px
    props: (share) => ({ "--decision-min": "100px", "--decision-h": `${share}%` }) },
};

function setupSplitters() {
  const main = $("main");
  for (const [id, split] of Object.entries(SPLITS)) {
    const bar = $(id);
    const apply = (share) => {
      for (const [prop, value] of Object.entries(split.props(share ?? 50))) {
        if (share === null) main.style.removeProperty(prop);
        else main.style.setProperty(prop, value);
      }
    };
    const saved = Number(store.get(split.key));
    if (saved > 0 && saved < 100) apply(saved);
    const done = (e) => {
      if (!bar.hasPointerCapture(e.pointerId)) return;
      bar.releasePointerCapture(e.pointerId);
      bar.classList.remove("dragging");
      document.body.classList.remove("splitting", "rows");
    };
    bar.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      bar.setPointerCapture(e.pointerId);
      bar.classList.add("dragging");
      document.body.classList.add("splitting", ...(split.rows ? ["rows"] : []));
    });
    bar.addEventListener("pointermove", (e) => {
      if (!bar.hasPointerCapture(e.pointerId)) return;
      const r = main.getBoundingClientRect(), size = split.rows ? r.height : r.width;
      const at = split.rows ? r.bottom - e.clientY : e.clientX - r.left;
      const px = Math.max(split.min, Math.min(size - split.rest, at));
      const share = Math.round(px / size * 1000) / 10;
      apply(share);
      store.set(split.key, share);
    });
    bar.addEventListener("pointerup", done);
    bar.addEventListener("pointercancel", done);
    bar.addEventListener("dblclick", () => {
      apply(null);
      store.set(split.key, null);
    });
  }
}

class App {
  constructor() {
    this.static = null;
    this.state = null;
    this.viewer = null;       // наше место за столом
    this.boardPlayer = null;  // чей планшет и чья карта открыты
    this.pending = null;      // решение, ждущее нашего ответа
    this.panel = null;
    this.ended = false;
    this.unread = 0;
    this.watching = false;    // зритель: места за столом нет, решений не бывает
    this.link = new Link({ message: (m) => this.handle(m), state: (text) => this.connection(text) });
    this.profiles = new ProfilesScreen($("profiles"), {
      password: () => $("join-password").value || store.get("heroes.password"),
      onBack: () => this.joinScreen(null, false),
      onUse: (profile) => {   // с этим именем и аватаркой и подключаемся (имя из ссылки больше не главное)
        store.set("heroes.name", profile.name);
        store.set("heroes.avatar", profile.avatar);
        const password = $("join-password").value;
        if (password) store.set("heroes.password", password);
        history.replaceState(null, "", location.pathname);
        this.joinScreen(null, false);
      },
    });
    this.map = new MapView($("map"));
    this.board = new BoardView($("board"));
    this.boardTab = null;     // открытая вкладка планшетов: место игрока или "all" — «Все игроки»
    this.boardStale = false;  // вкладка планшетов скрыта (телефон): перерисовать, когда её откроют
    this.movePeek = false;    // наведён конь в полосе раунда: карта показывает, куда дойдёт игрок
    setSeatAvatar((p) => this.avatarSrc(p));
    setTipProvider((kind, args) => (this.static && this.state ? tipContent(this.static, this.state, kind, args, this.viewer) : null));
    for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => {   // решение, журнал: карта — своя
      if (this.viewer !== null && this.boardPlayer !== this.viewer) this.openBoard(this.viewer);
      this.setView(b.dataset.view);
    });
    setupSplitters();
    $("log-toggle").addEventListener("click", () => this.toggleLog());
    $("log-close").addEventListener("click", () => this.toggleLog(false));
    $("join-watch").addEventListener("click", () => this.join(true));
    $("join-profiles").addEventListener("click", () => this.profilesScreen());
    if (location.hash === "#profiles") this.profilesScreen();
    else this.joinScreen();
  }

  profilesScreen() {
    if (!this.static) {   // ожидающий выбора места запрос к хозяину не нужен, пока смотрим профили
      this.link.ended = true;
      this.link.ws?.close();
    }
    document.body.dataset.screen = "profiles";
    this.profiles.open();
  }

  // --- подключение ---------------------------------------------------------------------------

  // auto — сразу подключиться, если страницу перезагрузили посреди партии или открыли ссылкой с именем
  joinScreen(error = null, auto = true) {
    document.body.dataset.screen = "join";
    this.asked = false;   // join() уже вызывали (кнопкой, пока ждали ответ о пароле)?
    $("join-host").textContent = location.host;
    const name = $("join-name"), password = $("join-password");
    // ссылка вида ?name=Боря&password=… (её печатает хозяин) — сразу за это место
    const params = new URLSearchParams(location.search);
    name.value = params.get("name") ?? store.get("heroes.name") ?? "";
    password.value = params.get("password") ?? store.get("heroes.password") ?? "";
    this.avatar = store.get("heroes.avatar");
    this.showAvatar();
    $("join-error").textContent = error || "";
    $("join-error").classList.toggle("hidden", !error);
    $("join-seats").replaceChildren();
    $("join-form").onsubmit = (e) => {
      e.preventDefault();
      this.join();
    };
    $("join-photo").onchange = (e) => this.pickAvatar(e.target.files[0]);
    $("join-photo-clear").onclick = () => {
      this.avatar = null;
      store.set("heroes.avatar", null);
      this.showAvatar();
    };
    this.gate(error, auto);
  }

  // без пароля партии поле пароля и кнопка не нужны: сразу просим у хозяина свободные места
  async gate(error, auto) {
    if (this.locked === undefined) {
      try { this.locked = (await (await fetch("access", { cache: "no-store" })).json()).password; } catch { this.locked = true; }
    }
    if (this.asked) return;
    const open = !this.locked;
    $("join-password").parentElement.classList.toggle("hidden", open);
    $("join-submit").classList.toggle("hidden", open && !error);
    if (open && !error && !session.get("heroes.watching") && !session.get("heroes.playing")) {
      this.join();
      return;
    }
    // страницу перезагрузили посреди партии — сразу подключаемся снова
    const params = new URLSearchParams(location.search), name = $("join-name");
    if (!auto || error) return;
    if (session.get("heroes.watching")) this.join(true);
    else if (session.get("heroes.playing") && session.get("heroes.seat")) {
      name.value = session.get("heroes.seat");
      this.join();
    } else if (params.has("name") && name.value) this.join();
  }

  // watch — смотреть партию, не занимая места
  join(watch = false) {
    this.asked = true;
    const name = $("join-name").value.trim(), password = $("join-password").value;
    if (!watch) store.set("heroes.name", null);   // имя из профиля работает один раз; дальше хозяин предлагает места
    store.set("heroes.password", password || null);
    $("join-error").classList.add("hidden");
    $("join-submit").disabled = true;
    $("join-status").textContent = "Подключение…";
    this.watching = watch;
    this.link.connect(watch ? null : name || null, password || null, watch ? null : this.avatar, watch);
  }

  showAvatar() {
    const img = $("join-avatar");
    img.src = this.avatar ? `data:image/png;base64,${this.avatar}` : "assets/avatars/human_neutral.png";
    $("join-photo-clear").classList.toggle("hidden", !this.avatar);
  }

  // своя картинка → квадрат PNG (profiles.squareAvatar); в конце партии хозяин кладёт её в профиль
  async pickAvatar(file) {
    const encoded = await squareAvatar(file);
    if (!encoded) return;
    this.avatar = encoded;
    store.set("heroes.avatar", encoded);
    this.showAvatar();
  }

  connection(text) {
    $("join-status").textContent = text || "";
    const banner = $("banner");
    banner.textContent = text || "";
    banner.classList.toggle("hidden", !text);
  }

  // --- сообщения партии ------------------------------------------------------------------------

  handle(m) {
    if ("state" in m) this.state = m.state;
    switch (m.t) {
      case "choose":
        this.chooseSeat(m.seats);
        break;
      case "welcome":
        this.gameScreen();
        if (m.watch) {
          session.set("heroes.watching", "1");
          this.setStatus("Вы смотрите партию");
          document.title = "«Герои» — зритель";
          this.waiting(null, "Вы смотрите партию. Ждём, пока все сядут за стол…");
        } else {
          session.set("heroes.playing", "1");
          session.set("heroes.seat", m.name);
          this.setStatus(`Вы за столом: ${m.name}`);
          document.title = `«Герои» — ${m.name}`;
          this.waiting(null, `Вы подключены как ${m.name}. Ждём начала партии…`);
        }
        break;
      case "sync":
        this.static = m.static;
        if (m.you !== null && m.you !== undefined) this.viewer = this.boardPlayer = m.you;
        if (this.boardPlayer === null) this.boardPlayer = 0;
        if (this.boardTab !== "all") this.boardTab = this.boardPlayer;
        this.gameScreen();
        this.buildBoardTabs();
        this.clearLog();
        (m.log_ru || m.log).forEach((line) => this.addLog(line));
        this.title();
        break;
      case "event":
        this.addLog(m.text_ru ?? m.text);
        this.map.event(m.popup);
        break;
      case "decision":
        this.decision(m);
        break;
      case "end":
        session.set("heroes.playing", null);
        session.set("heroes.watching", null);
        this.finish(m.text || "", m.text_ru);
        break;
      case "error":
        session.set("heroes.playing", null);
        session.set("heroes.watching", null);
        if (document.body.dataset.screen === "join" || !this.static) this.joinScreen(m.text_ru || m.text);
        else this.failed(m.text_ru || m.text);
        break;
    }
    if (this.state && this.static) this.refresh();
  }

  chooseSeat(seats) {
    document.body.dataset.screen = "join";
    $("join-submit").disabled = false;
    $("join-status").textContent = "Какое место за столом ваше?";
    $("join-seats").replaceChildren(...seats.map((seat) => h("button.seat", {
      type: "button",
      onclick: () => {
        $("join-name").value = seat;
        $("join-seats").replaceChildren();
        $("join-status").textContent = "Подключение…";
        this.link.chooseSeat(seat);
      },
    }, seat)));
  }

  gameScreen() {
    if (document.body.dataset.screen === "game") return;
    document.body.dataset.screen = "game";
    $("join-submit").disabled = false;
    $("join-status").textContent = "";
    if (!document.body.dataset.view) this.setView("decision");
  }

  avatarSrc(p) {
    const own = (this.static?.avatars || [])[p];
    if (own) return `data:image/png;base64,${own}`;
    const kind = (this.static?.kinds || [])[p];
    return `assets/avatars/${kind === "claude" || kind === "bot" ? kind : "human"}_${p % 4}.png`;
  }

  title() {
    if (this.watching) document.title = "«Герои» — зритель";
    if (this.viewer === null || !this.static) return;
    document.title = (this.pending ? "● Ваш ход — " : "") + `«Герои» — ${C.nameOf(this.static, this.viewer)}`;
  }

  // --- вид экрана ------------------------------------------------------------------------------

  // На телефоне карта видна всегда, а под ней (сбоку) — одна часть: решение, планшет игрока, сводка или журнал
  // (вкладки внизу или сбоку); на широком экране — все, и view не важен.
  setView(view) {
    document.body.dataset.view = view;
    this.markTabs();
    if (view === "log") this.markRead();
    if (view === "boards" && this.boardStale) this.showBoard();
  }

  toggleLog(open = !document.body.classList.contains("log-open")) {
    document.body.classList.toggle("log-open", open);
    if (open) this.markRead();
  }

  logVisible() {
    const pane = $("log-pane");
    return pane.offsetParent !== null && getComputedStyle(pane).visibility !== "hidden";
  }

  markRead() {
    this.unread = 0;
    this.showUnread();
    const log = $("log");
    log.scrollTop = log.scrollHeight;
  }

  showUnread() {
    const text = this.unread ? String(this.unread) : "";
    for (const badge of document.querySelectorAll(".unread")) {
      badge.textContent = text;
      badge.classList.toggle("hidden", !text);
    }
  }

  // --- планшеты --------------------------------------------------------------------------------

  // вкладка на каждого игрока — аватарка и «Имя (Фракция)»; «Все игроки» — последней. На телефоне те же
  // вкладки стоят в нижней полосе, между «Решением» и «Журналом» (там — коротко: аватарка и имя)
  buildBoardTabs() {
    const seats = [...this.static.names.keys(), "all"];
    const open = (tab) => () => {
      this.openBoard(tab);
      this.setView("boards");
    };
    $("board-tabs").replaceChildren(...seats.map((tab) => h("button", { type: "button", "data-board": tab, onclick: open(tab) },
      tab === "all" ? "Все игроки" : C.nameOf(this.static, tab, true))));
    for (const b of document.querySelectorAll("#tabs button[data-board]")) b.remove();
    $("tabs").lastElementChild.before(...seats.map((tab) => h("button.board-tab", { type: "button", "data-view": "boards", "data-board": tab,
      title: tab === "all" ? "Все игроки" : C.nameOf(this.static, tab), onclick: open(tab) },
    tab === "all" ? h("span.tab-name", "Все") : [mark(`avatar${tab}`), h("span.tab-name", this.static.names[tab])])));
    this.markTabs();
  }

  // выделить открытую вкладку: в нижней полосе — вид экрана (а у планшетов — ещё и чей), над планшетом — чей
  markTabs() {
    const view = document.body.dataset.view, board = String(this.boardTab);
    for (const b of document.querySelectorAll("#tabs button")) {
      b.classList.toggle("active", b.dataset.view === view && (view !== "boards" || b.dataset.board === board));
    }
    for (const b of $("board-tabs").children) b.classList.toggle("active", b.dataset.board === board);
  }

  // открыта вкладка игрока — его планшет и его карта; «Все игроки» — карта остаётся прежней
  openBoard(p) {
    this.boardTab = p;
    if (p !== "all") this.boardPlayer = p;
    this.markTabs();
    $("board").scrollTop = 0;
    if (this.state && this.static) this.refresh();
  }

  paneVisible(id) {
    return getComputedStyle($(id)).visibility !== "hidden";
  }

  showBoard() {
    if (!this.state || !this.static) return;
    if (!this.paneVisible("boards-pane")) {
      this.boardStale = true;
      return;
    }
    this.boardStale = false;
    if (this.boardTab === "all") {
      const scroll = $("board").scrollTop;
      $("board").replaceChildren(playersSummary(this.static, this.state, this.viewer, (p) => this.openBoard(p)));
      $("board").scrollTop = scroll;
    } else {
      this.board.show(this.static, this.state, this.boardPlayer, this.viewer);
    }
  }

  // строительство: здания можно выбрать тапом по своему планшету (player null — выбор закончен)
  pickBuilding(player, options = null, onPick = null) {
    if (player === null) {
      this.board.selectMode(null);
      return;
    }
    if (this.boardTab !== player) this.openBoard(player);
    this.board.selectMode(player, options, null, onPick);
  }

  pickMonster(player, options = null, onPick = null) {
    if (player === null) {
      this.board.selectMonsters(null);
      return;
    }
    if (this.boardTab !== player) this.openBoard(player);
    this.board.selectMonsters(player, options, onPick);
  }

  // --- полоса раунда и очерёдность ---------------------------------------------------------------

  refresh() {
    const st = this.state, static_ = this.static;
    $("round").textContent = st.round ? `Раунд ${st.round}/${static_.rounds}` : "Подготовка";
    if (st.round) $("round").dataset.tip = "rounds";   // при наведении — кто первым ходит в остальных раундах
    else delete $("round").dataset.tip;
    const phase = this.ended ? null : st.phase;
    const chips = [];
    if (this.movePeek) {   // полоса перестраивается: наведённого коня больше нет
      this.movePeek = false;
      this.map.setPreview(null);
    }
    if (st.round) {
      if (phase === 0) chips.push(h("span.chip.current", "Выбор порядка действий"));
      else st.action_order.forEach((a, i) => {
        const n = i + 1;
        const cls = phase === null || phase === undefined || n > phase ? "" : n === phase ? ".current" : ".done";
        const chip = h("span.chip" + cls, { title: UI.actions[a] }, `${n}. `, icon(a));
        // перемещение ещё впереди: наведение на коня обводит на карте, куда дойдёт игрок, чей планшет открыт
        const reach = st.upcoming_reach || {};
        if (a === "move" && !this.ended && Object.keys(reach).length) {
          chip.addEventListener("pointerenter", () => {
            this.movePeek = true;
            this.map.setPreview(reach);
          });
          chip.addEventListener("pointerleave", () => {
            this.movePeek = false;
            this.map.setPreview(null);
          });
        }
        chips.push(chip);
      });
    } else if (!this.ended) {
      chips.push(h("span.chip.current", "Выбор стартовых регионов"));
    }
    $("phases").replaceChildren(...chips);
    this.showOrder();
    this.map.show(static_, st, this.boardPlayer, this.viewer);   // карта — того же игрока, чей планшет открыт
    this.showBoard();
    this.showThinking();
  }

  showOrder() {
    const st = this.state, static_ = this.static, bar = $("order");
    if (this.ended) {
      bar.replaceChildren();
      return;
    }
    const now = new Set((st.thinking || []).map(([p]) => p));
    const items = [h("span.label", st.round ? "Очерёдность:" : "Выбор стартовых регионов:")];
    C.turnOrder(static_, st).forEach((p, i) => {
      if (i) items.push(h("span.arrow", "→"));
      const role = st.round && i === 0 ? " · первый" : "";
      items.push(h("span.order-chip" + (now.has(p) ? ".now" : ""), { style: { "--seat": C.color(p) } },
        `${i + 1}. ${C.nameOf(static_, p, true)}${role}`));
    });
    const upcoming = C.nextFirstPlayers(static_, st, static_.names.length);
    if (upcoming.length) {
      items.push(h("span.label.upcoming", "Первым будет: " + upcoming.map(([r, p], j) =>
        (j === 0 ? "раунд " : "") + `${r} — ${C.nameOf(static_, p, true)}`).join(", ")));
    } else {
      items.push(h("span.label.upcoming", "Последний раунд."));
    }
    bar.replaceChildren(...items);
  }

  setStatus(text) {
    if (text === this.statusText) return;
    this.statusText = text;
    $("status").replaceChildren(rich(text));
  }

  // --- журнал ----------------------------------------------------------------------------------

  clearLog() {
    $("log").replaceChildren();
  }

  addLog(line, cls = null) {
    const static_ = this.static;
    let seat = -1;   // строка о действии игрока — его цветом
    if (cls === null) {
      if (line.startsWith("===")) cls = "round";
      else if (line.startsWith("⚠")) cls = "warn";
      else if (static_) seat = static_.names.findIndex((n) => line.startsWith(n + " "));
    }
    const el = h("div.line" + (cls ? "." + cls : ""));
    if (seat >= 0) el.style.color = C.color(seat);
    if (static_ && cls !== "end") {
      let start = 0;
      for (const [pos, p] of C.nameSpans(static_, line)) {
        appendRich(el, line.slice(start, pos));
        el.append(icon(`avatar${p}`));
        start = pos;
      }
      appendRich(el, line.slice(start));
    } else {
      el.textContent = line;
    }
    const log = $("log");
    const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.append(el);
    if (atEnd) log.scrollTop = log.scrollHeight;
    if (!this.logVisible()) {
      this.unread++;
      this.showUnread();
    }
  }

  // --- решения ---------------------------------------------------------------------------------

  clearDecision() {
    if (this.panel) this.panel.destroy();
    this.panel = null;
    this.map.selectMode(null);
    this.map.setPreview(null);
    $("decision").replaceChildren();
  }

  decision(m) {
    this.pending = m;
    if (this.boardPlayer !== m.player) {   // решают на своей карте и своём планшете (сводку «Все игроки» не закрываем)
      this.boardPlayer = m.player;
      if (this.boardTab !== "all") this.boardTab = m.player;
      this.markTabs();
      if (this.state) this.refresh();
    }
    this.clearDecision();
    try {
      this.panel = makePanel(this, m);
    } catch (e) {
      console.error(e);
      this.panel = makePanel(this, { ...m, kind: "raw:" + m.kind, error: `ошибка формы: ${e.message}` });
    }
    $("decision").append(this.panel.el);
    this.setStatus("Ваше решение");
    document.body.classList.add("my-turn");
    this.title();
    this.setView("decision");   // на телефоне решение — на своей вкладке (карта видна всегда)
    if (document.visibilityState !== "visible" || !this.wasPending) navigator.vibrate?.(120);
    this.wasPending = true;
  }

  sendAnswer(m, answer) {
    if (this.pending !== m) return;
    this.pending = null;
    this.wasPending = false;
    document.body.classList.remove("my-turn");
    this.link.answer(m.id, answer);
    this.waiting(isSecret(m) ? "Выбор сделан и скрыт от соперников." : null);
    this.setStatus("Ждём остальных игроков…");
    this.title();
  }

  // Панель ожидания: своё пояснение и под ним — кто сейчас что решает (обновляет showThinking).
  waiting(text = null, idle = "Ждём остальных игроков…") {
    this.clearDecision();
    this.waitBox = h("div.wait-who");
    this.waitNote = Boolean(text);
    this.waitText = null;
    $("decision").append(h("div.waiting", text ? h("p.muted", text) : null, this.waitBox));
    this.setWait(text ? "" : idle);
    this.showThinking();
  }

  setWait(text) {
    if (!this.waitBox || text === this.waitText) return;
    this.waitText = text;
    this.waitBox.replaceChildren(...text.split("\n").filter(Boolean).map((line) => h("p.wait-line", line)));
  }

  showThinking() {
    if (!this.waitBox || !this.waitBox.isConnected || this.pending || this.ended || !this.state || !this.static) return;
    const text = C.thinkingText(this.static, this.state, true);
    if (text) {
      this.setWait(text);
      this.setStatus("Ждём: " + this.state.thinking.map(([p]) => C.nameOf(this.static, p, true)).join(", "));
    } else if ("thinking" in this.state) {
      this.setWait(this.waitNote ? "" : "Ждём остальных игроков…");
      this.setStatus("Ждём остальных игроков…");
    }
  }

  // --- конец партии --------------------------------------------------------------------------

  finish(text, textRu) {
    this.ended = true;
    this.pending = null;
    document.body.classList.remove("my-turn");
    this.clearDecision();
    const finished = text.includes("FINAL SCORES") && this.state;
    const usage = text.split("\n").filter((line) => line.includes("Claude calls"));
    const box = h("div.finished", h("h2", "Партия окончена"));
    if (finished) {
      box.append(this.scoreTable(), ...usage.map((line) => h("p.muted", line)));
      this.addLog("ИТОГИ", "end");
    } else {
      const shown = (textRu || text).trim() || "Партия прервана.";
      box.append(h("pre.end-text", shown));
      this.addLog("ИТОГИ\n" + shown, "end");
    }
    // доигранная партия уже в профилях людей за столом (cli.record_profiles)
    box.append(h("div.join-more",
      finished ? h("button.primary", { type: "button", onclick: () => this.profilesScreen() }, "Профили и статистика") : null,
      h("button", { type: "button", onclick: () => location.reload() }, "К подключению")));
    $("decision").append(box);
    this.setStatus("Партия окончена");
    this.setView("decision");
  }

  scoreTable() {
    const st = this.state, static_ = this.static, win = C.winners(st);
    const head = h("tr", h("th"), st.players.map((pl, p) => h("th", { style: { color: win.includes(p) ? "var(--good)" : C.color(p) } },
      icon(`avatar${p}`, "big"), h("div", C.nameOf(static_, p) + (win.includes(p) ? " ★" : "")))));
    const rows = [...Object.entries(UI.scores), ["total", "ИТОГО"]].map(([key, title]) =>
      h("tr" + (key === "total" ? ".total" : ""), h("th", title), st.players.map((pl) => h("td", String(pl.score[key])))));
    return h("table.scores", h("thead", head), h("tbody", rows));
  }

  failed(text) {
    this.ended = true;
    this.clearDecision();
    $("decision").append(h("div.finished", h("h2.bad", "Партию нельзя продолжить"), h("pre.end-text", text.slice(-1500)),
      h("button", { type: "button", onclick: () => location.reload() }, "К подключению")));
    this.setStatus("Ошибка");
    this.setView("decision");
  }
}

loadUI().then(async () => {
  const params = new URLSearchParams(location.search);
  // тестовый режим: формы заполняет «человек наугад» (js/test.js); ставится раньше первого сообщения хозяина
  const tester = params.get("test") === "random" ? await import("./test.js") : null;
  const app = window.app = new App();
  tester?.installTester(app, Number(params.get("seed") || 0));
});
