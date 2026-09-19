const { createApp } = Vue;

createApp({
  data() {
    return {
      pageLoading: true,
      mcpToken: "",
      mcpPath: "/mcp",
      tokenVisible: false,
      tokenCopied: false,
      urlCopied: false,
      cmdCopied: false,
      confirmingRegenerate: false,
      regenerating: false,
      error: "",
    };
  },
  computed: {
    serverUrl() {
      return `${window.location.origin}${this.mcpPath}`;
    },
    claudeCodeCommand() {
      return `claude mcp add --transport http subtitlarr ${this.serverUrl} --header "Authorization: Bearer ${this.mcpToken}"`;
    },
    mcpJsonSnippet() {
      const config = {
        mcpServers: {
          subtitlarr: {
            type: "http",
            url: this.serverUrl,
            headers: { Authorization: `Bearer ${this.mcpToken}` },
          },
        },
      };
      return JSON.stringify(config, null, 2);
    },
  },
  methods: {
    async load() {
      try {
        const status = await Api.getMcpStatus();
        this.mcpToken = status.token;
        this.mcpPath = status.path;
      } catch (err) {
        this.error = err.message;
      }
    },
    async copyValue(value, which) {
      try {
        await navigator.clipboard.writeText(value);
        if (which === "token") {
          this.tokenCopied = true;
          setTimeout(() => (this.tokenCopied = false), 2000);
        } else if (which === "url") {
          this.urlCopied = true;
          setTimeout(() => (this.urlCopied = false), 2000);
        } else if (which === "cmd") {
          this.cmdCopied = true;
          setTimeout(() => (this.cmdCopied = false), 2000);
        }
      } catch (_) {
        // clipboard API unavailable (non-HTTPS context, permissions) — the
        // value is already shown/copyable by hand
      }
    },
    async regenerateToken() {
      this.regenerating = true;
      this.error = "";
      try {
        const result = await Api.regenerateMcpToken();
        this.mcpToken = result.token;
        this.confirmingRegenerate = false;
      } catch (err) {
        this.error = err.message;
      } finally {
        this.regenerating = false;
      }
    },
  },
  async mounted() {
    await this.load();
    this.pageLoading = false;
  },
}).mount("#mcp-app");
