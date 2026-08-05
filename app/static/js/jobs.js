const { createApp } = Vue;

createApp({
  data() {
    return {
      pageLoading: true,
      cronExpression: "",
      ageThresholdDays: 14,
      dailyTranslationLimit: 100,
      nextRun: "",
      runActive: false,
      running: false,
      runStarted: false,
      syncMediaActive: false,
      syncingMedia: false,
      syncMediaStarted: false,
      syncSubsActive: false,
      syncingSubs: false,
      syncSubsStarted: false,
      syncSubsResult: null,
      confirmingClear: false,
      clearing: false,
      clearResult: null,
      error: "",
      _pollHandle: null,
    };
  },
  methods: {
    async load() {
      try {
        const jobs = await Api.getJobs();
        this.cronExpression = jobs.cron_expression;
        this.ageThresholdDays = jobs.age_threshold_days;
        this.dailyTranslationLimit = jobs.daily_translation_limit;
        this.nextRun = jobs.next_run ? new Date(jobs.next_run).toLocaleString() : "";
        this.runActive = jobs.run_active;
        this.syncMediaActive = jobs.sync_media_active;
        this.syncSubsActive = jobs.sync_subs_active;
      } catch (_) {
        // keep last known state on transient failure
      }
    },
    async runNow() {
      this.running = true;
      this.error = "";
      try {
        const result = await Api.runScheduledJobNow();
        if (!result.started) {
          this.error = result.reason || "Could not start run";
          return;
        }
        this.runStarted = true;
        setTimeout(() => (this.runStarted = false), 3000);
        await this.load();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.running = false;
      }
    },
    async syncMedia() {
      this.syncingMedia = true;
      this.error = "";
      try {
        const result = await Api.syncMedia();
        if (!result.started) {
          this.error = result.reason || "Could not start media sync";
          return;
        }
        this.syncMediaStarted = true;
        setTimeout(() => (this.syncMediaStarted = false), 3000);
        await this.load();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.syncingMedia = false;
      }
    },
    async syncSubs() {
      this.syncingSubs = true;
      this.error = "";
      this.syncSubsResult = null;
      try {
        const result = await Api.syncSubs();
        if (!result.started) {
          this.error = result.reason || "Could not start subtitle sync";
          return;
        }
        this.syncSubsStarted = true;
        setTimeout(() => (this.syncSubsStarted = false), 3000);
        await this.load();
        this.pollSyncSubsResult();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.syncingSubs = false;
      }
    },
    pollSyncSubsResult() {
      // The job runs in the background (asyncio.create_task) — poll until
      // it finishes so the "cached N/M items" result can be shown once
      // it's actually done, not just "started".
      const check = async () => {
        const status = await Api.getSyncStatus();
        if (status.sync_subs.active) {
          setTimeout(check, 1000);
        } else {
          this.syncSubsResult = status.sync_subs.result;
          if (status.sync_subs.error) this.error = status.sync_subs.error;
        }
      };
      setTimeout(check, 1000);
    },
    async clearDatabase() {
      this.clearing = true;
      this.error = "";
      try {
        const result = await Api.clearDatabase();
        this.clearResult = result;
        this.confirmingClear = false;
      } catch (err) {
        this.error = err.message;
      } finally {
        this.clearing = false;
      }
    },
    schedulePoll() {
      if (this._pollHandle) clearTimeout(this._pollHandle);
      this._pollHandle = setTimeout(async () => {
        await this.load();
        this.schedulePoll();
      }, 3000);
    },
  },
  async mounted() {
    await this.load();
    this.pageLoading = false;
    this.schedulePoll();
  },
  unmounted() {
    if (this._pollHandle) clearTimeout(this._pollHandle);
  },
}).mount("#jobs-app");
