const { createApp } = Vue;

const STATUS_LABELS = {
  pending: "queued",
  queued: "queued",
  translating: "translating",
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
      polling: false,
      _refreshTimerHandle: null,
    };
  },
  computed: {
    runActive() {
      return this.run.active;
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
    async refreshAll() {
      await Promise.all([this.refreshStats(), this.refreshRun(), this.refreshQueue()]);
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
