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
  },
  methods: {
    languageName(code) {
      return VARIANT_LANGUAGE_NAMES[code] || code.toUpperCase();
    },
    variantFor(code) {
      return this.languageVariants[code] || this.variantDefaults[code];
    },
    setVariant(code, value) {
      this.languageVariants = { ...this.languageVariants, [code]: value };
    },
    async load() {
      const [cfg, variants] = await Promise.all([
        Api.getLanguageConfig(),
        Api.getAvailableLanguageVariants(),
      ]);
      this.sourcePriority = cfg.source_priority;
      this.catalanVegetaInsults = cfg.catalan_vegeta_insults;
      this.languageVariants = cfg.language_variants || {};
      this.availableVariants = variants.variants;
      this.variantDefaults = variants.defaults;
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
    async save() {
      this.saving = true;
      this.saved = false;
      try {
        await Api.setLanguageConfig({
          source_priority: this.sourcePriority,
          catalan_vegeta_insults: this.catalanVegetaInsults,
          language_variants: this.languageVariants,
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
