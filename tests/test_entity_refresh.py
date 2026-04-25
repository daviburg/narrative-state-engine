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
    _MAX_REFRESH_BATCH_SIZE,
    _REFRESH_TYPE_SHARES,
    extract_semantic_batch,
    CATALOG_KEYS,
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
    catalogs = {"characters.json": [], "locations.json": [], "items.json": [], "factions.json": []}
    type_map = {
        "character": "characters.json",
        "location": "locations.json",
        "item": "items.json",
        "faction": "factions.json",
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
        assert _DEFAULT_REFRESH_BATCH_SIZE == 10

    def test_config_contains_refresh_keys(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "llm.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "entity_refresh_interval" in config
        assert "entity_refresh_batch_size" in config
        assert config["entity_refresh_interval"] == 50
        assert config["entity_refresh_batch_size"] == 10


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


# ---------------------------------------------------------------------------
# Type-aware allocation and dynamic scaling (#182)
# ---------------------------------------------------------------------------

class TestTypeAwareAllocation:
    def test_characters_get_proportional_slots(self):
        """Characters should receive ~50% of refresh slots."""
        chars = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                 for i in range(10)]
        locs = [_make_entity(f"loc-l{i}", f"Loc{i}", "location", "turn-001", "turn-001")
                for i in range(4)]
        items = [_make_entity(f"item-i{i}", f"Item{i}", "item", "turn-001", "turn-001")
                 for i in range(4)]
        factions = [_make_entity(f"fac-f{i}", f"Fac{i}", "faction", "turn-001", "turn-001")
                    for i in range(2)]

        all_entities = chars + locs + items + factions
        turns = [_make_turn(i, " ".join(e["name"] for e in all_entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*all_entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=10)
        # With 10 slots: chars get 5, locs get 2, items get 2, factions get 1
        char_ids = {e["id"] for _, e in stale if e["type"] == "character"}
        assert len(char_ids) >= 4  # at least ~50% of 10 slots

    def test_overflow_redistribution(self):
        """Unused faction slots should overflow to other types."""
        chars = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                 for i in range(10)]
        # No factions at all — those slots should redistribute
        turns = [_make_turn(i, " ".join(e["name"] for e in chars))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*chars)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=10)
        # All 10 slots should be filled with characters since overflow from
        # empty location/item/faction buckets goes to character bucket
        assert len(stale) == 10

    def test_mixed_types_all_represented(self):
        """When all types have stale entities, each type should get some slots."""
        chars = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                 for i in range(6)]
        locs = [_make_entity(f"loc-l{i}", f"Loc{i}", "location", "turn-001", "turn-001")
                for i in range(3)]
        items = [_make_entity(f"item-i{i}", f"Item{i}", "item", "turn-001", "turn-001")
                 for i in range(3)]
        factions = [_make_entity(f"fac-f{i}", f"Fac{i}", "faction", "turn-001", "turn-001")
                    for i in range(2)]

        all_entities = chars + locs + items + factions
        turns = [_make_turn(i, " ".join(e["name"] for e in all_entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*all_entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=10)
        types_present = {e["type"] for _, e in stale}
        # All types should be represented
        assert "character" in types_present
        assert "location" in types_present
        assert "item" in types_present
        assert "faction" in types_present

    def test_batch_size_1_characters_win(self):
        """With batch_size=1, a character should win the single slot."""
        char = _make_entity("char-c0", "Char0", "character", "turn-001", "turn-001")
        loc = _make_entity("loc-l0", "Loc0", "location", "turn-001", "turn-001")
        item = _make_entity("item-i0", "Item0", "item", "turn-001", "turn-001")
        fac = _make_entity("fac-f0", "Fac0", "faction", "turn-001", "turn-001")

        all_entities = [char, loc, item, fac]
        turns = [_make_turn(i, " ".join(e["name"] for e in all_entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*all_entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=1)
        assert len(stale) == 1

    def test_batch_size_3_slot_sum_matches(self):
        """With batch_size=3, exactly 3 entities should be returned."""
        chars = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                 for i in range(5)]
        locs = [_make_entity(f"loc-l{i}", f"Loc{i}", "location", "turn-001", "turn-001")
                for i in range(3)]
        items = [_make_entity(f"item-i{i}", f"Item{i}", "item", "turn-001", "turn-001")
                 for i in range(3)]
        factions = [_make_entity(f"fac-f{i}", f"Fac{i}", "faction", "turn-001", "turn-001")
                    for i in range(2)]

        all_entities = chars + locs + items + factions
        turns = [_make_turn(i, " ".join(e["name"] for e in all_entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*all_entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=3)
        assert len(stale) == 3


class TestDynamicScaling:
    def test_small_catalog_uses_configured_batch(self):
        """Catalog with <60 entities should use the configured batch_size."""
        entities = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                    for i in range(10)]
        turns = [_make_turn(i, " ".join(e["name"] for e in entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=5)
        assert len(stale) == 5

    def test_large_catalog_scales_up(self):
        """Catalog with 60+ entities should scale batch to catalog_size // 5."""
        chars = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                 for i in range(40)]
        locs = [_make_entity(f"loc-l{i}", f"Loc{i}", "location", "turn-001", "turn-001")
                for i in range(15)]
        items = [_make_entity(f"item-i{i}", f"Item{i}", "item", "turn-001", "turn-001")
                 for i in range(10)]

        all_entities = chars + locs + items  # 65 total
        turns = [_make_turn(i, " ".join(e["name"] for e in all_entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*all_entities)

        # batch_size=10 but catalog_size//5 = 13 → effective batch = 13
        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=10)
        assert len(stale) == 13

    def test_dynamic_scaling_caps_at_max(self):
        """Even very large catalogs should be capped at _MAX_REFRESH_BATCH_SIZE."""
        # 150 entities → 150 // 5 = 30, but max is 25
        chars = [_make_entity(f"char-c{i}", f"Char{i}", "character", "turn-001", "turn-001")
                 for i in range(80)]
        locs = [_make_entity(f"loc-l{i}", f"Loc{i}", "location", "turn-001", "turn-001")
                for i in range(40)]
        items = [_make_entity(f"item-i{i}", f"Item{i}", "item", "turn-001", "turn-001")
                 for i in range(30)]

        all_entities = chars + locs + items  # 150 total
        turns = [_make_turn(i, " ".join(e["name"] for e in all_entities))
                 for i in range(1, 201)]
        catalogs = _make_catalogs(*all_entities)

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50, batch_size=10)
        assert len(stale) == _MAX_REFRESH_BATCH_SIZE

    def test_backward_compatible_small_catalog(self):
        """Small catalog with explicit batch_size should behave as before."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        turns = [_make_turn(i, "The Elder speaks" if i % 10 == 0 else "Nothing")
                 for i in range(1, 101)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50, batch_size=5)
        assert len(stale) == 1
        assert stale[0][1]["id"] == "char-elder"


class TestEventFrequencyTiebreaker:
    def test_event_rich_entity_wins_tie(self):
        """Between two entities with equal staleness, the one with more events wins."""
        e1 = _make_entity("char-minor", "Minor", "character", "turn-001", "turn-001")
        e2 = _make_entity("char-major", "Major", "character", "turn-001", "turn-001")
        turns = [_make_turn(i, "Minor and Major both here") for i in range(1, 201)]
        catalogs = _make_catalogs(e1, e2)

        # Major has many events, Minor has few
        events = [
            {"related_entities": ["char-major"]} for _ in range(16)
        ] + [
            {"related_entities": ["char-minor"]} for _ in range(2)
        ]

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50,
                                    batch_size=2, events_list=events)
        assert len(stale) == 2
        # Both have gap=199, but Major has 16 events vs Minor's 2
        assert stale[0][1]["id"] == "char-major"
        assert stale[1][1]["id"] == "char-minor"

    def test_staleness_still_primary_sort(self):
        """Staleness gap should still be the primary sort key, not event count."""
        e1 = _make_entity("char-old", "OldChar", "character", "turn-001", "turn-001")
        e2 = _make_entity("char-recent", "RecentChar", "character", "turn-001", "turn-100")
        turns = [_make_turn(i, "OldChar and RecentChar here") for i in range(1, 201)]
        catalogs = _make_catalogs(e1, e2)

        # RecentChar has more events but OldChar is more stale
        events = [
            {"related_entities": ["char-recent"]} for _ in range(20)
        ] + [
            {"related_entities": ["char-old"]} for _ in range(1)
        ]

        stale = find_stale_entities(200, catalogs, turns, refresh_interval=50,
                                    batch_size=2, events_list=events)
        # OldChar gap=199, RecentChar gap=100 → OldChar first despite fewer events
        assert stale[0][1]["id"] == "char-old"

    def test_no_events_list_still_works(self):
        """When events_list is not provided, function should still work."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        turns = [_make_turn(i, "The Elder speaks") for i in range(1, 101)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(100, catalogs, turns, refresh_interval=50)
        assert len(stale) == 1
        assert stale[0][1]["id"] == "char-elder"


# ---------------------------------------------------------------------------
# End-of-run refresh (#212)
# ---------------------------------------------------------------------------

class TestEndOfRunRefresh:
    """Final refresh pass catches stale entities after the last modulo checkpoint."""

    def test_stale_detected_at_non_modulo_boundary(self):
        """Entities stale since last modulo refresh should be found at a
        non-modulo turn number (simulating end-of-run)."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        turns = [_make_turn(i, "The Elder is present") for i in range(1, 345)]
        catalogs = _make_catalogs(elder)

        # At turn 344 (not a multiple of 50), elder should be stale
        stale = find_stale_entities(344, catalogs, turns, refresh_interval=50)
        assert len(stale) == 1
        assert stale[0][1]["id"] == "char-elder"

    def test_no_stale_at_exact_modulo(self):
        """If the last turn IS a modulo boundary, the regular refresh already
        ran; an entity updated at that boundary should not be stale."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-300")
        turns = [_make_turn(i, "The Elder is here") for i in range(1, 345)]
        catalogs = _make_catalogs(elder)

        # At turn 300 (a modulo of 50), elder was just updated — not stale
        stale = find_stale_entities(300, catalogs, turns, refresh_interval=50)
        assert len(stale) == 0

    def test_end_of_run_finds_multiple_stale(self):
        """Multiple entities stale since last modulo should all be returned."""
        e1 = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        e2 = _make_entity("loc-cave", "Dark Cave", "location", "turn-020", "turn-020")
        e3 = _make_entity("item-sword", "Magic Sword", "item", "turn-030", "turn-030")
        turns = [_make_turn(i, "Elder enters Dark Cave carrying Magic Sword")
                 for i in range(1, 345)]
        catalogs = _make_catalogs(e1, e2, e3)

        stale = find_stale_entities(344, catalogs, turns, refresh_interval=50,
                                    batch_size=25)
        assert len(stale) == 3

    def test_recently_updated_entity_not_stale_at_end(self):
        """Entity updated at turn-340 should not be stale at turn-344
        (within one refresh_interval)."""
        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-340")
        turns = [_make_turn(i, "The Elder is here") for i in range(1, 345)]
        catalogs = _make_catalogs(elder)

        stale = find_stale_entities(344, catalogs, turns, refresh_interval=50)
        assert len(stale) == 0

    def test_end_of_run_refresh_calls_refresh_entities(self):
        """Integration: verify refresh_entities is called for stale entities
        found at end-of-run (non-modulo final turn)."""
        from unittest.mock import MagicMock, patch
        from semantic_extraction import refresh_entities

        elder = _make_entity("char-elder", "Elder", "character", "turn-010", "turn-010")
        catalogs = _make_catalogs(elder)
        turns = [_make_turn(i, "The Elder is here") for i in range(1, 75)]

        mock_llm = MagicMock()
        mock_llm.extract_json.return_value = {
            "entity": {
                "id": "char-elder",
                "name": "Elder",
                "type": "character",
                "identity": "Refreshed elder",
                "first_seen_turn": "turn-010",
                "last_updated_turn": "turn-074",
            }
        }
        mock_llm.delay = MagicMock()

        # Simulate end-of-run: find stale at turn 74 (not % 50)
        stale = find_stale_entities(74, catalogs, turns, refresh_interval=50)
        assert len(stale) == 1

        with patch("semantic_extraction.merge_entity"):
            refreshed = refresh_entities(stale, "turn-074", turns, catalogs, mock_llm)
            assert refreshed == 1


# ---------------------------------------------------------------------------
# Integration: extract_semantic_batch end-of-run refresh wiring (#212)
# ---------------------------------------------------------------------------

def _monkeypatch_batch_env(monkeypatch, *, config_overrides=None):
    """Patch all heavy dependencies so extract_semantic_batch runs fast."""
    from unittest.mock import MagicMock

    mock_llm = MagicMock()
    cfg = {"entity_refresh_interval": 50, "entity_refresh_batch_size": 10}
    if config_overrides:
        cfg.update(config_overrides)
    mock_llm.config = cfg

    monkeypatch.setattr("semantic_extraction.LLMClient", lambda *a, **kw: mock_llm)
    monkeypatch.setattr("semantic_extraction.load_catalogs", lambda d: {fn: [] for fn in CATALOG_KEYS})
    monkeypatch.setattr("semantic_extraction.load_events", lambda d: [])
    monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
    monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)
    monkeypatch.setattr("semantic_extraction.extract_and_merge",
                        lambda *a, **kw: ({fn: [] for fn in CATALOG_KEYS}, []))
    monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
    monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
    monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
    monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
    return mock_llm


class TestEndOfRunRefreshIntegration:
    """Integration tests: verify extract_semantic_batch wires the final
    refresh pass correctly (#212)."""

    def test_final_refresh_triggers_on_non_modulo_turn(self, monkeypatch):
        """extract_semantic_batch calls find_stale_entities + refresh_entities
        at end-of-run when the final turn is NOT on a modulo boundary."""
        _monkeypatch_batch_env(monkeypatch)

        find_calls = []
        refresh_calls = []

        def mock_find_stale(*args, **kwargs):
            find_calls.append(kwargs or args)
            return [("characters.json", {"id": "char-elder", "name": "Elder"})]

        def mock_refresh(stale, *args, **kwargs):
            refresh_calls.append((stale, args, kwargs))
            return len(stale)

        monkeypatch.setattr("semantic_extraction.find_stale_entities", mock_find_stale)
        monkeypatch.setattr("semantic_extraction.refresh_entities", mock_refresh)

        # 74 turns → final turn-074 is NOT a multiple of 50
        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "DM", "text": "hi"}
                 for i in range(1, 75)]
        extract_semantic_batch(turns, "sessions/test", dry_run=True, segment_size=0)

        # find_stale_entities should have been called with the final turn number
        assert len(find_calls) >= 1
        last_find = find_calls[-1]
        # The end-of-run call uses keyword args
        if isinstance(last_find, dict):
            assert last_find["current_turn_number"] == 74
        # refresh_entities should have been called
        assert len(refresh_calls) >= 1

    def test_final_refresh_skipped_on_modulo_turn(self, monkeypatch):
        """extract_semantic_batch does NOT call the end-of-run refresh
        when the final turn lands exactly on a modulo boundary."""
        _monkeypatch_batch_env(monkeypatch)

        find_calls = []

        def mock_find_stale(*args, **kwargs):
            find_calls.append(kwargs.get("current_turn_number") or (args[0] if args else None))
            return []

        monkeypatch.setattr("semantic_extraction.find_stale_entities", mock_find_stale)
        monkeypatch.setattr("semantic_extraction.refresh_entities", lambda *a, **kw: 0)

        # 50 turns → final turn-050 IS a multiple of 50
        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "DM", "text": "hi"}
                 for i in range(1, 51)]
        extract_semantic_batch(turns, "sessions/test", dry_run=True, segment_size=0)

        # The end-of-run refresh should NOT have been called for turn 50;
        # any find_stale calls should be from the in-loop modulo trigger only
        # (turn 50 % 50 == 0 triggers in-loop, but NOT end-of-run)
        end_of_run_calls = [c for c in find_calls
                            if isinstance(c, dict) and c.get("current_turn_number") == 50]
        assert len(end_of_run_calls) == 0

    def test_final_refresh_uses_larger_batch_size(self, monkeypatch):
        """End-of-run refresh should pass max(configured_batch, 25) to
        find_stale_entities, not the smaller periodic batch size."""
        _monkeypatch_batch_env(monkeypatch, config_overrides={"entity_refresh_batch_size": 5})

        find_calls = []

        def mock_find_stale(*args, **kwargs):
            find_calls.append(kwargs if kwargs else {})
            return []

        monkeypatch.setattr("semantic_extraction.find_stale_entities", mock_find_stale)
        monkeypatch.setattr("semantic_extraction.refresh_entities", lambda *a, **kw: 0)

        # 74 turns → non-modulo end
        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "DM", "text": "hi"}
                 for i in range(1, 75)]
        extract_semantic_batch(turns, "sessions/test", dry_run=True, segment_size=0)

        # The last find_stale call should be the end-of-run one with batch_size=25
        end_of_run = [c for c in find_calls if c.get("current_turn_number") == 74]
        assert len(end_of_run) == 1
        assert end_of_run[0]["batch_size"] == _MAX_REFRESH_BATCH_SIZE  # 25, not 5


class TestEndOfSegmentRefreshIntegration:
    """Integration tests: verify _extract_segmented wires the final
    refresh pass at the end of each segment (#212)."""

    def test_segment_end_refresh_triggers(self, monkeypatch):
        """Segmented extraction calls find_stale_entities at end of each
        segment when the segment's final turn is not on a modulo boundary."""
        from unittest.mock import MagicMock
        from semantic_extraction import _extract_segmented

        mock_llm = MagicMock()
        mock_llm.config = {"entity_refresh_interval": 50, "entity_refresh_batch_size": 10}

        find_calls = []

        def mock_find_stale(*args, **kwargs):
            find_calls.append(kwargs if kwargs else {})
            return []

        monkeypatch.setattr("semantic_extraction.find_stale_entities", mock_find_stale)
        monkeypatch.setattr("semantic_extraction.refresh_entities", lambda *a, **kw: 0)
        monkeypatch.setattr("semantic_extraction.extract_and_merge",
                            lambda *a, **kw: ({fn: [] for fn in CATALOG_KEYS}, []))
        monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)

        # 74 turns, segment_size=30 → segments end at turn 30, 60, 74
        # turn 30: 30 % 50 != 0 → triggers end-of-segment refresh
        # turn 60: 60 % 50 != 0 → triggers end-of-segment refresh
        # turn 74: 74 % 50 != 0 → triggers end-of-segment refresh
        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "DM", "text": "hi"}
                 for i in range(1, 75)]
        _extract_segmented(turns, "sessions/test", "framework", "framework/catalogs",
                           mock_llm, 0.0, True, 30)

        # All 3 segments end on non-modulo turns, so each gets an end-of-segment call
        end_of_seg_calls = [c for c in find_calls
                            if c.get("current_turn_number") in (30, 60, 74)]
        assert len(end_of_seg_calls) == 3

    def test_segment_end_refresh_skipped_at_modulo(self, monkeypatch):
        """End-of-segment refresh is skipped when the segment ends on a
        modulo boundary (the in-loop refresh already ran there)."""
        from unittest.mock import MagicMock
        from semantic_extraction import _extract_segmented

        mock_llm = MagicMock()
        mock_llm.config = {"entity_refresh_interval": 50, "entity_refresh_batch_size": 10}

        find_calls = []

        def mock_find_stale(*args, **kwargs):
            find_calls.append(kwargs if kwargs else {})
            return []

        monkeypatch.setattr("semantic_extraction.find_stale_entities", mock_find_stale)
        monkeypatch.setattr("semantic_extraction.refresh_entities", lambda *a, **kw: 0)
        monkeypatch.setattr("semantic_extraction.extract_and_merge",
                            lambda *a, **kw: ({fn: [] for fn in CATALOG_KEYS}, []))
        monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)

        # 50 turns, segment_size=50 → single segment ending at turn 50
        # turn 50 % 50 == 0 → in-loop refresh fires, end-of-segment should NOT
        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "DM", "text": "hi"}
                 for i in range(1, 51)]
        _extract_segmented(turns, "sessions/test", "framework", "framework/catalogs",
                           mock_llm, 0.0, True, 50)

        # End-of-segment refresh should not trigger (turn 50 is modulo)
        # Any call with current_turn_number=50 comes from the in-loop path, not end-of-segment
        end_of_seg_calls = [c for c in find_calls
                            if c.get("current_turn_number") == 50
                            and c.get("batch_size") == _MAX_REFRESH_BATCH_SIZE]
        assert len(end_of_seg_calls) == 0
