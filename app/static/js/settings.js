const { createApp } = Vue;

createApp({
  data() {
    return {
      cronExpression: "",
      ageThresholdDays: 14,
      dailyTranslationLimit: 100,
      pauseBetweenItemsSeconds: 30,
      nextRun: "",
      saving: false,
      saved: false,
      error: "",
    };
  },
  methods: {
    async load() {
      const cfg = await Api.getScheduleConfig();
      this.cronExpression = cfg.cron_expression;
      this.ageThresholdDays = cfg.age_threshold_days;
      this.dailyTranslationLimit = cfg.daily_translation_limit;
      this.pauseBetweenItemsSeconds = cfg.pause_between_items_seconds;
      await this.loadNextRun();
    },
    async loadNextRun() {
      try {
        const result = await Api.getNextRuns();
        this.nextRun = result.next_run ? new Date(result.next_run).toLocaleString() : "";
      } catch (_) {
        this.nextRun = "";
      }
    },
    async save() {
      this.saving = true;
      this.saved = false;
      this.error = "";
      try {
        await Api.setScheduleConfig({
          cron_expression: this.cronExpression,
          age_threshold_days: this.ageThresholdDays,
          daily_translation_limit: this.dailyTranslationLimit,
          pause_between_items_seconds: this.pauseBetweenItemsSeconds,
        });
        await this.loadNextRun();
        this.saved = true;
        setTimeout(() => (this.saved = false), 3000);
      } catch (err) {
        this.error = err.message;
      } finally {
        this.saving = false;
      }
    },
  },
  async mounted() {
    await this.load();
  },
}).mount("#settings-app");
