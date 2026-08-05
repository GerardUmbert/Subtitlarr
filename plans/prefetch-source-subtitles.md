# Plan: Pre-fetch source subtitles tied to the daily scheduled sync

## Goal

Fetch each item's source SRT from Bazarr once, in a burst, and cache it
locally — instead of the current behavior where every item's translation
hits Bazarr's API (and therefore the NAS) individually, keeping the NAS's
disks from spinning down for the whole duration of a run.

## Current behavior (confirmed by reading the code)

`translator.py`'s `translate_item()` calls
`client.get_subtitle_contents(source_subtitle_path)` exactly once per item
— NOT once per batch. So today's actual pain is: across a large backlog run
(e.g. hundreds of items, spread out by `pause_between_items_seconds`), the
NAS gets woken up once per item over the course of potentially many hours,
never getting a chance to spin back down.

`run_scheduled()` (fired by the 10am cron, `schedule_cron` setting) already
does:
1. `poll()` — refreshes wanted-item METADATA from Bazarr (not subtitle
   content)
2. Immediately starts translating the age-gated queue

## Proposed approach (refined based on user's observation about the 10am cron)

Trigger point: right after `run_scheduled()`'s existing `poll()` call,
before translation begins — NOT on every manual run. Manual "Run all"/
single-item clicks are ad-hoc and shouldn't wait on a bulk pre-fetch first.

Flow:
1. `poll()` refreshes wanted-item metadata from Bazarr (already happens,
   unchanged).
2. NEW: resolve the age-gated queue (`selector.get_age_gated_queue` — this
   already gets computed next either way) and burst-fetch each item's
   actual source subtitle CONTENT from Bazarr in one pass, writing each to
   a local scratch file (e.g. `{db_path parent}/scratch/run_{run_id}/
   {item_id}.srt`).
3. `translator.translate_item()` gets an optional `source_content` param —
   use it directly instead of fetching from Bazarr when provided. Falls
   back to a live fetch otherwise (e.g. single-item manual re-runs, which
   don't need the batching benefit and should stay simple).
4. Uploads back to Bazarr still happen immediately per-item as each
   translation finishes (not deferred/batched) — decided against batching
   uploads since they're quick writes, not the disk-spin-down concern;
   only the READ side needs batching.
5. Written to disk (not memory-only) so a mid-run crash doesn't force
   re-fetching everything from Bazarr again on restart/retry.
6. Clean up the scratch dir after the run finishes (success or failure).

Net effect: the NAS gets one burst of read activity at 10am (poll + bulk
subtitle fetch), then can stay spun down for the rest of the day while the
actual LLM translation work — which can take hours for a large batch —
reads from local disk instead.

## Open questions (ask before building)

1. Scratch cleanup timing: per-item immediately after that item's
   translation completes, or hold all of them until the whole scheduled
   run finishes? Matters if a fallback-provider retry needs to re-read the
   same source.
2. Does this pre-fetch also apply to `run_now()` (manual "Run all"), or
   stay scheduled-only, matching how the user framed it ("we sync with
   Bazarr every morning at 10am... couldn't we pull all queued subtitles
   then?")?
3. Where should the scratch directory live relative to `DB_PATH`/Docker
   volume mounting, so it survives restarts but doesn't need its own
   separate volume config in the Unraid/docker-compose setup?
4. Burst-fetch ALL age-gated items upfront (could be large for a full
   backlog — memory/disk usage), or fetch in smaller sub-batches (e.g. 20
   at a time) to bound peak resource usage?

## Status

Built 2026-08-05. Applies to ALL run types (scheduled, manual full,
filtered, single-item), not scheduled-only as originally framed — user
confirmed this scope during implementation. Scratch dir:
`tempfile.gettempdir()/subtitlarr-scratch/run_{run_id}/`, in the
container's own ephemeral filesystem (never the persistent `/data`
volume). Fetches all of a run's items concurrently via `asyncio.gather()`
(no windowing/pacing — this is local Bazarr/NAS traffic, not a
rate-limited cloud API). A successful item's cache is deleted right after
upload; a failed item's cache is deliberately left in place. New module:
`app/engine/prefetch.py`. `translator.translate_item()` gained an optional
`cached_source_path` param. Tests: `tests/unit/test_prefetch.py` (7 tests).

Not yet live-verified end-to-end against the real server/Bazarr as of
writing — implemented and unit-tested only.
