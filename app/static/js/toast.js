// Minimal toast notifications, framework-free so any page can call
// Toast.show() regardless of which Vue app (if any) owns that page.
const Toast = (() => {
  let container = null;
  // A burst of run events (e.g. many batches falling back/failing at once)
  // can fire dozens of toasts within a couple seconds — without a cap they
  // all stack indefinitely and cover the screen. Oldest visible toast is
  // dismissed first to make room, same as a typical toast queue.
  const MAX_VISIBLE = 4;

  function ensureContainer() {
    if (!container) {
      container = document.createElement("div");
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    return container;
  }

  function dismiss(el) {
    // A toast evicted by the MAX_VISIBLE cap can still be mid-flight —
    // show() runs synchronously in a loop when a poll picks up several
    // events at once, and its own requestAnimationFrame (which adds
    // toast-visible) hasn't necessarily run yet by the time an OLDER
    // toast gets evicted. Removing a class an element never had fires no
    // CSS transition, so a listener-based removal would never fire and
    // el.remove() would never run — leaking the node and its listener
    // forever. Confirmed live: a long session with frequent event bursts
    // (retries/fallbacks) accumulated enough orphaned toast nodes and
    // listeners to eventually lock up the tab. A plain timeout removal
    // works regardless of whether a transition ever started.
    el.classList.remove("toast-visible");
    setTimeout(() => el.remove(), 250);
  }

  function show(message, { duration = 4000 } = {}) {
    const c = ensureContainer();
    while (c.children.length >= MAX_VISIBLE) {
      dismiss(c.firstElementChild);
    }
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = message;
    c.appendChild(el);
    // next frame, so the initial state transitions in rather than popping
    requestAnimationFrame(() => el.classList.add("toast-visible"));
    setTimeout(() => dismiss(el), duration);
  }

  return { show };
})();
