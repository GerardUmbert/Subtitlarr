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

function itemDuration(item) {
  if (!item.last_attempt_at || !item.completed_at) return "—";
  const started = new Date(item.last_attempt_at).getTime();
  const finished = new Date(item.completed_at).getTime();
  const secs = Math.max(0, Math.round((finished - started) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

function runDuration(run) {
  if (!run.started_at || !run.finished_at) return run.finished_at === null && run.started_at ? "in progress" : "—";
  const started = new Date(run.started_at).getTime();
  const finished = new Date(run.finished_at).getTime();
  const secs = Math.max(0, Math.round((finished - started) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  if (mins < 60) return `${mins}m ${rem}s`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

function formatDateTime(isoString) {
  if (!isoString) return "—";
  return new Date(isoString).toLocaleString();
}

const TRIGGERED_BY_LABELS = {
  manual_full: "Run all",
  scheduled: "Scheduled",
  manual_item: "Single item",
  manual_filtered: "Filtered batch",
};

createApp({
  data() {
    return {
      pageLoading: true,
      runs: [],
      total: 0,
      page: 1,
      pageSize: 20,
      expandedRunId: null,
      runItems: {}, // run_id -> items[], loaded lazily on expand
      loadingItemsForRunId: null,
      errorModal: null, // the clicked item, or null when closed
      runningItemId: null,
      runActive: false,
    };
  },
  computed: {
    totalPages() {
      return Math.max(1, Math.ceil(this.total / this.pageSize));
    },
  },
  methods: {
    statusLabel(status) {
      return STATUS_LABELS[status] || status;
    },
    triggeredByLabel(triggeredBy) {
      return TRIGGERED_BY_LABELS[triggeredBy] || triggeredBy;
    },
    itemDuration,
    runDuration,
    formatDateTime,
    engineSummary(run) {
      if (!run.primary_engine) return "—";
      if (!run.other_engines || run.other_engines.length === 0) return run.primary_engine;
      return `${run.primary_engine} (+${run.other_engines.length} via ${run.other_engines.join(", ")})`;
    },
    async refresh() {
      try {
        const result = await Api.getHistory({ page: this.page, page_size: this.pageSize });
        this.runs = result.data;
        this.total = result.total;
      } catch (_) {
        // keep last known state on transient failure
      }
    },
    async toggleExpand(run) {
      if (this.expandedRunId === run.id) {
        this.expandedRunId = null;
        return;
      }
      this.expandedRunId = run.id;
      if (this.runItems[run.id]) return; // already loaded
      this.loadingItemsForRunId = run.id;
      try {
        const result = await Api.getHistoryRunItems(run.id);
        this.runItems = { ...this.runItems, [run.id]: result.data };
      } catch (_) {
        this.runItems = { ...this.runItems, [run.id]: [] };
      } finally {
        this.loadingItemsForRunId = null;
      }
    },
    openErrorModal(item) {
      this.errorModal = item;
    },
    closeErrorModal() {
      this.errorModal = null;
    },
    async runItem(item) {
      this.runningItemId = item.item_id;
      try {
        const result = await Api.runItem(item.item_id);
        if (!result.started) {
          alert(result.reason || "Could not start item");
          this.runningItemId = null;
          return;
        }
        const source = (result.source_language || "?").toUpperCase();
        const target = item.target_language.toUpperCase();
        Toast.show(`Translating from ${source} to ${target}…`);
        this.runActive = true;
        this.pollRunState();
      } catch (err) {
        alert(`Could not start item: ${err.message}`);
        this.runningItemId = null;
      }
    },
    pollRunState() {
      if (this._runPollHandle) clearTimeout(this._runPollHandle);
      this._runPollHandle = setTimeout(async () => {
        try {
          const run = await Api.getRunCurrent();
          this.runActive = !!run.active;
        } catch (_) {
          // keep last known state on transient failure
        }
        if (this.runActive) {
          this.pollRunState();
        } else {
          this.runningItemId = null;
          // Refresh whichever run's items are currently expanded so the
          // just-finished re-run's new status/duration/error show up
          // without a full page reload.
          if (this.expandedRunId !== null) {
            const result = await Api.getHistoryRunItems(this.expandedRunId).catch(() => null);
            if (result) this.runItems = { ...this.runItems, [this.expandedRunId]: result.data };
          }
        }
      }, 2000);
    },
    prevPage() {
      if (this.page > 1) {
        this.page -= 1;
        this.refresh();
      }
    },
    nextPage() {
      if (this.page < this.totalPages) {
        this.page += 1;
        this.refresh();
      }
    },
  },
  async mounted() {
    await this.refresh();
    try {
      const run = await Api.getRunCurrent();
      this.runActive = !!run.active;
      if (this.runActive) this.pollRunState();
    } catch (_) {
      // endpoint not reachable yet — ignore
    }
    this.pageLoading = false;
  },
  unmounted() {
    if (this._runPollHandle) clearTimeout(this._runPollHandle);
  },
}).mount("#history-app");
