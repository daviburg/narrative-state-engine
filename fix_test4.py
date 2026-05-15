"""Fix cascade drop test to use entities that don't independently merge."""
content = open('tests/test_dedup_fuzzy.py', 'r', encoding='utf-8').read()

old = '''def test_within_turn_dedup_cascade_drop():
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
    assert "camping site" in names'''

new = '''def test_within_turn_dedup_cascade_drop():
    """If A is dropped because of B, A should not cause further drops against C."""
    # A='camp', B='camping grounds', C='forest clearing' (unrelated to A)
    # A is substring of B (camp->camping) -> A dropped, inner loop breaks
    # C should survive (unrelated to both)
    entities = [
        _make_discovery("camp"),
        _make_discovery("camping grounds"),
        _make_discovery("forest clearing"),
    ]
    result = _within_turn_dedup(entities)
    names = [e["name"] for e in result]
    # camp dropped (substring of camping grounds)
    assert "camp" not in names
    assert "camping grounds" in names
    assert "forest clearing" in names
    assert len(result) == 2'''

assert old in content, 'Old text not found'
content = content.replace(old, new, 1)
open('tests/test_dedup_fuzzy.py', 'w', encoding='utf-8').write(content)
print('Done')
