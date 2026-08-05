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
    # fixed batch size regardless of context window.
    ollama_batch_token_budget: int = 0

    gemini_api_key: str = ""
    # Gemini's model lineup changes over time (1.5-flash has been retired,
    # returning 404s) — verify this is still current against
    # https://aistudio.google.com/ before relying on it, and prefer setting
    # it explicitly via the Engine page or GEMINI_MODEL env var.
    gemini_model: str = "gemini-2.0-flash"

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
    nvidia_batch_token_budget: int = 2000

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
