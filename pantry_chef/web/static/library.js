const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let polling = null;
let pickerAt = "";

async function api(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// --- adding a folder ------------------------------------------------------

let inspectTimer = null;
$("path").addEventListener("input", () => {
  clearTimeout(inspectTimer);
  inspectTimer = setTimeout(inspect, 400);
});

async function inspect() {
  const path = $("path").value.trim();
  if (!path) return ($("preview").textContent = "");
  const info = await api("/api/library/inspect", json({ path })).catch(e => ({ ok: false, error: e.message }));
  if (!info.ok) {
    $("preview").innerHTML = `<span style="color:var(--bad)">${esc(info.error)}</span>`;
    return;
  }
  const formats = Object.entries(info.by_format || {})
    .map(([k, v]) => `${v} ${k}`).join(", ");
  $("preview").innerHTML = info.books
    ? `Found <b>${info.books.toLocaleString()}</b> book${info.books === 1 ? "" : "s"}${formats ? " — " + esc(formats) : ""}`
    : `No supported ebooks found in that folder.`;
}

$("add").onclick = async () => {
  const path = $("path").value.trim();
  if (!path) return;
  try {
    await api("/api/library/sources", json({ path }));
    $("path").value = ""; $("preview").textContent = "";
    await load();
  } catch (err) {
    $("preview").innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`;
  }
};

// --- folder picker --------------------------------------------------------

$("browse").onclick = () => openPicker($("path").value.trim());
$("pickerCancel").onclick = () => $("picker").close();
$("pickerUse").onclick = () => {
  $("path").value = pickerAt;
  $("picker").close();
  inspect();
};

async function openPicker(at) {
  const data = await api("/api/library/browse?path=" + encodeURIComponent(at || ""));
  pickerAt = data.path || "";
  $("pickerPath").textContent = pickerAt;
  const rows = [];
  if (data.parent) rows.push(`<button data-p="${esc(data.parent)}">↑ ..</button>`);
  for (const d of data.directories || []) rows.push(`<button data-p="${esc(d.path)}">${esc(d.name)}</button>`);
  $("pickerList").innerHTML = rows.join("") || `<div class="empty">No sub-folders here.</div>`;
  $("pickerList").querySelectorAll("button").forEach(b =>
    b.onclick = () => openPicker(b.dataset.p));
  if (!$("picker").open) $("picker").showModal();
}

// --- indexing -------------------------------------------------------------

$("run").onclick = () => startRun(false);
$("runForce").onclick = () => startRun(true);
$("cancel").onclick = async () => {
  await api("/api/library/job/cancel", json({}));
  poll();
};

async function startRun(force, paths) {
  try {
    render(await api("/api/library/index", json({ force, paths: paths || [] })));
    watch();
  } catch (err) {
    $("current").innerHTML = `<span style="color:var(--bad)">${esc(err.message)}</span>`;
  }
}

function watch() {
  clearInterval(polling);
  polling = setInterval(poll, 700);
}

async function poll() {
  const job = await api("/api/library/job").catch(() => null);
  if (!job) return;
  render(job);
  if (job.status !== "running") {
    clearInterval(polling);
    polling = null;
    load();
  }
}

function secs(v) {
  if (!v) return "";
  return v < 60 ? `${Math.round(v)}s` : `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
}

function render(job) {
  const running = job.status === "running";
  $("run").disabled = running;
  $("runForce").disabled = running;
  $("cancel").disabled = !running;
  $("bar").style.width = (job.percent || 0) + "%";
  document.querySelector(".track").classList.toggle("idle", !running);

  const automatic = job.origin === "automatic";
  $("current").innerHTML = running && job.current
    ? `${automatic ? "New books found — " : ""}Reading <b>${esc(job.current)}</b>`
    : esc((automatic && job.message ? "Automatically: " : "") + (job.message || ""));

  const parts = [];
  if (job.total) parts.push(`<span><b>${job.done}</b> of ${job.total} books</span>`);
  if (job.recipes) parts.push(`<span><b>${job.recipes.toLocaleString()}</b> recipes</span>`);
  if (job.skipped) parts.push(`<span><b>${job.skipped}</b> unchanged</span>`);
  if (job.failed) parts.push(`<span style="color:var(--bad)"><b>${job.failed}</b> failed</span>`);
  if (job.elapsed) parts.push(`<span>${secs(job.elapsed)} elapsed</span>`);
  if (running && job.eta) parts.push(`<span>~${secs(job.eta)} left</span>`);
  $("counts").innerHTML = parts.join("");

  if (job.log && job.log.length) {
    const pre = $("log");
    pre.textContent = job.log.join("\n");
    pre.scrollTop = pre.scrollHeight;
  }
}

// --- the library ----------------------------------------------------------

async function load() {
  const data = await api("/api/library").catch(() => null);
  if (!data) return;
  const s = data.stats;

  $("stats").textContent =
    `${s.recipes.toLocaleString()} recipes from ${s.books.toLocaleString()} books`;
  $("dbPath").textContent = data.database;

  $("sources").innerHTML = data.sources.length ? `<table>
    <thead><tr><th>Folder</th><th>Books</th><th>Last indexed</th><th></th></tr></thead>
    <tbody>${data.sources.map(src => `
      <tr class="${src.exists ? "" : "gone"}">
        <td class="path">${esc(src.path)}
          ${src.exists ? "" : `<div class="note" style="color:var(--bad)">${esc(src.error || "missing")}</div>`}</td>
        <td>${src.exists ? src.books_on_disk.toLocaleString() : "—"}
          <div class="note">${esc(Object.entries(src.by_format || {}).map(([k, v]) => `${v} ${k}`).join(", "))}</div></td>
        <td class="note">${src.last_indexed ? esc(src.last_indexed) : "never"}</td>
        <td class="right">
          <button class="ghost" data-index="${esc(src.path)}">Index</button>
          <button class="ghost" data-remove="${src.id}">Remove</button>
        </td>
      </tr>`).join("")}</tbody></table>`
    : `<div class="empty">No folders yet. Add one above.</div>`;

  $("sources").querySelectorAll("[data-index]").forEach(b =>
    b.onclick = () => startRun(false, [b.dataset.index]));
  $("sources").querySelectorAll("[data-remove]").forEach(b =>
    b.onclick = async () => { await api("/api/library/sources/" + b.dataset.remove, { method: "DELETE" }); load(); });

  const issues = [
    ...data.failures.map(f => ({ ...f, kind: "failed" })),
    ...data.empty.map(f => ({ ...f, kind: "empty" })),
  ];
  $("issuesPanel").hidden = issues.length === 0;
  $("issueCount").textContent = issues.length;
  $("issues").innerHTML = issues.map(issue => `
    <div class="issue">
      <div class="row">
        <h3>${esc(issue.name)}</h3>
        <span class="badge ${issue.kind === "failed" ? "bad" : "short"}">
          ${issue.kind === "failed" ? "could not read" : "no recipes found"}</span>
        <span class="right">
          <button class="ghost" data-retry="${esc(issue.path)}">Retry</button>
        </span>
      </div>
      <p class="why">${esc(issue.cause)}</p>
      <p class="fix"><b>Fix:</b> ${esc(issue.fix)}</p>
      ${issue.error ? `<div class="raw">${esc(issue.error)}</div>` : ""}
    </div>`).join("");

  $("issues").querySelectorAll("[data-retry]").forEach(b =>
    b.onclick = async () => {
      try { render(await api("/api/library/retry", json({ path: b.dataset.retry }))); watch(); }
      catch (err) { alert(err.message); }
    });

  if (data.job && data.job.status === "running") { render(data.job); watch(); }
  else if (data.job) render(data.job);
  if (data.watch) renderWatch(data.watch);
}

// --- automatic indexing ----------------------------------------------------

function ago(seconds) {
  if (!seconds) return "";
  const gone = Date.now() / 1000 - seconds;
  if (gone < 60) return "just now";
  if (gone < 3600) return `${Math.round(gone / 60)} min ago`;
  return "at " + new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderWatch(w) {
  const toggle = $("autoToggle");
  toggle.checked = w.enabled;
  toggle.disabled = !w.available;
  $("autoState").textContent = w.enabled ? "On" : "Off";
  $("autoCheck").disabled = !w.available;

  let text;
  if (!w.available) {
    text = "Switched off on the server (PANTRY_CHEF_AUTO_INDEX=off). "
      + "New books are indexed when you press Index.";
  } else if (!w.enabled) {
    text = "Off. New books are indexed only when you press Index, or Check now.";
  } else {
    const every = w.minutes === 1 ? "Every minute" : `Every ${w.minutes} minutes`;
    text = `${every}, your folders are checked for new or changed books, and those are `
      + "indexed on their own — nothing to press. Books still being copied in are "
      + "left until they are complete, and nothing is ever removed.";
    if (w.last_check) {
      text += ` Last looked ${ago(w.last_check)}: `
        + (w.last_found
          ? `${w.last_found} new or changed book${w.last_found === 1 ? "" : "s"}.`
          : `nothing new in ${w.books.toLocaleString()} book${w.books === 1 ? "" : "s"}.`);
    } else {
      text += " The first look is a few seconds after the app starts.";
    }
  }
  if (w.error) text += ` Last problem: ${w.error}`;
  $("autoInfo").textContent = text;
}

$("autoToggle").addEventListener("change", async () => {
  const toggle = $("autoToggle");
  try {
    renderWatch(await api("/api/library/watch", json({ enabled: toggle.checked })));
  } catch (err) {
    toggle.checked = !toggle.checked;
    $("autoInfo").textContent = `Not changed: ${err.message}`;
  }
});

$("autoCheck").addEventListener("click", async () => {
  const button = $("autoCheck");
  button.disabled = true;
  try {
    const result = await api("/api/library/watch/check", json({}));
    renderWatch(result.watch);
    if (result.started) { render(result.job); watch(); }
  } catch (err) {
    $("autoInfo").textContent = `Could not look: ${err.message}`;
  } finally {
    button.disabled = false;
  }
});

// An automatic run can start while this page is open; notice it, and keep
// "last looked" current, without polling hard.
setInterval(async () => {
  const data = await api("/api/library/watch").catch(() => null);
  if (!data) return;
  renderWatch(data.watch);
  if (data.job.status === "running" && !polling) { render(data.job); watch(); }
}, 15000);

load();
