// Minimal toast notifications, framework-free so any page can call
// Toast.show() regardless of which Vue app (if any) owns that page.
const Toast = (() => {
  let container = null;
  // A burst of run events (e.g. many batches falling back/failing at once)
  // can fire dozens of toasts within a couple seconds — without a cap they
  // all stack indefinitely and cover the screen. Oldest visible toast is
  // dismissed first to make room, same as a typical toast queue.
  const MAX_VISIBLE = 4;

  // A sustained streak (not just a one-off burst) can hit this MUCH
  // harder — confirmed live: an all-Gemini cascade with its only
  // non-Gemini fallback rate-limited means every content-blocked item in
  // a run fails immediately with no bisection/retry, one after another,
  // ~1-2s apart, for as long as the streak lasts (potentially dozens of
  // episodes). At that sustained rate, creating a full DOM node per
  // event — even a correctly-cleaned-up one — is still continuous
  // layout/paint work for as long as the run keeps failing. Two
  // independent throttles below handle this: identical repeated messages
  // update one toast in place instead of spawning a new node each time,
  // and a hard minimum gap between distinct new toasts drops the rest
  // rather than queuing them up.
  const REPEAT_WINDOW_MS = 4000;
  const MIN_CREATE_GAP_MS = 500;
  let lastMessage = null;
  let lastMessageEl = null;
  let lastMessageCount = 0;
  let lastMessageAt = 0;
  let lastCreateAt = 0;

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
    if (el === lastMessageEl) {
      lastMessageEl = null;
      lastMessage = null;
    }
  }

  function show(message, { duration = 4000 } = {}) {
    const now = Date.now();

    // Same message as the currently-visible toast, seen again within the
    // window — update its count in place instead of creating a new node.
    // Handles the sustained-identical-failure case (e.g. many episodes in
    // a row all hitting the same content-block message) without growing
    // the DOM at all past the first occurrence.
    if (message === lastMessage && lastMessageEl && now - lastMessageAt < REPEAT_WINDOW_MS) {
      lastMessageCount += 1;
      lastMessageAt = now;
      lastMessageEl.textContent = `${message} (×${lastMessageCount})`;
      clearTimeout(lastMessageEl._dismissTimer);
      lastMessageEl._dismissTimer = setTimeout(() => dismiss(lastMessageEl), duration);
      return;
    }

    // A genuinely new/different message arriving faster than this floor
    // is dropped rather than queued — under a sustained failure streak,
    // queuing would just delay the backlog forever while still doing
    // full DOM work for every single one eventually. Silently skipping
    // is fine here: the same information is always live on-page (Queue/
    // Dashboard/History), this popup is a convenience, not the record.
    if (now - lastCreateAt < MIN_CREATE_GAP_MS) return;
    lastCreateAt = now;

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
    el._dismissTimer = setTimeout(() => dismiss(el), duration);

    lastMessage = message;
    lastMessageEl = el;
    lastMessageCount = 1;
    lastMessageAt = now;
  }

  return { show };
})();
