"""Tests for fuzzy dedup matching in _dedup_catalogs()."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _dedup_catalogs


def _make_entity(id_, name, turn="turn-001"):
    return {
        "id": id_,
        "name": name,
        "first_seen_turn": turn,
        "attributes": {},
        "relationships": [],
    }


def _ids(catalogs, filename):
    return {e["id"] for e in catalogs[filename]}


def test_substring_merge_spear():
    catalogs = {
        "items.json": [
            _make_entity("item-crude-woodhafted-spear", "Crude wood-hafted spear", "turn-003"),
            _make_entity("item-crude-spear", "Crude spear", "turn-007"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 1
    assert len(catalogs["items.json"]) == 1
    assert "item-crude-spear" in merge_map
    assert merge_map["item-crude-spear"] == "item-crude-woodhafted-spear"


def test_substring_merge_bowl_group():
    catalogs = {
        "items.json": [
            _make_entity("item-bowl", "Bowl", "turn-002"),
            _make_entity("item-steaming-bowl", "Steaming bowl", "turn-005"),
            _make_entity("item-steaming-broth-bowl", "Steaming broth bowl", "turn-008"),
            _make_entity("item-warm-wooden-bowl", "Warm wooden bowl", "turn-010"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    # All should merge into the earliest entry
    assert len(catalogs["items.json"]) == 1
    survivor = catalogs["items.json"][0]
    assert survivor["id"] == "item-bowl"
    assert count == 3


def test_substring_merge_moonpetal():
    catalogs = {
        "items.json": [
            _make_entity("item-dried-moonpetal", "Dried moonpetal", "turn-004"),
            _make_entity("item-moonpetal", "Moonpetal", "turn-009"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 1
    assert len(catalogs["items.json"]) == 1
    assert "item-moonpetal" in merge_map


def test_no_cross_catalog_merge():
    catalogs = {
        "characters.json": [
            _make_entity("char-elder", "Elder", "turn-001"),
        ],
        "locations.json": [
            _make_entity("loc-forest", "Forest", "turn-001"),
        ],
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 0
    assert len(catalogs["characters.json"]) == 1
    assert len(catalogs["locations.json"]) == 1


def test_id_stem_merge():
    catalogs = {
        "items.json": [
            _make_entity("item-crude-spear", "Pointy stick", "turn-003"),
            _make_entity("item-crude-spear-broken", "Broken pointy stick", "turn-010"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 1
    assert len(catalogs["items.json"]) == 1


if __name__ == "__main__":
    test_substring_merge_spear()
    test_substring_merge_bowl_group()
    test_substring_merge_moonpetal()
    test_no_cross_catalog_merge()
    test_id_stem_merge()
    print("All tests passed!")
