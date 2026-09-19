"""Classification of a failed item's error_message into retryable vs.
non-retryable against the SAME engine/credential that produced it.

This exists because of a hard rule (see plans/mcp-server.md, "Hard
rule: never resubmit an already-failed item to the same engine"):
repeatedly resubmitting content that a provider already content-
blocked, or hammering a provider with an already-dead/quota-exhausted
key, risks that provider's own abuse enforcement against the account —
up to suspension or termination. That is a far worse outcome than one
untranslated subtitle, so the default on any uncertainty is "treat as
NOT retryable," never "guess and resubmit."

Mirrors the classification already used by the manual-translate skill
(.claude/skills/manual-translate/SKILL.md) — kept as one shared source
of truth here since both the skill and these MCP tools reason about the
exact same error_message strings coming out of app/engine/translator.py.
"""

# Substrings observed in real error_message values (see AGENTS.md and
# app/engine/translator.py) that indicate a transient, provider-side
# hiccup — safe to retry through the normal engine cascade.
_RETRYABLE_MARKERS = (
    "rate limit",
    "429",
    "timed out",
    "timeout",
    "connection",
    "502",
    "503",
    "504",
    "gateway",
    "server error",
    "500",
)

# Substrings indicating the provider made a judgment about the content
# or the credential itself — retrying changes nothing and risks the
# account.
_NON_RETRYABLE_MARKERS = (
    "blocked its own response",
    "blocked this request",
    "prohibited content",
    "safety",
    "content policy",
    "quota",
    "insufficient_quota",
    "402",
    "403",
    "401",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "service account",
    "disabled",
    "revoked",
    "deleted",
)


def classify_failure(error_message: str | None) -> str:
    """Returns "retryable", "non_retryable", or "unknown". Callers MUST
    treat "unknown" the same as "non_retryable" — see module docstring."""
    if not error_message:
        return "unknown"
    text = error_message.lower()
    if any(marker in text for marker in _NON_RETRYABLE_MARKERS):
        return "non_retryable"
    if any(marker in text for marker in _RETRYABLE_MARKERS):
        return "retryable"
    return "unknown"


def is_safe_to_retry(error_message: str | None) -> bool:
    return classify_failure(error_message) == "retryable"
