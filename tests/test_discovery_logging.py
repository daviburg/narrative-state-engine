"""Tests for discovery proposal and filter logging in extraction log (#250)."""

import os
import sys

from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_stub_llm(discovery_entities=None):
    """Build a stub LLM that returns the given discovery entities."""
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    if discovery_entities is None:
        discovery_entities = []

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None, schema=None, temperature=None):
        prompt_lower = system_prompt.lower()
        if "discover" in prompt_lower or "discovery" in prompt_lower:
            return {"entities": discovery_entities}
        if "detail" in prompt_lower:
            return {"entity": {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-001",
            }}
        if "relationship" in prompt_lower:
            return {"relationships": []}
        if "event" in prompt_lower:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


class TestDiscoveryProposalsLogged:
    """discovery_proposals field captures all entities proposed by the model."""

    def test_proposals_logged(self, monkeypatch):
        """All proposed entities appear in discovery_proposals with correct fields."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        entities = [
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
            {"name": "Village", "is_new": True, "proposed_id": "loc-village",
             "type": "location", "confidence": 0.8, "source_turn": "turn-001"},
        ]
        llm = _make_stub_llm(discovery_entities=entities)
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "The elder stands in the village."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        assert "discovery_proposals" in log
        proposals = log["discovery_proposals"]
        assert len(proposals) == 2
        assert proposals[0]["name"] == "Elder"
        assert proposals[0]["is_new"] is True
        assert proposals[0]["proposed_id"] == "char-elder"
        assert proposals[0]["existing_id"] is None  # not provided by model
        assert proposals[0]["confidence"] == 0.9
        assert proposals[1]["name"] == "Village"
        assert proposals[1]["confidence"] == 0.8

    def test_empty_discovery(self, monkeypatch):
        """Empty discovery returns empty proposals and filtered lists."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(discovery_entities=[])
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Nothing happens."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        assert log["discovery_proposals"] == []
        assert log["discovery_filtered"] == []

    def test_existing_entity_in_proposals(self, monkeypatch):
        """An existing entity (is_new=false) is captured in proposals."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        entities = [
            {"name": "Elder", "is_new": False, "existing_id": "char-elder",
             "type": "character", "confidence": 0.95, "source_turn": "turn-002"},
        ]
        llm = _make_stub_llm(discovery_entities=entities)
        turn = {"turn_id": "turn-002", "speaker": "dm", "text": "The elder returns."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        proposals = log["discovery_proposals"]
        assert len(proposals) == 1
        assert proposals[0]["is_new"] is False
        assert proposals[0]["existing_id"] == "char-elder"
        assert proposals[0]["proposed_id"] is None  # not provided for existing entities


class TestDiscoveryFilteredLogged:
    """discovery_filtered field tracks rejection reasons."""

    def test_below_threshold_filtered(self, monkeypatch):
        """Entity below confidence threshold appears in discovery_filtered."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        entities = [
            {"name": "Ghost", "is_new": True, "proposed_id": "char-ghost",
             "type": "character", "confidence": 0.3, "source_turn": "turn-001"},
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
        ]
        llm = _make_stub_llm(discovery_entities=entities)
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "A ghost and elder appear."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        filtered = log["discovery_filtered"]
        low_conf = [f for f in filtered if f["reason"] == "below_confidence_threshold"]
        assert len(low_conf) == 1
        assert low_conf[0]["name"] == "Ghost"
        assert low_conf[0]["id"] == "char-ghost"

        # Elder should not be filtered
        assert all(f["name"] != "Elder" for f in filtered)

    def test_concept_prefix_filtered(self, monkeypatch):
        """Entity with concept- prefix ID appears in discovery_filtered."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        entities = [
            {"name": "Honor", "is_new": True, "proposed_id": "concept-honor",
             "type": "concept", "confidence": 0.9, "source_turn": "turn-001"},
        ]
        llm = _make_stub_llm(discovery_entities=entities)
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Honor is important."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        filtered = log["discovery_filtered"]
        concept = [f for f in filtered if f["reason"] == "concept_prefix"]
        assert len(concept) == 1
        assert concept[0]["name"] == "Honor"
        assert concept[0]["id"] == "concept-honor"

    def test_both_filter_reasons(self, monkeypatch):
        """Entities filtered for different reasons each get correct reason."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        entities = [
            {"name": "Fog", "is_new": True, "proposed_id": "char-fog",
             "type": "character", "confidence": 0.2, "source_turn": "turn-001"},
            {"name": "Bravery", "is_new": True, "proposed_id": "concept-bravery",
             "type": "concept", "confidence": 0.95, "source_turn": "turn-001"},
            {"name": "Knight", "is_new": True, "proposed_id": "char-knight",
             "type": "character", "confidence": 0.85, "source_turn": "turn-001"},
        ]
        llm = _make_stub_llm(discovery_entities=entities)
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "A fog, bravery, and a knight."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        filtered = log["discovery_filtered"]
        reasons = {f["name"]: f["reason"] for f in filtered}
        assert reasons["Fog"] == "below_confidence_threshold"
        assert reasons["Bravery"] == "concept_prefix"
        assert "Knight" not in reasons

    def test_non_list_entities_handled(self, monkeypatch):
        """Non-list entities value triggers defensive fallback."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm()
        # Override to return entities as a dict instead of list
        llm.extract_json = lambda **kw: (
            {"entities": {"bad": "shape"}} if "discover" in kw.get("system_prompt", "").lower()
            else {"events": []}
        )
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Malformed."}

        _, _, failed, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        assert failed is True
        assert log["discovery_ok"] is False
        assert "not a list" in log["discovery_error"]
        assert log["discovery_proposals"] == []
        assert log["discovery_filtered"] == []

    def test_non_dict_entries_stripped(self, monkeypatch):
        """Non-dict entries in the entities list are silently stripped."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        entities = [
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
            "not-a-dict",
            42,
        ]
        llm = _make_stub_llm(discovery_entities=entities)
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Mixed entries."}

        _, _, _, log = se.extract_and_merge(
            turn, _fresh_catalogs(), [], llm, min_confidence=0.6,
        )

        # Only the valid dict entity should appear
        assert len(log["discovery_proposals"]) == 1
        assert log["discovery_proposals"][0]["name"] == "Elder"
