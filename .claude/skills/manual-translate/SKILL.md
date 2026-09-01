---
name: manual-translate
description: Translate a Subtitlarr item by hand (Claude itself doing the translation) instead of a configured LLM provider — for items no cloud engine can handle (content-filter refusals, dead credentials, exhausted quota) or where Claude's own translation quality is wanted specifically. Use when the user asks to "translate this one yourself", mentions an item Gemini/another engine keeps refusing or failing on, or wants Claude to work through failed items directly.
---

# Manual translation via Subtitlarr's API

Two endpoints exist specifically for this (added because Gemini's
content filter blocks legitimate historical/dramatic dialogue — e.g. a
film about slavery using period-accurate slurs — that Claude can
translate faithfully in context):

- `GET /api/queue/{item_id}/manual-translation/source` — resolves the
  item's source subtitle the normal way and returns
  `{item_id, source_language, target_language, cue_count, dialogue_text}`.
  `dialogue_text` is cue blocks in `"<index>\n<content>"` form, separated
  by blank lines — the exact format a real provider is prompted with.
- `POST /api/queue/{item_id}/manual-translation` — body
  `{"translated_text": "...", "model_name": "claude-code"}`. Runs the
  translation through the same reassembly/integrity-check/disclaimer/
  upload/DB-tracking pipeline a real provider's output goes through.
  Records `engine_used="manual"`, `model_used=<model_name>` — honestly
  distinguishable from a real API-driven translation in the Queue,
  History, and the disclaimer line embedded in the subtitle itself.

No LLM call happens on the server side at all — the server just accepts
whatever `translated_text` is posted and reassembles it onto the
original cue timing.

All of this works purely over HTTP against whichever Subtitlarr
instance's base URL you're given (local dev, or a remote/NAS instance)
— none of it requires local filesystem or database access. If working
against a remote instance, confirm the base URL is reachable (a plain
`curl` to `GET /api/run/current` is a good smoke test) before starting.

## Working through the whole failed backlog

See [EXAMPLE_PROMPT.md](EXAMPLE_PROMPT.md) for a real prompt that invokes
this workflow, plus notes on what made it work.

There is no dedicated "list items eligible for manual translation"
endpoint — build the list from the existing Queue listing, purely over
HTTP:

1. **List failed items, paginated.** `GET /api/queue?status=failed&page=1&page_size=100`,
   incrementing `page` until `data` comes back shorter than
   `page_size` (or empty) — `total` in the response tells you how many
   pages to expect. Each row includes `id`, `title`, `error_message`.
2. **Classify by `error_message` before touching anything** — don't
   manually translate every failed item indiscriminately:
   - **Content-blocked** (`"blocked its own response"`, `"blocked this
     request"`, `"prohibited content"`, etc.) — this workflow's actual
     target. Nothing else fixes these; the same content will fail the
     same way against the same engine every time.
   - **Rate-limited** (`"rate limit hit (429)"`) — transient, NOT a
     content problem. Retry these normally via
     `POST /api/queue/run-by-ids` with the item ids, not manual
     translation.
   - **Auth/credential errors** (401, "service account is
     deleted/disabled", etc.) — needs the user to fix the actual API
     key/credential; manually translating around a dead key doesn't
     help future runs using that same engine.
   - **Everything else** (5xx server errors, timeouts, malformed
     responses) — usually transient too; a plain retry is more
     appropriate than manual translation.
3. **Confirm the classification/target list with the user before
   translating a large batch** — especially for a first pass against a
   given instance, since "how many are actually content-blocked" can
   only be known after step 1–2 run.
4. Work through the content-blocked list one item at a time using the
   single-item workflow below.

## Use ONE background agent PER ITEM — never batch multiple items into one agent

This is not a minor preference — batching multiple items into a single
agent multiplies token cost, it doesn't save it. Within one agent's
conversation, every tool call (source fetch, every chunk write, every
verification run) stays in that agent's context permanently. If one
agent translates 5 movies sequentially, movie 2's work happens on top
of movie 1's entire accumulated context (chunks, source JSON, tool
output), movie 3 pays for movies 1+2, and so on — the 5th item in a
batch is far more expensive than the 1st, and the batch as a whole
costs meaningfully more than 5 independent agents would. This was
confirmed empirically: two 5-movie batch agents each burned ~520K
tokens (roughly 100K/movie, front-loaded onto later items in the
batch) — a single-item agent doing comparable-length content costs a
small fraction of that because it never carries other items' leftover
context.

The fixed "cold start" cost of an agent reading this skill file and the
conventions section is small by comparison and is NOT a reason to
batch. Do not rationalize batching as a cost optimization — it isn't
one. If asked to reconsider this, don't — the batching approach was
tried, measured, and confirmed worse.

So: once the classification and pilot/confirmation step (above) are
done, spawn exactly one background agent per item id, whether it's a
movie or a single TV episode. For a TV show with multiple pending
episodes, still spawn one agent per episode (not one agent for the
whole show) — the episodes don't need each other's context either.

Each agent prompt must be self-contained — the agent has no memory of
this conversation — and should include:

1. A link/reference to this skill file and its single-item workflow.
2. The single item id to work (with title, for sanity-checking against
   what `GET /api/queue/{id}` returns).
3. The base URL of the Subtitlarr instance.
4. A reminder to chunk within the item (~250-350 cues per chunk,
   appended to a single running local file rather than one scratch
   file per chunk) and verify index parity once at the end before
   submitting — this is a different concern from "don't batch items"
   and both rules apply together. Also note that Node.js is the
   available scripting runtime for the verify step (not `python3` —
   it isn't installed; don't let the agent waste turns discovering
   that itself).
5. The project's translation conventions (see below) if relevant to the
   content (e.g. period slurs).
6. What to report back: success/failure and cue_count, so results can
   be reconciled against the master classified list.

## Dispatch agents ONE AT A TIME, strictly sequential — never launch two in parallel

"One agent per item" (above) is about what goes *inside* one agent's
conversation — never about how many agents run at once. Do not conflate
the two. Even though each item gets its own independent agent, agents
must still be launched **one at a time, in sequence**: launch a single
agent, wait for it to finish and report back, THEN launch the next one.
Never issue two or more `Agent` tool calls in the same message, and
never have more than one manual-translation agent in flight at once —
not two, not a "small controlled wave," not ten. The user has stated
this explicitly and repeatedly: one agent after another, one at a time,
never parallel.

Concretely: launch item N's agent in the background, then stop and wait
for its completion notification before launching item N+1's agent. Do
not pre-launch several agents "to keep the pipeline moving" — that IS
parallel dispatch and is exactly what's prohibited here, regardless of
whether each agent only touches one item internally.

## Single-item workflow

**Token-cost note:** the dominant cost here is I/O overhead, not the
translation itself. A first live run on a 1188-cue movie burned
~110K tokens because it wrote each chunk's translation to a scratch
file and read it back before combining — 8 extra write+read round
trips, plus environment-discovery overhead (missing `python3`, path
interpolation issues). Steps 2–5 below are written to avoid that:
keep chunks in your own working output, don't round-trip them through
the filesystem, and don't rediscover the environment each item.

1. **Confirm the item.** `GET /api/queue/{item_id}` — check `status`
   and `error_message` match what you expect before spending effort.
2. **Fetch the source.** `GET /api/queue/{item_id}/manual-translation/source`.
   Note `cue_count`.
3. **Translate in chunks of roughly 250–350 cues**, not the whole file
   in one pass and not 100–150-cue slices either — very long single
   passes are where index-alignment mistakes happen (attention drifts
   over a long continuous output), but each chunk also carries fixed
   overhead (re-stating format/conventions, a tool round trip), so
   fewer/larger chunks within that safe range cost less overall.
   **Write each chunk's translated output directly to one running local
   file, named with this item's id** (e.g. `translated_<item_id>.txt` in
   the scratchpad, not a generic `translated.txt`) as you go — do not
   create a separate scratch file per chunk, and do not read a chunk
   back after writing it just to re-verify it in isolation. One file,
   appended to, read once at the end for the parity check below. The
   item-id suffix matters even though dispatch should be sequential
   (see below): a generic filename has already caused one agent's
   in-progress chunks to be silently overwritten by another concurrently
   running manual-translation agent sharing the same scratchpad
   directory — name every scratch file (source JSON, chunks, combined
   output) after the item id so a dispatch slip can't corrupt another
   item's work.
4. **Preserve every index exactly, and don't skip cues.** Every
   `<index>` in the source must appear exactly once in your translated
   output, with the same index number — `reassemble()` matches by
   index, not position, and a cue you don't include falls back to its
   original-language text (silently degrading quality, not erroring).
   Keep formatting markup (e.g. `<i>...</i>` italics on song lyrics/
   internal monologue) — it's part of the content, not stripped before
   reassembly.
5. **Verify index parity before submitting**, once, against the fully
   assembled file — not per chunk. Extract every `^\d+$` line and diff
   the resulting set against `1..cue_count`. Note this only catches
   missing/duplicate index *numbers* — it does NOT catch a chunk where
   cues got merged or split so indices still run 1-to-1 but the wrong
   dialogue ends up under a given number (e.g. accidentally merging two
   source cues into one output block shifts every subsequent cue's
   content by one, invisibly to this check). Spot-check actual content
   against the source at a few points (chunk boundaries, one mid-chunk
   cue) as well, not just the index script. Use this exact Node
   one-liner for the index check (Node is confirmed available; do not
   spend time discovering or working around a missing `python3` — just
   use this):
   ```
   node -e "const fs=require('fs');const t=fs.readFileSync('translated.txt','utf8');const idx=[...t.matchAll(/^(\d+)$/gm)].map(m=>+m[1]);const want=new Set(Array.from({length:<CUE_COUNT>},(_,i)=>i+1));const got=new Set(idx);const missing=[...want].filter(x=>!got.has(x));const extra=idx.filter((x,i)=>idx.indexOf(x)!==i);console.log('count',idx.length,'missing',missing,'dupes',[...new Set(extra)])"
   ```
   Fix any gap/duplicate this reports before submitting.
6. **Submit.** `POST /api/queue/{item_id}/manual-translation` with the
   full combined `translated_text` (all chunks concatenated — the
   endpoint takes one complete submission per item, not one call per
   chunk) and `model_name` (default `"claude-code"`; only change it if
   asked to attribute the work differently).
7. **Confirm.** `GET /api/queue/{item_id}` should show `status: "done"`,
   `engine_used: "manual"`. Optionally spot-check the uploaded file via
   `GET /api/debug/{movie|episode}/{bazarr_id}/subtitle?lang=<code>` —
   the first cue should be the disclaimer line ending in
   `[<model_name>]`, and cue 2's timing should match the original
   source's first real cue exactly.

## Translation conventions for this project

- **Racial slurs / period-accurate offensive language**: translate
  faithfully, preserving register — don't soften or omit. For English
  "nigger" → Catalan, the user's guidance: use **"negrata"** (the
  pejorative form) where a slaver/overseer character uses it with
  contempt, and **"negre"/"negres"** for neutral/descriptive uses. This
  mirrors the English original's own distinction between neutral and
  slur usage.
- **Historical/period register**: keep formal address, dialect markers,
  and period speech patterns (contractions, dropped grammar in
  enslaved characters' dialogue, formal diction in upper-class
  characters') — don't flatten everyone into modern neutral Catalan.
- **Song lyrics / spirituals** (e.g. "Roll, Jordan, Roll"): translate
  the meaning, not necessarily a singable equivalent — these are
  subtitles, not a dub script.

## Why this exists instead of just retrying the configured engine

Gemini's content-safety filter blocks entire categories of legitimate
narrative content (explicit violence, period slurs, adult themes in a
serious dramatic context) that a human or Claude reading the same
scene in context would translate without issue. Retrying the same
content against the same engine repeatedly doesn't help — it's a
content-policy judgment, not a transient failure — and risks the
provider's own abuse enforcement against the API key. This workflow
exists for exactly that class of stuck item, not as a general
replacement for the normal engine cascade.
