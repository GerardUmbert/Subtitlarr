const { createApp } = Vue;

createApp({
  data() {
    return {
      active: "ollama",
      fallback: "",
      ollamaModel: "",
      ollamaBaseUrl: "",
      ollamaNumCtx: 8192,
      ollamaBatchTokenBudget: 0,
      geminiModel: "",
      geminiApiKey: "",
      geminiKeyMasked: "",
      geminiHasKey: false,
      testing: null,
      testResults: {},
      saving: false,
      saved: false,
      pull: { active: false, status: "", completed: 0, total: 0, pct: 0, done: false, error: null },
      _pullPollHandle: null,
    };
  },
  computed: {
    ollamaStatus() {
      const result = this.testResults.ollama;
      if (!result) return { label: "unknown", cls: "off" };
      return result.ok ? { label: "ready", cls: "" } : { label: "error", cls: "error" };
    },
  },
  methods: {
    async load() {
      const cfg = await Api.getEngineConfig();
      this.active = cfg.active_engine;
      this.fallback = cfg.fallback_engine;
      this.ollamaModel = cfg.ollama_model;
      this.ollamaBaseUrl = cfg.ollama_base_url;
      this.ollamaNumCtx = cfg.ollama_num_ctx;
      this.ollamaBatchTokenBudget = cfg.ollama_batch_token_budget;
      this.geminiModel = cfg.gemini_model;
      this.geminiKeyMasked = cfg.gemini_api_key_masked;
      this.geminiHasKey = cfg.gemini_has_key;
    },
    async testEngine(name) {
      this.testing = name;
      try {
        const cfg =
          name === "ollama"
            ? { base_url: this.ollamaBaseUrl, model: this.ollamaModel }
            : { model: this.geminiModel, api_key: this.geminiApiKey || null };
        const result = await Api.testEngine(name, cfg);
        this.testResults = { ...this.testResults, [name]: result };
      } catch (err) {
        this.testResults = { ...this.testResults, [name]: { ok: false, detail: err.message } };
      } finally {
        this.testing = null;
      }
    },
    async save() {
      this.saving = true;
      this.saved = false;
      try {
        await Api.setEngineConfig({
          active_engine: this.active,
          fallback_engine: this.fallback,
          ollama_base_url: this.ollamaBaseUrl,
          ollama_model: this.ollamaModel,
          ollama_num_ctx: this.ollamaNumCtx,
          ollama_batch_token_budget: this.ollamaBatchTokenBudget,
          gemini_model: this.geminiModel,
          gemini_api_key: this.geminiApiKey || null,
        });
        this.geminiApiKey = "";
        await this.load();
        this.saved = true;
        setTimeout(() => (this.saved = false), 3000);
      } catch (err) {
        alert(`Could not save: ${err.message}`);
      } finally {
        this.saving = false;
      }
    },
    async pullModel() {
      try {
        // Pulls whatever is currently in the form directly — does not
        // depend on Save having been clicked first.
        const result = await Api.pullOllamaModel(this.ollamaModel, this.ollamaBaseUrl);
        if (!result.started) {
          alert(result.reason || "Could not start pull");
          return;
        }
        this.pull = { active: true, status: "starting", completed: 0, total: 0, pct: 0, done: false, error: null };
        this.pollPullStatus();
      } catch (err) {
        alert(`Could not start pull: ${err.message}`);
      }
    },
    pollPullStatus() {
      if (this._pullPollHandle) clearTimeout(this._pullPollHandle);
      this._pullPollHandle = setTimeout(async () => {
        try {
          this.pull = await Api.getPullStatus();
        } catch (_) {
          // ignore transient errors, keep polling
        }
        if (this.pull.active) {
          this.pollPullStatus();
        } else if (this.pull.done) {
          await this.testEngine("ollama");
        }
      }, 1500);
    },
  },
  async mounted() {
    await this.load();
    // pick up an in-progress pull if the page was reloaded mid-download
    try {
      const status = await Api.getPullStatus();
      if (status.active) {
        this.pull = status;
        this.pollPullStatus();
      }
    } catch (_) {
      // no pull in progress / endpoint not reachable yet
    }
  },
  unmounted() {
    if (this._pullPollHandle) clearTimeout(this._pullPollHandle);
  },
}).mount("#engines-app");
