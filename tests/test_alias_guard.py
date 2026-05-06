"""Tests for alias cross-reference guard (#302)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _filter_pc_aliases, _filter_entity_aliases, _collect_known_entity_names


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
