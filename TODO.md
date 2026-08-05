# TODO

## Done this session (not yet restarted into the live server)

- **Fixed 0/N-cues reconciler bug**: gemma3:4b occasionally emits the ENTIRE
  response as literal `\n` escape sequences instead of real newlines, which
  broke the header-line regex entirely (0/62 recovered on "1000 Men and Me:
  The Bonnie Blue Story", en→es, despite correct Spanish translation text).
  Fixed via `_normalize_literal_newlines()` in reconciler.py, regression test
  added.
- **Fixed page width inconsistency**: `main` and `.engine-card` were
  `max-width` (shrink-to-fit), not fixed `width` — caused visible page-size
  jitter across pages/screens. Now fixed width with `max-width:100%` floor.
- **Fixed wrong DB file**: server was silently using `C:\data\subtitlarr.db`
  (config.py's absolute-path default), NOT the repo-local `data/dev.db` file
  being inspected all session — root cause of multiple "why doesn't this
  match what I just did" confusions. Real DB moved to `data/subtitlarr.db`,
  pinned via new `.env` (`DB_PATH=./data/subtitlarr.db`). One file, one truth
  now.
- **Toast notifications**: new `toast.js` + CSS, fires "Translating from X
  to Y…" on Queue page per-item run/re-run, "Translation run started…" on
  Dashboard's Run now.
- **Eager source-language preview**: poller now resolves and stores each
  pending item's would-be source language at poll time (via
  `selector.build_source_map`/`pick_source_language`, same logic
  `resolve_and_gate` uses at actual translate time) — Queue UI shows a real
  language instead of "?" before any translation attempt. Costs one extra
  Bazarr detail call per wanted item per poll (accepted tradeoff). Guarded
  to never overwrite a `done`/`failed` item's real recorded source language
  — preview only applies to `pending` items. `resolve_and_gate` still
  re-resolves fresh at actual translate time; the preview is not a cache it
  trusts.
- **Fixed the real "one good run, next always bad" bug**: NOT Ollama state
  bleeding between requests (each /api/chat call is stateless, confirmed).
  Root cause: the model mixed TWO header formats in a single response — some
  cues as "N\ntext" (matched), others as "N. text" with content on the SAME
  line as the index (did not match — our regex required the header line to
  contain ONLY the index). Silently skipped every inline-content cue,
  recovering just 20/62 on the real captured failure. Fixed by adding
  `_HEADER_WITH_INLINE_CONTENT_RE` alongside the existing header-alone
  pattern in reconciler.py, merged/deduped by position. Verified against
  the exact real captured response: 62/62 recovered (was 20/62).
- **Added a Jobs page** (`/jobs`): shows the cron expression + age threshold
  + next scheduled run, a manual "Run now" button (fires the same age-gated
  `run_scheduled()` job on demand), and a confirm-gated "Clear database"
  action — wipes items/run_history/item_run_log (matches the manual clears
  done earlier tonight) while leaving all app_config/settings untouched.
  Blocked with a 409 while a run is active. New `app/api/jobs.py`,
  `repository.clear_queue_data()`, `jobs.html`/`jobs.js`, sidebar link.
- **Re-run now re-resolves source language fresh against Bazarr** at click
  time (not just from cached/last-known data) — `POST /api/queue/{id}/run`
  does a live `build_source_map`/`pick_source_language` call and returns it
  in the response so the toast shows the CURRENT answer, since a manual
  re-run is often prompted by something having changed on Bazarr's end.
- **Bulk "Run all N matching" on the Queue page**: filter by status/type/
  search, then translate everything in that filtered set in one click.
  Respects the normal daily cap/age gate (not bypassed — a large filtered
  set still can't blow past the GPU-load protections). New:
  `GET /api/queue/matching-count`, `POST /api/queue/run-filtered`,
  `repository.get_translatable_queue_filtered()`,
  `selector.get_filtered_translatable_queue()`, `RunController.run_filtered()`.
  New migration 0003 widens `run_history.triggered_by` CHECK to accept
  `'manual_filtered'` — required rebuilding the table (SQLite has no ALTER
  for CHECK constraints) with a scoped `PRAGMA foreign_keys=OFF` around just
  that migration (item_run_log.run_id references run_history), verified
  safe against both fresh and pre-existing data. Also fixed
  `database.apply_migrations()` to strip full-line SQL comments before
  splitting on `;` — the original naive split had no comment awareness.
- **Settings_snapshot logging**, DB-clearing, etc. — see prior entries below.

## Discussed but not built (needs its own dedicated pass)

- **Jellyfin playback-start webhook** → fast-track translation for missing
  languages on the item just started, bypassing daily cap/age gate (like a
  manual re-run). Key design decision made: do NOT match by file path
  (Jellyfin/Bazarr have different mount roots for the same library — e.g.
  Jellyfin sees `/movies/...`, Bazarr sees `/media/movies/...`). Instead
  match via IMDb/TMDb/TVDb provider IDs, which Jellyfin's webhook payload
  includes and which Bazarr's REST API ALSO returns but our current
  `MovieDetail`/`WantedMovie`/series schemas don't parse yet — confirmed
  live against the real Bazarr instance:
  - `/api/movies` returns `imdbId` and `year` per movie (not in our schema).
  - `/api/series` returns `imdbId` and `tvdbId` per series (not per-episode).
  - `/api/episodes` has NO provider ID — episode matching must go
    series-first (by IMDb/TVDb ID) then season+episode number within that
    series (both systems track these unambiguously).
  Guard condition agreed: only trigger if the item has actual missing-
  language rows in our DB AND isn't already queued/done — naturally
  idempotent against repeated playback-start events (scrub/pause/resume).
  Not started: no webhook endpoint, no schema fields added, no matching
  logic, no tests.

## In progress

- **Slow/broken translations from large batch sizes** — `_batch_token_budget()`
  auto-scales batch size with `ollama_num_ctx` (8192 → ~3496 dialogue tokens/batch).
  Live repro: this recovered only 1/106 cues from the LLM response (`TranslationAlignmentError`),
  where the old flat 900-token batches reliably recovered ~61/61 cues on the same
  `gemma3:4b` model. Small models apparently lose reliable numbered-output formatting
  well before they run out of raw context — fitting in `num_ctx` isn't the same as
  being able to format that much output correctly.
  - [x] Added `ollama_batch_token_budget` setting (0 = auto-derive from num_ctx, >0 = fixed override)
  - [x] Wired through config.py, settings_store.py, translator._batch_token_budget(), runner.py
  - [x] Exposed as an editable field on the Engine settings page (UI + API)
  - [x] Added regression tests (test_batch_budget.py)
  - [x] Tested setting it to 900 against a real translation (Fastball ES→IT) — confirmed
        fix: correct Italian output this time, not the earlier silent-passthrough/1-N-cues bug.
        Slower than the old ~2.5min baseline (~4-5min at 900), but correct.
  - [ ] Once confirmed stable across more items, consider whether the *default* auto-formula
        should be more conservative, not just overridable
  - [x] Added `settings_snapshot` (JSON: engine, num_ctx, batch budget override + resolved
        value) to `item_run_log` per attempt, so future "which config produced this result"
        questions can be read off the DB instead of reconstructed from timestamps vs. server
        restarts (migration 0002, `repository.log_item_attempt`, `translator.translate_item`).
  - [ ] Not yet surfaced in the Queue UI — currently DB-only, readable via item_run_log.
        Would need a per-row expand/detail view to show it in the app itself.

## Bugs found during batch-size investigation (not yet fixed)

- **`/api/run/current` can report stale state after a server restart.** Found `run_id: 1`
  (a run from the previous day, already finished) being reported as the "current" run,
  including briefly showing `active: true`. `RunController.current` is a plain in-memory
  `RunProgress` object — needs investigating why it wasn't `None` on a fresh process, or
  whether some path assigns/mutates it incorrectly. `POST /api/queue/{id}/run` and
  `POST /api/run/now` both silently no-op ("A run is already in progress") if this flag is
  wrongly true, which could make a real run request look like it started when it didn't.
- **Orphaned `run_history` rows with `finished_at = NULL` forever.** Rows id 18 and 25 (at
  least) are stuck open from processes killed mid-batch (server restarts during live
  debugging). `finish_run()` only runs in `run_batch()`'s `finally` block, which a hard
  process kill skips entirely. We already have `reset_stuck_translating_items()` for the
  equivalent problem on `items` (runs at startup) — `run_history` needs the same treatment,
  e.g. mark any run still open at startup as finished/aborted.

## Not started

- **`PORT` env var has no effect.** `settings.port` is defined but never
  read anywhere — the Dockerfile hardcodes `uvicorn --port 8000`. Either
  wire it into the Dockerfile's CMD or drop the setting/document it as
  host-mapping-only.

- [x] **Pre-fetch source subtitles from Bazarr, cached locally** — built
  2026-08-05, see `plans/prefetch-source-subtitles.md` for full details.
  Applies to every run type (not just scheduled). Not yet live-verified
  end-to-end.
- **Per-batch translation history view** — see `plans/batch-history-view.md`.

- [x] **Watchdog for stuck/slow Ollama requests** — implemented in
  `OllamaProvider.translate()` (ollama_provider.py). `WATCHDOG_TIMEOUT_SECONDS = 300`
  (half the old 600s hard timeout). `asyncio.wait_for` cancels a hung request at that
  threshold, `_force_unload_model()` sends `keep_alive: 0` to evict the model from
  Ollama's memory (best-effort — logs a warning and proceeds if the unload itself
  fails), then retries exactly once. If the retry also exceeds the threshold, gives
  up with `ProviderRateLimitedError` rather than looping forever. Regression tests
  in test_ollama_watchdog.py cover: unload+retry-succeeds, gives-up-after-2nd-timeout,
  and doesn't interfere with normal fast responses (no unload call made).
