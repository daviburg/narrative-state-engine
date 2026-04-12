"""Tests for tools/migrate_catalogs_v2.py"""

from pathlib import Path

import pytest

# Ensure tools/ is importable
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from migrate_catalogs_v2 import (
    classify_attributes,
    consolidate_relationships,
    convert_entity,
    build_index_entry,
    migrate_catalog,
    strip_inference_tag,
    wrap_stable_attribute,
    write_json,
    read_json,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_framework(tmp_path):
    """Create a temporary framework directory with V1 catalog files."""
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    return tmp_path


def write_catalog(framework_dir: Path, catalog_name: str, entities: list):
    """Helper to write a V1 catalog file."""
    path = framework_dir / "catalogs" / f"{catalog_name}.json"
    write_json(path, entities)


# ---------------------------------------------------------------------------
# test_description_to_identity_split
# ---------------------------------------------------------------------------


def test_description_to_identity_split():
    """V1 entity with 'description' produces V2 entity with 'identity' + placeholder 'current_status'."""
    v1 = {
        "id": "char-test",
        "name": "Test Character",
        "type": "character",
        "description": "A brave warrior from the northern lands.",
        "attributes": {},
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-010",
    }
    v2 = convert_entity(v1, max_turn=50)

    assert v2["identity"] == "A brave warrior from the northern lands."
    assert v2["current_status"] == "Status unknown \u2014 migrated from V1 catalog."
    assert v2["status_updated_turn"] == "turn-010"


# ---------------------------------------------------------------------------
# test_stable_attribute_classification
# ---------------------------------------------------------------------------


def test_stable_attribute_classification():
    """'race', 'class' go to stable_attributes; 'condition', 'equipment' go to volatile_state."""
    attrs = {
        "race": "Elf",
        "class": "Ranger",
        "appearance": "Tall with dark hair",
        "condition": "Healthy",
        "equipment": "sword, shield, lantern",
    }
    stable, volatile = classify_attributes(attrs, "turn-001")

    assert "race" in stable
    assert stable["race"]["value"] == "Elf"
    assert "class" in stable
    assert "appearance" in stable

    assert "condition" in volatile
    assert volatile["condition"] == "Healthy"
    assert "equipment" in volatile
    assert isinstance(volatile["equipment"], list)
    assert "sword" in volatile["equipment"]


# ---------------------------------------------------------------------------
# test_volatile_attribute_classification
# ---------------------------------------------------------------------------


def test_volatile_attribute_classification():
    """Volatile keys moved correctly with last_updated_turn in volatile_state."""
    v1 = {
        "id": "char-v",
        "name": "Volatile Test",
        "type": "character",
        "description": "Test entity.",
        "attributes": {
            "condition": "Injured",
            "status": "Resting",
            "hp_change": "-2 HP",
            "location": "loc-camp",
            "last_action": "sleeping",
            "race": "Human",
        },
        "first_seen_turn": "turn-005",
        "last_updated_turn": "turn-020",
    }
    v2 = convert_entity(v1, max_turn=50)

    vol = v2["volatile_state"]
    assert vol["condition"] == "Injured"
    assert vol["status"] == "Resting"
    assert vol["hp_change"] == "-2 HP"
    assert vol["location"] == "loc-camp"
    assert vol["last_action"] == "sleeping"
    assert vol["last_updated_turn"] == "turn-020"

    stable = v2.get("stable_attributes", {})
    assert "race" in stable
    assert stable["race"]["value"] == "Human"
    # Volatile keys should NOT be in stable
    assert "condition" not in stable
    assert "status" not in stable


# ---------------------------------------------------------------------------
# test_inference_tag_parsing
# ---------------------------------------------------------------------------


def test_inference_tag_parsing():
    """'tall, scarred [inference]' → value stripped, inference=True, confidence=0.7."""
    clean, inferred = strip_inference_tag("tall, scarred [inference]")
    assert clean == "tall, scarred"
    assert inferred is True

    attr = wrap_stable_attribute("tall, scarred [inference]", "turn-003")
    assert attr["value"] == "tall, scarred"
    assert attr["inference"] is True
    assert attr["confidence"] == 0.7
    assert attr["source_turn"] == "turn-003"


def test_no_inference_tag():
    """Normal value without [inference] → inference=False, no confidence."""
    attr = wrap_stable_attribute("Elf", "turn-001")
    assert attr["value"] == "Elf"
    assert attr["inference"] is False
    assert "confidence" not in attr


# ---------------------------------------------------------------------------
# test_relationship_consolidation
# ---------------------------------------------------------------------------


def test_relationship_consolidation():
    """3 relationships for same pair → 1 entry with current_relationship + 2 history entries."""
    rels = [
        {
            "target_id": "char-npc",
            "relationship": "meets",
            "type": "social",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-010",
        },
        {
            "target_id": "char-npc",
            "relationship": "befriends",
            "type": "partnership",
            "first_seen_turn": "turn-020",
            "last_updated_turn": "turn-020",
        },
        {
            "target_id": "char-npc",
            "relationship": "close allies",
            "type": "partnership",
            "first_seen_turn": "turn-030",
            "last_updated_turn": "turn-030",
        },
    ]
    result = consolidate_relationships(rels, "turn-001", max_turn=50)

    assert len(result) == 1
    r = result[0]
    assert r["target_id"] == "char-npc"
    assert r["current_relationship"] == "close allies"
    assert r["type"] == "partnership"
    assert r["first_seen_turn"] == "turn-010"
    assert r["last_updated_turn"] == "turn-030"
    assert len(r["history"]) == 2
    assert r["history"][0]["description"] == "meets"
    assert r["history"][0]["turn"] == "turn-010"
    assert r["history"][1]["description"] == "befriends"


def test_relationship_consolidation_multiple_targets():
    """Relationships to different targets stay separate."""
    rels = [
        {
            "target_id": "char-a",
            "relationship": "friends",
            "type": "social",
            "first_seen_turn": "turn-005",
            "last_updated_turn": "turn-005",
        },
        {
            "target_id": "char-b",
            "relationship": "rivals",
            "type": "adversarial",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-010",
        },
    ]
    result = consolidate_relationships(rels, "turn-001", max_turn=50)
    assert len(result) == 2
    targets = {r["target_id"] for r in result}
    assert targets == {"char-a", "char-b"}


# ---------------------------------------------------------------------------
# test_dormancy_marking
# ---------------------------------------------------------------------------


def test_dormancy_marking():
    """Entity not updated in 15 turns → relationships marked dormant."""
    rels = [
        {
            "target_id": "char-old",
            "relationship": "acquaintance",
            "type": "social",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-035",
        },
    ]
    # max_turn=50, last_updated=35, delta=15 > threshold(10) → dormant
    result = consolidate_relationships(rels, "turn-001", max_turn=50)
    assert result[0]["status"] == "dormant"


def test_active_marking():
    """Entity recently updated → relationships marked active."""
    rels = [
        {
            "target_id": "char-recent",
            "relationship": "ally",
            "type": "partnership",
            "first_seen_turn": "turn-040",
            "last_updated_turn": "turn-048",
        },
    ]
    # max_turn=50, last_updated=48, delta=2 ≤ threshold(10) → active
    result = consolidate_relationships(rels, "turn-001", max_turn=50)
    assert result[0]["status"] == "active"


# ---------------------------------------------------------------------------
# test_index_generation
# ---------------------------------------------------------------------------


def test_index_generation():
    """index.json has correct counts and summaries."""
    v2 = {
        "id": "char-hero",
        "name": "Hero",
        "type": "character",
        "identity": "A legendary hero known throughout the land for slaying dragons and saving villages from doom.",
        "current_status": "Resting.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-050",
        "relationships": [
            {
                "target_id": "char-sidekick",
                "current_relationship": "trusted companion",
                "type": "partnership",
                "status": "active",
                "first_seen_turn": "turn-005",
            },
            {
                "target_id": "char-villain",
                "current_relationship": "nemesis",
                "type": "adversarial",
                "status": "dormant",
                "first_seen_turn": "turn-010",
            },
        ],
    }
    entry = build_index_entry(v2)

    assert entry["id"] == "char-hero"
    assert entry["name"] == "Hero"
    assert entry["type"] == "character"
    assert entry["first_seen_turn"] == "turn-001"
    assert entry["last_updated_turn"] == "turn-050"
    assert entry["active_relationship_count"] == 1
    # status_summary derives from current_status (per schema), not identity
    assert entry["status_summary"] == "Resting."


# ---------------------------------------------------------------------------
# test_idempotency_guard
# ---------------------------------------------------------------------------


def test_idempotency_guard(tmp_framework):
    """Running twice without --force aborts on second run."""
    entities = [
        {
            "id": "char-test",
            "name": "Test",
            "type": "character",
            "description": "A test entity.",
            "attributes": {},
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-005",
        }
    ]
    write_catalog(tmp_framework, "characters", entities)

    # First run succeeds
    count, warnings = migrate_catalog(tmp_framework, "characters", max_turn=10, force=False)
    assert count == 1
    assert not any(w.startswith("ABORT:") for w in warnings)

    # Re-create flat file (simulating a second run setup)
    write_catalog(tmp_framework, "characters", entities)

    # Second run aborts
    count2, warnings2 = migrate_catalog(tmp_framework, "characters", max_turn=10, force=False)
    assert count2 == 0
    assert any("ABORT" in w for w in warnings2)


def test_force_overwrite(tmp_framework):
    """Running with --force overwrites existing per-entity directories."""
    entities = [
        {
            "id": "char-test",
            "name": "Test",
            "type": "character",
            "description": "Original.",
            "attributes": {},
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-005",
        }
    ]
    write_catalog(tmp_framework, "characters", entities)
    migrate_catalog(tmp_framework, "characters", max_turn=10, force=False)

    # Update entity and re-create flat file
    entities[0]["description"] = "Updated."
    write_catalog(tmp_framework, "characters", entities)

    count, warnings = migrate_catalog(tmp_framework, "characters", max_turn=10, force=True)
    assert count == 1
    assert not any(w.startswith("ABORT:") for w in warnings)

    # Verify updated content
    v2 = read_json(tmp_framework / "catalogs" / "characters" / "char-test.json")
    assert v2["identity"] == "Updated."


# ---------------------------------------------------------------------------
# test_v1_backup
# ---------------------------------------------------------------------------


def test_v1_backup(tmp_framework):
    """Original file renamed to .v1.json."""
    entities = [
        {
            "id": "loc-test",
            "name": "Test Location",
            "type": "location",
            "description": "A test location.",
            "attributes": {},
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-003",
        }
    ]
    write_catalog(tmp_framework, "locations", entities)

    migrate_catalog(tmp_framework, "locations", max_turn=10, force=False)

    assert not (tmp_framework / "catalogs" / "locations.json").exists()
    assert (tmp_framework / "catalogs" / "locations.v1.json").exists()

    backup = read_json(tmp_framework / "catalogs" / "locations.v1.json")
    assert len(backup) == 1
    assert backup[0]["id"] == "loc-test"


# ---------------------------------------------------------------------------
# test_invalid_relationship_type_mapping
# ---------------------------------------------------------------------------


def test_invalid_relationship_type_mapping():
    """Invalid V1 relationship types map to 'other'."""
    rels = [
        {
            "target_id": "char-x",
            "relationship": "leaning on",
            "type": "physical_contact",
            "first_seen_turn": "turn-054",
            "last_updated_turn": "turn-054",
        },
    ]
    result = consolidate_relationships(rels, "turn-001", max_turn=60)
    assert result[0]["type"] == "other"


# ---------------------------------------------------------------------------
# test_equipment_string_to_list
# ---------------------------------------------------------------------------


def test_equipment_string_to_list():
    """Comma-separated equipment string converted to list."""
    attrs = {"equipment": "sword, shield, lantern"}
    _, volatile = classify_attributes(attrs, "turn-001")
    assert volatile["equipment"] == ["sword", "shield", "lantern"]


# ---------------------------------------------------------------------------
# test_full_migration_roundtrip
# ---------------------------------------------------------------------------


def test_full_migration_roundtrip(tmp_framework):
    """Full migration produces valid V2 files with index."""
    characters = [
        {
            "id": "char-hero",
            "name": "Hero",
            "type": "character",
            "description": "A brave hero.",
            "attributes": {
                "race": "Human",
                "class": "Fighter [inference]",
                "condition": "Healthy",
            },
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-020",
            "relationships": [
                {
                    "target_id": "char-villain",
                    "relationship": "fights",
                    "type": "adversarial",
                    "direction": "outgoing",
                    "confidence": 0.9,
                    "first_seen_turn": "turn-005",
                    "last_updated_turn": "turn-005",
                },
                {
                    "target_id": "char-villain",
                    "relationship": "defeats",
                    "type": "adversarial",
                    "direction": "outgoing",
                    "confidence": 1.0,
                    "first_seen_turn": "turn-018",
                    "last_updated_turn": "turn-018",
                },
            ],
        },
    ]
    write_catalog(tmp_framework, "characters", characters)

    count, warnings = migrate_catalog(tmp_framework, "characters", max_turn=20, force=False)
    assert count == 1

    # Check entity file
    char_dir = tmp_framework / "catalogs" / "characters"
    v2 = read_json(char_dir / "char-hero.json")
    assert v2["identity"] == "A brave hero."
    assert v2["stable_attributes"]["race"]["value"] == "Human"
    assert v2["stable_attributes"]["class"]["value"] == "Fighter"
    assert v2["stable_attributes"]["class"]["inference"] is True
    assert v2["stable_attributes"]["class"]["confidence"] == 0.7
    assert v2["volatile_state"]["condition"] == "Healthy"
    assert len(v2["relationships"]) == 1
    assert v2["relationships"][0]["current_relationship"] == "defeats"
    assert len(v2["relationships"][0]["history"]) == 1

    # Check index
    index = read_json(char_dir / "index.json")
    assert len(index) == 1
    assert index[0]["id"] == "char-hero"
    assert index[0]["active_relationship_count"] == 1
