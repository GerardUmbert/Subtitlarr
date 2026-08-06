# TODO: Click-to-sort table columns, server-side/paginated

## Request

Add clickable column-header sorting to the Queue page, History's Runs
list, and History's Events tab — sorting must be applied server-side
against the full result set (re-issuing the query with a new ORDER BY and
re-paginating), not a client-side re-sort of whatever page happens to be
currently loaded in the browser.

## Current state

- **Queue** (`app/api/queue.py` → `repository.list_queue`) already
  accepts a `sort` param, but `_QUEUE_SORTS` (app/db/repository.py:290)
  is only two fixed named presets — `"title"` and `"recent"` — not a
  general "any column, either direction" mechanism. Nothing in
  `queue.js`/`queue.html` calls it from a header click today; the sort
  param exists but is unused by the UI.
- **History → Runs** (`repository.list_run_history`) has no sort param
  at all — always `ORDER BY started_at DESC`, hardcoded.
- **History → Events** (`log_events.read_events`) has no sort param
  either — always newest-first by line id, hardcoded.

## Shape of the fix

1. Generalize the sort mechanism beyond `_QUEUE_SORTS`'s two fixed
   presets: accept a `(column, direction)` pair validated against a
   per-table allowlist of real column names (never interpolate the raw
   query param directly into SQL — allowlist-and-map, same pattern
   `_QUEUE_SORTS` already uses, just keyed by column+direction instead of
   one fixed label).
2. Extend `list_queue`, `list_run_history`, and `log_events.read_events`
   to accept `sort_by`/`sort_dir` and thread them into their `ORDER BY`.
3. Frontend: clickable `<th>` per sortable column, showing current
   sort direction (▲/▼), triggering a fresh paginated fetch (same
   pattern the existing `page`/`page_size` fetches already use) —
   NOT a client-side `.sort()` on `this.rows`, since that would only
   reorder whatever page is currently in memory rather than the true
   full-dataset order.
4. Decide which columns are actually sortable per table (e.g. Queue:
   title, language, status, updated, duration; Events: time, item, engine,
   type — probably not free-text detail).

## Not yet scoped

- Exact column list per table
- Whether multi-column sort is needed (probably not — single column,
  click again to flip direction, is the common pattern and matches most
  users' expectations)
