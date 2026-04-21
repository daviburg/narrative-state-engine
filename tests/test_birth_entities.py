"""Tests for child entity extraction from birth events (#136)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _collect_stub_context,
    _ensure_birth_entities,
    _find_earliest_mention,
    _create_orphan_stubs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(eid, etype, desc, related, source_turns):
    return {
        "id": eid,
        "type": etype,
        "description": desc,
        "related_entities": related,
        "source_turns": source_turns,
    }


def _make_turn(turn_id, text):
    return {"turn_id": turn_id, "speaker": "DM", "text": text}


# ---------------------------------------------------------------------------
# Test _collect_stub_context includes description mentions (Fix A)
# ---------------------------------------------------------------------------

def test_collect_stub_context_includes_description_mentions():
    """Stub context for entity 'Lyrawyn' includes events where name appears in description."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.", ["char-player"], ["turn-141"]),
        _make_event("evt-2", "social", "Lyrawyn takes her first steps.", ["char-player"], ["turn-160"]),
        _make_event("evt-3", "combat", "Wolves attack the camp.", ["char-player"], ["turn-170"]),
    ]
    turns = [
        _make_turn("turn-141", "Birth turn text about Lyrawyn"),
        _make_turn("turn-160", "Lyrawyn walking turn text"),
        _make_turn("turn-170", "Combat turn text no child"),
    ]
    context = _collect_stub_context("char-lyrawyn", events, turns, "turn-141",
                                    entity_name="Lyrawyn")
    assert "turn-141" in context
    assert "turn-160" in context
    assert "turn-170" not in context


def test_collect_stub_context_without_name_uses_related_entities_only():
    """Without entity_name, only related_entities matches are used."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.", ["char-player"], ["turn-141"]),
        _make_event("evt-2", "social", "Lyrawyn plays.", ["char-lyrawyn"], ["turn-160"]),
    ]
    turns = [
        _make_turn("turn-141", "Birth turn text"),
        _make_turn("turn-160", "Play turn text"),
    ]
    # Without name, only evt-2 matches via related_entities
    context = _collect_stub_context("char-lyrawyn", events, turns, None,
                                    entity_name=None)
    assert "turn-160" in context
    assert "turn-141" not in context


# ---------------------------------------------------------------------------
# Test _find_earliest_mention (Fix B)
# ---------------------------------------------------------------------------

def test_first_seen_turn_uses_earliest_mention():
    """Earliest mention by name should be returned even if ID isn't in related_entities."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.", ["char-player"], ["turn-141"]),
        _make_event("evt-2", "social", "Lyrawyn plays.", ["char-lyrawyn"], ["turn-160"]),
    ]
    result = _find_earliest_mention("char-lyrawyn", "Lyrawyn", events)
    assert result == "turn-141"


def test_first_seen_turn_id_only():
    """When name doesn't appear in descriptions, use related_entities match."""
    events = [
        _make_event("evt-1", "social", "The child plays.", ["char-lyrawyn"], ["turn-160"]),
    ]
    result = _find_earliest_mention("char-lyrawyn", "Lyrawyn", events)
    assert result == "turn-160"


def test_first_seen_turn_no_mention():
    """Returns None when entity is not mentioned anywhere."""
    events = [
        _make_event("evt-1", "combat", "Wolves attack.", ["char-player"], ["turn-100"]),
    ]
    result = _find_earliest_mention("char-lyrawyn", "Lyrawyn", events)
    assert result is None


# ---------------------------------------------------------------------------
# Test _ensure_birth_entities (Fix C)
# ---------------------------------------------------------------------------

def test_birth_event_creates_entity():
    """A birth event with 'named Lyrawyn' creates a char-lyrawyn entity."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.", ["char-player"], ["turn-141"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    created = _ensure_birth_entities(events, catalogs)
    assert "char-lyrawyn" in created
    # Verify entity was added to catalogs
    ids = [e["id"] for e in catalogs["characters.json"]]
    assert "char-lyrawyn" in ids
    entity = [e for e in catalogs["characters.json"] if e["id"] == "char-lyrawyn"][0]
    assert entity["name"] == "Lyrawyn"
    assert entity["first_seen_turn"] == "turn-141"
    assert entity["type"] == "character"


def test_birth_event_no_duplicate():
    """If the entity already exists, _ensure_birth_entities does not duplicate it."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.", ["char-player"], ["turn-141"]),
    ]
    catalogs = {"characters.json": [
        {"id": "char-player", "name": "Player"},
        {"id": "char-lyrawyn", "name": "Lyrawyn", "type": "character"},
    ]}
    created = _ensure_birth_entities(events, catalogs)
    assert created == []
    # Still only 2 entities
    assert len(catalogs["characters.json"]) == 2


def test_birth_event_no_named_pattern():
    """Birth events without 'named X' pattern don't create entities."""
    events = [
        _make_event("evt-1", "birth", "A child is born in the village.", ["char-player"], ["turn-141"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    created = _ensure_birth_entities(events, catalogs)
    assert created == []


def test_non_birth_event_ignored():
    """Non-birth events are not processed even if they mention 'named'."""
    events = [
        _make_event("evt-1", "social", "A warrior named Kael arrives.", ["char-player"], ["turn-050"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    created = _ensure_birth_entities(events, catalogs)
    assert created == []


# ---------------------------------------------------------------------------
# Test birth event related_entities update (Fix D)
# ---------------------------------------------------------------------------

def test_birth_event_adds_related_entity():
    """After processing, birth events have the child ID in related_entities."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.", ["char-player"], ["turn-141"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    _ensure_birth_entities(events, catalogs)
    assert "char-lyrawyn" in events[0]["related_entities"]


def test_birth_event_related_entity_no_duplicate():
    """If child ID is already in related_entities, it isn't duplicated."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.",
                     ["char-player", "char-lyrawyn"], ["turn-141"]),
    ]
    catalogs = {"characters.json": [
        {"id": "char-player", "name": "Player"},
        {"id": "char-lyrawyn", "name": "Lyrawyn", "type": "character"},
    ]}
    _ensure_birth_entities(events, catalogs)
    count = events[0]["related_entities"].count("char-lyrawyn")
    assert count == 1


# ---------------------------------------------------------------------------
# Test name matching (case insensitivity, short name guard)
# ---------------------------------------------------------------------------

def test_name_match_case_insensitive():
    """Description matching is case-insensitive."""
    events = [
        _make_event("evt-1", "birth", "a girl named lyrawyn is born.", ["char-player"], ["turn-141"]),
    ]
    result = _find_earliest_mention("char-lyrawyn", "Lyrawyn", events)
    assert result == "turn-141"


def test_short_name_not_matched():
    """Entity names shorter than 3 chars are not matched in descriptions."""
    events = [
        _make_event("evt-1", "social", "The bo appeared.", ["char-player"], ["turn-100"]),
    ]
    # Name "Bo" is only 2 chars — should not match in descriptions
    result = _find_earliest_mention("char-bo", "Bo", events)
    # Only ID match would work, but char-bo not in related_entities
    assert result is None


def test_short_name_not_matched_in_stub_context():
    """Short entity names don't expand context via description matching."""
    events = [
        _make_event("evt-1", "social", "Bo the cat appeared.", ["char-player"], ["turn-100"]),
    ]
    turns = [_make_turn("turn-100", "Turn with Bo")]
    context = _collect_stub_context("char-bo", events, turns, None, entity_name="Bo")
    # No match: "Bo" is <3 chars, char-bo not in related_entities
    assert context == ""


# ---------------------------------------------------------------------------
# Test _create_orphan_stubs uses earliest mention (Fix B integration)
# ---------------------------------------------------------------------------

def test_create_orphan_stubs_earliest_turn():
    """Stubs pick up earliest mention turn, not the processing turn."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.",
                     ["char-player", "char-lyrawyn"], ["turn-141"]),
        _make_event("evt-2", "social", "Lyrawyn plays.",
                     ["char-lyrawyn"], ["turn-200"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    _create_orphan_stubs(catalogs, events, "turn-999")
    lyra = [e for e in catalogs["characters.json"] if e["id"] == "char-lyrawyn"]
    assert len(lyra) == 1
    assert lyra[0]["first_seen_turn"] == "turn-141"


# ---------------------------------------------------------------------------
# PR review comment fixes — additional tests
# ---------------------------------------------------------------------------

def test_earliest_mention_numeric_comparison():
    """Turn IDs are compared numerically, not lexicographically.

    'turn-999' should sort before 'turn-1000' even though string comparison
    would place 'turn-1000' first.
    """
    events = [
        _make_event("evt-1", "social", "Lyrawyn appears.", ["char-player"], ["turn-1000"]),
        _make_event("evt-2", "social", "Lyrawyn again.", ["char-player"], ["turn-999"]),
    ]
    result = _find_earliest_mention("char-lyrawyn", "Lyrawyn", events)
    assert result == "turn-999"


def test_earliest_mention_multi_source_turns():
    """When an event has multiple source_turns, the smallest is chosen."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.",
                     ["char-player"], ["turn-143", "turn-141", "turn-142"]),
    ]
    result = _find_earliest_mention("char-lyrawyn", "Lyrawyn", events)
    assert result == "turn-141"


def test_birth_entity_id_sanitized():
    """Child names with apostrophes or special chars produce valid entity IDs."""
    events = [
        _make_event("evt-1", "birth", "A boy named Kel'thas is born.",
                     ["char-player"], ["turn-200"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    created = _ensure_birth_entities(events, catalogs)
    assert len(created) == 1
    # The apostrophe should be stripped, producing a valid schema ID
    child_id = created[0]
    import re
    assert re.match(r'^char-[a-z0-9]+(-[a-z0-9]+)*$', child_id), f"Invalid ID: {child_id}"


def test_birth_entity_picks_earliest_source_turn():
    """Birth entity first_seen_turn uses the smallest source turn."""
    events = [
        _make_event("evt-1", "birth", "A girl named Lyrawyn is born.",
                     ["char-player"], ["turn-143", "turn-141"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    _ensure_birth_entities(events, catalogs)
    entity = [e for e in catalogs["characters.json"] if e["id"] == "char-lyrawyn"][0]
    assert entity["first_seen_turn"] == "turn-141"


def test_create_orphan_stubs_uses_all_events():
    """Stubs find earliest mention from all_events, not just current-turn events."""
    # Historical event mentioning Lyrawyn by name (already merged)
    historical = [
        _make_event("evt-old", "birth", "A girl named Lyrawyn is born.",
                     ["char-player"], ["turn-141"]),
    ]
    # Current turn event referencing char-lyrawyn by ID (orphan)
    current = [
        _make_event("evt-new", "social", "The child dances.",
                     ["char-lyrawyn"], ["turn-200"]),
    ]
    catalogs = {"characters.json": [{"id": "char-player", "name": "Player"}]}
    _create_orphan_stubs(catalogs, current, "turn-200",
                         all_events=historical + current)
    lyra = [e for e in catalogs["characters.json"] if e["id"] == "char-lyrawyn"]
    assert len(lyra) == 1
    assert lyra[0]["first_seen_turn"] == "turn-141"
