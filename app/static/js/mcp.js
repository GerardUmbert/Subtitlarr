const { createApp } = Vue;

// navigator.clipboard needs a secure context (HTTPS or localhost) — a
// plain http://<lan-ip> address (the normal way to reach a NAS-hosted
// instance) doesn't qualify, so the modern API silently isn't there at
// all. Falls back to the older execCommand('copy') via a temporary
// textarea, which works over plain HTTP. Returns whether it actually
// worked, so the caller can tell the user rather than pretending success.
async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // fall through to the legacy path below
    }
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch (_) {
    return false;
  }
}

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
      const ok = await copyToClipboard(value);
      if (!ok) {
        this.error = "Couldn't copy automatically — select the text and copy it by hand (browsers only allow the automatic clipboard on HTTPS or localhost, not a plain http:// LAN address).";
        return;
      }
      this.error = "";
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
