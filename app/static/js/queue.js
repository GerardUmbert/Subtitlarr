const { createApp } = Vue;

const STATUS_LABELS = {
  pending: "queued",
  queued: "queued",
  translating: "translating",
  done: "done",
  failed: "failed",
  skipped_no_source: "no source",
};

function duration(item) {
  if (!item.last_attempt_at || !item.completed_at) return "—";
  const started = new Date(item.last_attempt_at).getTime();
  const finished = new Date(item.completed_at).getTime();
  const secs = Math.max(0, Math.round((finished - started) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

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
      items: [],
      total: 0,
      page: 1,
      pageSize: 20,
      statusFilter: "",
      typeFilter: "",
      searchInput: "",
      search: "",
      runningItemId: null,
      runActive: false,
      matchingCount: 0,
      runningFiltered: false,
      filters: [
        { label: "All", value: "" },
        { label: "Queued", value: "pending" },
        { label: "Translating", value: "translating" },
        { label: "Done", value: "done" },
        { label: "Failed", value: "failed" },
        { label: "No source", value: "skipped_no_source" },
      ],
      typeFilters: [
        { label: "All types", value: "" },
        { label: "Movies", value: "movie" },
        { label: "TV", value: "episode" },
      ],
      _pollHandle: null,
      _searchDebounceHandle: null,
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
    timeAgo,
    duration,
    canRunItem(item) {
      if (this.runActive) return false; // a run (any run) is already in progress server-side
      return item.status !== "translating"; // done/failed/pending/skipped can all be (re-)run manually
    },
    setFilter(value) {
      this.statusFilter = value;
      this.page = 1;
      this.refresh();
    },
    setTypeFilter(value) {
      this.typeFilter = value;
      this.page = 1;
      this.refresh();
    },
    onSearchInput() {
      if (this._searchDebounceHandle) clearTimeout(this._searchDebounceHandle);
      this._searchDebounceHandle = setTimeout(() => {
        this.search = this.searchInput.trim();
        this.page = 1;
        this.refresh();
      }, 300);
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
    filterParams() {
      const params = {};
      if (this.statusFilter) params.status = this.statusFilter;
      if (this.typeFilter) params.item_type = this.typeFilter;
      if (this.search) params.search = this.search;
      return params;
    },
    async refresh() {
      try {
        const result = await Api.getQueue({ ...this.filterParams(), page: this.page, page_size: this.pageSize });
        this.items = result.data;
        this.total = result.total;
      } catch (_) {
        // keep last known state on transient failure
      }
      await this.refreshMatchingCount();
    },
    async refreshMatchingCount() {
      try {
        const result = await Api.getMatchingCount(this.filterParams());
        this.matchingCount = result.count;
      } catch (_) {
        // keep last known state on transient failure
      }
    },
    async runFiltered() {
      this.runningFiltered = true;
      try {
        const result = await Api.runFiltered(this.filterParams());
        if (!result.started) {
          alert(result.reason || "Could not start run");
          return;
        }
        Toast.show(`Translating ${this.matchingCount} matching item(s)…`);
        this.runActive = true;
        await this.refresh();
      } catch (err) {
        alert(`Could not start run: ${err.message}`);
      } finally {
        this.runningFiltered = false;
      }
    },
    async runItem(item) {
      this.runningItemId = item.id;
      try {
        const result = await Api.runItem(item.id);
        if (!result.started) {
          alert(result.reason || "Could not start item");
          this.runningItemId = null;
          await this.refreshRunState(); // correct a stale button state immediately, don't wait for the next poll
          return;
        }
        // Prefer the freshly re-resolved source language from the response
        // (checked against Bazarr right now) over the item's possibly-stale
        // cached value, since a manual re-run is often prompted by exactly
        // that — something changed on Bazarr's end since the last poll.
        const source = (result.source_language || item.source_language || "?").toUpperCase();
        const target = item.target_language.toUpperCase();
        Toast.show(`Translating from ${source} to ${target}…`);
        this.runActive = true;
        await this.refresh();
      } catch (err) {
        alert(`Could not start item: ${err.message}`);
        this.runningItemId = null;
      }
    },
    async refreshRunState() {
      try {
        const run = await Api.getRunCurrent();
        this.runActive = !!run.active;
        if (!this.runActive) {
          this.runningItemId = null; // the job that was running has finished — release the button
        }
      } catch (_) {
        // keep last known state on transient failure
      }
    },
    schedulePoll() {
      if (this._pollHandle) clearTimeout(this._pollHandle);
      // Poll faster while idle than before (2s) so another run started
      // elsewhere (Dashboard, per-item run, scheduled run) disables this
      // page's run buttons quickly rather than leaving a stale window
      // where a click here silently no-ops server-side.
      this._pollHandle = setTimeout(async () => {
        await this.refreshRunState();
        await this.refresh();
        this.schedulePoll();
      }, this.runActive ? 2000 : 3000);
    },
    onVisibilityChange() {
      if (document.visibilityState === "visible") {
        this.refreshRunState();
      }
    },
  },
  async mounted() {
    await this.refreshRunState();
    await this.refresh();
    this.schedulePoll();
    document.addEventListener("visibilitychange", this.onVisibilityChange);
  },
  unmounted() {
    if (this._pollHandle) clearTimeout(this._pollHandle);
    if (this._searchDebounceHandle) clearTimeout(this._searchDebounceHandle);
    document.removeEventListener("visibilitychange", this.onVisibilityChange);
  },
}).mount("#queue-app");
