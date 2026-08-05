# Plan: Per-batch translation history

## Goal

A view showing, for each translation attempt, every individual LLM
batch/chunk request that was sent — what dialogue text went out, what came
back, timing, and outcome (success/fallback/error) — not just the final
item-level result we have today.

## What exists today

`item_run_log` records one row per *item attempt* (done/failed), with a
`settings_snapshot` JSON blob (engine, num_ctx, batch budget). It has no
visibility into the individual batches inside that attempt — e.g. we can't
currently see "batch 2 of 3 took 45s and used the fallback engine" after
the fact, only the final outcome.

This gap was directly felt investigating the DeepSeek/NVIDIA repetition-loop
failures (2026-08-05): the raw LLM response IS logged by
`reconciler.reassemble()` on failure (`logger.error(... "Raw LLM response
follows:\n%s", ...)`), but the server's stdout-redirected log file
(`nohup uvicorn ... > data/server.log`) failed to interpolate the `%r`/`%s`
args — only the raw template string showed up, not the actual content.
Root cause not yet confirmed (suspected Windows console encoding issue with
non-ASCII translated text breaking Python logging's lazy `%`-formatting),
but the practical effect was having to reproduce the failure live via a
standalone script just to see what actually repeated. A persisted,
structured batch history — stored in SQLite, not dependent on stdout
encoding — would have made that immediate instead of requiring a
live reproduction.

## Proposed schema addition

New `batch_log` table:
- `id`, `item_run_log_id` (FK to the existing per-item attempt row)
- `batch_index` (1-based position within that attempt's batches)
- `cue_count`, `char_count` (of the sent dialogue_text)
- `engine_used`
- `started_at`, `finished_at`, `duration_ms`
- `status` (`success` / `fallback_used` / `failed`)
- `error_message` (nullable)
- `sent_text` / `received_text` — raw content. OPEN QUESTION (see below):
  store always, or gate behind a setting?

## Where it plugs in

`translator.py`'s `_translate_batch()` loop already iterates one batch at a
time — this is the natural place to log each attempt as it happens,
independent of whether the overall item eventually succeeds or fails. Needs
the `item_run_log_id` to exist before batches start, or logged after the
fact with all batches attributed to the item attempt they belong to.

## UI

Not yet decided — either:
- An expandable row under each Queue & History item, showing its batches
  inline, or
- A new dedicated "Batch History" page/panel

## Open questions (ask before building)

1. Store raw sent/received text always, or gated behind a setting? Tradeoffs:
   - DB size — a large backlog with full raw text per batch could get big
   - Potentially sensitive dialogue content sitting in the DB long-term
   - But: this is exactly what would have made the repetition-loop
     investigation immediate instead of requiring live reproduction
2. Surface as part of the existing Queue & History page (expand a row) or a
   new dedicated page?
3. Any retention limit — keep only the last N batches per item, prune after
   X days, or keep everything indefinitely (same as item_run_log today)?

## Status

Not started. Discussed 2026-08-05, written up per user request — build only
after the open questions above are answered.
