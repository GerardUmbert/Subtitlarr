import pytest

from app.engine import run_events


@pytest.fixture(autouse=True)
def clear_events():
    """Module-level deque is shared global state across tests — clear it
    before and after each test so one test's events don't leak into the
    next test's assertions."""
    run_events._events.clear()
    yield
    run_events._events.clear()


def test_emit_and_events_since_returns_new_events():
    run_events.emit(1, 100, 1, 5, "retrying", "nvidia: 504 — retrying in 62s")
    run_events.emit(1, 100, 2, 5, "retry_succeeded", "nvidia: succeeded on retry")

    events = run_events.events_since(0)
    assert len(events) == 2
    assert events[0].event_type == "retrying"
    assert events[1].event_type == "retry_succeeded"


def test_events_since_only_returns_events_after_the_given_id():
    run_events.emit(1, 100, 1, 5, "retrying", "first")
    first_id = run_events.events_since(0)[0].id
    run_events.emit(1, 100, 2, 5, "retrying", "second")

    events = run_events.events_since(first_id)
    assert len(events) == 1
    assert events[0].detail == "second"


def test_events_since_returns_empty_when_no_new_events():
    run_events.emit(1, 100, 1, 5, "retrying", "only one")
    latest_id = run_events.events_since(0)[0].id

    assert run_events.events_since(latest_id) == []


def test_event_ids_are_monotonically_increasing():
    run_events.emit(1, 100, 1, 1, "item_failed", "a")
    run_events.emit(1, 200, 1, 1, "item_failed", "b")
    run_events.emit(1, 300, 1, 1, "item_failed", "c")

    ids = [e.id for e in run_events.events_since(0)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 3  # all unique


def test_buffer_is_bounded():
    for i in range(run_events._MAX_EVENTS + 50):
        run_events.emit(1, i, 1, 1, "item_failed", f"event {i}")

    assert len(run_events._events) == run_events._MAX_EVENTS
    # oldest events were dropped — the earliest surviving one should be
    # well past the first ones emitted
    remaining = run_events.events_since(0)
    assert remaining[0].detail == "event 50"
