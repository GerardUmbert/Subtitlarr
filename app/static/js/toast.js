// Minimal toast notifications, framework-free so any page can call
// Toast.show() regardless of which Vue app (if any) owns that page.
const Toast = (() => {
  let container = null;

  function ensureContainer() {
    if (!container) {
      container = document.createElement("div");
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    return container;
  }

  function show(message, { duration = 4000 } = {}) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = message;
    ensureContainer().appendChild(el);
    // next frame, so the initial state transitions in rather than popping
    requestAnimationFrame(() => el.classList.add("toast-visible"));
    setTimeout(() => {
      el.classList.remove("toast-visible");
      el.addEventListener("transitionend", () => el.remove(), { once: true });
    }, duration);
  }

  return { show };
})();
