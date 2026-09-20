import pytest

from app.api.external_translate import TranslateRequest
from app.bazarr.schemas import SubtitleCue, SubtitleCueTime
from app.db import database, repository
from app.engine import external_translate as external_translate_module
from app.engine.external_translate import run_external_translate_job


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _cue(index, content, start_s, end_s):
    return SubtitleCue(
        index=index, content=content, proprietary="",
        start=SubtitleCueTime(hours=0, minutes=0, seconds=start_s, total_seconds=start_s, microseconds=0),
        end=SubtitleCueTime(hours=0, minutes=0, seconds=end_s, total_seconds=end_s, microseconds=0),
    )


class FakeProvider:
    name = "fake-engine"
    model = "fake-model"
    provider_type = "fake"
    batch_token_budget = 0

    async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults, language_variants):
        # Echo back each "index\ncontent" pair with a recognizable prefix,
        # same shape reconciler.reassemble expects from a real provider.
        lines = []
        for block in dialogue_text.split("\n\n"):
            index, content = block.split("\n", 1)
            lines.append(f"{index}\nTRANSLATED: {content}")
        return "\n\n".join(lines)


def _stub_cascade(monkeypatch):
    fake_instance = {
        "id": 1, "name": "fake-engine", "provider_type": "fake",
        "enabled": True, "config": {}, "rate_limited_until": None,
    }
    monkeypatch.setattr(
        external_translate_module.engine_instances_repo, "get_cascade", lambda conn: [fake_instance]
    )
    monkeypatch.setattr(
        external_translate_module.registry,
        "build_cascade_providers",
        lambda instances: ([FakeProvider()], {"fake-engine": 1}),
    )


@pytest.mark.asyncio
async def test_run_external_translate_job_success(conn, monkeypatch):
    _stub_cascade(monkeypatch)
    source_subs = external_translate_module.srt_io.cues_from_bazarr(
        [_cue(1, "Hello there.", 1, 3), _cue(2, "How are you?", 4, 6)]
    )
    job_id = repository.create_external_translate_job(conn, "en", "ca")
    job_event_id = repository.start_job_event(conn, "external_translate", triggered_by="api")

    await run_external_translate_job(conn, job_id, source_subs, "en", "ca", job_event_id)

    job = repository.get_external_translate_job(conn, job_id)
    assert job["status"] == "done"
    assert job["engine_used"] == "fake-engine"
    assert job["model_used"] == "fake-model"
    assert "TRANSLATED: Hello there." in job["result_srt"]
    assert "TRANSLATED: How are you?" in job["result_srt"]
    # Disclaimer cue prepended, same as a normal Bazarr-sourced run.
    assert "Subtitlarr used AI" in job["result_srt"] or "[fake-model]" in job["result_srt"]

    # Shows up on the History page's Jobs tab, same mechanism as
    # sync/backup/language-check jobs — see repository.list_job_events.
    events = repository.list_job_events(conn)
    matching = [e for e in events if e["id"] == job_event_id]
    assert len(matching) == 1
    assert matching[0]["job"] == "external_translate"
    assert matching[0]["status"] == "done"
    assert matching[0]["triggered_by"] == "api"


@pytest.mark.asyncio
async def test_run_external_translate_job_no_engine_configured(conn, monkeypatch):
    monkeypatch.setattr(
        external_translate_module.engine_instances_repo, "get_cascade", lambda conn: []
    )
    source_subs = external_translate_module.srt_io.cues_from_bazarr([_cue(1, "Hi.", 0, 1)])
    job_id = repository.create_external_translate_job(conn, "en", "ca")
    job_event_id = repository.start_job_event(conn, "external_translate", triggered_by="api")

    await run_external_translate_job(conn, job_id, source_subs, "en", "ca", job_event_id)

    job = repository.get_external_translate_job(conn, job_id)
    assert job["status"] == "failed"
    assert "No enabled" in job["error"]

    events = repository.list_job_events(conn)
    matching = [e for e in events if e["id"] == job_event_id]
    assert matching[0]["status"] == "failed"
    assert "No enabled" in matching[0]["error"]


def test_translate_request_rejects_neither_input():
    with pytest.raises(ValueError):
        TranslateRequest(source_language="en", target_language="ca")


def test_translate_request_rejects_both_inputs():
    with pytest.raises(ValueError):
        TranslateRequest(
            source_language="en", target_language="ca",
            srt_content="1\n00:00:00,000 --> 00:00:01,000\nHi.\n",
            cues=[_cue(1, "Hi.", 0, 1)],
        )


def test_translate_request_accepts_srt_content_only():
    req = TranslateRequest(
        source_language="en", target_language="ca",
        srt_content="1\n00:00:00,000 --> 00:00:01,000\nHi.\n",
    )
    assert req.cues is None


def test_translate_request_accepts_cues_only():
    req = TranslateRequest(
        source_language="en", target_language="ca", cues=[_cue(1, "Hi.", 0, 1)],
    )
    assert req.srt_content is None


@pytest.mark.parametrize("raw,expected", [
    ("es-ES", "es"), ("pt_BR", "pt"), ("EN", "en"), ("fr-CA", "fr"), ("ca", "ca"),
])
def test_translate_request_normalizes_regional_codes(raw, expected):
    req = TranslateRequest(
        source_language=raw, target_language="ca", cues=[_cue(1, "Hi.", 0, 1)],
    )
    assert req.source_language == expected


def test_translate_request_rejects_empty_language():
    with pytest.raises(ValueError):
        TranslateRequest(source_language="", target_language="ca", cues=[_cue(1, "Hi.", 0, 1)])
