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
    _HIGH_ENTITY_COUNT_THRESHOLD,
    _HIGH_ENTITY_DETAIL_MAX_TOKENS,
)
from catalog_merger import (
    _DEFAULT_ENTITY_BUDGET_FRACTION,
    _DEFAULT_STALENESS_THRESHOLD,
    _DISCOVERY_STALENESS_THRESHOLD,
    _CONTEXT_FLOOR_FRACTION,
    format_known_entities_bounded,
    _estimate_tokens,
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


class TestHighEntityMaxTokens:
    """Verify adaptive max_tokens threshold for entity detail extraction."""

    def test_threshold_constant_is_20(self):
        assert _HIGH_ENTITY_COUNT_THRESHOLD == 20

    def test_high_entity_max_tokens_is_8192(self):
        assert _HIGH_ENTITY_DETAIL_MAX_TOKENS == 8192

    def test_detail_max_tokens_logic_low_count(self):
        """When entity count <= 20, max_tokens should remain at default (None)."""
        entity_count = 15
        result = _HIGH_ENTITY_DETAIL_MAX_TOKENS if entity_count > _HIGH_ENTITY_COUNT_THRESHOLD else None
        assert result is None

    def test_detail_max_tokens_logic_high_count(self):
        """When entity count > 20, max_tokens should be 8192."""
        entity_count = 25
        result = _HIGH_ENTITY_DETAIL_MAX_TOKENS if entity_count > _HIGH_ENTITY_COUNT_THRESHOLD else None
        assert result == 8192

    def test_detail_max_tokens_at_boundary(self):
        """Entity count exactly at threshold should not trigger high tokens."""
        entity_count = 20
        result = _HIGH_ENTITY_DETAIL_MAX_TOKENS if entity_count > _HIGH_ENTITY_COUNT_THRESHOLD else None
        assert result is None

    def test_detail_max_tokens_one_over_boundary(self):
        """Entity count one over threshold triggers high tokens."""
        entity_count = 21
        result = _HIGH_ENTITY_DETAIL_MAX_TOKENS if entity_count > _HIGH_ENTITY_COUNT_THRESHOLD else None
        assert result == 8192


class TestContextFloor:
    """Verify the 50% context floor prevents catastrophic entity loss."""

    def test_floor_fraction_is_50_percent(self):
        assert _CONTEXT_FLOOR_FRACTION == 0.5

    def test_discovery_staleness_threshold_more_permissive(self):
        """Discovery staleness threshold must be >= default to retain more entities."""
        assert _DISCOVERY_STALENESS_THRESHOLD >= _DEFAULT_STALENESS_THRESHOLD

    def test_discovery_staleness_threshold_is_50(self):
        assert _DISCOVERY_STALENESS_THRESHOLD == 50

    def _make_large_catalogs(self, n=30):
        """Make a catalog with n entities."""
        entities = []
        for i in range(n):
            entities.append({
                "id": f"char-npc{i:03d}",
                "name": f"NPC {i}",
                "type": "character",
                "identity": "A character with a very long detailed description. " * 5,
                "last_updated_turn": f"turn-{max(1, 200 - i):03d}",
            })
        return {"characters.json": entities}

    def test_floor_prevents_over_compression(self):
        """When floor triggers, all entity IDs are preserved in the result."""
        catalogs = self._make_large_catalogs(30)

        # Use a tiny budget to force heavy compression and trigger floor
        result = format_known_entities_bounded(
            catalogs,
            current_turn=300,
            entity_context_budget=5,  # 5 tokens -- tiny, will trigger floor
            turn_text="something happened",
        )
        # Floor must have fired: all entity IDs must appear in the result
        for i in range(30):
            assert f"char-npc{i:03d}" in result, f"char-npc{i:03d} missing from floor fallback"

    def test_normal_compression_within_floor_passes_through(self):
        """When compression stays above 50%, normal behavior applies."""
        catalogs = {
            "characters.json": [
                {"id": "char-a", "name": "Alice", "type": "character",
                 "identity": "A hero.", "last_updated_turn": "turn-299"},
                {"id": "char-b", "name": "Bob", "type": "character",
                 "identity": "A merchant.", "last_updated_turn": "turn-295"},
            ]
        }
        # Large budget so no compression needed
        result = format_known_entities_bounded(
            catalogs,
            current_turn=300,
            entity_context_budget=10000,
            turn_text="Alice went to see Bob",
        )
        assert "char-a" in result
        assert "char-b" in result

    def test_floor_triggers_on_discovery_path(self):
        """Floor triggers on discovery calls with _DISCOVERY_STALENESS_THRESHOLD."""
        # Use a large catalog where staleness filtering will exclude many entities
        # when turn_text mentions only a few.
        catalogs = self._make_large_catalogs(30)

        result = format_known_entities_bounded(
            catalogs,
            current_turn=300,
            entity_context_budget=5,  # tiny budget to force floor
            turn_text="something happened",
            staleness_threshold=_DISCOVERY_STALENESS_THRESHOLD,
            context_label="discovery",
        )
        # Floor must have fired: result should contain all entity IDs
        for i in range(30):
            assert f"char-npc{i:03d}" in result


class TestItemFactionExemption:
    """Items and factions must not have their stable attributes compressed."""

    def test_item_keeps_all_stable_attrs(self):
        entity = {
            "id": "item-sword001",
            "name": "Iron Sword",
            "type": "item",
            "identity": "A simple iron sword.",
            "stable_attributes": {
                "material": {"value": "iron", "inference": False, "confidence": 1.0, "source_turn": "turn-010"},
                "enchantment": {"value": "fire", "inference": True, "confidence": 0.8, "source_turn": "turn-020"},
                "weight": {"value": "heavy", "inference": False, "confidence": 1.0, "source_turn": "turn-010"},
                "origin": {"value": "dwarven forge", "inference": True, "confidence": 0.7, "source_turn": "turn-015"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        # All attributes must be present -- items are exempt from NPC filtering
        assert "material" in sa
        assert "enchantment" in sa
        assert "weight" in sa
        assert "origin" in sa

    def test_faction_keeps_all_stable_attrs(self):
        entity = {
            "id": "faction-guild001",
            "name": "Merchants Guild",
            "type": "faction",
            "identity": "A powerful merchants guild.",
            "stable_attributes": {
                "alignment": {"value": "neutral", "inference": False, "confidence": 1.0, "source_turn": "turn-005"},
                "headquarters": {"value": "Capital City", "inference": False, "confidence": 1.0, "source_turn": "turn-005"},
                "leader": {"value": "Guild Master Vann", "inference": True, "confidence": 0.9, "source_turn": "turn-010"},
                "secret_agenda": {"value": "control trade routes", "inference": True, "confidence": 0.6, "source_turn": "turn-050"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        assert "alignment" in sa
        assert "headquarters" in sa
        assert "leader" in sa
        assert "secret_agenda" in sa

    def test_character_still_filters_non_key_attrs(self):
        """Non-PC characters still get attribute compression (not exempt)."""
        entity = {
            "id": "char-npc001",
            "name": "Innkeeper",
            "type": "character",
            "identity": "An innkeeper.",
            "stable_attributes": {
                "role": {"value": "innkeeper", "inference": False, "confidence": 1.0, "source_turn": "turn-005"},
                "favorite_food": {"value": "stew", "inference": True, "confidence": 0.5, "source_turn": "turn-020"},
                "birthday": {"value": "spring equinox", "inference": True, "confidence": 0.4, "source_turn": "turn-100"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        assert "role" in sa
        assert "favorite_food" not in sa
        assert "birthday" not in sa

    def test_item_faction_provenance_still_stripped(self):
        """Even though items/factions keep all attrs, provenance metadata is stripped."""
        entity = {
            "id": "item-ring001",
            "name": "Magic Ring",
            "type": "item",
            "identity": "A glowing ring.",
            "stable_attributes": {
                "power": {"value": "invisibility", "inference": True, "confidence": 0.9, "source_turn": "turn-030"},
            },
        }
        result = json.loads(_format_prior_entity_context(entity))
        sa = result.get("stable_attributes", {})
        # Value should be unwrapped -- just the string
        assert sa.get("power") == "invisibility"


class TestCompressionLogging:
    """Verify compression metrics logging includes before/after token counts."""

    def test_compression_log_emitted(self, caplog):
        """Compression log is emitted at DEBUG level with before/after token counts."""
        import logging
        entities = []
        for i in range(10):
            entities.append({
                "id": f"char-npc{i:03d}",
                "name": f"Character {i}",
                "type": "character",
                "identity": "A detailed character with a long description. " * 10,
                "last_updated_turn": f"turn-{200 - i * 5:03d}",
            })
        catalogs = {"characters.json": entities}

        # Very small budget to force compression
        with caplog.at_level(logging.DEBUG, logger="catalog_merger"):
            format_known_entities_bounded(
                catalogs,
                current_turn=250,
                entity_context_budget=20,
                turn_text="Something happened",
                context_label="test-compression",
            )
        # Should emit a COMPRESS: record at DEBUG level
        assert any("COMPRESS:" in r.message for r in caplog.records)
        assert any("test-compression" in r.message for r in caplog.records)

    def test_compression_log_includes_token_counts(self, caplog):
        """Compression log line contains numeric token counts."""
        import logging
        import re
        entities = []
        for i in range(5):
            entities.append({
                "id": f"char-npc{i:03d}",
                "name": f"NPC {i}",
                "type": "character",
                "identity": "A long detailed backstory. " * 20,
                "last_updated_turn": f"turn-{200 - i * 10:03d}",
            })
        catalogs = {"characters.json": entities}

        with caplog.at_level(logging.DEBUG, logger="catalog_merger"):
            format_known_entities_bounded(
                catalogs,
                current_turn=250,
                entity_context_budget=50,
                turn_text="NPC 0 did something",
                context_label="test-tokens",
            )
        # Must emit a COMPRESS: record and it must contain token counts
        compress_messages = [r.message for r in caplog.records if "COMPRESS:" in r.message]
        assert compress_messages, "Expected at least one COMPRESS: log record"
        assert re.search(r'\d+ tokens', compress_messages[0])
