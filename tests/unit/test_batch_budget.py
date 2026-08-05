from app.engine.translator import _batch_token_budget


def test_default_8k_context_matches_old_conservative_behavior():
    """The old hardcoded default was 900 tokens/batch at implicit ~4-8K
    context. This confirms the formula produces a similarly conservative
    budget at the same context size, not a regression for existing users
    who haven't touched the setting."""
    budget = _batch_token_budget(8192)
    assert 400 <= budget <= 3500


def test_larger_context_window_yields_larger_batches():
    """The actual feature request: raising num_ctx should reduce the
    number of batches needed for a given file, not just be ignored."""
    small = _batch_token_budget(8192)
    large = _batch_token_budget(32768)
    huge = _batch_token_budget(131072)
    assert large > small
    assert huge > large


def test_never_goes_below_the_conservative_floor():
    assert _batch_token_budget(0) == 400
    assert _batch_token_budget(100) == 400


def test_roughly_scales_linearly_with_context_size():
    small = _batch_token_budget(16384)
    double = _batch_token_budget(32768)
    # not exact due to fixed overhead subtraction, but should be in the
    # right ballpark (within 20%) rather than flat or wildly divergent
    assert 1.6 <= (double / small) <= 2.4


def test_override_bypasses_the_auto_formula():
    """Regression test: a live run recovered only 1/106 cues at the
    auto-scaled ~3496-token batch size (num_ctx=8192), where the same model
    reliably recovered ~61/61 cues at the old flat 900-token batches — small
    models can lose reliable output formatting well before they run out of
    raw context. The UI-exposed override must take priority over num_ctx
    scaling regardless of context size."""
    assert _batch_token_budget(8192, override=900) == 900
    assert _batch_token_budget(131072, override=900) == 900


def test_override_of_zero_falls_back_to_auto_formula():
    assert _batch_token_budget(8192, override=0) == _batch_token_budget(8192)
