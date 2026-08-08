const { createApp } = Vue;

// Minimal word-level diff (LCS-based) — no vendored diff library exists in
// this project yet, and this is the only page that needs one, so a small
// self-contained implementation is enough rather than pulling in a
// dependency for one page.
//
// Both sides here are independent translations of the same source, NOT a
// before/after edit of one document — there's no sense in which engine
// A's wording was "removed" in favor of engine B's "added" wording, both
// are just different valid choices. So every word that doesn't line up
// between the two is labeled the same neutral "diff" on BOTH sides
// (highlighted, not colored as a subtraction/addition pair) — only words
// that appear on ONE side with nothing to match on the other (a genuine
// length difference) get no highlight at all, since there's nothing to
// contrast them against.
function wordDiff(a, b) {
  const wordsA = a.split(/(\s+)/);
  const wordsB = b.split(/(\s+)/);
  const n = wordsA.length, m = wordsB.length;
  const lcs = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = wordsA[i] === wordsB[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const opsA = [], opsB = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (wordsA[i] === wordsB[j]) {
      opsA.push({ text: wordsA[i], type: "same" });
      opsB.push({ text: wordsB[j], type: "same" });
      i++; j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      opsA.push({ text: wordsA[i], type: "diff" });
      i++;
    } else {
      opsB.push({ text: wordsB[j], type: "diff" });
      j++;
    }
  }
  while (i < n) { opsA.push({ text: wordsA[i], type: "diff" }); i++; }
  while (j < m) { opsB.push({ text: wordsB[j], type: "diff" }); j++; }
  return { opsA, opsB };
}

// Parses a composed SRT string (timestamps ignored for the comparison
// view — both sides always share the same original cue timing since both
// were translated from the identical source) into {index, content} blocks.
function parseSrtBlocks(text) {
  if (!text) return [];
  const blocks = text.replace(/\r\n/g, "\n").split(/\n\n+/);
  const result = [];
  for (const block of blocks) {
    const lines = block.split("\n").filter((l) => l.trim().length > 0);
    if (lines.length < 2) continue;
    const index = parseInt(lines[0], 10);
    if (Number.isNaN(index)) continue;
    // Trim each line — a trailing/leading space one engine's output has
    // and the other doesn't (invisible in rendered text) was previously
    // making two VISUALLY identical cues compare as different, showing a
    // spurious single-word diff (confirmed live: "Ok, cosa prendiamo
    // oggi?" flagged as changed on both sides despite being byte-for-byte
    // the same once trimmed).
    const content = lines.slice(2).map((l) => l.trim()).join("\n"); // lines[1] is the timestamp line
    result.push({ index, content });
  }
  return result;
}

createApp({
  data() {
    return {
      pageLoading: false,
      instances: [],
      languages: [], // [{code2, name}] from Bazarr's own known-language list

      // Source mode: "library" (search Bazarr's FULL episode/movie list —
      // not just Subtitlarr's own narrower "wanted" queue) or "upload" (a
      // raw .srt independent of Bazarr entirely).
      sourceMode: "library",

      itemSearch: "",
      itemSourceLangFilter: "", // filters library search results to items with an existing subtitle in this language
      itemResults: [],
      searchingItems: false,
      selectedItem: null, // {item_type, bazarr_id, display_title, subtitle_langs, ...} from search_library
      selectedSourceLang: "", // which of selectedItem.subtitle_langs to translate FROM
      selectedTargetLang: "", // explicitly chosen — NOT inherited from any items row

      uploadFile: null,
      uploadSourceLang: "",
      uploadTargetLang: "",

      instanceIdA: null,
      instanceIdB: null,
      parallel: false,
      // Per-side override of the Catalan "Vegeta insults" style toggle —
      // null means "use the saved Language Rules setting" (shown as an
      // indeterminate checkbox state isn't practical in plain HTML, so
      // this defaults to whatever the saved setting is once loaded, then
      // tracks independently per side from there).
      catalanVegetaInsultsA: false,
      catalanVegetaInsultsB: false,
      savedCatalanVegetaInsults: false,

      // Per-side temperature override — null means "use whatever this
      // engine instance is saved with," populated to that saved value the
      // moment an instance is picked (see onInstanceAChange/B below) so
      // the field always shows a real starting number, but stays
      // independently editable per side from there.
      temperatureA: null,
      temperatureB: null,

      // Optional: compare engine A's fresh result against an uploaded
      // already-translated file instead of running a second engine.
      useReference: false,
      referenceFile: null,
      referenceText: null,
      referenceError: null,
      loadingReference: false,

      running: false,
      runError: null,
      result: null, // full /api/compare(|/uploaded) response

      _searchDebounce: null,
    };
  },
  computed: {
    // Which target-language code is actually in play, regardless of
    // source mode — used to gate the Catalan Vegeta-insults toggle, which
    // only ever affects a Catalan target (see build_system_prompt).
    targetLangCode() {
      const raw = this.sourceMode === "library" ? this.selectedTargetLang : this.uploadTargetLang;
      return (raw || "").trim().toLowerCase();
    },
    isCatalanTarget() {
      return this.targetLangCode === "ca";
    },
    sameEndpointWarning() {
      if (!this.instanceIdA || !this.instanceIdB || this.useReference) return null;
      const a = this.instances.find((i) => i.id === this.instanceIdA);
      const b = this.instances.find((i) => i.id === this.instanceIdB);
      if (!a || !b) return null;
      const urlA = a.config && a.config.base_url;
      const urlB = b.config && b.config.base_url;
      if (urlA && urlB && urlA === urlB) {
        return "Both engines point to the same server — running in parallel may slow both down, or one may queue behind the other.";
      }
      return null;
    },
    canRun() {
      const sourceReady =
        this.sourceMode === "library"
          ? !!this.selectedItem && this.selectedSourceLang && this.selectedTargetLang.trim()
          : !!this.uploadFile && this.uploadSourceLang.trim() && this.uploadTargetLang.trim();
      const enginesReady = this.useReference
        ? !!this.instanceIdA
        : this.instanceIdA && this.instanceIdB && this.instanceIdA !== this.instanceIdB;
      const referenceReady = !this.useReference || (this.referenceText && !this.loadingReference);
      return sourceReady && enginesReady && referenceReady && !this.running;
    },
    // Normalizes to always show exactly two labeled sides in the diff,
    // whether side B came from a second engine run or a static reference
    // upload — the rest of the UI doesn't need to know which.
    sideA() {
      if (!this.result) return null;
      return this.result.results[0];
    },
    sideB() {
      if (this.useReference) {
        return this.referenceText
          ? { instance_name: "Reference upload", ok: true, subtitle_text: this.referenceText }
          : null;
      }
      return this.result ? this.result.results[1] : null;
    },
    diffRows() {
      const a = this.sideA, b = this.sideB;
      if (!a || !b || !a.ok || !b.ok) return [];
      const blocksA = parseSrtBlocks(a.subtitle_text);
      const blocksB = parseSrtBlocks(b.subtitle_text);
      const byIndexB = new Map(blocksB.map((blk) => [blk.index, blk]));
      const bySourceIndex = new Map(
        this.result && this.result.source_text ? parseSrtBlocks(this.result.source_text).map((blk) => [blk.index, blk]) : []
      );
      return blocksA.map((blkA) => {
        const blkB = byIndexB.get(blkA.index) || { content: "" };
        const identical = blkA.content === blkB.content;
        const diff = identical ? null : wordDiff(blkA.content, blkB.content);
        const sourceBlk = bySourceIndex.get(blkA.index);
        return {
          index: blkA.index, contentA: blkA.content, contentB: blkB.content, identical, diff,
          sourceText: sourceBlk ? sourceBlk.content : null,
        };
      });
    },
    identicalCount() {
      return this.diffRows.filter((r) => r.identical).length;
    },
  },
  methods: {
    async loadInstances() {
      const res = await Api.listEngineInstances();
      this.instances = (res.data || []).filter((i) => i.provider_type !== "separator");
    },
    onInstanceAChange() {
      const inst = this.instances.find((i) => i.id === this.instanceIdA);
      this.temperatureA = inst && inst.config ? (inst.config.temperature ?? null) : null;
    },
    onInstanceBChange() {
      const inst = this.instances.find((i) => i.id === this.instanceIdB);
      this.temperatureB = inst && inst.config ? (inst.config.temperature ?? null) : null;
    },
    // The number input's min/max attrs are only a soft browser hint — a
    // user can still type/scroll past them. Confirmed live: temperature=3
    // was submittable and Gemini rejected it server-side with "must be in
    // the range [0.0, 2.0]" — this clamps on blur so an out-of-range
    // value never leaves the field, on top of the server-side check.
    clampTemperature(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return value;
      return Math.min(2, Math.max(0, value));
    },
    async loadLanguages() {
      const res = await Api.getCompareLanguages();
      this.languages = res.languages || [];
    },
    async loadSavedCatalanSetting() {
      const res = await Api.getLanguageConfig();
      this.savedCatalanVegetaInsults = !!res.catalan_vegeta_insults;
      this.catalanVegetaInsultsA = this.savedCatalanVegetaInsults;
      this.catalanVegetaInsultsB = this.savedCatalanVegetaInsults;
    },
    onSearchInput() {
      clearTimeout(this._searchDebounce);
      this._searchDebounce = setTimeout(() => this.searchItems(), 300);
    },
    async searchItems() {
      // A source-language filter alone (no text typed yet) is still a
      // valid, useful search — "show me everything with an existing
      // Spanish subtitle" — so this only bails out when BOTH are empty,
      // not just when the text field is.
      if (!this.itemSearch.trim() && !this.itemSourceLangFilter) {
        this.itemResults = [];
        return;
      }
      this.searchingItems = true;
      try {
        const res = await Api.searchCompareLibrary(this.itemSearch.trim(), this.itemSourceLangFilter);
        this.itemResults = res.data || [];
      } catch (_) {
        this.itemResults = [];
      } finally {
        this.searchingItems = false;
      }
    },
    pickItem(item) {
      this.selectedItem = item;
      this.itemResults = [];
      this.itemSearch = item.display_title;
      // Pre-select the source language if the filter already narrowed to
      // exactly one, or if the item only has one subtitle track anyway —
      // otherwise leave it for an explicit pick among subtitle_langs.
      this.selectedSourceLang =
        this.itemSourceLangFilter || (item.subtitle_langs.length === 1 ? item.subtitle_langs[0] : "");
      this.result = null;
      this.runError = null;
    },
    onUploadFileChange(event) {
      this.uploadFile = event.target.files[0] || null;
      this.result = null;
      this.runError = null;
    },
    async onReferenceFileChange(event) {
      const file = event.target.files[0] || null;
      this.referenceFile = file;
      this.referenceText = null;
      this.referenceError = null;
      if (!file) return;
      this.loadingReference = true;
      try {
        const res = await Api.parseReferenceSubtitle(file);
        this.referenceText = res.subtitle_text;
      } catch (err) {
        this.referenceError = err.message;
      } finally {
        this.loadingReference = false;
      }
    },
    async runComparison() {
      if (!this.canRun) return;
      this.running = true;
      this.runError = null;
      this.result = null;
      // useReference: only engine A actually runs — instanceIdB is sent as
      // null/omitted, and the backend skips the second engine call
      // entirely rather than wastefully running it just to discard it.
      const instanceB = this.useReference ? null : this.instanceIdB;
      const parallel = this.useReference ? false : this.parallel;
      // Only meaningful (and only sent as non-null) for a Catalan target —
      // for any other target language build_system_prompt ignores this
      // flag entirely, so there's nothing useful to override.
      const opts = {
        temperatureA: this.temperatureA, temperatureB: this.temperatureB,
        ...(this.isCatalanTarget
          ? { catalanVegetaInsultsA: this.catalanVegetaInsultsA, catalanVegetaInsultsB: this.catalanVegetaInsultsB }
          : {}),
      };
      try {
        if (this.sourceMode === "library") {
          this.result = await Api.runCompare(
            this.selectedItem, this.selectedSourceLang, this.selectedTargetLang.trim(),
            this.instanceIdA, instanceB, parallel, opts
          );
        } else {
          this.result = await Api.runCompareUploaded(
            this.uploadFile, this.uploadSourceLang.trim(), this.uploadTargetLang.trim(),
            this.instanceIdA, instanceB, parallel, opts
          );
        }
      } catch (err) {
        this.runError = err.message;
      } finally {
        this.running = false;
      }
    },
    instanceLabel(id) {
      const inst = this.instances.find((i) => i.id === id);
      return inst ? inst.name : "—";
    },
  },
  async mounted() {
    this.pageLoading = true;
    await Promise.all([this.loadInstances(), this.loadLanguages(), this.loadSavedCatalanSetting()]);
    this.pageLoading = false;
  },
}).mount("#compare-app");
