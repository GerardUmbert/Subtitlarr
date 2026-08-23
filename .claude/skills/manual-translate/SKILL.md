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

## Workflow

1. **Find the item id.** Query the local instance's DB or
   `GET /api/queue?status=failed` — confirm the item is actually
   `failed` (or otherwise stuck) before spending effort on it.
2. **Fetch the source.** `GET /api/queue/{item_id}/manual-translation/source`.
   Note `cue_count` — this tells you how much work is ahead and whether
   to chunk (see below).
3. **Translate in chunks of roughly 100–200 cues, not the whole file at
   once.** A single very long generation (confirmed live: a 1498-cue
   film translated in one pass) is where index-alignment mistakes
   happen — attention drifts over a long continuous output and a cue
   gets dropped or duplicated near the end. Smaller chunks are faster
   per-chunk, and a mistake in one chunk doesn't require re-verifying
   the whole file.
4. **Preserve every index exactly, and don't skip cues.** Every
   `<index>` in the source must appear exactly once in your translated
   output, with the same index number — `reassemble()` matches by
   index, not position, and a cue you don't include falls back to its
   original-language text (silently degrading quality, not erroring).
   Keep formatting markup (e.g. `<i>...</i>` italics on song lyrics/
   internal monologue) — it's part of the content, not stripped before
   reassembly.
5. **Verify index parity before submitting**, especially after
   concatenating multiple translated chunks back together. A quick
   check: extract every `^\d+$` line from your combined translation and
   diff the resulting set against `set(range(1, cue_count + 1))` — this
   is exactly what caught a dropped/duplicated cue during the first use
   of this workflow. Do this in a scratch script, not by eye — eyeballing
   ~1500 lines is how the mistake happened in the first place.
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
