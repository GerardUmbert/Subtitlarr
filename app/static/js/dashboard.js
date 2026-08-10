const { createApp } = Vue;

const STATUS_LABELS = {
  pending: "queued",
  queued: "queued",
  translating: "translating",
  translated_pending_upload: "pending upload",
  done: "done",
  failed: "failed",
  skipped_no_source: "no source",
};

function timeAgo(isoString) {
  if (!isoString) return "—";
  const then = new Date(isoString).getTime();
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

createApp({
  data() {
    return {
      pageLoading: true,
      stats: {},
      run: { active: false, processed: 0, total: 0, failed: 0, rate_per_min: 0 },
      recentItems: [],
      engineInstances: [],
      polling: false,
      cancelling: false,
      _refreshTimerHandle: null,
    };
  },
  computed: {
    runActive() {
      return this.run.active;
    },
    // Non-separator, enabled instances only — a disabled instance or a
    // "stop cascade here" separator was never going to be tried, so
    // counting either toward "N of M cooling down" would misrepresent
    // how much real fallback capacity the cascade actually has right now.
    activeCascade() {
      return this.engineInstances.filter(
        (i) => i.provider_type !== "separator" && i.enabled
      );
    },
    // The first entry in the active cascade that ISN'T currently
    // rate-limited — matches how the runner itself picks cascade[0] for a
    // fresh item (see translator.py), so this reflects which engine would
    // actually receive the NEXT translation, not just whichever is first
    // in sort_order regardless of its cooldown state.
    activeEngine() {
      const now = Date.now();
      return (
        this.activeCascade.find(
          (i) => !i.rate_limited_until || new Date(i.rate_limited_until).getTime() <= now
        ) || this.activeCascade[0] || null
      );
    },
    coolingDownCount() {
      const now = Date.now();
      return this.activeCascade.filter(
        (i) => i.rate_limited_until && new Date(i.rate_limited_until).getTime() > now
      ).length;
    },
    // Instance names are free-text and often already include the model
    // (e.g. "Gemini Secondary gemini-3.1-flash-lite") — appending
    // config.model unconditionally then reads as "X gemini-3.1-flash-lite
    // · gemini-3.1-flash-lite". Only append it when the name doesn't
    // already contain it (case-insensitive, since a user might type the
    // model in a different case than the API returns it).
    activeEngineLabel() {
      const engine = this.activeEngine;
      if (!engine) return "";
      const model = engine.config && engine.config.model;
      if (!model || engine.name.toLowerCase().includes(model.toLowerCase())) {
        return engine.name;
      }
      return `${engine.name} · ${model}`;
    },
    pendingUploads() {
      return (this.stats.by_status && this.stats.by_status.translated_pending_upload) || 0;
    },
    progressPct() {
      if (!this.run.total) return 0;
      return Math.min(100, Math.round((this.run.processed / this.run.total) * 100));
    },
    etaLabel() {
      const secs = this.run.eta_seconds;
      if (!secs) return "—";
      const mins = Math.round(secs / 60);
      if (mins < 60) return `~${mins}m`;
      return `~${Math.floor(mins / 60)}h ${mins % 60}m`;
    },
  },
  methods: {
    statusLabel(status) {
      return STATUS_LABELS[status] || status;
    },
    timeAgo,
    async refreshStats() {
      try {
        this.stats = await Api.getStats();
      } catch (_) {
        // stats are best-effort; keep last known values on transient failure
      }
    },
    async refreshRun() {
      try {
        this.run = await Api.getRunCurrent();
      } catch (_) {
        // ignore transient errors, keep polling
      }
    },
    async refreshQueue() {
      try {
        const result = await Api.getQueue({ page_size: 8, sort: "recent" });
        this.recentItems = result.data;
      } catch (_) {
        // ignore transient errors
      }
    },
    async refreshEngines() {
      try {
        const result = await Api.listEngineInstances();
        this.engineInstances = result.data;
      } catch (_) {
        // ignore transient errors — keep last known state
      }
    },
    async refreshAll() {
      await Promise.all([
        this.refreshStats(), this.refreshRun(), this.refreshQueue(), this.refreshEngines(),
      ]);
    },
    async triggerRunNow() {
      try {
        const result = await Api.runNow();
        if (!result.started) {
          alert(result.reason || "Could not start run");
          return;
        }
        Toast.show("Translation run started…");
        await this.refreshRun();
        this.scheduleRefresh();
      } catch (err) {
        alert(`Could not start run: ${err.message}`);
      }
    },
    async cancelRun() {
      this.cancelling = true;
      try {
        const result = await Api.cancelRun();
        if (!result.cancelled) {
          alert(result.reason || "Could not stop the run");
          return;
        }
        Toast.show("Stopping after the current item finishes…");
        await this.refreshRun();
      } catch (err) {
        alert(`Could not stop the run: ${err.message}`);
      } finally {
        this.cancelling = false;
      }
    },
    async triggerPoll() {
      this.polling = true;
      try {
        const result = await Api.pollNow();
        if (!result.started) {
          alert(result.reason || "Could not start refresh");
          this.polling = false;
          return;
        }
        this.watchPollStatus();
      } catch (err) {
        alert(`Could not start refresh: ${err.message}`);
        this.polling = false;
      }
    },
    watchPollStatus() {
      const check = async () => {
        try {
          const status = await Api.getPollNowStatus();
          if (status.active) {
            setTimeout(check, 1000);
            return;
          }
          if (status.error) alert(`Refresh failed: ${status.error}`);
        } catch (_) {
          // ignore transient errors
        } finally {
          this.polling = false;
          await this.refreshAll();
        }
      };
      check();
    },
    scheduleRefresh() {
      if (this._refreshTimerHandle) clearTimeout(this._refreshTimerHandle);
      const interval = this.run.active ? 2000 : 10000;
      this._refreshTimerHandle = setTimeout(async () => {
        await this.refreshAll();
        this.scheduleRefresh();
      }, interval);
    },
  },
  async mounted() {
    await this.refreshAll();
    this.pageLoading = false;
    this.scheduleRefresh();
  },
  unmounted() {
    if (this._refreshTimerHandle) clearTimeout(this._refreshTimerHandle);
  },
}).mount("#dashboard-app");
