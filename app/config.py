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

    # Runtime
    db_path: str = "/data/subtitlarr.db"
    run_concurrency: int = 1
    log_level: str = "INFO"
    port: int = 8000


settings = Settings()
