const { createApp } = Vue;

// Shared across every provider type — subtitle translation wants literal,
// consistent output that reliably follows the rigid index/format
// instructions, not the creative variation a higher temperature invites.
// Every provider defaults to ~1.0 (tuned for chat/creative use) when this
// isn't set at all, which is why Subtitlarr ships its own lower default
// (see registry.DEFAULT_TEMPERATURE) instead of leaving it unset.
const TEMPERATURE_FIELD = {
  key: "temperature", label: "Temperature", type: "number", min: 0, max: 2, step: 0.1,
  hint: "Lower = more literal/consistent, higher = more varied/creative. Most providers default to ~1.0 (tuned for chat) if left unset, which invites more format drift and stylistic variation than a rigid subtitle-translation task wants — 0.2 (default here) trades away creative flexibility for reliability.",
};

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
      TEMPERATURE_FIELD,
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
      TEMPERATURE_FIELD,
    ],
  },
  llamacpp: {
    label: "llama.cpp",
    badges: ["local"],
    fields: [
      { key: "base_url", label: "Base URL", placeholder: "http://localhost:8080", hint:
        "llama.cpp's own built-in local HTTP server — a separate local runtime from Ollama, not another name for it. Start/stop and model selection happen on the llama.cpp server itself." },
      { key: "model", label: "Model name (optional)", placeholder: "gemma3:4b", llamacppModelPicker: true, hint:
        "Most llama.cpp servers ignore this — only one model is ever loaded. Some builds/reverse proxies reject a request with no model field at all — set this to whatever /v1/models reports if you hit a \"model name is missing\" error." },
      { key: "api_key", label: "API key", type: "password", secret: true, optional: true, hint:
        "llama.cpp's own server has no built-in auth — only needed if the Base URL points at an instance behind a reverse proxy/gateway enforcing its own auth. Sent as Authorization: Bearer." },
      { key: "batch_token_budget", label: "Batch size override (dialogue tokens)", type: "number", min: 0, step: 100 },
      TEMPERATURE_FIELD,
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
      TEMPERATURE_FIELD,
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
      TEMPERATURE_FIELD,
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
      TEMPERATURE_FIELD,
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
      llamacppModels: {}, // instance_id -> models[]
      llamacppModelsError: {},
      loadingLlamacppModels: {},
      customModelEntry: {}, // instance_id -> bool, toggles select vs free-text input
      // Reorder is pointer-events-based (not native HTML5 drag-and-drop)
      // so it works on touch/mobile too — native HTML5 DnD has no touch
      // equivalent at all, confirmed live as the reason dragging didn't
      // work in the mobile PWA. draggingId is set for the duration of a
      // drag; dragOffsetY is the pointer's live Y position minus the
      // handle's Y at pointerdown, used to translateY the dragged card so
      // it visually follows the pointer/finger.
      draggingId: null,
      dragOffsetY: 0,
      _dragStartClientY: 0,
      _dragCardHeight: 0,
    };
  },
  methods: {
    formatNumCtx,
    // The number input's min/max attrs are only a soft browser hint — a
    // user can still type/scroll past them. Confirmed live: temperature=3
    // was submittable and Gemini rejected it server-side with "must be in
    // the range [0.0, 2.0]" — this clamps on blur so an out-of-range
    // value never leaves the field in the first place, on top of the
    // server-side check in app/api/engine_instances.py.
    clampNumberField(instanceId, field) {
      if (field.min === undefined && field.max === undefined) return;
      const buffer = this.editBuffers[instanceId];
      const value = buffer.config[field.key];
      if (value === null || value === undefined || value === "" || Number.isNaN(value)) return;
      let clamped = value;
      if (field.min !== undefined) clamped = Math.max(field.min, clamped);
      if (field.max !== undefined) clamped = Math.min(field.max, clamped);
      if (clamped !== value) buffer.config[field.key] = clamped;
    },
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
      if (instance.provider_type === "llamacpp" && !this.llamacppModels[instance.id]) {
        this.refreshLlamaCppModels(instance);
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
    async refreshLlamaCppModels(instance) {
      const baseUrl = this.editBuffers[instance.id].config.base_url;
      if (!baseUrl) return;
      this.loadingLlamacppModels = { ...this.loadingLlamacppModels, [instance.id]: true };
      this.llamacppModelsError = { ...this.llamacppModelsError, [instance.id]: null };
      try {
        const result = await Api.listLlamaCppModels(baseUrl);
        this.llamacppModels = { ...this.llamacppModels, [instance.id]: result.models };
      } catch (err) {
        this.llamacppModels = { ...this.llamacppModels, [instance.id]: [] };
        this.llamacppModelsError = { ...this.llamacppModelsError, [instance.id]: err.message };
      } finally {
        this.loadingLlamacppModels = { ...this.loadingLlamacppModels, [instance.id]: false };
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
    // Pointer Events fire for mouse, touch, AND pen uniformly — one
    // implementation covers desktop drag AND mobile/PWA drag, unlike the
    // old native HTML5 draggable="true" approach (mouse-only, no touch
    // equivalent exists at all).
    dragPointerDown(instance, event) {
      // Only the primary pointer/button starts a drag (a multi-touch
      // second finger, or a right-click, must not hijack the gesture).
      if (event.button !== undefined && event.button !== 0) return;
      const card = event.currentTarget.closest(".engine-card");
      this.draggingId = instance.id;
      this.dragOffsetY = 0;
      this._dragStartClientY = event.clientY;
      this._dragCardHeight = card ? card.getBoundingClientRect().height : 0;
      // Capturing the pointer on the handle itself means move/up events
      // keep firing on it even once the finger/cursor has moved over a
      // DIFFERENT card underneath — without this, a fast drag over
      // another element interrupts tracking.
      event.currentTarget.setPointerCapture(event.pointerId);
      document.addEventListener("pointermove", this._onDragPointerMove);
      document.addEventListener("pointerup", this._onDragPointerUp, { once: true });
      // Prevents the page from scrolling while dragging a card on touch
      // (the default touch-action would otherwise treat a vertical drag
      // as a scroll gesture instead).
      event.preventDefault();
    },
    _dragPointerMove(event) {
      if (this.draggingId === null) return;
      this.dragOffsetY = event.clientY - this._dragStartClientY;
      // Live reorder: as soon as the dragged card's CENTER has crossed
      // into a neighboring card's slot, splice it into that position
      // immediately (not just on drop) — combined with the CSS
      // transition on .engine-card's position, this is what produces the
      // "other cards jump out of the way" animation, so it's always
      // visually obvious whether the card is about to land above or
      // below its neighbor.
      const ids = this.instances.map((i) => i.id);
      const fromIndex = ids.indexOf(this.draggingId);
      if (fromIndex === -1) return;
      const movedBy = this.dragOffsetY;
      const slots = Math.round(movedBy / Math.max(1, this._dragCardHeight));
      const toIndex = Math.min(ids.length - 1, Math.max(0, fromIndex + slots));
      if (toIndex !== fromIndex) {
        ids.splice(fromIndex, 1);
        ids.splice(toIndex, 0, this.draggingId);
        const byId = Object.fromEntries(this.instances.map((i) => [i.id, i]));
        this.instances = ids.map((id) => byId[id]);
        // The dragged card's OWN base position just moved by (toIndex -
        // fromIndex) slots — subtract that back out of the pointer
        // offset so the card stays glued to the actual pointer position
        // instead of jumping an extra slot's worth on top of the reflow.
        this._dragStartClientY += (toIndex - fromIndex) * this._dragCardHeight;
        this.dragOffsetY = event.clientY - this._dragStartClientY;
      }
    },
    async _dragPointerUp() {
      document.removeEventListener("pointermove", this._onDragPointerMove);
      if (this.draggingId === null) return;
      this.draggingId = null;
      this.dragOffsetY = 0;
      const ids = this.instances.map((i) => i.id);
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
    // Stable bound references so addEventListener/removeEventListener
    // target the exact same function — needed since these are attached
    // dynamically per-drag (dragPointerDown) rather than once here.
    this._onDragPointerMove = this._dragPointerMove.bind(this);
    this._onDragPointerUp = this._dragPointerUp.bind(this);
  },
  unmounted() {
    if (this._pullPollHandle) clearTimeout(this._pullPollHandle);
    document.removeEventListener("pointermove", this._onDragPointerMove);
    document.removeEventListener("pointerup", this._onDragPointerUp);
  },
}).mount("#engines-app");
