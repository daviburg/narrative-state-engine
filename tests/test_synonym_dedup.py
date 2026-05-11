"""Tests for same-turn synonym explosion dedup gate (#337)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _same_turn_dedup_gate


def _make_proposal(name, etype="location", turn="turn-169", is_new=True):
    return {
        "name": name,
        "type": etype,
        "is_new": is_new,
        "source_turn": turn,
        "proposed_id": f"loc-{name.lower().replace(' ', '-')}" if is_new else None,
        "existing_id": None if is_new else f"loc-{name.lower().replace(' ', '-')}",
        "confidence": 0.8,
    }


class TestSameTurnDedupGate:
    def test_14_synonyms_reduced_to_1(self):
        entities = [
            _make_proposal("camouflaged entrance"),
            _make_proposal("defensible place"),
            _make_proposal("defensible sanctuary"),
            _make_proposal("hidden location"),
            _make_proposal("hidden refuge"),
            _make_proposal("the hidden sanctuary"),
            _make_proposal("the protected location"),
            _make_proposal("sanctuary for the vulnerable"),
            _make_proposal("safe haven"),
            _make_proposal("safe location"),
            _make_proposal("safety sanctuary"),
            _make_proposal("secure location"),
            _make_proposal("strategic location"),
            _make_proposal("strategic sanctuary"),
        ]
        result = _same_turn_dedup_gate(entities, threshold=3)
        new_locations = [e for e in result if e["is_new"] and e["type"] == "location"]
        assert len(new_locations) == 1
        assert len(new_locations[0]["name"]) == max(len(e["name"]) for e in entities)

    def test_below_threshold_untouched(self):
        entities = [
            _make_proposal("the forest"),
            _make_proposal("the river"),
            _make_proposal("the camp"),
        ]
        result = _same_turn_dedup_gate(entities, threshold=3)
        assert len(result) == 3

    def test_existing_entities_preserved(self):
        entities = [
            _make_proposal("hidden refuge"),
            _make_proposal("safe haven"),
            _make_proposal("secure location"),
            _make_proposal("strategic sanctuary"),
            _make_proposal("known place", is_new=False),
        ]
        result = _same_turn_dedup_gate(entities, threshold=3)
        existing = [e for e in result if not e["is_new"]]
        assert len(existing) == 1

    def test_different_types_counted_separately(self):
        entities = [
            _make_proposal("place a", etype="location"),
            _make_proposal("place b", etype="location"),
            _make_proposal("place c", etype="location"),
            _make_proposal("place d", etype="location"),
            _make_proposal("person a", etype="character"),
            _make_proposal("person b", etype="character"),
            _make_proposal("person c", etype="character"),
            _make_proposal("person d", etype="character"),
        ]
        result = _same_turn_dedup_gate(entities, threshold=3)
        new_locs = [e for e in result if e["type"] == "location" and e["is_new"]]
        new_chars = [e for e in result if e["type"] == "character" and e["is_new"]]
        assert len(new_locs) == 1
        assert len(new_chars) == 1

    def test_different_turns_counted_separately(self):
        entities = [
            _make_proposal("place a", turn="turn-100"),
            _make_proposal("place b", turn="turn-100"),
            _make_proposal("place c", turn="turn-100"),
            _make_proposal("place d", turn="turn-100"),
            _make_proposal("place e", turn="turn-200"),
            _make_proposal("place f", turn="turn-200"),
        ]
        result = _same_turn_dedup_gate(entities, threshold=3)
        turn_100 = [e for e in result if e["source_turn"] == "turn-100" and e["is_new"]]
        turn_200 = [e for e in result if e["source_turn"] == "turn-200" and e["is_new"]]
        assert len(turn_100) == 1
        assert len(turn_200) == 2
