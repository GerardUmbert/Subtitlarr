from app.providers.ollama_provider import DEFAULT_OLLAMA_TIMEOUT_SECONDS, _default_timeout_for_context


def test_default_timeout_is_generous_but_bounded():
    """Regression test: a real live request timed out at a fixed 300s once
    batch sizes grew (from raising num_ctx). The default must be higher
    than that old value, but grounded in what was actually observed
    (~3 min real translations on the user's hardware) rather than an
    unbounded scale-with-context formula that could balloon to 17+ minutes."""
    assert DEFAULT_OLLAMA_TIMEOUT_SECONDS > 300.0
    assert DEFAULT_OLLAMA_TIMEOUT_SECONDS <= 900.0  # ~15 min hard ceiling, not open-ended


def test_timeout_is_flat_regardless_of_context_size():
    """Deliberately flat, not scaled with num_ctx — a 3x-normal cap is
    simpler to reason about than a formula, and this hardware's batches
    complete in ~3 minutes regardless of context window size in practice."""
    assert _default_timeout_for_context(8192) == DEFAULT_OLLAMA_TIMEOUT_SECONDS
    assert _default_timeout_for_context(131072) == DEFAULT_OLLAMA_TIMEOUT_SECONDS
