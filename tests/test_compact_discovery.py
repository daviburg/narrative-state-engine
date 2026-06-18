"""Tests for compact discovery entry expansion (#310).

Covers:
- Compact entries (existing_id + confidence only) get name/type from catalog
- Mixed full + compact entries are handled correctly
- Compact entries with an unresolvable existing_id are dropped (fail closed, #524)
- _repair_truncated_discovery works with mixed full/compact JSON
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure openai mock exists for llm_client import
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from semantic_extraction import _repair_truncated_discovery


def _make_catalogs(*entities):
    """Build a minimal catalogs dict from (id, name, type) tuples."""
    catalog = {}
    for eid, name, etype in entities:
        filename = f"{etype}s.json"
        catalog.setdefault(filename, [])
        catalog[filename].append({"id": eid, "name": name, "type": etype})
    return catalog


class TestCompactDiscoveryExpansion:
    """Verify that compact discovery entries are expanded from catalogs."""

    def _expand(self, discovered, catalogs):
        """Call the production compact-expansion helper (#310, #524)."""
        from semantic_extraction import _expand_compact_discovery_entries

        expanded, _count, _dropped = _expand_compact_discovery_entries(discovered, catalogs)
        return expanded

    def test_compact_entry_gets_name_and_type(self):
        """A compact entry with only existing_id + confidence is expanded."""
        catalogs = _make_catalogs(("char-kael", "Kael", "character"))
        discovered = [{"existing_id": "char-kael", "confidence": 0.9}]
        result = self._expand(discovered, catalogs)
        assert result[0]["name"] == "Kael"
        assert result[0]["type"] == "character"
        assert result[0]["is_new"] is False
        assert result[0]["proposed_id"] is None
        assert result[0]["existing_id"] == "char-kael"
        assert result[0]["confidence"] == 0.9

    def test_full_entry_not_modified(self):
        """Full entries with name already set are not touched."""
        catalogs = _make_catalogs(("char-kael", "Kael", "character"))
        discovered = [{
            "name": "Kael",
            "type": "character",
            "is_new": False,
            "existing_id": "char-kael",
            "proposed_id": None,
            "confidence": 0.95,
            "source_turn": "turn-042",
        }]
        result = self._expand(discovered, catalogs)
        assert result[0]["name"] == "Kael"
        assert result[0]["source_turn"] == "turn-042"

    def test_mixed_full_and_compact(self):
        """Mixed list of full and compact entries both work."""
        catalogs = _make_catalogs(
            ("char-kael", "Kael", "character"),
            ("loc-longhouse", "Communal Longhouse", "location"),
        )
        discovered = [
            {
                "name": "New Entity",
                "type": "item",
                "is_new": True,
                "existing_id": None,
                "proposed_id": "item-new-entity",
                "description": "A shiny new item.",
                "confidence": 0.85,
                "source_turn": "turn-300",
            },
            {"existing_id": "char-kael", "confidence": 0.9},
            {"existing_id": "loc-longhouse", "confidence": 0.8},
        ]
        result = self._expand(discovered, catalogs)
        assert len(result) == 3
        # Full entry unchanged
        assert result[0]["name"] == "New Entity"
        assert result[0]["is_new"] is True
        # Compact entries expanded
        assert result[1]["name"] == "Kael"
        assert result[1]["type"] == "character"
        assert result[2]["name"] == "Communal Longhouse"
        assert result[2]["type"] == "location"

    def test_unknown_existing_id_dropped(self):
        """Compact entry with an unresolvable existing_id is dropped (fail closed, #524).

        Fabricating ``name=raw-id`` + ``is_new=False`` for an id absent from the
        catalog would smuggle a bogus fragment downstream into ``merge_entity``,
        so the production helper rejects it instead of inventing defaults.
        """
        from semantic_extraction import _expand_compact_discovery_entries

        catalogs = _make_catalogs()  # empty
        discovered = [{"existing_id": "char-unknown", "confidence": 0.7}]
        expanded, _count, dropped = _expand_compact_discovery_entries(discovered, catalogs)
        assert expanded == []
        assert dropped == ["char-unknown"]

    def test_compact_entry_preserves_extra_fields(self):
        """If LLM includes extra fields in compact entry, they are kept."""
        catalogs = _make_catalogs(("char-kael", "Kael", "character"))
        discovered = [{"existing_id": "char-kael", "confidence": 0.9, "source_turn": "turn-300"}]
        result = self._expand(discovered, catalogs)
        assert result[0]["name"] == "Kael"
        assert result[0]["source_turn"] == "turn-300"


class TestRepairTruncatedDiscoveryWithCompact:
    """Verify JSON repair handles mixed full/compact discovery entries."""

    def test_repair_preserves_compact_entries(self):
        """Truncation after compact entries preserves them."""
        partial = '{"entities": [{"existing_id": "char-kael", "confidence": 0.9}, {"existing_id": "loc-longhouse", "confidence": 0.8}, {"name": "Trun'
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert len(result["entities"]) == 2
        assert result["entities"][0]["existing_id"] == "char-kael"
        assert result["entities"][1]["existing_id"] == "loc-longhouse"

    def test_repair_mixed_full_and_compact(self):
        """Truncation mid-full-entry preserves prior compact and full entries."""
        partial = (
            '{"entities": ['
            '{"name": "Kael", "type": "character", "is_new": false, "existing_id": "char-kael", "proposed_id": null, "confidence": 0.95, "source_turn": "turn-042"}, '
            '{"existing_id": "loc-longhouse", "confidence": 0.8}, '
            '{"name": "New Thing", "type": "item", "is_new": true, "existing_id": null, "proposed_id": "item-new-thi'
        )
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert len(result["entities"]) == 2
        assert result["entities"][0]["name"] == "Kael"
        assert result["entities"][1]["existing_id"] == "loc-longhouse"

    def test_repair_all_compact(self):
        """Repair works when all entries are compact format."""
        partial = '{"entities": [{"existing_id": "char-a", "confidence": 0.9}, {"existing_id": "char-b", "confidence": 0.8}, {"existing_id": "char-c'
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert len(result["entities"]) == 2
        assert result["entities"][0]["existing_id"] == "char-a"
        assert result["entities"][1]["existing_id"] == "char-b"


class TestCompactExpansionDownstreamCompat:
    """Verify expanded compact entries are compatible with downstream pipeline."""

    def _expand(self, discovered, catalogs):
        """Call the production compact-expansion helper (#310, #524)."""
        from semantic_extraction import _expand_compact_discovery_entries

        expanded, _count, _dropped = _expand_compact_discovery_entries(discovered, catalogs)
        return expanded

    def test_expanded_entry_has_correct_entity_id(self):
        """get_entity_id returns existing_id for expanded compact entries."""
        from semantic_extraction import get_entity_id

        catalogs = _make_catalogs(("char-kael", "Kael", "character"))
        discovered = [{"existing_id": "char-kael", "confidence": 0.9}]
        result = self._expand(discovered, catalogs)
        assert get_entity_id(result[0]) == "char-kael"

    def test_expanded_entry_not_treated_as_new(self):
        """Expanded compact entries are not new, so detail extraction fetches existing."""
        from catalog_merger import find_entity_by_id

        catalogs = _make_catalogs(("char-kael", "Kael", "character"))
        discovered = [{"existing_id": "char-kael", "confidence": 0.9}]
        result = self._expand(discovered, catalogs)
        entity = result[0]

        # Simulate detail extraction path: existing entities look up catalog
        assert entity["is_new"] is False
        found = find_entity_by_id(catalogs, entity["existing_id"])
        assert found is not None
        _, cat_entry = found
        assert cat_entry["name"] == "Kael"

    def test_expanded_entry_source_turn_set_by_postprocess(self):
        """Post-processing fills source_turn if missing from compact entry."""
        catalogs = _make_catalogs(("loc-camp", "Camp", "location"))
        discovered = [{"existing_id": "loc-camp", "confidence": 0.9}]
        result = self._expand(discovered, catalogs)
        entity = result[0]
        # source_turn is not set by expansion — post-processing fills it
        assert "source_turn" not in entity
        # Simulate post-processing
        entity.setdefault("source_turn", "turn-201")
        assert entity["source_turn"] == "turn-201"
