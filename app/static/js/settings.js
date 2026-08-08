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
      languageCheckCron: "",
      backupCron: "",
      backupKeepCount: 20,
      nextRun: "",
      nextSyncMediaRun: "",
      nextSyncSubsRun: "",
      nextLanguageCheckRun: "",
      nextBackupRun: "",
      saving: false,
      saved: false,
      error: "",
      backupActive: false,
      startingBackup: false,
      backupStarted: false,
      backupResult: null,
      backupError: "",
      backups: [],
      backupsLoading: false,
      restoringFilename: null,
      restoreResult: null,
      restoreError: "",
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
      this.languageCheckCron = cfg.language_check_cron;
      this.backupCron = cfg.backup_cron;
      this.backupKeepCount = cfg.backup_keep_count;
      await this.loadNextRun();
      try {
        const status = await Api.getSyncStatus();
        this.backupActive = !!(status.backup && status.backup.active);
      } catch (_) {
        // endpoint not reachable yet — ignore
      }
    },
    async loadNextRun() {
      try {
        const result = await Api.getNextRuns();
        this.nextRun = result.next_run ? new Date(result.next_run).toLocaleString() : "";
        this.nextSyncMediaRun = result.next_sync_media_run ? new Date(result.next_sync_media_run).toLocaleString() : "";
        this.nextSyncSubsRun = result.next_sync_subs_run ? new Date(result.next_sync_subs_run).toLocaleString() : "";
        this.nextLanguageCheckRun = result.next_language_check_run ? new Date(result.next_language_check_run).toLocaleString() : "";
        this.nextBackupRun = result.next_backup_run ? new Date(result.next_backup_run).toLocaleString() : "";
      } catch (_) {
        this.nextRun = "";
        this.nextSyncMediaRun = "";
        this.nextSyncSubsRun = "";
        this.nextLanguageCheckRun = "";
        this.nextBackupRun = "";
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
          language_check_cron: this.languageCheckCron,
          backup_cron: this.backupCron,
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
    async runBackupNow() {
      this.startingBackup = true;
      this.backupError = "";
      this.backupResult = null;
      try {
        const result = await Api.runBackup();
        if (!result.started) {
          this.backupError = result.reason || "Could not start backup";
          return;
        }
        this.backupStarted = true;
        setTimeout(() => (this.backupStarted = false), 3000);
        this.pollBackupResult();
      } catch (err) {
        this.backupError = err.message;
      } finally {
        this.startingBackup = false;
      }
    },
    pollBackupResult() {
      const check = async () => {
        const status = await Api.getSyncStatus();
        this.backupActive = status.backup.active;
        if (status.backup.active) {
          setTimeout(check, 1000);
        } else {
          this.backupResult = status.backup.result;
          if (status.backup.error) this.backupError = status.backup.error;
          await this.loadBackups();
        }
      };
      setTimeout(check, 1000);
    },
    async loadBackups() {
      this.backupsLoading = true;
      try {
        const result = await Api.listBackups();
        this.backups = result.data;
      } catch (_) {
        // keep last known state on transient failure
      } finally {
        this.backupsLoading = false;
      }
    },
    formatBackupTime(iso) {
      return new Date(iso).toLocaleString();
    },
    formatBackupSize(bytes) {
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    },
    confirmRestore(b) {
      const label = this.formatBackupTime(b.created_at);
      if (!confirm(`Restore the database from the ${label} snapshot? This overwrites all current data (a safety snapshot of the current state is taken first).`)) return;
      this.restoreBackup(b.filename);
    },
    async restoreBackup(filename) {
      this.restoringFilename = filename;
      this.restoreError = "";
      this.restoreResult = null;
      try {
        const result = await Api.restoreBackup(filename);
        this.restoreResult = result;
        await this.loadBackups();
      } catch (err) {
        this.restoreError = err.message;
      } finally {
        this.restoringFilename = null;
      }
    },
  },
  async mounted() {
    await this.load();
    await this.loadBackups();
  },
}).mount("#settings-app");
