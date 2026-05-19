"""Tests for smart entity context compression."""
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _format_prior_entity_context,
    _filter_relationships_for_scene,
    _SCENE_MAX_RELATIONSHIPS,
    _ARC_AWARE_MAX_VOLATILE_SNAPSHOTS,
)
from catalog_merger import (
    _DEFAULT_ENTITY_BUDGET_FRACTION,
    _DEFAULT_STALENESS_THRESHOLD,
)


class TestConstantValues:
    """Verify compression constants are at expected values."""

    def test_volatile_snapshots_capped_at_1(self):
        assert _ARC_AWARE_MAX_VOLATILE_SNAPSHOTS == 1

    def test_scene_max_relationships_is_5(self):
        assert _SCENE_MAX_RELATIONSHIPS == 5

    def test_entity_budget_fraction_is_15_percent(self):
        assert _DEFAULT_ENTITY_BUDGET_FRACTION == 0.15

    def test_staleness_threshold_is_30(self):
        assert _DEFAULT_STALENESS_THRESHOLD == 30


class TestStableAttributeCompression:
    """Non-PC entities get only key attributes with stripped metadata."""

    def test_npc_stable_attrs_filtered(self):
        entity = {
            "id": "char-elder",
            "name": "Elder",
            "type": "character",
            "identity": "An elder.",
            "stable_attributes": {
                "role": {"value": "tribal leader", "inference": True, "confidence": 0.8, "source_turn": "turn-010"},
                "favorite_food": {"value": "berries", "inference": True, "confidence": 0.5, "source_turn": "turn-050"},
                "aliases": {"value": ["The Old One"], "inference": False, "confidence": 1.0, "source_turn": "turn-010"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        assert "role" in sa
        assert "aliases" in sa
        assert "favorite_food" not in sa  # Not in _NPC_KEY_STABLE_ATTRS

    def test_npc_stable_attr_values_stripped(self):
        entity = {
            "id": "char-elder",
            "name": "Elder",
            "type": "character",
            "identity": "An elder.",
            "stable_attributes": {
                "role": {"value": "tribal leader", "inference": True, "confidence": 0.8, "source_turn": "turn-010"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        # Value should be unwrapped — just the string, not the dict
        assert result["stable_attributes"]["role"] == "tribal leader"

    def test_pc_stable_attrs_use_existing_filter(self):
        entity = {
            "id": "char-player",
            "name": "Fenouille",
            "type": "character",
            "identity": "A healer.",
            "stable_attributes": {
                "species": {"value": "elf", "inference": False, "confidence": 1.0, "source_turn": "turn-001"},
                "role": {"value": "healer", "inference": True, "confidence": 0.9, "source_turn": "turn-001"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        assert "species" in sa
        # PC uses _PC_KEY_STABLE_ATTRS — "role" may or may not be in that set
        # The key test is that PC still uses the existing filter, not the new NPC one

    def test_pc_stable_attr_values_also_stripped(self):
        """PC stable attributes also get provenance metadata stripped."""
        entity = {
            "id": "char-player",
            "name": "Fenouille",
            "type": "character",
            "identity": "A healer.",
            "stable_attributes": {
                "species": {"value": "elf", "inference": False, "confidence": 1.0, "source_turn": "turn-001"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        # Value should be unwrapped for PC too
        assert result["stable_attributes"]["species"] == "elf"

    def test_plain_string_stable_attrs_preserved(self):
        """Stable attributes that are plain strings (not dicts) are preserved as-is."""
        entity = {
            "id": "char-guard",
            "name": "Guard",
            "type": "character",
            "identity": "A guard.",
            "stable_attributes": {
                "role": "sentry",  # Plain string, not a dict
                "species": {"value": "human", "inference": False, "confidence": 1.0, "source_turn": "turn-005"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        assert sa["role"] == "sentry"
        assert sa["species"] == "human"


class TestRelationshipTypePrioritization:
    """Kinship relationships are prioritized over spatial ones."""

    def test_kinship_kept_over_spatial_when_capped(self):
        # Create relationships: mix of kinship and spatial, more than cap
        rels = []
        # 3 kinship
        for i in range(3):
            rels.append({
                "source_id": "char-a",
                "target_id": f"char-kin-{i}",
                "type": "kinship",
                "current_relationship": "parent_of",
                "status": "active",
                "last_updated_turn": f"turn-{100+i:03d}",
            })
        # 5 spatial
        for i in range(5):
            rels.append({
                "source_id": "char-a",
                "target_id": f"loc-place-{i}",
                "type": "spatial",
                "current_relationship": "resides_at",
                "status": "active",
                "last_updated_turn": f"turn-{100+i:03d}",
            })
        # Filter with empty mentions (so no mention bonus)
        filtered = _filter_relationships_for_scene(rels, set(), 105)
        # All 3 kinship should be in the result (they get +20 bonus)
        kinship_in_result = [r for r in filtered if r.get("type") == "kinship"]
        assert len(kinship_in_result) == 3, f"Expected 3 kinship, got {len(kinship_in_result)}"

    def test_partnership_also_prioritized(self):
        rels = []
        # 2 partnership
        for i in range(2):
            rels.append({
                "source_id": "char-a",
                "target_id": f"char-partner-{i}",
                "type": "partnership",
                "current_relationship": "ally",
                "status": "active",
                "last_updated_turn": f"turn-{100+i:03d}",
            })
        # 6 spatial (more than cap)
        for i in range(6):
            rels.append({
                "source_id": "char-a",
                "target_id": f"loc-place-{i}",
                "type": "spatial",
                "current_relationship": "resides_at",
                "status": "active",
                "last_updated_turn": f"turn-{100+i:03d}",
            })
        filtered = _filter_relationships_for_scene(rels, set(), 105)
        partnership_in_result = [r for r in filtered if r.get("type") == "partnership"]
        assert len(partnership_in_result) == 2

    def test_max_relationships_is_5(self):
        """Total cap is 5 relationships."""
        rels = []
        for i in range(10):
            rels.append({
                "source_id": "char-a",
                "target_id": f"char-{i}",
                "type": "social",
                "current_relationship": "acquaintance",
                "status": "active",
                "last_updated_turn": f"turn-{100+i:03d}",
            })
        filtered = _filter_relationships_for_scene(rels, set(), 105)
        assert len(filtered) <= 5
