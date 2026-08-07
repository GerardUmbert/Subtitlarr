# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [0.8.6]

### Changed
- **Stopping a run now interrupts an item mid-translation, not just
  between items.** Previously, clicking Stop only took effect once the
  currently in-flight item finished ALL of its batches — a large
  multi-batch item could keep running for minutes after Stop was
  clicked. `_translate_batches()` now takes a `cancel_check` callable,
  polled before every batch (sequential engines) or before every window
  of batches (concurrent engines — a window already in flight via
  `asyncio.gather()` can't be interrupted mid-window, only between
  them). A new `RunCancelledError` marks the interrupted item as
  `failed` with a clear "cancelled" message — a partial translation is
  never uploaded.

### Fixed
- **`.srt`-only source subtitle filtering**: `app/subtitles/srt_io.py`'s
  parser only understands `.srt` structure, but `build_source_map()`
  previously accepted any subtitle path Bazarr returned regardless of
  extension. Confirmed live: Bazarr's own `/api/subtitles/contents`
  endpoint 500'd trying to serve an `.ass` file's content. Non-`.srt`
  source candidates (`.ass`, `.ssa`, `.vtt`, `.sub`, etc.) are now
  filtered out before ever being attempted as a translation source,
  falling back to another available language's `.srt` track if one
  exists instead of failing the item outright.

## [0.8.5]

### Fixed
- **Queue page**: the Language column (`EN → ES`) had no `white-space:
  nowrap`, so once the row's available width got squeezed by the Error
  column's own wrap fix (0.8.4), the arrow/target-language span could
  end up wrapped onto its own line. New `.lang-cell` class keeps the
  whole language pair on one line.

### Added
- A public docs/landing site under `docs/` (index, install, engine
  setup, features with real screenshots, and an auto-generated
  changelog page via `docs/assets/build_changelog.py`), plus a
  `docs-pages.yml` GitHub Actions workflow to deploy it to GitHub Pages
  on every push touching `docs/`. Not yet live — GitHub Pages requires
  the repo to be public first, and Pages itself has no private option
  on the Free plan, so this stays dormant until that's a deliberate
  choice.

## [0.8.4]

### Added
- **Regional language variants**, replacing the old single European-Spanish
  toggle: Spanish (Spain / Mexican / Argentine / generic Latin American),
  Portuguese (Portugal / Brazil), English (American / British), French
  (France / Québécois / Belgian / Swiss), and Chinese (Simplified/Mainland
  / Traditional/Taiwan-HK) each get their own dropdown on the Language
  Rules page, defaulting to that language's own "home" standard except
  English (defaults to American, the more commonly expected target).
  `app/providers/prompts.py`'s `LANGUAGE_VARIANTS`/
  `DEFAULT_LANGUAGE_VARIANTS` registry replaces the old
  `european_spanish` bool everywhere it was threaded through (every
  provider's `translate()`, the whole translator.py cascade/retry chain,
  `/api/config/languages`) — persisted as a single `language_variants`
  dict (`{"es": "es-MX", ...}`) instead of one boolean.
- Unraid template (`unraid/subtitlarr.xml`) `ExtraParams` now defaults to
  `--memory=2g --memory-swap=2g --restart=unless-stopped --log-opt
  max-size=10m --log-opt max-file=3` — a sensible memory ceiling (with
  swap disabled beyond it) and bounded Docker log file growth for a
  long-running background service, instead of shipping with no limits at
  all.

### Fixed
- **History and Queue pages**: a long, space-free error message (e.g. a
  URL-ish string) had nowhere to wrap under `.error-cell`'s inherited
  `white-space: nowrap` with no scroll room, so the browser fell back to
  wrapping it one character per line. Capped with `max-width` +
  `overflow-wrap: break-word` instead.
- **History page**: the expanded run table's `re-run`/`events` action
  columns had the same one-character-per-line wrap once the Error
  column's fix above made more of the table's width contended — fixed
  with `white-space: nowrap` on `.row-action`.
- **History page**: a stray bottom border rendered under the Runs-tab sort
  chips and the Events-tab filter row, left over from `.queue-toolbar`'s
  border-to-table-below styling being reused in a context with no table
  directly beneath it in the same panel.
- **Jobs, Settings, Language Rules, Bazarr Connection pages**: all four
  used `.engine-card`, a class with no background of its own (originally
  meant only as a row inside the Engines page's `.engine-cascade`
  container) — this rendered as a visibly different, unstyled background
  compared to every other page's `.panel`-based cards. Converted to
  `.panel` + a new `.panel-body` class (plain padding, no background,
  unlike `.engine-config`'s inset-expansion look). Language Rules'
  Regional Variants section also gained proper `.field`/`select` styling
  (previously a bare unstyled `<select>`) and now renders as its own
  separate card rather than crammed into the same box as source-language
  priority.
- `.engine-config` (the Engines page's per-instance config-reveal panel)
  changed from a solid `--surface-2` fill to a `--surface-2`-colored
  outline instead.

## [0.8.3]

### Changed
- **Default internal container port changed from 8000 to 7777**, and the
  Unraid template's host-side default now matches it exactly (`7777:7777`
  instead of `7777:8000`). Root cause: Unraid's built-in Tailscale
  integration auto-configures `tailscale serve` to proxy to the
  **host-mapped** port number, not the container's actual internal port —
  a `7777:8000` mapping (custom LAN port, default internal port) left
  Serve pointed at `localhost:7777` inside the container, where nothing
  was listening, so the Tailscale hostname URL silently failed while
  direct `:8000` access still worked. Keeping host and container ports
  identical is what makes Unraid's Tailscale hook target the right port
  automatically, matching how other Unraid-common images (e.g. Immich)
  keep host/container ports matched for exactly this reason.
- `.env.example`'s `PORT` value, `docker-compose.yml`'s port mapping, and
  `app/config.py`'s `port` default all updated to `7777` to match.

## [0.8.2]

### Fixed
- **Container failed to start on Unraid** (`sqlite3.OperationalError: unable
  to open database file`, plus "Could not open /data/subtitlarr.log for
  writing"): the image ran as a fixed non-root uid (1000) baked in at build
  time, but a bind-mounted host `/data` folder's actual ownership comes
  from the HOST, not the image — Unraid's default `nobody:users` (99:100,
  or often root-owned freshly-created appdata folders) didn't match, so
  the container had no write access to its own data directory at all.
  Fixed with a `PUID`/`PGID` entrypoint (`docker-entrypoint.sh`), the same
  pattern LinuxServer.io images use: the container now starts as root,
  `chown`s `/data` to the requested `PUID:PGID` (defaults `1000:1000`,
  Unraid template defaults `99:100`), then drops to that user via `gosu`
  before ever running the app — the app process itself is still never
  root. `docker-compose.yml` and `.env.example` also gained `PUID`/`PGID`.

### Changed
- Unraid template (`unraid/subtitlarr.xml`) trimmed down to only the two
  fields that genuinely can't be set from the web UI — **WebUI Port** and
  **Data** path (plus the new PUID/PGID) — since every other setting
  (Bazarr connection, scheduling, limits, log level) is fully editable
  from the Settings/Bazarr Connection pages after first start and was
  just duplicating the UI in the container form, with real risk of
  drifting out of sync with the app's actual defaults (as the sync-cron
  fields already had).
- Unraid template's default WebUI port changed to `7777` (host-side
  mapping only — the app always listens on `8000` inside the container).
- Bazarr Base URL/API Key are no longer marked required in the Unraid
  form — they can be set from the Bazarr Connection page after first
  start instead, same as every other setting.

## [0.8.1]

### Added
- **Per-item model tracking**: every `TranslationProvider` now exposes its
  real `model` string (e.g. `gemini-3.5-flash-lite`), separate from the
  instance's display `name`. Threaded through the whole translate/retry/
  cascade chain and persisted as `items.model_used` /
  `item_run_log.model_used` (new migration `0009_add_model_used.sql`) —
  distinct from `engine_used` (the instance name), since two instances can
  share a model, or the same instance name can be repointed at a different
  model over time.
- **Model filter + column on the Queue page**: on the **Done** and
  **Pending upload** tabs specifically (where the existing Error column is
  always empty), the column is replaced with **Model**, and a row of
  filter chips (populated from `GET /api/queue/models`, the distinct
  `model_used` values actually seen) lets you isolate everything a
  specific model translated — e.g. everything that fell back to a weaker
  model — and bulk re-run just those via the existing "Run all N
  matching" action, now with a `model` param wired through
  `GET/POST /api/queue`, `/matching-count`, and `/run-filtered`. The Error
  column and its click-to-expand full-error modal are unchanged on every
  other tab.
- `README.md`: **Recommended cascade** section documents stacking 2
  Gemini models × 2 Google accounts for 2000 requests/day, plus a
  quality-first alternative that skips the weaker fallback model. New
  `docs/api-keys-setup.md` walks through getting Gemini/NVIDIA keys and
  the exact per-engine batch token budgets confirmed to work
  (`gemini-3.5-flash-lite`/`gemini-3.1-flash-lite`: 4000, NVIDIA
  DeepSeek V4 Flash: 700, Ollama `gemma3:4b`: 400), including a table
  mirroring the actual Translation Engine page cascade order.
- `.github/workflows/ci.yml` (pytest on push/PR) and
  `docker-release.yml` (build + push to GHCR on `vX.Y.Z` tags, matching
  the existing tag convention) — the project now has CI and an automated
  Docker release pipeline for the first time.

### Changed
- README, `.env.example`, `docker-compose.yml`, and the Unraid template
  (`unraid/subtitlarr.xml`) rewritten to drop the old `ACTIVE_ENGINE`/
  `FALLBACK_ENGINE`/per-provider-type env vars removed in 0.8.0's cascade
  rewrite (they'd gone stale, still describing the pre-cascade setup) and
  point at the Translation Engine page and the real published
  `ghcr.io/gerardumbert/subtitlarr` image instead of a placeholder
  username.

### Removed
- `plans/` and `TODO.md` are no longer tracked in git (internal working
  docs with no value to someone pulling the published image) — gitignored
  going forward, still present locally.

## [0.8.0]

### Added
- **Multiple engine instances with an ordered fallback cascade**, replacing
  the old single `active_engine`/`fallback_engine` model. Any number of
  independently-configured, individually-named instances (e.g. two
  separate Gemini API keys) can now be added, reordered by drag-and-drop
  on the Engines page, and enabled/disabled — a translation tries each
  enabled instance top-to-bottom until one succeeds. A **separator** row
  can be inserted anywhere in the list to stop the cascade at that point
  (e.g. "no fallback at all" = a separator right under the first
  instance); everything below a separator is excluded from the fallback
  walk regardless of its own enabled state.
- **Automatic 24h rate-limit cooldown per instance**: 3 consecutive
  `ProviderRateLimitedError`s (429s, timeouts, transient 5xx) against the
  same instance mark it rate-limited for 24 hours, and the cascade
  builder skips it without a live round-trip. A successful manual "Test
  connection" clears the cooldown early. Deliberately NOT a usage/quota
  meter — no RPD/TPM counters, no per-provider reset-timezone tracking,
  just a blunt "this looked dead, leave it alone for a while" signal (see
  `plans/multiple-engine-instances-cascade.md` for why the original
  usage-tracking design was dropped in favor of this simpler mechanism).
- New `app/db/engine_instances_repo.py` (CRUD + cascade-building +
  rate-limit-cooldown queries) and `app/api/engine_instances.py`
  (`GET/POST /api/config/engine-instances`, `PUT`/`DELETE .../{id}`,
  `POST .../reorder`, `POST .../{id}/test`) — replaces the old
  `active_engine`/`fallback_engine` Settings fields and the per-provider-
  type config fields (`gemini_api_key`, `nvidia_batch_token_budget`,
  etc.), which are all removed. `app/providers/registry.py`'s `_build()`
  is now `build_provider(provider_type, config_dict, instance_name=...)`,
  reading from an instance's own `config_json` instead of global
  `Settings` fields; `TranslationProvider.name` is now a per-instance
  display name (settable at construction) distinct from the fixed
  `provider_type` used for concurrency/behavior decisions.
- `app/engine/translator.py`'s three near-identical retry/fallback
  `except` blocks (rate-limited, content-blocked, unreliable-response)
  are now one shared `_try_cascade()` helper that walks an ordered
  `cascade: list[TranslationProvider]` instead of a fixed `active_provider`/
  `fallback_provider` pair — same retry-once-then-fallback behavior,
  generalized to any number of fallback instances.
- The Engines page is now a **reorderable list of instance cards**
  (drag-and-drop, browser-native `draggable`) instead of one radio button
  per provider type — each card shows a rate-limit status badge, and an
  "+ Add engine" menu creates a new instance of any provider type (or a
  separator) at the end of the cascade.
- **The engine cascade is now rebuilt fresh for every item in a run**,
  not once at run start — confirmed live this was necessary: after an
  instance tripped its rate-limit cooldown mid-run, every subsequent
  item was still trying it first and paying for a guaranteed-to-fail
  request plus the retry wait before falling back, because the cascade
  snapshot taken at the start of the run never noticed the trip.
- **Stop button** for an in-progress run (Dashboard's Current Run panel)
  — stops after the in-flight item finishes (never mid-item), leaving
  remaining items untouched (`pending`/`queued`, not marked failed)
  rather than requiring a full server restart to interrupt a run.
- **Ollama's reload-on-failure (force-unload + retry once) now covers
  any server-responded-but-stuck/errored failure** — a watchdog timeout,
  an httpx-level timeout, or a 5xx response — not just watchdog timeouts
  as before. A `ConnectError` (Ollama unreachable) still skips reload
  and fails immediately, since there's no loaded model state to clear if
  the process was never reached in the first place. llama.cpp
  intentionally has no equivalent (no reload/restart endpoint exists).
- **"Clear all rate limits" job** (Jobs page, manual-only, no cron) —
  immediately un-flags every engine instance currently in its 24h
  rate-limit cooldown. For when a trip turns out to be a false positive
  rather than genuine exhaustion (see the burst-debounce fix below) or
  the underlying issue's already fixed, without waiting per-instance for
  a Test Connection or the full 24h.
- **Gemini 429 responses now log their full response body**, not just
  the bare status code — needed to actually diagnose a live session
  where the account's AI Studio dashboard showed RPM/TPM/RPD headroom
  while the API kept returning real 429s (most likely the dashboard's
  "last hour" view scoping to requests within that hour rather than
  cumulative usage against the daily cap, not a bug in this app —
  investigation deferred to a fresh-quota test).

### Fixed
- **Engines page**: dragging a card no longer hijacked text selection
  inside its input fields (the whole card was `draggable`; now only its
  ⠿ handle is), the "+ Add engine" dropdown menu was invisible (clipped
  by its parent's `overflow: hidden`), and a card dragged without moving
  stayed stuck at reduced opacity.
- **A healthy engine could trip its 24h rate-limit cooldown from a single
  burst, not sustained exhaustion.** Confirmed live: a Gemini account
  with plenty of RPM/TPM/RPD headroom (per its own AI Studio dashboard)
  still got flagged, because several batches fired concurrently
  (`concurrent_batch_window`) all 429'd within milliseconds of each
  other on a short burst limit distinct from the rolling per-minute
  average, and each one independently counted as its own strike —
  turning one burst event into 3 "consecutive" failures. Failures within
  5 seconds of each other (`BURST_DEBOUNCE_SECONDS`) now collapse into a
  single strike; only failures spaced further apart advance the counter.
  New `engine_instances.last_failure_at` column tracks this
  independently of `updated_at`, which is also touched by unrelated
  writes (a name/config edit) that must never be mistaken for a recent
  failure.

### Removed
- `active_engine`/`fallback_engine` and all per-provider-type Settings
  fields (`ollama_base_url`, `gemini_api_key`, `nvidia_batch_token_budget`,
  etc.) — engine configuration lives entirely in the `engine_instances`
  DB table now. No migration path from the old settings was written
  (pre-release software, no installs to preserve) — a fresh install
  starts with an empty engine list and instances are added through the
  Engines page.

## [0.7.0]

### Added
- **Clickable column-header sorting** on Queue (Title/Language/Status/
  Updated) and History's Runs (sort chips for Started/Duration/Files/
  Failed) and Events (Time/Item/Engine/Type) tables — server-side and
  paginated, not a client-side re-sort of whatever page happens to be
  loaded. Column/direction are validated against a per-table allowlist
  before ever reaching SQL.
- **Optional API key for llama.cpp** (Engines page, blank by default):
  llama.cpp's own server has no built-in auth, but a remote instance
  sitting behind a reverse proxy/gateway can enforce its own — confirmed
  live with a friend's llama.cpp instance exposed over a Tailscale
  Funnel, gated by a bearer token in front of it. Sent as
  `Authorization: Bearer <key>` on every request when set; left blank,
  no Authorization header is sent at all, matching llama.cpp's default
  unauthenticated behavior. (An equivalent feature was first built and
  then reverted for Ollama earlier in this same work, after the actual
  remote instance turned out to be llama.cpp, not Ollama.)
- **History page now has three tabs: Runs, Events, Stats.** Runs is the
  existing run-by-run breakdown (now with a "re-run" button — see below).
  Events is a searchable feed of individual translator log lines
  (per-batch send/response, rate-limit retries, content-block fallbacks,
  Ollama watchdog restarts, item failures), filterable by item id, event
  type, or engine, with a "view events" link from each run's expanded
  item row so a specific item's whole request history can be inspected.
  Stats shows items per target language, queue status totals, per-engine
  fail ratio, per-engine p50/p90 response time, and fallback counts
  (e.g. "gemini → ollama: 4"), with a 7-day/30-day/all-time range filter.
- **New `app/engine/log_events.py`** parses the app's own log file into
  structured events via a small pattern table (one regex per known log
  message shape) rather than free-text search — the same "map of known
  values" approach already used for provider error explanations.
  **New `app/engine/stats.py`** computes the Stats tab's aggregates:
  item/status counts from the DB, response-time percentiles and fallback
  counts from the parsed log (`item_run_log` only ever stores one
  terminal timestamp per attempt, not individual call durations or which
  engine a fallback landed on).
- **A real rotating log file** (`RotatingFileHandler`, 5MB × 3 backups,
  alongside the SQLite DB in the same persistent volume). Previously the
  app only logged to stdout — in this dev session a log file existed
  purely as a side effect of how the server happened to be launched
  (`nohup ... > file.log`), which would not exist at all under a normal
  Docker/Unraid deployment where stdout goes to `docker logs`. The
  Events tab needs a real, persistent, bounded file in every deployment.
- **"Re-run" button on the History page's expanded run view**, mirroring
  the Queue page's per-item run action — History was previously
  read-only, so a failed item found while reviewing a past run couldn't
  be retried without navigating away to the Queue page and re-finding it
  there.
- **Collapsible sidebar.** A toggle button collapses the nav down to an
  icon rail (plain Unicode glyphs — no icon library added) and persists
  the collapsed/expanded state in `localStorage`, restored before first
  paint so there's no flash of the wrong layout on reload. Icons use
  distinct glyphs per page (Jobs: ▶, Settings: ⚙, etc. — an early pass
  had these backwards).

### Fixed
- **Repetition-loop failures never triggered fallback.** A degenerate
  response (10+ consecutive cues with identical translated content) is
  caught by `reassemble()` AFTER `translate()` already returned
  successfully — so it fell entirely outside the
  `ProviderRateLimitedError`/`ProviderContentBlockedError` fallback
  handling, which only wraps the `translate()` call itself. Confirmed
  live: several items failed outright on Gemini repetition loops with
  Ollama configured as fallback but never attempted. Now retries once
  against the fallback provider (keyed by engine name, so it can't bounce
  back to the provider that just produced the bad output).
- **`rate_per_min`/ETA on the current-run panel was a whole-run
  cumulative average**, not a current rate — a single slow item early in
  a long run (a 300s watchdog timeout, a content-block-then-fallback
  chain) permanently dragged the displayed rate down for the rest of the
  run even after throughput fully recovered. Now uses a rolling window
  of the last 20 completions.
- **Stats tab showed test-fixture "engines" (`echo`, `fake`,
  `fake-failing`) alongside real ones, with nonsense response-time
  percentiles.** Root cause: `app.main`'s import-time `configure_logging()`
  wrote to the SAME log file (`data/subtitlarr.log`) that both the live
  server and every local `pytest` run share, so every test run's fake
  translate() calls were appended into production's own event log.
  `configure_logging()` now detects pytest (`PYTEST_CURRENT_TEST`/
  `PYTEST_VERSION` env vars) and skips the file handler entirely in that
  case. Also hardened `stats.py`'s duration calculation to only count
  "response" events for items that ultimately succeeded (`item_run_log`
  status `done`) — a fast reply that was later rejected (content-blocked,
  alignment failure) isn't a representative "this engine answers in Xs"
  data point. The pre-existing polluted log was rotated out to a backup
  file so the Events/Stats views start clean.
- **Models softened profanity instead of translating it directly** —
  confirmed live comparing the same source against llamacpp and ollama:
  a repeated "fuck" translated to euphemistic "besaría" (would kiss)
  instead of the real Spanish equivalent. The system prompt now
  explicitly instructs faithful, uncensored translation of profanity and
  vulgar language, matching the source's intensity rather than
  substituting a milder alternative. When Catalan's Vegeta-insults addon
  is also active, its instruction to adapt rather than translate
  literally now explicitly overrides this rule, instead of the two
  competing silently based on prompt ordering alone.
- **Main content area didn't adapt to narrower viewports.** `main` had a
  fixed `width: 1180px` (not `max-width`) inside a bare `1fr` grid track
  — a bare `1fr` track's implicit min-width is `auto`, not 0, so the
  column couldn't shrink below that fixed width regardless of available
  space. Combined with `body { overflow-x: hidden }`, content just
  clipped instead of reflowing or scrolling. Fixed via `max-width` +
  `minmax(0, 1fr)` grid tracks, plus defensive `overflow-wrap: anywhere`
  on text containers.
- **Sidebar stretched taller than the viewport on long pages**, scrolling
  away with the page instead of staying pinned — it had no height
  constraint of its own, so it filled `.shell`'s grid row instead of the
  actual screen. Fixed with `position: sticky; top: 0; height: 100vh`
  (with a mobile-breakpoint override, since below 980px the sidebar
  becomes a horizontal top bar instead of a side column).
- **Events tab toolbar was misaligned, and its engine filter required an
  exact match.** Added `align-items: center` to the wrapping toolbar;
  engine filtering is now a case-insensitive substring match against
  both the primary and fallback engine, not an exact match.

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
