// Cook mode: the recipe one step at a time, large enough to read from the
// stove, with a timer behind every "simmer for 20 minutes".
//
// The recipe page calls PantryCook.init(recipe) whenever it loads or rescales
// the recipe, and PantryCook.open(recipe, servingsNote) from its button. The
// timers live outside the overlay, so one started in cook mode keeps counting
// — and rings — after cook mode is closed.
//
// Everything the book wrote reaches the page through esc().
(() => {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  // Progress is kept for a while so a reload, or a phone that locked itself,
  // does not lose your place. After half a day it is a different meal.
  const STORE_PREFIX = "pantry-chef:cook:";
  const KEEP_FOR_MS = 12 * 3600 * 1000;
  const RING_EVERY_MS = 3000;
  const RING_AT_MOST = 20;          // a minute of beeping, then it stays silent but red

  const CLOCK = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round"><circle cx="12" cy="13" r="8"/>
    <path d="M12 9v4l2.5 1.5M9 2h6"/></svg>`;

  let recipe = null;
  let servingsNote = "";
  let step = 0;                     // 0 is "before you start"; 1..n are the method
  let ticked = new Set();
  let timers = [];
  let resumed = false;
  let ticker = null;
  let ringing = null;
  let rings = 0;
  let wakeLock = null;
  let audio = null;
  let returnFocus = null;
  let pointer = null;
  let pageTitle = document.title;

  const isOpen = () => !$("cook").hidden;
  const stepCount = () => (recipe?.steps || []).length;
  const live = () => timers.filter(t => !t.dismissed);
  const wide = () => window.matchMedia("(min-width: 1000px)").matches;

  // --- storage ------------------------------------------------------------------

  const storeKey = () => STORE_PREFIX + recipe.id;

  function save() {
    try {
      localStorage.setItem(storeKey(), JSON.stringify({
        step, ticked: [...ticked], timers: live(), savedAt: Date.now(),
      }));
    } catch (_) { /* storage blocked: cook mode works, it just forgets on reload */ }
  }

  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(storeKey()) || "null");
      if (!saved || Date.now() - saved.savedAt > KEEP_FOR_MS) return;
      step = Math.min(Math.max(0, saved.step | 0), stepCount());
      ticked = new Set((saved.ticked || []).filter(Number.isInteger));
      timers = (saved.timers || []).filter(t => t && t.total > 0 && typeof t.id === "string");
      resumed = step > 0;
    } catch (_) { /* unreadable: start fresh */ }
  }

  // --- opening and closing --------------------------------------------------------

  /** Called on every load or rescale: take the new amounts, keep the progress. */
  function init(r) {
    const first = !recipe || recipe.id !== r.id;
    recipe = r;
    if (first) {
      pageTitle = document.title;
      restore();
      renderTray();
      if (live().length) startTicker();
    }
    if (isOpen()) { renderIngredients(); render(); }
  }

  function open(r, note = "") {
    init(r);
    servingsNote = note;
    returnFocus = document.activeElement;
    pageTitle = document.title.replace(/^⏰ /, "");
    $("cookTitle").textContent = r.title;
    document.documentElement.classList.add("cook-open");
    document.querySelector(".wrap").inert = true;
    $("cook").hidden = false;
    if (location.hash !== "#cook") history.pushState({ cook: true }, "", location.href.split("#")[0] + "#cook");
    setIngredientsPanel(wide());
    renderIngredients();
    render();
    renderTray();
    keepAwake();
    $("cookNext").focus();
  }

  function close({ fromHistory = false } = {}) {
    if (!isOpen()) return;
    $("cook").hidden = true;
    document.documentElement.classList.remove("cook-open");
    document.querySelector(".wrap").inert = false;
    releaseWake();
    save();
    renderTray();
    if (!fromHistory && location.hash === "#cook") {
      // Opening pushed a history entry so the phone's back gesture leaves cook
      // mode rather than the page; closing by button should unwind it.
      if (history.state && history.state.cook) history.back();
      else history.replaceState(history.state, "", location.href.split("#")[0]);
    }
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
  }

  window.addEventListener("popstate", () => {
    if (isOpen() && location.hash !== "#cook") close({ fromHistory: true });
  });

  // --- the steps ----------------------------------------------------------------------

  function checkItem(item, i) {
    return `<li><label><input type="checkbox" data-tick="${i}"${ticked.has(i) ? " checked" : ""}>
      <span>${esc(item.line || item.display)}</span></label></li>`;
  }

  function introHtml() {
    const items = recipe.ingredients || [];
    const count = `${items.length} ingredient${items.length === 1 ? "" : "s"}`;
    return `<h2 class="cook-h" id="cookHeading">Before you start</h2>
      <p class="cook-sub">${esc(servingsNote ? servingsNote + " · " : "")}${count}.
        Tick them off as you get them out.</p>
      <ul class="cook-checklist">${items.map(checkItem).join("")}</ul>`;
  }

  function stepHtml(k) {
    const text = recipe.steps[k - 1] || "";
    const found = (recipe.step_timers || [])[k - 1] || [];
    let html = "";
    let at = 0;
    found.forEach((t, j) => {
      html += esc(text.slice(at, t.start));
      html += `<button type="button" class="cook-timer-start" data-step="${k}" data-timer="${j}"
        aria-label="Start a ${esc(t.label)} timer">${CLOCK}${esc(text.slice(t.start, t.end))}</button>`;
      at = t.end;
    });
    html += esc(text.slice(at));
    const banner = resumed
      ? `<p class="cook-resumed">Picked up where you left off.
           <button type="button" class="linkish" data-cook="restart">Start over</button></p>`
      : "";
    return `${banner}<div class="cook-num" id="cookHeading">Step ${k}</div>
      <p class="cook-text${text.length > 420 ? " long" : ""}">${html}</p>
      ${found.length ? `<p class="cook-hint">Tap a time to start a timer.</p>` : ""}`;
  }

  function render() {
    const n = stepCount();
    $("cookCount").textContent = step === 0 ? "Before you start" : `Step ${step} of ${n}`;
    $("cookBar").style.width = `${n ? (step / n) * 100 : 0}%`;
    $("cookPrev").disabled = step === 0;
    $("cookNext").textContent = step === 0 ? "Start →" : step === n ? "Finish" : "Next →";
    const stage = $("cookStage");
    stage.innerHTML = step === 0 ? introHtml() : stepHtml(step);
    stage.scrollTop = 0;
    applyPanel();
    save();
  }

  function renderIngredients() {
    const items = recipe.ingredients || [];
    $("cookIngNote").textContent = servingsNote;
    $("cookIngList").innerHTML = items.map(checkItem).join("");
  }

  function go(direction) {
    const next = step + direction;
    if (next < 0) return;
    if (next > stepCount()) { finish(); return; }
    step = next;
    resumed = false;
    render();
  }

  function finish() {
    // The meal is done; the next visit starts at the top. Running timers stay.
    step = 0;
    ticked = new Set();
    resumed = false;
    save();
    close();
  }

  // The first screen is the ingredient list, so the side panel repeating it
  // there would be noise; it appears from step 1, if wanted.
  let panelWanted = false;

  function setIngredientsPanel(show) {
    panelWanted = show;
    applyPanel();
  }

  function applyPanel() {
    const visible = panelWanted && step > 0;
    $("cookIng").hidden = !visible;
    $("cookIngBtn").hidden = step === 0;
    $("cookIngBtn").setAttribute("aria-expanded", String(visible));
    $("cook").classList.toggle("with-ing", visible);
  }

  // --- timers -----------------------------------------------------------------------

  function clock(ms) {
    const total = Math.max(0, Math.ceil(ms / 1000));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = String(total % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
  }

  function startTimer(k, j) {
    const t = ((recipe.step_timers || [])[k - 1] || [])[j];
    if (!t) return;
    unlockAudio();         // this click is the gesture a browser needs before it will beep
    const id = `${k}:${j}`;
    const running = timers.find(x => x.id === id && !x.dismissed && !x.done);
    if (running) { highlight(id); return; }
    timers = timers.filter(x => x.id !== id);
    timers.push({
      id, step: k, label: t.label, total: t.seconds * 1000,
      endsAt: Date.now() + t.seconds * 1000, remaining: t.seconds * 1000,
      paused: false, done: false, dismissed: false,
    });
    renderTray();
    startTicker();
    save();
    highlight(id);
  }

  function highlight(id) {
    const el = document.querySelector(`#cookTray [data-id="${CSS.escape(id)}"]`);
    if (!el) return;
    el.classList.remove("flash");
    void el.offsetWidth;            // restart the animation
    el.classList.add("flash");
  }

  // The tray is rebuilt only when timers come or go; the countdown itself is a
  // text update, so a keyboard user's focus on Pause is not lost every second.
  function renderTray() {
    const tray = $("cookTray");
    const shown = live();
    tray.hidden = shown.length === 0;
    tray.classList.toggle("floating", !isOpen());
    tray.innerHTML = shown.map(t => `
      <div class="tray-timer${t.done ? " done" : ""}${t.paused ? " paused" : ""}" data-id="${esc(t.id)}">
        <button type="button" class="tray-go" data-act="goto"
                title="Go to step ${t.step}">Step ${t.step}<small>${esc(t.label)}</small></button>
        <span class="tray-left" role="timer">${t.done ? "Done" : clock(t.paused ? t.remaining : t.endsAt - Date.now())}</span>
        ${t.done
          ? `<button type="button" class="tray-btn ok" data-act="dismiss">OK</button>`
          : `<button type="button" class="tray-btn" data-act="pause"
                aria-label="${t.paused ? "Resume" : "Pause"} the step ${t.step} timer">${t.paused ? "▶" : "❚❚"}</button>
             <button type="button" class="tray-btn" data-act="cancel"
                aria-label="Cancel the step ${t.step} timer">✕</button>`}
      </div>`).join("");
  }

  function tick() {
    const now = Date.now();
    let finished = false;
    for (const t of timers) {
      if (!t.done && !t.paused && !t.dismissed && now >= t.endsAt) {
        t.done = true;
        finished = true;
      }
    }
    if (finished) { renderTray(); ring(); save(); }
    document.querySelectorAll("#cookTray .tray-timer").forEach(el => {
      const t = timers.find(x => x.id === el.dataset.id);
      if (t && !t.done) el.querySelector(".tray-left").textContent =
        clock(t.paused ? t.remaining : t.endsAt - now);
    });
    if (!live().length) stopTicker();
  }

  function startTicker() {
    if (!ticker) ticker = setInterval(tick, 500);
    tick();
  }

  function stopTicker() {
    clearInterval(ticker);
    ticker = null;
  }

  function trayAction(id, act) {
    const t = timers.find(x => x.id === id);
    if (!t) return;
    if (act === "goto") {
      if (!isOpen()) open(recipe, servingsNote);
      step = Math.min(t.step, stepCount());
      resumed = false;
      render();
      return;
    }
    if (act === "pause") {
      if (t.paused) { t.endsAt = Date.now() + t.remaining; t.paused = false; }
      else { t.remaining = Math.max(0, t.endsAt - Date.now()); t.paused = true; }
    } else if (act === "cancel" || act === "dismiss") {
      t.dismissed = true;
    }
    if (!timers.some(x => x.done && !x.dismissed)) stopRinging();
    renderTray();
    save();
    if (live().length) startTicker(); else stopTicker();
  }

  // --- the alarm ------------------------------------------------------------------

  function unlockAudio() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume();
    } catch (_) { audio = null; }
  }

  function beep() {
    // A timer that finishes after a reload has had no click to unlock sound;
    // the browser may refuse, and the red tray and title still say it.
    if (!audio) unlockAudio();
    if (!audio) return;
    const now = audio.currentTime;
    for (const offset of [0, 0.22, 0.44]) {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.35, now + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.18);
      osc.connect(gain).connect(audio.destination);
      osc.start(now + offset);
      osc.stop(now + offset + 0.2);
    }
  }

  function ring() {
    if (ringing) return;
    rings = 0;
    const once = () => {
      if (!timers.some(t => t.done && !t.dismissed) || rings++ >= RING_AT_MOST) {
        clearInterval(ringing);
        ringing = null;
        return;
      }
      beep();
      if (navigator.vibrate) navigator.vibrate([300, 150, 300]);
    };
    document.title = "⏰ " + pageTitle;
    once();
    ringing = setInterval(once, RING_EVERY_MS);
  }

  function stopRinging() {
    clearInterval(ringing);
    ringing = null;
    document.title = pageTitle;
  }

  // --- keeping the screen on -----------------------------------------------------

  function setAwake(on, text) {
    const el = $("cookAwake");
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle("off", !on);
  }

  async function keepAwake() {
    if (!("wakeLock" in navigator)) {
      // Browsers only offer this to secure pages: localhost, or HTTPS. Opened
      // over plain http from a phone on the network, the screen will dim.
      setAwake(false, window.isSecureContext
        ? "Screen may dim — this browser can’t keep it on"
        : "Screen may dim — keeping it on needs HTTPS");
      return;
    }
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      setAwake(true, "Screen stays on");
      wakeLock.addEventListener("release", () => {
        wakeLock = null;
        if (isOpen() && document.visibilityState === "visible") setAwake(false, "Screen may dim");
      });
    } catch (_) {
      setAwake(false, "Screen may dim");
    }
  }

  function releaseWake() {
    if (wakeLock) wakeLock.release().catch(() => {});
    wakeLock = null;
  }

  // The lock is dropped whenever the tab is hidden; take it back on return.
  document.addEventListener("visibilitychange", () => {
    if (isOpen() && document.visibilityState === "visible" && !wakeLock) keepAwake();
    if (document.visibilityState === "visible" && live().length) tick();
  });

  // --- input ------------------------------------------------------------------------

  function wire() {
    $("cookPrev").addEventListener("click", () => go(-1));
    $("cookNext").addEventListener("click", () => go(1));
    $("cookClose").addEventListener("click", () => close());
    $("cookIngBtn").addEventListener("click", () => setIngredientsPanel(!panelWanted));
    $("cookIngClose").addEventListener("click", () => setIngredientsPanel(false));

    $("cook").addEventListener("click", (e) => {
      const start = e.target.closest(".cook-timer-start");
      if (start) startTimer(Number(start.dataset.step), Number(start.dataset.timer));
      if (e.target.closest('[data-cook="restart"]')) {
        step = 0; ticked = new Set(); resumed = false;
        renderIngredients(); render();
      }
    });

    // The checklist appears twice — on the first screen and in the side panel —
    // and the two must agree.
    $("cook").addEventListener("change", (e) => {
      const box = e.target.closest("input[data-tick]");
      if (!box) return;
      const i = Number(box.dataset.tick);
      if (box.checked) ticked.add(i); else ticked.delete(i);
      document.querySelectorAll(`#cook input[data-tick="${i}"]`).forEach(b => { b.checked = box.checked; });
      save();
    });

    $("cookTray").addEventListener("click", (e) => {
      const button = e.target.closest("[data-act]");
      const row = e.target.closest("[data-id]");
      if (button && row) trayAction(row.dataset.id, button.dataset.act);
    });

    document.addEventListener("keydown", (e) => {
      if (!isOpen() || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "Escape") {
        e.preventDefault();
        if (!$("cookIng").hidden && !wide()) setIngredientsPanel(false); else close();
      } else if (e.key === "ArrowRight" || e.key === "PageDown") {
        e.preventDefault(); go(1);
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault(); go(-1);
      }
    });

    // Swipe between steps, for floury fingers. Horizontal only, so a long
    // step still scrolls; the stage's touch-action leaves horizontal to us.
    const stage = $("cookStage");
    stage.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "mouse") pointer = { x: e.clientX, y: e.clientY };
    });
    stage.addEventListener("pointerup", (e) => {
      if (!pointer) return;
      const dx = e.clientX - pointer.x;
      const dy = e.clientY - pointer.y;
      pointer = null;
      if (Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? 1 : -1);
    });
    stage.addEventListener("pointercancel", () => { pointer = null; });
  }

  wire();
  window.PantryCook = { init, open, close, isOpen };
})();
