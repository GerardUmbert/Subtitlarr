# Plan: Expose batch-level progress, not just item-level

## Problem (confirmed live, 2026-08-05)

`RunProgress` (app/engine/runner.py) tracks progress at the ITEM level
only — `processed`/`failed` counters increment once per whole item
finishing, not per internal batch. For a run with many small items this
looks fine (the rate/ETA update frequently as items complete). But for a
single large item split into several internal LLM batches by
`srt_io.chunk_cues()` (e.g. a 2216-cue episode split into 7 batches at the
current `nvidia_batch_token_budget`), there is NO progress signal at all
until the entire item finishes — confirmed live: a single-item run sat at
"0 / 1", "Rate 0 files/min", "Est. remaining —" for 5+ minutes while 3 of
7 batches had already completed successfully underneath, because none of
that was visible to `RunProgress`.

## Where the gap is

`app/engine/translator.py`'s `translate_item()` has a `for batch in
batches:` loop that DOES know how many batches exist and which one is
currently in flight — but nothing about that loop reports back to the
`RunProgress` object driving the UI. `_translate_batch()` also isn't
logged/exposed anywhere the Queue or Dashboard page could read from.

## Proposed approach (not yet designed in detail — sketch only)

Some combination of:
- `RunProgress` gains a way to track sub-item progress (e.g.
  `current_item_batch_index` / `current_item_batch_total`), updated by
  `translate_item()`/`_translate_batch()` as each batch completes.
- The Dashboard/Queue "Current run" panel shows "batch N/M" for whatever
  item is actively translating, not just the item-level X/Y count.
- Rate/ETA calculation could optionally weight by batch count instead of
  item count, so a run mixing small and large items gives a more accurate
  estimate (a 7-batch item is roughly 7x the work of a 1-batch item, but
  today's `rate_per_min` treats them identically).

This overlaps conceptually with the also-unbuilt
`plans/batch-history-view.md` (which is about PERSISTED history of past
batches, not live in-progress state) — worth designing both together
since they'd likely share some of the same "batch-level" plumbing through
translator.py, even though one is live/ephemeral and the other is
persisted/historical.

## Status

Not started — observed live 2026-08-05, not yet discussed with the user
beyond noting the symptom. Needs a real design conversation on approach
before building (see sketch above).
