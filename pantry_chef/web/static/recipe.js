const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const recipeId = Number(location.pathname.split("/").pop());
const params = new URLSearchParams(location.search);
let units = params.get("units") || "metric";
const have = params.get("have") || "";

// The server accepts up to 20x; beyond that a recipe is a different recipe.
const MAX_SCALE = 20;
// For a book that gives no servings there is no count to step through, so the
// stepper walks these multipliers instead.
const MULTIPLIERS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8];

let scale = clampScale(Number(params.get("scale")) || 1);
let current = null;          // the recipe as last loaded
let request = 0;             // so a slow response cannot overwrite a newer one

function clampScale(value) {
  return Math.min(MAX_SCALE, Math.max(0.05, value));
}

const ICONS = {
  servings: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
    <path d="M6 2v9M4 2v4a2 2 0 0 0 4 0V2M6 11v11"/>
    <path d="M17 2c-1.5 2-2 4-2 6s.5 3 2 3 2-1 2-3-.5-4-2-6zM17 11v11"/></svg>`,
  time: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
    <circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
  difficulty: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.3" stroke-linejoin="round">
    <path d="M6 20h12v-3H6zM6 17c-2.5-1-4-3-4-5.5A5.5 5.5 0 0 1 8.5 6a4.8 4.8 0 0 1 7 0
       A5.5 5.5 0 0 1 22 11.5c0 2.5-1.5 4.5-4 5.5"/></svg>`,
  count: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
    <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>`,
  cook: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"
    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M3 11h18M5 11v6a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-6M9 11V8a3 3 0 0 1 6 0v3"/></svg>`,
};

// Most specific first: a dessert filed under "lunch" should read as dessert.
const MEAL_PRIORITY = ["dessert", "breakfast", "snack", "side", "dinner", "lunch"];

function chooseCategory(r) {
  // The meal type leads, because it is derived and consistent. A book's own
  // chapter heading sounds better when it is real, but in practice many are
  // placeholders ("Chapter 3") or, worse, the previous recipe's title picked
  // up as a heading — so it is only a fallback.
  for (const meal of MEAL_PRIORITY) {
    if ((r.meals || []).includes(meal)) return meal;
  }
  const section = (r.section || "").trim();
  const useless = /^(chapter|part|section)\s*[\divxlc]*$/i.test(section);
  if (section && !useless && section.length <= 40) return section;
  return "Recipe";
}

function fmtTime(m, estimate) {
  if (m == null) return "—";
  const text = m < 60 ? m + " min"
    : (m % 60 ? Math.floor(m / 60) + " h " + (m % 60) + " min" : m / 60 + " h");
  return estimate ? "~" + text : text;
}

function fmtFactor(f) {
  const nice = { 0.25: "¼", 0.5: "½", 0.75: "¾", 1.5: "1½" };
  return nice[f] || String(Math.round(f * 100) / 100);
}

async function fetchRecipe() {
  const query = new URLSearchParams({ units });
  if (have) query.set("have", have);
  if (scale !== 1) query.set("scale", String(scale));
  const res = await fetch(`/api/recipe/${recipeId}?` + query);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

// --- servings ---------------------------------------------------------------

function targetServings(r) {
  return r.servings_base ? Math.max(1, Math.round(r.servings_base * r.scale)) : null;
}

/** What the page calls the current quantity: "6 servings", "×2", "4 to 6". */
function servingsLabel(r) {
  const target = targetServings(r);
  if (target) return `${target} serving${target === 1 ? "" : "s"}`;
  return r.scale === 1 ? "" : `×${fmtFactor(r.scale)} the book’s amounts`;
}

function nextScale(r, direction) {
  const base = r.servings_base;
  if (base) {
    const most = Math.floor(base * MAX_SCALE);
    const target = Math.min(most, Math.max(1, targetServings(r) + direction));
    return target / base;
  }
  let i = MULTIPLIERS.findIndex(m => m >= r.scale - 1e-9);
  if (i === -1) i = MULTIPLIERS.length - 1;
  if (direction < 0 && MULTIPLIERS[i] > r.scale + 1e-9) i -= 1;   // between two steps
  return MULTIPLIERS[Math.min(MULTIPLIERS.length - 1, Math.max(0, i + direction))];
}

function servingsCell(r) {
  const target = targetServings(r);
  const value = target ? String(target) : `×${fmtFactor(r.scale)}`;
  const book = (r.servings || "").trim();
  let from = "";
  if (r.scale !== 1) from = book ? `book: ${book}` : "book gives no servings";
  else if (!target) from = "book gives no servings";
  else if (book && book !== String(target)) from = book;       // "4 to 6", "8 scones"

  const less = nextScale(r, -1), more = nextScale(r, +1);
  return `${ICONS.servings}<div class="k">Servings</div>
    <div class="v stepper${r.scale !== 1 ? " scaled" : ""}">
      <button type="button" class="step" data-scale="${less}" ${less === r.scale ? "disabled" : ""}
              aria-label="Fewer servings">−</button>
      <output aria-live="polite" aria-label="Servings">${esc(value)}</output>
      <button type="button" class="step" data-scale="${more}" ${more === r.scale ? "disabled" : ""}
              aria-label="More servings">+</button>
    </div>
    ${from ? `<div class="from">${esc(from)}</div>` : ""}`;
}

function ingredientItems(r) {
  return (r.ingredients || []).map(i => {
    const classes = [i.have ? "have" : "", i.staple ? "staple" : ""].filter(Boolean).join(" ");
    const note = i.unscaled
      ? `<span class="why unscaled">amount not adjusted — scale by eye</span>`
      : i.staple ? `<span class="why">store cupboard</span>` : "";
    return `<li class="${classes}">${esc(i.line || i.display)}${note}</li>`;
  }).join("");
}

function scaleNote(r) {
  if (r.scale === 1) return "";
  const book = (r.servings || "").trim();
  return `Scaled ×${esc(fmtFactor(r.scale))}${book ? ` from the book’s ${esc(book)}` : ""}.
    <button type="button" class="linkish" data-scale="1">Reset</button>`;
}

function methodNote(r) {
  if (r.scale === 1) return "";
  return "The method is as the book wrote it: cooking times, pan sizes and any "
    + "amounts it mentions are for the original quantity.";
}

/** Redraw only what scaling changes, so the photo and scroll position stay put. */
function paintScaled(r) {
  document.getElementById("servingsFact").innerHTML = servingsCell(r);
  document.getElementById("ingredientList").innerHTML = ingredientItems(r);
  const note = document.getElementById("scaleNote");
  note.innerHTML = scaleNote(r);
  note.hidden = r.scale === 1;
  const method = document.getElementById("methodNote");
  method.textContent = methodNote(r);
  method.hidden = r.scale === 1;
  window.PantryCook?.init(r);
}

async function setScale(next) {
  scale = clampScale(next);
  const query = new URLSearchParams(location.search);
  if (scale === 1) query.delete("scale"); else query.set("scale", String(scale));
  const search = query.toString();
  history.replaceState(history.state, "",
    location.pathname + (search ? "?" + search : "") + location.hash);

  const mine = ++request;
  try {
    const r = await fetchRecipe();
    if (mine !== request) return;
    current = r;
    paintScaled(r);
  } catch (_) {
    if (mine === request) document.getElementById("scaleNote").textContent =
      "Could not rescale — is the server still running?";
  }
}

// --- the page -------------------------------------------------------------------

async function load() {
  let r;
  try {
    r = await fetchRecipe();
  } catch (err) {
    document.getElementById("page").innerHTML =
      `<div class="empty">${esc(err.message)}</div>`;
    return;
  }
  current = r;

  document.title = r.title + " · Pantry Chef";
  const category = chooseCategory(r);

  const tags = [
    ...(r.cuisine ? [r.cuisine] : []),
    ...(r.diets || []).filter(d => d === "vegetarian" || d === "vegan"),
  ].map(t => `<span class="tag">${esc(t)}</span>`);
  if (r.allergens && r.allergens.length)
    tags.push(`<span class="tag warn">contains ${r.allergens.map(esc).join(", ")}</span>`);

  const hasSteps = (r.steps || []).length > 0;
  const steps = hasSteps
    ? `<ol>${r.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>`
    : `<p class="note">No method was captured for this recipe — see the book, page ${r.page || "?"}.</p>`;
  const timerCount = (r.step_timers || []).reduce((n, list) => n + list.length, 0);

  const caveat = [
    r.diet_caveats || "",
    r.n_unknown ? `${r.n_unknown} ingredient${r.n_unknown > 1 ? "s" : ""} not recognised, so the allergen list may be incomplete` : "",
  ].filter(Boolean).join(" · ");

  document.getElementById("page").innerHTML = `
    <div class="card-page">
      <div class="eyebrow">${esc(category)}</div>
      <h2 class="recipe-title">${esc(r.title)}</h2>
      <div class="byline">
        from <b>${esc(r.book)}</b>${r.page ? `, page ${r.page}` : ""}
      </div>

      <div class="tags">${tags.join("")}</div>

      ${r.has_image ? `<img class="hero" id="heroImage" src="${esc(r.image_url)}"
           alt="${esc(r.title)}" loading="lazy">` : ""}

      <div class="facts">
        <div id="servingsFact"></div>
        <div>${ICONS.time}<div class="k">Time</div>
          <div class="v">${esc(fmtTime(r.total_minutes, r.time_is_estimate))}</div></div>
        <div>${ICONS.difficulty}<div class="k">Difficulty</div>
          <div class="v">${esc(r.difficulty || "—")}</div></div>
        <div>${ICONS.count}<div class="k">Ingredients</div>
          <div class="v">${(r.ingredients || []).length}</div></div>
      </div>

      ${hasSteps ? `<div class="cook-cta">
        <button type="button" class="go" id="cookStart">${ICONS.cook}Start cooking</button>
        <span class="note">One step at a time, large enough to read from the stove${
          timerCount ? `, with ${timerCount} timer${timerCount === 1 ? "" : "s"} ready to start` : ""}.</span>
      </div>` : ""}

      <div class="columns">
        <div class="ingredients">
          <h2>Ingredients</h2>
          <p class="scale-note" id="scaleNote" hidden></p>
          <ul id="ingredientList"></ul>
        </div>
        <div class="directions">
          <h2>Directions</h2>
          <p class="method-note" id="methodNote" hidden></p>
          ${steps}
        </div>
      </div>

      ${caveat ? `<div class="source" style="color:var(--warn)">${esc(caveat)}</div>` : ""}

      <div class="tools">
        <span class="note">Amounts shown in ${units === "metric" ? "metric" : "the book's original units"}.</span>
        <button class="ghost fav-toggle" id="favToggle" type="button"
                aria-pressed="${r.is_favourite ? "true" : "false"}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.3s-7.3-4.5-9.3-9.1C1.3 7.9 3.3 4.4 6.8 4.4c2 0 3.5 1.1 4.2 2.6h2c.7-1.5 2.2-2.6 4.2-2.6 3.5 0 5.5 3.5 4.1 6.8-2 4.6-9.3 9.1-9.3 9.1z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
          <span>${r.is_favourite ? "Saved" : "Save to favourites"}</span>
        </button>
        <button class="ghost" id="units">Show ${units === "metric" ? "original units" : "metric"}</button>
        <button class="ghost" id="print">Print</button>
      </div>

      <div class="source">
        <b>${esc(r.book)}</b>${r.page ? ` · page ${r.page}` : ""}
        ${r.time_is_estimate ? " · time is estimated, not stated in the book" : ""}
      </div>
    </div>`;

  paintScaled(r);

  // A photo that cannot be read should leave no gap; bound here rather than as
  // an inline onerror, which the Content-Security-Policy forbids.
  const hero = document.getElementById("heroImage");
  if (hero) hero.addEventListener("error", () => hero.remove());

  const favButton = document.getElementById("favToggle");
  favButton.onclick = async () => {
    const saved = favButton.getAttribute("aria-pressed") === "true";
    favButton.disabled = true;
    try {
      const res = await fetch(`/api/favourites/${recipeId}`,
                              { method: saved ? "DELETE" : "PUT" });
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      favButton.setAttribute("aria-pressed", String(data.is_favourite));
      favButton.querySelector("span").textContent =
        data.is_favourite ? "Saved" : "Save to favourites";
    } catch (_) {
      favButton.querySelector("span").textContent = "Could not save — try again";
    } finally {
      favButton.disabled = false;
    }
  };

  document.getElementById("units").onclick = () => {
    units = units === "metric" ? "original" : "metric";
    const next = new URLSearchParams(location.search);
    next.set("units", units);
    history.replaceState(history.state, "", location.pathname + "?" + next + location.hash);
    load();
  };
  document.getElementById("print").onclick = () => window.print();

  const start = document.getElementById("cookStart");
  if (start) start.onclick = () => window.PantryCook.open(current, servingsLabel(current));
  if (location.hash === "#cook" && hasSteps && !window.PantryCook.isOpen()) {
    window.PantryCook.open(current, servingsLabel(current));
  }
}

// One listener for the stepper and the reset link, which are redrawn on every
// change and so cannot keep handlers of their own.
document.getElementById("page").addEventListener("click", (e) => {
  const button = e.target.closest("[data-scale]");
  if (button && !button.disabled) setScale(Number(button.dataset.scale));
});

load();
