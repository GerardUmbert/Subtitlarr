import pytest

from app.engine import compare as compare_mod


class _FakeProvider:
    model = "fake-model"

    async def aclose(self):
        pass


def _instance(extra=None):
    config = {"api_key": "k", "model": "m"}
    if extra is not None:
        config["extra_instructions"] = extra
    return {"id": 1, "name": "Gemini Main", "provider_type": "gemini", "config": config}


async def _run(monkeypatch, instance, override):
    seen = {}

    def fake_build_provider(provider_type, config, *, instance_name=None):
        seen["config"] = config
        return _FakeProvider()

    async def boom(*args, **kwargs):
        raise RuntimeError("stop before translating")

    monkeypatch.setattr(compare_mod.registry, "build_provider", fake_build_provider)
    monkeypatch.setattr(compare_mod, "_translate_batches", boom)
    result = await compare_mod._run_one_engine(
        instance=instance, original_subs=[], source_lang="en", target_lang="es", item_id=-1,
        catalan_vegeta_insults=False, language_variants={}, extra_instructions_override=override,
    )
    return result, seen["config"]


@pytest.mark.asyncio
async def test_saved_extra_instructions_used_when_no_override(monkeypatch):
    result, config = await _run(monkeypatch, _instance("Be formal."), None)
    assert config["extra_instructions"] == "Be formal."
    assert result.extra_instructions == "Be formal."


@pytest.mark.asyncio
async def test_override_replaces_saved_value_for_this_run_only(monkeypatch):
    instance = _instance("Be formal.")
    result, config = await _run(monkeypatch, instance, "Use slang.")
    assert config["extra_instructions"] == "Use slang."
    assert result.extra_instructions == "Use slang."
    assert instance["config"]["extra_instructions"] == "Be formal."  # saved config untouched


@pytest.mark.asyncio
async def test_empty_override_means_none_not_fall_back_to_saved(monkeypatch):
    result, config = await _run(monkeypatch, _instance("Be formal."), "")
    assert config["extra_instructions"] == ""
    assert result.extra_instructions == ""
