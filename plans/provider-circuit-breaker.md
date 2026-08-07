# TODO: Circuit-breaker for a provider that's actually down

> **STATUS: core mechanism shipped.** The multi-engine-cascade work
> (`[[multiple-engine-instances-cascade]]`) implemented the "N
> consecutive failures → mark the instance rate-limited for 24h,
> subsequent items skip it" behavior this doc proposes below, plus the
> critical detail this doc's Option B was reaching for: `runner.py`
> rebuilds the cascade FRESH per item, so a trip mid-run is picked up by
> the very next item, not just future runs. See
> `app/db/engine_instances_repo.py` (`record_rate_limited_failure`,
> `get_cascade`) and `app/engine/runner.py`'s `_build_cascade()` closure.
> The sections below are kept for historical context (the original
> incident + design reasoning) but are largely superseded. Remaining
> open thread: `[[local-engine-reload-on-failure]]` (Ollama-specific
> reload-before-strike behavior).

## What happened (live incident)

Ran a 42-item filtered batch against NVIDIA. NVIDIA's endpoint was
degraded — response times climbed steadily across successive calls
(112s → 116s → 117s → 164s → 240s → 245s → 295s) before finally
returning 504 Gateway Timeout. The runner retried once (per existing
`ProviderRateLimitedError` handling), the retry also 504'd, and the item
failed outright — no fallback engine was configured for this run.

The NEXT item started immediately, hit the exact same pattern (multiple
504s, ~15s of retries, fail), and failed too. At that point the run's
own `eta_seconds` had climbed to ~12000 (3.3 hours) for a batch that
would, at a healthy provider's actual response time, take a few minutes
per item. Killed the server to stop it (see AGENTS.md-adjacent history —
no in-app way to cancel a run existed at the time, other than killing
the process).

## Root cause

`app/engine/runner.py`'s `run_batch()` loop (around line 183-219) wraps
each item's `translate_item()` call in its own `except Exception` block
that increments `progress.failed` and moves straight to the next item:

```python
for i, entry in enumerate(ready_items):
    try:
        await translator.translate_item(...)
    except Exception:  # noqa: BLE001 - one item's failure must not abort the batch
        progress.failed += 1
        logger.exception("Translation failed for item %s", item_id)
    ...
```

That per-item isolation is correct and deliberate for the failure mode
it was designed for: one bad subtitle file, one content-block, one
transient blip shouldn't kill an otherwise-healthy 42-item run. But
there is no equivalent protection for the OPPOSITE failure mode: the
provider itself being systemically unreachable/degraded, where every
remaining item is doomed to repeat the same multi-minute failure before
the run ever notices a pattern. Nothing tracks "how many of the last N
attempts against this same engine failed" across items.

## Shape of the fix

Add a lightweight, in-memory circuit breaker scoped to a single
`run_batch()` call (no new DB state needed — this doesn't need to
survive a restart, only to stop one run from burning hours against a
dead endpoint).

### 1. Track consecutive same-provider failures

In `RunProgress` (or a small local variable inside `run_batch()`),
track consecutive failures **against the currently active provider
specifically** — a fallback succeeding should reset the counter, since
that means the system is working as intended (primary down, fallback
covering). Only count failures where the SAME provider failed with no
successful fallback landing:

```python
consecutive_provider_failures: int = 0
```

Increment on each item whose failure reached the top-level `except` in
the loop AND whose `engine_used` (or last-attempted engine, since a
fully-failed item may not have `engine_used` set) matches the active
provider. Reset to 0 on any item that completes successfully via ANY
engine (active or fallback) — a successful fallback proves the run
overall is still making progress, just not via the primary.

### 2. Trip threshold

A configurable-but-sensibly-defaulted threshold, e.g. **3 consecutive
failures** against the same provider with no fallback rescuing them.
Three was picked over one/two because a couple of genuinely unlucky
items (a truly malformed source file, back-to-back) shouldn't trip it —
this needs to be "the provider itself is the problem," not "two bad
files in a row."

### 3. What happens when it trips

Two options, not mutually exclusive — could ship the simpler one first:

**Option A — abort the rest of the run.** Remaining `ready_items` are
never attempted. Their status is left as whatever it already was
(`pending`/`queued`) rather than marked `failed` — an aborted item is
not the same claim as "we tried this and it didn't work," and leaving
it `pending` means a later re-run naturally picks it back up. Log a
single clear line (`"Circuit breaker tripped: N consecutive failures
against {provider}; aborting remaining M items"`) and a new
`run_events.emit(..., "circuit_breaker_tripped", ...)` event so it's
visible in Events/toasts, not just the log file.

**Option B — skip only items for that provider, keep the run alive.**
Only meaningfully different from Option A if a fallback IS configured:
instead of aborting entirely, stop trying the primary for the rest of
this run and route every remaining item straight to the fallback (skip
the "try primary, retry once, then fallback" dance that's now known to
be wasted time). Falls back to Option A's abort behavior if there's no
fallback configured — nothing left to route to. This is more useful
when a fallback exists, since it turns "provider died 20% into a big
batch" into "the rest of the batch quietly finishes on the fallback"
instead of a hard stop.

**Recommendation: implement A first** (simpler, immediately solves the
"why did this burn 3 hours" problem), and treat B as a natural follow-up
once [[multiple-engine-instances-cascade]] lands — B is much more
valuable once cascades are a first-class ordered list rather than a
single optional fallback.

### 4. Surfacing it to the user

- A toast on trip ("NVIDIA failed 3 times in a row — stopping this run
  early. N items left untouched.") — same toast/event pattern already
  used for retry/fallback notifications on the History Events tab.
- The run's final summary (History → Runs) should distinguish "ran to
  completion" from "aborted early by circuit breaker" — today
  `finished_at` + `items_processed`/`items_failed` doesn't capture WHY a
  run had fewer processed items than `total`. Might need a new
  `run_history` column (`stopped_reason: 'completed' | 'circuit_breaker'
  | 'server_restart'`) or could reuse/extend whatever already marks a
  run "closed as stale" (`close-stale-runs` job) if that path already
  has similar plumbing worth sharing.

## Not yet scoped

- Exact threshold value (3 is a starting guess, not derived from data)
  and whether it should be user-configurable (Settings page) or just a
  sensible hardcoded constant.
- Whether "consecutive" should be a strict run of failures or a
  sliding-window ratio (e.g. "4 of the last 5") — strict consecutive is
  simpler and matches the observed incident (every single attempt
  failed the same way), but a sliding window is more robust against one
  fluke success in the middle of a real outage resetting the counter
  and letting the run limp along for hours anyway.
- Whether this should also apply within a SINGLE item's own batch loop
  (an item with 40 batches, all going to the same degraded provider,
  today has no equivalent early-exit either — it just keeps retrying
  batch after batch). Worth deciding if the fix belongs at the
  item-loop level, the batch-loop level, or both.
- Interaction with the existing per-call retry-once behavior
  (`ProviderRateLimitedError`) — should a retry's failure count as ONE
  toward the consecutive total, or does retry-then-fail count double?
  Leaning toward "one" (the item's overall place in the loop is the
  natural unit), but worth confirming once implementing.
