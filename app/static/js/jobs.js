const { createApp } = Vue;

createApp({
  data() {
    return {
      cronExpression: "",
      ageThresholdDays: 14,
      nextRun: "",
      runActive: false,
      running: false,
      runStarted: false,
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
        this.nextRun = jobs.next_run ? new Date(jobs.next_run).toLocaleString() : "";
        this.runActive = jobs.run_active;
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
    this.schedulePoll();
  },
  unmounted() {
    if (this._pollHandle) clearTimeout(this._pollHandle);
  },
}).mount("#jobs-app");
