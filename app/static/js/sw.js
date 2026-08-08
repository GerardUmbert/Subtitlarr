// Installability-only service worker — no caching. Subtitlarr is a live
// dashboard over an active translation queue; every page already busts its
// own JS/CSS cache via asset_version on each server restart, and the data
// itself (stats, queue, history) is meaningless without a live connection
// to the server anyway. A caching SW here would risk serving stale assets
// or a frozen dashboard state, so this exists purely to satisfy browsers'
// "has a service worker" install requirement.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", () => {
  // Intentionally not calling event.respondWith() — every request falls
  // through to the browser's normal network handling, unmodified.
});
