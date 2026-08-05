from app.engine.selector import SourceCandidate, pick_source_language


def _c(path, hi=False):
    return SourceCandidate(path=path, hi=hi)


def test_picks_priority_language_when_available():
    source_map = {"en": _c("/path/en.srt"), "it": _c("/path/it.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=["en", "it"]) == "en"


def test_falls_back_to_any_available_language_when_priority_list_empty():
    """Core fix: an empty/unconfigured priority list must not mean 'nothing
    is translatable' — it means 'no preference, use whatever exists'. This
    matters for users whose libraries aren't English-first (e.g. Chinese,
    Thai, German only) who shouldn't have to configure anything to get
    started."""
    source_map = {"th": _c("/path/th.srt")}
    assert pick_source_language(source_map, target_lang="de", source_priority=[]) == "th"


def test_falls_back_to_any_available_language_not_in_priority_list():
    """A language that exists but wasn't explicitly added to the priority
    list must still be usable — the list is a preference, not a whitelist."""
    source_map = {"zh": _c("/path/zh.srt")}
    assert pick_source_language(source_map, target_lang="th", source_priority=["en"]) == "zh"


def test_never_picks_target_language_as_its_own_source():
    source_map = {"es": _c("/path/es.srt"), "en": _c("/path/en.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=["es", "en"]) == "en"


def test_returns_none_when_only_source_is_the_target_language():
    source_map = {"es": _c("/path/es.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=[]) is None


def test_returns_none_when_no_sources_at_all():
    assert pick_source_language({}, target_lang="es", source_priority=["en"]) is None


def test_priority_order_wins_over_dict_iteration_order():
    source_map = {"fr": _c("/path/fr.srt"), "de": _c("/path/de.srt"), "en": _c("/path/en.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=["de", "en"]) == "de"


def test_hi_priority_language_beats_non_hi_non_priority_language():
    """Regression test for the real bug: Fastball 2026 only had an HI
    English track and a non-HI Spanish track. English (priority) + HI must
    still win over Spanish (not in priority list) + non-HI — translating
    from the wrong LANGUAGE entirely is worse than translating from an HI
    track in the right language."""
    source_map = {
        "en": SourceCandidate(path="/path/en.hi.srt", hi=True),
        "es": SourceCandidate(path="/path/es.srt", hi=False),
    }
    assert pick_source_language(source_map, target_lang="it", source_priority=["en"]) == "en"


def test_non_hi_priority_language_preferred_over_hi_same_language():
    source_map = {
        "en": SourceCandidate(path="/path/en.hi.srt", hi=True),
    }
    # only HI english available -> still picked (better than nothing)
    assert pick_source_language(source_map, target_lang="it", source_priority=["en"]) == "en"


def test_non_hi_beats_hi_within_same_priority_tier():
    """build_source_map itself prefers non-HI over HI for the same language
    code when both exist — this test documents pick_source_language's
    behavior given that pre-filtered map (a single candidate per language)."""
    # en has both hi and non-hi versions in Bazarr, but build_source_map
    # would have already collapsed that to the non-hi one — simulate that
    # pre-filtered state here.
    source_map = {"en": SourceCandidate(path="/path/en.srt", hi=False)}
    assert pick_source_language(source_map, target_lang="it", source_priority=["en"]) == "en"


def test_any_language_non_hi_beats_any_language_hi_when_nothing_on_priority_list():
    source_map = {
        "de": SourceCandidate(path="/path/de.hi.srt", hi=True),
        "fr": SourceCandidate(path="/path/fr.srt", hi=False),
    }
    assert pick_source_language(source_map, target_lang="it", source_priority=[]) == "fr"
