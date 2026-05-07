"""Tests for alias cross-reference guard (#302)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _filter_pc_aliases, _collect_known_entity_names
from catalog_merger import merge_entity, _filter_entity_aliases


def test_alias_rejected_when_matching_existing_entity():
    """Alias 'Maelis' rejected when catalog has an entity named 'Maelis'."""
    known = {"maelis", "chief thorne", "rune"}
    result = _filter_pc_aliases(["Maelis", "Fenouille"], known)
    assert "Maelis" not in result
    assert "Fenouille" in result


def test_alias_kept_when_no_conflict():
    """Alias 'Fenouille' kept when no entity is named 'Fenouille'."""
    known = {"maelis", "chief thorne"}
    result = _filter_pc_aliases(["Fenouille"], known)
    assert result == ["Fenouille"]


def test_entity_own_name_not_rejected():
    """An entity named 'Kael' with alias 'Kael' isn't self-blocked."""
    known = {"kael", "maelis"}
    # _filter_entity_aliases excludes the entity's own name
    result = _filter_entity_aliases(["Kael", "Shadow"], "Kael", known)
    assert "Kael" in result
    assert "Shadow" in result


def test_case_insensitive_matching():
    """'maelis' matches entity named 'Maelis' (case-insensitive)."""
    known = {"maelis"}
    result = _filter_pc_aliases(["maelis", "MAELIS", "Maelis"], known)
    assert result == []


def test_non_pc_alias_also_filtered():
    """Non-PC entity alias blocked when matching another entity name."""
    known = {"rune", "lyrawyn", "thorne"}
    # Entity "Lyrawyn" tries to have alias "Rune" — but "Rune" is another entity
    result = _filter_entity_aliases(["Rune", "Sparklewind"], "Lyrawyn", known)
    assert "Rune" not in result
    assert "Sparklewind" in result


def test_collect_known_entity_names():
    """_collect_known_entity_names builds correct set from catalogs."""
    catalogs = {
        "characters.json": [
            {"id": "char-player", "name": "Kael"},
            {"id": "char-maelis", "name": "Maelis"},
            {"id": "char-rune", "name": "Rune"},
        ],
        "locations.json": [
            {"id": "loc-tavern", "name": "The Rusty Tankard"},
        ],
    }
    names = _collect_known_entity_names(catalogs)
    assert "kael" in names
    assert "maelis" in names
    assert "rune" in names
    assert "the rusty tankard" in names


# --- Integration tests: merge_entity with alias cross-reference ---


def test_merge_entity_strips_conflicting_alias_on_update():
    """merge_entity() strips aliases conflicting with other entity names during update."""
    catalogs = {
        "characters.json": [
            {"id": "char-player", "name": "Kael", "type": "character",
             "identity": "The player character", "first_seen_turn": "turn-001",
             "stable_attributes": {"aliases": {"value": ["Shadow"]}}},
            {"id": "char-maelis", "name": "Maelis", "type": "character",
             "identity": "An NPC mage", "first_seen_turn": "turn-001"},
        ],
    }
    # Update char-player with an alias that matches "Maelis"
    update = {
        "id": "char-player", "type": "character",
        "stable_attributes": {"aliases": {"value": ["Shadow", "Maelis", "Nightblade"]}},
        "last_updated_turn": "turn-050",
    }
    merge_entity(catalogs, update)
    pc = catalogs["characters.json"][0]
    alias_list = pc["stable_attributes"]["aliases"]["value"]
    assert "Maelis" not in alias_list
    assert "Shadow" in alias_list
    assert "Nightblade" in alias_list


def test_merge_entity_keeps_non_conflicting_alias_on_update():
    """merge_entity() keeps aliases that don't conflict with any entity name."""
    catalogs = {
        "characters.json": [
            {"id": "char-npc", "name": "Thorne", "type": "character",
             "identity": "A guard captain", "first_seen_turn": "turn-010",
             "stable_attributes": {}},
        ],
    }
    update = {
        "id": "char-npc", "type": "character",
        "stable_attributes": {"aliases": {"value": ["Chief", "Captain"]}},
        "last_updated_turn": "turn-020",
    }
    merge_entity(catalogs, update)
    npc = catalogs["characters.json"][0]
    alias_list = npc["stable_attributes"]["aliases"]["value"]
    assert "Chief" in alias_list
    assert "Captain" in alias_list


def test_merge_entity_filters_aliases_on_new_entity():
    """merge_entity() filters conflicting aliases when appending a new entity."""
    catalogs = {
        "characters.json": [
            {"id": "char-maelis", "name": "Maelis", "type": "character",
             "identity": "An NPC", "first_seen_turn": "turn-001"},
        ],
    }
    new_entity = {
        "id": "char-lyra", "name": "Lyra", "type": "character",
        "identity": "A bard", "first_seen_turn": "turn-020",
        "stable_attributes": {"aliases": {"value": ["Songbird", "Maelis"]}},
    }
    merge_entity(catalogs, new_entity)
    lyra = next(e for e in catalogs["characters.json"] if e["id"] == "char-lyra")
    alias_list = lyra["stable_attributes"]["aliases"]["value"]
    assert "Maelis" not in alias_list
    assert "Songbird" in alias_list
