import pytest

from app.bazarr.schemas import SubtitleCue, SubtitleCueTime
from app.engine import prefetch
from app.subtitles import srt_io


def _cue(index: int, content: str) -> SubtitleCue:
    start = SubtitleCueTime(hours=0, minutes=0, seconds=index, total_seconds=index, microseconds=0)
    end = SubtitleCueTime(hours=0, minutes=0, seconds=index + 1, total_seconds=index + 1, microseconds=0)
    return SubtitleCue(index=index, content=content, start=start, end=end)


class FakeClient:
    def __init__(self, cues_by_path: dict[str, list[SubtitleCue]], fail_paths: set[str] | None = None):
        self._cues_by_path = cues_by_path
        self._fail_paths = fail_paths or set()

    async def get_subtitle_contents(self, path: str) -> list[SubtitleCue]:
        if path in self._fail_paths:
            raise RuntimeError(f"simulated fetch failure for {path}")
        return self._cues_by_path[path]


@pytest.mark.asyncio
async def test_prefetch_writes_one_scratch_file_per_item(tmp_path):
    client = FakeClient({
        "/a.srt": [_cue(1, "Hello.")],
        "/b.srt": [_cue(1, "World.")],
    })
    ready_items = [
        {"item": {"id": 101}, "source_path": "/a.srt"},
        {"item": {"id": 102}, "source_path": "/b.srt"},
    ]
    scratch_dir = tmp_path / "scratch"

    result = await prefetch.prefetch_source_subtitles(client, ready_items, scratch_dir)

    assert set(result.keys()) == {101, 102}
    assert result[101].exists()
    assert result[102].exists()

    subs = srt_io.parse_srt_bytes(result[101].read_bytes())
    assert subs[0].content == "Hello."


@pytest.mark.asyncio
async def test_prefetch_skips_failed_items_without_aborting_others(tmp_path):
    """One item's fetch failing must not prevent the others from being
    prefetched — translate_item() falls back to a live fetch for whatever
    isn't in the returned map."""
    client = FakeClient(
        {"/a.srt": [_cue(1, "Hello.")], "/b.srt": [_cue(1, "World.")]},
        fail_paths={"/a.srt"},
    )
    ready_items = [
        {"item": {"id": 101}, "source_path": "/a.srt"},
        {"item": {"id": 102}, "source_path": "/b.srt"},
    ]
    scratch_dir = tmp_path / "scratch"

    result = await prefetch.prefetch_source_subtitles(client, ready_items, scratch_dir)

    assert 101 not in result  # failed fetch, excluded
    assert 102 in result  # unaffected by the other item's failure


@pytest.mark.asyncio
async def test_prefetch_preserves_cue_timing_and_content_round_trip(tmp_path):
    """The scratch file must round-trip through compose/parse without
    losing timing or content — translate_item() reassembles onto whatever
    timing/index it reads back from this file. Note: srt.compose() always
    renumbers cues sequentially starting from 1 (confirmed — this is the
    `srt` library's own behavior, not something prefetch.py controls), so
    the ORIGINAL raw index isn't expected to survive — only timing and
    content need to, since the cache is written and read back within the
    same translate_item() call and never cross-referenced against a
    separately-fetched index list."""
    t_start = SubtitleCueTime(hours=0, minutes=1, seconds=30, total_seconds=90, microseconds=500000)
    t_end = SubtitleCueTime(hours=0, minutes=1, seconds=33, total_seconds=93, microseconds=0)
    cue = SubtitleCue(index=5, content="Timed line.", start=t_start, end=t_end)
    client = FakeClient({"/a.srt": [cue]})
    ready_items = [{"item": {"id": 101}, "source_path": "/a.srt"}]
    scratch_dir = tmp_path / "scratch"

    result = await prefetch.prefetch_source_subtitles(client, ready_items, scratch_dir)
    subs = srt_io.parse_srt_bytes(result[101].read_bytes())

    assert subs[0].content == "Timed line."
    assert subs[0].start.total_seconds() == pytest.approx(90.5)
    assert subs[0].end.total_seconds() == pytest.approx(93.0)


def test_cleanup_scratch_file_removes_it(tmp_path):
    f = tmp_path / "101.srt"
    f.write_text("content")
    prefetch.cleanup_scratch_file(f)
    assert not f.exists()


def test_cleanup_scratch_file_handles_none():
    prefetch.cleanup_scratch_file(None)  # must not raise


@pytest.mark.asyncio
async def test_prefetch_reuses_existing_cached_file_instead_of_refetching(tmp_path):
    """Regression test for a real design gap: an earlier version used a
    per-run_id scratch subfolder, so a failed item's cached file from a
    PREVIOUS run was orphaned in that run's now-abandoned folder and never
    found by a later run — defeating the whole point of keeping a failed
    item's cache around for retry. With a shared flat directory, a file
    already on disk for an item_id must be reused, not fetched again."""
    client = FakeClient({"/a.srt": [_cue(1, "Hello.")]})
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    # Simulate a file left over from an earlier run's failed attempt —
    # deliberately DIFFERENT content from what the fake client would
    # return, so we can prove it wasn't re-fetched.
    pre_existing = scratch_dir / "101.srt"
    pre_existing.write_text("1\n00:00:00,000 --> 00:00:01,000\nLeftover from a previous run.\n")

    ready_items = [{"item": {"id": 101}, "source_path": "/a.srt"}]
    result = await prefetch.prefetch_source_subtitles(client, ready_items, scratch_dir)

    assert result[101] == pre_existing
    subs = srt_io.parse_srt_bytes(result[101].read_bytes())
    assert subs[0].content == "Leftover from a previous run."  # NOT re-fetched


@pytest.mark.asyncio
async def test_prefetch_only_fetches_items_missing_from_cache(tmp_path):
    """A mix of cached and uncached items — only the uncached one should
    trigger a real Bazarr fetch."""
    fetch_calls = []

    class TrackingClient(FakeClient):
        async def get_subtitle_contents(self, path):
            fetch_calls.append(path)
            return await super().get_subtitle_contents(path)

    client = TrackingClient({"/a.srt": [_cue(1, "Hello.")], "/b.srt": [_cue(1, "World.")]})
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    (scratch_dir / "101.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nCached.\n")

    ready_items = [
        {"item": {"id": 101}, "source_path": "/a.srt"},  # already cached
        {"item": {"id": 102}, "source_path": "/b.srt"},  # not cached
    ]
    result = await prefetch.prefetch_source_subtitles(client, ready_items, scratch_dir)

    assert fetch_calls == ["/b.srt"]  # only the uncached item was fetched
    assert set(result.keys()) == {101, 102}
