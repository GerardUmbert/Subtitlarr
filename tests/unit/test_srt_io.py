from pathlib import Path

import srt

from app.bazarr.schemas import SubtitleCue, SubtitleCueTime
from app.subtitles import srt_io

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_srt_bytes_basic():
    raw = (FIXTURES / "sample_en.srt").read_bytes()
    subs = srt_io.parse_srt_bytes(raw)
    assert len(subs) == 3
    assert subs[0].content == "Hello there."
    assert subs[1].content == "How are you today?"


def test_parse_srt_bytes_handles_bom():
    raw = b"\xef\xbb\xbf" + (FIXTURES / "sample_en.srt").read_bytes()
    subs = srt_io.parse_srt_bytes(raw)
    assert len(subs) == 3
    assert subs[0].content == "Hello there."


def test_extract_dialogue_text_excludes_timestamps():
    raw = (FIXTURES / "sample_en.srt").read_bytes()
    subs = srt_io.parse_srt_bytes(raw)
    text = srt_io.extract_dialogue_text(subs)
    assert "00:00:01" not in text
    assert "1\nHello there." in text
    assert "3\nI'm doing well, thanks." in text


def test_compose_srt_roundtrip():
    raw = (FIXTURES / "sample_en.srt").read_bytes()
    subs = srt_io.parse_srt_bytes(raw)
    composed = srt_io.compose_srt(subs)
    reparsed = srt_io.parse_srt_bytes(composed)
    assert [s.content for s in reparsed] == [s.content for s in subs]
    assert [s.start for s in reparsed] == [s.start for s in subs]


def test_cues_from_bazarr_matches_direct_parse():
    cues = [
        SubtitleCue(
            index=1,
            content="Hello there.",
            proprietary="",
            start=SubtitleCueTime(hours=0, minutes=0, seconds=1, total_seconds=1, microseconds=0),
            end=SubtitleCueTime(hours=0, minutes=0, seconds=3, total_seconds=3, microseconds=0),
        )
    ]
    subs = srt_io.cues_from_bazarr(cues)
    assert len(subs) == 1
    assert subs[0].content == "Hello there."
    assert subs[0].start.total_seconds() == 1
    assert subs[0].end.total_seconds() == 3


def test_parse_srt_bytes_handles_malformed_extra_blank_lines():
    malformed = (
        "1\n00:00:01,000 --> 00:00:03,000\nLine one.\n\n\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nLine two.\n"
    ).encode("utf-8")
    subs = srt_io.parse_srt_bytes(malformed)
    assert len(subs) == 2
    assert subs[1].content == "Line two."
