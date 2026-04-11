"""Tests for LLM output coercion before entity validation."""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _coerce_entity_fields


def test_unwraps_single_element_array_name():
    entity = {"name": ["A younger woman"], "type": "character"}
    result = _coerce_entity_fields(entity)
    assert result["name"] == "A younger woman"


def test_joins_multi_element_array_name():
    entity = {"name": ["Kael", "Player Character"], "type": "character"}
    result = _coerce_entity_fields(entity)
    assert result["name"] == "Kael, Player Character"


def test_stringifies_array_attribute():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"uses": ["healing", "cooking"]},
    }
    result = _coerce_entity_fields(entity)
    assert result["attributes"]["uses"] == "healing, cooking"


def test_stringifies_dict_attribute():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"relationship": {"target_id": "char-001", "type": "owned_by"}},
    }
    result = _coerce_entity_fields(entity)
    assert isinstance(result["attributes"]["relationship"], str)


def test_wraps_single_relationship_dict():
    entity = {
        "name": "Kael",
        "type": "character",
        "relationships": {"target_id": "loc-camp", "type": "located_at"},
    }
    result = _coerce_entity_fields(entity)
    assert isinstance(result["relationships"], list)
    assert len(result["relationships"]) == 1


def test_leaves_valid_entity_unchanged():
    entity = {
        "name": "Kael",
        "type": "character",
        "description": "The player character.",
        "attributes": {"role": "protagonist"},
    }
    result = _coerce_entity_fields(entity)
    assert result["name"] == "Kael"
    assert result["attributes"]["role"] == "protagonist"


def test_logs_coercions_to_stderr():
    entity = {"name": ["Test"], "type": "character"}
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        _coerce_entity_fields(entity)
    finally:
        sys.stderr = old
    assert "COERCE" in buf.getvalue()


def test_returns_none_for_non_dict():
    assert _coerce_entity_fields(["not", "a", "dict"]) is None
    assert _coerce_entity_fields("just a string") is None
    assert _coerce_entity_fields(42) is None


def test_empty_array_becomes_empty_string():
    entity = {"name": [], "type": "character"}
    result = _coerce_entity_fields(entity)
    assert result["name"] == ""


def test_coerces_numeric_attribute_to_string():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"weight": 5, "magical": True, "value": 3.5},
    }
    result = _coerce_entity_fields(entity)
    assert result["attributes"]["weight"] == "5"
    assert result["attributes"]["magical"] == "True"
    assert result["attributes"]["value"] == "3.5"


def test_splits_comma_separated_proposed_id():
    entity = {
        "name": "two figures",
        "type": "faction",
        "proposed_id": "char-broad-figure,char-companion-of-broad-figure,faction-two-figures",
    }
    result = _coerce_entity_fields(entity)
    assert result["proposed_id"] == "faction-two-figures"


def test_splits_comma_id_takes_first_when_no_type_match():
    entity = {
        "name": "something",
        "type": "creature",
        "proposed_id": "char-a,loc-b,faction-c",
    }
    result = _coerce_entity_fields(entity)
    # No creature- prefix match, takes first
    assert result["proposed_id"] == "char-a"


def test_leaves_non_comma_proposed_id_unchanged():
    entity = {
        "name": "elder",
        "type": "character",
        "proposed_id": "char-elder",
    }
    result = _coerce_entity_fields(entity)
    assert result["proposed_id"] == "char-elder"


def test_coerces_none_attribute_to_empty_string():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"notes": None},
    }
    result = _coerce_entity_fields(entity)
    assert result["attributes"]["notes"] == ""
