const { createApp } = Vue;

createApp({
  data() {
    return {
      baseUrl: "",
      apiKey: "",
      keyMasked: "",
      hasKey: false,
      testing: false,
      testResult: null,
      saving: false,
      saved: false,
    };
  },
  methods: {
    async load() {
      const cfg = await Api.getBazarrConfig();
      this.baseUrl = cfg.base_url;
      this.keyMasked = cfg.api_key_masked;
      this.hasKey = cfg.has_key;
    },
    async test() {
      this.testing = true;
      try {
        // Tests whatever is currently in the form, unsaved or not — falls
        // back to the saved key on the backend if the field is left blank.
        this.testResult = await Api.testBazarr({
          base_url: this.baseUrl,
          api_key: this.apiKey || null,
        });
      } catch (err) {
        this.testResult = { ok: false };
      } finally {
        this.testing = false;
      }
    },
    async save() {
      this.saving = true;
      this.saved = false;
      try {
        await Api.setBazarrConfig({
          base_url: this.baseUrl,
          api_key: this.apiKey || null,
        });
        this.apiKey = "";
        await this.load();
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
}).mount("#bazarr-app");
