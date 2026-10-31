// Экран «Профили и статистика»: профили на компьютере хозяина (heroes/profiles.py) — имя, аватарка
// и доигранные партии, по карточке на каждую. Запросы — POST /profiles (heroes/net/web.py: profile_request).
// Партия попадает в профиль игрока с тем же именем после подсчёта очков; профиля нет — хозяин заводит его сам.

import { h, mark } from "./rich.js";

const MAX_AVATAR = 300_000;   // heroes/profiles.py: MAX_AVATAR (base64)
const AVATAR_SIZE = 128;
const MAX_CARDS = 100;        // больше карточек партий не рисуем

// Своя картинка → квадрат AVATAR_SIZE по центру, PNG в base64 без префикса (null — не вышло или слишком большая)
export function squareAvatar(file) {
  return new Promise((resolve) => {
    if (!file) return resolve(null);
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = AVATAR_SIZE;
      canvas.getContext("2d").drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side,
        0, 0, AVATAR_SIZE, AVATAR_SIZE);
      URL.revokeObjectURL(url);
      const encoded = canvas.toDataURL("image/png").split(",")[1];
      resolve(encoded.length > MAX_AVATAR ? null : encoded);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(null);
    };
    img.src = url;
  });
}

const png = (encoded) => `data:image/png;base64,${encoded}`;

// «Победа» или «2-е место из 3»; выше — у кого больше ПО и победители при равных ПО (у них больше ресурсов)
function placeText(game) {
  const players = game.players, me = players[game.you];
  if (me.winner) return ["Победа", "good"];
  const place = 1 + players.filter((o) => o.vp > me.vp || (o.vp === me.vp && o.winner)).length;
  return [`${place}-е место из ${players.length}`, "bad"];
}

function dateText(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const two = (n) => String(n).padStart(2, "0");
  return `${two(d.getDate())}.${two(d.getMonth() + 1)}.${d.getFullYear()}, ${two(d.getHours())}:${two(d.getMinutes())}`;
}

function gamesWord(n) {
  if (n % 10 === 1 && n % 100 !== 11) return "партия";
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return "партии";
  return "партий";
}

export class ProfilesScreen {
  // root — элемент экрана; password() — пароль партии (с экрана подключения); inGame() — открыт из партии
  // (тогда назад — к партии, а «Играть под этим профилем» не нужно); onBack() — назад к подключению или
  // к партии; onUse(profile) — «Играть под этим профилем»
  constructor(root, { password, inGame, onBack, onUse }) {
    this.root = root;
    this.password = password;
    this.inGame = inGame;
    this.onBack = onBack;
    this.onUse = onUse;
    this.list = [];
    this.selected = null;   // id открытого профиля или null — список
    this.turn = false;      // в партии ждут нашего решения: кнопка «К партии» это показывает
    this.backButton = null;
  }

  // посреди партии пришёл (или ушёл) наш ход
  markTurn(turn) {
    this.turn = turn;
    if (!this.backButton) return;
    this.backButton.textContent = turn ? "← К партии: ваш ход" : "← К партии";
    this.backButton.classList.toggle("primary", turn);
  }

  async request(body) {
    let response;
    try {
      response = await fetch("profiles", {
        method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, password: this.password() || null }),
      });
    } catch {
      throw new Error("Нет связи с хозяином: страница профилей работает, пока у него запущена игра.");
    }
    const reply = await response.json().catch(() => ({}));
    if (response.status === 403) throw new Error("Нужен пароль партии: впишите его на экране подключения.");
    if (!response.ok) throw new Error(reply.error || "Хозяин не принял запрос.");
    return reply;
  }

  async open(selected = null) {
    this.selected = selected;
    this.render(h("p.muted", "Загрузка…"));
    await this.reload();
  }

  async reload() {
    try {
      this.list = (await this.request({ action: "list" })).profiles;
      this.error = null;
    } catch (e) {
      this.list = [];
      this.error = e.message;
    }
    this.show();
  }

  async act(body) {
    try {
      const reply = await this.request(body);
      this.error = null;
      return reply;
    } catch (e) {
      this.error = e.message;
      this.show();
      return null;
    }
  }

  render(...content) {
    const inGame = this.inGame();
    this.backButton = inGame ? h("button", { type: "button", onclick: () => this.onBack() }) : null;
    const back = this.backButton
      || h("button", { type: "button", onclick: () => this.onBack() }, "← К подключению");
    const list = this.selected
      ? h("button", { type: "button", onclick: () => { this.selected = null; this.error = null; this.show(); } }, "← Все профили")
      : null;
    if (this.backButton) this.markTurn(this.turn);
    this.root.replaceChildren(h("div.card.profiles",
      h("div.profiles-head", ...(inGame ? [back, list] : [list || back]), h("h1", "Профили")),
      this.error ? h("p.bad", this.error) : null,
      ...content));
  }

  show() {
    const profile = this.list.find((p) => p.id === this.selected);
    if (this.selected && profile) this.showProfile(profile);
    else {
      this.selected = null;
      this.showList();
    }
  }

  // --- список ----------------------------------------------------------------------------------

  showList() {
    const name = h("input", { maxlength: 24, placeholder: "имя нового профиля" });
    const create = async () => {
      const reply = await this.act({ action: "create", name: name.value });
      if (!reply) return;
      this.selected = reply.profile.id;
      await this.reload();
    };
    name.addEventListener("keydown", (e) => {
      if (e.key === "Enter") create();
    });
    const rows = this.list.map((p) => h("button.profile-row", { type: "button", onclick: () => { this.selected = p.id; this.show(); } },
      this.avatar(p, "row-avatar"),
      h("span.profile-name", p.name),
      h("span.muted", p.summary.games ? `${p.summary.games} ${gamesWord(p.summary.games)}, побед: ${p.summary.wins}` : "партий пока нет")));
    this.render(
      h("p.muted", "Профили хранятся на компьютере хозяина. Доигранная партия записывается в профиль игрока " +
        "с тем же именем, что за столом; если такого профиля нет, хозяин заводит его сам — с аватаркой, с которой вы играли."),
      rows.length ? h("div.profile-list", rows) : (this.error ? null : h("p", "Профилей пока нет.")),
      h("div.profile-new", name, h("button", { type: "button", onclick: create }, "Новый профиль")));
  }

  avatar(profile, cls) {
    return h(`img.${cls}`, { src: profile.avatar ? png(profile.avatar) : "assets/avatars/human_neutral.png", alt: "" });
  }

  // --- профиль -----------------------------------------------------------------------------------

  showProfile(profile) {
    const name = h("input", { value: profile.name, maxlength: 24 });
    const rename = async () => {
      if (name.value.trim() === profile.name) return;
      if (await this.act({ action: "rename", id: profile.id, name: name.value })) await this.reload();
    };
    name.addEventListener("keydown", (e) => {
      if (e.key === "Enter") rename();
    });
    const photo = h("input", { type: "file", accept: "image/*" });
    photo.addEventListener("change", async () => {
      const encoded = await squareAvatar(photo.files[0]);
      if (!encoded) {
        this.error = "Не удалось взять эту картинку (или она слишком большая).";
        this.show();
        return;
      }
      if (await this.act({ action: "avatar", id: profile.id, avatar: encoded })) await this.reload();
    });
    const remove = async () => {
      if (await this.act({ action: "avatar", id: profile.id, avatar: null })) await this.reload();
    };
    const drop = async () => {
      const games = profile.games.length ? ` и статистику (${profile.games.length} ${gamesWord(profile.games.length)})` : "";
      if (!confirm(`Удалить профиль «${profile.name}»${games}? Это нельзя отменить.`)) return;
      if (await this.act({ action: "delete", id: profile.id })) {
        this.selected = null;
        await this.reload();
      }
    };
    const s = profile.summary;
    const stats = s.games
      ? `Партий: ${s.games} · Побед: ${s.wins} · В среднем: ${s.avg_vp.toFixed(1)}${mark("laurel")} · Лучший результат: ${s.best_vp}${mark("laurel")}`
      : "Доигранных партий пока нет. Партия попадает сюда после подсчёта очков в конце 16-го раунда.";
    const games = [...profile.games].reverse();
    const known = Object.fromEntries(this.list.map((p) => [p.id, p]));
    this.render(
      h("div.profile-head",
        this.avatar(profile, "big-avatar"),
        h("div.profile-edit",
          h("label", "Имя", h("div.inline", name, h("button", { type: "button", onclick: rename }, "Сохранить"))),
          h("div.inline",
            h("label.file-button", "Выбрать картинку", photo),
            profile.avatar ? h("button.link", { type: "button", onclick: remove }, "убрать") : null))),
      h("div.inline",
        this.inGame() ? null : h("button.primary", { type: "button", onclick: () => this.onUse(profile) }, "Играть под этим профилем"),
        h("button", { type: "button", onclick: drop }, "Удалить профиль")),
      h("h2", "Статистика"),
      h("p.muted", stats),
      games.slice(0, MAX_CARDS).map((game) => this.gameCard(game, known)),
      games.length > MAX_CARDS ? h("p.muted", `Показаны последние ${MAX_CARDS} партий из ${games.length}.`) : null);
  }

  // Карточка партии: дата, итог; строки — вы и соперники: аватарка, имя, фракция, номер хода, ПО
  gameCard(game, known) {
    const players = game.players;
    const [place, cls] = placeText(game);
    const order = [game.you, ...players.map((_, p) => p).filter((p) => p !== game.you).sort((a, b) => players[a].turn - players[b].turn)];
    const rows = order.map((p) => {
      const pl = players[p], you = p === game.you;
      const own = known[pl.profile]?.avatar;
      const kind = pl.kind === "claude" || pl.kind === "bot" ? pl.kind : "human";
      return h("tr" + (you ? ".you" : ""),
        h("td", h("img.row-avatar", { src: own ? png(own) : `assets/avatars/${kind}_${p % 4}.png`, alt: "" })),
        h("td", pl.name + (you ? " (вы)" : "")),
        h("td", pl.faction_name),
        h("td", String(pl.turn)),
        h("td", String(pl.vp)),
        h("td.good", pl.winner ? "★ победа" : ""));
    });
    return h("div.game-card",
      h("div.game-card-head", h("span.muted", `${dateText(game.date)} · игроков: ${players.length}`), h(`b.${cls}`, place)),
      h("table.game-table",
        h("thead", h("tr", ["", "Игрок", "Фракция", "Номер хода", mark("laurel"), ""].map((t) => h("th", t)))),
        h("tbody", rows)));
  }
}
