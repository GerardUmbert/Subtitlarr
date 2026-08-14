const { createApp } = Vue;

// Full names for the language codes LANGUAGE_VARIANTS covers — used to
// label each dropdown row on the page (e.g. "Spanish (es)").
const VARIANT_LANGUAGE_NAMES = {
  es: "Spanish",
  pt: "Portuguese",
  en: "English",
  fr: "French",
  zh: "Chinese",
};

createApp({
  data() {
    return {
      pageLoading: true,
      sourcePriority: [],
      newSourceLang: "",
      targetAllowlist: [],
      newTargetLang: "",
      bazarrLanguages: [], // [{code2, name}] — exactly what THIS Bazarr instance knows/reports
      catalanVegetaInsults: false,
      languageVariants: {},
      availableVariants: {},
      variantDefaults: {},
      saving: false,
      saved: false,
    };
  },
  computed: {
    variantLanguageCodes() {
      // Stable order regardless of object key insertion order from the API.
      return Object.keys(this.availableVariants).sort();
    },
    bazarrLanguageNames() {
      const map = {};
      for (const lang of this.bazarrLanguages) map[lang.code2] = lang.name;
      return map;
    },
  },
  methods: {
    languageName(code) {
      return VARIANT_LANGUAGE_NAMES[code] || code.toUpperCase();
    },
    // Prefers Bazarr's own name for a target-allowlist code (e.g. "Portuguese
    // (Brazil)" for "pb") since these codes aren't limited to plain ISO 639-1
    // — falls back to the bare code if Bazarr's list hasn't loaded or doesn't
    // recognize it.
    targetLangLabel(code) {
      return this.bazarrLanguageNames[code] || code.toUpperCase();
    },
    variantFor(code) {
      return this.languageVariants[code] || this.variantDefaults[code];
    },
    setVariant(code, value) {
      this.languageVariants = { ...this.languageVariants, [code]: value };
    },
    async load() {
      const [cfg, variants, langs] = await Promise.all([
        Api.getLanguageConfig(),
        Api.getAvailableLanguageVariants(),
        Api.getCompareLanguages(),
      ]);
      this.sourcePriority = cfg.source_priority;
      this.targetAllowlist = cfg.target_language_allowlist || [];
      this.catalanVegetaInsults = cfg.catalan_vegeta_insults;
      this.languageVariants = cfg.language_variants || {};
      this.availableVariants = variants.variants;
      this.variantDefaults = variants.defaults;
      this.bazarrLanguages = langs.languages || [];
    },
    addSource() {
      const lang = this.newSourceLang.trim().toLowerCase();
      if (lang && !this.sourcePriority.includes(lang)) {
        this.sourcePriority.push(lang);
      }
      this.newSourceLang = "";
    },
    removeSource(i) {
      this.sourcePriority.splice(i, 1);
    },
    addTarget() {
      const lang = this.newTargetLang.trim().toLowerCase();
      // Requires a code Bazarr itself recognizes — free text here risked
      // e.g. typing "pt-BR" when Bazarr actually reports Brazilian
      // Portuguese as "pb", silently matching nothing at poll time.
      if (lang && lang in this.bazarrLanguageNames && !this.targetAllowlist.includes(lang)) {
        this.targetAllowlist.push(lang);
      }
      this.newTargetLang = "";
    },
    removeTarget(i) {
      this.targetAllowlist.splice(i, 1);
    },
    async save() {
      this.saving = true;
      this.saved = false;
      try {
        await Api.setLanguageConfig({
          source_priority: this.sourcePriority,
          catalan_vegeta_insults: this.catalanVegetaInsults,
          language_variants: this.languageVariants,
          target_language_allowlist: this.targetAllowlist,
        });
        this.saved = true;
        setTimeout(() => (this.saved = false), 3000);
      } catch (err) {
        alert(`Could not save: ${err.message}`);
      } finally {
        this.saving = false;
      }
    },
  },
  async mounted() {
    await this.load();
    this.pageLoading = false;
  },
}).mount("#languages-app");
