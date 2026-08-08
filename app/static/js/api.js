const Api = (() => {
  async function request(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    let data = null;
    try {
      data = await resp.json();
    } catch (_) {
      // no JSON body (e.g. some errors) — leave data null
    }
    if (!resp.ok) {
      const detail = data && data.detail ? data.detail : resp.statusText;
      throw new Error(detail || `Request failed: ${resp.status}`);
    }
    return data;
  }

  return {
    getStats: () => request("GET", "/api/stats"),
    getRunCurrent: () => request("GET", "/api/run/current"),
    getRunEvents: (since = 0) => request("GET", `/api/run/events?since=${since}`),
    getLatestRunEventId: () => request("GET", "/api/run/events/latest_id"),
    runNow: () => request("POST", "/api/run/now"),
    cancelRun: () => request("POST", "/api/run/cancel"),
    pollNow: () => request("POST", "/api/run/poll"),
    getPollNowStatus: () => request("GET", "/api/run/poll/status"),
    runItem: (id) => request("POST", `/api/queue/${id}/run`),
    getItem: (id) => request("GET", `/api/queue/${id}`),
    getMatchingCount: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/queue/matching-count${qs ? "?" + qs : ""}`);
    },
    runFiltered: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("POST", `/api/queue/run-filtered${qs ? "?" + qs : ""}`);
    },
    runByIds: (itemIds) => request("POST", "/api/queue/run-by-ids", { item_ids: itemIds }),
    getQueue: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/queue${qs ? "?" + qs : ""}`);
    },
    getCurrentRunItems: () => request("GET", "/api/queue/current-run"),
    getUsedModels: () => request("GET", "/api/queue/models"),

    pullOllamaModel: (model, base_url) =>
      request("POST", "/api/config/engines/ollama/pull", { model, base_url }),
    getPullStatus: () => request("GET", "/api/config/engines/ollama/pull"),
    listOllamaModels: (base_url) => {
      const qs = base_url ? `?base_url=${encodeURIComponent(base_url)}` : "";
      return request("GET", `/api/config/engines/ollama/models${qs}`);
    },
    listLlamaCppModels: (base_url) => {
      const qs = base_url ? `?base_url=${encodeURIComponent(base_url)}` : "";
      return request("GET", `/api/config/engines/llamacpp/models${qs}`);
    },

    listEngineInstances: () => request("GET", "/api/config/engine-instances"),
    createEngineInstance: (data) => request("POST", "/api/config/engine-instances", data),
    updateEngineInstance: (id, data) =>
      request("PUT", `/api/config/engine-instances/${id}`, data),
    deleteEngineInstance: (id) => request("DELETE", `/api/config/engine-instances/${id}`),
    reorderEngineInstances: (ids) =>
      request("POST", "/api/config/engine-instances/reorder", { ids }),
    testEngineInstance: (id, config = {}) =>
      request("POST", `/api/config/engine-instances/${id}/test`, { config }),

    getLanguageConfig: () => request("GET", "/api/config/languages"),
    setLanguageConfig: (cfg) => request("POST", "/api/config/languages", cfg),
    getAvailableLanguageVariants: () => request("GET", "/api/config/languages/variants"),

    getBazarrConfig: () => request("GET", "/api/config/bazarr"),
    setBazarrConfig: (cfg) => request("POST", "/api/config/bazarr", cfg),
    testBazarr: (cfg) => request("POST", "/api/config/bazarr/test", cfg),

    getScheduleConfig: () => request("GET", "/api/config/schedule"),
    setScheduleConfig: (cfg) => request("POST", "/api/config/schedule", cfg),
    getNextRuns: () => request("GET", "/api/schedule/next-runs"),

    getJobs: () => request("GET", "/api/jobs"),
    runScheduledJobNow: () => request("POST", "/api/jobs/run-now"),
    clearDatabase: () => request("POST", "/api/jobs/clear-database"),
    closeStaleRuns: () => request("POST", "/api/jobs/close-stale-runs"),
    clearEngineRateLimits: () => request("POST", "/api/jobs/clear-engine-rate-limits"),
    syncMedia: () => request("POST", "/api/jobs/sync-media"),
    syncSubs: () => request("POST", "/api/jobs/sync-subs"),
    runLanguageCheck: () => request("POST", "/api/jobs/language-check"),
    getLanguageCheckSettings: () => request("GET", "/api/jobs/language-check/settings"),
    setLanguageCheckSettings: (instanceId) =>
      request("POST", "/api/jobs/language-check/settings", { instance_id: instanceId }),
    pushUploads: () => request("POST", "/api/jobs/push-uploads"),
    runBackup: () => request("POST", "/api/jobs/backup"),
    listBackups: () => request("GET", "/api/jobs/backups"),
    restoreBackup: (filename) => request("POST", "/api/jobs/backups/restore", { filename }),
    getSyncStatus: () => request("GET", "/api/jobs/sync-status"),

    getHistory: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/history${qs ? "?" + qs : ""}`);
    },
    getHistoryRunItems: (runId) => request("GET", `/api/history/${runId}/items`),
    getHistoryEvents: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/history/events${qs ? "?" + qs : ""}`);
    },
    getHistoryStats: (range = "all") => request("GET", `/api/history/stats?range=${range}`),
    getHistoryJobs: (limit = 100) => request("GET", `/api/history/jobs?limit=${limit}`),
    getLanguageMismatches: (limit = 100) => request("GET", `/api/history/language-mismatches?limit=${limit}`),

    searchCompareLibrary: (q, sourceLanguage) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (sourceLanguage) params.set("source_language", sourceLanguage);
      return request("GET", `/api/compare/library?${params.toString()}`);
    },
    refreshCompareLibrary: () => request("POST", "/api/compare/library/refresh"),
    runCompare: (libraryItem, sourceLanguage, targetLanguage, instanceIdA, instanceIdB, parallel, opts = {}) =>
      request("POST", "/api/compare", {
        item_type: libraryItem.item_type, bazarr_id: libraryItem.bazarr_id,
        source_language: sourceLanguage, target_language: targetLanguage,
        instance_id_a: instanceIdA, instance_id_b: instanceIdB, parallel,
        catalan_vegeta_insults_a: opts.catalanVegetaInsultsA ?? null,
        catalan_vegeta_insults_b: opts.catalanVegetaInsultsB ?? null,
        temperature_a: opts.temperatureA ?? null,
        temperature_b: opts.temperatureB ?? null,
      }),
    runCompareUploaded: async (file, sourceLang, targetLang, instanceIdA, instanceIdB, parallel, opts = {}) => {
      const form = new FormData();
      form.append("source_file", file);
      form.append("source_lang", sourceLang);
      form.append("target_lang", targetLang);
      form.append("instance_id_a", instanceIdA);
      if (instanceIdB != null) form.append("instance_id_b", instanceIdB);
      form.append("parallel", parallel ? "true" : "false");
      if (opts.catalanVegetaInsultsA != null) form.append("catalan_vegeta_insults_a", opts.catalanVegetaInsultsA ? "true" : "false");
      if (opts.catalanVegetaInsultsB != null) form.append("catalan_vegeta_insults_b", opts.catalanVegetaInsultsB ? "true" : "false");
      if (opts.temperatureA != null) form.append("temperature_a", String(opts.temperatureA));
      if (opts.temperatureB != null) form.append("temperature_b", String(opts.temperatureB));
      const resp = await fetch("/api/compare/uploaded", { method: "POST", body: form });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) throw new Error((data && data.detail) || resp.statusText);
      return data;
    },
    getCompareLanguages: () => request("GET", "/api/compare/languages"),
    parseReferenceSubtitle: async (file) => {
      const form = new FormData();
      form.append("reference_file", file);
      const resp = await fetch("/api/compare/reference", { method: "POST", body: form });
      const data = await resp.json().catch(() => null);
      if (!resp.ok) throw new Error((data && data.detail) || resp.statusText);
      return data;
    },
  };
})();

// Sidebar Bazarr connection indicator, shared across every page.
(async function initSidebarStatus() {
  const el = document.getElementById("sidebar-bazarr-status");
  if (!el) return;
  const label = el.querySelector("span:last-child");
  try {
    const result = await Api.testBazarr();
    if (result.ok) {
      label.className = "mono ok";
      label.textContent = "● connected";
    } else {
      label.className = "mono bad";
      label.textContent = "● unreachable";
    }
  } catch (_) {
    label.className = "mono bad";
    label.textContent = "● not configured";
  }
})();

// Mobile nav toggle — sidebar collapses to a top bar below 980px, this
// expands/collapses the nav links in place instead of leaving them
// permanently hidden with no way to reach other pages.
(function initNavToggle() {
  const toggle = document.getElementById("nav-toggle");
  const sidebar = document.getElementById("sidebar");
  if (!toggle || !sidebar) return;
  toggle.addEventListener("click", () => {
    const open = sidebar.classList.toggle("nav-open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();

// Desktop sidebar collapse — shrinks to an icon-only rail rather than
// hiding navigation entirely, so the shell's grid column (see base.css's
// .shell.sidebar-collapsed) resizes the main content smoothly instead of
// jumping. Persisted so the choice survives navigating between pages —
// each page load is a fresh document, not an SPA route change, so
// without persistence the sidebar would silently reset to expanded on
// every click through the app. Applied ASAP (see the inline script in
// base.html's <head>, before this file loads) to avoid a flash of the
// expanded sidebar on load.
(function initCollapseToggle() {
  const toggle = document.getElementById("collapse-toggle");
  const sidebar = document.getElementById("sidebar");
  const shell = document.getElementById("shell");
  if (!toggle || !sidebar || !shell) return;
  // Enabled after this script runs (post-first-paint), not in the initial
  // inline <script> in base.html's <head> — see base.css's comment on
  // .transitions-enabled for why.
  shell.classList.add("transitions-enabled");
  toggle.addEventListener("click", () => {
    const collapsed = !sidebar.classList.contains("collapsed");
    sidebar.classList.toggle("collapsed", collapsed);
    shell.classList.toggle("sidebar-collapsed", collapsed);
    toggle.textContent = collapsed ? "▸" : "◂";
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0");
  });
})();
