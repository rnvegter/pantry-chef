// The count beside "Shopping" in every page's navigation.
//
// Pages that change the list call PantryNav.setShoppingCount(n) with the count
// the server sent back, so the badge moves without another request.
(() => {
  "use strict";

  function paint(count) {
    const badge = document.getElementById("shopCount");
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = !count;
    const link = badge.closest("a");
    if (link) {
      link.setAttribute("aria-label",
        count ? `Shopping list, ${count} recipe${count === 1 ? "" : "s"}` : "Shopping list");
    }
  }

  fetch("/api/shopping/summary")
    .then(res => (res.ok ? res.json() : null))
    .then(data => { if (data) paint(data.recipes); })
    .catch(() => { /* the badge is a nicety; the link works without it */ });

  window.PantryNav = { setShoppingCount: paint };
})();
