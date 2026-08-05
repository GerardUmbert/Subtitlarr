import pytest

from app.config import Settings
from app.db import database
from app.engine.runner import RunController


class FakeClosableClient:
    def __init__(self, name):
        self.name = name
        self.closed = False

    async def iter_all_wanted_episodes(self):
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed")
        return
        yield  # pragma: no cover

    async def iter_all_wanted_movies(self):
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed")
        return
        yield  # pragma: no cover

    async def aclose(self):
        self.closed = True


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


@pytest.mark.asyncio
async def test_poll_uses_current_client_not_stale_reference(conn):
    """Regression test: saving Bazarr connection settings closes the old
    client and swaps in a new one. RunController must always fetch the
    CURRENT client at call time, not hold a fixed reference captured at
    construction — otherwise every call after a settings save fails with
    'Cannot send a request, as the client has been closed'."""
    clients = {"current": FakeClosableClient("first")}

    controller = RunController(conn, lambda: clients["current"], Settings())

    # first poll works fine with the original client
    await controller.poll()

    # simulate what /api/config/bazarr POST does: close old, swap in new
    old_client = clients["current"]
    await old_client.aclose()
    clients["current"] = FakeClosableClient("second")

    # this must use the NEW client, not the closed old one
    await controller.poll()  # should not raise
