// Polls /api/run/events and fires a Toast for each new event — shared
// across every page (Dashboard, Queue) so live retry/fallback/failure
// notifications show up regardless of which page is open during a run.
// Framework-free like toast.js, so it can run alongside any Vue app.
const RunEvents = (() => {
  let lastSeenId = 0;
  let pollHandle = null;

  const LABELS = {
    retrying: (e) => `Batch ${e.batch_index}/${e.batch_total} failed — ${e.detail}`,
    retry_succeeded: (e) => `Batch ${e.batch_index}/${e.batch_total} succeeded on retry`,
    fell_back: (e) => `Batch ${e.batch_index}/${e.batch_total}: ${e.detail}`,
    item_failed: (e) => `Item failed: ${e.detail}`,
  };

  async function poll() {
    try {
      const result = await Api.getRunEvents(lastSeenId);
      for (const e of result.events) {
        lastSeenId = Math.max(lastSeenId, e.id);
        const label = LABELS[e.event_type];
        if (label) Toast.show(label(e), { duration: 6000 });
      }
    } catch (_) {
      // transient failure — just try again next tick
    }
  }

  async function start(intervalMs = 2000) {
    if (pollHandle) return; // already running
    // Seek to the current tip before polling, so a freshly loaded page
    // doesn't replay the whole buffered backlog (old runs' retries/
    // failures) as toasts — only events emitted AFTER this page opened.
    try {
      const { id } = await Api.getLatestRunEventId();
      lastSeenId = id;
    } catch (_) {
      // if this fails, fall through and poll from 0 rather than never
      // starting at all
    }
    const tick = async () => {
      await poll();
      pollHandle = setTimeout(tick, intervalMs);
    };
    tick();
  }

  function stop() {
    if (pollHandle) clearTimeout(pollHandle);
    pollHandle = null;
  }

  return { start, stop };
})();
