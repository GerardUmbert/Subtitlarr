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

function formatSecs(secs) {
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

function duration(item) {
  if (!item.last_attempt_at) return "—";
  const started = new Date(item.last_attempt_at).getTime();
  if (item.status === "translating") {
    // Checked BEFORE completed_at on purpose: a re-run of an already-done
    // item still carries its PREVIOUS run's completed_at (never cleared
    // when a new attempt starts) — trusting it here would compute
    // finished-started against a stale timestamp from before this
    // attempt even began, instead of showing the live count-up.
    return formatSecs(Math.max(0, Math.round((Date.now() - started) / 1000))) + "…";
  }
  if (item.completed_at) {
    const finished = new Date(item.completed_at).getTime();
    return formatSecs(Math.max(0, Math.round((finished - started) / 1000)));
  }
  if (item.status === "failed" && item.last_updated) {
    // No completed_at on a failure, but last_updated is stamped at the
    // exact moment the failure was recorded — close enough to "when it
    // stopped" to show real elapsed time instead of a dash.
    const failedAt = new Date(item.last_updated).getTime();
    return formatSecs(Math.max(0, Math.round((failedAt - started) / 1000)));
  }
  return "—";
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
      pageLoading: true,
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
      currentRunItems: [],
      currentBatchOnly: false,
      excludeNoSource: true,
      sortBy: null,
      sortDir: "asc",
      filters: [
        { label: "All", value: "" },
        { label: "Queued", value: "pending" },
        { label: "Translating", value: "translating" },
        { label: "Pending upload", value: "translated_pending_upload" },
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
      errorModal: null, // the clicked item, or null when closed
    };
  },
  created() {
    // Read filter/page state from the URL BEFORE mount's first refresh(),
    // so a page reload lands back on the same tab/filters/page instead of
    // always resetting to defaults.
    const params = new URLSearchParams(window.location.search);
    if (params.has("status")) this.statusFilter = params.get("status");
    if (params.has("item_type")) this.typeFilter = params.get("item_type");
    if (params.has("search")) {
      this.search = params.get("search");
      this.searchInput = this.search;
    }
    if (params.has("page")) {
      const p = parseInt(params.get("page"), 10);
      if (Number.isFinite(p) && p > 0) this.page = p;
    }
    if (params.get("batch") === "1") this.currentBatchOnly = true;
    if (params.has("exclude_no_source")) this.excludeNoSource = params.get("exclude_no_source") === "1";
    if (params.has("sort_by")) this.sortBy = params.get("sort_by");
    if (params.has("sort_dir")) this.sortDir = params.get("sort_dir");
  },
  computed: {
    totalPages() {
      return Math.max(1, Math.ceil(this.total / this.pageSize));
    },
    displayedItems() {
      return this.currentBatchOnly ? this.currentRunItems : this.items;
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
      this.currentBatchOnly = false;
      this.statusFilter = value;
      this.page = 1;
      this.syncUrl();
      this.refresh();
    },
    setTypeFilter(value) {
      this.currentBatchOnly = false;
      this.typeFilter = value;
      this.page = 1;
      this.syncUrl();
      this.refresh();
    },
    toggleCurrentBatch() {
      this.currentBatchOnly = !this.currentBatchOnly;
      this.syncUrl();
    },
    sortIndicator(column) {
      if (this.sortBy !== column) return "";
      return this.sortDir === "asc" ? "▲" : "▼";
    },
    setSort(column) {
      if (this.sortBy === column) {
        this.sortDir = this.sortDir === "asc" ? "desc" : "asc";
      } else {
        this.sortBy = column;
        this.sortDir = "asc";
      }
      this.page = 1;
      this.syncUrl();
      this.refresh();
    },
    onExcludeNoSourceChange() {
      this.page = 1;
      this.syncUrl();
      this.refresh();
    },
    onSearchInput() {
      if (this._searchDebounceHandle) clearTimeout(this._searchDebounceHandle);
      this._searchDebounceHandle = setTimeout(() => {
        this.search = this.searchInput.trim();
        this.page = 1;
        this.syncUrl();
        this.refresh();
      }, 300);
    },
    prevPage() {
      if (this.page > 1) {
        this.page -= 1;
        this.syncUrl();
        this.refresh();
      }
    },
    nextPage() {
      if (this.page < this.totalPages) {
        this.page += 1;
        this.syncUrl();
        this.refresh();
      }
    },
    syncUrl() {
      const params = new URLSearchParams();
      if (this.statusFilter) params.set("status", this.statusFilter);
      if (this.typeFilter) params.set("item_type", this.typeFilter);
      if (this.search) params.set("search", this.search);
      if (this.page > 1) params.set("page", String(this.page));
      if (this.currentBatchOnly) params.set("batch", "1");
      // Always written explicitly (not just when true) — the default is
      // now true, so omitting it when false would make a reload silently
      // re-check a box the user just unchecked.
      params.set("exclude_no_source", this.excludeNoSource ? "1" : "0");
      if (this.sortBy) {
        params.set("sort_by", this.sortBy);
        params.set("sort_dir", this.sortDir);
      }
      const qs = params.toString();
      const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
      // replaceState (not pushState) — filter changes shouldn't pile up
      // separate entries in the browser's back-button history.
      window.history.replaceState(null, "", newUrl);
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
        const params = { ...this.filterParams(), page: this.page, page_size: this.pageSize };
        if (this.excludeNoSource) params.exclude_no_source = true;
        if (this.sortBy) {
          params.sort_by = this.sortBy;
          params.sort_dir = this.sortDir;
        }
        const result = await Api.getQueue(params);
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
          this.currentRunItems = [];
        }
      } catch (_) {
        // keep last known state on transient failure
      }
      await this.refreshCurrentRunItems();
    },
    async refreshCurrentRunItems() {
      if (!this.runActive) return;
      try {
        const result = await Api.getCurrentRunItems();
        this.currentRunItems = result.active ? result.data : [];
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
    openErrorModal(item) {
      this.errorModal = item;
    },
    closeErrorModal() {
      this.errorModal = null;
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
    this.pageLoading = false;
    this.schedulePoll();
    document.addEventListener("visibilitychange", this.onVisibilityChange);
  },
  unmounted() {
    if (this._pollHandle) clearTimeout(this._pollHandle);
    if (this._searchDebounceHandle) clearTimeout(this._searchDebounceHandle);
    document.removeEventListener("visibilitychange", this.onVisibilityChange);
  },
}).mount("#queue-app");
