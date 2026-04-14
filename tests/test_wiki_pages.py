"""Tests for wiki-style markdown page generation."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from generate_wiki_pages import (
    generate_character_page,
    generate_location_page,
    generate_faction_page,
    generate_item_page,
    generate_index_page,
    generate_wiki_pages,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CHARACTER = {
    "id": "char-elder",
    "name": "The Elder",
    "type": "character",
    "identity": "An elderly authority figure in the tribal community.",
    "current_status": "Watching over the camp activities.",
    "status_updated_turn": "turn-061",
    "stable_attributes": {
        "race": {
            "value": "Human",
            "inference": True,
            "confidence": 0.9,
            "source_turn": "turn-019",
        },
        "appearance": {
            "value": ["grizzled man", "eyes like chips of flint"],
            "inference": False,
            "confidence": 1.0,
            "source_turn": "turn-019",
        },
    },
    "volatile_state": {
        "condition": "alert and observant",
        "equipment": ["walking stick"],
        "location": "central spot in the camp",
        "last_updated_turn": "turn-061",
    },
    "first_seen_turn": "turn-016",
    "last_updated_turn": "turn-061",
    "relationships": [
        {
            "target_id": "char-player",
            "current_relationship": "commanding",
            "type": "social",
            "status": "active",
            "first_seen_turn": "turn-017",
            "last_updated_turn": "turn-045",
            "history": [
                {"turn": "turn-017", "description": "first meeting"},
                {"turn": "turn-045", "description": "issuing orders"},
            ],
        }
    ],
}

SAMPLE_LOCATION = {
    "id": "loc-camp-dwelling",
    "name": "the camp",
    "type": "location",
    "identity": "A tribal encampment where various activities take place.",
    "current_status": "The camp is busy with daily tasks.",
    "status_updated_turn": "turn-085",
    "stable_attributes": {
        "aliases": {
            "value": ["camp or dwelling"],
            "inference": False,
            "source_turn": "turn-085",
        }
    },
    "first_seen_turn": "turn-013",
    "last_updated_turn": "turn-085",
    "relationships": [
        {
            "target_id": "char-elder",
            "current_relationship": "home of",
            "type": "location",
        }
    ],
}

SAMPLE_FACTION = {
    "id": "faction-two-figures",
    "name": "figures",
    "type": "faction",
    "identity": "Unidentified individuals who captured the player.",
    "current_status": "Capturing the player.",
    "status_updated_turn": "turn-014",
    "stable_attributes": {
        "role": {
            "value": ["captors"],
            "inference": True,
            "confidence": 0.95,
            "source_turn": "turn-007",
        }
    },
    "first_seen_turn": "turn-007",
    "last_updated_turn": "turn-014",
}

SAMPLE_ITEM = {
    "id": "item-your-staff",
    "name": "your staff",
    "type": "item",
    "identity": "A personal weapon and tool.",
    "current_status": "Resting at your side.",
    "status_updated_turn": "turn-045",
    "stable_attributes": {},
    "volatile_state": {
        "condition": "securely resting",
        "location": "at your side",
        "last_updated_turn": "turn-045",
    },
    "first_seen_turn": "turn-045",
    "last_updated_turn": "turn-045",
}

NAME_INDEX = {
    "char-player": ("Player Character", "../characters/char-player.md"),
    "char-elder": ("The Elder", "../characters/char-elder.md"),
    "loc-camp-dwelling": ("the camp", "../locations/loc-camp-dwelling.md"),
    "faction-two-figures": ("figures", "../factions/faction-two-figures.md"),
    "item-your-staff": ("your staff", "../items/item-your-staff.md"),
}


# ---------------------------------------------------------------------------
# Character page tests
# ---------------------------------------------------------------------------

def test_character_page_has_infobox():
    """Generated character markdown contains name, race, appearance in table."""
    md = generate_character_page(SAMPLE_CHARACTER, NAME_INDEX)
    assert "# The Elder" in md
    assert "| **Type** | Character |" in md
    assert "| **Race** | Human |" in md
    assert "turn-016" in md


def test_character_page_has_relationships():
    """Relationships rendered as table with resolved target names."""
    md = generate_character_page(SAMPLE_CHARACTER, NAME_INDEX)
    assert "## Relationships" in md
    assert "commanding" in md
    assert "social" in md
    assert "active" in md


def test_character_page_has_history():
    """Relationship history entries rendered."""
    md = generate_character_page(SAMPLE_CHARACTER, NAME_INDEX)
    assert "### Relationship History" in md
    assert "**turn-017:**" in md
    assert "first meeting" in md
    assert "**turn-045:**" in md


def test_character_page_has_current_state():
    """Volatile state section rendered."""
    md = generate_character_page(SAMPLE_CHARACTER, NAME_INDEX)
    assert "**Condition:** alert and observant" in md
    assert "**Equipment:** walking stick" in md
    assert "**Location:** central spot in the camp" in md


# ---------------------------------------------------------------------------
# Location page tests
# ---------------------------------------------------------------------------

def test_location_page_format():
    """Location page has appropriate sections."""
    md = generate_location_page(SAMPLE_LOCATION, NAME_INDEX)
    assert "# the camp" in md
    assert "| **Type** | Location |" in md
    assert "## Current Status" in md
    assert "## Connected Entities" in md
    assert "home of" in md


# ---------------------------------------------------------------------------
# Faction page tests
# ---------------------------------------------------------------------------

def test_faction_page_format():
    """Faction page has appropriate sections."""
    md = generate_faction_page(SAMPLE_FACTION, NAME_INDEX)
    assert "# figures" in md
    assert "| **Type** | Faction |" in md
    assert "## Attributes" in md
    assert "captors" in md


# ---------------------------------------------------------------------------
# Item page tests
# ---------------------------------------------------------------------------

def test_item_page_format():
    """Item page has appropriate sections."""
    md = generate_item_page(SAMPLE_ITEM, NAME_INDEX)
    assert "# your staff" in md
    assert "| **Type** | Item |" in md
    assert "## Current State" in md
    assert "**Condition:** securely resting" in md


# ---------------------------------------------------------------------------
# Index page tests
# ---------------------------------------------------------------------------

def test_index_page_links():
    """README.md contains links to all entity pages."""
    entities = [SAMPLE_CHARACTER, {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-054",
        "current_status": "Leaning against the warrior.",
    }]
    md = generate_index_page("characters", entities)
    assert "# Characters" in md
    assert "[The Elder](char-elder.md)" in md
    assert "[Player Character](char-player.md)" in md
    # Player appears first (turn-001 < turn-016)
    player_pos = md.index("char-player.md")
    elder_pos = md.index("char-elder.md")
    assert player_pos < elder_pos


def test_index_page_truncates_status():
    """Long status is truncated to ~60 chars."""
    entity = {
        "id": "char-test",
        "name": "Test",
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-001",
        "current_status": "A" * 100,
    }
    md = generate_index_page("characters", [entity])
    # Should contain truncated status with ...
    assert "..." in md
    # Full 100-char string should NOT appear
    assert "A" * 100 not in md


# ---------------------------------------------------------------------------
# Target ID resolution tests
# ---------------------------------------------------------------------------

def test_target_id_resolved_to_name():
    """Relationship target_id resolved to entity name from index."""
    md = generate_character_page(SAMPLE_CHARACTER, NAME_INDEX)
    # char-player should be resolved to a link with name
    assert "[Player Character]" in md


def test_missing_target_graceful():
    """Unknown target_id rendered as raw ID without crash."""
    entity = dict(SAMPLE_CHARACTER)
    entity["relationships"] = [
        {
            "target_id": "char-unknown-npc",
            "current_relationship": "met once",
            "type": "social",
            "status": "dormant",
        }
    ]
    md = generate_character_page(entity, NAME_INDEX)
    assert "char-unknown-npc" in md


# ---------------------------------------------------------------------------
# Empty catalog tests
# ---------------------------------------------------------------------------

def test_empty_catalog_graceful():
    """Empty entity type directory produces empty index page."""
    md = generate_index_page("characters", [])
    assert "# Characters" in md
    assert "No entities cataloged yet" in md


# ---------------------------------------------------------------------------
# Integration: generate to temp dir
# ---------------------------------------------------------------------------

def test_generate_wiki_pages_integration():
    """Full generation writes .md files alongside .json files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up V2 catalog structure
        char_dir = os.path.join(tmpdir, "characters")
        loc_dir = os.path.join(tmpdir, "locations")
        faction_dir = os.path.join(tmpdir, "factions")
        items_dir = os.path.join(tmpdir, "items")
        for d in (char_dir, loc_dir, faction_dir, items_dir):
            os.makedirs(d)

        # Write sample entity files
        with open(os.path.join(char_dir, "char-elder.json"), "w") as f:
            json.dump(SAMPLE_CHARACTER, f)
        with open(os.path.join(loc_dir, "loc-camp-dwelling.json"), "w") as f:
            json.dump(SAMPLE_LOCATION, f)
        with open(os.path.join(faction_dir, "faction-two-figures.json"), "w") as f:
            json.dump(SAMPLE_FACTION, f)
        with open(os.path.join(items_dir, "item-your-staff.json"), "w") as f:
            json.dump(SAMPLE_ITEM, f)

        stats = generate_wiki_pages(tmpdir)

        # Check that .md files were created
        assert os.path.exists(os.path.join(char_dir, "char-elder.md"))
        assert os.path.exists(os.path.join(char_dir, "README.md"))
        assert os.path.exists(os.path.join(loc_dir, "loc-camp-dwelling.md"))
        assert os.path.exists(os.path.join(loc_dir, "README.md"))
        assert os.path.exists(os.path.join(faction_dir, "faction-two-figures.md"))
        assert os.path.exists(os.path.join(items_dir, "item-your-staff.md"))

        # Verify stats
        assert stats["characters"] == 2  # 1 entity + 1 README
        assert stats["locations"] == 2
        assert stats["factions"] == 2
        assert stats["items"] == 2


def test_generate_wiki_pages_index_only():
    """--index-only only creates README.md, not entity .md files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        char_dir = os.path.join(tmpdir, "characters")
        os.makedirs(char_dir)
        with open(os.path.join(char_dir, "char-elder.json"), "w") as f:
            json.dump(SAMPLE_CHARACTER, f)

        stats = generate_wiki_pages(tmpdir, entity_types=["characters"], index_only=True)

        assert os.path.exists(os.path.join(char_dir, "README.md"))
        assert not os.path.exists(os.path.join(char_dir, "char-elder.md"))
        assert stats["characters"] == 1  # Only README


# ---------------------------------------------------------------------------
# Table escaping tests
# ---------------------------------------------------------------------------

def test_pipe_in_value_escaped():
    """Pipe characters in attribute values are escaped in table output."""
    entity = dict(SAMPLE_CHARACTER)
    entity["stable_attributes"] = {
        "catch_phrase": {
            "value": "yes | no | maybe",
            "inference": False,
            "confidence": 1.0,
            "source_turn": "turn-020",
        }
    }
    md = generate_character_page(entity, NAME_INDEX)
    # Pipes in the value should be escaped
    assert "yes \\| no \\| maybe" in md
    # Should not break the table (no bare | inside cell content)


def test_newline_in_value_escaped():
    """Newlines in attribute values are replaced with spaces."""
    entity = dict(SAMPLE_CHARACTER)
    entity["stable_attributes"] = {
        "bio": {
            "value": "Line one\nLine two",
            "inference": False,
            "confidence": 1.0,
            "source_turn": "turn-020",
        }
    }
    md = generate_character_page(entity, NAME_INDEX)
    assert "Line one Line two" in md
    assert "Line one\nLine two" not in md


# ---------------------------------------------------------------------------
# Entity type label tests
# ---------------------------------------------------------------------------

def test_creature_type_label():
    """Creature entities in characters catalog show 'Creature' not 'Character'."""
    entity = dict(SAMPLE_CHARACTER)
    entity["type"] = "creature"
    entity["id"] = "creature-wolf"
    md = generate_character_page(entity, NAME_INDEX)
    assert "| **Type** | Creature |" in md


def test_concept_type_label():
    """Concept entities in items catalog show 'Concept' not 'Item'."""
    entity = dict(SAMPLE_ITEM)
    entity["type"] = "concept"
    entity["id"] = "concept-fate"
    md = generate_item_page(entity, NAME_INDEX)
    assert "| **Type** | Concept |" in md


# ---------------------------------------------------------------------------
# Stale page pruning tests
# ---------------------------------------------------------------------------

def test_stale_md_pruned():
    """Wiki generation removes .md files for entities no longer in JSON catalog."""
    with tempfile.TemporaryDirectory() as tmpdir:
        char_dir = os.path.join(tmpdir, "characters")
        os.makedirs(char_dir)

        # Write one entity JSON
        with open(os.path.join(char_dir, "char-elder.json"), "w") as f:
            json.dump(SAMPLE_CHARACTER, f)
        # Write a stale .md for a deleted entity
        stale_md = os.path.join(char_dir, "char-deleted-npc.md")
        with open(stale_md, "w") as f:
            f.write("# Old NPC\n")

        generate_wiki_pages(tmpdir, entity_types=["characters"])

        # Stale page should be removed
        assert not os.path.exists(stale_md)
        # Valid page should exist
        assert os.path.exists(os.path.join(char_dir, "char-elder.md"))
