# Plan: Run NVIDIA batches concurrently within the 40rpm budget

## Goal

Speed up multi-batch items (e.g. a large episode split into 2-3 batches at
the current `nvidia_batch_token_budget`) by firing several batch requests
concurrently instead of one at a time — while respecting NVIDIA's shared
40 requests/minute account-level ceiling.

## Why this is safe (confirmed by reading the code, not assumed)

`translator.py` currently does `for batch in batches: await
_translate_batch(...)` — sequential, one request in flight at a time.

Cue ordering/identity is NOT dependent on request order or arrival order:
- Each batch's dialogue text is built with explicit per-cue indices baked
  into the prompt (`"<index>\ntext"` format, via
  `srt_io.extract_dialogue_text`).
- `reconciler.reassemble()` maps the LLM's response back onto the
  ORIGINAL cue timing/index structure by matching those indices — never by
  response order.
- `translated_subs.extend(batch_result)` just appends whatever comes back;
  final on-disk cue order is always driven by the original SRT's index/
  timing, never by which batch happened to finish first.

So running batches concurrently would NOT lose sorting or index
correctness — this was confirmed by re-reading translator.py/reconciler.py
directly (2026-08-05), not assumed.

## Real risk to weigh before building

Repetition-loop failures (degenerate repeated-output from DeepSeek) have
now been observed 4 times in one session on real content, including on a
NORMAL-sized batch (item 134/135), not just the one oversized 1481-cue
episode. Root cause not yet confirmed — possibly load/timing-related on
NVIDIA's side (a real 504 was also observed on the oversized request).
Introducing concurrent requests changes the request-timing/load pattern in
a way that could make an already-not-understood failure mode worse or
harder to diagnose. Firm decision: do NOT build this until the repetition-
loop root cause is actually understood (see the live investigation this
session, still pending as of 2026-08-05 — blocked by a separate logging bug
where the server's stdout-redirected log fails to interpolate the raw
LLM response text, `%r`/`%s` args silently dropped, only the raw template
showing up in data/server.log).

## Proposed approach (once safe to build)

- Batch NVIDIA requests in windows of 2-3 concurrent `asyncio.gather()`
  calls, rather than fully unbounded concurrency — stays conservative
  against the 40rpm ceiling rather than trying to maximize throughput.
- Needs to interact correctly with the existing reactive 429-handling in
  the (now-removed) Riva provider's design lessons — NVIDIA's chat-model
  provider (`nvidia_provider.py`) doesn't currently have any of that
  proactive/reactive throttling logic at all, since a single chat request
  covers what previously took Riva many small requests. Concurrency would
  reintroduce exactly the kind of burst-request-volume problem that caused
  Riva's 429 loop earlier this session — needs its own throttling design,
  not just "fire N at once."

## Status

Not started, and explicitly blocked on the repetition-loop investigation
completing first. Written up per user's question on 2026-08-05 ("could we
optimize those 40 requests a minute and actually request 2 or 3 at once").
