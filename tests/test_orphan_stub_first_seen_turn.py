"""Tests for the orphan/coreference stub first_seen_turn invariant.

A Phase-2 smoke surfaced stub entities (e.g. ``char-kael``, ``char-valerius``
split off from the canonical ``char-valerius-kael``) written with an empty
``first_seen_turn`` (``''``).  That is schema-invalid — ``entity.schema.json``
requires ``first_seen_turn`` to match ``^turn-[0-9]{3,}$`` — and makes
``tools/validate.py`` exit 1.

Root cause: ``_post_batch_orphan_sweep`` and ``_name_mention_discovery`` read
``event['turn_id']``, but serialised/reconciled events carry their provenance in
``source_turns`` (``turn_id`` is only present transiently).  The turn therefore
resolved to ``''``.  These tests pin the invariant: every emitted stub gets a
real ``turn-NNN`` ``first_seen_turn`` derived from the event's provenance, and
a stub is never written when no valid source turn exists.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _TURN_ID_RE,
    _event_first_turn,
    _name_mention_discovery,
    _post_batch_orphan_sweep,
)


def _make_event(eid, etype, desc, related, source_turns):
    return {
        "id": eid,
        "type": etype,
        "description": desc,
        "related_entities": related,
        "source_turns": source_turns,
    }


def _all_first_seen_valid(catalogs):
    """Every entity in *catalogs* has a schema-valid first_seen_turn."""
    for entities in catalogs.values():
        for entity in entities:
            fst = entity.get("first_seen_turn", "")
            if not _TURN_ID_RE.match(fst):
                return False
    return True


# ---------------------------------------------------------------------------
# _event_first_turn helper
# ---------------------------------------------------------------------------

def test_event_first_turn_prefers_earliest_source_turn():
    event = _make_event("evt-1", "social", "x", [], ["turn-009", "turn-004", "turn-007"])
    assert _event_first_turn(event) == "turn-004"


def test_event_first_turn_falls_back_to_turn_id():
    event = {"id": "evt-1", "related_entities": [], "turn_id": "turn-012"}
    assert _event_first_turn(event) == "turn-012"


def test_event_first_turn_empty_when_no_provenance():
    event = {"id": "evt-1", "related_entities": [], "description": "x"}
    assert _event_first_turn(event) == ""


def test_event_first_turn_ignores_garbage_entries():
    event = _make_event("evt-1", "social", "x", [], ["not-a-turn", "turn-006"])
    assert _event_first_turn(event) == "turn-006"


def test_event_first_turn_prefers_valid_over_malformed_earlier():
    """A malformed-but-parseable earlier candidate (``bad-turn-001``) must not
    out-rank a schema-valid later turn (``turn-005``).  Only anchored
    ``^turn-[0-9]{3,}$`` candidates are considered."""
    event = _make_event("evt-1", "social", "x", [], ["bad-turn-001", "turn-005"])
    assert _event_first_turn(event) == "turn-005"


def test_event_first_turn_all_malformed_falls_back_to_turn_id():
    """When every ``source_turns`` entry is malformed, fall back to a valid
    ``turn_id`` if present, else ''."""
    event = {
        "id": "evt-1",
        "related_entities": [],
        "source_turns": ["bad-turn-001", "garbage"],
        "turn_id": "turn-008",
    }
    assert _event_first_turn(event) == "turn-008"

    event_no_tid = {
        "id": "evt-2",
        "related_entities": [],
        "source_turns": ["bad-turn-001", "garbage"],
    }
    assert _event_first_turn(event_no_tid) == ""


# ---------------------------------------------------------------------------
# _post_batch_orphan_sweep
# ---------------------------------------------------------------------------

def test_post_batch_orphan_sweep_sets_first_seen_from_source_turns():
    """Orphan referenced in events (source_turns only, no turn_id) gets a valid
    first_seen_turn rather than ''."""
    events = [
        _make_event("evt-1", "social", "A.", ["char-valerius"], ["turn-004"]),
        _make_event("evt-2", "social", "B.", ["char-valerius"], ["turn-006"]),
        _make_event("evt-3", "social", "C.", ["char-valerius"], ["turn-005"]),
    ]
    catalogs = {"characters.json": []}
    created = _post_batch_orphan_sweep(catalogs, events)
    assert created == 1
    stub = catalogs["characters.json"][0]
    assert stub["id"] == "char-valerius"
    assert stub["first_seen_turn"] == "turn-004"
    assert _TURN_ID_RE.match(stub["first_seen_turn"])
    assert _all_first_seen_valid(catalogs)


def test_post_batch_orphan_sweep_skips_when_no_source_turn():
    """No valid source turn -> no schema-invalid stub is emitted."""
    events = [
        _make_event("evt-1", "social", "A.", ["char-valerius"], []),
        _make_event("evt-2", "social", "B.", ["char-valerius"], ["bogus"]),
        _make_event("evt-3", "social", "C.", ["char-valerius"], []),
    ]
    catalogs = {"characters.json": []}
    created = _post_batch_orphan_sweep(catalogs, events)
    assert created == 0
    assert catalogs["characters.json"] == []
    assert _all_first_seen_valid(catalogs)


# ---------------------------------------------------------------------------
# _name_mention_discovery
# ---------------------------------------------------------------------------

def test_name_mention_discovery_sets_first_seen_from_source_turns():
    """A name fragment mentioned in descriptions (source_turns only) gets a
    valid first_seen_turn rather than ''."""
    events = [
        _make_event("evt-1", "social", "Valerius enters the hall.", [], ["turn-004"]),
        _make_event("evt-2", "social", "Later, Valerius departs.", [], ["turn-007"]),
    ]
    catalogs = {"characters.json": []}
    created = _name_mention_discovery(catalogs, events)
    assert created == 1
    stub = catalogs["characters.json"][0]
    assert stub["id"] == "char-valerius"
    assert stub["first_seen_turn"] == "turn-004"
    assert _TURN_ID_RE.match(stub["first_seen_turn"])
    assert _all_first_seen_valid(catalogs)


def test_name_mention_discovery_skips_when_no_source_turn():
    """No valid source turn -> no schema-invalid stub is emitted."""
    events = [
        _make_event("evt-1", "social", "Valerius enters the hall.", [], []),
        _make_event("evt-2", "social", "Later, Valerius departs.", [], ["bogus"]),
    ]
    catalogs = {"characters.json": []}
    created = _name_mention_discovery(catalogs, events)
    assert created == 0
    assert catalogs["characters.json"] == []
    assert _all_first_seen_valid(catalogs)
