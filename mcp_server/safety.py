"""Classification of a failed item's error_message into retryable vs.
non-retryable against the SAME engine/credential that produced it.

This exists because of a hard rule (see plans/mcp-server.md, "Hard
rule: never resubmit an already-failed item to the same engine"): only
one failure mode actually risks the provider's own abuse enforcement
against the account — the provider making a *content judgment*
(refusing the request or blanking its own response as prohibited
content). Resubmitting that unchanged is what can escalate to
suspension/termination, so it's the one case where the default on any
uncertainty is "treat as NOT retryable," never "guess and resubmit."

Dead/revoked credentials, exhausted quota, and transient 5xx/network
errors are a different animal: they say nothing about the content, and
they stop applying the moment the underlying cause is fixed (key
rotated, quota resets, provider recovers). Once that's true they are
ordinary retry candidates through the normal cascade — refusing them
forever because the *old* error text still says "401" or "disabled" is
exactly the false-positive this module used to produce. Verdict here
still can't know whether a credential has actually been rotated since
the failure (see "credential_or_infra" below) — that judgment call
stays with the caller (human or agent), this module just stops
conflating it with the content-safety case.

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
    "unexpected",  # e.g. "Unexpected Gemini response shape" — a parsing
                   # hiccup on our side, not a provider content judgment.
)

# Substrings indicating the provider made a judgment about the CONTENT
# itself (not the credential) — resubmitting the same content changes
# nothing and risks the account. This is the only bucket that must
# route to manual translation instead of a retry.
_CONTENT_BLOCKED_MARKERS = (
    "blocked its own response",
    "blocked this request",
    "prohibited content",
    "safety",
    "content policy",
)

# Substrings indicating a dead/exhausted credential or quota, not a
# content judgment. Not safe to retry blindly (the same dead key will
# just fail again), but NOT a content-safety veto either — once the
# underlying cause is fixed (key rotated, quota reset) these are
# ordinary retry candidates. Kept distinct from content-blocked so
# callers can decide "is the credential still bad?" instead of being
# told "never, this is a content judgment" when it isn't one.
_CREDENTIAL_OR_INFRA_MARKERS = (
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
    """Returns one of:

    - "retryable": a transient provider-side or parsing hiccup — safe
      to resubmit via subtitlarr_run_by_ids/subtitlarr_run_item as-is.
    - "content_blocked": the provider made a judgment about the
      content itself. Never resubmit this to the same engine — use
      manual translation instead.
    - "credential_or_infra": a dead/revoked credential or exhausted
      quota. NOT safe to retry blindly (same dead key -> same
      failure), but also not a content-safety veto — if the caller has
      confirmed the underlying cause no longer applies (e.g. the API
      key was rotated), this is safe to retry through the normal
      cascade.
    - "unknown": couldn't tell from the text. Callers MUST treat this
      the same as "content_blocked" (refuse by default) unless a human
      has separately confirmed it's safe.
    """
    if not error_message:
        return "unknown"
    text = error_message.lower()
    if any(marker in text for marker in _CONTENT_BLOCKED_MARKERS):
        return "content_blocked"
    if any(marker in text for marker in _CREDENTIAL_OR_INFRA_MARKERS):
        return "credential_or_infra"
    if any(marker in text for marker in _RETRYABLE_MARKERS):
        return "retryable"
    return "unknown"


def is_safe_to_retry(error_message: str | None) -> bool:
    return classify_failure(error_message) == "retryable"
