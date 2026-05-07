"""Tests for cross-catalog type conflict detection (#303).

Verifies that _find_cross_catalog_type_conflict rejects entities whose name
already exists in a different catalog under a different type.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _find_cross_catalog_type_conflict


def _make_catalog_entry(id_, name, type_, aliases=None):
    entry = {
        "id": id_,
        "name": name,
        "type": type_,
        "first_seen_turn": "turn-001",
        "stable_attributes": {},
    }
    if aliases:
        entry["stable_attributes"]["aliases"] = {"value": aliases}
    return entry


def _make_discovery(name, type_, is_new=True, proposed_id=None):
    return {
        "name": name,
        "type": type_,
        "is_new": is_new,
        "proposed_id": proposed_id or f"{type_[:3]}-{name.lower().replace(' ', '-')}",
    }


class TestCrossCatalogConflict:
    """Test _find_cross_catalog_type_conflict detects name collisions."""

    def test_same_name_different_type_rejected(self):
        """Fenouille exists as character → loc-fenouille should be rejected."""
        catalogs = {
            "characters.json": [
                _make_catalog_entry("char-fenouille", "Fenouille", "character"),
            ],
            "locations.json": [],
        }
        entity = _make_discovery("Fenouille", "location", proposed_id="loc-fenouille")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is not None
        assert conflict["id"] == "char-fenouille"

    def test_same_name_same_type_no_conflict(self):
        """Same type should not trigger a conflict."""
        catalogs = {
            "characters.json": [
                _make_catalog_entry("char-fenouille", "Fenouille", "character"),
            ],
        }
        entity = _make_discovery("Fenouille", "character", proposed_id="char-fenouille-2")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is None

    def test_article_stripped(self):
        """'The Fragment' matches 'Fragment' with article stripped."""
        catalogs = {
            "items.json": [
                _make_catalog_entry("item-fragment", "The Fragment", "item"),
            ],
        }
        entity = _make_discovery("The Fragment", "location", proposed_id="loc-fragment")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is not None
        assert conflict["id"] == "item-fragment"

    def test_case_insensitive(self):
        """Name comparison is case-insensitive."""
        catalogs = {
            "characters.json": [
                _make_catalog_entry("char-shaman", "The Shaman", "character"),
            ],
        }
        entity = _make_discovery("the shaman", "location", proposed_id="loc-shaman")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is not None

    def test_alias_match(self):
        """Match against existing entity aliases."""
        catalogs = {
            "characters.json": [
                _make_catalog_entry("char-elder", "Village Elder", "character",
                                    aliases=["Elder", "Old One"]),
            ],
        }
        entity = _make_discovery("Old One", "location", proposed_id="loc-old-one")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is not None
        assert conflict["id"] == "char-elder"

    def test_not_new_entity_skipped(self):
        """Entities with is_new=False should not be checked."""
        catalogs = {
            "characters.json": [
                _make_catalog_entry("char-fenouille", "Fenouille", "character"),
            ],
        }
        entity = _make_discovery("Fenouille", "location", is_new=False)
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is None

    def test_no_conflict_when_name_absent(self):
        """No conflict when the name doesn't exist anywhere."""
        catalogs = {
            "characters.json": [
                _make_catalog_entry("char-shaman", "Shaman", "character"),
            ],
        }
        entity = _make_discovery("Fenouille", "location", proposed_id="loc-fenouille")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is None

    def test_empty_catalogs(self):
        """No crash on empty catalogs."""
        catalogs = {"characters.json": [], "locations.json": []}
        entity = _make_discovery("Fenouille", "location")
        conflict = _find_cross_catalog_type_conflict(entity, catalogs)
        assert conflict is None
