"""Add _within_turn_dedup import and tests to test_dedup_fuzzy.py."""
content = open('tests/test_dedup_fuzzy.py', 'r', encoding='utf-8').read()

# 1. Add import
old_import = 'from semantic_extraction import _dedup_catalogs'
new_import = 'from semantic_extraction import _dedup_catalogs, _within_turn_dedup'
assert old_import in content, f"Import not found: {old_import}"
content = content.replace(old_import, new_import, 1)

# 2. Add tests at end of file
new_tests = '''

# ---------------------------------------------------------------------------
# #365 — Within-turn dedup
# ---------------------------------------------------------------------------

def _make_discovery(name, is_new=True):
    """Make a minimal discovery entity for _within_turn_dedup tests."""
    return {"name": name, "is_new": is_new, "type": "character"}


def test_within_turn_dedup_elder_eldorman():
    """'elder' is a token prefix of 'eldorman' -> keep 'eldorman'."""
    entities = [_make_discovery("elder"), _make_discovery("eldorman")]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    assert "eldorman" in names
    assert "elder" not in names


def test_within_turn_dedup_camp_camping_grounds():
    """'camp' is a token prefix of 'camping' -> keep 'camping grounds'."""
    entities = [_make_discovery("camp"), _make_discovery("camping grounds")]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    assert "camping grounds" in names
    assert "camp" not in names


def test_within_turn_dedup_no_similarity():
    """Dissimilar names -> keep both."""
    entities = [_make_discovery("wizard"), _make_discovery("goblin")]
    result = _within_turn_dedup(entities)
    assert len(result) == 2


def test_within_turn_dedup_short_names_kept():
    """Short dissimilar names skip substring check and have high Levenshtein -> keep both."""
    entities = [_make_discovery("ax"), _make_discovery("grove")]
    result = _within_turn_dedup(entities)
    assert len(result) == 2


def test_within_turn_dedup_non_new_untouched():
    """Non-new entities should not be deduped."""
    entities = [
        _make_discovery("elder", is_new=False),
        _make_discovery("eldorman", is_new=True),
    ]
    result = _within_turn_dedup(entities)
    assert len(result) == 2


def test_within_turn_dedup_levenshtein_merge():
    """Levenshtein distance <= 3 should merge (e.g. 'shaman' vs 'shamn')."""
    entities = [_make_discovery("shaman"), _make_discovery("shamn")]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    assert len(result) == 1
    assert "shaman" in names


def test_within_turn_dedup_sequence_matcher_merge():
    """SequenceMatcher ratio >= 0.6 should merge similar names (lev > 3, not substring)."""
    # 'elder spirit totem' vs 'elder spirits totem pole' -- ratio ~0.86, lev=6
    entities = [_make_discovery("elder spirit totem"), _make_discovery("elder spirits totem pole")]
    result = _within_turn_dedup(entities)
    assert len(result) == 1
    names = [e["name"] for e in result]
    assert "elder spirits totem pole" in names  # longer name kept


def test_within_turn_dedup_no_false_merge_ring_spring():
    """'ring' should NOT merge with 'spring' -- ring is a substring but not a token prefix."""
    entities = [_make_discovery("ring"), _make_discovery("spring")]
    result = _within_turn_dedup(entities)
    assert len(result) == 2


def test_within_turn_dedup_preserves_ordering():
    """Result should preserve original entity ordering (non-new + new interleaved)."""
    entities = [
        _make_discovery("existing-npc", is_new=False),
        _make_discovery("elder"),
        _make_discovery("another-npc", is_new=False),
        _make_discovery("eldorman"),
    ]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    # elder dropped, eldorman kept; non-new entities stay in original positions
    assert names == ["existing-npc", "another-npc", "eldorman"]


def test_within_turn_dedup_cascade_drop():
    """If A is dropped because of B, A should not cause further drops."""
    # A='camp', B='camping grounds', C='camping site'
    # A is substring of B (camp->camping) -> A dropped
    # A should NOT then also cause C to be compared against stale A
    entities = [
        _make_discovery("camp"),
        _make_discovery("camping grounds"),
        _make_discovery("camping site"),
    ]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    # camp dropped (substring of camping grounds)
    assert "camp" not in names
    # Both camping variants kept (they are not similar enough to merge)
    assert "camping grounds" in names
    assert "camping site" in names
'''

content = content.rstrip() + new_tests
open('tests/test_dedup_fuzzy.py', 'w', encoding='utf-8').write(content)
print('Done. Import updated and tests added.')
