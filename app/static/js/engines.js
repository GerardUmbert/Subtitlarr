const { createApp } = Vue;

// One entry per provider_type — drives both the "add new instance" menu
// and each card's config form generically, instead of duplicating markup
// per provider like the old single-active-engine page did. Field hints
// carry over the same tuning guidance that page had (real benchmarked
// numbers, confirmed-live failure modes) since that content is still
// exactly as true per-instance as it was globally.
const PROVIDER_TYPES = {
  gemini: {
    label: "Gemini",
    badges: ["free tier", "recommended"],
    fields: [
      { key: "model", label: "Model", placeholder: "gemini-3.5-flash-lite", hint:
        "Not every Gemini model name actually has free-tier quota on your account — confirmed live: gemini-2.0-flash (an old default) had 0 RPM/TPM/RPD, every request 429'd instantly. Check aistudio.google.com/rate-limit for your account's real per-model numbers. gemini-3.5-flash-lite (the default) had the best confirmed free-tier quota at time of writing: 15 RPM, 250K TPM, 500 RPD." },
      { key: "api_key", label: "API key", type: "password", secret: true, hint:
        "Get a free key at aistudio.google.com/apikey — no billing setup required for the free tier." },
      { key: "batch_token_budget", label: "Batch size (dialogue tokens)", type: "number", min: 1, step: 100, hint:
        "A real-quota model has 250K TPM — enormous headroom, so this can stay high. Lower it only if translations come back with a low \"recovered N/M cues\" count, not in response to 429s." },
      { key: "concurrent_batch_window", label: "Concurrent batches", type: "number", min: 1, step: 1, hint:
        "How many batches are sent at once before waiting for that group to finish. Confirmed live at 15 RPM for the recommended model — 3 keeps comfortable headroom." },
    ],
  },
  ollama: {
    label: "Ollama",
    badges: ["local"],
    fields: [
      { key: "base_url", label: "Base URL", placeholder: "http://ollama:11434" },
      { key: "model", label: "Model", placeholder: "gemma3:4b", ollamaModelPicker: true },
      { key: "num_ctx", label: "Context window (tokens)", type: "numCtxSelect", hint:
        "Ollama defaults to 4096 regardless of what the model supports — raise this if translations of longer or denser files come back incomplete. Higher values use more RAM." },
      { key: "batch_token_budget", label: "Batch size override (dialogue tokens)", type: "number", min: 0, step: 100, hint:
        "0 = auto (derives from context window above). 1 = one cue per request, most reliable but slowest. 400-900 is the sweet spot; 900 has been seen to drop into garbled output on some content, 400 has not." },
    ],
  },
  llamacpp: {
    label: "llama.cpp",
    badges: ["local"],
    fields: [
      { key: "base_url", label: "Base URL", placeholder: "http://localhost:8080", hint:
        "llama.cpp's own built-in local HTTP server — a separate local runtime from Ollama, not another name for it. Start/stop and model selection happen on the llama.cpp server itself." },
      { key: "model", label: "Model name (optional)", placeholder: "gemma3:4b", hint:
        "Most llama.cpp servers ignore this — only one model is ever loaded. Some builds/reverse proxies reject a request with no model field at all — set this to whatever /v1/models reports if you hit a \"model name is missing\" error." },
      { key: "api_key", label: "API key", type: "password", secret: true, optional: true, hint:
        "llama.cpp's own server has no built-in auth — only needed if the Base URL points at an instance behind a reverse proxy/gateway enforcing its own auth. Sent as Authorization: Bearer." },
      { key: "batch_token_budget", label: "Batch size override (dialogue tokens)", type: "number", min: 0, step: 100 },
    ],
  },
  nvidia: {
    label: "NVIDIA (DeepSeek V4 Flash)",
    badges: ["free tier"],
    fields: [
      { key: "model", label: "Model", placeholder: "deepseek-ai/deepseek-v4-flash", hint:
        "Must be a real instructable chat model. NVIDIA also hosts translation-only models (e.g. Riva Translate) which are not compatible here." },
      { key: "api_key", label: "API key", type: "password", secret: true, hint: "Get a free key at build.nvidia.com." },
      { key: "batch_token_budget", label: "Batch size (dialogue tokens)", type: "number", min: 1, step: 100, hint:
        "700 (default) is confirmed reliable — same per-cue speed as 400, at roughly half the request count — while 900 has reproducibly failed on real content." },
      { key: "concurrent_batch_window", label: "Concurrent batches", type: "number", min: 1, step: 1, hint:
        "NVIDIA's free tier allows up to 40 requests/minute; a small window naturally stays well under that." },
    ],
  },
  openrouter: {
    label: "OpenRouter",
    badges: ["API key"],
    fields: [
      { key: "model", label: "Model", placeholder: "google/gemma-4-26b-a4b-it:free", hint:
        "OpenRouter routes to many underlying providers under one API — see openrouter.ai/models for the full lineup." },
      { key: "api_key", label: "API key", type: "password", secret: true, hint: "Get a key at openrouter.ai/keys." },
      { key: "batch_token_budget", label: "Batch size (dialogue tokens)", type: "number", min: 1, step: 100, hint:
        "Free \":free\" models are capped at 50 requests/DAY on top of 20/minute — keep this high so one file doesn't burn a large chunk of the day's quota." },
      { key: "concurrent_batch_window", label: "Concurrent batches", type: "number", min: 1, step: 1 },
    ],
  },
  groq: {
    label: "Groq",
    badges: ["free tier"],
    fields: [
      { key: "model", label: "Model", placeholder: "llama-3.1-8b-instant", hint:
        "Groq serves a fixed lineup on its own LPU hardware — see console.groq.com/docs/models." },
      { key: "api_key", label: "API key", type: "password", secret: true, hint: "Get a free key at console.groq.com/keys." },
      { key: "batch_token_budget", label: "Batch size (dialogue tokens)", type: "number", min: 1, step: 100, hint:
        "Groq also enforces a 6000 TPM cap for this model on top of request-count limits — 1800 keeps the full round-trip safely under that ceiling." },
      { key: "concurrent_batch_window", label: "Concurrent batches", type: "number", min: 1, step: 1, hint:
        "Defaults to 1 (sequential) — the TPM cap is a single rolling budget shared across every concurrent request." },
    ],
  },
};

const NUM_CTX_OPTIONS = [4096, 8192, 16384, 32768, 65536, 131072, 262144];

function formatNumCtx(value) {
  return value >= 1024 ? `${value / 1024}k` : String(value);
}

createApp({
  data() {
    return {
      pageLoading: true,
      instances: [],
      providerTypes: PROVIDER_TYPES,
      numCtxOptions: NUM_CTX_OPTIONS,
      expandedId: null,
      editBuffers: {}, // instance_id -> {name, config, enabled}
      testing: null,
      testResults: {},
      saving: null,
      saved: null,
      addMenuOpen: false,
      pull: { active: false, status: "", completed: 0, total: 0, pct: 0, done: false, error: null },
      _pullPollHandle: null,
      ollamaModels: {}, // instance_id -> models[]
      ollamaModelsError: {},
      loadingOllamaModels: {},
      draggingId: null,
      // Cards are only draggable while the mouse is actually down on the
      // ⠿ handle — the native draggable="true" attribute has no built-in
      // "only from this child" restriction, and applying it to the whole
      // card made selecting text inside any input/textarea (e.g. an API
      // key field) get hijacked as a card-drag instead. Toggled on the
      // handle's mousedown, cleared on dragend/drop.
      dragEnabledId: null,
    };
  },
  methods: {
    formatNumCtx,
    typeInfo(providerType) {
      return this.providerTypes[providerType] || { label: providerType, badges: [], fields: [] };
    },
    isSeparator(instance) {
      return instance.provider_type === "separator";
    },
    async load() {
      const result = await Api.listEngineInstances();
      this.instances = result.data;
      this.editBuffers = {};
      for (const inst of this.instances) {
        this.editBuffers[inst.id] = {
          name: inst.name,
          enabled: inst.enabled,
          config: { ...inst.config },
        };
      }
    },
    toggleExpand(instance) {
      this.expandedId = this.expandedId === instance.id ? null : instance.id;
      if (instance.provider_type === "ollama" && !this.ollamaModels[instance.id]) {
        this.refreshOllamaModels(instance);
      }
    },
    async refreshOllamaModels(instance) {
      const baseUrl = this.editBuffers[instance.id].config.base_url;
      if (!baseUrl) return;
      this.loadingOllamaModels = { ...this.loadingOllamaModels, [instance.id]: true };
      this.ollamaModelsError = { ...this.ollamaModelsError, [instance.id]: null };
      try {
        const result = await Api.listOllamaModels(baseUrl);
        this.ollamaModels = { ...this.ollamaModels, [instance.id]: result.models };
      } catch (err) {
        this.ollamaModels = { ...this.ollamaModels, [instance.id]: [] };
        this.ollamaModelsError = { ...this.ollamaModelsError, [instance.id]: err.message };
      } finally {
        this.loadingOllamaModels = { ...this.loadingOllamaModels, [instance.id]: false };
      }
    },
    async testInstance(instance) {
      this.testing = instance.id;
      try {
        const buffer = this.editBuffers[instance.id];
        const config = {};
        for (const [key, value] of Object.entries(buffer.config)) {
          if (value !== null && value !== "") config[key] = value;
        }
        const result = await Api.testEngineInstance(instance.id, config);
        this.testResults = { ...this.testResults, [instance.id]: result };
      } catch (err) {
        this.testResults = { ...this.testResults, [instance.id]: { ok: false, detail: err.message } };
      } finally {
        this.testing = null;
      }
    },
    async saveInstance(instance) {
      this.saving = instance.id;
      try {
        const buffer = this.editBuffers[instance.id];
        const updated = await Api.updateEngineInstance(instance.id, {
          name: buffer.name,
          enabled: buffer.enabled,
          config: buffer.config,
        });
        await this.load();
        this.saved = instance.id;
        setTimeout(() => {
          if (this.saved === instance.id) this.saved = null;
        }, 3000);
      } catch (err) {
        alert(`Could not save: ${err.message}`);
      } finally {
        this.saving = null;
      }
    },
    async toggleEnabled(instance) {
      const buffer = this.editBuffers[instance.id];
      buffer.enabled = !buffer.enabled;
      await this.saveInstance(instance);
    },
    async deleteInstance(instance) {
      const label = this.isSeparator(instance) ? "this separator" : instance.name;
      if (!confirm(`Remove ${label} from the cascade?`)) return;
      await Api.deleteEngineInstance(instance.id);
      await this.load();
    },
    async startAdd(providerType) {
      this.addMenuOpen = false;
      const typeInfo = this.typeInfo(providerType);
      const created = await Api.createEngineInstance({
        name: typeInfo.label,
        provider_type: providerType,
        config: {},
      });
      await this.load();
      // Open the new card right away so its (blank) API key/model fields
      // are immediately visible instead of leaving the user to hunt for
      // which of the now-N cards is the one they just added.
      this.expandedId = created.id;
    },
    async addSeparator() {
      this.addMenuOpen = false;
      await Api.createEngineInstance({
        name: "— stop cascade here —",
        provider_type: "separator",
        config: {},
      });
      await this.load();
    },
    dragStart(instance) {
      this.draggingId = instance.id;
    },
    dragOver(instance, event) {
      event.preventDefault();
    },
    async dropOn(targetInstance) {
      if (this.draggingId === null || this.draggingId === targetInstance.id) return;
      const ids = this.instances.map((i) => i.id);
      const fromIndex = ids.indexOf(this.draggingId);
      const toIndex = ids.indexOf(targetInstance.id);
      ids.splice(fromIndex, 1);
      ids.splice(toIndex, 0, this.draggingId);
      this.draggingId = null;
      this.dragEnabledId = null;
      // Optimistic reorder so the list doesn't visibly snap back while the
      // request is in flight.
      const byId = Object.fromEntries(this.instances.map((i) => [i.id, i]));
      this.instances = ids.map((id) => byId[id]);
      await Api.reorderEngineInstances(ids);
      await this.load();
    },
    async pullModel(instance) {
      try {
        const buffer = this.editBuffers[instance.id];
        const result = await Api.pullOllamaModel(buffer.config.model, buffer.config.base_url);
        if (!result.started) {
          alert(result.reason || "Could not start pull");
          return;
        }
        this.pull = { active: true, status: "starting", completed: 0, total: 0, pct: 0, done: false, error: null };
        this.pollPullStatus(instance);
      } catch (err) {
        alert(`Could not start pull: ${err.message}`);
      }
    },
    pollPullStatus(instance) {
      if (this._pullPollHandle) clearTimeout(this._pullPollHandle);
      this._pullPollHandle = setTimeout(async () => {
        try {
          this.pull = await Api.getPullStatus();
        } catch (_) {
          // ignore transient errors, keep polling
        }
        if (this.pull.active) {
          this.pollPullStatus(instance);
        } else if (this.pull.done) {
          await this.refreshOllamaModels(instance);
        }
      }, 1500);
    },
  },
  async mounted() {
    await this.load();
    try {
      const status = await Api.getPullStatus();
      if (status.active) this.pull = status;
    } catch (_) {
      // no pull in progress / endpoint not reachable yet
    }
    this.pageLoading = false;
    // Safety net: a mousedown on the handle followed by a plain click
    // (release without ever dragging) never fires dragend, which would
    // otherwise leave dragEnabledId stuck pointing at that card forever.
    this._onGlobalMouseUp = () => { this.dragEnabledId = null; };
    document.addEventListener("mouseup", this._onGlobalMouseUp);
  },
  unmounted() {
    if (this._pullPollHandle) clearTimeout(this._pullPollHandle);
    document.removeEventListener("mouseup", this._onGlobalMouseUp);
  },
}).mount("#engines-app");
