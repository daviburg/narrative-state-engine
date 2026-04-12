"""Tests for LLM output coercion before entity validation."""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _coerce_entity_fields,
    _filter_concept_prefix_from_items,
    _format_prior_entity_context,
    _collect_existing_relationships,
    format_relationship_prompt,
)


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
    # V1→V2 coercion: attributes → stable_attributes (array was first stringified)
    assert result["stable_attributes"]["uses"]["value"] == "healing, cooking"


def test_stringifies_dict_attribute():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"relationship": {"target_id": "char-001", "type": "owned_by"}},
    }
    result = _coerce_entity_fields(entity)
    # V1→V2 coercion: dict was stringified then moved to stable_attributes
    assert isinstance(result["stable_attributes"]["relationship"]["value"], str)


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
        "identity": "The player character.",
        "stable_attributes": {
            "role": {"value": "protagonist", "inference": False, "confidence": 1.0, "source_turn": "turn-001"},
        },
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-001",
    }
    result = _coerce_entity_fields(entity)
    assert result["name"] == "Kael"
    assert result["stable_attributes"]["role"]["value"] == "protagonist"


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
    # V1→V2 coercion: numeric values were stringified then moved to stable_attributes
    assert result["stable_attributes"]["weight"]["value"] == "5"
    assert result["stable_attributes"]["magical"]["value"] == "True"
    assert result["stable_attributes"]["value"]["value"] == "3.5"


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


def test_splits_comma_separated_id():
    entity = {
        "name": "two figures",
        "type": "faction",
        "id": "char-broad-figure,char-companion-of-broad-figure,faction-two-figures",
    }
    result = _coerce_entity_fields(entity)
    assert result["id"] == "faction-two-figures"


def test_splits_comma_separated_id_takes_first_when_no_type_match():
    entity = {
        "name": "something",
        "type": "creature",
        "id": "char-a,loc-b,faction-c",
    }
    result = _coerce_entity_fields(entity)
    # No creature- prefix match, takes first
    assert result["id"] == "char-a"


def test_leaves_non_comma_id_unchanged():
    entity = {
        "name": "elder",
        "type": "character",
        "id": "char-elder",
    }
    result = _coerce_entity_fields(entity)
    assert result["id"] == "char-elder"


def test_coerces_none_attribute_to_empty_string():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"notes": None},
    }
    result = _coerce_entity_fields(entity)
    # V1→V2 coercion: None was stringified to "" then moved to stable_attributes
    assert result["stable_attributes"]["notes"]["value"] == ""


# --- V1→V2 coercion tests ---

def test_v1_description_fallback_coercion():
    """LLM returning 'description' but not 'identity' gets mapped to identity."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "description": "An elderly authority figure in the tribal community.",
        "first_seen_turn": "turn-019",
        "last_updated_turn": "turn-019",
    }
    result = _coerce_entity_fields(entity)
    assert "identity" in result
    assert result["identity"] == "An elderly authority figure in the tribal community."
    assert "description" not in result
    assert result["current_status"] == ""


def test_v1_description_not_overwritten_when_identity_present():
    """If both description and identity exist, identity takes precedence."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "description": "Old text.",
        "identity": "New V2 identity text.",
        "first_seen_turn": "turn-019",
        "last_updated_turn": "turn-019",
    }
    result = _coerce_entity_fields(entity)
    assert result["identity"] == "New V2 identity text."
    # description is untouched when identity already present
    assert result.get("description") == "Old text."


def test_v1_flat_attributes_coerced_to_stable_volatile():
    """LLM returning flat 'attributes' should be classified into stable/volatile."""
    entity = {
        "id": "char-kael",
        "name": "Kael",
        "type": "character",
        "identity": "A wandering warrior.",
        "attributes": {
            "race": "human",
            "class": "fighter [inference]",
            "condition": "wounded",
            "equipment": "sword, shield",
            "location": "village square",
            "appearance": "tall with dark hair",
        },
        "last_updated_turn": "turn-025",
        "first_seen_turn": "turn-010",
    }
    result = _coerce_entity_fields(entity)
    assert "stable_attributes" in result
    assert "volatile_state" in result
    assert "attributes" not in result

    # Stable attributes should be typed objects
    sa = result["stable_attributes"]
    assert sa["race"]["value"] == "human"
    assert sa["race"]["inference"] is False
    assert sa["race"]["confidence"] == 1.0
    assert sa["race"]["source_turn"] == "turn-025"

    # Inference marker detected from V1 suffix
    assert sa["class"]["value"] == "fighter"
    assert sa["class"]["inference"] is True
    assert sa["class"]["confidence"] == 0.7

    # Volatile state
    vs = result["volatile_state"]
    assert vs["condition"] == "wounded"
    assert vs["equipment"] == ["sword", "shield"]
    assert vs["location"] == "village square"
    assert vs["last_updated_turn"] == "turn-025"


def test_identity_status_split_in_output():
    """V2 entity with identity + current_status passes through cleanly."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "identity": "An elderly authority figure.",
        "current_status": "Speaking with the player at the fire.",
        "status_updated_turn": "turn-020",
        "first_seen_turn": "turn-019",
        "last_updated_turn": "turn-020",
    }
    result = _coerce_entity_fields(entity)
    assert result["identity"] == "An elderly authority figure."
    assert result["current_status"] == "Speaking with the player at the fire."


def test_stable_volatile_attributes_in_output():
    """V2 entity with stable_attributes + volatile_state passes through."""
    entity = {
        "id": "char-kael",
        "name": "Kael",
        "type": "character",
        "identity": "A warrior.",
        "stable_attributes": {
            "race": {"value": "human", "inference": False, "confidence": 1.0, "source_turn": "turn-001"},
        },
        "volatile_state": {
            "condition": "healthy",
            "last_updated_turn": "turn-010",
        },
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-010",
    }
    result = _coerce_entity_fields(entity)
    assert result["stable_attributes"]["race"]["value"] == "human"
    assert result["volatile_state"]["condition"] == "healthy"


def test_v1_relationship_fields_coerced_to_v2():
    """V1 relationship with 'relationship' and 'source_turn' gets V2 fields."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "identity": "An elder.",
        "relationships": [
            {
                "target_id": "char-player",
                "relationship": "mentor of",
                "type": "mentorship",
                "source_turn": "turn-019",
            }
        ],
        "first_seen_turn": "turn-019",
        "last_updated_turn": "turn-019",
    }
    result = _coerce_entity_fields(entity)
    rel = result["relationships"][0]
    assert "current_relationship" in rel
    assert rel["current_relationship"] == "mentor of"
    assert "relationship" not in rel
    assert rel["first_seen_turn"] == "turn-019"
    assert rel["last_updated_turn"] == "turn-019"


# --- Concept prefix filtering tests ---

def test_filters_concept_prefix_from_items():
    """Entity with concept- prefix and type=item should be filtered."""
    entity = {"name": "spirit world", "type": "item", "proposed_id": "concept-spirit-world"}
    assert _filter_concept_prefix_from_items(entity) is False


def test_filters_concept_prefix_from_items_by_id():
    """Also check by id field."""
    entity = {"name": "spirit world", "type": "item", "id": "concept-spirit-world"}
    assert _filter_concept_prefix_from_items(entity) is False


def test_keeps_concept_prefix_with_concept_type():
    """Concept-prefix with type=concept should be kept."""
    entity = {"name": "spirit world", "type": "concept", "id": "concept-spirit-world"}
    assert _filter_concept_prefix_from_items(entity) is True


def test_keeps_item_prefix_with_item_type():
    """Normal item should be kept."""
    entity = {"name": "healing herb", "type": "item", "id": "item-healing-herb"}
    assert _filter_concept_prefix_from_items(entity) is True


def test_concept_filter_logs_to_stderr():
    """Filtering should log to stderr with FILTER prefix."""
    entity = {"name": "spirit world", "type": "item", "proposed_id": "concept-spirit-world"}
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        _filter_concept_prefix_from_items(entity)
    finally:
        sys.stderr = old
    assert "FILTER" in buf.getvalue()
    assert "concept-spirit-world" in buf.getvalue()


# --- Prior entity context assembly tests ---

def test_prior_entity_context_assembly_v2():
    """Prior entity data is loaded and formatted for template injection."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "identity": "An elderly authority figure.",
        "current_status": "At the council fire.",
        "status_updated_turn": "turn-019",
        "stable_attributes": {
            "role": {"value": "tribal leader", "inference": True, "confidence": 0.8, "source_turn": "turn-019"}
        },
        "volatile_state": {"condition": "alert", "last_updated_turn": "turn-019"},
        "first_seen_turn": "turn-019",
        "last_updated_turn": "turn-019",
    }
    result = _format_prior_entity_context(entity)
    parsed = json.loads(result)
    assert parsed["identity"] == "An elderly authority figure."
    assert parsed["current_status"] == "At the council fire."
    assert "stable_attributes" in parsed
    assert "volatile_state" in parsed


def test_prior_entity_context_assembly_v1_fallback():
    """V1 entity falls back to description/attributes in context."""
    entity = {
        "id": "char-elder",
        "name": "The Elder",
        "type": "character",
        "description": "An old leader.",
        "attributes": {"role": "leader"},
        "first_seen_turn": "turn-019",
        "last_updated_turn": "turn-019",
    }
    result = _format_prior_entity_context(entity)
    parsed = json.loads(result)
    assert parsed["description"] == "An old leader."
    assert parsed["attributes"]["role"] == "leader"
    assert "identity" not in parsed


def test_prior_entity_context_empty():
    """None entry returns empty JSON object."""
    assert _format_prior_entity_context(None) == "{}"


# --- Relationship context assembly tests ---

def test_relationship_context_assembly():
    """Existing relationships are collected and injected into prompt."""
    catalogs = {
        "characters.json": [
            {
                "id": "char-elder",
                "name": "The Elder",
                "type": "character",
                "identity": "An elder.",
                "first_seen_turn": "turn-019",
                "relationships": [
                    {
                        "target_id": "char-player",
                        "current_relationship": "mentor of",
                        "type": "mentorship",
                        "status": "active",
                        "first_seen_turn": "turn-019",
                    }
                ],
            },
            {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player.",
                "first_seen_turn": "turn-001",
                "relationships": [],
            },
        ],
    }
    result = _collect_existing_relationships(catalogs, ["char-elder", "char-player"])
    parsed = json.loads(result)
    assert "char-elder" in parsed
    assert parsed["char-elder"][0]["target_id"] == "char-player"


def test_relationship_context_empty():
    """No existing relationships returns placeholder text."""
    catalogs = {
        "characters.json": [
            {
                "id": "char-player",
                "name": "Player",
                "type": "character",
                "identity": "The player.",
                "first_seen_turn": "turn-001",
            },
        ],
    }
    result = _collect_existing_relationships(catalogs, ["char-player"])
    assert result == "(none — no existing relationships)"


def test_relationship_prompt_includes_existing():
    """format_relationship_prompt includes existing relationships section."""
    turn = {"turn_id": "turn-020", "speaker": "dm", "text": "The elder speaks."}
    entities = [
        {"id": "char-elder", "name": "The Elder", "type": "character"},
        {"id": "char-player", "name": "Player", "type": "character"},
    ]
    existing = '{"char-elder": [{"target_id": "char-player", "current_relationship": "mentor of"}]}'
    prompt = format_relationship_prompt(turn, entities, existing)
    assert "Existing relationships" in prompt
    assert "mentor of" in prompt
