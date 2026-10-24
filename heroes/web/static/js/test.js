// Тестовый режим страницы (?test=random&seed=N): «человек», который заполняет формы наугад и жмёт кнопки —
// как PanelPlayer в tests/test_gui_panels.py. Его запускает tests/test_web_browser.py в headless Chromium.
// Итоги — в window.testLog: виды решений (и шаги строительства), отклонённые хозяином ответы, зависшие формы.

function random(seed) {   // mulberry32: воспроизводимая последовательность по seed
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function installTester(app, seed) {
  const rng = random(seed);
  const pick = (xs) => xs[Math.floor(rng() * xs.length)];
  const int = (lo, hi) => lo + Math.floor(rng() * (hi - lo + 1));
  const log = window.testLog = { kinds: [], errors: [], stuck: [], answers: 0 };
  const kinds = new Set();
  let last = null;   // какая кнопка нажата последней — к отклонённому ответу

  const decision = app.decision.bind(app);
  app.decision = (m) => {
    if (m.error) log.errors.push([m.kind, m.error, last]);
    decision(m);
    setTimeout(() => play(m), 0);
  };
  const sendAnswer = app.sendAnswer.bind(app);
  app.sendAnswer = (m, answer) => {
    log.answers++;
    if (m.kind === "build_recruit") {
      for (const step of answer) if (step && typeof step === "object") kinds.add(`step:${Object.keys(step)[0]}`);
    }
    if (answer && typeof answer === "object" && !Array.isArray(answer) && answer.upgrade) kinds.add(`${m.kind}:upgrade`).add("upgrade");
    log.kinds = [...kinds];
    sendAnswer(m, answer);
  };
  // тапы по планшету: здание — наугад (или снять выбор), чудовище — в половине случаев
  const pickBuilding = app.pickBuilding?.bind(app);
  app.pickBuilding = (player, options, onPick) => {
    pickBuilding?.(player, options, onPick);
    if (player !== null && options) onPick(pick([...options, null]));
  };
  const pickMonster = app.pickMonster?.bind(app);
  app.pickMonster = (player, options, onPick) => {
    pickMonster?.(player, options, onPick);
    if (player !== null && options && rng() < 0.5) onPick(pick([...options]));
  };

  function fiddle(panel) {
    const el = panel.el;
    if (panel.options && panel.pick) panel.pick(pick(panel.options));
    if (panel.buttons && panel.toggle) {
      panel.clear();
      const actions = Object.keys(panel.buttons);
      while (panel.order.includes(null)) panel.toggle(pick(actions.filter((a) => !panel.order.includes(a))));
    }
    for (const group of el.querySelectorAll(".radios")) {
      const values = [...group.radios.inputs.keys()];
      if (values.length) group.radios.set(pick(values));
    }
    // после переключателей: они перестраивают поля заявки
    for (const counter of el.querySelectorAll(".counter")) {
      const c = counter.counter;
      c.set(rng() < 0.7 ? int(0, Math.max(0, c.maximum)) : 0);
    }
    const helpers = [...el.querySelectorAll("button.helper")];
    if (helpers.length && rng() < 0.3) pick(helpers).click();
  }

  function play(m) {
    if (app.pending !== m || !app.panel) return;
    const panel = app.panel;
    kinds.add(m.kind);
    log.kinds = [...kinds];
    for (let attempt = 0; attempt < 40; attempt++) {
      fiddle(panel);
      const footer = [...panel.footer.querySelectorAll("button")];
      const primary = footer.find((b) => b.classList.contains("primary"));
      const others = footer.filter((b) => b !== primary && !b.disabled);
      const steps = [...panel.body.querySelectorAll("button.step-button")].filter((b) => !b.disabled);
      let button = null;
      if (m.kind === "build_recruit" && steps.length && rng() < 0.7) button = pick(steps);
      else if (primary && !primary.disabled && rng() < 0.85) button = primary;
      else if (others.length) button = pick(others);
      else if (primary && !primary.disabled) button = primary;
      if (button) {
        last = button.textContent;
        button.click();
        if (app.pending !== m) return;   // ответ ушёл; иначе кнопка ничего не отправляет («Сбросить») — ещё раз
      }
    }
    log.stuck.push(m.kind);
  }
}
