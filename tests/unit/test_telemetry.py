import httpx
import pytest
import respx

from app import telemetry
from app.config import Settings
from app.db import database, engine_instances_repo, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_build_payload_never_includes_identifying_fields(conn):
    engine_instances_repo.create_instance(conn, name="my-ollama", provider_type="ollama", config={"base_url": "http://secret-host:11434"})

    payload = telemetry.build_payload(conn)

    assert payload["engine_types"] == "ollama"
    assert "instance_id" in payload
    dumped = str(payload)
    assert "secret-host" not in dumped
    assert "my-ollama" not in dumped


def test_first_payload_delta_equals_full_current_total(conn):
    """Regression: nothing has been sent yet, so the first ping's delta
    must equal the full pre-existing total, not zero — otherwise an
    instance that already has history when telemetry is turned on would
    have that history silently never counted."""
    conn.execute(
        "INSERT INTO run_history (started_at, finished_at, triggered_by) VALUES ('t', 't', 'manual_full')"
    )
    conn.commit()

    payload = telemetry.build_payload(conn)
    assert payload["translation_runs_delta"] == 1


@pytest.mark.asyncio
async def test_send_ping_only_reports_new_activity_next_time(conn):
    """Second ping's delta must reflect only what changed since the first
    successful send, not the full total again — otherwise summing every
    ping in GA4 would double-count everything after the first."""
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    conn.execute(
        "INSERT INTO run_history (started_at, finished_at, triggered_by) VALUES ('t', 't', 'manual_full')"
    )
    conn.commit()

    with respx.mock:
        route = respx.post("https://www.google-analytics.com/mp/collect").mock(
            return_value=httpx.Response(204)
        )
        await telemetry.send_ping(conn, settings)
        first_body = route.calls[0].request.content
        assert b'"translation_runs_delta":1' in first_body or b'"translation_runs_delta": 1' in first_body

    conn.execute(
        "INSERT INTO run_history (started_at, finished_at, triggered_by) VALUES ('t2', 't2', 'manual_full')"
    )
    conn.commit()

    with respx.mock:
        route = respx.post("https://www.google-analytics.com/mp/collect").mock(
            return_value=httpx.Response(204)
        )
        await telemetry.send_ping(conn, settings)
        second_body = route.calls[0].request.content
        assert b'"translation_runs_delta":1' in second_body or b'"translation_runs_delta": 1' in second_body


@pytest.mark.asyncio
async def test_failed_send_does_not_advance_last_sent_counters(conn):
    """If the HTTP call fails, the next ping's delta must still include
    whatever wasn't successfully reported — otherwise a network blip
    permanently loses that activity from the global total."""
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    conn.execute(
        "INSERT INTO run_history (started_at, finished_at, triggered_by) VALUES ('t', 't', 'manual_full')"
    )
    conn.commit()

    with respx.mock:
        respx.post("https://www.google-analytics.com/mp/collect").mock(
            side_effect=httpx.ConnectError("refused")
        )
        await telemetry.send_ping(conn, settings)

    payload = telemetry.build_payload(conn)
    assert payload["translation_runs_delta"] == 1


def test_instance_id_is_stable_across_calls(conn):
    first = telemetry.build_payload(conn)["instance_id"]
    second = telemetry.build_payload(conn)["instance_id"]
    assert first == second


@pytest.mark.asyncio
async def test_send_ping_noop_when_disabled(conn):
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = False
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    with respx.mock:
        route = respx.post("https://www.google-analytics.com/mp/collect").mock(
            return_value=httpx.Response(204)
        )
        await telemetry.send_ping(conn, settings)
        assert route.call_count == 0


@pytest.mark.asyncio
async def test_send_ping_noop_without_credentials(conn):
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True

    with respx.mock:
        route = respx.post("https://www.google-analytics.com/mp/collect").mock(
            return_value=httpx.Response(204)
        )
        await telemetry.send_ping(conn, settings)
        assert route.call_count == 0


@pytest.mark.asyncio
async def test_send_ping_posts_to_ga4_when_configured(conn):
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    with respx.mock:
        route = respx.post("https://www.google-analytics.com/mp/collect").mock(
            return_value=httpx.Response(204)
        )
        await telemetry.send_ping(conn, settings)
        assert route.call_count == 1
        request = route.calls[0].request
        assert request.url.params["measurement_id"] == "G-TEST"
        assert request.url.params["api_secret"] == "secret"


@pytest.mark.asyncio
async def test_send_ping_swallows_http_errors(conn):
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    with respx.mock:
        respx.post("https://www.google-analytics.com/mp/collect").mock(
            side_effect=httpx.ConnectError("refused")
        )
        await telemetry.send_ping(conn, settings)  # must not raise


def _last_telemetry_event(conn):
    row = conn.execute(
        "SELECT * FROM job_events WHERE job = 'telemetry' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


@pytest.mark.asyncio
async def test_successful_ping_is_visible_as_a_job_event(conn):
    """Regression: previously a successful ping left NO trace anywhere —
    only a warning log line on failure. Silence was ambiguous between
    "it worked" and "the cron never fired." A job_events row gives it
    the same Jobs/History page visibility every other cron job has."""
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    with respx.mock:
        respx.post("https://www.google-analytics.com/mp/collect").mock(
            return_value=httpx.Response(204)
        )
        await telemetry.send_ping(conn, settings, triggered_by="manual")

    event = _last_telemetry_event(conn)
    assert event is not None
    assert event["status"] == "done"
    assert event["triggered_by"] == "manual"
    assert "sent" in event["result"]


@pytest.mark.asyncio
async def test_disabled_ping_is_visible_as_a_skipped_job_event(conn):
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = False
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    await telemetry.send_ping(conn, settings, triggered_by="cron")

    event = _last_telemetry_event(conn)
    assert event is not None
    assert event["status"] == "done"
    assert "disabled" in event["result"]


@pytest.mark.asyncio
async def test_failed_ping_is_visible_as_a_failed_job_event(conn):
    settings = Settings(_env_file=None)
    settings.telemetry_enabled = True
    settings.telemetry_measurement_id = "G-TEST"
    settings.telemetry_api_secret = "secret"

    with respx.mock:
        respx.post("https://www.google-analytics.com/mp/collect").mock(
            side_effect=httpx.ConnectError("refused")
        )
        await telemetry.send_ping(conn, settings)

    event = _last_telemetry_event(conn)
    assert event is not None
    assert event["status"] == "failed"
    assert event["error"] is not None
