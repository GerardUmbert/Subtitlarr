from app.providers.languages import language_name
from app.providers.prompts import build_system_prompt


def test_known_codes_resolve_to_full_names():
    assert language_name("en") == "English"
    assert language_name("it") == "Italian"
    assert language_name("de") == "German"
    assert language_name("es") == "Spanish"


def test_case_insensitive():
    assert language_name("EN") == "English"
    assert language_name("It") == "Italian"


def test_unknown_code_falls_back_to_uppercased_code_not_crash():
    assert language_name("xx") == "XX"


def test_system_prompt_spells_out_full_names_not_just_bare_codes():
    """Regression test: a live run asking to translate 'from en to it'
    (bare codes only) came back entirely in German with a small local
    model. The prompt must spell out full language names, not rely on
    bare ISO codes alone, to remove that ambiguity."""
    prompt = build_system_prompt("en", "it")
    assert "English" in prompt
    assert "Italian" in prompt
    assert "German" not in prompt


def test_system_prompt_still_includes_codes_for_precision():
    prompt = build_system_prompt("en", "it")
    assert "(en)" in prompt
    assert "(it)" in prompt


def test_system_prompt_states_target_language_explicitly():
    prompt = build_system_prompt("en", "es")
    assert "output MUST be in Spanish" in prompt
