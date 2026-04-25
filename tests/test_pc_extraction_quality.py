"""Tests for PC alias validation and volatile_state pruning (#214)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _filter_pc_aliases,
    _prune_pc_volatile_state,
    _PC_ALIAS_MAX_COUNT,
    _PC_VOLATILE_STATE_MAX_KEYS,
    _PC_VOLATILE_STATE_CORE_KEYS,
    _sanitize_pc_catalog_entry,
)


# ---------------------------------------------------------------------------
# Alias validation tests
# ---------------------------------------------------------------------------


def test_pc_alias_common_word_rejected():
    """Common English words should be rejected as PC aliases (#214)."""
    aliases = ["Kael", "Broken", "Pattern", "Precision", "Disruption"]
    result = _filter_pc_aliases(aliases)
    assert "Kael" in result
    for bad in ("Broken", "Pattern", "Precision", "Disruption"):
        assert bad not in result


def test_pc_alias_meta_label_rejected():
    """Meta-labels like 'player character' should be rejected (#186)."""
    aliases = ["Kael", "player character", "protagonist", "the hero"]
    result = _filter_pc_aliases(aliases)
    assert result == ["Kael"]


def test_pc_alias_too_short_rejected():
    """Aliases shorter than minimum length are rejected (#214)."""
    aliases = ["Kael", "Al", "X", "Bo"]
    result = _filter_pc_aliases(aliases)
    assert result == ["Kael"]


def test_pc_alias_max_count_enforced():
    """Aliases beyond max count are trimmed, keeping most recent (#214)."""
    aliases = [f"Alias{i}" for i in range(_PC_ALIAS_MAX_COUNT + 5)]
    result = _filter_pc_aliases(aliases)
    assert len(result) == _PC_ALIAS_MAX_COUNT
    # Should keep the last N (most recent)
    assert result[-1] == f"Alias{_PC_ALIAS_MAX_COUNT + 4}"
    assert result[0] == f"Alias5"


def test_pc_alias_valid_names_preserved():
    """Valid character names should pass through unchanged."""
    aliases = ["Kael", "Storm Runner", "Nightwalker"]
    result = _filter_pc_aliases(aliases)
    assert result == aliases


def test_pc_alias_case_insensitive_blocklist():
    """Blocklist matching should be case-insensitive."""
    aliases = ["BROKEN", "broken", "Broken", "Kael"]
    result = _filter_pc_aliases(aliases)
    assert result == ["Kael"]


def test_pc_alias_empty_and_whitespace_skipped():
    """Empty strings and whitespace-only aliases are filtered out."""
    aliases = ["Kael", "", "  ", None, "Storm"]
    result = _filter_pc_aliases(aliases)
    assert result == ["Kael", "Storm"]


# ---------------------------------------------------------------------------
# Volatile state pruning tests
# ---------------------------------------------------------------------------


def test_pc_volatile_state_pruned():
    """PC volatile_state should be pruned to max keys (#214)."""
    vs = {f"key_{i}": f"value_{i}" for i in range(175)}
    # Add core keys
    for k in _PC_VOLATILE_STATE_CORE_KEYS:
        vs[k] = f"core_{k}"
    entity = {"id": "char-player", "volatile_state": vs}

    _prune_pc_volatile_state(entity)

    assert len(entity["volatile_state"]) == _PC_VOLATILE_STATE_MAX_KEYS


def test_pc_volatile_state_core_keys_preserved():
    """Core volatile_state keys must survive pruning (#214)."""
    vs = {f"extra_key_{i}": f"val_{i}" for i in range(50)}
    for k in _PC_VOLATILE_STATE_CORE_KEYS:
        vs[k] = f"core_{k}"
    entity = {"id": "char-player", "volatile_state": vs}

    _prune_pc_volatile_state(entity)

    for k in _PC_VOLATILE_STATE_CORE_KEYS:
        assert k in entity["volatile_state"], f"Core key '{k}' was lost"
        assert entity["volatile_state"][k] == f"core_{k}"


def test_pc_volatile_state_under_limit_unchanged():
    """volatile_state at or under the limit should not be modified."""
    vs = {"condition": "healthy", "location": "town", "quest_status": "active"}
    entity = {"id": "char-player", "volatile_state": dict(vs)}

    _prune_pc_volatile_state(entity)

    assert entity["volatile_state"] == vs


def test_pc_volatile_state_none_or_missing():
    """Entities without volatile_state or with non-dict values are skipped."""
    entity_no_vs = {"id": "char-player"}
    _prune_pc_volatile_state(entity_no_vs)
    assert "volatile_state" not in entity_no_vs

    entity_none = {"id": "char-player", "volatile_state": None}
    _prune_pc_volatile_state(entity_none)
    assert entity_none["volatile_state"] is None


# ---------------------------------------------------------------------------
# Integration: _sanitize_pc_catalog_entry covers both guards
# ---------------------------------------------------------------------------


def test_sanitize_filters_aliases_and_prunes_volatile():
    """_sanitize_pc_catalog_entry applies both alias filter and VS pruning (#214)."""
    pc = {
        "id": "char-player",
        "stable_attributes": {
            "aliases": {"value": ["Kael", "Broken", "Pattern"]},
            "race": {"value": "human"},
        },
        "volatile_state": {f"k{i}": f"v{i}" for i in range(50)},
    }
    catalogs = {"characters.json": [pc]}

    _sanitize_pc_catalog_entry(catalogs)

    aliases = pc["stable_attributes"]["aliases"]["value"]
    assert "Kael" in aliases
    assert "Broken" not in aliases
    assert "Pattern" not in aliases
    assert len(pc["volatile_state"]) == _PC_VOLATILE_STATE_MAX_KEYS
