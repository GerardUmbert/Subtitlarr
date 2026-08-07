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


def test_spanish_variant_defaults_to_spain_without_any_explicit_choice():
    """Unlike the Catalan Vegeta addon (opt-in, defaults off), Spanish's
    default variant applies with NO language_variants dict passed at all —
    a real translation confirmed live defaulted to Latin American
    colloquial phrasing ("¿Qué anduvo Missy ahora?") with the bare 'es'
    code and no way to request otherwise, so this must apply by default."""
    prompt = build_system_prompt("en", "es")
    assert "Peninsular" in prompt or "Castilian" in prompt
    assert "Spain" in prompt


def test_spanish_variant_can_be_switched_to_mexican():
    prompt = build_system_prompt("en", "es", language_variants={"es": "es-MX"})
    assert "Use Mexican Spanish" in prompt
    assert "Mexico" in prompt


def test_spanish_variant_can_be_switched_to_argentine():
    prompt = build_system_prompt("en", "es", language_variants={"es": "es-AR"})
    assert "Argentina" in prompt
    assert "voseo" in prompt.lower() or "vos" in prompt


def test_spanish_variant_can_be_switched_to_generic_latin_american():
    prompt = build_system_prompt("en", "es", language_variants={"es": "es-419"})
    assert "Use general neutral Latin American Spanish" in prompt


def test_unrecognized_variant_key_falls_back_to_the_language_default():
    prompt = build_system_prompt("en", "es", language_variants={"es": "es-does-not-exist"})
    assert "Peninsular" in prompt or "Castilian" in prompt


def test_language_variant_only_applies_to_its_own_target_language():
    prompt = build_system_prompt("en", "ca", language_variants={"es": "es-MX"})
    assert "Peninsular" not in prompt
    assert "Mexico" not in prompt


def test_language_variant_and_catalan_vegeta_addons_can_coexist():
    """Both addons only ever trigger for their own target language (es vs
    ca respectively), so they should never actually appear together in
    practice — but the function must not error or interfere between them
    when both are set regardless of target."""
    prompt = build_system_prompt(
        "en", "es", catalan_vegeta_insults=True, language_variants={"es": "es-ES"}
    )
    assert "Peninsular" in prompt or "Castilian" in prompt
    assert "Vegeta" not in prompt  # target is es, not ca — Vegeta addon must not leak in


def test_portuguese_defaults_to_european():
    prompt = build_system_prompt("en", "pt")
    assert "Portugal" in prompt
    assert "Brazilian" not in prompt or "NOT Brazilian" in prompt


def test_portuguese_variant_can_be_switched_to_brazilian():
    prompt = build_system_prompt("en", "pt", language_variants={"pt": "pt-BR"})
    assert "Brazil" in prompt


def test_english_defaults_to_american_not_the_language_home_country():
    """en is the deliberate exception to the "defaults to the language's
    own home country" rule — American English is the far more common
    expected target, not British English."""
    prompt = build_system_prompt("es", "en")
    assert "American" in prompt
    assert "United States" in prompt


def test_english_variant_can_be_switched_to_british():
    prompt = build_system_prompt("es", "en", language_variants={"en": "en-GB"})
    assert "British" in prompt
    assert "United Kingdom" in prompt


def test_french_defaults_to_france():
    prompt = build_system_prompt("en", "fr")
    assert "France" in prompt or "Metropolitan" in prompt


def test_french_variant_can_be_switched_to_quebecois():
    prompt = build_system_prompt("en", "fr", language_variants={"fr": "fr-CA"})
    assert "Québec" in prompt


def test_french_variant_can_be_switched_to_belgian():
    prompt = build_system_prompt("en", "fr", language_variants={"fr": "fr-BE"})
    assert "Belgium" in prompt


def test_french_variant_can_be_switched_to_swiss():
    prompt = build_system_prompt("en", "fr", language_variants={"fr": "fr-CH"})
    assert "Switzerland" in prompt


def test_chinese_defaults_to_simplified_mainland():
    prompt = build_system_prompt("en", "zh")
    assert "Simplified" in prompt
    assert "Mainland" in prompt


def test_chinese_variant_can_be_switched_to_traditional():
    prompt = build_system_prompt("en", "zh", language_variants={"zh": "zh-Hant"})
    assert "Traditional" in prompt


def test_language_with_no_variants_defined_gets_no_addon():
    """Italian has no LANGUAGE_VARIANTS entry — build_system_prompt must
    not error or append anything when the target language isn't in the
    registry at all."""
    prompt = build_system_prompt("en", "it")
    assert "Peninsular" not in prompt
    assert "Simplified" not in prompt
