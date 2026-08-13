# Getting API keys and setting token budgets

This walks through getting free-tier API keys for the cloud engines and the
token budgets confirmed to work well for each, so a fresh install doesn't
have to rediscover them by trial and error. All of this is configured from
the **Translation Engine** page — see the main [README](../README.md) for
how the engine cascade works.

## Google Gemini

1. Go to [aistudio.google.com](https://aistudio.google.com) and sign in with
   a Google account.
2. Click **Get API key** (left sidebar) → **Create API key**. Copy it.
3. In Subtitlarr, add a new engine instance: provider **Gemini**, name it
   **"Gemini Main"**, paste the key, and set the model to
   **`gemini-3.5-flash-lite`**.
4. **Batch token budget: `4000`**.

### Stacking a second model on the same key

Gemini's free-tier daily request quota (500/day) is tracked *per model*, not
once per account — so a second instance using the **same** key but a
**different** fast model gets its own independent 500/day pool:

1. Add another instance: name it **"Gemini Main gemini-3.1-flash-lite"**,
   same API key as "Gemini Main", model set to
   **`gemini-3.1-flash-lite`** — the confirmed real API model ID (Google's
   AI Studio dashboard shows a friendlier display name that doesn't map
   1:1 to this string).
2. **Batch token budget: `4000`**.

This alone doubles account A's usable quota to 1000/day, with zero extra
signups.

> **Quality note:** Gemini 3.5 Flash-Lite gives noticeably better
> translation quality, better context handling, and higher throughput
> (~350 tokens/sec) than 3.1 Flash-Lite. Adding a 3.1 instance below 3.5 in
> the same cascade doubles daily throughput, but every item that spills
> over onto it gets the weaker model's output. If translation quality
> matters more to you than squeezing out the extra 500/day, skip the 3.1
> instance entirely and let items that exceed 3.5's quota fall through to
> your next cascade tier (a second account's 3.5 instance, or a local
> engine) instead.

### Doubling again with a second account

The same 2-models-per-key trick works again with a second Google account
and its own key, for **4 Gemini instances total** and 2000 requests/day
combined:

1. Repeat the "Google Gemini" steps above with a second Google account (a
   second free Gmail account works fine) to get a second key.
2. Add it as **"Gemini Secondary"**, model **`gemini-3.5-flash-lite`**,
   batch token budget `4000`.
3. (Optional — see the quality note above) Add **"Gemini Secondary
   gemini-3.1-flash-lite"**, same key, model **`gemini-3.1-flash-lite`**,
   batch token budget `4000`.
4. Order the cascade Main → Secondary → Main (3.1, if used) → Secondary
   (3.1, if used) (drag to reorder), so BOTH accounts' stronger model is
   tried before either account's weaker fallback model — keeps quality
   as high as possible for as long as possible before any item ever gets
   the weaker 3.1 model's output.

This is legitimate use of separate free tiers — it is not evading any
single account's limit, since each account's quota is used independently
and entirely for that account's own usage.

If you'd rather keep quality consistent across the whole cascade, skip
both 3.1 instances and just use **"Gemini Main"** + **"Gemini Secondary"**
(both `gemini-3.5-flash-lite`) for 1000/day — half the ceiling of the
4-instance setup, but every item gets the better model.

## NVIDIA NIM

1. Go to [build.nvidia.com](https://build.nvidia.com) and sign in / create
   an account.
2. Open any model page (e.g. search for DeepSeek), click **Get API Key**,
   and copy it.
3. In Subtitlarr, add a new engine instance: provider **NVIDIA**, paste the
   key, and set the model to a real instructable chat model — the default,
   `deepseek-ai/deepseek-v4-flash`, is confirmed working. NVIDIA also hosts
   dedicated translation-only models (e.g. Riva Translate) — do **not** use
   one of those, they don't support the formatting instructions this app
   relies on and translations will fail.
4. **Batch token budget: `700`**.
5. Free tier is capped at roughly 40 requests/minute — no daily cap
   confirmed, unlike Gemini.

## Local Ollama

No API key needed — this is your own local (or LAN) inference server.

1. Install [Ollama](https://ollama.com) and pull a model, e.g.:
   ```bash
   ollama pull translategemma:12b
   ```
2. In Subtitlarr, add a new engine instance: provider **Ollama**, base URL
   pointing at your Ollama server (e.g. `http://localhost:11434` or
   `http://<lan-ip>:11434`), model `translategemma:12b`.
3. **Batch token budget: `400`**. Small local models lose reliable output
   formatting well before they run out of raw context window — `400` is a
   safe starting point for modest/low-end GPUs, including with
   `translategemma:12b`. If you use a more capable local GPU/model you may
   be able to raise this.

## Recommended cascade shape

Put your free-tier Gemini instance(s) at the top of the cascade, then a
**separator**, then your local engine(s) below it. See the README's
"Recommended cascade" section for why — in short, this stops a run from
silently falling back to slow local inference for hours just because Gemini
hit a temporary rate limit, while still leaving local engines available for
a deliberate manual run against whatever's left over.

This is what the **Translation Engine** page should look like, top to
bottom, once everything above is set up (max-throughput version, 2000/day):

| Order | Instance name | Provider · Model | Batch token budget |
|---|---|---|---|
| 1 | Gemini Main | Gemini · `gemini-3.5-flash-lite` | `4000` |
| 2 | Gemini Secondary | Gemini · `gemini-3.5-flash-lite` | `4000` |
| 3 | Gemini Main gemini-3.1-flash-lite *(optional — see quality note above)* | Gemini · `gemini-3.1-flash-lite` | `4000` |
| 4 | Gemini Secondary gemini-3.1-flash-lite *(optional)* | Gemini · `gemini-3.1-flash-lite` | `4000` |
| — | *— cascade stops here — anything below is never tried as a fallback —* | *(separator)* | — |
| 5 | Ollama | Ollama · `translategemma:12b` | `400` |

Drop rows 3 and 4 for the quality-first version instead (1000/day, every
item on `gemini-3.5-flash-lite`).

Optionally add an NVIDIA instance (`deepseek-ai/deepseek-v4-flash`, budget
`700`) either above the separator as a 5th cloud fallback, or below it
alongside Ollama, depending on whether you want it tried automatically or
reserved for manual runs.
