from app.providers.prompts import build_system_prompt


def test_default_prompt_has_no_vegeta_addon():
    prompt = build_system_prompt("en", "ca")
    assert "Vegeta" not in prompt


def test_vegeta_addon_only_applies_to_catalan_target():
    prompt = build_system_prompt("en", "es", catalan_vegeta_insults=True)
    assert "Vegeta" not in prompt


def test_vegeta_addon_applies_when_target_is_catalan_and_enabled():
    prompt = build_system_prompt("en", "ca", catalan_vegeta_insults=True)
    assert "Vegeta" in prompt
    assert "insult" in prompt.lower()


def test_vegeta_addon_instructs_matching_intensity_not_a_random_pick():
    """A mild source insult shouldn't get an arbitrarily severe (or vice
    versa) Vegeta-style replacement — the model must be told to match
    intensity/context, not just pick from the general style."""
    prompt = build_system_prompt("en", "ca", catalan_vegeta_insults=True)
    assert "intensity" in prompt.lower()
    assert "context" in prompt.lower()


def test_vegeta_addon_does_not_replace_the_base_translation_instructions():
    """The addon must be additive — insults get special treatment, but the
    rest of the translation still follows the normal accuracy rules."""
    base = build_system_prompt("en", "ca")
    with_addon = build_system_prompt("en", "ca", catalan_vegeta_insults=True)
    assert with_addon.startswith(base)
