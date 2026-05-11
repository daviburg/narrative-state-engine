"""Tests for entity identity corruption guards (#339)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import merge_entity
from semantic_extraction import _is_misclassified_item, _coerce_entity_fields


def _make_entity(id_, name, etype="item", turn="turn-001", **kwargs):
    entity = {
        "id": id_,
        "name": name,
        "type": etype,
        "identity": f"{name} identity.",
        "first_seen_turn": turn,
        "last_updated_turn": turn,
    }
    entity.update(kwargs)
    return entity


class TestNameStabilityGuard:
    def test_rejects_spear_to_bowl_rename(self):
        catalogs = {
            "items.json": [_make_entity("item-spear", "Crude wood-hafted spear")],
            "characters.json": [], "locations.json": [], "factions.json": [],
        }
        update = _make_entity("item-spear", "sturdy bowl", turn="turn-020")
        merge_entity(catalogs, update)
        assert catalogs["items.json"][0]["name"] == "Crude wood-hafted spear"

    def test_allows_legitimate_rename(self):
        catalogs = {
            "items.json": [_make_entity("item-spear", "Crude spear")],
            "characters.json": [], "locations.json": [], "factions.json": [],
        }
        update = _make_entity("item-spear", "Crude wood-hafted spear", turn="turn-020")
        merge_entity(catalogs, update)
        assert catalogs["items.json"][0]["name"] == "Crude wood-hafted spear"

    def test_allows_proper_name_reveal(self):
        catalogs = {
            "characters.json": [_make_entity("char-elder", "The elder", etype="character")],
            "items.json": [], "locations.json": [], "factions.json": [],
        }
        update = _make_entity("char-elder", "Elder Lyra", etype="character", turn="turn-020")
        merge_entity(catalogs, update)
        assert catalogs["characters.json"][0]["name"] == "Elder Lyra"


class TestIdentityOverwriteGuard:
    def test_rejects_identity_from_wrong_entity(self):
        catalogs = {
            "items.json": [_make_entity("item-spear", "Crude spear", identity="A simple hunting spear.")],
            "characters.json": [], "locations.json": [], "factions.json": [],
        }
        update = {"id": "item-spear", "name": "sturdy bowl", "type": "item",
                  "identity": "A practical ceramic vessel.", "first_seen_turn": "turn-001",
                  "last_updated_turn": "turn-020"}
        merge_entity(catalogs, update)
        assert "ceramic" not in catalogs["items.json"][0]["identity"].lower()

    def test_allows_identity_update_same_name(self):
        catalogs = {
            "items.json": [_make_entity("item-spear", "Crude spear", identity="A spear.")],
            "characters.json": [], "locations.json": [], "factions.json": [],
        }
        update = {"id": "item-spear", "name": "Crude spear", "type": "item",
                  "identity": "A sturdy hunting spear used for survival.",
                  "first_seen_turn": "turn-001", "last_updated_turn": "turn-020"}
        merge_entity(catalogs, update)
        assert "hunting spear" in catalogs["items.json"][0]["identity"]


class TestAbstractItemFilter:
    def test_method_rejected(self):
        assert _is_misclassified_item({"name": "Pattern Disruption Method", "type": "item"})

    def test_protocol_rejected(self):
        assert _is_misclassified_item({"name": "Plague Treatment Protocol", "type": "item"})

    def test_technique_rejected(self):
        assert _is_misclassified_item({"name": "Ancient technique", "type": "item"})

    def test_real_item_not_rejected(self):
        assert not _is_misclassified_item({"name": "Crude spear", "type": "item"})

    def test_non_item_ignored(self):
        assert not _is_misclassified_item({"name": "Pattern Disruption Method", "type": "concept"})


class TestCharacterFieldStripping:
    def test_species_removed_from_item(self):
        entity = {"id": "item-spear", "name": "Crude spear", "type": "item",
                  "stable_attributes": {"species": {"value": "wood"}, "race": {"value": "crafted"},
                                        "class": {"value": "weapon"}, "description": {"value": "A spear"}},
                  "first_seen_turn": "turn-001", "last_updated_turn": "turn-020"}
        result = _coerce_entity_fields(entity)
        sa = result.get("stable_attributes", {})
        assert "species" not in sa
        assert "race" not in sa
        assert "class" not in sa
        assert "description" in sa
