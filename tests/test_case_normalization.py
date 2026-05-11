"""Tests for case-insensitive attribute key normalization (#336)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import merge_entity


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


def test_stable_attributes_case_normalized():
    """LLM returning 'Availability' should merge with existing 'availability'."""
    catalogs = {
        "items.json": [
            _make_entity("item-metal", "Metal", stable_attributes={
                "availability": {"value": "rare", "source_turn": "turn-010"},
            }),
        ],
        "characters.json": [],
        "locations.json": [],
        "factions.json": [],
    }
    update = _make_entity("item-metal", "Metal", turn="turn-020", stable_attributes={
        "Availability": {"value": "common", "source_turn": "turn-020"},
    })
    merge_entity(catalogs, update)
    entity = catalogs["items.json"][0]
    sa = entity["stable_attributes"]
    availability_keys = [k for k in sa if k.lower() == "availability"]
    assert len(availability_keys) == 1
    assert "availability" in sa
    assert sa["availability"]["value"] == "common"


def test_volatile_state_case_normalized():
    """Volatile state keys should also be normalized to lowercase."""
    catalogs = {
        "items.json": [
            _make_entity("item-bowl", "Bowl", volatile_state={
                "condition": "good",
            }),
        ],
        "characters.json": [],
        "locations.json": [],
        "factions.json": [],
    }
    update = _make_entity("item-bowl", "Bowl", turn="turn-020", volatile_state={
        "Condition": "cracked",
    })
    merge_entity(catalogs, update)
    entity = catalogs["items.json"][0]
    vs = entity["volatile_state"]
    condition_keys = [k for k in vs if k.lower() == "condition"]
    assert len(condition_keys) == 1
    assert "condition" in vs
    assert vs["condition"] == "cracked"


def test_multiple_case_variants_collapsed():
    """Multiple casing variants of the same key should collapse to one."""
    catalogs = {
        "items.json": [
            _make_entity("item-ore", "Ore", stable_attributes={
                "Availability": {"value": "old1"},
                "availability": {"value": "old2"},
                "AVAILABILITY": {"value": "old3"},
            }),
        ],
        "characters.json": [],
        "locations.json": [],
        "factions.json": [],
    }
    update = _make_entity("item-ore", "Ore", turn="turn-020", stable_attributes={
        "availability": {"value": "new"},
    })
    merge_entity(catalogs, update)
    entity = catalogs["items.json"][0]
    sa = entity["stable_attributes"]
    availability_keys = [k for k in sa if k.lower() == "availability"]
    assert len(availability_keys) == 1
    assert sa["availability"]["value"] == "new"
