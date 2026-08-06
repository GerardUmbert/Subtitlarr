# Plan: Multiple Engine Instances + Cascading Fallback

## Problem with today's model

`app/config.py` has ONE set of settings per provider TYPE (`gemini_api_key`,
`gemini_model`, ...) and exactly two roles: `active_engine` (a string like
`"gemini"`) and `fallback_engine` (one more string). You can't have three
Gemini API keys, can't reorder fallbacks, and `_rate_limited_until` in each
provider is only an in-memory cooldown timer scoped to that one process —
it doesn't survive a restart and isn't visible anywhere in the UI.

## Target model

Move from "engine type → single config" to "named, ordered instances, each
independently configured, tried top-to-bottom on failure."

### 1. Data model — new `engine_instances` table

```sql
CREATE TABLE engine_instances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,             -- user-facing label, e.g. "Gemini (main)"
    provider_type   TEXT NOT NULL,              -- "gemini" | "ollama" | "nvidia" | "openrouter" | "groq" | "llamacpp"
    enabled         BOOLEAN NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL,           -- cascade position, top-to-bottom
    config_json     TEXT NOT NULL,              -- {api_key, base_url, model, batch_token_budget, concurrent_batch_window, ...} — shape varies per provider_type
    -- Rate-limit cooldown (see #3) — NOT usage/quota accounting, just
    -- "this instance recently looked dead, skip it for a while"
    rate_limited_until TIMESTAMP,                -- null = not currently cooling down
    created_at      TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL
);
```

Replaces `active_engine`/`fallback_engine` entirely. "Active" becomes
"the enabled instance with the lowest `sort_order`"; the cascade is
"every OTHER enabled instance, in `sort_order`, excluding whichever one
just failed" — this directly satisfies "next one on the list minus that
instance itself."

`config_json` keeps this schema-flexible across very different provider
shapes (Ollama has `base_url`+optional `api_key`, Gemini has `api_key`+
`model`, etc.) without a wide sparse table or a migration every time a
provider gains a field.

**Cascade-stop separator.** The list can contain a literal "stop here"
marker row between instances — anything below it is never tried as a
fallback, only used if explicitly selected as primary some other way (or
simply never reached). This is how "no fallback at all" is expressed: put
the separator directly under the first (active) instance, and the cascade
walk stops immediately after trying it. Modeled as its own row rather
than a boolean flag on the instance below it, since a flag reads
backwards ("this instance says the PREVIOUS one shouldn't fall further")
and doesn't extend cleanly to multiple stop points:

```sql
-- same table, provider_type = 'separator', config_json = '{}', no usage columns used
```

The cascade builder stops walking `sort_order` the moment it hits a
`provider_type = 'separator'` row — everything after it is excluded from
the fallback walk entirely, regardless of `enabled`. Reordering the
separator is just reordering any other row (drag/up-down buttons apply
to it the same way), so "how many fallbacks do I want" becomes a purely
visual/positional choice instead of a per-instance setting to configure.

### 2. Provider construction — generalize the registry

`app/providers/registry.py`'s `_build(name, settings)` becomes
`_build(provider_type, config_dict)` — same factory switch, just reading
from an instance's `config_json` instead of global `Settings` fields.

`get_active_provider`/`get_fallback_provider` (today: 2 functions, 0/1
fallback) become:

```python
def get_engine_cascade(conn) -> list[tuple[EngineInstance, TranslationProvider]]:
    """Every ENABLED instance in sort_order, each paired with its built
    provider. First element is 'active'; the rest are fallback candidates
    in order."""
```

### 3. Cascading fallback in the translator

Today `_translate_batch` has 3 near-identical `except` blocks, each doing
"try the ONE fallback_provider, re-raise if that also fails." Replace
`active_provider`/`fallback_provider: TranslationProvider | None` params
with `cascade: list[TranslationProvider]` (already active-first ordered,
already excludes nothing extra since caller builds it fresh per item).

New shared helper:

```python
async def _try_cascade(cascade, start_index, dialogue_text, ...) -> tuple[str, int, TranslationProvider]:
    """Tries cascade[start_index:], in order, returning (llm_response, engine_index, provider)
    on first success. Raises the LAST error if every remaining instance fails."""
```

All three failure paths (`ProviderRateLimitedError`, `ProviderContentBlockedError`,
`TranslationAlignmentError`/`TranslationIntegrityError`) call this same
helper starting from `current_index + 1` instead of duplicating "try the
fallback" logic three times — this is a simplification of the current
code, not just an extension.

Same-instance-retry behavior (today: ONE retry on `ProviderRateLimitedError`
before moving to fallback) is preserved as "retry cascade[i] once before
advancing to cascade[i+1]".

### 4. Rate-limit cooldown signal (deliberately NOT usage/quota tracking)

No RPD/TPM counters, no per-provider reset-cadence lookup table, no
attempt-vs-success accounting question. Instead, one simple DB-persisted
signal per instance: **N consecutive `ProviderRateLimitedError`s (429s,
or repeated timeouts/5xx — same exception class already covers both,
see `[[provider-circuit-breaker]]`) mark the instance rate-limited for a
flat 24 hours.**

- Track consecutive-rate-limited-failure count per instance the same way
  the circuit-breaker plan tracks it per run, just persisted instead of
  in-memory: a counter that increments on `ProviderRateLimitedError` and
  resets to 0 on ANY successful call through that instance.
- Once the count hits the threshold (same default as the circuit
  breaker, e.g. 3), set `rate_limited_until = now + 24h` on that
  instance's row and reset the counter. No per-provider knowledge of
  actual RPD limits, reset timezones, or token accounting needed — this
  is a blunt "this looked dead, leave it alone for a day" signal, not a
  quota meter.
- The cascade builder (`get_engine_cascade`) skips any instance whose
  `rate_limited_until` is still in the future, the same way it already
  skips `enabled=0` instances — no live round-trip needed to find out an
  instance is worth trying again, and no separate polling job needed
  either (it's just a `WHERE` clause checked at cascade-build time).
- A manually-triggered "test connection" on an instance (already exists
  per-provider today) should clear `rate_limited_until` early on success
  — lets a user un-stick an instance immediately once they know the
  underlying issue (e.g. a fixed API key, a restarted local server) is
  resolved, without waiting out the full 24h.
- Surfaced in the UI as a simple status badge on the instance card
  ("rate-limited until 14:32 tomorrow") rather than a usage bar — no
  percentage-of-quota math to get right, just a plain timestamp.

This intentionally drops the original plan's RPD/TPM counters, per-
provider reset-timezone table, and the open question about whether
failed attempts count against quota — none of that is needed to solve
the actual problem ("stop hammering an engine that's clearly down/rate-
limited"), and all of it was the highest-effort, most provider-specific
part of the original plan.

### 5. API + UI

New `app/api/engine_instances.py`:
- `GET /api/config/engine-instances` — list, ordered, with rate-limit status
- `POST /api/config/engine-instances` — create
- `PUT /api/config/engine-instances/{id}` — update (config, enabled, name)
- `DELETE /api/config/engine-instances/{id}`
- `POST /api/config/engine-instances/reorder` — `{ids: [3, 1, 2]}`, rewrites `sort_order`
- `POST /api/config/engine-instances/{id}/test` — mirrors today's per-type test

Engines page becomes a **list of instance cards** (drag-to-reorder, or
up/down buttons — drag-and-drop needs a small JS lib or manual
pointer-event handling since there's no build step; up/down buttons are
simpler and match the no-dependency constraint) instead of one radio
button per provider TYPE. Each card shows: name, type, enabled toggle,
a rate-limit status badge (nothing shown when healthy; "rate-limited
until {time}" when cooling down), edit/delete, and its position in the
cascade.

### 6. Migration path

- New migration `0007_engine_instances.sql` creates the table.
- A data migration (Python, run once at startup or via a script) reads
  today's `active_engine`/`fallback_engine`/`gemini_api_key`/etc. out of
  `app_config` and creates 1-2 rows in `engine_instances` so existing
  installs don't lose their configuration on upgrade.
- Keep `active_engine`/`fallback_engine` columns/settings dead-but-present
  for one release (or drop immediately — TBD, see Open Questions) rather
  than a hard cutover with no rollback path.

## Scope estimate

Smaller than the original usage-tracking version, but still a real
multi-part change:
- DB migration + data-migration script: small
- registry.py generalization: small-medium
- translator.py cascade rewrite (3 except blocks → 1 shared path): medium,
  touches the most gnarly existing logic (retry/fallback/rate-limit
  timing), needs careful test coverage since it's changing tested behavior
- Rate-limit cooldown signal (consecutive-failure counter + 24h
  timestamp + cascade-builder skip + test-connection early-clear): small
  — no per-provider knowledge, no reset-cadence table, just one counter
  and one timestamp column
- API + UI (list/reorder/edit/status badges): medium — smaller than the
  original usage-bar version since there's no percentage/quota math to
  display, but still the most user-facing-visible chunk and the part
  most likely to need iteration once you see it

## Open questions before implementation starts

1. **What happens when EVERY instance in the cascade is rate-limited or
   failing?** Today: the item just fails. Should that stay the behavior,
   or should it auto-pause the whole run (closer to what "stop the
   server on RPD" was getting at, but scoped to "stop this run" instead
   of "kill the process")? Same question the circuit-breaker plan raises
   for the single-fallback case — worth answering once, consistently,
   for both.
2. **Keep or drop `active_engine`/`fallback_engine` after migration?**
   Recommend dropping in the SAME release once the data migration is
   confirmed working — keeping dead settings around is exactly the kind
   of clutter this session's other work has been cleaning up.
3. **Manual reordering UI: buttons or drag-and-drop?** Buttons are much
   less work and there's no drag library already in the project; only
   worth drag-and-drop if the list is expected to be long/reordered
   often.
