const { createApp } = Vue;

createApp({
  data() {
    return {
      pageLoading: true,
      sourcePriority: [],
      newSourceLang: "",
      catalanVegetaInsults: false,
      saving: false,
      saved: false,
    };
  },
  methods: {
    async load() {
      const cfg = await Api.getLanguageConfig();
      this.sourcePriority = cfg.source_priority;
      this.catalanVegetaInsults = cfg.catalan_vegeta_insults;
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
