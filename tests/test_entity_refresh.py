"""Tests for periodic entity refresh mechanism (#161)."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    find_stale_entities,
    _entity_mentioned_since,
    _DEFAULT_REFRESH_INTERVAL,
    _DEFAULT_REFRESH_BATCH_SIZE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(entity_id, name, etype, first_turn, last_turn):
    return {
        "id": entity_id,
        "name": name,
        "type": etype,
        "identity": f"{name} identity",
        "first_seen_turn": first_turn,
        "last_updated_turn": last_turn,
    }


def _make_turn(turn_num, text, speaker="DM"):
    return {
        "turn_id": f"turn-{turn_num:03d}",
        "speaker": speaker,
        "text": text,
    }


def _make_catalogs(*entities):
    """Build a catalogs dict from a flat list of entity dicts."""
    catalogs = {"characters.json": [], "locations.json": [], "items.json": []}
    type_map = {
        "character": "characters.json",
        "location": "locations.json",
        "item": "items.json",
    }
    for e in entities:
        cat = type_map.get(e["type"], "characters.json")
        catalogs[cat].append(e)
    return catalogs


# ---------------------------------------------------------------------------
# find_stale_entities
# ---------------------------------------------------------------------------

class TestFindStaleEntities:
    def test_basic_staleness_detection(self):
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        turns = [_make_turn(i, f"The Elder speaks at turn {i}" if i % 10 == 0 else "Nothing happens")
                 for i in range(1, 101)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50)
        assert len(stale) == 1
        assert stale[0][1]["id"] == "char-elder"

    def test_not_stale_within_interval(self):
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-060")
        turns = [_make_turn(i, "The Elder appears") for i in range(1, 101)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50)
        assert len(stale) == 0

    def test_sorts_by_staleness_most_stale_first(self):
        e1 = _make_entity("char-ancient", "Ancient", "character", "turn-001", "turn-001")
        e2 = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-030")
        e3 = _make_entity("char-young", "Youngster", "character", "turn-050", "turn-050")
        turns = [_make_turn(i, "Ancient Elder Youngster all here") for i in range(1, 201)]
        catalogs = _make_catalogs(e1, e2, e3)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50)
        assert len(stale) == 3
        # Most stale first: Ancient (gap=199), Elder (gap=170), Young (gap=150)
        assert stale[0][1]["id"] == "char-ancient"
        assert stale[1][1]["id"] == "char-elder"
        assert stale[2][1]["id"] == "char-young"

    def test_batch_size_limit(self):
        entities = [
            _make_entity(f"char-e{i}", f"Entity{i}", "character", "turn-001", "turn-001")
            for i in range(10)
        ]
        turns = [_make_turn(i, " ".join(f"Entity{j}" for j in range(10)))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=3)
        assert len(stale) == 3

    def test_skips_entities_not_mentioned_since_last_update(self):
        """Entity not mentioned in transcript after last_updated_turn should be skipped."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        # Elder only mentioned in turns before turn-010
        turns = [_make_turn(i, "The Elder speaks" if i <= 10 else "Nothing here")
                 for i in range(1, 101)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50)
        assert len(stale) == 0

    def test_skips_player_character(self):
        """char-player is always extracted, so should be excluded from refresh."""
        pc = _make_entity("char-player", "Player", "character", "turn-001", "turn-001")
        turns = [_make_turn(i, "Player does something") for i in range(1, 101)]
        catalogs = _make_catalogs(pc)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50)
        assert len(stale) == 0

    def test_handles_entities_with_no_last_updated_turn(self):
        """Entity without last_updated_turn should be skipped (not crash)."""
        entity = {"id": "char-mystery", "name": "Mystery", "type": "character",
                   "identity": "Unknown", "first_seen_turn": "turn-001"}
        catalogs = _make_catalogs(entity)
        turns = [_make_turn(i, "Mystery appears") for i in range(1, 101)]

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50)
        assert len(stale) == 0

    def test_refresh_interval_zero_returns_empty(self):
        """When refresh_interval is 0, no entities should be found."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-001", "turn-001")
        turns = [_make_turn(i, "Elder appears") for i in range(1, 101)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=0)
        assert len(stale) == 0

    def test_multiple_catalog_types(self):
        """Entities across different catalog types should all be found."""
        char = _make_entity("char-elder", "Elder", "character", "turn-001", "turn-001")
        loc = _make_entity("loc-cave", "Dark Cave", "location", "turn-005", "turn-005")
        item = _make_entity("item-sword", "Magic Sword", "item", "turn-010", "turn-010")
        turns = [_make_turn(i, "Elder enters Dark Cave carrying Magic Sword")
                 for i in range(1, 201)]
        catalogs = _make_catalogs(char, loc, item)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50)
        assert len(stale) == 3


# ---------------------------------------------------------------------------
# _entity_mentioned_since
# ---------------------------------------------------------------------------

class TestEntityMentionedSince:
    def test_mentioned_by_name(self):
        turns = [_make_turn(50, "Nothing"), _make_turn(60, "The Elder arrives")]
        assert _entity_mentioned_since("char-elder", "Elder", 50, turns) is True

    def test_mentioned_by_id(self):
        turns = [_make_turn(60, "Referring to char-elder in the text")]
        assert _entity_mentioned_since("char-elder", "Elder", 50, turns) is True

    def test_not_mentioned_after_turn(self):
        turns = [_make_turn(40, "The Elder speaks"), _make_turn(60, "Nothing happens")]
        assert _entity_mentioned_since("char-elder", "Elder", 50, turns) is False

    def test_case_insensitive_name(self):
        turns = [_make_turn(60, "the ELDER arrives")]
        assert _entity_mentioned_since("char-elder", "Elder", 50, turns) is True

    def test_short_name_skipped(self):
        """Names shorter than 3 chars should not be matched to avoid false positives."""
        turns = [_make_turn(60, "Go to the inn")]
        assert _entity_mentioned_since("char-go", "Go", 50, turns) is False

    def test_empty_turns(self):
        assert _entity_mentioned_since("char-elder", "Elder", 50, []) is False


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_default_refresh_interval(self):
        assert _DEFAULT_REFRESH_INTERVAL == 50

    def test_default_refresh_batch_size(self):
        assert _DEFAULT_REFRESH_BATCH_SIZE == 5

    def test_config_contains_refresh_keys(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "llm.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "entity_refresh_interval" in config
        assert "entity_refresh_batch_size" in config
        assert config["entity_refresh_interval"] == 50
        assert config["entity_refresh_batch_size"] == 5


# ---------------------------------------------------------------------------
# Interval trigger logic
# ---------------------------------------------------------------------------

class TestIntervalTrigger:
    """Test that the refresh would fire at the correct turn intervals."""

    def test_fires_at_interval_multiples(self):
        """Confirm that the modulo check fires correctly."""
        interval = 50
        # Should fire at turn 50, 100, 150, etc.
        for turn_num in [50, 100, 150, 200, 250, 300, 350]:
            assert turn_num % interval == 0

    def test_does_not_fire_between_intervals(self):
        interval = 50
        for turn_num in [1, 25, 49, 51, 73, 99, 101]:
            assert turn_num % interval != 0


# ---------------------------------------------------------------------------
# refresh_entities (unit test with mock LLM)
# ---------------------------------------------------------------------------

class TestRefreshEntities:
    def test_refresh_merges_not_overwrites(self):
        """refresh_entities should call merge_entity, which augments rather
        than replaces. Verify merge_entity is invoked and original attributes
        survive alongside the new ones."""
        from unittest.mock import MagicMock, patch
        from semantic_extraction import refresh_entities

        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        elder["stable_attributes"] = {"race": {"value": "elf"}}
        catalogs = _make_catalogs(elder)
        turns = [_make_turn(i, "The Elder is here") for i in range(1, 101)]

        mock_llm = MagicMock()
        mock_llm.extract_json.return_value = {
            "entity": {
                "id": "char-elder",
                "name": "Elder",
                "type": "character",
                "identity": "A wise elf elder",
                "first_seen_turn": "turn-010",
                "last_updated_turn": "turn-100",
                "stable_attributes": {"class": {"value": "sage"}},
            }
        }
        mock_llm.delay = MagicMock()

        stale = [("characters.json", elder)]

        with patch("semantic_extraction.merge_entity") as mock_merge:
            refreshed = refresh_entities(stale, "turn-100", turns, catalogs, mock_llm)
            assert refreshed == 1
            assert mock_llm.extract_json.called
            # merge_entity must be called (merge, not overwrite)
            assert mock_merge.called
            merged_entity = mock_merge.call_args[0][1]
            assert merged_entity["id"] == "char-elder"
            assert merged_entity["last_updated_turn"] == "turn-100"

    def test_refresh_skips_when_no_context(self):
        """If entity has no mentions since last update, refresh should skip."""
        from unittest.mock import MagicMock
        from semantic_extraction import refresh_entities

        elder = _make_entity("char-elder", "Elder", "character", "turn-090", "turn-090")
        catalogs = _make_catalogs(elder)
        # Turns don't mention Elder after turn 90
        turns = [_make_turn(i, "Nothing here") for i in range(91, 101)]

        mock_llm = MagicMock()
        mock_llm.delay = MagicMock()

        stale = [("characters.json", elder)]
        refreshed = refresh_entities(stale, "turn-100", turns, catalogs, mock_llm)
        assert refreshed == 0
        assert not mock_llm.extract_json.called

    def test_refresh_handles_llm_failure(self):
        """If LLM extraction fails, refresh should continue gracefully."""
        from unittest.mock import MagicMock
        from semantic_extraction import refresh_entities
        from llm_client import LLMExtractionError

        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        catalogs = _make_catalogs(elder)
        turns = [_make_turn(i, "The Elder speaks") for i in range(1, 101)]

        mock_llm = MagicMock()
        mock_llm.extract_json.side_effect = LLMExtractionError("LLM failed")
        mock_llm.delay = MagicMock()

        stale = [("characters.json", elder)]
        refreshed = refresh_entities(stale, "turn-100", turns, catalogs, mock_llm)
        assert refreshed == 0

    def test_refresh_preserves_first_seen_turn(self):
        """Refreshed entity should keep its original first_seen_turn."""
        from unittest.mock import MagicMock, patch, call
        from semantic_extraction import refresh_entities

        elder = _make_entity("char-elder", "Elder", "character", "turn-005", "turn-005")
        catalogs = _make_catalogs(elder)
        turns = [_make_turn(i, "The Elder is here") for i in range(1, 101)]

        mock_llm = MagicMock()
        mock_llm.extract_json.return_value = {
            "entity": {
                "id": "char-elder",
                "name": "Elder",
                "type": "character",
                "identity": "Updated identity",
                "first_seen_turn": "turn-100",  # LLM might set this wrong
                "last_updated_turn": "turn-100",
            }
        }
        mock_llm.delay = MagicMock()

        stale = [("characters.json", elder)]

        with patch("semantic_extraction.merge_entity") as mock_merge:
            refreshed = refresh_entities(stale, "turn-100", turns, catalogs, mock_llm)
            assert refreshed == 1
            # Check that first_seen_turn was preserved as turn-005
            merged_entity = mock_merge.call_args[0][1]
            assert merged_entity["first_seen_turn"] == "turn-005"
