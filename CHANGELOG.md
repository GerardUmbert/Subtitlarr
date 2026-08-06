# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [0.6.0]

### Added
- **llama.cpp translation engine** (Engines page): a new LOCAL provider
  option alongside Ollama, reached via llama.cpp's own built-in HTTP
  server (github.com/ggml-org/llama.cpp) — a separate local runtime from
  Ollama, not another name for it. OpenAI-compatible request format like
  the cloud providers, but treated operationally like Ollama: no API key,
  no rate limits, no windowed concurrency, its own watchdog-timeout-and-
  retry pattern. No web UI and no model-switching endpoint — the server
  is started with one fixed model already loaded via its own CLI flags,
  so there's no model field or pull button on this page.
- **"European Spanish, not Latin American" toggle** (Language Rules page,
  **enabled by default**): the bare "Spanish (es)" language code is
  ambiguous between European (Castilian/Peninsular) and Latin American
  Spanish, and a real translation confirmed live defaulted to Latin
  American colloquial phrasing ("¿Qué anduvo Missy ahora?") with no way
  to have requested otherwise. Threaded through every provider's
  `translate()` and read live per-item from Language Rules' saved config,
  same pattern as the existing Catalan Vegeta-insults toggle — but
  opt-out instead of opt-in.
- **Click-to-expand full error detail** on the Queue and History pages:
  a failed item's error cell is now clickable, opening a modal with the
  complete underlying error (e.g. a provider's full raw JSON response),
  not just the short summary already shown in the table. New
  `error_detail` column on both `items` and `item_run_log`
  (`ProviderError.raw_detail`), separate from the short human-readable
  `error_message` so a long raw response never bloats the table itself.
- **Content-policy blocks now trigger automatic fallback.** A new
  `ProviderContentBlockedError` (Gemini's `PROHIBITED_CONTENT`/`SAFETY`
  block reasons, mapped to human-readable explanations instead of an
  opaque `KeyError`) is fallback-eligible — unlike a generic
  `ProviderError`, which the runner never retried or fell back on at
  all. Confirmed live: a real batch failed outright on Gemini with a
  fallback engine configured but never even attempted, since content
  blocks weren't a recognized retryable/fallback case. No same-provider
  retry step (pointless — the content won't stop tripping the same
  filter), goes straight to the fallback provider if one is configured.
- **Explicit "sending" log line before every translate() call.** Both
  httpx's own request logging and the existing completion-timing log
  only fire AFTER a response comes back, so there was no way to confirm
  "is this request actually in flight" from the log alone — confirmed
  live, a real stuck-looking request required inspecting live TCP
  connections (`Get-NetTCPConnection`) to confirm it had genuinely
  reached the provider's servers. The new line fires immediately before
  the call, with the batch index/total and character count.
- Temporary read-only `/api/debug` endpoints for inspecting a Bazarr
  episode's raw subtitle detail (embedded tracks, external file paths)
  and any subtitle's actual parsed content, without needing to hand the
  Bazarr API key to whoever's debugging — added to investigate a real
  incident (see Fixed) and left in as a standing diagnostic tool.

### Fixed
- **Root-caused a real source-language contamination incident**: several
  "Georgie & Mandy's First Marriage" episodes had only an Italian
  (fansub) external subtitle file in Bazarr, with a genuine English
  track existing only as an EMBEDDED (baked-in) stream — Bazarr's
  "treat embedded subtitles as downloaded" setting suppressed the
  missing-subtitle nag but never materialized the track as an external
  file `build_source_map()` could see, so the source-language picker
  correctly (per its own logic) fell back to the contaminated Italian
  file. Not a Subtitlarr bug — confirmed via the new debug endpoints
  that Bazarr's API already reports embedded tracks with `path: null`
  distinctly from real external files; the fix was extracting the
  embedded track in Bazarr itself. Of 19 already-uploaded episodes
  checked directly against Bazarr's live content, only 1 (still
  un-pushed, `translated_pending_upload`) was actually affected.
- **Gemini model default was pointing at a model with ZERO free-tier
  quota.** `gemini-2.0-flash` showed 0 RPM / 0 TPM / 0 RPD on a real
  account's AI Studio rate-limit dashboard — every request 429'd
  instantly, which looks identical to a batch-size/rate-limit problem
  but is actually "this model isn't available to you at all." Switched
  default to `gemini-3.5-flash-lite` (confirmed live: 15 RPM / 250K TPM
  / 500 RPD, the best free-tier numbers of any text-output model at time
  of writing) and raised `gemini_batch_token_budget` back up (250K TPM
  gives enormous headroom vs. Groq's confirmed 6000 TPM cap) and
  `gemini_concurrent_batch_window` to 3.
- **Groq's real constraint is a 6000 TPM cap, not just its documented
  RPM/RPD numbers** — confirmed live via Groq's own 429/413 error body
  naming the exact limit. The default `groq_batch_token_budget` (4000
  dialogue tokens, ~9200 total with prompt/response overhead) already
  exceeded it on the FIRST request. Lowered to 1800 and
  `groq_concurrent_batch_window` to 1 (a TPM cap is a single rolling
  budget shared across concurrent requests, so concurrency made hitting
  it MORE likely, not less).
- **Gemini's API key was leaking into plaintext logs** via the
  `?key=...` query-string auth method — every request URL, including
  the full key, ended up verbatim in httpx/uvicorn's access logs.
  Switched to the `x-goog-api-key` header, which Google's REST API
  accepts as an equivalent alternative specifically to avoid this.
- **Failed item_run_log rows never recorded which engine was used**,
  because `engine_used` was only ever set on the SUCCESS path — a run
  that failed outright showed `"primary_engine": null` on the History
  page instead of naming the engine that actually failed. Now set from
  `active_provider.name` on both failure paths.
- OpenRouter/NVIDIA/Groq/Gemini's per-engine batch-token-budget and
  concurrency settings are now selected via a single lookup table in
  `runner.py` instead of a growing if/elif chain, so a newly added cloud
  provider can no longer silently inherit Ollama's small GPU-safe
  default by omission (the exact gap that caused the Groq/Gemini batch-
  size bugs above).

## [0.5.0]

### Added
- **Groq translation engine** (Engines page): a new provider option
  alongside Ollama/Gemini/NVIDIA/OpenRouter, reached via Groq's
  OpenAI-compatible `/openai/v1/chat/completions` endpoint
  (`app.providers.groq_provider`). Defaults to `llama-3.1-8b-instant`
  (confirmed to handle Catalan translation, and Groq's most generous
  documented free-tier limits: 30 requests/minute, 14,400/day — far
  looser than OpenRouter's free-tier 20 RPM / 50-per-day). Gets the same
  windowed-concurrency batching and its own batch-token-budget setting as
  NVIDIA/OpenRouter (`groq_batch_token_budget`/`groq_concurrent_batch_window`,
  both default 4000/4). Groq serves a fixed lineup on its own LPU
  hardware (Llama, GPT-OSS, Qwen, etc. — no Gemma or DeepSeek).
- **Gemini engine upgraded to the same reliability pattern as NVIDIA/
  OpenRouter/Groq**: previously had no shared rate-limit cooldown gate, no
  connection-error retry handling, and no per-engine batch-token-budget/
  concurrency settings (silently inherited Ollama's small GPU-safe
  default and ran strictly sequential batches). Now has its own
  `gemini_batch_token_budget`/`gemini_concurrent_batch_window` settings
  and a shared 429 cooldown (`GeminiProvider._rate_limited_until`) so a
  rate limit on one call makes every other in-flight batch/item wait at
  the same gate, instead of each one independently hitting its own 429.
- **OpenRouter translation engine** (Engines page): a new provider option
  alongside Ollama/Gemini/NVIDIA, reached via OpenRouter's OpenAI-
  compatible `/chat/completions` endpoint (`app.providers.openrouter_provider`).
  Defaults to the free-tier `google/gemma-4-26b-a4b-it:free` model so a
  fresh install needs no spend to try it. Gets the same NVIDIA-style
  windowed-concurrency batching (`openrouter_concurrent_batch_window`,
  default 4) and its own batch-token-budget setting
  (`openrouter_batch_token_budget`, default 4000 — kept high since free
  models are capped at 50 requests/DAY in addition to 20/minute, so
  request count matters far more here than for NVIDIA). A 429 from the
  per-minute cap is retried automatically like any other provider; a 429
  from the daily cap raises a distinct, non-retryable
  `OpenRouterDailyLimitError` instead, so the runner fails that item
  immediately rather than looping retries against a quota that won't
  reset until the next day.
- **Catalan "Vegeta-style" insult translation** (Language Rules page): an
  optional toggle that, when translating into Catalan, adapts insults and
  profanity into the proud, colorful, non-literal style of Vegeta's
  Catalan dub (TV3's Bola de Drac Z) instead of a literal translation.
  Confirmed live that DeepSeek V4 Flash already recognizes this specific
  cultural reference and produces natural, in-character adaptations from
  a style description alone — no hardcoded phrase list needed. The prompt
  explicitly requires matching the ORIGINAL insult's intensity/context
  (a mild jab gets a milder Vegeta-style line, a harsh insult gets a more
  severe one), not an arbitrary pick from the style. Only affects insult/
  profanity lines — the rest of the translation stays normal and accurate.
  New `app.providers.prompts.CATALAN_VEGETA_INSULTS_ADDON`, threaded
  through every provider's `translate()` and read live per-item from
  Language Rules' saved config (not locked in at run start).

### Fixed
- **Live toast notifications no longer replay the entire historical event
  backlog on every page load.** `run_events.py`'s in-memory buffer
  (up to 500 events, survives across page loads for the life of the
  server process) was always polled starting from event id 0, so opening
  Dashboard/Queue would immediately fire a toast for every retry/failure
  from old runs, not just new ones. The frontend (`run-events.js`) now
  calls a new `GET /api/run/events/latest_id` endpoint to seek to the
  current tip before starting to poll, so only events emitted after the
  page opened produce a toast.
- **OpenRouter items no longer silently ran with Ollama's small,
  GPU-safe batch-token-budget and no concurrency**, even after the
  OpenRouter engine was added — `runner.py`'s per-engine batch-budget
  selection only special-cased `"nvidia"`, and `translator.py`'s
  windowed-concurrency path was hardcoded to NVIDIA only. Both are now
  generalized (`_CONCURRENT_PROVIDERS` in `translator.py`) so OpenRouter
  gets its own configured batch size and concurrent-batch window, same as
  NVIDIA.
- **"Push queued uploads" no longer blocked while a translation run is
  active** — it only touches items that already finished translating and
  are sitting in the upload queue, which a live run never writes to
  mid-progress, so the guard was unnecessarily conservative.
- **Removed the dead "Managed languages" field** from Language Rules — it
  was saved but never actually read anywhere in the translation pipeline;
  target languages always come directly from whatever Bazarr itself
  reports as missing, not from Subtitlarr's own config.
- **Source language priority now defaults to `[\"en\"]`** on a fresh
  install instead of an empty list.
- **Items held as "pending upload" now show a real duration** on the
  Queue page instead of "—" — `completed_at` is stamped when translation
  actually finishes, not deferred until the later "push queued uploads"
  action, and pushing to Bazarr no longer overwrites that original
  timestamp with the (much later) push time.
- Jobs page: "Sync wanted / missing" and "Pull pending subtitles" now show
  their own cron expression and next scheduled run, same as "Translate
  next batch" already did — previously only the main translation job
  surfaced this even though both sync jobs got independent crons in 0.4.0.
- Settings page's translation-schedule card was still titled "Schedule"
  after Jobs renamed its counterpart to "Translate next batch" — renamed
  to match.
- **Queue page's "Hide items with no source subtitle" now defaults to
  checked** — was unchecked by default, so a fresh page load showed every
  untranslatable item until toggled manually. The filter's URL-round-trip
  is now explicit both ways (`exclude_no_source=1`/`0`), since silently
  omitting the param when unchecked would have let a reload re-apply the
  new default over a deliberate uncheck.
- **`docker-compose.yml`**: `OLLAMA_BASE_URL` was hardcoded to the bundled
  `ollama` service, silently ignoring the env var if set — now respects
  `${OLLAMA_BASE_URL}` like every other setting, defaulting to the bundled
  container only when unset. Also dropped `depends_on: [ollama]` — Ollama
  isn't a hard dependency (Gemini/NVIDIA need no local model server at
  all), so requiring it to be defined/running was wrong whenever a
  cloud engine is active.
- **`app.bazarr.client.get_subtitle_contents()` now raises a clear
  `BazarrError`** (including a snippet of the actual response body)
  instead of crashing on a bare `JSONDecodeError` when Bazarr returns a
  200 OK with a non-JSON body — seen live (empty body for one real
  source file; a separate live report also saw an HTML page returned
  instead of JSON, still under investigation).

## [0.4.0]

### Added
- **Optional deferred-upload queue**: a new "Queue uploads instead of
  pushing immediately" setting caches a successful translation's output
  locally (`translated_pending_upload` status, new scratch dir
  `subtitlarr-upload-queue/`) instead of uploading to Bazarr right away —
  a new "Push queued uploads" job on the Jobs page then sends everything
  queued to Bazarr in one batch. Lets a whole translation run finish
  without waking a sleeping NAS (Bazarr's own handling of the upload is
  what wakes it), batching that wake-up into one deliberate push instead
  of once per item. New `app/engine/upload_queue.py`,
  `POST /api/jobs/push-uploads`, migration 0004 (widens `items.status`).
- **Independent daily crons for the two Bazarr sync jobs**: "Sync
  wanted/missing" and "Pull pending subtitles" can now run on their own
  schedule (default `40 9 * * *` for both), separate from the main
  translation cron — configurable per-job from Settings, or left blank to
  stay manual-only via the Jobs page as before. `CronScheduler` now
  supports multiple independently-managed named jobs instead of a single
  hardcoded one.
- **Startup cleanup for stale open runs**: `run_history` rows left with
  `finished_at IS NULL` by a process killed mid-batch are now closed out
  automatically at startup (mirrors the existing stuck-item recovery),
  with counts backfilled from `item_run_log`. Non-destructive — unlike
  "Clear database," the run and its item history are kept, just marked
  finished. Also exposed as an on-demand `POST /api/jobs/close-stale-runs`.
- Jobs page action names now describe what they actually do: "Translate
  next batch" (was "Scheduled job" — now also shows the configured daily
  limit), "Sync wanted / missing from Bazarr" (was "Sync media"), "Pull
  pending subtitles (sources) from Bazarr" (was "Sync subtitles").

### Fixed
- **Responsive layout was broken below 980px**: the sidebar used to
  vanish entirely (`display:none`) with no way to reach other pages: it's
  now a collapsible top bar with a working hamburger toggle. Also fixed a
  CSS grid-track sizing bug that let wide table content force the whole
  page to scroll horizontally instead of just the table itself, and a
  grid-stretch bug that made the collapsed sidebar visually balloon to
  match the page's full height on tall pages.
- **Docker base image switched from `python:3.12-alpine` to
  `python:3.12-slim`**: pydantic-core's Rust-based wheels don't reliably
  cover musl/Alpine, which was causing real build failures. Also updated
  `docker-compose.yml` and `.env.example`, which had drifted out of sync
  with several engine/scheduling settings added since they were last
  touched (NVIDIA engine, daily limit, pause-between-items, queue-uploads,
  sync crons).

### Added (deployment)
- **Unraid Community Applications template** (`unraid/subtitlarr.xml`)
  documenting every environment variable as a proper UI field, plus
  README guidance on building/pushing your own image and picking a Docker
  network type that can reach Bazarr/Ollama.

## [0.3.0]

### Added
- **New History page** (`/history`): every past translation run, newest
  first, as an expandable card showing total files/succeeded/failed and
  total elapsed time, tagged with the engine used (and a "+1 via gemini"
  style note if a run's items used more than one engine, e.g. via
  fallback). Expanding a run shows each individual item's status, engine,
  per-item elapsed time, and error message. The Queue page is now
  Queue-only (the "& History" framing didn't match what it actually
  showed — a live/current-state table, not grouped past runs) and links
  to the new page. New `GET /api/history`, `GET /api/history/{id}/items`,
  `repository.list_run_history()`, `repository.get_run_items()`.
- **Granular timing logs for NVIDIA translation steps**: source
  read+parse, `chunk_cues()`, each individual batch's `translate()` call,
  and each concurrent window's total time are now logged — added to
  actually pin down a real, reproducible 60-135s gap observed live
  between LLM requests that had no corresponding explanation in the
  existing httpx-only request logging (which only logs AFTER a call
  completes, so a slow DNS/TLS/connection-setup phase before the request
  is even sent was completely invisible). Root cause not yet identified;
  this instrumentation is the next step toward finding it.
- **NVIDIA batches now translate concurrently** (window of 4 at a time),
  instead of one at a time like every other engine. NVIDIA-only —
  deliberately NOT applied to Ollama (local GPU contention would mean no
  real speedup, and it fights the watchdog/timeout logic built around one
  request at a time) or Gemini. Correctness is guaranteed two ways: each
  cue carries its own real subtitle index baked into the LLM prompt, and
  `reassemble()` maps translated content back onto the original cue list
  by matching that index rather than by response order; and
  `asyncio.gather()` itself always returns results in the same order as
  its inputs regardless of which one resolves first. A batch that hits a
  429 inside a concurrent window falls back to the fallback engine
  independently, same as the existing sequential per-batch behavior — no
  new coordination needed, since NVIDIA's rate limit responds near-
  instantly rather than after a long wait.
- **Jobs page: "Sync media" and "Sync subtitles" actions**: two new
  on-demand jobs alongside the existing "Run now" — "Sync media" refreshes
  Bazarr's wanted-list metadata only (no subtitle content, no
  translation); "Sync subtitles" resolves source language and pre-fetches
  subtitle content into the local scratch cache for every pending item
  (see the caching feature below), without starting any translation. Lets
  the cache be warmed ahead of time, decoupled from actually running a
  translation.
- **Page loading states**: every page now shows a brief loading spinner
  while its initial data fetch is in flight, instead of rendering
  default/empty content first and having the real data visibly pop in a
  moment later.
- **Pre-fetch source subtitles once per run, cached locally**: every run
  (scheduled, manual full, filtered, single-item) fetches all its items'
  source subtitle content from Bazarr in one concurrent burst up front,
  caching it in a shared scratch directory in the container's own
  ephemeral temp directory (`tempfile.gettempdir()/subtitlarr-scratch` —
  never the persistent `/data` volume, and no extra Docker mount needed),
  instead of hitting Bazarr (and therefore the NAS's disk) once per item
  spread out over the whole run. Only the actual subtitle CONTENT read is
  cached — polling and source-language resolution stay lightweight calls
  against Bazarr's own database and don't touch the NAS's disk either way;
  uploading a finished translation always writes through immediately, not
  deferred. `translate_item()` reads from the cache when available and
  transparently falls back to a live Bazarr fetch otherwise (e.g. a
  prefetch that failed for one item). A successful item's cached file is
  deleted right after upload; a FAILED item's cache is deliberately left
  in place — and unlike an earlier version of this feature (which used a
  per-run_id scratch subfolder, silently orphaning a failed item's cache
  the moment the run that fetched it ended), the shared flat directory
  means a LATER run's prefetch will actually find and reuse that leftover
  file instead of re-fetching it. The Queue page shows a "cached" badge
  next to any item whose source is currently sitting in the local cache.
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
