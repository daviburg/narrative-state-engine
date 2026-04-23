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
    # LLM coercion: attributes → stable_attributes (array was first stringified)
    assert result["stable_attributes"]["uses"]["value"] == "healing, cooking"


def test_stringifies_dict_attribute():
    entity = {
        "name": "Herb",
        "type": "item",
        "attributes": {"relationship": {"target_id": "char-001", "type": "owned_by"}},
    }
    result = _coerce_entity_fields(entity)
    # LLM coercion: dict was stringified then moved to stable_attributes
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
    # LLM coercion: numeric values were stringified then moved to stable_attributes
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
    # LLM coercion: None was stringified to "" then moved to stable_attributes
    assert result["stable_attributes"]["notes"]["value"] == ""


# --- LLM field normalization tests ---

def test_description_fallback_coercion():
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
    assert "description" not in result  # stripped for V2 schema compliance
    assert result["current_status"] == ""


def test_description_not_overwritten_when_identity_present():
    """If both description and identity exist, identity stays and description is stripped."""
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
    # description is stripped to avoid V2 schema validation failure
    assert "description" not in result


def test_flat_attributes_coerced_to_stable_volatile():
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

    # Inference marker detected from suffix
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


def test_relationship_fields_coerced_to_v2():
    """Relationship with 'relationship' and 'source_turn' gets V2 fields."""
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


# --- Run 11 coercion regression tests (#196) ---

def test_run11_abilities_and_traits_mapped_to_stable_attributes():
    entity = {
        "name": "Kael",
        "type": "character",
        "last_updated_turn": "turn-011",
        "abilities_and_traits": ["swordsmanship", "leadership"],
    }
    result = _coerce_entity_fields(entity)
    assert "abilities_and_traits" not in result
    assert result["stable_attributes"]["abilities"]["value"] == ["swordsmanship", "leadership"]
    assert result["stable_attributes"]["abilities"]["inference"] is False


def test_run11_additional_items_equipped_appended_to_equipment():
    entity = {
        "name": "Kael",
        "type": "character",
        "last_updated_turn": "turn-011",
        "volatile_state": {"equipment": ["sword"], "location": "village"},
        "additional_items_equipped": ["shield", "helm"],
    }
    result = _coerce_entity_fields(entity)
    assert "additional_items_equipped" not in result
    assert "shield" in result["volatile_state"]["equipment"]
    assert "helm" in result["volatile_state"]["equipment"]
    assert "sword" in result["volatile_state"]["equipment"]


def test_run11_locations_remapped_to_volatile_state_location():
    entity = {
        "name": "Kael",
        "type": "character",
        "last_updated_turn": "turn-011",
        "locations": "the market",
    }
    result = _coerce_entity_fields(entity)
    assert "locations" not in result
    assert result["volatile_state"]["location"] == "the market"


def test_run11_stable_remap_age_gender_occupation():
    entity = {
        "name": "Kael",
        "type": "character",
        "last_updated_turn": "turn-011",
        "age": "30",
        "gender": "male",
        "occupation": "blacksmith",
    }
    result = _coerce_entity_fields(entity)
    assert "age" not in result
    assert "gender" not in result
    assert "occupation" not in result
    assert result["stable_attributes"]["age"]["value"] == "30"
    assert result["stable_attributes"]["gender"]["value"] == "male"
    assert result["stable_attributes"]["occupation"]["value"] == "blacksmith"


def test_run11_discard_keys_removed():
    entity = {
        "name": "Kael",
        "type": "character",
        "recent_activities": "training",
        "current_activities": "resting",
        "updated_turn": "turn-010",
        "history_highlights": ["won a battle"],
        "goals": "become champion",
        "background": "grew up in the north",
        "abilities_description": "very strong",
        "status_updated_turn": "turn-010",
    }
    result = _coerce_entity_fields(entity)
    for key in ("recent_activities", "current_activities", "updated_turn",
                "history_highlights", "goals", "background",
                "abilities_description"):
        assert key not in result, f"Expected '{key}' to be discarded"
    # status_updated_turn is schema-valid at top level — it must be preserved
    assert result["status_updated_turn"] == "turn-010"


def test_run11_status_updated_turn_stripped_from_volatile_state():
    """When status_updated_turn is nested in volatile_state, strip it and promote to top level."""
    entity = {
        "name": "Kael",
        "type": "character",
        "last_updated_turn": "turn-011",
        "volatile_state": {
            "location": "castle",
            "status_updated_turn": "turn-010",
            "last_updated_turn": "turn-011",
        },
    }
    result = _coerce_entity_fields(entity)
    assert "status_updated_turn" not in result["volatile_state"]
    assert result["volatile_state"]["location"] == "castle"
    # Should be promoted to top level since it was absent there
    assert result["status_updated_turn"] == "turn-010"


def test_run11_status_updated_turn_not_overwritten_if_already_top_level():
    """When status_updated_turn exists at top level and also in volatile_state, just strip the nested one."""
    entity = {
        "name": "Kael",
        "type": "character",
        "last_updated_turn": "turn-011",
        "status_updated_turn": "turn-011",
        "volatile_state": {
            "location": "castle",
            "status_updated_turn": "turn-010",
            "last_updated_turn": "turn-011",
        },
    }
    result = _coerce_entity_fields(entity)
    assert "status_updated_turn" not in result["volatile_state"]
    # Top-level value must not be overwritten
    assert result["status_updated_turn"] == "turn-011"


def test_relationship_context_empty():
    """No existing relationships returns empty string."""
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
    assert result == ""


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


# --- Tests for non-standard key coercion (#170) ---


def test_coerce_equipment_to_volatile_state():
    """Top-level 'equipment' remapped to volatile_state.equipment."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Resting.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "equipment": ["sword", "shield"],
    }
    result = _coerce_entity_fields(entity)
    assert "equipment" not in result  # removed from top level
    assert result["volatile_state"]["equipment"] == ["sword", "shield"]


def test_coerce_inventory_to_volatile_equipment():
    """Top-level 'inventory' remapped to volatile_state.equipment."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Resting.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "inventory": "sword, shield, potion",
    }
    result = _coerce_entity_fields(entity)
    assert "inventory" not in result
    assert result["volatile_state"]["equipment"] == ["sword", "shield", "potion"]


def test_coerce_location_to_volatile_state():
    """Top-level 'location' remapped to volatile_state.location."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Exploring.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "location": "The village square",
    }
    result = _coerce_entity_fields(entity)
    assert "location" not in result
    assert result["volatile_state"]["location"] == "The village square"


def test_coerce_current_location_to_volatile():
    """Top-level 'current_location' remapped to volatile_state.location."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "current_location": "The forest",
    }
    result = _coerce_entity_fields(entity)
    assert "current_location" not in result
    assert result["volatile_state"]["location"] == "The forest"


def test_coerce_status_to_volatile_condition():
    """Top-level 'status' remapped to volatile_state.condition."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Active.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "status": "healthy and alert",
    }
    result = _coerce_entity_fields(entity)
    assert "status" not in result  # removed from top level
    assert result["volatile_state"]["condition"] == "healthy and alert"


def test_coerce_emotional_state_to_volatile_condition():
    """Top-level 'emotional_state' remapped to volatile_state.condition."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "emotional_state": "anxious but determined",
    }
    result = _coerce_entity_fields(entity)
    assert "emotional_state" not in result
    assert result["volatile_state"]["condition"] == "anxious but determined"


def test_coerce_abilities_to_stable_attributes():
    """Top-level 'abilities' remapped to stable_attributes.abilities."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "abilities": ["darkvision", "keen senses"],
    }
    result = _coerce_entity_fields(entity)
    assert "abilities" not in result
    assert result["stable_attributes"]["abilities"]["value"] == ["darkvision", "keen senses"]
    assert result["stable_attributes"]["abilities"]["source_turn"] == "turn-050"


def test_coerce_name_aliases_to_stable_aliases():
    """Top-level 'name_aliases' remapped to stable_attributes.aliases."""
    entity = {
        "id": "char-player", "name": "Fenouille", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "name_aliases": "Fenouille Moonwind",
    }
    result = _coerce_entity_fields(entity)
    assert "name_aliases" not in result
    assert result["stable_attributes"]["aliases"]["value"] == "Fenouille Moonwind"


def test_coerce_relations_to_relationships():
    """Top-level 'relations' remapped to 'relationships'."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "relations": [{"target_id": "char-kael", "current_relationship": "ally",
                        "type": "social", "first_seen_turn": "turn-010"}],
    }
    result = _coerce_entity_fields(entity)
    assert "relations" not in result
    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["target_id"] == "char-kael"


def test_coerce_character_relations_to_relationships():
    """Top-level 'character_relations' remapped to 'relationships'."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "character_relations": [{"target_id": "char-elder", "current_relationship": "mentee",
                                  "type": "mentorship", "first_seen_turn": "turn-005"}],
    }
    result = _coerce_entity_fields(entity)
    assert "character_relations" not in result
    assert len(result["relationships"]) == 1


def test_coerce_discards_noise_keys():
    """Known noise keys are silently discarded."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Active.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "image_url": "http://example.com/img.png",
        "future_plans": "Find the artifact",
        "actions": ["walked to camp"],
        "current_activity": "resting",
        "confidence": 0.9,
    }
    result = _coerce_entity_fields(entity)
    for key in ("image_url", "future_plans", "actions", "current_activity", "confidence"):
        assert key not in result


def test_coerce_does_not_overwrite_existing_volatile():
    """Remapped keys don't overwrite existing volatile_state entries."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "volatile_state": {"location": "the camp", "last_updated_turn": "turn-050"},
        "location": "the forest",  # should NOT overwrite
    }
    result = _coerce_entity_fields(entity)
    assert result["volatile_state"]["location"] == "the camp"


def test_coerce_multiple_nonstandard_keys_combined():
    """Multiple non-standard keys coerced in a single entity."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Resting by fire.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-085",
        "abilities": ["darkvision"],
        "equipment": ["staff"],
        "location": "bonfire",
        "status": "tired but safe",
        "actions": ["sat down"],
        "image_url": "http://x.com/y.png",
    }
    result = _coerce_entity_fields(entity)
    # All non-standard keys should be gone
    for key in ("abilities", "equipment", "location", "status", "actions", "image_url"):
        assert key not in result, f"{key} should have been removed"
    # Data should be in the right V2 slots
    assert result["volatile_state"]["equipment"] == ["staff"]
    assert result["volatile_state"]["location"] == "bonfire"
    assert result["volatile_state"]["condition"] == "tired but safe"
    assert result["stable_attributes"]["abilities"]["value"] == ["darkvision"]


def test_coerce_relations_dict_wrapped_to_list():
    """Single dict under a relationship key variant is wrapped in a list."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "relations": {"target_id": "char-kael", "current_relationship": "ally",
                       "type": "social", "first_seen_turn": "turn-010"},
    }
    result = _coerce_entity_fields(entity)
    assert "relations" not in result
    assert isinstance(result["relationships"], list)
    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["target_id"] == "char-kael"


def test_coerce_rejects_malformed_turn_id_for_source_turn():
    """Malformed turn IDs (too few digits or non-numeric) don't become source_turn."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-ab",
        "abilities": ["darkvision"],
    }
    result = _coerce_entity_fields(entity)
    # abilities should still be remapped
    assert "abilities" in result["stable_attributes"]
    # but source_turn must NOT be set because "turn-ab" is invalid
    assert "source_turn" not in result["stable_attributes"]["abilities"]


def test_coerce_accepts_valid_turn_id_for_source_turn():
    """Valid turn IDs with 3+ digits are accepted as source_turn."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-1234",
        "abilities": ["darkvision"],
    }
    result = _coerce_entity_fields(entity)
    assert result["stable_attributes"]["abilities"]["source_turn"] == "turn-1234"


# --- #172: Extended coercion maps and _new suffix stripping ---


def test_coerce_equipment_and_tools_to_volatile():
    """Top-level 'equipment_and_tools' remapped to volatile_state.equipment."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "equipment_and_tools": ["staff", "rope"],
    }
    result = _coerce_entity_fields(entity)
    assert "equipment_and_tools" not in result
    assert result["volatile_state"]["equipment"] == ["staff", "rope"]


def test_coerce_item_equipment_to_volatile():
    """Top-level 'item_equipment' remapped to volatile_state.equipment."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "item_equipment": "sword, shield",
    }
    result = _coerce_entity_fields(entity)
    assert "item_equipment" not in result
    assert result["volatile_state"]["equipment"] == ["sword", "shield"]


def test_coerce_item_inventory_to_volatile():
    """Top-level 'item_inventory' remapped to volatile_state.equipment."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "item_inventory": ["potion", "scroll"],
    }
    result = _coerce_entity_fields(entity)
    assert "item_inventory" not in result
    assert result["volatile_state"]["equipment"] == ["potion", "scroll"]


def test_coerce_health_status_to_volatile_condition():
    """Top-level 'health_status' remapped to volatile_state.condition."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "health_status": "wounded but stable",
    }
    result = _coerce_entity_fields(entity)
    assert "health_status" not in result
    assert result["volatile_state"]["condition"] == "wounded but stable"


def test_coerce_status_effects_to_volatile_condition():
    """Top-level 'status_effects' remapped to volatile_state.condition."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "status_effects": "poisoned",
    }
    result = _coerce_entity_fields(entity)
    assert "status_effects" not in result
    assert result["volatile_state"]["condition"] == "poisoned"


def test_coerce_skills_and_abilities_to_stable():
    """Top-level 'skills_and_abilities' remapped to stable_attributes.abilities."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "skills_and_abilities": ["stealth", "perception"],
    }
    result = _coerce_entity_fields(entity)
    assert "skills_and_abilities" not in result
    assert result["stable_attributes"]["abilities"]["value"] == ["stealth", "perception"]


def test_coerce_alignment_to_stable():
    """Top-level 'alignment' remapped to stable_attributes.alignment."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "alignment": "chaotic neutral",
    }
    result = _coerce_entity_fields(entity)
    assert "alignment" not in result
    assert result["stable_attributes"]["alignment"]["value"] == "chaotic neutral"


def test_coerce_weaknesses_to_stable():
    """Top-level 'weaknesses' remapped to stable_attributes.weaknesses."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "weaknesses": "fire vulnerability",
    }
    result = _coerce_entity_fields(entity)
    assert "weaknesses" not in result
    assert result["stable_attributes"]["weaknesses"]["value"] == "fire vulnerability"


def test_coerce_items_relations_to_relationships():
    """Top-level 'items_relations' remapped to 'relationships'."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "items_relations": [{"target_id": "item-sword", "current_relationship": "owns",
                              "type": "social", "first_seen_turn": "turn-010"}],
    }
    result = _coerce_entity_fields(entity)
    assert "items_relations" not in result
    assert len(result["relationships"]) == 1


def test_coerce_current_relationships_to_relationships():
    """Top-level 'current_relationships' remapped to 'relationships'."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "current_relationships": [{"target_id": "char-npc", "current_relationship": "friend",
                                    "type": "social", "first_seen_turn": "turn-005"}],
    }
    result = _coerce_entity_fields(entity)
    assert "current_relationships" not in result
    assert len(result["relationships"]) == 1


def test_coerce_discards_extended_noise_keys():
    """Extended noise keys from #172 are silently discarded."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.", "current_status": "Active.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "events": [{"event": "arrived"}],
        "activities": ["walking"],
        "activity_history": ["rested", "walked"],
        "abilities_used_in_last_turn": ["darkvision"],
        "description_of_activity": "walking north",
        "recent_emotional_states": "calm",
        "recent_relationship_changes": "none",
        "name_changes": [],
        "equipment_history": ["found sword"],
        "faction_relations_history": [],
        "relationships_history": [],
    }
    result = _coerce_entity_fields(entity)
    for key in ("events", "activities", "activity_history",
                "abilities_used_in_last_turn", "description_of_activity",
                "recent_emotional_states", "recent_relationship_changes",
                "name_changes", "equipment_history", "faction_relations_history",
                "relationships_history"):
        assert key not in result, f"{key} should have been discarded"


def test_coerce_strips_new_suffix_to_base_key():
    """Keys ending in '_new' are normalised to their base form."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "description_new": "A seasoned adventurer.",
        "faction_relations_new": [{"target_id": "faction-guild",
                                    "current_relationship": "allied",
                                    "type": "factional",
                                    "first_seen_turn": "turn-020"}],
    }
    result = _coerce_entity_fields(entity)
    # The _new suffix should be removed, but V2 output must also remain schema-
    # compliant: `description` is not a valid top-level V2 field, and an
    # existing identity value must not be overwritten by description_new.
    assert "description_new" not in result
    assert "faction_relations_new" not in result
    assert "description" not in result
    assert result["identity"] == "The player."


def test_coerce_new_suffix_discards_when_base_exists():
    """Delta key is discarded when the base key already exists."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "relations": [{"target_id": "char-a", "current_relationship": "ally",
                        "type": "social", "first_seen_turn": "turn-010"}],
        "relations_new": [{"target_id": "char-b", "current_relationship": "rival",
                            "type": "adversarial", "first_seen_turn": "turn-040"}],
    }
    result = _coerce_entity_fields(entity)
    assert "relations_new" not in result
    assert "relations" not in result  # base processed by rel remap
    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["target_id"] == "char-a"


def test_coerce_new_suffix_remapped_through_volatile():
    """A _new suffixed volatile key gets stripped then remapped."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "equipment_new": ["bow", "arrows"],
    }
    result = _coerce_entity_fields(entity)
    assert "equipment_new" not in result
    assert "equipment" not in result
    assert result["volatile_state"]["equipment"] == ["bow", "arrows"]


def test_coerce_new_suffix_discards_schema_key_variants_when_base_exists():
    """Schema keys ending in '_new' are discarded when the base key exists."""
    entity = {
        "id": "char-player",
        "id_new": "char-player-updated",
        "name": "PC",
        "name_new": "Updated PC",
        "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-050",
    }
    result = _coerce_entity_fields(entity)
    assert "id_new" not in result
    assert "name_new" not in result
    assert result["id"] == "char-player"
    assert result["name"] == "PC"


def test_coerce_stable_attributes_null_value_removed():
    """stable_attributes entries with null value are stripped (#178)."""
    entity = {
        "id": "char-player", "name": "PC", "type": "character",
        "identity": "The player.",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-050",
        "stable_attributes": {
            "race": {"value": "human", "inference": False, "confidence": 1.0},
            "class": {"value": None, "inference": False, "confidence": 1.0},
            "alignment": {"value": "neutral", "inference": True, "confidence": 0.7},
        },
    }
    result = _coerce_entity_fields(entity)
    sa = result["stable_attributes"]
    assert "race" in sa
    assert "alignment" in sa
    assert "class" not in sa  # null value stripped
