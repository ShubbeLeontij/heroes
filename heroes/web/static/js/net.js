// Связь с хозяином партии: WebSocket /ws на том же адресе, откуда пришла страница.
// Сообщения — те же, что получает графический клиент (heroes/net/protocol.py), с русскими текстами
// от сервера (heroes/net/web.py). Если связь рвётся, страница сама подключается снова: сервер пришлёт
// всю партию (sync) и повторит вопрос, оставшийся без ответа.

export const VERSION = 4;   // heroes/net/protocol.py: VERSION
const RETRY_MS = [500, 1000, 2000, 4000, 5000];
const PONG_MS = 4000;       // проснувшись, ждём ответа на ping столько; нет ответа — переподключаемся

export class Link {
  // handlers: message(m) — каждое сообщение сервера; state(text|null) — что со связью (null — всё хорошо)
  constructor(handlers) {
    this.handlers = handlers;
    this.ws = null;
    this.ended = false;      // партия закончилась или сервер отказал: больше не подключаться
    this.attempt = 0;
    this.pongTimer = null;
    this.retryTimer = null;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") this.check();
    });
    window.addEventListener("online", () => this.check());
  }

  // name — место за столом (null — сервер предложит свободные), password, avatar — PNG в base64 или null
  connect(name, password, avatar) {
    Object.assign(this, { name, password, avatar });
    this.ended = false;
    this.open();
  }

  open() {
    clearTimeout(this.retryTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    this.ws = ws;
    // Safari 26 на iOS: новый WebSocket висит «подключающимся» и даже не отправляется хозяину, пока к нему
    // не пойдёт обычный запрос (https://bugs.webkit.org/show_bug.cgi?id=302823). Запрос сразу после
    // создания будит его.
    fetch("ping", { cache: "no-store" }).catch(() => {});
    ws.onopen = () => {
      this.hello(this.name);
      this.handlers.state(null);
    };
    ws.onmessage = (e) => {
      let m;
      try { m = JSON.parse(e.data); } catch { return; }
      if (m.t === "pong") {
        clearTimeout(this.pongTimer);
        return;
      }
      if (m.t === "welcome") {
        this.name = m.name;
        this.welcomed = true;
        this.attempt = 0;   // попытки считаем до места за столом: пока оно занято, повторяем всё реже
      }
      if (m.t === "error" && this.welcomed && m.text.startsWith("All network seats are taken")) {
        // телефон проснулся раньше, чем хозяин заметил обрыв старого соединения: место скоро освободится
        this.handlers.state("Хозяин ещё не заметил обрыв прежнего подключения — ждём и подключаемся снова…");
        return;   // сервер закроет соединение, onclose подключится снова
      }
      if (m.t === "end" || m.t === "error") this.ended = true;
      this.handlers.message(m);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.ended) return;
      const delay = RETRY_MS[Math.min(this.attempt++, RETRY_MS.length - 1)];
      this.handlers.state("Нет связи с хозяином — подключаемся снова…");
      this.retryTimer = setTimeout(() => this.open(), delay);
    };
  }

  hello(name) {
    const hello = { t: "hello", version: VERSION, name, password: this.password, mode: "gui" };
    if (this.avatar) hello.avatar = this.avatar;
    this.send(hello);
  }

  chooseSeat(name) {
    this.name = name;
    this.hello(name);
  }

  answer(id, answer) {
    if (!this.send({ t: "answer", id, answer })) this.handlers.state("Ответ не отправлен — нет связи. Подключаемся снова…");
  }

  send(message) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(message));
    return true;
  }

  // Телефон проснулся или вернулась сеть: соединение могло тихо умереть — проверяем его ping-ом.
  check() {
    if (this.ended || !this.name && !this.ws) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.open();
      return;
    }
    clearTimeout(this.pongTimer);
    this.send({ t: "ping" });
    this.pongTimer = setTimeout(() => this.open(), PONG_MS);
  }

  close() {
    this.ended = true;
    clearTimeout(this.retryTimer);
    if (this.ws) this.ws.close();
    this.ws = null;
  }
}
