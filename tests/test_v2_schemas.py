"""Tests for V2 entity schema, entity-index, turn-context, and updated state schema."""

import json
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from validate import validate_file, validate_dir

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
ENTITY_SCHEMA = os.path.join(REPO_ROOT, "schemas", "entity.schema.json")
ENTITY_INDEX_SCHEMA = os.path.join(REPO_ROOT, "schemas", "entity-index.schema.json")
TURN_CONTEXT_SCHEMA = os.path.join(REPO_ROOT, "schemas", "turn-context.schema.json")
STATE_SCHEMA = os.path.join(REPO_ROOT, "schemas", "state.schema.json")


def _write_and_validate(data, schema_path):
    """Write data to a temp file, close it, validate, then clean up.

    Closes the file handle before calling validate_file to avoid
    Windows file-locking issues with NamedTemporaryFile.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return validate_file(path, schema_path)
    finally:
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# V2 entity schema tests
# ---------------------------------------------------------------------------

def _minimal_v2_entity():
    return {
        "id": "char-test",
        "name": "Test Character",
        "type": "character",
        "identity": "A test character used in unit tests.",
        "first_seen_turn": "turn-001",
    }


def test_v2_entity_validates_minimal():
    """Minimal V2 entity with only required fields validates."""
    entity = _minimal_v2_entity()
    errors = _write_and_validate(entity, ENTITY_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_v2_entity_validates_full():
    """V2 entity with all optional fields validates."""
    entity = {
        "id": "char-hero",
        "name": "Hero",
        "type": "character",
        "identity": "A brave hero on a quest.",
        "current_status": "Currently resting at the inn.",
        "status_updated_turn": "turn-010",
        "stable_attributes": {
            "race": {"value": "Elf", "inference": True, "confidence": 0.8, "source_turn": "turn-002"},
            "class": {"value": "Ranger", "inference": True, "confidence": 0.6, "source_turn": "turn-005"},
            "aliases": {"value": ["The Wanderer", "Greencloak"], "inference": False, "source_turn": "turn-003"},
        },
        "volatile_state": {
            "condition": "Healthy",
            "equipment": ["sword", "shield", "lantern"],
            "location": "loc-inn",
            "last_updated_turn": "turn-010",
        },
        "relationships": [
            {
                "target_id": "char-companion",
                "current_relationship": "trusted traveling companion",
                "type": "partnership",
                "direction": "bidirectional",
                "status": "active",
                "confidence": 1.0,
                "first_seen_turn": "turn-003",
                "last_updated_turn": "turn-010",
                "history": [
                    {"turn": "turn-003", "description": "met on the road"},
                    {"turn": "turn-007", "description": "fought bandits together"},
                ],
            },
            {
                "target_id": "char-villain",
                "current_relationship": "captured by, later freed",
                "type": "adversarial",
                "direction": "incoming",
                "status": "resolved",
                "first_seen_turn": "turn-005",
                "last_updated_turn": "turn-008",
                "resolved_turn": "turn-008",
                "resolution_note": "escaped from captivity",
                "history": [
                    {"turn": "turn-005", "description": "captured during ambush"},
                    {"turn": "turn-008", "description": "escaped with help"},
                ],
            },
        ],
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-010",
        "notes": "Key protagonist. Track development carefully.",
    }
    errors = _write_and_validate(entity, ENTITY_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_v2_entity_missing_identity_fails():
    """V2 entity without required 'identity' field fails."""
    entity = {
        "id": "char-bad",
        "name": "Bad",
        "type": "character",
        "first_seen_turn": "turn-001",
    }
    errors = _write_and_validate(entity, ENTITY_SCHEMA)
    assert len(errors) > 0
    assert any("identity" in e for e in errors)


def test_v2_entity_new_relationship_types():
    """V2 relationship enum accepts social, adversarial, romantic."""
    for rel_type in ["social", "adversarial", "romantic"]:
        entity = _minimal_v2_entity()
        entity["relationships"] = [
            {
                "target_id": "char-other",
                "current_relationship": f"a {rel_type} connection",
                "type": rel_type,
                "first_seen_turn": "turn-002",
            }
        ]
        errors = _write_and_validate(entity, ENTITY_SCHEMA)
        assert errors == [], f"Failed for type '{rel_type}': {errors}"


def test_v2_entity_rejects_tribal_role():
    """V2 relationship enum no longer includes tribal_role."""
    entity = _minimal_v2_entity()
    entity["relationships"] = [
        {
            "target_id": "char-other",
            "current_relationship": "tribe leader",
            "type": "tribal_role",
            "first_seen_turn": "turn-002",
        }
    ]
    errors = _write_and_validate(entity, ENTITY_SCHEMA)
    assert len(errors) > 0


def test_v2_entity_id_pattern_enforced():
    """V2 entity ID pattern enforces lowercase kebab-case after prefix."""
    entity = _minimal_v2_entity()
    entity["id"] = "char-Bad_Name"  # uppercase and underscore should fail
    errors = _write_and_validate(entity, ENTITY_SCHEMA)
    assert len(errors) > 0


def test_v2_location_entity_validates():
    """V2 location entity validates."""
    entity = {
        "id": "loc-camp-light",
        "name": "The Camp",
        "type": "location",
        "identity": "Main tribal campsite.",
        "first_seen_turn": "turn-005",
    }
    errors = _write_and_validate(entity, ENTITY_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Entity index schema tests
# ---------------------------------------------------------------------------

def test_entity_index_validates():
    """Valid entity index array validates."""
    index = [
        {
            "id": "char-hero",
            "name": "Hero",
            "type": "character",
            "status_summary": "Active hero on a quest.",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-010",
            "active_relationship_count": 3,
        },
        {
            "id": "loc-inn",
            "name": "The Inn",
            "type": "location",
            "first_seen_turn": "turn-002",
        },
    ]
    errors = _write_and_validate(index, ENTITY_INDEX_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_entity_index_empty_validates():
    """Empty entity index validates."""
    errors = _write_and_validate([], ENTITY_INDEX_SCHEMA)
    assert errors == []


def test_entity_index_missing_required_fails():
    """Index entry missing required fields fails."""
    index = [{"id": "char-x", "name": "X"}]  # missing type and first_seen_turn
    errors = _write_and_validate(index, ENTITY_INDEX_SCHEMA)
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Turn context schema tests
# ---------------------------------------------------------------------------

def test_turn_context_validates_minimal():
    """Minimal turn context validates."""
    ctx = {
        "as_of_turn": "turn-010",
        "scene_entities": [
            {
                "id": "char-hero",
                "name": "Hero",
                "identity": "A brave hero.",
            }
        ],
    }
    errors = _write_and_validate(ctx, TURN_CONTEXT_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_turn_context_validates_full():
    """Full turn context with all sections validates."""
    ctx = {
        "as_of_turn": "turn-010",
        "scene_entities": [
            {
                "id": "char-hero",
                "name": "Hero",
                "identity": "A brave hero.",
                "current_status": "Resting at the inn.",
                "volatile_state": {"condition": "Healthy", "location": "loc-inn"},
                "active_relationships": [
                    {
                        "target_id": "char-companion",
                        "target_name": "Companion",
                        "relationship": "trusted ally",
                        "type": "partnership",
                        "status": "active",
                    }
                ],
            }
        ],
        "scene_locations": [
            {
                "id": "loc-inn",
                "name": "The Inn",
                "identity": "A roadside inn.",
                "current_status": "Busy evening.",
            }
        ],
        "nearby_entities_summary": [
            {
                "id": "char-elder",
                "name": "Elder",
                "status_summary": "Village elder. Not present.",
            }
        ],
    }
    errors = _write_and_validate(ctx, TURN_CONTEXT_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_turn_context_missing_scene_entities_fails():
    """Turn context without required scene_entities fails."""
    ctx = {"as_of_turn": "turn-010"}
    errors = _write_and_validate(ctx, TURN_CONTEXT_SCHEMA)
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# State schema tests (new optional fields)
# ---------------------------------------------------------------------------

def _minimal_state():
    return {
        "as_of_turn": "turn-006",
        "current_world_state": "The world is stable.",
        "player_state": {
            "location": "loc-inn",
            "condition": "Healthy",
        },
        "active_threads": ["plot-quest"],
    }


def test_state_without_new_fields_validates():
    """State.json without hp, inventory, status_effects still validates."""
    state = _minimal_state()
    errors = _write_and_validate(state, STATE_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_state_with_hp_validates():
    """State with structured HP validates."""
    state = _minimal_state()
    state["player_state"]["hp"] = {
        "narrative": "Full health",
        "numeric": 25,
        "max_hp": 30,
        "last_change": {"delta": "+5", "source": "healing potion", "turn": "turn-005"},
    }
    errors = _write_and_validate(state, STATE_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_state_with_null_hp_validates():
    """State with null numeric HP (non-numeric game) validates."""
    state = _minimal_state()
    state["player_state"]["hp"] = {
        "narrative": "Healthy, no injuries",
        "numeric": None,
        "max_hp": None,
    }
    errors = _write_and_validate(state, STATE_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_state_with_inventory_validates():
    """State with structured inventory validates."""
    state = _minimal_state()
    state["player_state"]["inventory"] = [
        {"item_id": "item-sword", "name": "Iron Sword", "carried": True, "quantity": 1, "notes": None},
        {"item_id": None, "name": "Herbal Pouches", "carried": True, "quantity": 3, "notes": "various herbs"},
    ]
    errors = _write_and_validate(state, STATE_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_state_with_status_effects_validates():
    """State with status effects validates."""
    state = _minimal_state()
    state["player_state"]["status_effects"] = [
        {"effect": "fatigued", "source": "long march", "since_turn": "turn-004"},
        {"effect": "blessed"},
    ]
    errors = _write_and_validate(state, STATE_SCHEMA)
    assert errors == [], f"Unexpected errors: {errors}"


def test_existing_demo_state_validates():
    """The existing demo-session state.json validates against updated schema."""
    demo_state = os.path.join(REPO_ROOT, "examples", "demo-session", "derived", "state.json")
    if os.path.exists(demo_state):
        errors = validate_file(demo_state, STATE_SCHEMA)
        assert errors == [], f"Demo state.json failed: {errors}"


# ---------------------------------------------------------------------------
# Per-entity directory validation tests
# ---------------------------------------------------------------------------

def test_per_entity_directory_validation():
    """validate_dir finds and validates per-entity files in catalog directories."""
    tmpdir = tempfile.mkdtemp()
    try:
        # Create catalogs/characters/ structure
        cat_dir = os.path.join(tmpdir, "catalogs", "characters")
        os.makedirs(cat_dir)

        entity = _minimal_v2_entity()
        with open(os.path.join(cat_dir, "char-test.json"), "w") as f:
            json.dump(entity, f)

        index = [{"id": "char-test", "name": "Test", "type": "character", "first_seen_turn": "turn-001"}]
        with open(os.path.join(cat_dir, "index.json"), "w") as f:
            json.dump(index, f)

        passed, failed, _ = validate_dir(tmpdir, REPO_ROOT)
        assert passed == 2, f"Expected 2 passed, got {passed}"
        assert failed == 0, f"Expected 0 failed, got {failed}"
    finally:
        shutil.rmtree(tmpdir)


def test_per_entity_dir_non_schema_field_fails():
    """Entity with non-schema fields (e.g. 'description') fails validation."""
    tmpdir = tempfile.mkdtemp()
    try:
        cat_dir = os.path.join(tmpdir, "catalogs", "characters")
        os.makedirs(cat_dir)

        bad_entity = {
            "id": "char-old",
            "name": "Old",
            "type": "character",
            "description": "Not a V2 field.",
            "first_seen_turn": "turn-001",
        }
        with open(os.path.join(cat_dir, "char-old.json"), "w") as f:
            json.dump(bad_entity, f)

        passed, failed, _ = validate_dir(tmpdir, REPO_ROOT)
        assert failed == 1
    finally:
        shutil.rmtree(tmpdir)
