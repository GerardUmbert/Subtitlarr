# Plan: Batch translated-subtitle uploads on a schedule

## Goal

Currently, `translate_item()` uploads each item's finished translation to
Bazarr immediately, right after that item's translation succeeds — one
write per item, whenever it happens to finish, which could be at any
random time during a multi-hour run. This wakes the NAS's HDD array for a
write at an unpredictable moment.

User's proposal: instead of uploading immediately per-item, hold finished
translations locally and push them all to Bazarr in one batch at a
scheduled time — so any HDD wake-up from the upload itself is also
consolidated into one predictable window, same idea as the existing
source-subtitle prefetch (this plan is the upload-side mirror of that).

## Relationship to the read-side prefetch (already built)

The prefetch work (see `plans/prefetch-source-subtitles.md`, built
2026-08-05) already solved the READ side: source subtitle content is
fetched once per run in a burst instead of once per item. This plan
addresses the WRITE side, which is currently still immediate/per-item.

Both together would mean a run's ENTIRE Bazarr/NAS disk interaction — both
reads and writes — happens in two predictable bursts (start and end of a
scheduled window) rather than being smeared across the whole run's
duration.

## Open design questions (not yet resolved — ask before building)

1. **What triggers the upload batch?** Options:
   - A dedicated cron/schedule setting (separate from `schedule_cron`,
     which drives when TRANSLATION starts) — e.g. "upload everything
     finished so far, once daily at HH:MM."
   - Tied to the existing translation run's own completion (upload
     everything at the END of a `run_batch()` call, not per-item during
     it) — simpler, no new schedule setting needed, but doesn't help if a
     single run spans many hours and the user wants uploads pushed earlier.
   - Both: upload at run-end by default, AND expose a way to force an
     early batch-upload if translations are piling up.
2. **Where do finished-but-not-yet-uploaded translations live in the
   interim?** Likely the same scratch-cache pattern as the read-side
   prefetch — write the composed, translated SRT bytes to a local file
   once translation succeeds, then a separate upload step reads all
   pending files and pushes them in one pass. Needs its own item status
   (e.g. `translated_pending_upload`, distinct from `done`) so the Queue
   UI can show "translated, not yet uploaded" as a real state.
3. **Does the daily_translation_limit / age-gate logic need to account
   for items that are translated-but-not-uploaded yet?** E.g. should
   `count_completed_today()` count them as done for the day's cap purposes
   before the actual Bazarr upload happens?
4. **Failure handling**: if the batch-upload step itself fails partway
   through (e.g. Bazarr temporarily unreachable), what happens to the
   items whose upload didn't go through — retry the whole batch, or just
   the ones that failed?
5. **User's NVMe-cache-pool point**: on Unraid, writes typically land on
   an NVMe cache pool first, with the mover relocating them to the HDD
   array later on its own schedule — the user noted upload writes are
   likely already somewhat insulated from directly spinning up the array.
   Given that, is batching uploads still worth building, or does the
   existing prefetch (read-side) already capture most of the practical
   benefit? Worth confirming this is still wanted before implementing.

## Status

Not started — idea proposed 2026-08-05, written up per user's request
("we could also just make a cron to push subtitles to bazarr at a
particular time in case the hdd gets woken up"). Open questions above need
answers before implementation begins.
