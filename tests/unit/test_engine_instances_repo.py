from datetime import datetime, timedelta, timezone

import pytest

from app.db import database, engine_instances_repo


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _expire_burst_debounce(conn, instance_id: int) -> None:
    """record_rate_limited_failure() debounces failures within
    BURST_DEBOUNCE_SECONDS of each other into a single strike (see its
    docstring) — real callers are separated by real elapsed time between
    items, but a test calling it in a tight loop needs to fast-forward
    last_failure_at itself to exercise more than one strike."""
    past = (
        datetime.now(timezone.utc)
        - timedelta(seconds=engine_instances_repo.BURST_DEBOUNCE_SECONDS + 1)
    ).isoformat()
    with conn:
        conn.execute(
            "UPDATE engine_instances SET last_failure_at = ? WHERE id = ?", (past, instance_id)
        )


def _expire_auth_burst_debounce(conn, instance_id: int) -> None:
    """record_auth_failure() debounces failures within
    BURST_DEBOUNCE_SECONDS of each other into a single strike, same
    reasoning/mechanism as record_rate_limited_failure — see
    _expire_burst_debounce above."""
    past = (
        datetime.now(timezone.utc)
        - timedelta(seconds=engine_instances_repo.BURST_DEBOUNCE_SECONDS + 1)
    ).isoformat()
    with conn:
        conn.execute(
            "UPDATE engine_instances SET last_auth_failure_at = ? WHERE id = ?", (past, instance_id)
        )


def test_create_appends_at_end_of_sort_order(conn):
    first = engine_instances_repo.create_instance(
        conn, name="A", provider_type="gemini", config={}
    )
    second = engine_instances_repo.create_instance(
        conn, name="B", provider_type="nvidia", config={}
    )
    listed = engine_instances_repo.list_instances(conn)
    assert [i["id"] for i in listed] == [first["id"], second["id"]]


def test_update_partial_only_changes_given_fields(conn):
    instance = engine_instances_repo.create_instance(
        conn, name="A", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    updated = engine_instances_repo.update_instance(conn, instance["id"], enabled=False)
    assert updated["enabled"] is False
    assert updated["name"] == "A"
    assert updated["config"] == {"api_key": "x", "model": "m"}


def test_delete_instance(conn):
    instance = engine_instances_repo.create_instance(
        conn, name="A", provider_type="gemini", config={}
    )
    engine_instances_repo.delete_instance(conn, instance["id"])
    assert engine_instances_repo.list_instances(conn) == []


def test_reorder_moves_ids_to_given_positions(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    b = engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})
    c = engine_instances_repo.create_instance(conn, name="C", provider_type="groq", config={})

    engine_instances_repo.reorder_instances(conn, [c["id"], a["id"], b["id"]])

    listed = engine_instances_repo.list_instances(conn)
    assert [i["id"] for i in listed] == [c["id"], a["id"], b["id"]]


def test_reorder_appends_ids_left_out_of_the_given_list(conn):
    """A stale/incomplete id list from the UI must not silently orphan a
    row outside the visible reorder — anything not mentioned keeps its
    relative order, appended after the given ones."""
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    b = engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})
    c = engine_instances_repo.create_instance(conn, name="C", provider_type="groq", config={})

    engine_instances_repo.reorder_instances(conn, [b["id"]])  # only mentions B

    listed = engine_instances_repo.list_instances(conn)
    assert listed[0]["id"] == b["id"]
    assert {i["id"] for i in listed[1:]} == {a["id"], c["id"]}


def test_get_cascade_excludes_disabled_instances(conn):
    engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={}, enabled=True)
    engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={}, enabled=False)

    cascade = engine_instances_repo.get_cascade(conn)
    assert [i["name"] for i in cascade] == ["A"]


def test_get_cascade_stops_at_separator(conn):
    engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    engine_instances_repo.create_instance(
        conn, name="sep", provider_type=engine_instances_repo.SEPARATOR_TYPE, config={}
    )
    engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})

    cascade = engine_instances_repo.get_cascade(conn)
    assert [i["name"] for i in cascade] == ["A"]  # B never reached — separator excludes it


def test_get_cascade_empty_when_no_instances(conn):
    assert engine_instances_repo.get_cascade(conn) == []


def test_get_cascade_excludes_rate_limited_instance(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    engine_instances_repo.update_instance(conn, a["id"])  # no-op, just confirms update path works
    with conn:
        conn.execute(
            "UPDATE engine_instances SET rate_limited_until = ? WHERE id = ?", (future, a["id"])
        )

    cascade = engine_instances_repo.get_cascade(conn)
    assert [i["name"] for i in cascade] == ["B"]


def test_get_cascade_includes_instance_whose_cooldown_already_expired(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with conn:
        conn.execute(
            "UPDATE engine_instances SET rate_limited_until = ? WHERE id = ?", (past, a["id"])
        )

    cascade = engine_instances_repo.get_cascade(conn)
    assert [i["name"] for i in cascade] == ["A"]


def test_record_rate_limited_failure_trips_cooldown_after_threshold(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    tripped = []
    for _ in range(engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD):
        _expire_burst_debounce(conn, a["id"])
        tripped.append(engine_instances_repo.record_rate_limited_failure(conn, a["id"]))

    assert tripped == [False] * (engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD - 1) + [True]

    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["rate_limited_until"] is not None
    assert instance["consecutive_failures"] == 0  # reset after tripping

    # And the cascade builder actually excludes it now.
    assert engine_instances_repo.get_cascade(conn) == []


def test_record_rate_limited_failure_debounces_a_concurrent_burst(conn):
    """Regression test for a real live incident: a healthy Gemini account
    (well under its documented RPM/TPM/RPD quota per its own dashboard)
    still tripped the 3-strike cooldown, because several batches in the
    SAME concurrent window each independently 429'd on a short burst
    limit and each called record_rate_limited_failure() separately —
    mistaking one burst event for three separate ones. Calls made back-
    to-back (no elapsed time) must count as ONE strike."""
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    tripped = [
        engine_instances_repo.record_rate_limited_failure(conn, a["id"]) for _ in range(5)
    ]

    assert tripped == [False] * 5  # never trips — all 5 calls collapse into 1 strike
    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["consecutive_failures"] == 1


def test_record_success_resets_consecutive_failures(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    engine_instances_repo.record_rate_limited_failure(conn, a["id"])
    _expire_burst_debounce(conn, a["id"])
    engine_instances_repo.record_rate_limited_failure(conn, a["id"])
    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["consecutive_failures"] == 2

    engine_instances_repo.record_success(conn, a["id"])
    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["consecutive_failures"] == 0
    assert instance["last_failure_at"] is None


def test_clear_rate_limit_removes_cooldown_early(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    for _ in range(engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD):
        _expire_burst_debounce(conn, a["id"])
        engine_instances_repo.record_rate_limited_failure(conn, a["id"])
    assert engine_instances_repo.get_instance(conn, a["id"])["rate_limited_until"] is not None

    engine_instances_repo.clear_rate_limit(conn, a["id"])

    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["rate_limited_until"] is None
    assert instance["consecutive_failures"] == 0
    assert [i["name"] for i in engine_instances_repo.get_cascade(conn)] == ["A"]


def test_clear_all_rate_limits_clears_every_flagged_instance_and_returns_count(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    b = engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})
    c = engine_instances_repo.create_instance(conn, name="C", provider_type="groq", config={})

    for instance in (a, b):
        for _ in range(engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD):
            _expire_burst_debounce(conn, instance["id"])
            engine_instances_repo.record_rate_limited_failure(conn, instance["id"])
    # C is left healthy — never flagged.

    cleared = engine_instances_repo.clear_all_rate_limits(conn)

    assert cleared == 2
    assert engine_instances_repo.get_instance(conn, a["id"])["rate_limited_until"] is None
    assert engine_instances_repo.get_instance(conn, b["id"])["rate_limited_until"] is None
    assert engine_instances_repo.get_instance(conn, c["id"])["rate_limited_until"] is None
    assert {i["name"] for i in engine_instances_repo.get_cascade(conn)} == {"A", "B", "C"}


def test_clear_all_rate_limits_is_a_no_op_when_nothing_is_flagged(conn):
    engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    assert engine_instances_repo.clear_all_rate_limits(conn) == 0


def test_get_cascade_excludes_auth_disabled_instance(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with conn:
        conn.execute(
            "UPDATE engine_instances SET auth_disabled_until = ? WHERE id = ?", (future, a["id"])
        )

    cascade = engine_instances_repo.get_cascade(conn)
    assert [i["name"] for i in cascade] == ["B"]


def test_get_cascade_includes_instance_whose_auth_cooldown_already_expired(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with conn:
        conn.execute(
            "UPDATE engine_instances SET auth_disabled_until = ? WHERE id = ?", (past, a["id"])
        )

    cascade = engine_instances_repo.get_cascade(conn)
    assert [i["name"] for i in cascade] == ["A"]


def test_record_auth_failure_trips_cooldown_after_threshold(conn):
    """Regression test: a disabled/deleted Google Cloud service account
    returns 401 on every request — without this, the instance would keep
    getting tried (and failing) on every subsequent item forever, since a
    401 never resolves itself the way a 429 rate limit does."""
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    tripped = []
    for _ in range(engine_instances_repo.AUTH_FAILURE_THRESHOLD):
        _expire_auth_burst_debounce(conn, a["id"])
        tripped.append(engine_instances_repo.record_auth_failure(conn, a["id"]))

    assert tripped == [False] * (engine_instances_repo.AUTH_FAILURE_THRESHOLD - 1) + [True]

    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["auth_disabled_until"] is not None
    assert instance["consecutive_auth_failures"] == 0  # reset after tripping
    assert instance["rate_limited_until"] is None  # NOT conflated with the rate-limit cooldown

    assert engine_instances_repo.get_cascade(conn) == []


def test_record_auth_failure_debounces_a_concurrent_burst(conn):
    """Same reasoning as the rate-limit burst-debounce regression: a
    concurrent batch window can fire several 401s from the same dead key
    within milliseconds of each other — those must count as ONE strike,
    not several."""
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    tripped = [engine_instances_repo.record_auth_failure(conn, a["id"]) for _ in range(5)]

    assert tripped == [False] * 5  # never trips — all 5 calls collapse into 1 strike
    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["consecutive_auth_failures"] == 1


def test_record_success_resets_both_rate_limit_and_auth_counters(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})

    engine_instances_repo.record_rate_limited_failure(conn, a["id"])
    engine_instances_repo.record_auth_failure(conn, a["id"])
    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["consecutive_failures"] == 1
    assert instance["consecutive_auth_failures"] == 1

    engine_instances_repo.record_success(conn, a["id"])
    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["consecutive_failures"] == 0
    assert instance["consecutive_auth_failures"] == 0
    assert instance["last_failure_at"] is None


def test_clear_rate_limit_also_clears_auth_disable(conn):
    """A successful manual Test Connection proves the key/connection is
    good again regardless of WHICH cooldown was active — clearing must
    not require the caller to know whether the instance was rate-limited
    or auth-disabled."""
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    for _ in range(engine_instances_repo.AUTH_FAILURE_THRESHOLD):
        _expire_auth_burst_debounce(conn, a["id"])
        engine_instances_repo.record_auth_failure(conn, a["id"])
    assert engine_instances_repo.get_instance(conn, a["id"])["auth_disabled_until"] is not None

    engine_instances_repo.clear_rate_limit(conn, a["id"])

    instance = engine_instances_repo.get_instance(conn, a["id"])
    assert instance["auth_disabled_until"] is None
    assert instance["consecutive_auth_failures"] == 0
    assert [i["name"] for i in engine_instances_repo.get_cascade(conn)] == ["A"]


def test_clear_all_rate_limits_also_clears_auth_disabled_instances(conn):
    a = engine_instances_repo.create_instance(conn, name="A", provider_type="gemini", config={})
    b = engine_instances_repo.create_instance(conn, name="B", provider_type="nvidia", config={})

    for _ in range(engine_instances_repo.AUTH_FAILURE_THRESHOLD):
        _expire_auth_burst_debounce(conn, a["id"])
        engine_instances_repo.record_auth_failure(conn, a["id"])
    for _ in range(engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD):
        _expire_burst_debounce(conn, b["id"])
        engine_instances_repo.record_rate_limited_failure(conn, b["id"])

    cleared = engine_instances_repo.clear_all_rate_limits(conn)

    assert cleared == 2
    assert engine_instances_repo.get_instance(conn, a["id"])["auth_disabled_until"] is None
    assert engine_instances_repo.get_instance(conn, b["id"])["rate_limited_until"] is None
