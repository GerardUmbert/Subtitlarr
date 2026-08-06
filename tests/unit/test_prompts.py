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


def test_european_spanish_addon_applies_by_default():
    """Unlike the Catalan Vegeta addon (opt-in, defaults off),
    european_spanish defaults to True — a real translation confirmed live
    defaulted to Latin American colloquial phrasing ("¿Qué anduvo Missy
    ahora?") with the bare 'es' code and no way to request otherwise, so
    this must apply WITHOUT the caller passing anything explicitly."""
    prompt = build_system_prompt("en", "es")
    assert "Peninsular" in prompt or "Castilian" in prompt
    assert "Spain" in prompt


def test_european_spanish_addon_can_be_disabled():
    prompt = build_system_prompt("en", "es", european_spanish=False)
    assert "Peninsular" not in prompt
    assert "Castilian" not in prompt


def test_european_spanish_addon_only_applies_to_spanish_target():
    prompt = build_system_prompt("en", "ca", european_spanish=True)
    assert "Peninsular" not in prompt
    assert "Castilian" not in prompt


def test_european_spanish_and_catalan_vegeta_addons_can_coexist():
    """Both addons only ever trigger for their own target language (es vs
    ca respectively), so they should never actually appear together in
    practice — but the function must not error or interfere between them
    when both flags are passed regardless of target."""
    prompt = build_system_prompt("en", "es", catalan_vegeta_insults=True, european_spanish=True)
    assert "Peninsular" in prompt or "Castilian" in prompt
    assert "Vegeta" not in prompt  # target is es, not ca — Vegeta addon must not leak in
