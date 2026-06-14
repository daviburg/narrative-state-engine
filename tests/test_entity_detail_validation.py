"""Tests for entity_detail validation observability (#503) and the
schema-driven sanitize/recover step (#504)."""

import json
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
    se._drain_validation_repairs()  # ensure a clean recovery buffer (#504)
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


# ---------------------------------------------------------------------------
# Part C — Item 1: relationship recovery is GENERIC / schema-driven
# ---------------------------------------------------------------------------

class TestSchemaDrivenGenericArrayRecovery:
    """The incomplete-item drop must be discovered from the schema for ANY
    array property whose items declare required sub-fields — not hardcoded to
    ``relationships`` (PR #507 adversarial P1)."""

    def test_generic_array_property_other_than_relationships(self, monkeypatch):
        """A non-``relationships`` required-item array is sanitized identically."""
        # Synthetic schema: a DIFFERENT array property ("members") whose items
        # have required sub-fields, plus an optional string and NO relationships
        # field at all — proving the repair is not tied to that literal name.
        synthetic_schema = {
            "type": "object",
            "required": ["id", "name"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "summary": {"type": "string"},  # optional string
                "members": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["member_id", "role"],
                    },
                },
            },
        }
        monkeypatch.setattr(se, "_load_schema", lambda _name: synthetic_schema)

        complete = {"member_id": "m-1", "role": "leader", "extra": "kept"}
        incomplete = {"member_id": "m-2"}  # missing required "role"
        entity = {
            "id": "faction-x",
            "name": "Faction X",
            "summary": None,  # null optional string -> dropped
            "members": [complete, incomplete],
        }
        out = se._sanitize_entity_for_validation(entity)

        # The incomplete member is dropped even though the field is NOT named
        # "relationships" — the discovery is generic.
        assert out["members"] == [complete]
        # The optional null string is still dropped generically.
        assert "summary" not in out

    def test_multiple_required_item_arrays_all_sanitized(self, monkeypatch):
        """Every required-item array property is sanitized, not just the first."""
        synthetic_schema = {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "links": {
                    "type": "array",
                    "items": {"type": "object", "required": ["to"]},
                },
                "events": {
                    "type": "array",
                    "items": {"type": "object", "required": ["when"]},
                },
                # An array WITHOUT required item sub-fields must be left intact.
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        }
        monkeypatch.setattr(se, "_load_schema", lambda _name: synthetic_schema)

        good_link = {"to": "a"}
        good_event = {"when": "turn-001"}
        entity = {
            "id": "x",
            "links": [good_link, {"note": "no to"}],
            "events": [good_event, {"note": "no when"}],
            "tags": ["t1", "t2"],
        }
        out = se._sanitize_entity_for_validation(entity)
        assert out["links"] == [good_link]
        assert out["events"] == [good_event]
        # No required item sub-fields -> untouched.
        assert out["tags"] == ["t1", "t2"]


# ---------------------------------------------------------------------------
# Part D — Item 2: recovery exercised at the solo / batched / backfill /
# refresh call sites (not just the PC path) (PR #507 adversarial P2)
# ---------------------------------------------------------------------------

def _recoverable_entity(entity_id="char-foe", name="Foe"):
    """A char entity with recoverable violations that validate after sanitizing.

    Includes ``current_status: None`` — an optional string-typed top-level key
    the #505 ``_coerce_entity_fields`` step does **not** pre-strip, so it
    survives to the #504 sanitizer and exercises (and logs) a genuine recovery
    at the real call sites.  ``notes: None`` and the incomplete relationship are
    also recoverable but are now handled upstream by #505's coerce (notes-null
    removal + relationship-key strip) before the sanitizer sees them.
    """
    return {
        "id": entity_id,
        "name": name,
        "type": "character",
        "identity": "A rival adventurer.",
        "current_status": None,  # recoverable, survives coerce -> sanitizer drops + logs
        "notes": None,  # recoverable; #505 coerce removes notes=null upstream
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-001",
        "relationships": [
            _valid_relationship("char-player"),
            {"target_id": "char-ally", "current_relationship": "wary",
             "type": "social"},  # missing first_seen_turn; #505 coerce strips rels
        ],
    }


def _unrepairable_entity(entity_id="char-foe", name="Foe"):
    """A char entity with an unrepairable violation (required identity is a
    number) that is rejected and recorded."""
    return {
        "id": entity_id,
        "name": name,
        "type": "character",
        "identity": 123,  # required string -> unrepairable
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-001",
    }


def _detail_llm(response):
    llm = MagicMock()
    llm.delay = MagicMock()
    llm.extract_json = MagicMock(return_value=response)
    return llm


def _patch_detail_prompts(monkeypatch):
    monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
    monkeypatch.setattr(se, "format_detail_prompt", lambda *a, **k: "detail prompt")
    monkeypatch.setattr(se, "format_detail_batch_prompt", lambda *a, **k: "batch prompt")


def _foe_ref(entity_id="char-foe", name="Foe"):
    return {"name": name, "type": "character", "existing_id": entity_id,
            "is_new": False}


class TestRecoveryCallSites:
    # --- solo path (_extract_single_entity_detail, line ~3727) ---
    def test_solo_recovers_recoverable(self, monkeypatch):
        _patch_detail_prompts(monkeypatch)
        se._reset_validation_failures()
        llm = _detail_llm({"entity": _recoverable_entity()})
        ref = _foe_ref()
        rref, data, err = se._extract_single_entity_detail(
            llm, {"turn_id": "turn-010"}, ref, None, None,
        )
        assert err is None
        assert data is not None
        assert "notes" not in data  # #505 coerce removed notes=null
        assert "relationships" not in data  # #505 coerce stripped rel keys
        assert "current_status" not in data  # #504 sanitizer recovered the null
        assert se._drain_validation_failures() == []
        se._drain_validation_repairs()

    def test_solo_records_unrepairable(self, monkeypatch):
        _patch_detail_prompts(monkeypatch)
        se._reset_validation_failures()
        llm = _detail_llm({"entity": _unrepairable_entity()})
        rref, data, err = se._extract_single_entity_detail(
            llm, {"turn_id": "turn-011"}, _foe_ref(), None, None,
        )
        assert data is None and err is None  # rejected, not an LLM error
        recorded = se._drain_validation_failures()
        assert len(recorded) == 1
        assert recorded[0]["turn"] == "turn-011"
        assert recorded[0]["entity_id"] == "char-foe"
        assert recorded[0]["phase"] == "entity_detail"

    # --- batched path (_extract_batched_entity_detail, line ~3960) ---
    def test_batched_recovers_recoverable(self, monkeypatch):
        _patch_detail_prompts(monkeypatch)
        se._reset_validation_failures()
        ref = _foe_ref()
        llm = _detail_llm({"entities": [_recoverable_entity()]})
        results, fallback = se._extract_batched_entity_detail(
            llm, {"turn_id": "turn-020"}, [(ref, None)], None,
        )
        assert fallback == []
        assert len(results) == 1
        _ref, data, err = results[0]
        assert err is None and data is not None
        assert "notes" not in data  # #505 coerce removed notes=null
        assert "relationships" not in data  # #505 coerce stripped rel keys
        assert "current_status" not in data  # #504 sanitizer recovered the null
        assert se._drain_validation_failures() == []
        se._drain_validation_repairs()

    def test_batched_records_unrepairable(self, monkeypatch):
        _patch_detail_prompts(monkeypatch)
        se._reset_validation_failures()
        ref = _foe_ref()
        llm = _detail_llm({"entities": [_unrepairable_entity()]})
        results, fallback = se._extract_batched_entity_detail(
            llm, {"turn_id": "turn-021"}, [(ref, None)], None,
        )
        # Invalid -> batch records a failure, falls back to a solo retry (which
        # also rejects + records).  The entity ends up in fallback and yields no
        # merged data in results.
        assert len(fallback) == 1
        assert all(data is None for _ref, data, _err in results)
        # Both records carry the batched turn id; assert the batched-phase
        # record is present and correctly attributed.
        recorded = se._drain_validation_failures()
        assert recorded, "expected at least one recorded failure"
        phases = {r["phase"] for r in recorded}
        assert "entity_detail_batch" in phases
        assert all(r["turn"] == "turn-021" for r in recorded)
        assert all(r["entity_id"] == "char-foe" for r in recorded)

    # --- backfill path (backfill_stubs, line ~6091) ---
    def test_backfill_records_unrepairable(self, monkeypatch, tmp_path):
        _patch_detail_prompts(monkeypatch)
        catalogs = _fresh_catalogs()
        stub = {
            "id": "char-foe", "name": "Foe", "type": "character",
            "identity": "",  # empty -> detected as stub
            "first_seen_turn": "turn-001",
            "notes": "Stub - needs backfill.",
        }
        catalogs["characters.json"].append(stub)
        turn_dicts = [{"turn_id": "turn-001", "speaker": "dm",
                       "text": "Foe appears in the hall."}]
        llm = _detail_llm({"entity": _unrepairable_entity()})
        log_path = tmp_path / "extraction-log.jsonl"

        n = se.backfill_stubs(turn_dicts, catalogs, [], llm,
                              extraction_log_path=str(log_path))
        assert n == 0  # unrepairable -> not backfilled
        lines = [json.loads(line) for line in
                 log_path.read_text().splitlines() if line.strip()]
        failures = [f for rec in lines for f in rec.get("validation_failures", [])]
        assert len(failures) == 1
        assert failures[0]["turn"] == "turn-001"
        assert failures[0]["entity_id"] == "char-foe"
        assert failures[0]["phase"] == "entity_detail_backfill"

    def test_backfill_recovers_recoverable(self, monkeypatch, tmp_path):
        _patch_detail_prompts(monkeypatch)
        catalogs = _fresh_catalogs()
        stub = {
            "id": "char-foe", "name": "Foe", "type": "character",
            "identity": "", "first_seen_turn": "turn-001",
            "notes": "Stub - needs backfill.",
        }
        catalogs["characters.json"].append(stub)
        turn_dicts = [{"turn_id": "turn-001", "speaker": "dm",
                       "text": "Foe appears in the hall."}]
        llm = _detail_llm({"entity": _recoverable_entity()})
        log_path = tmp_path / "extraction-log.jsonl"

        n = se.backfill_stubs(turn_dicts, catalogs, [], llm,
                              extraction_log_path=str(log_path))
        assert n == 1  # recovered + merged
        # No validation_failures flushed.
        if log_path.exists():
            lines = [json.loads(line) for line in
                     log_path.read_text().splitlines() if line.strip()]
            assert all("validation_failures" not in rec for rec in lines)

    # --- refresh path (refresh_entities, line ~6559) ---
    def test_refresh_records_unrepairable(self, monkeypatch, tmp_path):
        _patch_detail_prompts(monkeypatch)
        catalogs = _fresh_catalogs()
        stale = {
            "id": "char-foe", "name": "Foe", "type": "character",
            "identity": "A rival.", "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        catalogs["characters.json"].append(stale)
        turn_dicts = [{"turn_id": "turn-005", "speaker": "dm",
                       "text": "char-foe returns to the hall."}]
        llm = _detail_llm({"entity": _unrepairable_entity()})
        log_path = tmp_path / "extraction-log.jsonl"

        n = se.refresh_entities([("characters.json", stale)], "turn-005",
                                turn_dicts, catalogs, llm,
                                extraction_log_path=str(log_path))
        assert n == 0
        lines = [json.loads(line) for line in
                 log_path.read_text().splitlines() if line.strip()]
        failures = [f for rec in lines for f in rec.get("validation_failures", [])]
        assert len(failures) == 1
        assert failures[0]["turn"] == "turn-005"
        assert failures[0]["entity_id"] == "char-foe"
        assert failures[0]["phase"] == "entity_detail_refresh"

    def test_refresh_recovers_recoverable(self, monkeypatch, tmp_path):
        _patch_detail_prompts(monkeypatch)
        catalogs = _fresh_catalogs()
        stale = {
            "id": "char-foe", "name": "Foe", "type": "character",
            "identity": "A rival.", "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        catalogs["characters.json"].append(stale)
        turn_dicts = [{"turn_id": "turn-005", "speaker": "dm",
                       "text": "char-foe returns to the hall."}]
        llm = _detail_llm({"entity": _recoverable_entity()})
        log_path = tmp_path / "extraction-log.jsonl"

        n = se.refresh_entities([("characters.json", stale)], "turn-005",
                                turn_dicts, catalogs, llm,
                                extraction_log_path=str(log_path))
        assert n == 1
        if log_path.exists():
            lines = [json.loads(line) for line in
                     log_path.read_text().splitlines() if line.strip()]
            assert all("validation_failures" not in rec for rec in lines)


# ---------------------------------------------------------------------------
# Part E — Item 3: validation-failure buffer is thread-safe / per-call
# ---------------------------------------------------------------------------

class TestValidationFailureConcurrency:
    def test_worker_records_into_submitting_call_buffer(self):
        """A ThreadPoolExecutor worker's failure lands in the SUBMITTING call's
        per-call buffer (contextvars propagation via _submit_in_context)."""
        import concurrent.futures

        se._reset_validation_failures()

        def _worker(turn_id):
            se._record_validation_failure(turn_id, "e", "entity_detail", "err")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [se._submit_in_context(pool, _worker, f"turn-{i:03d}")
                       for i in range(8)]
            for f in futures:
                f.result()

        drained = se._drain_validation_failures()
        assert len(drained) == 8
        assert {r["turn"] for r in drained} == {f"turn-{i:03d}" for i in range(8)}

    def test_concurrent_extract_and_merge_no_cross_attribution(self, monkeypatch):
        """Concurrent extract_and_merge() calls (as in retry_failed_turns'
        ThreadPoolExecutor) never drain, lose, or mis-attribute each other's
        validation failures."""
        import concurrent.futures

        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")

        def _bad_pc(turn_id):
            return {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": 123,  # unrepairable -> recorded under this turn
                "first_seen_turn": turn_id,
                "last_updated_turn": turn_id,
            }

        def _run(turn_id):
            se._reset_pc_failure_tracking()
            llm = _make_stub_llm(_bad_pc(turn_id))
            catalogs = _fresh_catalogs()
            turn = {"turn_id": turn_id, "speaker": "dm", "text": "The DM speaks."}
            _c, _e, _f, log = se.extract_and_merge(
                turn, catalogs, [], llm, min_confidence=0.6,
            )
            return turn_id, log

        turn_ids = [f"turn-{i:03d}" for i in range(12)]
        # Fewer workers than tasks forces thread reuse, exercising the
        # reset-on-reuse isolation.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_run, turn_ids))

        for turn_id, log in results:
            failures = log.get("validation_failures", [])
            assert len(failures) == 1, (
                f"{turn_id}: expected exactly its own failure, got {failures}")
            assert failures[0]["turn"] == turn_id
            assert failures[0]["entity_id"] == "char-player"


# ---------------------------------------------------------------------------
# Part F — Recovery logging (#504): repairs are surfaced as validation_repairs,
# distinct from validation_failures, thread-safe, and measurement-only.
# ---------------------------------------------------------------------------

class TestRecoveryLogging:
    def test_optional_null_emits_repair_and_merges(self, monkeypatch):
        """A current_status:null PC emits a validation_repairs record AND still
        merges (the recovery is unchanged — only newly logged).  current_status
        is an optional string-typed top-level key that #505's coerce does NOT
        pre-strip, so it reaches the #504 sanitizer at the real call site."""
        pc = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "current_status": None,  # recoverable -> dropped + logged
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        catalogs, log = _run_turn(monkeypatch, pc)

        assert "validation_repairs" in log
        repairs = log["validation_repairs"]
        assert len(repairs) == 1
        rec = repairs[0]
        assert rec["turn"] == "turn-001"
        assert rec["entity_id"] == "char-player"
        assert rec["phase"] == "entity_detail"
        assert rec["repair"] == "dropped null optional-string key"
        assert rec["path"] == "current_status"
        assert "missing" not in rec
        # Recovered -> NOT recorded as a failure.
        assert "validation_failures" not in log
        # The entity still merged (recovery unchanged).
        pcs = [e for e in catalogs["characters.json"] if e.get("id") == "char-player"]
        assert len(pcs) == 1
        assert "current_status" not in pcs[0]

    def test_incomplete_relationship_emits_repair_record(self):
        """An incomplete array item -> a repairs record with the dropped item
        path + the missing required sub-field.  Driven through
        ``_validate_entity_detail()`` directly (the #504 sanitizer) because
        #505's coerce strips relationship keys wholesale before the call
        sites — the per-item recovery + logging still applies to any
        required-item array the schema declares."""
        se._reset_validation_failures()
        se._reset_validation_repairs()
        entity = {
            "id": "char-eldorman",
            "name": "Eldorman",
            "type": "character",
            "identity": "A rival.",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
            "relationships": [
                _valid_relationship("char-player"),
                # missing required first_seen_turn -> dropped + logged
                {"target_id": "char-ally", "current_relationship": "wary",
                 "type": "social"},
            ],
        }
        ok = se._validate_entity_detail(
            entity, turn="turn-029", entity_id="char-eldorman",
            phase="entity_detail",
        )
        assert ok is True
        assert len(entity["relationships"]) == 1  # the incomplete one was dropped

        repairs = se._drain_validation_repairs()
        assert len(repairs) == 1
        rec = repairs[0]
        assert rec["turn"] == "turn-029"
        assert rec["entity_id"] == "char-eldorman"
        assert rec["phase"] == "entity_detail"
        assert rec["repair"] == (
            "dropped incomplete array item missing required subfield")
        assert rec["path"] == "relationships[1]"
        assert rec["missing"] == "first_seen_turn"
        # Recovered -> NOT recorded as a failure.
        assert se._drain_validation_failures() == []

    def test_clean_entity_emits_no_repair_record(self, monkeypatch):
        """A clean entity (no repair needed) -> NO validation_repairs key."""
        good_pc = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        _catalogs, log = _run_turn(monkeypatch, good_pc)
        assert "validation_repairs" not in log
        assert "validation_failures" not in log

    def test_repair_logging_does_not_change_repair_output(self):
        """Measurement-only: sanitizing WITH vs WITHOUT the repairs out-param
        yields a byte-identical repaired entity (logging never changes the
        repair).  Uses the real entity schema."""
        import copy

        base = {
            "id": "char-foe",
            "name": "Foe",
            "type": "character",
            "identity": "A rival.",
            "notes": None,  # recoverable
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
            "relationships": [
                _valid_relationship("char-player"),
                {"target_id": "char-ally", "current_relationship": "wary",
                 "type": "social"},  # incomplete -> dropped
            ],
        }
        without = copy.deepcopy(base)
        with_log = copy.deepcopy(base)

        out_without = se._sanitize_entity_for_validation(without)  # no logging
        captured: list[dict] = []
        out_with = se._sanitize_entity_for_validation(with_log, captured)

        assert json.dumps(out_without, sort_keys=True) == \
            json.dumps(out_with, sort_keys=True)
        # Repairs were captured ONLY in the logging path (two: null key + rel).
        assert len(captured) == 2

    def test_unrepairable_records_failure_not_repair(self, monkeypatch):
        """Distinct: an unrepairable entity -> validation_failures (NOT a
        repairs record)."""
        _patch_detail_prompts(monkeypatch)
        se._reset_validation_failures()
        se._reset_validation_repairs()
        llm = _detail_llm({"entity": _unrepairable_entity()})
        _rref, data, _err = se._extract_single_entity_detail(
            llm, {"turn_id": "turn-040"}, _foe_ref(), None, None,
        )
        assert data is None
        assert len(se._drain_validation_failures()) == 1
        assert se._drain_validation_repairs() == []

    def test_recoverable_records_repair_not_failure(self, monkeypatch):
        """Distinct: a recoverable entity -> validation_repairs (NOT a failures
        record).  At the call site #505's coerce removes notes=null and strips
        the relationship keys, so the #504 sanitizer's surviving recovery is the
        current_status null-drop — which is what gets logged."""
        _patch_detail_prompts(monkeypatch)
        se._reset_validation_failures()
        se._reset_validation_repairs()
        llm = _detail_llm({"entity": _recoverable_entity()})
        _rref, data, _err = se._extract_single_entity_detail(
            llm, {"turn_id": "turn-041"}, _foe_ref(), None, None,
        )
        assert data is not None
        assert se._drain_validation_failures() == []
        repairs = se._drain_validation_repairs()
        assert len(repairs) == 1
        rec = repairs[0]
        assert rec["repair"] == "dropped null optional-string key"
        assert rec["path"] == "current_status"
        assert rec["turn"] == "turn-041"
        assert rec["entity_id"] == "char-foe"


class TestRecoveryLoggingConcurrency:
    def test_worker_records_repair_into_submitting_call_buffer(self):
        """A ThreadPoolExecutor worker's repair lands in the SUBMITTING call's
        per-call repairs buffer (contextvars propagation, no cross-attribution)."""
        import concurrent.futures

        se._reset_validation_repairs()

        def _worker(turn_id):
            se._record_validation_repairs(
                turn_id, "e", "entity_detail",
                [{"repair": "dropped null optional-string key", "path": "notes"}],
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [se._submit_in_context(pool, _worker, f"turn-{i:03d}")
                       for i in range(8)]
            for f in futures:
                f.result()

        drained = se._drain_validation_repairs()
        assert len(drained) == 8
        assert {r["turn"] for r in drained} == {f"turn-{i:03d}" for i in range(8)}
        assert all(r["path"] == "notes" for r in drained)

    def test_concurrent_recovery_no_cross_attribution(self, monkeypatch):
        """Concurrent extract_and_merge() calls (retry_failed_turns'
        ThreadPoolExecutor) attribute each turn's recovery to that turn only."""
        import concurrent.futures

        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")

        def _recoverable_pc(turn_id):
            return {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "current_status": None,  # recoverable, survives coerce -> logged
                "first_seen_turn": turn_id,
                "last_updated_turn": turn_id,
            }

        def _run(turn_id):
            se._reset_pc_failure_tracking()
            llm = _make_stub_llm(_recoverable_pc(turn_id))
            catalogs = _fresh_catalogs()
            turn = {"turn_id": turn_id, "speaker": "dm", "text": "The DM speaks."}
            _c, _e, _f, log = se.extract_and_merge(
                turn, catalogs, [], llm, min_confidence=0.6,
            )
            return turn_id, log

        turn_ids = [f"turn-{i:03d}" for i in range(12)]
        # Fewer workers than tasks forces thread reuse, exercising reset-on-reuse.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_run, turn_ids))

        for turn_id, log in results:
            repairs = log.get("validation_repairs", [])
            assert len(repairs) == 1, (
                f"{turn_id}: expected exactly its own repair, got {repairs}")
            assert repairs[0]["turn"] == turn_id
            assert repairs[0]["entity_id"] == "char-player"
            assert repairs[0]["path"] == "current_status"
            # Recovered -> no failure for this turn.
            assert "validation_failures" not in log


class TestRecoveryLoggingPostTurnPasses:
    def test_backfill_flushes_repairs(self, monkeypatch, tmp_path):
        """The backfill pass flushes recoveries as validation_repairs and still
        backfills the recovered stub."""
        _patch_detail_prompts(monkeypatch)
        catalogs = _fresh_catalogs()
        stub = {
            "id": "char-foe", "name": "Foe", "type": "character",
            "identity": "", "first_seen_turn": "turn-001",
            "notes": "Stub - needs backfill.",
        }
        catalogs["characters.json"].append(stub)
        turn_dicts = [{"turn_id": "turn-001", "speaker": "dm",
                       "text": "Foe appears in the hall."}]
        llm = _detail_llm({"entity": _recoverable_entity()})
        log_path = tmp_path / "extraction-log.jsonl"

        n = se.backfill_stubs(turn_dicts, catalogs, [], llm,
                              extraction_log_path=str(log_path))
        assert n == 1  # recovered + merged
        lines = [json.loads(line) for line in
                 log_path.read_text().splitlines() if line.strip()]
        repairs = [r for rec in lines for r in rec.get("validation_repairs", [])]
        assert len(repairs) == 1  # current_status null-drop (notes+rels handled by #505 coerce)
        assert all(r["turn"] == "turn-001" for r in repairs)
        assert all(r["entity_id"] == "char-foe" for r in repairs)
        assert all(r["phase"] == "entity_detail_backfill" for r in repairs)
        assert all(r["path"] == "current_status" for r in repairs)
        # Recovered -> no failures flushed.
        assert all("validation_failures" not in rec for rec in lines)

    def test_refresh_flushes_repairs(self, monkeypatch, tmp_path):
        """The refresh pass flushes recoveries as validation_repairs."""
        _patch_detail_prompts(monkeypatch)
        catalogs = _fresh_catalogs()
        stale = {
            "id": "char-foe", "name": "Foe", "type": "character",
            "identity": "A rival.", "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        catalogs["characters.json"].append(stale)
        turn_dicts = [{"turn_id": "turn-005", "speaker": "dm",
                       "text": "char-foe returns to the hall."}]
        llm = _detail_llm({"entity": _recoverable_entity()})
        log_path = tmp_path / "extraction-log.jsonl"

        n = se.refresh_entities([("characters.json", stale)], "turn-005",
                                turn_dicts, catalogs, llm,
                                extraction_log_path=str(log_path))
        assert n == 1
        lines = [json.loads(line) for line in
                 log_path.read_text().splitlines() if line.strip()]
        repairs = [r for rec in lines for r in rec.get("validation_repairs", [])]
        assert len(repairs) == 1  # current_status null-drop (notes+rels handled by #505 coerce)
        assert all(r["phase"] == "entity_detail_refresh" for r in repairs)
        assert all(r["path"] == "current_status" for r in repairs)
        assert all("validation_failures" not in rec for rec in lines)
