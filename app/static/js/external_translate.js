const { createApp } = Vue;

// Same clipboard fallback as mcp.js — navigator.clipboard needs a secure
// context (HTTPS or localhost), which a plain http://<lan-ip> address (the
// normal way to reach a NAS-hosted instance) doesn't qualify for.
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
      token: "",
      tokenVisible: false,
      tokenCopied: false,
      confirmingRegenerate: false,
      regenerating: false,
      error: "",
    };
  },
  methods: {
    async load() {
      try {
        const status = await Api.getExternalTranslateStatus();
        this.token = status.token;
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
      }
    },
    async regenerateToken() {
      this.regenerating = true;
      this.error = "";
      try {
        const result = await Api.regenerateExternalTranslateToken();
        this.token = result.token;
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
}).mount("#external-translate-app");
