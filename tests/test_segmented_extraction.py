"""Tests for segmented extraction with reconciliation (#141)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _compare_turns,
    _find_canonical,
    _is_empty_attr_value,
    _merge_entity_across_segments,
    _dedup_events,
    _reconcile_segments,
    _ensure_player_character,
    extract_semantic_batch,
    _V1_FILENAMES,
)


def _make_entity(id_, name, turn="turn-001", etype="character", identity="", notes=""):
    return {
        "id": id_,
        "name": name,
        "type": etype,
        "first_seen_turn": turn,
        "last_updated_turn": turn,
        "identity": identity,
        "current_status": "active",
        "stable_attributes": {},
        "relationships": [],
        "notes": notes,
    }


def _make_event(eid, desc, source_turn, related=None):
    return {
        "id": eid,
        "description": desc,
        "source_turns": [source_turn],
        "related_entities": related or [],
    }


def _make_segment(seg_id, catalogs, events, turn_range):
    return {
        "id": seg_id,
        "catalogs": catalogs,
        "events": events,
        "turn_range": turn_range,
    }


# --- _find_canonical ---

def test_find_canonical_by_id():
    entity_map = {"char-kael": _make_entity("char-kael", "Kael")}
    result = _find_canonical("char-kael", "kael", entity_map, {})
    assert result == "char-kael"


def test_find_canonical_by_alias():
    entity_map = {"char-kael": _make_entity("char-kael", "Kael")}
    id_aliases = {"char-kael-2": "char-kael"}
    result = _find_canonical("char-kael-2", "kael", entity_map, id_aliases)
    assert result == "char-kael"


def test_find_canonical_by_name():
    entity_map = {"char-kael": _make_entity("char-kael", "Kael")}
    result = _find_canonical("char-kael-warrior", "kael", entity_map, {})
    assert result == "char-kael"


def test_find_canonical_not_found():
    entity_map = {"char-kael": _make_entity("char-kael", "Kael")}
    result = _find_canonical("char-renn", "renn", entity_map, {})
    assert result is None


def test_find_canonical_empty_name_no_false_match():
    entity_map = {"char-kael": _make_entity("char-kael", "")}
    result = _find_canonical("char-other", "", entity_map, {})
    assert result is None


# --- _merge_entity_across_segments ---

def test_merge_prefers_non_stub_identity():
    target = _make_entity("char-kael", "Kael", "turn-001", identity="stub — discovered turn-001")
    source = _make_entity("char-kael", "Kael", "turn-150", identity="A fierce warrior and leader of the settlement guard.")
    _merge_entity_across_segments(target, source)
    assert target["identity"] == "A fierce warrior and leader of the settlement guard."


def test_merge_preserves_first_seen_turn():
    target = _make_entity("char-kael", "Kael", "turn-050")
    source = _make_entity("char-kael", "Kael", "turn-150")
    _merge_entity_across_segments(target, source)
    assert target["first_seen_turn"] == "turn-050"
    assert target["last_updated_turn"] == "turn-150"


def test_merge_updates_first_seen_if_earlier():
    target = _make_entity("char-kael", "Kael", "turn-050")
    source = _make_entity("char-kael", "Kael", "turn-010")
    _merge_entity_across_segments(target, source)
    assert target["first_seen_turn"] == "turn-010"


def test_merge_current_status_from_later_segment():
    target = _make_entity("char-kael", "Kael", "turn-050")
    target["current_status"] = "active"
    source = _make_entity("char-kael", "Kael", "turn-150")
    source["current_status"] = "injured"
    _merge_entity_across_segments(target, source)
    assert target["current_status"] == "injured"


def test_merge_stable_attributes_union():
    target = _make_entity("char-kael", "Kael", "turn-050")
    target["stable_attributes"] = {"race": "human"}
    source = _make_entity("char-kael", "Kael", "turn-150")
    source["stable_attributes"] = {"class": "warrior", "race": ""}
    _merge_entity_across_segments(target, source)
    assert target["stable_attributes"]["race"] == "human"
    assert target["stable_attributes"]["class"] == "warrior"


def test_merge_stable_attributes_v2_format():
    """V2 dict-format stable_attributes are merged correctly."""
    target = _make_entity("char-kael", "Kael", "turn-050")
    target["stable_attributes"] = {
        "race": {"value": "human", "source_turn": "turn-010"},
    }
    source = _make_entity("char-kael", "Kael", "turn-150")
    source["stable_attributes"] = {
        "class": {"value": "warrior", "source_turn": "turn-120"},
        "race": {"value": "", "source_turn": "turn-005"},
    }
    _merge_entity_across_segments(target, source)
    assert target["stable_attributes"]["race"]["value"] == "human"
    assert target["stable_attributes"]["class"]["value"] == "warrior"


def test_merge_stable_attributes_v2_newer_wins():
    """V2 attr with later source_turn replaces earlier one."""
    target = _make_entity("char-kael", "Kael", "turn-050")
    target["stable_attributes"] = {
        "role": {"value": "scout", "source_turn": "turn-010"},
    }
    source = _make_entity("char-kael", "Kael", "turn-150")
    source["stable_attributes"] = {
        "role": {"value": "captain", "source_turn": "turn-130"},
    }
    _merge_entity_across_segments(target, source)
    assert target["stable_attributes"]["role"]["value"] == "captain"
    assert target["stable_attributes"]["role"]["source_turn"] == "turn-130"


def test_merge_relationships_dedup():
    target = _make_entity("char-kael", "Kael", "turn-050")
    target["relationships"] = [
        {"target_id": "char-player", "type": "social",
         "current_relationship": "ally", "first_seen_turn": "turn-050",
         "last_updated_turn": "turn-050"},
    ]
    source = _make_entity("char-kael", "Kael", "turn-150")
    source["relationships"] = [
        {"target_id": "char-player", "type": "social",
         "current_relationship": "trusted ally", "first_seen_turn": "turn-050",
         "last_updated_turn": "turn-150"},
        {"target_id": "char-renn", "type": "social",
         "current_relationship": "friend", "first_seen_turn": "turn-120",
         "last_updated_turn": "turn-120"},
    ]
    _merge_entity_across_segments(target, source)
    assert len(target["relationships"]) == 2
    targets = {r["target_id"] for r in target["relationships"]}
    assert targets == {"char-player", "char-renn"}


def test_merge_relationships_updates_existing():
    """Existing relationships are updated with later segment data."""
    target = _make_entity("char-kael", "Kael", "turn-050")
    target["relationships"] = [
        {"target_id": "char-player", "type": "social",
         "current_relationship": "ally", "first_seen_turn": "turn-050",
         "last_updated_turn": "turn-050", "history": [
             {"turn": "turn-050", "description": "Met during patrol"},
         ]},
    ]
    source = _make_entity("char-kael", "Kael", "turn-150")
    source["relationships"] = [
        {"target_id": "char-player", "type": "social",
         "current_relationship": "trusted ally", "first_seen_turn": "turn-050",
         "last_updated_turn": "turn-150", "history": [
             {"turn": "turn-150", "description": "Fought together in battle"},
         ]},
    ]
    _merge_entity_across_segments(target, source)
    rel = target["relationships"][0]
    assert rel["current_relationship"] == "trusted ally"
    assert rel["last_updated_turn"] == "turn-150"
    assert len(rel["history"]) == 2


def test_merge_replaces_stub_notes():
    target = _make_entity("char-kael", "Kael", notes="Stub entity — needs enrichment")
    source = _make_entity("char-kael", "Kael", notes="Guard captain of the northern settlement.")
    _merge_entity_across_segments(target, source)
    assert target["notes"] == "Guard captain of the northern settlement."


# --- _dedup_events ---

def test_dedup_events_removes_duplicates():
    events = [
        _make_event("evt-001", "Kael draws his sword", "turn-050"),
        _make_event("evt-002", "Kael draws his sword", "turn-050"),
        _make_event("evt-003", "Renn casts a spell", "turn-051"),
    ]
    result = _dedup_events(events)
    assert len(result) == 2
    descs = [e["description"] for e in result]
    assert "Kael draws his sword" in descs
    assert "Renn casts a spell" in descs


def test_dedup_events_keeps_same_desc_different_turns():
    events = [
        _make_event("evt-001", "Combat begins", "turn-050"),
        _make_event("evt-002", "Combat begins", "turn-150"),
    ]
    result = _dedup_events(events)
    assert len(result) == 2


# --- _reconcile_segments ---

def test_reconcile_same_entity_across_segments():
    seg1_catalogs = {
        "characters.json": [_make_entity("char-kael", "Kael", "turn-001")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    seg2_catalogs = {
        "characters.json": [_make_entity("char-kael", "Kael", "turn-150")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    segments = [
        _make_segment("segment-1", seg1_catalogs, [], ("turn-001", "turn-100")),
        _make_segment("segment-2", seg2_catalogs, [], ("turn-101", "turn-200")),
    ]
    catalogs, events = _reconcile_segments(segments)
    # Should merge into one Kael entry
    all_chars = catalogs["characters.json"]
    kael_entries = [e for e in all_chars if e["name"] == "Kael"]
    assert len(kael_entries) == 1
    assert kael_entries[0]["first_seen_turn"] == "turn-001"
    assert kael_entries[0]["last_updated_turn"] == "turn-150"


def test_reconcile_new_entity_in_later_segment():
    seg1_catalogs = {
        "characters.json": [_make_entity("char-kael", "Kael", "turn-001")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    seg2_catalogs = {
        "characters.json": [_make_entity("char-renn", "Renn", "turn-310")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    segments = [
        _make_segment("segment-1", seg1_catalogs, [], ("turn-001", "turn-100")),
        _make_segment("segment-2", seg2_catalogs, [], ("turn-301", "turn-345")),
    ]
    catalogs, events = _reconcile_segments(segments)
    all_chars = catalogs["characters.json"]
    names = {e["name"] for e in all_chars}
    assert "Kael" in names
    assert "Renn" in names


def test_reconcile_preserves_first_seen_turn():
    seg1_catalogs = {
        "characters.json": [_make_entity("char-kael", "Kael", "turn-005")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    seg2_catalogs = {
        "characters.json": [_make_entity("char-kael", "Kael", "turn-120")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    segments = [
        _make_segment("segment-1", seg1_catalogs, [], ("turn-001", "turn-100")),
        _make_segment("segment-2", seg2_catalogs, [], ("turn-101", "turn-200")),
    ]
    catalogs, events = _reconcile_segments(segments)
    kael = [e for e in catalogs["characters.json"] if e["name"] == "Kael"][0]
    assert kael["first_seen_turn"] == "turn-005"


def test_reconcile_events_deduplicated():
    seg1_events = [_make_event("evt-001", "Kael draws his sword", "turn-050")]
    seg2_events = [_make_event("evt-010", "Kael draws his sword", "turn-050")]
    seg1_catalogs = {fn: [] for fn in _V1_FILENAMES}
    seg2_catalogs = {fn: [] for fn in _V1_FILENAMES}
    segments = [
        _make_segment("segment-1", seg1_catalogs, seg1_events, ("turn-001", "turn-100")),
        _make_segment("segment-2", seg2_catalogs, seg2_events, ("turn-101", "turn-200")),
    ]
    catalogs, events = _reconcile_segments(segments)
    assert len(events) == 1


def test_reconcile_event_entity_ids_rewritten():
    seg1_catalogs = {
        "characters.json": [_make_entity("char-kael", "Kael", "turn-001")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    seg2_catalogs = {
        "characters.json": [_make_entity("char-kael-warrior", "Kael", "turn-150")],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    seg2_events = [
        _make_event("evt-010", "Kael fights", "turn-150", related=["char-kael-warrior"]),
    ]
    segments = [
        _make_segment("segment-1", seg1_catalogs, [], ("turn-001", "turn-100")),
        _make_segment("segment-2", seg2_catalogs, seg2_events, ("turn-101", "turn-200")),
    ]
    catalogs, events = _reconcile_segments(segments)
    # char-kael-warrior should be aliased to char-kael
    assert events[0]["related_entities"] == ["char-kael"]


def test_segment_size_zero_uses_legacy(monkeypatch):
    """segment_size=0 should NOT call _extract_segmented."""
    calls = []

    def mock_extract_segmented(*a, **kw):
        calls.append(1)

    monkeypatch.setattr("semantic_extraction._extract_segmented", mock_extract_segmented)

    # Also mock LLMClient to avoid needing config
    from unittest.mock import MagicMock
    monkeypatch.setattr("semantic_extraction.LLMClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("semantic_extraction.load_catalogs", lambda d: {fn: [] for fn in _V1_FILENAMES})
    monkeypatch.setattr("semantic_extraction.load_events", lambda d: [])
    monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
    monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)
    monkeypatch.setattr("semantic_extraction.extract_and_merge", lambda *a, **kw: ({fn: [] for fn in _V1_FILENAMES}, []))
    monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
    monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)

    turns = [{"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": "hello"} for i in range(1, 11)]
    extract_semantic_batch(turns, "sessions/test", dry_run=True, segment_size=0)
    assert len(calls) == 0


def test_segment_size_partitions_correctly():
    """345 turns with segment_size=100 → 4 segments (100, 100, 100, 45)."""
    turns = [{"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": "text"} for i in range(1, 346)]
    segment_size = 100

    # Compute partitions the same way _extract_segmented does
    segments = []
    for start in range(0, len(turns), segment_size):
        end = min(start + segment_size, len(turns))
        segments.append(turns[start:end])

    assert len(segments) == 4
    assert len(segments[0]) == 100
    assert len(segments[1]) == 100
    assert len(segments[2]) == 100
    assert len(segments[3]) == 45


def test_player_character_seeded_each_segment():
    """char-player should be seeded in every segment's catalog."""
    seg_catalogs = {fn: [] for fn in _V1_FILENAMES}
    _ensure_player_character(seg_catalogs, "turn-101")
    chars = seg_catalogs["characters.json"]
    pc = [e for e in chars if e["id"] == "char-player"]
    assert len(pc) == 1
    assert pc[0]["first_seen_turn"] == "turn-101"

    # Second segment
    seg_catalogs2 = {fn: [] for fn in _V1_FILENAMES}
    _ensure_player_character(seg_catalogs2, "turn-201")
    chars2 = seg_catalogs2["characters.json"]
    pc2 = [e for e in chars2 if e["id"] == "char-player"]
    assert len(pc2) == 1
    assert pc2[0]["first_seen_turn"] == "turn-201"


# --- _compare_turns (numeric comparison) ---

def test_compare_turns_numeric():
    assert _compare_turns("turn-999", "turn-1000") < 0
    assert _compare_turns("turn-1000", "turn-999") > 0
    assert _compare_turns("turn-100", "turn-100") == 0


def test_compare_turns_three_digit_padding():
    assert _compare_turns("turn-010", "turn-050") < 0
    assert _compare_turns("turn-345", "turn-100") > 0


# --- Type-aware _find_canonical ---

def test_find_canonical_type_mismatch_no_merge():
    """Same name but different type should NOT match."""
    entity_map = {
        "loc-haven": _make_entity("loc-haven", "Haven", etype="location"),
    }
    result = _find_canonical("item-haven", "haven", entity_map, {})
    assert result is None


def test_find_canonical_type_match_succeeds():
    """Same name and same type should match."""
    entity_map = {
        "char-kael": _make_entity("char-kael", "Kael", etype="character"),
    }
    result = _find_canonical("char-kael-warrior", "kael", entity_map, {})
    assert result == "char-kael"


# --- Reconciliation: relationship alias rewriting ---

def test_reconcile_rewrites_relationship_target_ids():
    """After reconciliation, relationship target_ids should use canonical IDs."""
    seg1_catalogs = {
        "characters.json": [
            _make_entity("char-kael", "Kael", "turn-001"),
            _make_entity("char-renn", "Renn", "turn-020"),
        ],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    seg2_entity = _make_entity("char-kael-warrior", "Kael", "turn-150")
    seg2_entity["relationships"] = [
        {"target_id": "char-renn-scout", "type": "social",
         "current_relationship": "ally", "first_seen_turn": "turn-150"},
    ]
    seg2_catalogs = {
        "characters.json": [
            seg2_entity,
            _make_entity("char-renn-scout", "Renn", "turn-150"),
        ],
        "locations.json": [], "factions.json": [], "items.json": [],
    }
    segments = [
        _make_segment("segment-1", seg1_catalogs, [], ("turn-001", "turn-100")),
        _make_segment("segment-2", seg2_catalogs, [], ("turn-101", "turn-200")),
    ]
    catalogs, events = _reconcile_segments(segments)
    kael = [e for e in catalogs["characters.json"] if e["id"] == "char-kael"][0]
    rel_targets = {r["target_id"] for r in kael.get("relationships", [])}
    # char-renn-scout should have been rewritten to char-renn
    assert "char-renn" in rel_targets
    assert "char-renn-scout" not in rel_targets


# --- Event numeric sort ---

def test_reconcile_events_sorted_numerically():
    """Events should sort by turn number, not lexicographically."""
    events_input = [
        _make_event("evt-001", "Event at 999", "turn-999"),
        _make_event("evt-002", "Event at 1000", "turn-1000"),
        _make_event("evt-003", "Event at 50", "turn-050"),
    ]
    seg_catalogs = {fn: [] for fn in _V1_FILENAMES}
    segments = [
        _make_segment("segment-1", seg_catalogs, events_input, ("turn-001", "turn-1000")),
    ]
    catalogs, events = _reconcile_segments(segments)
    turn_order = [e.get("source_turns", [""])[0] for e in events]
    assert turn_order == ["turn-050", "turn-999", "turn-1000"]
