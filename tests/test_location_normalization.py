"""Tests for _normalize_entity_location (#322)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _normalize_entity_location


def _make_loc(loc_id, name, aliases=None):
    """Build a minimal location entity."""
    e = {"id": loc_id, "name": name, "type": "location"}
    if aliases:
        e["stable_attributes"] = {"aliases": {"value": aliases}}
    return e


def _make_catalogs(locations):
    return {"locations.json": locations, "characters.json": []}


class TestNormalizeEntityLocation:

    def test_exact_name_match(self):
        catalogs = _make_catalogs([_make_loc("loc-longhouse", "Longhouse")])
        entity = {"volatile_state": {"location": "Longhouse"}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-longhouse"

    def test_case_insensitive_match(self):
        catalogs = _make_catalogs([_make_loc("loc-longhouse", "Longhouse")])
        entity = {"volatile_state": {"location": "longhouse"}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-longhouse"

    def test_alias_match(self):
        catalogs = _make_catalogs([
            _make_loc("loc-council-fire", "the council fire",
                       aliases=["council fire", "fire pit"])
        ])
        entity = {"volatile_state": {"location": "fire pit"}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-council-fire"

    def test_no_match_preserves_original(self):
        catalogs = _make_catalogs([_make_loc("loc-longhouse", "Longhouse")])
        entity = {"volatile_state": {"location": "the dark forest"}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "the dark forest"

    def test_already_loc_id_unchanged(self):
        catalogs = _make_catalogs([_make_loc("loc-longhouse", "Longhouse")])
        entity = {"volatile_state": {"location": "loc-longhouse"}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-longhouse"

    def test_no_volatile_state(self):
        catalogs = _make_catalogs([_make_loc("loc-x", "X")])
        entity = {"id": "char-a", "name": "A"}
        _normalize_entity_location(entity, catalogs)
        assert "volatile_state" not in entity

    def test_no_location_field(self):
        catalogs = _make_catalogs([_make_loc("loc-x", "X")])
        entity = {"volatile_state": {"condition": "healthy"}}
        _normalize_entity_location(entity, catalogs)
        assert "location" not in entity["volatile_state"]

    def test_empty_location_string(self):
        catalogs = _make_catalogs([_make_loc("loc-x", "X")])
        entity = {"volatile_state": {"location": ""}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == ""

    def test_whitespace_trimmed(self):
        catalogs = _make_catalogs([_make_loc("loc-camp", "the camp")])
        entity = {"volatile_state": {"location": "  the camp  "}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-camp"

    def test_alias_as_string_not_list(self):
        catalogs = _make_catalogs([
            _make_loc("loc-river", "the river", aliases="River")
        ])
        entity = {"volatile_state": {"location": "river"}}
        # aliases stored as plain string (not list) — handle alias format
        loc = catalogs["locations.json"][0]
        loc["stable_attributes"]["aliases"] = {"value": "River"}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-river"

    def test_empty_catalogs(self):
        entity = {"volatile_state": {"location": "somewhere"}}
        _normalize_entity_location(entity, {"locations.json": []})
        assert entity["volatile_state"]["location"] == "somewhere"

    def test_multiple_locations_first_match_wins(self):
        catalogs = _make_catalogs([
            _make_loc("loc-a", "The Hall"),
            _make_loc("loc-b", "The Hall"),
        ])
        entity = {"volatile_state": {"location": "The Hall"}}
        _normalize_entity_location(entity, catalogs)
        assert entity["volatile_state"]["location"] == "loc-a"
