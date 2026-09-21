const { createApp } = Vue;

createApp({
  data() {
    return {
      username: "",
      newPassword: "",
      newPasswordConfirm: "",
      currentPassword: "",
      showCurrentPassword: false,
      showNewPassword: false,
      saving: false,
      saved: false,
      error: "",
    };
  },
  async mounted() {
    try {
      const data = await Api.getAccount();
      this.username = data.username;
    } catch (err) {
      this.error = err.message;
    }
  },
  methods: {
    async submit() {
      this.saved = false;
      this.error = "";
      if (this.newPassword && this.newPassword !== this.newPasswordConfirm) {
        this.error = "New password and confirmation don't match.";
        return;
      }
      this.saving = true;
      try {
        await Api.updateAccount(this.username, this.currentPassword, this.newPassword);
        this.saved = true;
        this.newPassword = "";
        this.newPasswordConfirm = "";
        this.currentPassword = "";
      } catch (err) {
        this.error = err.message;
      } finally {
        this.saving = false;
      }
    },
  },
}).mount("#account-app");
