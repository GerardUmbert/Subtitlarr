from datetime import timedelta

import srt

from app.subtitles.srt_io import chunk_cues


def _make_cues(n: int, content_len: int = 50) -> list[srt.Subtitle]:
    return [
        srt.Subtitle(
            index=i + 1,
            start=timedelta(seconds=i),
            end=timedelta(seconds=i + 1),
            content="x" * content_len,
        )
        for i in range(n)
    ]


def test_small_file_fits_in_one_batch():
    subs = _make_cues(10, content_len=20)
    batches = chunk_cues(subs, max_tokens_per_batch=900)
    assert len(batches) == 1
    assert len(batches[0]) == 10


def test_large_file_splits_into_multiple_batches():
    """Regression test: a full movie's dialogue (e.g. 1071 cues, as seen in
    the real failure this fixes) must never be sent as a single prompt to a
    small-context-window model — it gets silently truncated and the
    reconciler recovers ~0% of cues."""
    subs = _make_cues(1071, content_len=60)
    batches = chunk_cues(subs, max_tokens_per_batch=900)
    assert len(batches) > 1

    # every cue accounted for exactly once, in original order, none split
    all_indices = [sub.index for batch in batches for sub in batch]
    assert all_indices == list(range(1, 1072))


def test_no_batch_exceeds_the_token_budget():
    subs = _make_cues(500, content_len=100)
    max_tokens = 900
    batches = chunk_cues(subs, max_tokens_per_batch=max_tokens)
    max_chars = max_tokens * 3.2
    for batch in batches:
        total_chars = sum(len(s.content) + len(str(s.index)) + 2 for s in batch)
        # allow the last cue in a batch to push slightly over, since a
        # single oversized cue can't be split mid-cue — but no batch with
        # more than one cue should badly exceed the budget
        if len(batch) > 1:
            assert total_chars <= max_chars * 1.1


def test_single_oversized_cue_still_gets_its_own_batch_not_dropped():
    """A single cue larger than the whole budget must still be included
    (as its own batch) rather than silently lost."""
    subs = _make_cues(1, content_len=10000)
    batches = chunk_cues(subs, max_tokens_per_batch=900)
    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_empty_input_returns_no_batches():
    assert chunk_cues([]) == []
