"""Tests for entity_detail validation observability (#503) and the
schema-driven sanitize/recover step (#504)."""

import os
import sys

from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_stub_llm(pc_entity):
    """Build a stub LLM that returns *pc_entity* from the detail phase.

    Discovery returns no extra entities, so the only detail extraction is the
    always-on char-player pass; relationship/event phases return empty.
    """
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        prompt_lower = system_prompt.lower()
        if "discover" in prompt_lower or "discovery" in prompt_lower:
            return {"entities": []}
        if "detail" in prompt_lower:
            return {"entity": pc_entity}
        if "relationship" in prompt_lower:
            return {"relationships": []}
        if "event" in prompt_lower:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def _run_turn(monkeypatch, pc_entity, turn_id="turn-001"):
    monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
    se._reset_pc_failure_tracking()
    se._drain_validation_failures()  # ensure a clean buffer
    llm = _make_stub_llm(pc_entity)
    catalogs = _fresh_catalogs()
    turn = {"turn_id": turn_id, "speaker": "dm", "text": "The DM speaks."}
    catalogs, _events, _failed, log = se.extract_and_merge(
        turn, catalogs, [], llm, min_confidence=0.6,
    )
    return catalogs, log


# ---------------------------------------------------------------------------
# Part A — #503 observability
# ---------------------------------------------------------------------------

class TestValidationFailureObservability:
    def test_unrepairable_failure_recorded(self, monkeypatch):
        """A required-field type violation records a structured failure."""
        # identity is a required string; a number is an unrepairable violation.
        bad_pc = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": 123,
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        _catalogs, log = _run_turn(monkeypatch, bad_pc)

        assert "validation_failures" in log
        failures = log["validation_failures"]
        assert len(failures) == 1
        rec = failures[0]
        assert rec["turn"] == "turn-001"
        assert rec["entity_id"] == "char-player"
        assert rec["phase"] == "entity_detail"
        # error carries the offending JSON path + message
        assert "identity" in rec["error"]
        assert "is not of type" in rec["error"]

    def test_success_records_no_failure(self, monkeypatch):
        """A clean detail completion produces no validation_failures key."""
        good_pc = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        _catalogs, log = _run_turn(monkeypatch, good_pc)
        assert "validation_failures" not in log

    def test_recorded_count_matches_warning_count(self, monkeypatch, capsys):
        """The recorded failure count matches the stderr WARNING count."""
        bad_pc = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": 123,
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        _catalogs, log = _run_turn(monkeypatch, bad_pc)
        warnings = [
            line for line in capsys.readouterr().err.splitlines()
            if "failed schema validation" in line
        ]
        assert len(log.get("validation_failures", [])) == len(warnings) == 1
