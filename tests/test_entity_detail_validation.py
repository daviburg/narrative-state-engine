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


# ---------------------------------------------------------------------------
# Part B — #504 schema-driven sanitize + recover
# ---------------------------------------------------------------------------

def _valid_relationship(target_id="char-foe"):
    return {
        "target_id": target_id,
        "current_relationship": "allies",
        "type": "social",
        "first_seen_turn": "turn-001",
    }


class TestSanitizeForValidation:
    def test_drops_null_optional_string_keys(self):
        """null-valued optional string keys are omitted; other fields intact."""
        entity = {
            "id": "char-ally",
            "name": "Ally",
            "type": "character",
            "identity": "An ally.",
            "current_status": None,  # optional string -> drop
            "notes": None,           # optional string -> drop
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        out = se._sanitize_entity_for_validation(entity)
        assert "notes" not in out
        assert "current_status" not in out
        # Unaffected fields are not mutated.
        assert out["identity"] == "An ally."
        assert out["name"] == "Ally"
        # The sanitized remainder validates.
        ok, _ = se._validate_entity_detailed(out)
        assert ok is True

    def test_keeps_required_null_so_validation_rejects(self):
        """A required field set to null is NOT stripped (unrepairable)."""
        entity = {
            "id": "char-ally",
            "name": "Ally",
            "type": "character",
            "identity": None,  # required string -> must remain (unrepairable)
            "first_seen_turn": "turn-001",
        }
        out = se._sanitize_entity_for_validation(entity)
        assert "identity" in out  # not stripped
        ok, _ = se._validate_entity_detailed(out)
        assert ok is False

    def test_drops_incomplete_relationship_keeps_valid(self):
        """Incomplete relationship entries are dropped; valid ones + status kept."""
        good = _valid_relationship("char-player")
        incomplete = {
            "target_id": "char-foe",
            "current_relationship": "enemies",
            "type": "adversarial",
            # missing required first_seen_turn
        }
        entity = {
            "id": "char-ally",
            "name": "Ally",
            "type": "character",
            "identity": "An ally.",
            "current_status": "Standing guard.",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
            "relationships": [good, incomplete],
        }
        out = se._sanitize_entity_for_validation(entity)
        assert out["relationships"] == [good]  # only the valid one survives
        assert out["current_status"] == "Standing guard."  # status preserved
        ok, _ = se._validate_entity_detailed(out)
        assert ok is True

    def test_does_not_mutate_valid_entity(self):
        """A fully valid entity is returned unchanged."""
        entity = {
            "id": "char-ally",
            "name": "Ally",
            "type": "character",
            "identity": "An ally.",
            "current_status": "Active.",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
            "relationships": [_valid_relationship()],
        }
        import copy
        before = copy.deepcopy(entity)
        out = se._sanitize_entity_for_validation(entity)
        assert out == before


class TestValidateEntityDetailRecovery:
    def test_recoverable_violation_validates_and_records_nothing(self, monkeypatch):
        """A recoverable entity passes validation and records no failure."""
        se._drain_validation_failures()
        entity = {
            "id": "char-ally",
            "name": "Ally",
            "type": "character",
            "identity": "An ally.",
            "notes": None,  # recoverable
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
            "relationships": [
                _valid_relationship("char-player"),
                {"target_id": "char-foe", "current_relationship": "enemies",
                 "type": "adversarial"},  # incomplete -> dropped
            ],
        }
        ok = se._validate_entity_detail(
            entity, turn="turn-005", entity_id="char-ally", phase="entity_detail",
        )
        assert ok is True
        assert "notes" not in entity
        assert len(entity["relationships"]) == 1
        assert se._drain_validation_failures() == []

    def test_unrepairable_violation_rejected_and_recorded(self, monkeypatch):
        """A missing required top-level field is still rejected and recorded."""
        se._drain_validation_failures()
        entity = {
            "id": "char-ally",
            "name": "Ally",
            "type": "character",
            # identity (required) missing -> unrepairable
            "first_seen_turn": "turn-001",
        }
        ok = se._validate_entity_detail(
            entity, turn="turn-006", entity_id="char-ally", phase="entity_detail",
        )
        assert ok is False
        recorded = se._drain_validation_failures()
        assert len(recorded) == 1
        assert recorded[0]["turn"] == "turn-006"
        assert recorded[0]["entity_id"] == "char-ally"
        assert "identity" in recorded[0]["error"]


class TestRecoveryEndToEnd:
    def test_notes_null_pc_merges_without_recording(self, monkeypatch):
        """A notes:null char-player detail now merges instead of being dropped."""
        pc = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "notes": None,  # recoverable violation
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        catalogs, log = _run_turn(monkeypatch, pc)
        # No failure recorded (it was recovered).
        assert "validation_failures" not in log
        # The PC entity merged into the characters catalog.
        pcs = [e for e in catalogs["characters.json"] if e.get("id") == "char-player"]
        assert len(pcs) == 1
        assert pcs[0].get("notes") in (None, "") or "notes" not in pcs[0]

