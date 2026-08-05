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
    runNow: () => request("POST", "/api/run/now"),
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
    getQueue: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/queue${qs ? "?" + qs : ""}`);
    },
    getCurrentRunItems: () => request("GET", "/api/queue/current-run"),

    getEngineConfig: () => request("GET", "/api/config/engines"),
    setEngineConfig: (cfg) => request("POST", "/api/config/engines", cfg),
    testEngine: (name, cfg) => request("POST", `/api/config/engines/${name}/test`, cfg),
    pullOllamaModel: (model, base_url) =>
      request("POST", "/api/config/engines/ollama/pull", { model, base_url }),
    getPullStatus: () => request("GET", "/api/config/engines/ollama/pull"),

    getLanguageConfig: () => request("GET", "/api/config/languages"),
    setLanguageConfig: (cfg) => request("POST", "/api/config/languages", cfg),

    getBazarrConfig: () => request("GET", "/api/config/bazarr"),
    setBazarrConfig: (cfg) => request("POST", "/api/config/bazarr", cfg),
    testBazarr: (cfg) => request("POST", "/api/config/bazarr/test", cfg),

    getScheduleConfig: () => request("GET", "/api/config/schedule"),
    setScheduleConfig: (cfg) => request("POST", "/api/config/schedule", cfg),
    getNextRuns: () => request("GET", "/api/schedule/next-runs"),

    getJobs: () => request("GET", "/api/jobs"),
    runScheduledJobNow: () => request("POST", "/api/jobs/run-now"),
    clearDatabase: () => request("POST", "/api/jobs/clear-database"),
    syncMedia: () => request("POST", "/api/jobs/sync-media"),
    syncSubs: () => request("POST", "/api/jobs/sync-subs"),
    pushUploads: () => request("POST", "/api/jobs/push-uploads"),
    getSyncStatus: () => request("GET", "/api/jobs/sync-status"),

    getHistory: (params = {}) => {
      const qs = new URLSearchParams(params).toString();
      return request("GET", `/api/history${qs ? "?" + qs : ""}`);
    },
    getHistoryRunItems: (runId) => request("GET", `/api/history/${runId}/items`),
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
