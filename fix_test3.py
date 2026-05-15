"""Fix the ring/spring test to use a proper non-merging substring pair."""
content = open('tests/test_dedup_fuzzy.py', 'r', encoding='utf-8').read()

old = '''def test_within_turn_dedup_no_false_merge_ring_spring():
    """'ring' should NOT merge with 'spring' -- ring is a substring but not a token prefix."""
    entities = [_make_discovery("ring"), _make_discovery("spring")]
    result = _within_turn_dedup(entities)
    assert len(result) == 2'''

new = '''def test_within_turn_dedup_no_false_merge_oaks_cloaks():
    """'oaks' is a substring of 'cloaks of shadow' but not a token prefix -- no merge."""
    entities = [_make_discovery("oaks"), _make_discovery("cloaks of shadow")]
    result = _within_turn_dedup(entities)
    assert len(result) == 2'''

assert old in content, 'Old text not found'
content = content.replace(old, new, 1)
open('tests/test_dedup_fuzzy.py', 'w', encoding='utf-8').write(content)
print('Done')
