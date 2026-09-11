// Correcting a recipe by hand.
//
// The recipe page calls PantryEdit.open(recipeId, done). The form replaces the
// recipe card until it is saved or cancelled; `done(result)` is then called,
// with the server's answer after a save and null after a cancel, so the page
// can reload what changed.
//
// Values are put into the form with .value, never through markup, and the
// book's version is shown through esc().
(() => {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  const MAX_MINUTES = 7 * 24 * 60;
  const NAMES = {
    title: "title", servings: "servings", total_minutes: "time",
    ingredients: "ingredients", instructions: "method",
  };
  const INPUTS = {
    title: "edTitle", servings: "edServings", total_minutes: "edTime",
    ingredients: "edIngredients", instructions: "edMethod",
  };

  let recipeId = null;
  let form = null;          // what the server sent: values, book, changed
  let done = null;
  let dirty = false;
  let saving = false;

  const isOpen = () => !$("editor").hidden;

  function asText(field, value) {
    if (field === "ingredients") return (value || []).join("\n");
    if (field === "total_minutes") return value == null ? "" : String(value);
    return value ?? "";
  }

  function bookLine(field) {
    if (!form.changed.includes(field)) return "";
    const book = form.book[field];
    const reset = `<button type="button" class="linkish" data-reset="${field}">Use the book’s</button>`;
    if (field === "ingredients" || field === "instructions") {
      return `<details class="was"><summary>The book’s version</summary>
        <pre>${esc(asText(field, book))}</pre>${reset}</details>`;
    }
    const shown = field === "total_minutes"
      ? (book == null ? "not given" : `${book} min`)
      : (book || "not given");
    return `<p class="was">Book: <b>${esc(shown)}</b> · ${reset}</p>`;
  }

  function render() {
    const changed = form.changed.map(f => NAMES[f]).join(", ");
    $("editor").innerHTML = `
      <form class="card-page editor" id="editForm" novalidate>
        <div class="eyebrow">Edit recipe</div>
        <p class="editor-intro">Fix what was read wrongly from the book. Search, the
          filters and the allergen check use your version straight away, and the
          book’s is kept, so you can go back to it at any time.${
          changed ? `<br><b>Corrected so far:</b> ${esc(changed)}.` : ""}</p>

        <div class="ed-field">
          <label for="edTitle">Title</label>
          <input type="text" id="edTitle" maxlength="200" required autocomplete="off">
          ${bookLine("title")}
        </div>

        <div class="ed-row">
          <div class="ed-field">
            <label for="edServings">Servings</label>
            <input type="text" id="edServings" maxlength="60" autocomplete="off"
                   placeholder="e.g. 4, or 4 to 6">
            ${bookLine("servings")}
          </div>
          <div class="ed-field">
            <label for="edTime">Total time <span class="hint">in minutes</span></label>
            <input type="number" id="edTime" min="1" max="${MAX_MINUTES}" step="1"
                   inputmode="numeric" placeholder="not known">
            ${bookLine("total_minutes")}
          </div>
        </div>

        <div class="ed-field">
          <label for="edIngredients">Ingredients
            <span class="hint">one per line, amounts as the book writes them</span></label>
          <textarea id="edIngredients" spellcheck="false"></textarea>
          ${bookLine("ingredients")}
        </div>

        <div class="ed-field">
          <label for="edMethod">Method
            <span class="hint">one step per paragraph, with a blank line between steps</span></label>
          <textarea id="edMethod"></textarea>
          ${bookLine("instructions")}
        </div>

        <p class="ed-error" id="edError" role="alert" hidden></p>

        <div class="ed-actions">
          <button type="submit" class="go" id="edSave">Save corrections</button>
          <button type="button" class="ghost" id="edCancel">Cancel</button>
          ${form.changed.length
            ? `<button type="button" class="linkish ed-revert" id="edRevert">Undo all corrections</button>`
            : ""}
        </div>
      </form>`;

    for (const [field, id] of Object.entries(INPUTS)) $(id).value = asText(field, form.values[field]);
    fit($("edIngredients"));
    fit($("edMethod"));
  }

  /** Grow a textarea to its content, within reason. */
  function fit(area) {
    const lines = area.value.split("\n").length;
    area.rows = Math.min(28, Math.max(6, lines + 1));
  }

  function showError(text) {
    const el = $("edError");
    el.textContent = text;
    el.hidden = !text;
    if (text) el.scrollIntoView({ block: "nearest" });
  }

  function readable(detail) {
    // FastAPI's own validation errors come as a list; ours as a sentence.
    if (Array.isArray(detail)) {
      return detail.map(d => `${NAMES[(d.loc || []).slice(-1)[0]] || "input"}: ${d.msg}`).join("; ");
    }
    return detail || "";
  }

  function collect() {
    const minutes = $("edTime").value.trim();
    return {
      title: $("edTitle").value.trim(),
      servings: $("edServings").value.trim(),
      total_minutes: minutes === "" ? null : Number(minutes),
      ingredients: $("edIngredients").value.split("\n").map(s => s.trim()).filter(Boolean),
      instructions: $("edMethod").value.trim(),
    };
  }

  function problem(body) {
    if (!body.title) return ["A recipe needs a title.", "edTitle"];
    const m = body.total_minutes;
    if (m !== null && (!Number.isInteger(m) || m < 1 || m > MAX_MINUTES)) {
      return [`The time must be a whole number of minutes, from 1 to ${MAX_MINUTES}.`, "edTime"];
    }
    if (!body.ingredients.length) return ["A recipe needs at least one ingredient.", "edIngredients"];
    return null;
  }

  async function save(e) {
    e?.preventDefault();
    if (saving) return;
    const body = collect();
    const wrong = problem(body);
    if (wrong) { showError(wrong[0]); $(wrong[1]).focus(); return; }

    saving = true;
    const button = $("edSave");
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      const res = await fetch(`/api/recipe/${recipeId}/edit`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(readable(data.detail) || res.statusText);
      dirty = false;
      close({ result: data });
    } catch (err) {
      showError(`Not saved: ${err.message || "is the server still running?"}`);
    } finally {
      saving = false;
      if (isOpen()) { button.disabled = false; button.textContent = "Save corrections"; }
    }
  }

  async function revertAll() {
    if (!window.confirm("Undo all your corrections and go back to the book’s version?")) return;
    try {
      const res = await fetch(`/api/recipe/${recipeId}/edit`, { method: "DELETE" });
      if (!res.ok) throw new Error(res.statusText);
      dirty = false;
      close({ result: { changed: [], folded: [], reverted: true } });
    } catch (err) {
      showError(`Could not undo: ${err.message}`);
    }
  }

  function cancel() {
    if (dirty && !window.confirm("Discard your changes?")) return;
    dirty = false;
    close();
  }

  async function open(id, onDone) {
    recipeId = id;
    done = onDone;
    const res = await fetch(`/api/recipe/${id}/edit`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    form = await res.json();
    dirty = false;
    render();
    $("page").hidden = true;
    $("editor").hidden = false;
    if (location.hash !== "#edit") {
      history.pushState({ edit: true }, "", location.href.split("#")[0] + "#edit");
    }
    window.scrollTo(0, 0);
    $("edTitle").focus();
  }

  function close({ result = null, fromHistory = false } = {}) {
    if (!isOpen()) return;
    $("editor").hidden = true;
    $("editor").innerHTML = "";
    $("page").hidden = false;
    if (!fromHistory && location.hash === "#edit") {
      if (history.state && history.state.edit) history.back();
      else history.replaceState(history.state, "", location.href.split("#")[0]);
    }
    done?.(result);
  }

  // --- wiring ---------------------------------------------------------------------

  $("editor").addEventListener("submit", save);
  $("editor").addEventListener("input", (e) => {
    dirty = true;
    if (e.target.tagName === "TEXTAREA") fit(e.target);
    showError("");
  });
  $("editor").addEventListener("click", (e) => {
    if (e.target.id === "edCancel") cancel();
    if (e.target.id === "edRevert") revertAll();
    const reset = e.target.closest("[data-reset]");
    if (reset) {
      const field = reset.dataset.reset;
      const input = $(INPUTS[field]);
      input.value = asText(field, form.book[field]);
      if (input.tagName === "TEXTAREA") fit(input);
      dirty = true;
      input.focus();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (isOpen() && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    }
  });

  // The back button leaves the editor rather than the page — after asking,
  // if there is something to lose.
  window.addEventListener("popstate", () => {
    if (!isOpen() || location.hash === "#edit") return;
    if (dirty && !window.confirm("Discard your changes?")) {
      history.pushState({ edit: true }, "", location.href.split("#")[0] + "#edit");
      return;
    }
    dirty = false;
    close({ fromHistory: true });
  });
  window.addEventListener("beforeunload", (e) => {
    if (isOpen() && dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  window.PantryEdit = { open, isOpen };
})();
