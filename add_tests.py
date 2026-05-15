"""Add _within_turn_dedup import and tests to test_dedup_fuzzy.py."""
content = open('tests/test_dedup_fuzzy.py', 'r', encoding='utf-8').read()

# 1. Update import if needed
old_import = 'from semantic_extraction import _dedup_catalogs'
new_import = 'from semantic_extraction import _dedup_catalogs, _within_turn_dedup'
if new_import not in content:
    assert old_import in content, "Import not found"
    content = content.replace(old_import, new_import, 1)
    print("Updated import")
else:
    print("Import already updated")

# 2. Add tests at end of file (only if not already there)
if 'test_within_turn_dedup_levenshtein_merge' in content:
    print("Tests already present, skipping")
else:
    new_tests = """

# ---------------------------------------------------------------------------
# #365 review feedback - Additional within-turn dedup tests
# ---------------------------------------------------------------------------


def test_within_turn_dedup_levenshtein_merge():
    \\"\\"\\"Levenshtein distance <= 3 should merge (e.g. 'shaman' vs 'shamn').\\"\\"\\"
    entities = [_make_discovery("shaman"), _make_discovery("shamn")]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    assert len(result) == 1
    assert "shaman" in names


def test_within_turn_dedup_sequence_matcher_merge():
    \\"\\"\\"SequenceMatcher ratio >= 0.6 should merge similar names (lev > 3, not substring).\\"\\"\\"
    # 'elder spirit totem' vs 'elder spirits totem pole' -- ratio ~0.86, lev=6
    entities = [_make_discovery("elder spirit totem"), _make_discovery("elder spirits totem pole")]
    result = _within_turn_dedup(entities)
    assert len(result) == 1
    names = [e["name"] for e in result]
    assert "elder spirits totem pole" in names  # longer name kept


def test_within_turn_dedup_no_false_merge_oaks_cloaks():
    \\"\\"\\"'oaks' is a substring of 'cloaks of shadow' but not a token prefix -- no merge.\\"\\"\\"
    entities = [_make_discovery("oaks"), _make_discovery("cloaks of shadow")]
    result = _within_turn_dedup(entities)
    assert len(result) == 2


def test_within_turn_dedup_preserves_ordering():
    \\"\\"\\"Result should preserve original entity ordering (non-new + new interleaved).\\"\\"\\"
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
    \\"\\"\\"If A is dropped because of B, A should not cause further drops against C.\\"\\"\\"
    entities = [
        _make_discovery("camp"),
        _make_discovery("camping grounds"),
        _make_discovery("forest clearing"),
    ]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    assert "camp" not in names
    assert "camping grounds" in names
    assert "forest clearing" in names
    assert len(result) == 2
"""
    content = content.rstrip() + new_tests
    open('tests/test_dedup_fuzzy.py', 'w', encoding='utf-8').write(content)
    print("Tests added.")

print("Done.")
