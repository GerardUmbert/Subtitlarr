# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [0.3.0]

### Added
- **NVIDIA provider**: a new cloud engine option using NVIDIA's free-tier
  NIM API (`build.nvidia.com`), defaulting to DeepSeek V4 Flash. Up to 40
  requests/minute on the free tier. Must be a real instructable chat model —
  NVIDIA's dedicated Riva Translate model was tried first and dropped: it
  has no instructable system prompt and proved unreliable at any real batch
  size (confirmed live testing: it merges/drops joined subtitle lines
  instead of translating them individually, even in small 5-line batches).
  The NVIDIA provider reuses the exact same numbered-index prompt scheme
  and outer batching as Ollama/Gemini, with no special-case chunking logic
  of its own. Has its own `nvidia_batch_token_budget` setting, separate
  from Ollama's — sharing one budget between them would have meant
  NVIDIA's cloud model silently inheriting Ollama's small GPU-safe
  default. Request timeout raised from 120s to 600s (matching Ollama's)
  after confirming live that a large single-request batch can legitimately
  take several minutes to generate — the shorter timeout was cutting off
  requests that were still working, not actually stuck.

  The default batch size went through several rounds tuning down live:
  12000 tokens failed on an unusually large episode (1481 cues in one
  ~29,500-char request) — a real 504 from NVIDIA's own servers on one
  attempt, a degenerate repeated-output failure on another. Cut to 6000,
  which still hit the same repeated-output failure — including on a
  NORMAL-sized batch, not just the oversized one — so it isn't purely a
  "batch too large" problem. Cut further to 2000 as a precaution while
  the actual root cause is investigated (blocked by a separate logging
  bug: the raw failing LLM response isn't reaching server.log — see
  `plans/` for the open investigation).
- **Queue page "Current batch" filter**: while a run is active, a chip
  next to the status filters shows every item in that run (including ones
  still queued and not yet started) and filters the table down to just
  them when clicked.
- **Queue page filters/page/search now survive a reload**: they're synced
  to the URL query string, so refreshing the page (or sharing/bookmarking
  the link) lands back on the same tab/filters/page instead of always
  resetting to defaults.
- **"Hide items with no source subtitle" toggle** on the Queue page: a
  standalone checkbox, independent of (and stacks with) the status/type/
  search filters, so skipped_no_source noise can be hidden from any view
  without needing its own separate tab.
- **Repetition-loop detection** in the LLM response parser: if the same
  non-trivial translated line repeats across 10+ consecutive cues, the
  response is now rejected outright rather than silently accepted — a
  live run got stuck repeating one line across 53 consecutive cues, which
  the old logic would have counted as "recovered" and could have uploaded
  as valid content.
- **Context window is now a dropdown** of standard power-of-2 values (4k
  through 256k) instead of a free-number input, matching the convention
  used by Ollama's own UI. A previously-saved non-standard value is still
  shown correctly if present.
- **Bulk "Run all N matching" now honors an explicit status filter**:
  filtering the Queue page to "Failed" (or "Done") and clicking the bulk-run
  button now actually retries those items, instead of always showing 0
  matching — an explicit status filter is trusted as an intent to act on
  it, not silently overridden by the passive pending/queued-only default
  used when no filter is set.
- `CHANGELOG.md` (this file).

### Fixed
- **Repetition-loop detection false-positive on genuinely repeated source
  content**: a real subtitle rip (Bakuon!!) had 50 consecutive identical
  cues ("Seat height / Weight" — a HUD/spec-overlay quirk), which DeepSeek
  translated correctly and identically every time; the repetition guard
  rejected this as a hallucination loop even though the translation was
  accurate. Now only flags a repeated translation as degenerate when the
  ORIGINAL source cues underneath it were NOT already identically
  repeated — a real hallucination loop (distinct source, repeated
  translation) is still caught exactly as before.
- **"Run all N matching" on the 'All' status tab skipped failed items**:
  a live case showed 2 failed + 1 pending items together in a filtered
  view, but the bulk-run button only picked up the 1 pending one. The
  passive (no explicit status filter) default now includes `failed`
  alongside `pending`/`queued` — `done` items are still excluded from
  this default to avoid silently re-translating everything already
  finished on a bare "run all" click.
- **Configure pages (Engine/Bazarr/Languages/Settings) were visibly
  narrower than Queue/Dashboard**: the settings card used a fixed 480px
  width left over from before the page-width fix; now fills the same
  content width as the rest of the app.
- **Response truncation on verbose/dense subtitle content** (e.g. anime
  cues with duplicated original-language text, heavy `<I>` formatting):
  raised the default context window headroom so a batch that fits the
  token budget doesn't get cut off mid-response before finishing.

## [0.2.0]

### Added
- **Jobs page** (`/jobs`): shows the cron expression, age threshold, and next
  scheduled run; a manual "Run now" button that fires the same age-gated job
  the cron runs; and a confirm-gated "Clear database" action that wipes
  queue/run history without touching any saved settings.
- **Bulk "Run all N matching" on the Queue page**: filter by status/type/
  search, then translate everything in that filtered set in one click.
  Respects the normal daily cap and age gate.
- **Daily translation limit** and **pause between items** settings — caps
  how many items a full-queue/scheduled run will translate per day (default
  100, 0 = unlimited), and adds a configurable rest between items (default
  30s) so a long run doesn't peg the GPU non-stop for hours.
- **Batch size override** (Engine settings): the per-batch dialogue token
  budget normally auto-scales with the context window, but can now be
  pinned to a fixed value — needed because small models can lose reliable
  output formatting on large batches well before running out of raw
  context.
- **Ollama watchdog**: if a single translation request runs longer than 300s
  with no response, the model is force-unloaded (`keep_alive: 0`) and the
  request retried exactly once before giving up.
- **Eager source-language preview**: the poller now resolves and stores each
  pending item's likely source language at poll time, so the Queue UI shows
  a real language instead of "?" before any translation has been attempted.
- **Fresh source-language resolution on re-run**: clicking run/re-run on an
  item now re-checks Bazarr for the current source language immediately,
  rather than trusting a possibly-stale cached value.
- **Toast notifications** for run/re-run actions ("Translating from X to
  Y…", "Translation run started…").
- **Per-attempt settings snapshot**: `item_run_log` now records exactly
  which engine, context window, and batch size were in effect for each
  translation attempt, so past results can be diagnosed without
  reconstructing timestamps against server restarts.
- `AGENTS.md` / `CLAUDE.md`: onboarding notes for an AI agent running the
  project locally outside Docker.

### Fixed
- **Cue-recovery failures on the LLM response parser**, found via several
  live translation runs:
  - Literal `\n` escape sequences in place of real line breaks (a response
    could come back entirely as `"616\nHola.\n\n617\n..."` instead of real
    newlines), which broke the header-line parser outright.
  - Mixed header formats within a single response — some cues as `"N\ntext"`,
    others as `"N. text"` with content on the same line as the index — where
    only the first format was recognized, silently dropping the rest.
- **Batch size regression**: an auto-scaling formula for per-batch token
  budget (tied to context window size) produced batches large enough that
  the model's output formatting became unreliable, causing very low cue-
  recovery rates on some items. Fixed by allowing a manual override and
  documenting the tradeoff (fits-in-context ≠ can-format-reliably).
- **Wrong database file**: the app's `DB_PATH` default resolves to an
  absolute path outside the repo; a real database had silently been living
  there instead of the intended local dev path, causing confusing state
  mismatches during testing. `.env` now pins `DB_PATH` explicitly.
- **Page width inconsistency**: several pages used `max-width` (shrink-to-
  fit) instead of a fixed `width`, causing visible layout jitter across
  pages and screen sizes.

## [0.1.0]

Initial version: poll Bazarr's wanted-subtitle list, translate existing-
language subtitles into missing languages via Ollama or Gemini, reassemble
onto original timing, and upload back to Bazarr — entirely through Bazarr's
REST API, with manual, scheduled, and per-item trigger modes.
