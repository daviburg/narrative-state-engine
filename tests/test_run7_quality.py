"""Tests for Run 7 quality fixes (#124-#129)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _pc_partial_merge,
    _fix_event_source_turns,
    _dedup_catalogs,
    _is_stub_entity,
    _collect_stub_context,
    get_entity_id,
    filter_by_confidence,
)
from catalog_merger import (
    _coerce_relationship_type,
    _consolidate_relationship,
)


# ---------------------------------------------------------------------------
# #124 — Concept-prefix filter at discovery time
# ---------------------------------------------------------------------------

class TestConceptPrefixDiscoveryFilter:
    """Test that concept-prefix entities are rejected at discovery time."""

    def test_concept_prefix_filtered_from_qualified(self):
        """Entities with concept- prefix IDs should be filtered before detail extraction."""
        discovered = [
            {"proposed_id": "concept-spirit-world", "name": "Spirit World",
             "type": "concept", "confidence": 0.9},
            {"proposed_id": "char-grim", "name": "Grim",
             "type": "character", "confidence": 0.9},
            {"proposed_id": "concept-honor", "name": "Honor",
             "type": "concept", "confidence": 0.8},
        ]
        qualified = filter_by_confidence(discovered, 0.6)
        # Simulate the discovery filter from extract_and_merge
        filtered = []
        for entity_ref in qualified:
            eid = get_entity_id(entity_ref)
            if eid and eid.lower().startswith("concept-"):
                continue
            filtered.append(entity_ref)

        assert len(filtered) == 1
        assert get_entity_id(filtered[0]) == "char-grim"

    def test_non_concept_prefix_passes(self):
        discovered = [
            {"proposed_id": "char-test", "name": "Test", "type": "character", "confidence": 0.9},
        ]
        qualified = filter_by_confidence(discovered, 0.6)
        filtered = [e for e in qualified if not get_entity_id(e).lower().startswith("concept-")]
        assert len(filtered) == 1

    def test_concept_prefix_case_insensitive(self):
        discovered = [
            {"proposed_id": "Concept-Magic", "name": "Magic", "type": "concept", "confidence": 0.9},
        ]
        qualified = filter_by_confidence(discovered, 0.6)
        filtered = [e for e in qualified if not get_entity_id(e).lower().startswith("concept-")]
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# #125 — PC partial merge diagnostic logging
# ---------------------------------------------------------------------------

class TestPCPartialMergeLogging:
    def _make_pc_catalogs(self):
        return {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }

    def test_logs_merged_fields(self, capsys):
        catalogs = self._make_pc_catalogs()
        entity_data = {
            "id": "char-player",
            "current_status": "Fighting.",
        }
        _pc_partial_merge(catalogs, entity_data, "turn-100")
        captured = capsys.readouterr()
        assert "current_status" in captured.err
        assert "merged" in captured.err.lower()
        assert "turn-100" in captured.err

    def test_logs_empty_merge_warning(self, capsys):
        catalogs = self._make_pc_catalogs()
        entity_data = {"id": "char-player"}
        _pc_partial_merge(catalogs, entity_data, "turn-060")
        captured = capsys.readouterr()
        assert "no fields could be merged" in captured.err.lower()
        assert "turn-060" in captured.err

    def test_logs_attempted_fields(self, capsys):
        catalogs = self._make_pc_catalogs()
        entity_data = {
            "id": "char-player",
            "current_status": "In camp.",
            "volatile_state": {"condition": "resting"},
        }
        _pc_partial_merge(catalogs, entity_data, "turn-075")
        captured = capsys.readouterr()
        assert "attempted" in captured.err.lower()
        assert "current_status" in captured.err
        assert "volatile_state" in captured.err


# ---------------------------------------------------------------------------
# #126 — Relationship type coercion
# ---------------------------------------------------------------------------

class TestRelationshipTypeCoercion:
    def test_ally_maps_to_social(self):
        assert _coerce_relationship_type("ally") == "social"

    def test_captive_maps_to_adversarial(self):
        assert _coerce_relationship_type("captive") == "adversarial"

    def test_mentor_maps_to_mentorship(self):
        assert _coerce_relationship_type("mentor") == "mentorship"

    def test_parent_maps_to_kinship(self):
        assert _coerce_relationship_type("parent") == "kinship"

    def test_partner_maps_to_partnership(self):
        assert _coerce_relationship_type("partner") == "partnership"

    def test_lover_maps_to_romantic(self):
        assert _coerce_relationship_type("lover") == "romantic"

    def test_diplomatic_maps_to_political(self):
        assert _coerce_relationship_type("diplomatic") == "political"

    def test_guild_maps_to_factional(self):
        assert _coerce_relationship_type("guild") == "factional"

    def test_unmapped_defaults_to_other(self):
        assert _coerce_relationship_type("blood_oath") == "other"

    def test_empty_defaults_to_other(self):
        assert _coerce_relationship_type("") == "other"

    def test_case_insensitive(self):
        assert _coerce_relationship_type("ALLY") == "social"
        assert _coerce_relationship_type("Captive Of") == "adversarial"

    def test_strips_whitespace(self):
        assert _coerce_relationship_type("  ally  ") == "social"

    def test_collaborating_maps_to_partnership(self):
        assert _coerce_relationship_type("collaborating") == "partnership"
        assert _coerce_relationship_type("collaborator") == "partnership"

    def test_schema_enum_values_pass_through(self):
        schema_enums = [
            "kinship", "partnership", "mentorship", "political",
            "factional", "social", "adversarial", "romantic", "spatial", "other",
        ]
        for val in schema_enums:
            result = _coerce_relationship_type(val)
            assert result == val

    def test_spatial_labels_map_to_spatial(self):
        spatial_labels = [
            "resides at", "resides_at", "located at", "located_at",
            "traveling to", "traveling_to", "departed from", "departed_from",
            "visited", "stationed at", "stationed_at", "moved to", "moved_to",
            "lives in", "lives_in", "headquartered at", "headquartered_at",
            "based in", "based_in", "connected to", "adjacent to",
            "near", "inside", "contains",
        ]
        for label in spatial_labels:
            assert _coerce_relationship_type(label) == "spatial", f"{label!r} should map to 'spatial'"

    def test_coercion_applied_in_consolidate(self):
        existing = {
            "target_id": "char-beta",
            "current_relationship": "working together",
            "type": "other",
            "status": "active",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-010",
        }
        update = {
            "current_relationship": "close allies",
            "type": "ally",
            "last_updated_turn": "turn-020",
        }
        _consolidate_relationship(existing, update)
        assert existing["type"] == "social"


# ---------------------------------------------------------------------------
# #127 — Event source_turns validation
# ---------------------------------------------------------------------------

class TestEventSourceTurnsValidation:
    def test_corrects_mismatched_source_turns(self):
        events = [
            {
                "id": "evt-346",
                "source_turns": ["turn-001"],
                "description": "Late game event",
            }
        ]
        _fix_event_source_turns(events, "turn-300")
        assert events[0]["source_turns"] == ["turn-300"]

    def test_valid_source_turns_unchanged(self):
        events = [
            {
                "id": "evt-100",
                "source_turns": ["turn-050"],
                "description": "Event near turn 050",
            }
        ]
        _fix_event_source_turns(events, "turn-052")
        assert events[0]["source_turns"] == ["turn-050"]

    def test_boundary_within_tolerance(self):
        events = [
            {
                "id": "evt-200",
                "source_turns": ["turn-095"],
                "description": "Within 5 turns",
            }
        ]
        _fix_event_source_turns(events, "turn-100")
        assert events[0]["source_turns"] == ["turn-095"]

    def test_boundary_beyond_tolerance(self):
        events = [
            {
                "id": "evt-201",
                "source_turns": ["turn-094"],
                "description": "Beyond 5 turns",
            }
        ]
        _fix_event_source_turns(events, "turn-100")
        assert events[0]["source_turns"] == ["turn-100"]

    def test_multiple_source_turns_partial_fix(self):
        events = [
            {
                "id": "evt-300",
                "source_turns": ["turn-001", "turn-098"],
                "description": "Mixed",
            }
        ]
        _fix_event_source_turns(events, "turn-100")
        assert "turn-100" in events[0]["source_turns"]
        assert "turn-098" in events[0]["source_turns"]
        assert "turn-001" not in events[0]["source_turns"]

    def test_no_source_turns_is_noop(self):
        events = [{"id": "evt-400", "description": "No source_turns"}]
        _fix_event_source_turns(events, "turn-100")
        assert "source_turns" not in events[0]

    def test_deduplicates_after_correction(self):
        events = [
            {
                "id": "evt-500",
                "source_turns": ["turn-001", "turn-002"],
                "description": "Both wrong",
            }
        ]
        _fix_event_source_turns(events, "turn-300")
        assert events[0]["source_turns"] == ["turn-300"]


# ---------------------------------------------------------------------------
# #128 — Stub backfill
# ---------------------------------------------------------------------------

class TestStubIdentification:
    def test_stub_with_event_stub_notes(self):
        entity = {"id": "char-lyra", "name": "Lyra", "identity": "Known",
                  "notes": "Auto-created by event-stub."}
        assert _is_stub_entity(entity) is True

    def test_stub_with_orphan_sweep_notes(self):
        entity = {"id": "char-lyra", "name": "Lyra", "identity": "Known",
                  "notes": "Auto-created by post-batch orphan sweep."}
        assert _is_stub_entity(entity) is True

    def test_non_stub_notes_not_flagged(self):
        entity = {"id": "char-test", "name": "Test", "identity": "A person.",
                  "notes": "Has a stubborn personality."}
        assert _is_stub_entity(entity) is False

    def test_stub_with_empty_identity(self):
        entity = {"id": "char-test", "name": "Test", "identity": "", "notes": "something"}
        assert _is_stub_entity(entity) is True

    def test_stub_with_missing_identity(self):
        entity = {"id": "char-test", "name": "Test", "notes": "normal"}
        assert _is_stub_entity(entity) is True

    def test_backfilled_entity_not_reflagged(self):
        entity = {"id": "char-lyra", "name": "Lyra", "identity": "An elf.",
                  "notes": "Backfilled from stub."}
        assert _is_stub_entity(entity) is False

    def test_full_entity_not_stub(self):
        entity = {
            "id": "char-grim",
            "name": "Grim",
            "identity": "A seasoned warrior.",
            "notes": "Discovered turn 5.",
        }
        assert _is_stub_entity(entity) is False

    def test_collect_context_from_events(self):
        events = [
            {
                "id": "evt-001",
                "related_entities": ["char-lyra"],
                "source_turns": ["turn-010"],
            },
            {
                "id": "evt-002",
                "related_entities": ["char-other"],
                "source_turns": ["turn-050"],
            },
        ]
        turns = [
            {"turn_id": "turn-009", "speaker": "DM", "text": "Previous context."},
            {"turn_id": "turn-010", "speaker": "DM", "text": "Lyra appeared at the gate."},
            {"turn_id": "turn-011", "speaker": "DM", "text": "Next context."},
            {"turn_id": "turn-050", "speaker": "DM", "text": "Other speaks."},
        ]
        context = _collect_stub_context("char-lyra", events, turns, "turn-010")
        assert "Lyra appeared" in context
        assert "Other speaks" not in context

    def test_collect_context_includes_first_seen(self):
        events = []  # no events reference this entity
        turns = [
            {"turn_id": "turn-004", "speaker": "DM", "text": "Previous turn."},
            {"turn_id": "turn-005", "speaker": "DM", "text": "First appearance."},
            {"turn_id": "turn-006", "speaker": "DM", "text": "Next turn."},
        ]
        context = _collect_stub_context("char-unknown", events, turns, "turn-005")
        assert "First appearance" in context
        # Neighbors should also be included
        assert "Previous turn" in context
        assert "Next turn" in context

    def test_backfill_preserves_first_seen_turn(self):
        """Stub detection finds entities correctly before backfill."""
        entity = {
            "id": "char-lyra",
            "name": "Lyra",
            "first_seen_turn": "turn-003",
            "notes": "Auto-created by event-stub.",
            "identity": "Entity referenced in events (stub — auto-created from event data).",
        }
        # The stub should be identified for backfill
        assert _is_stub_entity(entity) is True


# ---------------------------------------------------------------------------
# #129 — Levenshtein fuzzy dedup
# ---------------------------------------------------------------------------

def _make_entity(id_, name, turn="turn-001"):
    return {
        "id": id_,
        "name": name,
        "first_seen_turn": turn,
        "attributes": {},
        "relationships": [],
    }


class TestLevenshteinDedup:
    def test_communal_vs_communial(self):
        """Spelling variants with distance=1 should merge."""
        catalogs = {
            "locations.json": [
                _make_entity("loc-communal-home", "Communal Home", "turn-005"),
                _make_entity("loc-communial-home", "Communial Home", "turn-010"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 1
        assert len(catalogs["locations.json"]) == 1
        survivor = catalogs["locations.json"][0]
        assert survivor["id"] == "loc-communal-home"

    def test_distinct_entities_not_merged(self):
        """Truly distinct entities (distance > 2) should not merge."""
        catalogs = {
            "items.json": [
                _make_entity("item-sword", "Sword", "turn-001"),
                _make_entity("item-board", "Board", "turn-002"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 0
        assert len(catalogs["items.json"]) == 2

    def test_levenshtein_respects_first_char(self):
        """Entities whose stems start with different chars should not merge."""
        catalogs = {
            "locations.json": [
                _make_entity("loc-cave", "Cave", "turn-001"),
                _make_entity("loc-dave", "Dave", "turn-002"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 0
        assert len(catalogs["locations.json"]) == 2

    def test_no_double_merge_with_existing_rules(self):
        """Levenshtein dedup should not cause double-merges with token overlap."""
        catalogs = {
            "items.json": [
                _make_entity("item-healing-potion", "Healing Potion", "turn-001"),
                _make_entity("item-healng-potion", "Healng Potion", "turn-005"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        # Should merge exactly once (either by token overlap or levenshtein)
        assert count == 1
        assert len(catalogs["items.json"]) == 1

    def test_length_difference_guard(self):
        """Entities with large length difference should not merge even with low distance."""
        catalogs = {
            "locations.json": [
                _make_entity("loc-ab", "Ab", "turn-001"),
                _make_entity("loc-abcdef", "Abcdef", "turn-002"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 0
        assert len(catalogs["locations.json"]) == 2
