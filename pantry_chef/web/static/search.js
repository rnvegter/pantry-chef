const $ = (id) => document.getElementById(id);
// "ingredient" = what can I cook with what I have; "name" = find that recipe.
let mode = "ingredient";
let pantry = [];
// Counts for the Favourites tab badge and the "could not be found" note.
let favSummary = { total: 0, available: 0, missing: 0 };
let meals = new Set();
let diets = new Set();
let avoid = new Set();
let cursor = -1;

// --- pantry chips ---------------------------------------------------------

function renderChips() {
  [...$("chips").querySelectorAll(".chip")].forEach(c => c.remove());
  pantry.forEach((name, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name;
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.title = "remove";
    x.onclick = () => { pantry.splice(i, 1); renderChips(); run(); };
    chip.appendChild(x);
    $("chips").insertBefore(chip, $("ing"));
  });
}

function addItem(name) {
  name = (name || "").trim().replace(/,$/, "");
  if (name && !pantry.includes(name)) pantry.push(name);
  $("ing").value = "";
  hideSuggest();
  renderChips();
}

// People type and paste comma-separated lists, so commit a chip on every
// separator rather than only on Enter.
function commitSeparated() {
  const raw = $("ing").value;
  if (!/[,;\n]/.test(raw)) return false;
  const parts = raw.split(/[,;\n]/);
  const tail = parts.pop();
  parts.forEach(p => { const t = p.trim(); if (t && !pantry.includes(t)) pantry.push(t); });
  $("ing").value = tail.trim();
  renderChips();
  return true;
}

// --- autocomplete ---------------------------------------------------------

function hideSuggest() { $("suggest").hidden = true; cursor = -1; }

async function suggest() {
  const q = $("ing").value.trim();
  if (q.length < 2) return hideSuggest();
  const items = await fetch("/api/suggest?q=" + encodeURIComponent(q))
    .then(r => r.json()).catch(() => []);
  if (!items.length) return hideSuggest();

  const box = $("suggest");
  box.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.innerHTML = `<span>${esc(item.name)}</span><small>${item.recipes.toLocaleString()}</small>`;
    row.onmousedown = (e) => { e.preventDefault(); addItem(item.name); run(); };
    box.appendChild(row);
  });
  box.hidden = false;
}

$("ing").addEventListener("input", () => {
  if (commitSeparated()) { hideSuggest(); run(); return; }
  suggest();
});
$("ing").addEventListener("paste", () => setTimeout(() => {
  if (commitSeparated()) { hideSuggest(); run(); }
}, 0));
$("ing").addEventListener("blur", () => setTimeout(hideSuggest, 120));
$("ing").addEventListener("keydown", (e) => {
  const rows = [...$("suggest").querySelectorAll("div")];
  if (e.key === "Enter" || e.key === "Tab") {
    if (e.key === "Tab" && !$("ing").value.trim()) return;
    e.preventDefault();
    if (cursor >= 0 && rows[cursor]) rows[cursor].onmousedown(e);
    else { addItem($("ing").value); run(); }
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!rows.length) return;
    e.preventDefault();
    cursor = (cursor + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
    rows.forEach((r, i) => r.setAttribute("aria-selected", i === cursor));
  } else if (e.key === "Escape") {
    hideSuggest();
  } else if (e.key === "Backspace" && !$("ing").value && pantry.length) {
    pantry.pop(); renderChips(); run();
  }
});

// --- controls -------------------------------------------------------------

function minutes() { return +$("time").value || null; }

$("time").addEventListener("input", () => {
  const v = minutes();
  $("timeLabel").textContent = !v ? "any"
    : v < 60 ? v + " min"
    : (v % 60 ? Math.floor(v / 60) + "h " + (v % 60) + "m" : v / 60 + "h");
});
$("missing").addEventListener("input", () => {
  const v = +$("missing").value;
  $("missLabel").textContent = v === 0 ? "nothing" : v + (v === 1 ? " thing" : " things");
});
["time", "missing", "sort", "noWait", "cuisine", "strict"].forEach(id =>
  $(id).addEventListener("change", run));
// --- autocomplete for the by-name fields ---------------------------------

// Every suggestion is a value that exists in the library, so choosing one can
// never come back empty. That is the point: nobody guesses the spelling of
// "Bulsiewicz", and the old native datalist could not offer it, because it only
// matched from the start of the string.
function attachComplete(inputId, field) {
  const input = $(inputId);
  const box = $(inputId + "Suggest");
  let at = -1;
  let timer = null;

  const close = () => {
    box.hidden = true; box.innerHTML = ""; at = -1;
    input.setAttribute("aria-expanded", "false");
  };
  const rows = () => [...box.querySelectorAll("div")];

  const choose = (value) => { input.value = value; close(); run(); };

  // `browse` opens the whole list rather than filtering by what is typed.
  async function fetchSuggestions(browse = false) {
    const q = browse ? "" : input.value.trim();
    if (!browse && q.length < 2) return close();
    const items = await fetch(
      `/api/complete?field=${field}&limit=${browse ? 200 : 8}&q=` + encodeURIComponent(q)
    ).then(r => r.json()).catch(() => []);
    // The field may have moved on while the request was in flight.
    if (!browse && input.value.trim() !== q) return;
    if (!items.length || (items.length === 1 && items[0].value === q)) return close();

    box.innerHTML = "";
    items.forEach(item => {
      const row = document.createElement("div");
      row.innerHTML =
        `<span>${esc(item.value)}</span><small>${item.recipes.toLocaleString()}</small>`;
      row.onmousedown = (e) => { e.preventDefault(); choose(item.value); };
      box.appendChild(row);
    });
    at = -1;
    box.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(fetchSuggestions, 160);
    // Emptying a field should widen the search again rather than leave stale
    // results sitting under a box you have just cleared.
    if (!input.value.trim()) { close(); run(); }
  });
  // mousedown rather than click, with the default prevented, so the input does
  // not blur out from under the menu it is opening.
  $(inputId + "Open").addEventListener("mousedown", (e) => {
    e.preventDefault();
    if (!box.hidden) return close();
    input.focus();
    fetchSuggestions(true);
  });

  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    const list = rows();
    if (e.key === "Enter") {
      e.preventDefault();
      if (at >= 0 && list[at]) list[at].onmousedown(e);
      else { close(); run(); }
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!list.length) return;
      e.preventDefault();
      at = (at + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length;
      list.forEach((r, i) => r.setAttribute("aria-selected", i === at));
    } else if (e.key === "Escape") {
      close();
    }
  });
}

attachComplete("title", "title");
attachComplete("author", "author");
attachComplete("book", "book");

// "Anywhere in the recipe" is free text with nothing to complete against.
$("text").addEventListener("keydown", e => { if (e.key === "Enter") run(); });
$("go").onclick = run;
$("clear").onclick = () => {
  pantry = []; meals.clear(); diets.clear(); avoid.clear();
  $("cuisine").value = ""; $("strict").checked = false;
  $("title").value = ""; $("author").value = ""; $("book").value = "";
  document.querySelectorAll(".pills button").forEach(b => b.setAttribute("aria-pressed", "false"));
  updateAvoidNote();
  $("text").value = ""; $("time").value = 0; $("missing").value = 3;
  $("time").dispatchEvent(new Event("input"));
  $("missing").dispatchEvent(new Event("input"));
  renderChips(); $("results").innerHTML = ""; $("msg").textContent = "";
  setMode("ingredient");
};

// --- search ---------------------------------------------------------------

function fmtTime(m, estimate) {
  if (m == null) return "time unknown";
  const text = m < 60 ? m + " min"
    : (m % 60 ? Math.floor(m / 60) + "h " + (m % 60) + "m" : m / 60 + "h");
  return estimate ? "~" + text : text;
}

async function run() {
  // Values on the inactive tab are kept, so switching back restores them, but
  // they are not sent: a forgotten pantry must never quietly narrow a search
  // by name, nor an old author narrow a search by ingredient.
  const byName = mode === "name";
  // The Favourites tab lists what you saved, narrowed only by the shared
  // filters — like the name tab, it says nothing about what is in the fridge.
  const favourites = mode === "favourites";
  const noPantry = byName || favourites;
  const body = {
    have: noPantry ? [] : pantry,
    favourites_only: favourites,
    max_minutes: minutes(),
    max_missing: noPantry ? 99 : +$("missing").value,
    text: byName ? $("text").value : "",
    title: byName ? $("title").value : "",
    author: byName ? $("author").value : "",
    book: byName ? $("book").value : "",
    meals: [...meals],
    cuisines: $("cuisine").value ? [$("cuisine").value] : [],
    diets: [...diets],
    free_from: [...avoid],
    strict_diet: $("strict").checked,
    sort: $("sort").value,
    allow_long_wait: !$("noWait").checked,
    limit: 40,
  };
  $("msg").textContent = "searching…";

  let data;
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    data = await res.json();
  } catch (err) {
    $("msg").textContent = "";
    $("results").innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }

  const notes = [];
  if (data.unknown.length)
    notes.push(`not in your library: ${data.unknown.map(esc).join(", ")}`);
  if (data.relaxed_to != null)
    notes.push(`nothing matched exactly, so this shows recipes needing up to ${data.relaxed_to} more items`);
  if (data.duplicates_collapsed)
    notes.push(`${data.duplicates_collapsed} duplicate${data.duplicates_collapsed > 1 ? "s" : ""} collapsed`);
  $("msg").textContent = notes.join(" · ");
  applyFacets(data.facets);

  if (!data.results.length && mode === "favourites") {
    $("results").innerHTML = favSummary.available
      ? `<div class="empty">None of your favourites match these filters.<br>
           Clear a filter to see them all.</div>`
      : `<div class="empty">No favourites yet.<br>
           Tap the heart on any recipe to keep it here.</div>`;
    return;
  }
  if (!data.results.length) {
    $("results").innerHTML = `<div class="empty">No recipes matched.<br>${
      mode === "name"
        ? "Try fewer words, a different spelling, or check the Library page for which books are indexed."
        : "Try a longer time, more ingredients, or a bigger shopping list."
    }</div>`;
    return;
  }
  $("results").innerHTML = data.results.map(card).join("");
}

function card(r) {
  // Searching by name, or listing favourites, says nothing about what is in
  // your kitchen — so the coverage furniture ("needs 6", the bar, "0 of 6 on
  // hand") is noise on those tabs. The ingredient list is still worth showing,
  // just not as a verdict.
  const byName = mode === "name" || mode === "favourites";
  const pct = Math.round(r.coverage * 100);
  const badge = byName ? ""
    : r.missing.length === 0
      ? `<span class="badge ready">ready to cook</span>`
      : `<span class="badge short">needs ${r.missing.length}</span>`;
  const need = byName
    ? (r.ingredients || []).length
      ? `<div class="need">Ingredients: <b>${r.ingredients.map(m => esc(m.display)).join(", ")}</b></div>`
      : ""
    : r.missing.length
      ? `<div class="need">Shopping list: <b>${r.missing.map(m => esc(m.display)).join(", ")}</b></div>`
      : `<div class="need">You have everything.</div>`;
  const bits = [
    fmtTime(r.total_minutes, r.time_is_estimate),
    (r.meals || []).join(" / "),
    r.cuisine || "",
    (r.diets || []).filter(d => d !== "no red meat" && d !== "pescatarian").join(", "),
    r.servings ? "serves " + r.servings : "",
    r.book + (r.page ? ", p." + r.page : ""),
    r.has_long_wait ? "needs a long rest" : "",
  ].filter(Boolean);

  const allergens = (r.allergens || []).length
    ? `<div class="allergens">Contains: ${r.allergens.map(esc).join(", ")}</div>` : "";
  const caveat = [
    r.diet_caveats || "",
    r.n_unknown ? `${r.n_unknown} ingredient${r.n_unknown > 1 ? "s" : ""} not recognised — allergen check is incomplete` : "",
  ].filter(Boolean).join(" · ");

  const heart = `<button type="button" class="fav" data-id="${r.id}"
      aria-pressed="${r.is_favourite ? "true" : "false"}"
      aria-label="${r.is_favourite ? "Remove from favourites" : "Save to favourites"}: ${esc(r.title)}"
      ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.3s-7.3-4.5-9.3-9.1C1.3 7.9 3.3 4.4 6.8 4.4c2 0 3.5 1.1 4.2 2.6h2c.7-1.5 2.2-2.6 4.2-2.6 3.5 0 5.5 3.5 4.1 6.8-2 4.6-9.3 9.1-9.3 9.1z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg></button>`;

  return `<div class="card" data-card="${r.id}">
    ${heart}
    <h3>${esc(r.title)} ${badge}</h3>
    <div class="meta">${bits.map(b => `<span>${esc(b)}</span>`).join("")}</div>
    ${byName ? "" : `<div class="bar"><i style="width:${pct}%"></i></div>`}
    ${need}
    ${allergens}
    ${caveat ? `<div class="caveat">${esc(caveat)}</div>` : ""}
    ${byName ? "" : `<div class="have-list">${r.n_matched} of ${r.n_core} main ingredients on hand</div>`}
    <a class="open" target="_blank" rel="noopener"
       href="/recipe/${r.id}?units=metric${pantry.length ? "&have=" + encodeURIComponent(pantry.join(",")) : ""}">
      Open the recipe →</a>
  </div>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// --- boot -----------------------------------------------------------------

fetch("/api/stats").then(r => r.json()).then(s => {
  $("stats").textContent =
    `${s.recipes.toLocaleString()} recipes from ${s.books.toLocaleString()} books · ` +
    `${s.ingredients.toLocaleString()} ingredients`;
  $("meals").innerHTML = (s.meals || []).map(m =>
    `<button type="button" aria-pressed="false" data-m="${esc(m.name)}">${esc(m.name)}
       <small>${m.recipes.toLocaleString()}</small></button>`).join("")
    || `<span class="note">re-index to add meal tags</span>`;
  $("meals").querySelectorAll("button").forEach(b => b.onclick = () => {
    const on = b.getAttribute("aria-pressed") === "true";
    b.setAttribute("aria-pressed", String(!on));
    if (on) meals.delete(b.dataset.m); else meals.add(b.dataset.m);
    run();
  });

  const pillRow = (host, items, set) => {
    $(host).innerHTML = (items || []).map(i =>
      `<button type="button" aria-pressed="false" data-v="${esc(i.name)}">${esc(i.name)}
         <small>${i.recipes.toLocaleString()}</small></button>`).join("");
    $(host).querySelectorAll("button").forEach(b => b.onclick = () => {
      const on = b.getAttribute("aria-pressed") === "true";
      b.setAttribute("aria-pressed", String(!on));
      if (on) set.delete(b.dataset.v); else set.add(b.dataset.v);
      updateAvoidNote();
      run();
    });
  };
  pillRow("diets", s.diets, diets);
  pillRow("allergens", s.allergens, avoid);

  $("cuisine").innerHTML = `<option value="">Any style</option>` +
    (s.cuisines || []).map(c =>
      `<option value="${esc(c.name)}" data-name="${esc(c.name)}">${esc(c.name)} (${c.recipes.toLocaleString()})</option>`
    ).join("");

  $("quick").innerHTML = (s.popular || []).slice(0, 14).map(p =>
    `<button type="button" data-n="${esc(p.name)}">+ ${esc(p.name)}</button>`).join("");
  $("quick").querySelectorAll("button").forEach(b =>
    b.onclick = () => { addItem(b.dataset.n); run(); });
}).catch(() => { $("stats").textContent = "no database yet — run: python -m pantry_chef index <folder>"; });

// The number on a filter button is only useful if it describes what is in
// front of you: with an Italian filter on, "Lunch" should say how many Italian
// lunches there are. Every search returns facet counts for exactly that, each
// one counted with its own filter removed so a button can still tell you what
// selecting it would give.
function applyFacets(facets) {
  if (!facets) return;
  const paint = (host, kind, attr) => {
    $(host).querySelectorAll("button").forEach(b => {
      const n = (facets[kind] || {})[b.dataset[attr]] ?? 0;
      const small = b.querySelector("small");
      if (small) small.textContent = n.toLocaleString();
      // Nothing behind it: still selectable, so you can see it is empty rather
      // than wonder where it went, but visibly spent.
      b.classList.toggle("empty", n === 0 && b.getAttribute("aria-pressed") !== "true");
    });
  };
  paint("meals", "meal", "m");
  paint("diets", "diet", "v");
  paint("allergens", "allergen", "v");

  [...$("cuisine").options].forEach(o => {
    if (!o.value) return;
    const n = (facets.cuisine || {})[o.dataset.name] ?? 0;
    o.textContent = `${o.dataset.name} (${n.toLocaleString()})`;
    o.classList.toggle("empty", n === 0);
  });
}

// --- favourites ------------------------------------------------------------

function paintFavSummary() {
  const n = favSummary.available;
  $("favCount").hidden = !n;
  $("favCount").textContent = n.toLocaleString();
  // A favourite that no longer resolves is kept, not deleted, so say so.
  const lost = favSummary.missing;
  $("favMissing").hidden = !lost;
  $("favMissing").textContent = lost
    ? `${lost} saved recipe${lost > 1 ? "s" : ""} could not be found — the book may `
      + "have moved, or been re-read under a different title. It comes back if the book does."
    : "";
}

async function refreshFavSummary() {
  try {
    const res = await fetch("/api/favourites");
    if (res.ok) favSummary = await res.json();
  } catch (_) { /* keep the last known counts */ }
  paintFavSummary();
}

// One listener for every heart on the page, including cards drawn later.
$("results").addEventListener("click", async (e) => {
  const button = e.target.closest(".fav");
  if (!button) return;
  const saved = button.getAttribute("aria-pressed") === "true";
  button.disabled = true;
  try {
    const res = await fetch(`/api/favourites/${button.dataset.id}`,
                            { method: saved ? "DELETE" : "PUT" });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    button.setAttribute("aria-pressed", String(data.is_favourite));
    // Swap only the verb; the recipe's name after the colon stays as it was.
    button.setAttribute("aria-label", button.getAttribute("aria-label").replace(
      /^[^:]*/, data.is_favourite ? "Remove from favourites" : "Save to favourites"));
    favSummary = { total: data.total, available: data.available, missing: data.missing };
    paintFavSummary();
  } catch (_) {
    $("msg").textContent = "Could not update favourites — is the server still running?";
  } finally {
    button.disabled = false;
  }
});

function setMode(next) {
  mode = next;
  document.querySelectorAll(".tabs button").forEach(b =>
    b.setAttribute("aria-selected", String(b.dataset.mode === next)));
  $("panelIngredient").hidden = next !== "ingredient";
  $("panelName").hidden = next !== "name";
  $("panelFavourites").hidden = next !== "favourites";

  // Searching by name with nothing typed would list the whole library, which
  // is not what an empty box means. Wait for input instead.
  const hasNameQuery = ["title", "author", "book", "text"].some(id => $(id).value.trim());
  if (next === "name" && !hasNameQuery) {
    $("results").innerHTML = "";
    $("msg").textContent = "";
    $("title").focus();
    return;
  }
  if (next === "ingredient" && !pantry.length) {
    $("ing").focus();
  }
  run();
}

document.querySelectorAll(".tabs button").forEach(b =>
  b.onclick = () => setMode(b.dataset.mode));

function updateAvoidNote() {
  // Strict mode only means anything once a diet or allergen filter is on.
  const active = diets.size > 0 || avoid.size > 0;
  $("strictWrap").hidden = !active;
  $("avoidNote").textContent = avoid.size
    ? `— hiding recipes containing ${[...avoid].join(", ")}` : "";
}

renderChips();
updateAvoidNote();
refreshFavSummary();
