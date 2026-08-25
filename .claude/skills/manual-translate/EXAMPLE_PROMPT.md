# Example prompt: working the failed backlog

A real prompt that successfully invoked this skill's "whole failed backlog"
workflow (see SKILL.md) against a live instance. Paste something like this,
swapping in the real base URL, to kick off the same flow in a fresh session.

```
Use the manual-translate skill against my Subtitlarr instance at
http://<your-NAS-IP>:<port>. Go through the failed backlog: list failed
items via the Queue API, classify each by its error_message, and only
manually translate the ones that are genuinely content-blocked (Gemini's
safety filter) — leave rate-limited and credential-dead ones alone. Confirm
the list with me before starting, then work through them ONE ITEM AT A
TIME — do not batch or mix multiple items into a single translation pass.
Within each item, chunk the file to ~250-350 cues per chunk, appended to
one running local file rather than one scratch file per chunk, and
verify index parity once at the end before submitting.
```

## What made this work well

- **"One item at a time... do not batch or mix"** is now stated explicitly
  in the prompt itself, not left implicit. On the first real run, items
  got batched more loosely than intended — the assistant proposed
  translating several episodes' worth of cues (~1800 cues across 3 items)
  before checking in, and had to be corrected: chunk *within* an item to
  ~150 cues, never mix multiple items into one translation pass. Stating
  this up front in the prompt avoids that correction cycle.
- **"Confirm the list with me before starting"** matters at scale — a
  first classification pass against a real backlog turned up 740
  content-blocked items out of 809 failed, far too many for one sitting.
  Asking narrowed it down to a small pilot batch (a handful of items)
  before committing to the rest.
- Letting the assistant pick the classification logic and cue-chunking
  mechanics (per SKILL.md) while the user controls scope, pacing, and the
  one-item-at-a-time boundary split responsibility well.

## Follow-up prompt: switch to background agents once classification is done

Doing all 740 items sequentially in one conversation isn't just slow —
it burns through the Claude Code usage window fast, because each model
call resends the whole accumulated conversation (every source fetch,
every chunk file, every verification result before it). This was
discovered live: after 7 items in one session, the 5-hour usage window
was already at 48%. There is no benefit to keeping it all in one
conversation — each item's translation is independent.

Once the classification pass and pilot batch (above) are done and
confirmed, hand the rest off to background agents — **one agent per
single item, never multiple items batched into one agent**:

```
Now that the content-blocked list is confirmed, work through the rest
using background agents instead of continuing here — spawn ONE AGENT
PER ITEM (a single movie, or a single TV episode), never multiple items
in one agent. Each agent should get: a reference to the manual-translate
skill, the base URL, its one item id/title, a reminder to chunk within
that item (~250-350 cues per chunk, appended to one running local file
rather than one scratch file per chunk) and verify index parity once at
the end before submitting, and instructions to report back success/
failure and cue_count. Run them in the background and summarize results
as they land.
```

### Why one-per-item, not one-per-show or one-per-batch-of-movies

An earlier version of this guidance suggested batching several items
into one agent (one agent per TV show's episodes, one agent per ~10-15
movies) to "amortize" the cost of an agent reading the skill file. That
was tried and measured wrong: two 5-movie batch agents each burned
~520K tokens (roughly 100K/movie) to translate movies averaging
~1,300-1,500 cues. The batching itself is what drove the cost up —
within one agent's conversation, nothing gets dropped between items, so
movie 2 pays for movie 1's entire accumulated context (chunks, source
JSON, verification output), movie 3 pays for movies 1+2, and so on. A
5-item batch costs meaningfully more than 5 independent single-item
agents, not less. The one-time cost of an agent reading the skill file
and conventions is small in comparison and is not worth batching around.

If asked to batch multiple items per agent to "save tokens," don't —
that reasoning has already been tried and disproven against this exact
workflow.

### Single-item cost, and what actually moved it

Single-item agents were also measured directly, on two similarly-sized
movies (~1,200 cues each): with the old 100-150 cue chunking and one
scratch file per chunk, one movie cost ~110K tokens. After tightening
to 250-350 cue chunks appended to a single running file with one final
parity check (the current SKILL.md), a comparable movie cost ~88K
tokens — about 20% less, from far fewer tool calls (37 → 14). The
remaining cost is mostly the source dialogue and translated output
themselves, which have to pass through the agent's context no matter
how chunking is tuned — so ~90-110K tokens per ~1,200-cue movie is
roughly the realistic floor for this approach, not a sign that more
skill tuning will find another 2-3x.
