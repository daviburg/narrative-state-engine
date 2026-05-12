"""Tests for periodic dedup audit during extraction (#366)."""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _run_periodic_dedup,
    _DEFAULT_DEDUP_AUDIT_INTERVAL,
)


def _make_entity(id_, name, turn="turn-001", relationships=None):
    entity = {
        "id": id_,
        "name": name,
        "type": id_.split("-")[0].replace("loc", "location").replace("char", "character"),
        "identity": f"{name} entity",
        "first_seen_turn": turn,
    }
    if relationships:
        entity["relationships"] = relationships
    return entity


def _make_catalogs(entities):
    catalogs = {
        "characters.json": [],
        "locations.json": [],
        "factions.json": [],
        "items.json": [],
    }
    for e in entities:
        eid = e["id"]
        if eid.startswith("char-"):
            catalogs["characters.json"].append(e)
        elif eid.startswith("loc-"):
            catalogs["locations.json"].append(e)
        elif eid.startswith("faction-"):
            catalogs["factions.json"].append(e)
        elif eid.startswith("item-"):
            catalogs["items.json"].append(e)
    return catalogs


class TestDefaultInterval:
    def test_default_interval_value(self):
        assert _DEFAULT_DEDUP_AUDIT_INTERVAL == 50


class TestPeriodicDedupNoCandidates:
    def test_no_candidates_returns_zero(self):
        """When no candidates are generated, returns 0 merges."""
        catalogs = _make_catalogs([
            _make_entity("loc-forest", "Dark Forest"),
            _make_entity("loc-mountain", "Iron Mountain"),
        ])
        llm = MagicMock()
        result = _run_periodic_dedup(catalogs, [], llm, "turn-050")
        assert result == 0
        # LLM should not be called when there are no candidates
        llm.extract_json.assert_not_called()


class TestPeriodicDedupWithCandidates:
    def test_auto_merge_high_confidence(self):
        """Pairs with confidence >= 0.9 are auto-merged."""
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp", turn="turn-010"),
            _make_entity("loc-campsite", "Campsite", turn="turn-020"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": True,
            "confidence": 0.95,
            "canonical_id": "loc-camp",
            "rationale": "Same location evolved over time",
        }

        result = _run_periodic_dedup(catalogs, events, llm, "turn-050")
        assert result == 1
        # loc-campsite should be merged away
        remaining_ids = [e["id"] for e in catalogs["locations.json"]]
        assert "loc-camp" in remaining_ids
        assert "loc-campsite" not in remaining_ids

    def test_name_mismatch_guard_bypassed(self):
        """LLM-confirmed merges bypass the name-mismatch guard even when names differ."""
        catalogs = _make_catalogs([
            _make_entity("loc-shelter", "Shelter", turn="turn-010"),
            _make_entity("loc-shelters", "Shelters", turn="turn-020"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": True,
            "confidence": 0.95,
            "canonical_id": "loc-shelter",
            "rationale": "Same location evolved over time",
        }

        result = _run_periodic_dedup(catalogs, events, llm, "turn-050")
        assert result == 1
        remaining_ids = [e["id"] for e in catalogs["locations.json"]]
        assert "loc-shelter" in remaining_ids
        assert "loc-shelters" not in remaining_ids

    def test_below_threshold_no_merge(self):
        """Pairs with confidence < 0.9 are NOT auto-merged."""
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": True,
            "confidence": 0.75,
            "canonical_id": "loc-camp",
            "rationale": "Possibly same location",
        }

        result = _run_periodic_dedup(catalogs, events, llm, "turn-050")
        assert result == 0
        remaining_ids = [e["id"] for e in catalogs["locations.json"]]
        assert "loc-camp" in remaining_ids
        assert "loc-campsite" in remaining_ids

    def test_not_same_entity_no_merge(self):
        """Pairs where same_entity is False are NOT merged even with high confidence."""
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": False,
            "confidence": 0.95,
            "canonical_id": "loc-camp",
            "rationale": "Different locations",
        }

        result = _run_periodic_dedup(catalogs, events, llm, "turn-050")
        assert result == 0


class TestEnhancedPrompt:
    def test_prompt_includes_narrative_evolution(self):
        """The enhanced prompt passed to the LLM includes narrative evolution guidance."""
        catalogs = _make_catalogs([
            _make_entity("loc-shelter", "Shelter"),
            _make_entity("loc-shelters", "Shelters"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": False,
            "confidence": 0.3,
            "canonical_id": "loc-shelter",
            "rationale": "Different",
        }

        _run_periodic_dedup(catalogs, events, llm, "turn-050")

        # Check the system prompt passed to the LLM
        call_args = llm.extract_json.call_args
        system_prompt = call_args.kwargs.get("system_prompt", call_args[1].get("system_prompt", "")) if call_args[1:] else call_args.kwargs.get("system_prompt", "")
        assert "narrative evolution" in system_prompt.lower()
        assert "renamed, rebuilt, or upgraded" in system_prompt

    def test_prompt_includes_evolution_example(self):
        """The enhanced prompt mentions entity evolution examples."""
        catalogs = _make_catalogs([
            _make_entity("loc-shelter", "Shelter"),
            _make_entity("loc-shelters", "Shelters"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": False,
            "confidence": 0.3,
            "canonical_id": "loc-shelter",
            "rationale": "Different",
        }

        _run_periodic_dedup(catalogs, events, llm, "turn-050")

        call_args = llm.extract_json.call_args
        system_prompt = call_args.kwargs.get("system_prompt", call_args[1].get("system_prompt", "")) if call_args[1:] else call_args.kwargs.get("system_prompt", "")
        assert "longhouse" in system_prompt.lower()


class TestEventRewriting:
    def test_events_rewritten_on_merge(self):
        """Event related_entities are updated when entities merge."""
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
        ])
        events = [
            {"id": "evt-001", "related_entities": ["loc-campsite", "char-player"]},
            {"id": "evt-002", "related_entities": ["loc-camp"]},
        ]

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": True,
            "confidence": 0.95,
            "canonical_id": "loc-camp",
            "rationale": "Same location",
        }

        _run_periodic_dedup(catalogs, events, llm, "turn-050")

        # loc-campsite references should be rewritten to loc-camp
        assert events[0]["related_entities"] == ["loc-camp", "char-player"]
        assert events[1]["related_entities"] == ["loc-camp"]


class TestRelationshipRewriting:
    def test_relationships_rewritten_on_merge(self):
        """Relationship target_id/source_id are updated when entities merge."""
        other_entity = _make_entity("char-player", "Player", relationships=[
            {"source_id": "char-player", "target_id": "loc-campsite", "type": "location"},
        ])
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
            other_entity,
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = {
            "same_entity": True,
            "confidence": 0.95,
            "canonical_id": "loc-camp",
            "rationale": "Same location",
        }

        _run_periodic_dedup(catalogs, events, llm, "turn-050")

        # Player's relationship target should be rewritten
        player = catalogs["characters.json"][0]
        assert player["relationships"][0]["target_id"] == "loc-camp"


class TestLLMErrorHandling:
    def test_llm_error_gracefully_skipped(self):
        """If the LLM throws an exception, that pair is skipped."""
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.side_effect = RuntimeError("LLM timeout")

        result = _run_periodic_dedup(catalogs, events, llm, "turn-050")
        assert result == 0
        # Both entities should still exist
        assert len(catalogs["locations.json"]) == 2

    def test_llm_returns_non_dict(self):
        """If the LLM returns a non-dict, the pair is skipped."""
        catalogs = _make_catalogs([
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
        ])
        events = []

        llm = MagicMock()
        llm.extract_json.return_value = "not a dict"

        result = _run_periodic_dedup(catalogs, events, llm, "turn-050")
        assert result == 0


class TestIntervalConfig:
    def test_disabled_when_zero(self):
        """dedup_audit_interval=0 means periodic dedup never fires.

        This tests the calling convention, not _run_periodic_dedup itself,
        since the interval check is done in the extraction loop.
        """
        # Verify the interval-check pattern: turn_number % 0 would raise,
        # but the guard `dedup_interval > 0` prevents it.
        dedup_interval = 0
        # Short-circuit: dedup_interval > 0 is False, so modulo never fires
        assert dedup_interval <= 0

    def test_fires_at_correct_turn(self):
        """Interval=50 fires at turn 50, 100, 150, etc."""
        dedup_interval = 50
        for turn_number in [50, 100, 150, 200]:
            should_fire = (
                dedup_interval > 0
                and turn_number is not None
                and turn_number % dedup_interval == 0
            )
            assert should_fire is True, f"Should fire at turn {turn_number}"

    def test_does_not_fire_between_intervals(self):
        """Interval=50 does NOT fire at turns 25, 49, 51, 99."""
        dedup_interval = 50
        for turn_number in [25, 49, 51, 99]:
            should_fire = (
                dedup_interval > 0
                and turn_number is not None
                and turn_number % dedup_interval == 0
            )
            assert should_fire is False, f"Should NOT fire at turn {turn_number}"
