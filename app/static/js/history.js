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
    this.pageLoading = false;
  },
}).mount("#history-app");
