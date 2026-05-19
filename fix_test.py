"""Fix the SequenceMatcher test pair."""
content = open('tests/test_dedup_fuzzy.py', 'r', encoding='utf-8').read()

old = '''    """SequenceMatcher ratio >= 0.6 should merge similar names."""
    # 'crystal cavern' vs 'crystl caverns' \u2014 ratio ~0.86
    entities = [_make_discovery("crystal cavern"), _make_discovery("crystl caverns")]
    result = _within_turn_dedup(entities)
    assert len(result) == 1
    names = [e["name"] for e in result]
    assert "crystl caverns" in names  # longer name kept'''

new = '''    """SequenceMatcher ratio >= 0.6 should merge similar names (lev > 3, not substring)."""
    # 'elder spirit totem' vs 'elder spirits totem pole' \u2014 ratio ~0.86, lev=6
    entities = [_make_discovery("elder spirit totem"), _make_discovery("elder spirits totem pole")]
    result = _within_turn_dedup(entities)
    assert len(result) == 1
    names = [e["name"] for e in result]
    assert "elder spirits totem pole" in names  # longer name kept'''

if old not in content:
    print('ERROR: old text not found')
    idx = content.find('sequence_matcher_merge')
    if idx >= 0:
        print(repr(content[idx-10:idx+300]))
    else:
        print('Function not found at all')
else:
    content = content.replace(old, new, 1)
    open('tests/test_dedup_fuzzy.py', 'w', encoding='utf-8').write(content)
    print('Done')
