const { createApp } = Vue;

createApp({
  data() {
    return {
      sourcePriority: [],
      managedLanguages: [],
      newSourceLang: "",
      newManagedLang: "",
      saving: false,
      saved: false,
    };
  },
  methods: {
    async load() {
      const cfg = await Api.getLanguageConfig();
      this.sourcePriority = cfg.source_priority;
      this.managedLanguages = cfg.managed_languages;
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
    addManaged() {
      const lang = this.newManagedLang.trim().toLowerCase();
      if (lang && !this.managedLanguages.includes(lang)) {
        this.managedLanguages.push(lang);
      }
      this.newManagedLang = "";
    },
    removeManaged(i) {
      this.managedLanguages.splice(i, 1);
    },
    async save() {
      this.saving = true;
      this.saved = false;
      try {
        await Api.setLanguageConfig({
          source_priority: this.sourcePriority,
          managed_languages: this.managedLanguages,
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
  },
}).mount("#languages-app");
