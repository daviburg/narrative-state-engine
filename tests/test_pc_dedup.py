"""Tests for player character duplicate detection and merge."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _check_pc_duplicate, _merge_into_pc
from catalog_merger import find_entity_by_id


def test_self_intro_entity_detected_as_pc():
    """Entity with self-introduction identity should be flagged as PC duplicate."""
    entity = {
        "id": "char-fenouille-moonwind",
        "name": "Fenouille Moonwind",
        "type": "character",
        "identity": "A character who introduces themselves as Fenouille Moonwind",
    }
    result = _check_pc_duplicate(entity, {})
    assert result == "char-player"


def test_pointing_to_self_detected_as_pc():
    """Entity that 'points to self' should be flagged as PC duplicate."""
    entity = {
        "id": "char-some-name",
        "name": "Some Name",
        "type": "character",
        "identity": "A figure pointing to self and stating their name",
    }
    result = _check_pc_duplicate(entity, {})
    assert result == "char-player"


def test_non_pc_entity_not_flagged():
    """Normal NPC entity should not be flagged as PC."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "identity": "The village elder who leads the tribe",
    }
    result = _check_pc_duplicate(entity, {})
    assert result is None


def test_pc_entity_not_flagged():
    """char-player itself should not be flagged."""
    entity = {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "identity": "The player character",
    }
    result = _check_pc_duplicate(entity, {})
    assert result is None


def test_merge_into_pc_adds_alias():
    """Merging a duplicate should add its name as an alias on char-player."""
    catalogs = {
        "characters.json": [
            {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-050",
                "stable_attributes": {},
            }
        ]
    }
    duplicate = {
        "id": "char-fenouille-moonwind",
        "name": "Fenouille Moonwind",
        "type": "character",
        "identity": "A character who introduces themselves as Fenouille Moonwind",
        "last_updated_turn": "turn-059",
        "stable_attributes": {},
    }
    _merge_into_pc(catalogs, duplicate)

    pc = catalogs["characters.json"][0]
    assert pc["name"] == "Fenouille Moonwind"
    aliases = pc["stable_attributes"]["aliases"]["value"]
    assert "Fenouille Moonwind" in aliases
    assert pc["last_updated_turn"] == "turn-059"


def test_merge_into_pc_preserves_existing_name():
    """If PC already has a proper name, keep it and add duplicate as alias."""
    catalogs = {
        "characters.json": [
            {
                "id": "char-player",
                "name": "Fenouille Moonwind",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-059",
                "stable_attributes": {
                    "aliases": {
                        "value": ["Player Character"],
                        "inference": False,
                        "confidence": 1.0,
                        "source_turn": "turn-059",
                    }
                },
            }
        ]
    }
    duplicate = {
        "id": "char-fen",
        "name": "Fen",
        "type": "character",
        "identity": "A character who introduces themselves",
        "last_updated_turn": "turn-060",
        "stable_attributes": {},
    }
    _merge_into_pc(catalogs, duplicate)

    pc = catalogs["characters.json"][0]
    # Name stays as the already-proper name
    assert pc["name"] == "Fenouille Moonwind"
    aliases = pc["stable_attributes"]["aliases"]["value"]
    assert "Fen" in aliases
    assert "Player Character" in aliases


def test_merge_into_pc_adds_new_attributes():
    """Merging should bring over new stable attributes from the duplicate."""
    catalogs = {
        "characters.json": [
            {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-050",
                "stable_attributes": {
                    "race": {"value": "Elf", "inference": True,
                             "confidence": 0.95, "source_turn": "turn-019"},
                },
            }
        ]
    }
    duplicate = {
        "id": "char-fenouille-moonwind",
        "name": "Fenouille Moonwind",
        "type": "character",
        "identity": "A character who introduces themselves",
        "last_updated_turn": "turn-059",
        "stable_attributes": {
            "appearance": {"value": "Tall with silver hair", "inference": False,
                           "confidence": 1.0, "source_turn": "turn-059"},
        },
    }
    _merge_into_pc(catalogs, duplicate)

    pc = catalogs["characters.json"][0]
    assert "appearance" in pc["stable_attributes"]
    assert pc["stable_attributes"]["appearance"]["value"] == "Tall with silver hair"
    # Existing race attribute preserved
    assert pc["stable_attributes"]["race"]["value"] == "Elf"
