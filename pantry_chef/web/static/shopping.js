// The shopping list page.
//
// The server adds the list up; this draws it and sends back what you change.
// Ticking is shown at once and saved behind it — in a shop, the tick has to
// land under your thumb, not a round trip later.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);

const MAX_SCALE = 20;
const MULTIPLIERS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8];

let list = null;

async function api(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

const send = (method, body) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function say(text, bad = false) {
  const el = $("msg");
  el.textContent = text;
  el.hidden = !text;
  el.classList.toggle("bad", bad);
}

// --- drawing ----------------------------------------------------------------------

function servingsBase(text) {
  const match = /\d+/.exec(text || "");
  return match && Number(match[0]) > 0 ? Number(match[0]) : null;
}

function fmtFactor(f) {
  return { 0.25: "¼", 0.5: "½", 0.75: "¾", 1.5: "1½" }[f] || String(Math.round(f * 100) / 100);
}

/** The servings shown, and the scales one step down and up (null at the ends).
 *  `labelHtml` is markup built only from numbers and escaped text. */
function steps(r) {
  const base = servingsBase(r.servings);
  if (base) {
    const target = Math.max(1, Math.round(base * r.scale));
    return {
      labelHtml: `<b>${target}</b> serving${target === 1 ? "" : "s"}`,
      less: target > 1 ? (target - 1) / base : null,
      more: target + 1 <= base * MAX_SCALE ? (target + 1) / base : null,
    };
  }
  const i = MULTIPLIERS.findIndex(m => m >= r.scale - 1e-9);
  const at = i === -1 ? MULTIPLIERS.length - 1 : i;
  return {
    labelHtml: `<b>×${esc(fmtFactor(r.scale))}</b>`,
    less: at > 0 ? MULTIPLIERS[at - 1] : null,
    more: at < MULTIPLIERS.length - 1 ? MULTIPLIERS[at + 1] : null,
  };
}

function recipeRow(r) {
  const s = steps(r);
  const href = `/recipe/${r.id}${r.scale !== 1 ? `?scale=${r.scale}` : ""}`;
  return `<li data-id="${r.id}">
    <div class="what"><a href="${href}">${esc(r.title)}</a><span class="book">${esc(r.book)}</span></div>
    <span class="stepper">
      <button type="button" data-scale="${s.less ?? ""}" ${s.less === null ? "disabled" : ""}
              aria-label="Fewer servings of ${esc(r.title)}">−</button>
      <output>${s.labelHtml}</output>
      <button type="button" data-scale="${s.more ?? ""}" ${s.more === null ? "disabled" : ""}
              aria-label="More servings of ${esc(r.title)}">+</button>
    </span>
    <button type="button" class="x" data-remove aria-label="Take ${esc(r.title)} off the list">✕</button>
  </li>`;
}

function itemRow(item, several) {
  const lines = item.lines || [];
  return `<li class="${item.ticked ? "done" : ""}">
    <label><input type="checkbox" data-key="${esc(item.key)}"${item.ticked ? " checked" : ""}>
      <span class="name">${esc(item.name)}${several
        ? `<span class="for">${esc(item.recipes.join(" · "))}</span>` : ""}</span>
      <span class="amount">${esc(item.amount)}</span></label>
    ${lines.length > 1 ? `<details><summary>From ${lines.length} lines</summary>
      <ul>${lines.map(l => `<li>${esc(l)}</li>`).join("")}</ul></details>` : ""}
  </li>`;
}

function extraRow(extra) {
  return `<li class="extra ${extra.done ? "done" : ""}">
    <label><input type="checkbox" data-extra="${extra.id}"${extra.done ? " checked" : ""}>
      <span class="name">${esc(extra.text)}</span></label>
    <button type="button" class="x" data-remove-extra="${extra.id}"
            aria-label="Remove ${esc(extra.text)}">✕</button>
  </li>`;
}

function paintSummary() {
  const n = list.recipes.length;
  const { items, ticked } = list.counts;
  $("summary").textContent = n || items
    ? `${n} recipe${n === 1 ? "" : "s"} · ${items} thing${items === 1 ? "" : "s"} to buy`
      + (ticked ? ` · ${ticked} in the basket` : "")
    : "Nothing on it yet.";
}

function render() {
  const several = list.recipes.length > 1;
  const n = list.recipes.length;
  const { items } = list.counts;
  paintSummary();
  window.PantryNav?.setShoppingCount(n);

  $("recipes").innerHTML = n
    ? `<ul class="on-list">${list.recipes.map(recipeRow).join("")}</ul>`
    : `<div class="empty">No recipes on your list yet. Open a recipe and choose
        <b>Add to shopping list</b>, or use the basket on a search result.</div>`;
  if (list.missing) {
    $("recipes").insertAdjacentHTML("beforeend", `<p class="note">${list.missing} recipe${
      list.missing === 1 ? " on your list is" : "s on your list are"} not in the library
      at the moment — the book may have moved. ${list.missing === 1 ? "It comes" : "They come"}
      back when the book does.</p>`);
  }

  const aisles = list.aisles.map(a => `<section class="aisle"><h3>${esc(a.name)}</h3>
    <ul class="items">${a.items.map(i => itemRow(i, several)).join("")}</ul></section>`);
  if (list.extras.length) {
    aisles.push(`<section class="aisle"><h3>Also</h3>
      <ul class="items">${list.extras.map(extraRow).join("")}</ul></section>`);
  }
  $("aisles").innerHTML = aisles.join("") || (n
    ? `<div class="empty">Everything these recipes need is store-cupboard — see below.</div>`
    : "");

  $("cupboardPanel").hidden = !list.cupboard.length;
  $("cupboard").innerHTML = `<summary>Probably in your cupboard
      <span class="note">${list.cupboard.length} thing${list.cupboard.length === 1 ? "" : "s"}
      — check you have enough</span></summary>
    <ul class="items">${list.cupboard.map(i => itemRow(i, several)).join("")}</ul>`;

  $("clear").disabled = !n && !list.extras.length;
  $("copy").disabled = !items && !list.cupboard.length;
}

async function load() {
  try {
    list = await api("/api/shopping");
    render();
  } catch (err) {
    $("summary").textContent = "";
    say(`Could not load the list: ${err.message}`, true);
  }
}

// --- as text, for a message or a note ------------------------------------------------
// Plain text for the clipboard: never markup, so nothing here is escaped.

const textLine = (item) => ["-", item.name, item.amount ? "— " + item.amount : ""]
  .filter(Boolean).join(" ");

function asText() {
  const out = [];
  for (const aisle of list.aisles) {
    const left = aisle.items.filter(i => !i.ticked);
    if (!left.length) continue;
    out.push(aisle.name.toUpperCase(), ...left.map(textLine), "");
  }
  const extras = list.extras.filter(e => !e.done);
  if (extras.length) {
    out.push("ALSO", ...extras.map(e => "- " + e.text), "");
  }
  if (list.cupboard.length) {
    out.push("CHECK THE CUPBOARD", ...list.cupboard.map(textLine));
  }
  return out.join("\n").trim();
}

async function copyText() {
  const text = asText();
  try {
    // Only offered on secure pages; over plain http on a network it is absent.
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) { say("This browser would not copy. Use Print instead.", true); return; }
  }
  say("Copied — paste it into a message or a note.");
}

// --- changes -----------------------------------------------------------------------

document.addEventListener("change", async (e) => {
  const box = e.target;
  if (box.dataset.key !== undefined) {
    box.closest("li").classList.toggle("done", box.checked);
    try {
      await api(`/api/shopping/ticks/${encodeURIComponent(box.dataset.key)}`,
                send("PUT", { ticked: box.checked }));
      const item = [...list.aisles.flatMap(a => a.items), ...list.cupboard]
        .find(i => i.key === box.dataset.key);
      if (item) item.ticked = box.checked;
      // Cupboard items are not counted among the things to buy.
      if (!list.cupboard.includes(item)) list.counts.ticked += box.checked ? 1 : -1;
      paintSummary();          // not a redraw: the focus stays on the box
    } catch (err) {
      box.checked = !box.checked;
      box.closest("li").classList.toggle("done", box.checked);
      say(`Not saved: ${err.message}`, true);
    }
  } else if (box.dataset.extra !== undefined) {
    box.closest("li").classList.toggle("done", box.checked);
    try {
      await api(`/api/shopping/extras/${box.dataset.extra}`, send("PATCH", { done: box.checked }));
      await load();
    } catch (err) {
      box.checked = !box.checked;
      say(`Not saved: ${err.message}`, true);
    }
  }
});

document.addEventListener("click", async (e) => {
  const row = e.target.closest(".on-list li");
  try {
    if (e.target.closest("[data-scale]") && row) {
      const scale = Number(e.target.closest("[data-scale]").dataset.scale);
      await api(`/api/shopping/recipes/${row.dataset.id}`, send("PUT", { scale }));
      await load();
    } else if (e.target.closest("[data-remove]") && row) {
      await api(`/api/shopping/recipes/${row.dataset.id}`, { method: "DELETE" });
      await load();
    } else if (e.target.closest("[data-remove-extra]")) {
      await api(`/api/shopping/extras/${e.target.closest("[data-remove-extra]").dataset.removeExtra}`,
                { method: "DELETE" });
      await load();
    }
  } catch (err) {
    say(`Not saved: ${err.message}`, true);
  }
});

$("extraForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("extraText");
  if (!input.value.trim()) return;
  try {
    await api("/api/shopping/extras", send("POST", { text: input.value }));
    input.value = "";
    say("");
    await load();
    input.focus();
  } catch (err) {
    say(`Not added: ${err.message}`, true);
  }
});

$("copy").onclick = copyText;
$("print").onclick = () => window.print();
$("clear").onclick = async () => {
  if (!window.confirm("Clear the whole list — recipes, ticks and extras — and start again?")) return;
  try {
    await api("/api/shopping", { method: "DELETE" });
    say("");
    await load();
  } catch (err) {
    say(`Not cleared: ${err.message}`, true);
  }
};

load();
