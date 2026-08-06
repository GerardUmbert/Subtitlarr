const { createApp } = Vue;

createApp({
  data() {
    return {
      pageLoading: true,
      active: "ollama",
      fallback: "",
      ollamaModel: "",
      ollamaBaseUrl: "",
      ollamaNumCtx: 8192,
      ollamaBatchTokenBudget: 0,
      // Standard power-of-2 context sizes, matching the convention used by
      // Ollama's own UI and most model docs — not an enforced hard limit,
      // but covers every value anyone would realistically want and avoids
      // invalid/oddball entries (e.g. a value the model doesn't support).
      numCtxOptions: [4096, 8192, 16384, 32768, 65536, 131072, 262144],
      llamacppBaseUrl: "",
      llamacppModel: "",
      llamacppApiKey: "",
      llamacppKeyMasked: "",
      llamacppHasKey: false,
      llamacppBatchTokenBudget: 400,
      geminiModel: "",
      geminiApiKey: "",
      geminiKeyMasked: "",
      geminiHasKey: false,
      geminiBatchTokenBudget: 1800,
      geminiConcurrentBatchWindow: 1,
      nvidiaModel: "",
      nvidiaApiKey: "",
      nvidiaKeyMasked: "",
      nvidiaHasKey: false,
      nvidiaBatchTokenBudget: 2000,
      nvidiaConcurrentBatchWindow: 4,
      openrouterModel: "",
      openrouterApiKey: "",
      openrouterKeyMasked: "",
      openrouterHasKey: false,
      openrouterBatchTokenBudget: 4000,
      openrouterConcurrentBatchWindow: 4,
      groqModel: "",
      groqApiKey: "",
      groqKeyMasked: "",
      groqHasKey: false,
      groqBatchTokenBudget: 1800,
      groqConcurrentBatchWindow: 1,
      testing: null,
      testResults: {},
      saving: false,
      saved: false,
      pull: { active: false, status: "", completed: 0, total: 0, pct: 0, done: false, error: null },
      _pullPollHandle: null,
      ollamaModels: [],
      ollamaModelsError: null,
      loadingOllamaModels: false,
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
    formatNumCtx(value) {
      return value >= 1024 ? `${value / 1024}k` : String(value);
    },
    async load() {
      const cfg = await Api.getEngineConfig();
      this.active = cfg.active_engine;
      this.fallback = cfg.fallback_engine;
      this.ollamaModel = cfg.ollama_model;
      this.ollamaBaseUrl = cfg.ollama_base_url;
      // If a saved value isn't one of the standard dropdown options (e.g.
      // set via an older free-text input, or a custom env var), add it so
      // the dropdown still shows the actual current value rather than
      // silently falling back to nothing selected.
      if (!this.numCtxOptions.includes(cfg.ollama_num_ctx)) {
        this.numCtxOptions = [...this.numCtxOptions, cfg.ollama_num_ctx].sort((a, b) => a - b);
      }
      this.ollamaNumCtx = cfg.ollama_num_ctx;
      this.ollamaBatchTokenBudget = cfg.ollama_batch_token_budget;
      this.llamacppBaseUrl = cfg.llamacpp_base_url;
      this.llamacppModel = cfg.llamacpp_model;
      this.llamacppKeyMasked = cfg.llamacpp_api_key_masked;
      this.llamacppHasKey = cfg.llamacpp_has_key;
      this.llamacppBatchTokenBudget = cfg.llamacpp_batch_token_budget;
      this.geminiModel = cfg.gemini_model;
      this.geminiKeyMasked = cfg.gemini_api_key_masked;
      this.geminiHasKey = cfg.gemini_has_key;
      this.geminiBatchTokenBudget = cfg.gemini_batch_token_budget;
      this.geminiConcurrentBatchWindow = cfg.gemini_concurrent_batch_window;
      this.nvidiaModel = cfg.nvidia_model;
      this.nvidiaKeyMasked = cfg.nvidia_api_key_masked;
      this.nvidiaHasKey = cfg.nvidia_has_key;
      this.nvidiaBatchTokenBudget = cfg.nvidia_batch_token_budget;
      this.nvidiaConcurrentBatchWindow = cfg.nvidia_concurrent_batch_window;
      this.openrouterModel = cfg.openrouter_model;
      this.openrouterKeyMasked = cfg.openrouter_api_key_masked;
      this.openrouterHasKey = cfg.openrouter_has_key;
      this.openrouterBatchTokenBudget = cfg.openrouter_batch_token_budget;
      this.openrouterConcurrentBatchWindow = cfg.openrouter_concurrent_batch_window;
      this.groqModel = cfg.groq_model;
      this.groqKeyMasked = cfg.groq_api_key_masked;
      this.groqHasKey = cfg.groq_has_key;
      this.groqBatchTokenBudget = cfg.groq_batch_token_budget;
      this.groqConcurrentBatchWindow = cfg.groq_concurrent_batch_window;
    },
    async refreshOllamaModels() {
      this.loadingOllamaModels = true;
      this.ollamaModelsError = null;
      try {
        const result = await Api.listOllamaModels(this.ollamaBaseUrl);
        this.ollamaModels = result.models;
      } catch (err) {
        this.ollamaModels = [];
        this.ollamaModelsError = err.message;
      } finally {
        this.loadingOllamaModels = false;
      }
    },
    async testEngine(name) {
      this.testing = name;
      try {
        let cfg;
        if (name === "ollama") {
          cfg = { base_url: this.ollamaBaseUrl, model: this.ollamaModel };
        } else if (name === "llamacpp") {
          cfg = { base_url: this.llamacppBaseUrl, model: this.llamacppModel || null, api_key: this.llamacppApiKey || null };
        } else if (name === "gemini") {
          cfg = { model: this.geminiModel, api_key: this.geminiApiKey || null };
        } else if (name === "nvidia") {
          cfg = { model: this.nvidiaModel, api_key: this.nvidiaApiKey || null };
        } else if (name === "openrouter") {
          cfg = { model: this.openrouterModel, api_key: this.openrouterApiKey || null };
        } else {
          cfg = { model: this.groqModel, api_key: this.groqApiKey || null };
        }
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
          llamacpp_base_url: this.llamacppBaseUrl,
          llamacpp_model: this.llamacppModel,
          llamacpp_api_key: this.llamacppApiKey || null,
          llamacpp_batch_token_budget: this.llamacppBatchTokenBudget,
          gemini_model: this.geminiModel,
          gemini_api_key: this.geminiApiKey || null,
          gemini_batch_token_budget: this.geminiBatchTokenBudget,
          gemini_concurrent_batch_window: this.geminiConcurrentBatchWindow,
          nvidia_model: this.nvidiaModel,
          nvidia_api_key: this.nvidiaApiKey || null,
          nvidia_batch_token_budget: this.nvidiaBatchTokenBudget,
          nvidia_concurrent_batch_window: this.nvidiaConcurrentBatchWindow,
          openrouter_model: this.openrouterModel,
          openrouter_api_key: this.openrouterApiKey || null,
          openrouter_batch_token_budget: this.openrouterBatchTokenBudget,
          openrouter_concurrent_batch_window: this.openrouterConcurrentBatchWindow,
          groq_model: this.groqModel,
          groq_api_key: this.groqApiKey || null,
          groq_batch_token_budget: this.groqBatchTokenBudget,
          groq_concurrent_batch_window: this.groqConcurrentBatchWindow,
        });
        this.llamacppApiKey = "";
        this.geminiApiKey = "";
        this.nvidiaApiKey = "";
        this.openrouterApiKey = "";
        this.groqApiKey = "";
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
          await this.refreshOllamaModels();
        }
      }, 1500);
    },
  },
  async mounted() {
    await this.load();
    this.refreshOllamaModels();
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
    this.pageLoading = false;
  },
  unmounted() {
    if (this._pullPollHandle) clearTimeout(this._pullPollHandle);
  },
}).mount("#engines-app");
