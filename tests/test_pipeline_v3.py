"""Integration tests for orphan entity feedback loop and PC extraction resilience."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _create_orphan_stubs,
    _pc_partial_merge,
    _format_prior_entity_context,
    _post_batch_orphan_sweep,
)


# ---------------------------------------------------------------------------
# Orphan stub creation tests
# ---------------------------------------------------------------------------

class TestOrphanStubCreation:
    def test_creates_stub_for_orphan_id(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-player", "char-kael"], "turn_id": "turn-150"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-150")
        ids = {e["id"] for e in catalogs["characters.json"]}
        assert "char-kael" in ids
        stub = next(e for e in catalogs["characters.json"] if e["id"] == "char-kael")
        assert stub["name"] == "Kael"
        assert stub["type"] == "character"
        assert "Auto-created" in stub["notes"]

    def test_skips_char_player(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-player"], "turn_id": "turn-150"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-150")
        assert len(catalogs["characters.json"]) == 1

    def test_skips_generic_names(self):
        catalogs = {
            "characters.json": [],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-stranger", "char-figure"], "turn_id": "turn-100"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_skips_already_known(self):
        catalogs = {
            "characters.json": [
                {"id": "char-kael", "name": "Kael", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-kael"], "turn_id": "turn-200"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-200")
        assert len(catalogs["characters.json"]) == 1  # no duplicate


class TestLocationStub:
    def test_location_stub_goes_to_locations(self):
        catalogs = {
            "characters.json": [],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["loc-forest-camp"], "turn_id": "turn-050"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-050")
        assert len(catalogs["locations.json"]) == 1
        stub = catalogs["locations.json"][0]
        assert stub["id"] == "loc-forest-camp"
        assert stub["type"] == "location"


# ---------------------------------------------------------------------------
# PC partial merge tests
# ---------------------------------------------------------------------------

class TestPCPartialMerge:
    def test_merges_current_status(self):
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "current_status": "Resting at camp.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }
        entity_data = {
            "id": "char-player",
            "current_status": "Preparing for battle.",
            # Missing required fields — would fail validation
        }
        _pc_partial_merge(catalogs, entity_data, "turn-100")
        pc = catalogs["characters.json"][0]
        assert pc["current_status"] == "Preparing for battle."
        assert pc["last_updated_turn"] == "turn-100"

    def test_merges_volatile_state(self):
        catalogs = {
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
        entity_data = {
            "id": "char-player",
            "volatile_state": {"condition": "wounded", "equipment": ["sword", "shield"]},
        }
        _pc_partial_merge(catalogs, entity_data, "turn-075")
        pc = catalogs["characters.json"][0]
        assert pc["volatile_state"]["condition"] == "wounded"
        assert pc["last_updated_turn"] == "turn-075"

    def test_filters_disallowed_stable_attrs(self):
        catalogs = {
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
        entity_data = {
            "id": "char-player",
            "stable_attributes": {
                "race": {"value": "Human", "inference": False},
                "backstory": {"value": "Born in a village", "inference": True},
            },
        }
        _pc_partial_merge(catalogs, entity_data, "turn-060")
        pc = catalogs["characters.json"][0]
        assert "race" in pc["stable_attributes"]
        assert "backstory" not in pc["stable_attributes"]

    def test_no_merge_when_no_valid_fields(self):
        catalogs = {
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
        entity_data = {"id": "char-player"}
        _pc_partial_merge(catalogs, entity_data, "turn-060")
        pc = catalogs["characters.json"][0]
        assert pc["last_updated_turn"] == "turn-050"  # unchanged


# ---------------------------------------------------------------------------
# PC context trimming tests
# ---------------------------------------------------------------------------

class TestPCContextTrimming:
    def test_trims_stable_attributes_for_pc(self):
        entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "current_status": "In camp.",
            "stable_attributes": {
                "race": {"value": "Human"},
                "class": {"value": "Ranger"},
                "backstory": {"value": "Born in a village"},
                "motivation": {"value": "Find the artifact"},
                "aliases": {"value": ["PC", "Hero"]},
            },
            "volatile_state": {"condition": "healthy"},
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-345",
        }
        result = json.loads(_format_prior_entity_context(entry))
        # Only key attrs should be present
        sa = result.get("stable_attributes", {})
        assert "race" in sa
        assert "class" in sa
        assert "aliases" in sa
        assert "backstory" not in sa
        assert "motivation" not in sa

    def test_does_not_trim_non_pc(self):
        entry = {
            "id": "char-kael",
            "name": "Kael",
            "type": "character",
            "identity": "A young hunter.",
            "stable_attributes": {
                "race": {"value": "Elf"},
                "backstory": {"value": "Forest-born"},
                "motivation": {"value": "Protect the grove"},
            },
            "first_seen_turn": "turn-050",
            "last_updated_turn": "turn-200",
        }
        result = json.loads(_format_prior_entity_context(entry))
        sa = result.get("stable_attributes", {})
        assert "backstory" in sa
        assert "motivation" in sa

    def test_trims_volatile_state_lists(self):
        entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "volatile_state": {
                "equipment": ["sword", "shield", "bow", "arrows", "cloak"],
            },
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-345",
        }
        result = json.loads(_format_prior_entity_context(entry))
        vs = result.get("volatile_state", {})
        # List longer than 3 should be trimmed to last 3
        assert len(vs["equipment"]) == 3
        assert vs["equipment"] == ["bow", "arrows", "cloak"]

    def test_pc_context_size_reasonable(self):
        """PC context with large attributes should still be under 4K tokens (~16KB)."""
        large_entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character in an epic RPG campaign.",
            "current_status": "Standing at the edge of the forest, preparing for the final battle.",
            "stable_attributes": {
                "race": {"value": "Human", "inference": False, "source_turn": "turn-001"},
                "class": {"value": "Ranger", "inference": False, "source_turn": "turn-001"},
                "aliases": {"value": ["PC", "Hero", "The Chosen"], "inference": False},
                "backstory": {"value": "A long backstory " * 50},
                "motivation": {"value": "Motivation text " * 30},
                "personality": {"value": "Personality text " * 20},
                "equipment_notes": {"value": "Equipment " * 40},
                "quest_history": {"value": "Quest " * 60},
            },
            "volatile_state": {
                "condition": "healthy",
                "equipment": [f"item-{i}" for i in range(20)],
                "location": "forest-edge",
            },
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-345",
        }
        result = _format_prior_entity_context(large_entry)
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(result) / 4
        assert estimated_tokens < 4000, f"PC context too large: ~{estimated_tokens:.0f} tokens"


# ---------------------------------------------------------------------------
# Post-batch orphan sweep tests
# ---------------------------------------------------------------------------

class TestPostBatchOrphanSweep:
    def test_creates_stubs_for_frequent_orphans(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": f"evt-{i}", "related_entities": ["char-player", "char-kael"],
             "turn_id": f"turn-{150 + i}"}
            for i in range(5)
        ]
        count = _post_batch_orphan_sweep(catalogs, events)
        assert count == 1
        ids = {e["id"] for e in catalogs["characters.json"]}
        assert "char-kael" in ids

    def test_skips_infrequent_orphans(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-player", "char-random"],
             "turn_id": "turn-100"},
            {"id": "evt-2", "related_entities": ["char-player", "char-random"],
             "turn_id": "turn-101"},
        ]
        count = _post_batch_orphan_sweep(catalogs, events)
        assert count == 0
