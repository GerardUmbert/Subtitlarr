const { createApp } = Vue;

createApp({
  data() {
    return {
      pageLoading: true,
      cronExpression: "",
      ageThresholdDays: 14,
      dailyTranslationLimit: 100,
      nextRun: "",
      syncMediaCron: "",
      syncSubsCron: "",
      nextSyncMediaRun: "",
      nextSyncSubsRun: "",
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
      queueUploadsEnabled: false,
      pendingUploadCount: 0,
      pushUploadsActive: false,
      pushingUploads: false,
      pushUploadsStarted: false,
      pushUploadsResult: null,
      confirmingClear: false,
      clearing: false,
      clearResult: null,
      fixingStaleRuns: false,
      fixStaleRunsResult: null,
      clearingRateLimits: false,
      clearRateLimitsResult: null,
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
        this.queueUploadsEnabled = jobs.queue_uploads_enabled;
        this.pendingUploadCount = jobs.pending_upload_count;
        this.pushUploadsActive = jobs.push_uploads_active;
      } catch (_) {
        // keep last known state on transient failure
      }
      try {
        const [cfg, nextRuns] = await Promise.all([Api.getScheduleConfig(), Api.getNextRuns()]);
        this.syncMediaCron = cfg.sync_media_cron;
        this.syncSubsCron = cfg.sync_subs_cron;
        this.nextSyncMediaRun = nextRuns.next_sync_media_run
          ? new Date(nextRuns.next_sync_media_run).toLocaleString() : "";
        this.nextSyncSubsRun = nextRuns.next_sync_subs_run
          ? new Date(nextRuns.next_sync_subs_run).toLocaleString() : "";
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
    async pushUploads() {
      this.pushingUploads = true;
      this.error = "";
      this.pushUploadsResult = null;
      try {
        const result = await Api.pushUploads();
        if (!result.started) {
          this.error = result.reason || "Could not start push";
          return;
        }
        this.pushUploadsStarted = true;
        setTimeout(() => (this.pushUploadsStarted = false), 3000);
        await this.load();
        this.pollPushUploadsResult();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.pushingUploads = false;
      }
    },
    pollPushUploadsResult() {
      const check = async () => {
        const status = await Api.getSyncStatus();
        if (status.push_uploads.active) {
          setTimeout(check, 1000);
        } else {
          this.pushUploadsResult = status.push_uploads.result;
          if (status.push_uploads.error) this.error = status.push_uploads.error;
          await this.load();
        }
      };
      setTimeout(check, 1000);
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
    async fixStaleRuns() {
      this.fixingStaleRuns = true;
      this.error = "";
      this.fixStaleRunsResult = null;
      try {
        this.fixStaleRunsResult = await Api.closeStaleRuns();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.fixingStaleRuns = false;
      }
    },
    async clearRateLimits() {
      this.clearingRateLimits = true;
      this.error = "";
      this.clearRateLimitsResult = null;
      try {
        this.clearRateLimitsResult = await Api.clearEngineRateLimits();
      } catch (err) {
        this.error = err.message;
      } finally {
        this.clearingRateLimits = false;
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
