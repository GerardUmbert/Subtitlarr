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

const JOB_LABELS = {
  sync_media: "Sync wanted / missing",
  sync_subs: "Pull pending subtitles",
  push_uploads: "Push queued uploads",
};

const EVENT_TYPE_LABELS = {
  sending: "sending",
  response: "response",
  item_done: "item done",
  rate_limited_retry: "rate-limited, retrying",
  content_blocked_fallback: "content blocked → fallback",
  provider_failed_fallback: "provider failed → fallback",
  watchdog_timeout: "watchdog timeout",
  item_failed: "item failed",
};

const EVENT_TYPE_FILTER_OPTIONS = [
  { value: "", label: "All types" },
  { value: "sending", label: "Sending" },
  { value: "response", label: "Response" },
  { value: "item_done", label: "Item done" },
  { value: "rate_limited_retry", label: "Rate-limited retry" },
  { value: "content_blocked_fallback", label: "Content blocked" },
  { value: "provider_failed_fallback", label: "Provider failed" },
  { value: "watchdog_timeout", label: "Watchdog timeout" },
  { value: "item_failed", label: "Item failed" },
];

function formatLogTimestamp(ts) {
  // Log timestamps are "YYYY-MM-DD HH:MM:SS,mmm" (local server time, not ISO) —
  // swap the comma for a period so `new Date()` parses it instead of returning
  // Invalid Date.
  if (!ts) return "—";
  return new Date(ts.replace(",", ".")).toLocaleString();
}

createApp({
  data() {
    return {
      pageLoading: true,
      activeTab: "runs", // 'runs' | 'jobs' | 'mismatches' | 'events' | 'stats'

      // Jobs tab
      jobEvents: [],
      jobsLoading: false,

      // Language Mismatches tab
      languageMismatches: [],
      mismatchesLoading: false,
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
      runsSortBy: null,
      runsSortDir: "desc",

      // Events tab
      events: [],
      eventsLoading: false,
      eventsItemIdFilter: null, // set when deep-linking from a run's items
      eventsEngineFilter: "",
      eventsTypeFilter: "",
      eventsSortBy: null,
      eventsSortDir: "desc",

      // Stats tab
      stats: null,
      statsLoading: false,
      statsRange: "all",
    };
  },
  computed: {
    totalPages() {
      return Math.max(1, Math.ceil(this.total / this.pageSize));
    },
    eventTypeFilterOptions() {
      return EVENT_TYPE_FILTER_OPTIONS;
    },
  },
  methods: {
    statusLabel(status) {
      return STATUS_LABELS[status] || status;
    },
    triggeredByLabel(triggeredBy) {
      return TRIGGERED_BY_LABELS[triggeredBy] || triggeredBy;
    },
    jobLabel(job) {
      return JOB_LABELS[job] || job;
    },
    async loadJobEvents() {
      this.jobsLoading = true;
      try {
        const result = await Api.getHistoryJobs();
        this.jobEvents = result.data;
      } catch (_) {
        // keep last known state on transient failure
      } finally {
        this.jobsLoading = false;
      }
    },
    async loadLanguageMismatches() {
      this.mismatchesLoading = true;
      try {
        const result = await Api.getLanguageMismatches();
        this.languageMismatches = result.data;
      } catch (_) {
        // keep last known state on transient failure
      } finally {
        this.mismatchesLoading = false;
      }
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
        const params = { page: this.page, page_size: this.pageSize };
        if (this.runsSortBy) {
          params.sort_by = this.runsSortBy;
          params.sort_dir = this.runsSortDir;
        }
        const result = await Api.getHistory(params);
        this.runs = result.data;
        this.total = result.total;
      } catch (_) {
        // keep last known state on transient failure
      }
    },
    runsSortIndicator(column) {
      if (this.runsSortBy !== column) return "";
      return this.runsSortDir === "asc" ? "▲" : "▼";
    },
    setRunsSort(column) {
      if (this.runsSortBy === column) {
        this.runsSortDir = this.runsSortDir === "asc" ? "desc" : "asc";
      } else {
        this.runsSortBy = column;
        this.runsSortDir = "desc";
      }
      this.page = 1;
      this.refresh();
    },
    eventsSortIndicator(column) {
      if (this.eventsSortBy !== column) return "";
      return this.eventsSortDir === "asc" ? "▲" : "▼";
    },
    setEventsSort(column) {
      if (this.eventsSortBy === column) {
        this.eventsSortDir = this.eventsSortDir === "asc" ? "desc" : "asc";
      } else {
        this.eventsSortBy = column;
        this.eventsSortDir = "desc";
      }
      this.loadEvents();
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
    eventTypeLabel(type) {
      return EVENT_TYPE_LABELS[type] || type;
    },
    formatLogTimestamp,
    async switchTab(tab) {
      this.activeTab = tab;
      if (tab === "jobs" && this.jobEvents.length === 0) {
        await this.loadJobEvents();
      } else if (tab === "mismatches" && this.languageMismatches.length === 0) {
        await this.loadLanguageMismatches();
      } else if (tab === "events" && this.events.length === 0) {
        await this.loadEvents();
      } else if (tab === "stats" && this.stats === null) {
        await this.loadStats();
      }
    },
    async loadEvents() {
      this.eventsLoading = true;
      try {
        const params = {};
        if (this.eventsItemIdFilter) params.item_id = this.eventsItemIdFilter;
        if (this.eventsEngineFilter) params.engine = this.eventsEngineFilter;
        if (this.eventsTypeFilter) params.event_type = this.eventsTypeFilter;
        if (this.eventsSortBy) {
          params.sort_by = this.eventsSortBy;
          params.sort_dir = this.eventsSortDir;
        }
        const result = await Api.getHistoryEvents(params);
        this.events = result.data;
      } catch (_) {
        // keep last known state on transient failure
      } finally {
        this.eventsLoading = false;
      }
    },
    setEventsTypeFilter(type) {
      this.eventsTypeFilter = type;
      this.loadEvents();
    },
    async viewEventsForItem(itemId) {
      this.eventsItemIdFilter = itemId;
      this.eventsEngineFilter = "";
      this.eventsTypeFilter = "";
      this.activeTab = "events";
      await this.loadEvents();
    },
    clearEventsItemFilter() {
      this.eventsItemIdFilter = null;
      this.loadEvents();
    },
    async loadStats() {
      this.statsLoading = true;
      try {
        this.stats = await Api.getHistoryStats(this.statsRange);
      } catch (_) {
        // keep last known state on transient failure
      } finally {
        this.statsLoading = false;
      }
    },
    async changeStatsRange(range) {
      this.statsRange = range;
      await this.loadStats();
    },
    barWidthPct(value, max) {
      if (!max) return 0;
      return Math.max(2, Math.round((value / max) * 100));
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
