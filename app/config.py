from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Bazarr connection
    bazarr_base_url: str = ""
    bazarr_api_key: str = ""

    # Translation engines
    active_engine: str = "ollama"
    fallback_engine: str = ""

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    # Ollama defaults to 4096 tokens regardless of what the model actually
    # supports — raise this to use more of the model's real context window
    # (Gemma 3 supports up to 128K). Higher values use more RAM per request.
    ollama_num_ctx: int = 8192
    # 0 = auto-derive the per-batch dialogue token budget from ollama_num_ctx
    # (see translator._batch_token_budget). Small models can lose reliable
    # output formatting well before they run out of raw context — a batch
    # that technically fits doesn't mean the model can format that much
    # output correctly. Set explicitly to override the auto formula with a
    # fixed batch size regardless of context window. 400 confirmed live as
    # a reliable default for gemma3:4b — near-baseline speed (0.154s/cue vs
    # 0.132s/cue at 900 tokens) while avoiding the repetition-loop failures
    # 900-token batches reproducibly hit.
    ollama_batch_token_budget: int = 400

    gemini_api_key: str = ""
    # Gemini's model lineup changes over time (1.5-flash has been retired,
    # returning 404s) — verify this is still current against
    # https://aistudio.google.com/ before relying on it, and prefer setting
    # it explicitly via the Engine page or GEMINI_MODEL env var.
    gemini_model: str = "gemini-2.0-flash"
    # No local GPU/VRAM constraint. Google doesn't publish a fixed
    # free-tier RPM/RPD number (varies by account/usage tier — see
    # ai.google.dev/gemini-api/docs/rate-limits), so this starts at the
    # same conservative-but-not-tiny value as NVIDIA/OpenRouter/Groq
    # rather than a guessed number; lower it if translations come back
    # with low cue-recovery counts.
    gemini_batch_token_budget: int = 4000
    # Mirrors nvidia_concurrent_batch_window. Kept modest since Gemini's
    # actual per-account RPM isn't known ahead of time.
    gemini_concurrent_batch_window: int = 4

    nvidia_api_key: str = ""
    # NVIDIA's build.nvidia.com free tier (integrate.api.nvidia.com), an
    # OpenAI-compatible endpoint. MUST be a real instructable chat model —
    # this provider reuses the same numbered-index prompt scheme as
    # Ollama/Gemini. NVIDIA also hosts a dedicated Riva Translate model,
    # which was tried first and dropped: it has no instructable system
    # prompt and proved unreliable at any real batch size (confirmed live:
    # it merges/drops joined lines instead of translating them
    # individually, even in small batches).
    nvidia_model: str = "deepseek-ai/deepseek-v4-flash"
    # Separate from ollama_batch_token_budget: NVIDIA's cloud model has no
    # local VRAM/GPU constraint driving a small batch size, and confirmed
    # live that DeepSeek V4 Flash returns 100% of indices in order at up to
    # 400 cues (~9700 chars) in a SINGLE request.
    #
    # 12000 was tried as the default first and failed live on an unusually
    # large episode (1481 cues — roughly 2-3x normal — packed into ONE
    # ~29,500-char request): got a real 504 Gateway Timeout from NVIDIA's
    # own servers on one attempt, and a repetition-loop failure (the model
    # degenerating into repeating one line 10+ times) on another.
    #
    # 6000 was tried next and STILL hit the same repetition-loop failure —
    # twice, including on a NORMAL-sized batch, not just the oversized
    # episode — so this isn't purely a "batch too large" problem. Root
    # cause not yet confirmed (investigation blocked by a separate logging
    # bug that drops the raw LLM response text from server.log). Cut
    # further as a precaution while that's investigated — closer to the
    # ~50-cue scale that was cleanly reliable across repeated small-scale
    # tests earlier in the same investigation.
    #
    # 700 confirmed live afterward as a further-validated default: same
    # 0.154s/cue as 400 tokens, while cutting request count roughly in
    # half (25 requests vs 44 for a 1555-cue movie) — 900 tokens still
    # reproducibly failed on the same cue twice at that scale.
    nvidia_batch_token_budget: int = 700
    # How many batches run concurrently at once via asyncio.gather() before
    # waiting for that window to finish and starting the next one — NOT a
    # literal requests-per-minute limiter. NVIDIA's free-tier NIM account
    # allows up to RATE_LIMIT_RPM=40 req/min (see nvidia_provider.py), but
    # since each request typically takes multiple seconds, a small
    # concurrent window naturally stays well under that ceiling — the
    # actual backstop against a real 429 is the provider's own shared
    # cooldown gate (NvidiaProvider._rate_limited_until), not this number.
    # Raising this increases how many requests can be in flight at once.
    nvidia_concurrent_batch_window: int = 4

    openrouter_api_key: str = ""
    # OpenRouter is a router in front of many underlying models (OpenAI,
    # Anthropic, Meta, etc.) via one OpenAI-compatible endpoint. MUST be a
    # real instructable chat model — same requirement as Ollama/Gemini/
    # NVIDIA, since this provider reuses the same numbered-index prompt
    # scheme. See https://openrouter.ai/models for the full lineup. Defaults
    # to a free-tier model (Gemma) so a fresh install works with just an
    # OpenRouter account and no spend — switch to a paid model for better
    # reliability/quality if needed.
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    # Free ":free" model variants are capped at 20 requests/minute AND a
    # DAILY quota (50/day under $10 purchased credits, 1000/day at $10+ —
    # confirmed via https://openrouter.ai/docs/api_reference/limits). The
    # daily cap makes request COUNT matter far more here than for NVIDIA
    # (which has no daily cap): a small batch size that produces 16
    # requests for one episode can burn a third of the whole day's quota
    # on a SINGLE file. No local GPU/VRAM constraint driving a small
    # batch, so raise this well above Ollama's small-model-safe default —
    # fewer, bigger requests directly trade off against the daily cap.
    # Lower it only if translations come back with low cue-recovery
    # counts (the free Gemma model can still lose formatting reliability
    # on very large batches, same as any small model).
    openrouter_batch_token_budget: int = 4000
    # Mirrors nvidia_concurrent_batch_window — how many batches run
    # concurrently via asyncio.gather() before waiting for that window to
    # finish. Kept well under the confirmed 20 RPM ceiling (see
    # RATE_LIMIT_RPM in openrouter_provider.py); a 429 from the per-minute
    # limit is retried automatically, but a 429 from the DAILY cap is not
    # (see OpenRouterDailyLimitError) — concurrency only helps you get
    # through the day's quota faster, it doesn't raise the quota itself.
    openrouter_concurrent_batch_window: int = 4

    groq_api_key: str = ""
    # Groq serves a fixed lineup of models on its own LPU hardware (Llama,
    # GPT-OSS, Qwen, etc. — no Gemma or DeepSeek, unlike NVIDIA/OpenRouter).
    # MUST be a real instructable chat model — same requirement as every
    # other provider. See https://console.groq.com/docs/models for the
    # full lineup. llama-3.1-8b-instant confirmed to handle Catalan
    # translation and has Groq's most generous documented free-tier limits
    # (30 RPM / 14,400 per day — see RATE_LIMIT_RPM in groq_provider.py);
    # larger models on Groq typically get a LOWER per-model cap.
    groq_model: str = "llama-3.1-8b-instant"
    # No local GPU/VRAM constraint. Groq's free tier is far more generous
    # than OpenRouter's (30 RPM / 14,400 per day vs OpenRouter's 20 RPM /
    # 50 per day), so there's less pressure to maximize batch size purely
    # to conserve daily quota. Starts at the same value as OpenRouter's
    # for consistency; lower it if translations come back with low
    # cue-recovery counts.
    groq_batch_token_budget: int = 4000
    # Mirrors nvidia_concurrent_batch_window / openrouter_concurrent_batch_window.
    groq_concurrent_batch_window: int = 4

    # Scheduling
    schedule_cron: str = "0 3 * * *"
    age_threshold_days: int = 14
    # A backlog can run into the hundreds of hours of straight LLM work —
    # without a cap, "Run now" or a scheduled run will happily grind through
    # the entire queue in one go. 0 means unlimited (opt-in, not the default
    # posture for a fresh install). Only caps full-queue/scheduled runs; a
    # forced per-item re-run always bypasses this, since that's an explicit
    # one-off request.
    daily_translation_limit: int = 100
    # A brief rest between items so a long run doesn't peg the GPU
    # non-stop for hours straight. 0 disables the pause entirely.
    pause_between_items_seconds: int = 30
    # When true, a successful translation is cached to local disk instead of
    # immediately uploaded to Bazarr — items sit as 'translated_pending_upload'
    # until a separate "push queued uploads" action sends them all in one
    # burst. Lets a NAS's disks stay asleep for the whole translation run
    # (Bazarr's own upload handling is what wakes them, not anything
    # Subtitlarr does directly) and batches that wake-up into one burst
    # whenever you choose to push, instead of once per item.
    queue_uploads_enabled: bool = False

    # Independent daily crons for the two Bazarr sync jobs — deliberately
    # separate from schedule_cron (the translation job), so wanted-list and
    # source-subtitle prefetching can run ahead of a NAS waking up for
    # translation, or on their own schedule entirely. Empty string = not
    # scheduled (manual-only via the Jobs page, the original behavior).
    sync_media_cron: str = "40 9 * * *"
    sync_subs_cron: str = "40 9 * * *"

    # Runtime
    db_path: str = "/data/subtitlarr.db"
    run_concurrency: int = 1
    log_level: str = "INFO"
    port: int = 8000


settings = Settings()
