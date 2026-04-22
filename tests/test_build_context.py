"""Tests for build_context.py: entity mention detection, one-hop expansion,
location resolution, nearby entity filtering, and schema validation."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from build_context import (
    build_context,
    build_nearby_summary,
    expand_one_hop,
    find_mentions,
    load_indexes,
    read_turn_text,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def v2_fixture(tmp_path):
    """Create a minimal V2 catalog layout with characters, locations, and indexes."""
    session = tmp_path / "session"
    session.mkdir()
    transcript = session / "transcript"
    transcript.mkdir()
    derived = session / "derived"
    derived.mkdir()

    framework = tmp_path / "framework"
    catalogs = framework / "catalogs"

    # Characters directory
    chars_dir = catalogs / "characters"
    chars_dir.mkdir(parents=True)

    char_player = {
        "id": "char-player",
        "name": "Fenouille Moonwind",
        "type": "character",
        "identity": "A player character exploring the frozen north.",
        "current_status": "Awake and alert at camp.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-078",
        "volatile_state": {
            "condition": "healthy",
            "location": "loc-camp",
            "last_updated_turn": "turn-078",
        },
        "relationships": [
            {
                "target_id": "char-elder",
                "current_relationship": "cautious ally",
                "type": "social",
                "status": "active",
                "first_seen_turn": "turn-016",
                "last_updated_turn": "turn-072",
            },
            {
                "target_id": "char-ghost",
                "current_relationship": "former captor",
                "type": "adversarial",
                "status": "dormant",
                "first_seen_turn": "turn-005",
                "last_updated_turn": "turn-010",
            },
        ],
    }
    (chars_dir / "char-player.json").write_text(
        json.dumps(char_player, indent=2), encoding="utf-8"
    )

    char_elder = {
        "id": "char-elder",
        "name": "the elder",
        "type": "character",
        "identity": "A grizzled authority figure of the tribe.",
        "current_status": "Observing camp activity.",
        "first_seen_turn": "turn-016",
        "last_updated_turn": "turn-072",
        "volatile_state": {
            "condition": "alert",
            "last_updated_turn": "turn-072",
        },
        "relationships": [],
    }
    (chars_dir / "char-elder.json").write_text(
        json.dumps(char_elder, indent=2), encoding="utf-8"
    )

    char_ghost = {
        "id": "char-ghost",
        "name": "the ghost",
        "type": "character",
        "identity": "A spectral figure from the past.",
        "first_seen_turn": "turn-005",
        "last_updated_turn": "turn-010",
        "relationships": [],
    }
    (chars_dir / "char-ghost.json").write_text(
        json.dumps(char_ghost, indent=2), encoding="utf-8"
    )

    chars_index = [
        {
            "id": "char-player",
            "name": "Fenouille Moonwind",
            "type": "character",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-078",
            "status_summary": "Awake and alert at camp.",
            "active_relationship_count": 1,
        },
        {
            "id": "char-elder",
            "name": "the elder",
            "type": "character",
            "first_seen_turn": "turn-016",
            "last_updated_turn": "turn-072",
            "status_summary": "Observing camp activity.",
            "active_relationship_count": 0,
        },
        {
            "id": "char-ghost",
            "name": "the ghost",
            "type": "character",
            "first_seen_turn": "turn-005",
            "last_updated_turn": "turn-010",
            "status_summary": "A spectral figure from the past.",
            "active_relationship_count": 0,
        },
    ]
    (chars_dir / "index.json").write_text(
        json.dumps(chars_index, indent=2), encoding="utf-8"
    )

    # Locations directory
    locs_dir = catalogs / "locations"
    locs_dir.mkdir(parents=True)

    loc_camp = {
        "id": "loc-camp",
        "name": "the camp",
        "type": "location",
        "identity": "A rough encampment in a forest clearing.",
        "current_status": "Active and bustling with morning tasks.",
        "first_seen_turn": "turn-013",
        "last_updated_turn": "turn-078",
    }
    (locs_dir / "loc-camp.json").write_text(
        json.dumps(loc_camp, indent=2), encoding="utf-8"
    )

    loc_forest = {
        "id": "loc-forest",
        "name": "the forest",
        "type": "location",
        "identity": "Dense moonlit forest surrounding the camp.",
        "current_status": "Dark and quiet.",
        "first_seen_turn": "turn-003",
        "last_updated_turn": "turn-041",
    }
    (locs_dir / "loc-forest.json").write_text(
        json.dumps(loc_forest, indent=2), encoding="utf-8"
    )

    locs_index = [
        {
            "id": "loc-camp",
            "name": "the camp",
            "type": "location",
            "first_seen_turn": "turn-013",
            "last_updated_turn": "turn-078",
            "status_summary": "Active and bustling.",
            "active_relationship_count": 0,
        },
        {
            "id": "loc-forest",
            "name": "the forest",
            "type": "location",
            "first_seen_turn": "turn-003",
            "last_updated_turn": "turn-041",
            "status_summary": "Dark and quiet.",
            "active_relationship_count": 0,
        },
    ]
    (locs_dir / "index.json").write_text(
        json.dumps(locs_index, indent=2), encoding="utf-8"
    )

    # Factions (empty)
    factions_dir = catalogs / "factions"
    factions_dir.mkdir(parents=True)
    (factions_dir / "index.json").write_text("[]", encoding="utf-8")

    # Items (empty)
    items_dir = catalogs / "items"
    items_dir.mkdir(parents=True)
    (items_dir / "index.json").write_text("[]", encoding="utf-8")

    return {
        "session": str(session),
        "framework": str(framework),
        "catalogs": str(catalogs),
        "transcript": str(transcript),
        "derived": str(derived),
    }


def _write_turn(transcript_dir: str, turn_id: str, text: str, speaker: str = "dm"):
    """Write a turn transcript file."""
    path = os.path.join(transcript_dir, f"{turn_id}-{speaker}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {turn_id} — {speaker.upper()}\n\n{text}\n")


# ---------------------------------------------------------------------------
# Test: entity mention detection by name
# ---------------------------------------------------------------------------

class TestEntityMentionDetection:
    def test_entity_name_found(self, v2_fixture):
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "The elder watches from across the fire."
        )
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        turn_text = read_turn_text(v2_fixture["session"], "turn-078")
        mentions = find_mentions(turn_text, name_lookup, id_lookup)
        assert "char-elder" in mentions

    def test_entity_id_detection(self, v2_fixture):
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "Reference to char-player in the notes."
        )
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        turn_text = read_turn_text(v2_fixture["session"], "turn-078")
        mentions = find_mentions(turn_text, name_lookup, id_lookup)
        assert "char-player" in mentions

    def test_case_insensitive(self, v2_fixture):
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "FENOUILLE MOONWIND speaks to the group."
        )
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        turn_text = read_turn_text(v2_fixture["session"], "turn-078")
        mentions = find_mentions(turn_text, name_lookup, id_lookup)
        assert "char-player" in mentions


# ---------------------------------------------------------------------------
# Test: one-hop relationship expansion
# ---------------------------------------------------------------------------

class TestOneHopExpansion:
    def test_active_relationship_expanded(self, v2_fixture):
        """Entity A mentioned, A has active relationship to B → B in expanded."""
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "Fenouille Moonwind sits by the fire."
        )
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        turn_text = read_turn_text(v2_fixture["session"], "turn-078")
        mentioned = find_mentions(turn_text, name_lookup, id_lookup)
        assert "char-player" in mentioned

        expanded = expand_one_hop(mentioned, v2_fixture["catalogs"], id_lookup)
        # char-elder should be expanded via active relationship
        assert "char-elder" in expanded

    def test_dormant_relationship_excluded(self, v2_fixture):
        """Entity A mentioned, A has dormant relationship to C → C NOT expanded."""
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "Fenouille Moonwind sits by the fire."
        )
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        turn_text = read_turn_text(v2_fixture["session"], "turn-078")
        mentioned = find_mentions(turn_text, name_lookup, id_lookup)
        expanded = expand_one_hop(mentioned, v2_fixture["catalogs"], id_lookup)
        # char-ghost has dormant relationship — should NOT be expanded
        assert "char-ghost" not in expanded


# ---------------------------------------------------------------------------
# Test: location from volatile_state
# ---------------------------------------------------------------------------

class TestLocationFromVolatileState:
    def test_location_resolved_from_volatile(self, v2_fixture):
        """Entity A's volatile_state.location = 'loc-camp' → loc-camp in scene_locations."""
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "Fenouille Moonwind wakes up."
        )
        result = build_context(
            session_dir=v2_fixture["session"],
            turn_id="turn-078",
            framework_dir=v2_fixture["framework"],
            output_path=os.path.join(v2_fixture["derived"], "turn-context.json"),
        )
        loc_ids = [loc["id"] for loc in result.get("scene_locations", [])]
        assert "loc-camp" in loc_ids


# ---------------------------------------------------------------------------
# Test: nearby entities recency
# ---------------------------------------------------------------------------

class TestNearbyEntities:
    def test_nearby_within_threshold(self, v2_fixture):
        """Entity updated 6 turns ago (within default 10) → in nearby."""
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        # char-elder last updated turn-072, current turn-078 → 6 turns ago
        # Mark char-elder as NOT in scene
        scene_ids = {"char-player"}
        nearby = build_nearby_summary(id_lookup, scene_ids, "turn-078", 10)
        nearby_ids = [e["id"] for e in nearby]
        assert "char-elder" in nearby_ids

    def test_nearby_excluded_when_old(self, v2_fixture):
        """Entity updated 20 turns ago → NOT in nearby with threshold 10."""
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        # char-ghost last updated turn-010, current turn-078 → 68 turns ago
        scene_ids = {"char-player"}
        nearby = build_nearby_summary(id_lookup, scene_ids, "turn-078", 10)
        nearby_ids = [e["id"] for e in nearby]
        assert "char-ghost" not in nearby_ids


# ---------------------------------------------------------------------------
# Test: output validates against schema
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def test_output_validates_against_schema(self, v2_fixture):
        _write_turn(
            v2_fixture["transcript"], "turn-078",
            "The elder speaks to Fenouille Moonwind about the forest."
        )
        result = build_context(
            session_dir=v2_fixture["session"],
            turn_id="turn-078",
            framework_dir=v2_fixture["framework"],
            output_path=os.path.join(v2_fixture["derived"], "turn-context.json"),
        )

        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "turn-context.schema.json"
        )
        if not os.path.isfile(schema_path):
            pytest.skip("turn-context.schema.json not found")

        jsonschema = pytest.importorskip("jsonschema")

        with open(schema_path, "r", encoding="utf-8-sig") as f:
            schema = json.load(f)

        jsonschema.validate(result, schema)


# ---------------------------------------------------------------------------
# Test: empty catalogs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_catalogs_graceful(self, tmp_path):
        """No per-entity files → empty arrays, no crash."""
        session = tmp_path / "session"
        transcript = session / "transcript"
        transcript.mkdir(parents=True)
        derived = session / "derived"
        derived.mkdir()

        framework = tmp_path / "framework"
        catalogs = framework / "catalogs"
        for d in _V2_DIRNAMES:
            dpath = catalogs / d
            dpath.mkdir(parents=True)
            (dpath / "index.json").write_text("[]", encoding="utf-8")

        _write_turn(str(transcript), "turn-001", "Nothing happens.")

        result = build_context(
            session_dir=str(session),
            turn_id="turn-001",
            framework_dir=str(framework),
            output_path=str(derived / "turn-context.json"),
        )
        assert result["scene_entities"] == []
        assert result.get("scene_locations", []) == [] or "scene_locations" not in result

    def test_missing_turn_file_error(self, v2_fixture):
        """Nonexistent turn → clear error."""
        with pytest.raises(FileNotFoundError, match="No transcript files found"):
            build_context(
                session_dir=v2_fixture["session"],
                turn_id="turn-999",
                framework_dir=v2_fixture["framework"],
            )


# ---------------------------------------------------------------------------
# Test: word boundary matching
# ---------------------------------------------------------------------------

class TestWordBoundary:
    def test_short_name_not_matched_inside_word(self, v2_fixture):
        """'the' inside 'other' should not match 'the elder' or similar."""
        # Add an entity with a short-ish name that could be a substring
        chars_dir = os.path.join(v2_fixture["catalogs"], "characters")
        index_path = os.path.join(chars_dir, "index.json")
        with open(index_path, "r", encoding="utf-8-sig") as f:
            index_data = json.load(f)
        # "the elder" is multi-word, so it does full-phrase match
        # Let's test single-word: "elder"
        index_data.append({
            "id": "char-test-elder",
            "name": "Elder",
            "type": "character",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        })
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2)

        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])

        # "Elder" should match in text with word boundary
        mentions = find_mentions("The Elder spoke.", name_lookup, id_lookup)
        assert "char-test-elder" in mentions

        # "elder" inside "beelder" should NOT match (word boundary)
        mentions2 = find_mentions("The beelder was confused.", name_lookup, id_lookup)
        assert "char-test-elder" not in mentions2

    def test_case_insensitive_word_boundary(self, v2_fixture):
        """'Elder' matches 'elder' via case-insensitive matching."""
        chars_dir = os.path.join(v2_fixture["catalogs"], "characters")
        index_path = os.path.join(chars_dir, "index.json")
        with open(index_path, "r", encoding="utf-8-sig") as f:
            index_data = json.load(f)
        index_data.append({
            "id": "char-test-elder2",
            "name": "Elder",
            "type": "character",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        })
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2)

        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        mentions = find_mentions("The elder spoke softly.", name_lookup, id_lookup)
        assert "char-test-elder2" in mentions

    def test_id_substring_no_false_positive(self, v2_fixture):
        """'char-player' should NOT match inside 'char-player2'."""
        # Add char-player2 to the index
        chars_dir = os.path.join(v2_fixture["catalogs"], "characters")
        index_path = os.path.join(chars_dir, "index.json")
        with open(index_path, "r", encoding="utf-8-sig") as f:
            index_data = json.load(f)
        index_data.append({
            "id": "char-player2",
            "name": "Second Player",
            "type": "character",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        })
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2)

        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        # Text mentions only char-player2 — char-player should NOT match
        mentions = find_mentions("See char-player2 for details.", name_lookup, id_lookup)
        assert "char-player2" in mentions
        assert "char-player" not in mentions

    def test_multiword_no_false_positive_in_compound(self, v2_fixture):
        """'the camp' should NOT match inside 'the campfire'."""
        name_lookup, id_lookup = load_indexes(v2_fixture["catalogs"])
        # "the camp" is a location name in the fixture
        mentions = find_mentions("They gathered around the campfire.", name_lookup, id_lookup)
        assert "loc-camp" not in mentions

    def test_invalid_turn_id_raises(self, v2_fixture):
        """Invalid turn ID format raises ValueError."""
        _write_turn(v2_fixture["transcript"], "turn-078", "Hello.")
        with pytest.raises(ValueError, match="Invalid turn ID"):
            build_context(
                session_dir=v2_fixture["session"],
                turn_id="bad-id",
                framework_dir=v2_fixture["framework"],
            )


# Need the import for empty catalogs test
from build_context import _V2_DIRNAMES
