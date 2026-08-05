const { createApp } = Vue;

createApp({
  data() {
    return {
      cronExpression: "",
      ageThresholdDays: 14,
      dailyTranslationLimit: 100,
      pauseBetweenItemsSeconds: 30,
      queueUploadsEnabled: false,
      syncMediaCron: "",
      syncSubsCron: "",
      nextRun: "",
      nextSyncMediaRun: "",
      nextSyncSubsRun: "",
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
      this.queueUploadsEnabled = cfg.queue_uploads_enabled;
      this.syncMediaCron = cfg.sync_media_cron;
      this.syncSubsCron = cfg.sync_subs_cron;
      await this.loadNextRun();
    },
    async loadNextRun() {
      try {
        const result = await Api.getNextRuns();
        this.nextRun = result.next_run ? new Date(result.next_run).toLocaleString() : "";
        this.nextSyncMediaRun = result.next_sync_media_run ? new Date(result.next_sync_media_run).toLocaleString() : "";
        this.nextSyncSubsRun = result.next_sync_subs_run ? new Date(result.next_sync_subs_run).toLocaleString() : "";
      } catch (_) {
        this.nextRun = "";
        this.nextSyncMediaRun = "";
        this.nextSyncSubsRun = "";
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
          queue_uploads_enabled: this.queueUploadsEnabled,
          sync_media_cron: this.syncMediaCron,
          sync_subs_cron: this.syncSubsCron,
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
