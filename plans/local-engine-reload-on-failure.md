# Plan: Reload-on-failure for local engines (Ollama/llama.cpp)

> **STATUS: implemented.** See `app/providers/ollama_provider.py`'s
> `translate()` — reload+retry now covers watchdog timeout, httpx-level
> timeout, and 5xx responses (previously watchdog-timeout only);
> `httpx.ConnectError` still skips reload and fails immediately, per the
> resolved design below. llama.cpp intentionally unchanged (no reload
> capability). Tests: `tests/unit/test_ollama_watchdog.py`.

## Context — what's already shipped

The cooldown mechanism the old `provider-circuit-breaker.md` plan was
proposing now exists, via the multi-engine-cascade work:

- Every `engine_instances` row carries `consecutive_failures` and
  `rate_limited_until` (see `app/db/engine_instances_repo.py`).
- `record_rate_limited_failure()` increments the counter on each
  `ProviderRateLimitedError`; at `RATE_LIMIT_FAILURE_THRESHOLD` (3), it
  sets `rate_limited_until = now + 24h` and resets the counter.
- `engine_instances_repo.get_cascade()` filters out any instance whose
  `rate_limited_until` is still in the future.
- Critically, `runner.py`'s `run_batch()` loop rebuilds the cascade
  **fresh for every item**, not once at run start — so the moment an
  instance trips mid-run, the very next item's cascade already excludes
  it. Confirmed live: this fixed a real incident where a tripped Gemini
  instance kept getting retried-then-falling-back on every subsequent
  item for the rest of a 457-item run, burning a wasted request + a
  ~62s retry wait per item, before this fix landed.
- The Engines page shows a "rate-limited" badge per instance, driven by
  the same `rate_limited_until` field.

So: **"flag the engine as disabled until X, and have each item pick
through that at the start of its own attempt" is already exactly how
this works today.** No new mechanism needed for that part.

## What this plan adds: reload-on-failure for LOCAL engines specifically

For cloud engines, a `ProviderRateLimitedError` (429, timeout, 5xx) is
just... a 429 — there's nothing to "fix" locally, only wait out. For
Ollama, though, a failure is often a wedged/crashed model process that
a **reload can actually recover from** — and the provider already has
exactly this capability, just scoped narrowly today:

```python
# app/providers/ollama_provider.py
async def _force_unload_model(self) -> None:
    """Evicts the model from Ollama's memory immediately (keep_alive=0),
    used to break a wedged request out of whatever state it's stuck in."""
    ...
```

Today this only fires on a **watchdog timeout** specifically (one narrow
failure mode), inside `translate()`'s own 2-attempt retry loop. It does
NOT fire on other retryable failures (connection refused, a real 5xx
from Ollama itself, etc.) — those just raise `ProviderRateLimitedError`
directly with no recovery attempt.

### Proposed behavior

**Ollama**: on EVERY retryable failure (not just watchdog timeout) that
counts toward an instance's `consecutive_failures`, attempt
`_force_unload_model()` (or equivalent — a "kick" that clears whatever
wedged state caused the failure) BEFORE the strike is counted, or as
part of handling it. Concretely:

- 1st failure → attempt unload/reload, retry once (this already
  happens for watchdog timeouts; generalize it to cover connection
  failures and 5xx too, not just timeouts).
- 2nd failure (this item's retry also failed, OR a later item also
  fails) → another unload/reload attempt.
- 3rd consecutive failure despite reload attempts → the instance is
  genuinely unhealthy, not just transiently wedged. Trip
  `rate_limited_until` via the existing mechanism — no different from
  how a cloud engine trips today. Subsequent items' cascade rebuilds
  skip it in real time, same as already happens.

**llama.cpp**: confirmed (see `llamacpp_provider.py`'s own docstring)
there is NO reload/unload/model-switching endpoint — "no web UI... no
equivalent to Ollama's `/api/pull` or a model-switching endpoint."
Nothing to attempt. 3 consecutive failures trips the SAME cooldown
mechanism, just without a recovery attempt in between — the asymmetry
between the two local providers is real and shouldn't be papered over
with a fake no-op "reload" call for llama.cpp.

### Where this actually lives in the code

`_force_unload_model()`-style recovery is provider-internal logic (it
needs `self._client`, `self._model`), so it belongs inside
`OllamaProvider.translate()`'s own retry loop — NOT in `runner.py` or
`translator.py`, which only see the `ProviderRateLimitedError` after
the provider's own retry logic already gave up. This is consistent with
how the watchdog-timeout case already works today; this plan just
widens WHICH failures trigger it, from "watchdog timeout only" to "any
retryable failure this provider hits."

No new `engine_instances_repo`/`runner.py` mechanism is needed — the
3-strikes-trips-cooldown flow is already generic and doesn't care WHY
`ProviderRateLimitedError` was raised, only that it was. This plan is
scoped entirely to `app/providers/ollama_provider.py`'s internal retry
loop.

## Resolved design decisions

1. **Reload attempts stay invisible to the 3-strike count — and this
   isn't really a choice, it's forced by how `_force_unload_model()`
   already works.** It's deliberately best-effort: it swallows its own
   `httpx.HTTPError` and only logs a warning, never raises (see its own
   docstring: "the retry proceeds anyway rather than compounding one
   failure into two"). There's no independent "did the reload itself
   fail" signal to count in the first place — the only observable
   outcome is whether the (call → maybe-reload → retry) sequence as a
   WHOLE succeeded or raised `ProviderRateLimitedError`. So: one failed
   item-attempt against an instance = one strike, full stop, regardless
   of whether a reload happened inside it.
2. **Only reload for failures that indicate the server responded but
   got stuck/errored (timeout, 5xx) — skip it for connection-level
   failures (refused/unreachable).** If Ollama's process isn't even
   reachable, there's no loaded model state to clear; attempting a
   reload there is pure wasted time before the inevitable failure.
3. **llama.cpp gets no reload — skipped, not investigated further.**
   Keep `llamacpp_provider.py` generic to plain llama.cpp servers, which
   genuinely have no reload/restart capability per its own docstring.
   Not worth building against one friend's specific router setup even
   if it happens to expose more — out of scope for this provider.
