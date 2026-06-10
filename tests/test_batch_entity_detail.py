"""Tests for L2 batched entity_detail extraction (#491).

Covers:
- Config reader (`_get_batch_detail_config`) — default OFF, defensive parsing.
- Task partition (`_partition_detail_tasks`) — flag-OFF byte-identity (all solo,
  no groups) and the safety tiering (PC / new / high-confidence stay SOLO).
- Batched prompt (`format_detail_batch_prompt`) — template + turn text emitted
  ONCE, hard per-entity delimiters, uncompacted raw path.
- Response parsing (`_parse_batch_detail_response`) — envelope / list / single.
- Batched extraction (`_extract_batched_entity_detail`) — maps back per entity,
  falls back to SOLO on parse failure and for missing/invalid entities.
- Non-blind measurement — a synthetic 3-entity turn shows the recorded
  entity_detail metric (both input_tokens and raw_input_tokens) for the batched
  call is well below the per-entity (solo) total — the field the paired A/B
  scorer aggregates.
- Per-turn scoring mode in ab_paired_score (the fair metric for L2, which
  changes the call count by design).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
import ab_paired_score as ab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_turn(text="The elder speaks of ancient times near the council fire."):
    return {"turn_id": "turn-100", "speaker": "DM", "text": text}


def _make_ref(entity_id, name=None, etype="character", is_new=False, confidence=0.5):
    return {
        "name": name or entity_id.replace("-", " ").title(),
        "type": etype,
        "existing_id": entity_id,
        "is_new": is_new,
        "confidence": confidence,
    }


def _make_entry(entity_id, name=None):
    return {
        "id": entity_id,
        "name": name or entity_id.replace("-", " ").title(),
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-099",
        "identity": "A known entity in the scene.",
        "current_status": "present",
    }


def _valid_entity(entity_id, name=None):
    """A schema-valid entity object the LLM would return."""
    return {
        "id": entity_id,
        "name": name or entity_id.replace("-", " ").title(),
        "type": "character",
        "identity": "An entity revealed in this turn.",
        "current_status": "active",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-100",
    }


_ID_RE = re.compile(r"Entity ID:\s*(\S+)")


class _FakeLLM:
    """Minimal LLM stub: distinguishes batched vs solo by the user prompt."""

    def __init__(self, batch_response=None, batch_exc=None):
        self.batch_response = batch_response
        self.batch_exc = batch_exc
        self.batch_calls = 0
        self.solo_calls = 0

    def extract_json(self, system_prompt, user_prompt, **kwargs):
        if "ENTITY BLOCK" in user_prompt:
            self.batch_calls += 1
            if self.batch_exc is not None:
                raise self.batch_exc
            return self.batch_response
        # Solo call — echo back a valid entity for the requested id.
        self.solo_calls += 1
        m = _ID_RE.search(user_prompt)
        eid = m.group(1) if m else "char-unknown"
        return {"entity": _valid_entity(eid)}

    def delay(self):
        pass


# ===========================================================================
# A. Config reader
# ===========================================================================

class TestBatchConfig:
    def test_default_off(self):
        assert se._get_batch_detail_config(None) == (False, 4, 0.8)
        assert se._get_batch_detail_config({}) == (False, 4, 0.8)
        assert se._get_batch_detail_config({"context_optimizations": {}}) == (
            False, 4, 0.8,
        )

    def test_enabled_parsing(self):
        cfg = {
            "context_optimizations": {
                "batch_entity_detail": {
                    "enabled": True, "batch_size": 5,
                    "high_confidence_threshold": 0.7,
                }
            }
        }
        assert se._get_batch_detail_config(cfg) == (True, 5, 0.7)

    def test_strict_bool(self):
        # A truthy non-bool must NOT enable batching.
        cfg = {"context_optimizations": {"batch_entity_detail": {"enabled": "true"}}}
        enabled, _, _ = se._get_batch_detail_config(cfg)
        assert enabled is False

    def test_malformed_defensive(self):
        cfg = {"context_optimizations": {"batch_entity_detail": {
            "enabled": True, "batch_size": "lots", "high_confidence_threshold": None,
        }}}
        enabled, size, thr = se._get_batch_detail_config(cfg)
        assert enabled is True
        assert size == 4  # fallback
        assert thr == 0.8  # fallback

    def test_batch_size_floor(self):
        cfg = {"context_optimizations": {"batch_entity_detail": {
            "enabled": True, "batch_size": 1,
        }}}
        _, size, _ = se._get_batch_detail_config(cfg)
        assert size == 4  # < 2 falls back to default

    def test_shipped_config_default_off(self):
        """The committed config/llm.json ships the flag OFF (A/B control)."""
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "llm.json",
        )
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        enabled, _, _ = se._get_batch_detail_config(cfg)
        assert enabled is False


# ===========================================================================
# B. Partition / flag-OFF byte-identity + tiering
# ===========================================================================

class TestPartition:
    def _tasks(self):
        return [
            (_make_ref("char-player"), _make_entry("char-player")),
            (_make_ref("char-new", is_new=True), None),
            (_make_ref("char-hi", confidence=0.9), _make_entry("char-hi")),
            (_make_ref("char-a", confidence=0.5), _make_entry("char-a")),
            (_make_ref("char-b", confidence=0.4), _make_entry("char-b")),
            (_make_ref("char-c", confidence=0.3), _make_entry("char-c")),
        ]

    def test_flag_off_all_solo(self):
        tasks = self._tasks()
        solo, groups = se._partition_detail_tasks(tasks, None)
        assert solo == tasks  # identical objects, identical order
        assert groups == []

    def test_flag_off_config_present_disabled(self):
        tasks = self._tasks()
        cfg = {"context_optimizations": {"batch_entity_detail": {"enabled": False}}}
        solo, groups = se._partition_detail_tasks(tasks, cfg)
        assert solo == tasks
        assert groups == []

    def test_tiering_keeps_pc_new_highconf_solo(self):
        tasks = self._tasks()
        cfg = {"context_optimizations": {"batch_entity_detail": {
            "enabled": True, "batch_size": 4, "high_confidence_threshold": 0.8,
        }}}
        solo, groups = se._partition_detail_tasks(tasks, cfg)
        solo_ids = {se.get_entity_id(r) for r, _ in solo}
        assert "char-player" in solo_ids
        assert "char-new" in solo_ids
        assert "char-hi" in solo_ids
        # The three low-salience existing entities form one batch group.
        assert len(groups) == 1
        group_ids = {se.get_entity_id(r) for r, _ in groups[0]}
        assert group_ids == {"char-a", "char-b", "char-c"}

    def test_residual_single_demoted_to_solo(self):
        # 3 batchable, batch_size 2 -> [2-group, 1-residual]; residual -> solo.
        tasks = [
            (_make_ref("char-a", confidence=0.5), _make_entry("char-a")),
            (_make_ref("char-b", confidence=0.4), _make_entry("char-b")),
            (_make_ref("char-c", confidence=0.3), _make_entry("char-c")),
        ]
        cfg = {"context_optimizations": {"batch_entity_detail": {
            "enabled": True, "batch_size": 2,
        }}}
        solo, groups = se._partition_detail_tasks(tasks, cfg)
        assert len(groups) == 1
        assert len(groups[0]) == 2
        assert len(solo) == 1


# ===========================================================================
# C. Batched prompt
# ===========================================================================

class TestBatchPrompt:
    def _batch_tasks(self):
        return [
            (_make_ref("char-a", "Alice"), _make_entry("char-a", "Alice")),
            (_make_ref("char-b", "Bob"), _make_entry("char-b", "Bob")),
            (_make_ref("char-c", "Cara"), _make_entry("char-c", "Cara")),
        ]

    def test_turn_text_once_and_delimiters(self):
        turn = _make_turn("UNIQUE_TURN_MARKER appears here and only here.")
        prompt = se.format_detail_batch_prompt(
            turn, self._batch_tasks(), mentioned_ids=set(),
        )
        # Turn text appears exactly once, not once per entity.
        assert prompt.count("UNIQUE_TURN_MARKER") == 1
        # Hard delimiters for each of the 3 entities.
        assert prompt.count("ENTITY BLOCK 1 START") == 1
        assert prompt.count("ENTITY BLOCK 2 START") == 1
        assert prompt.count("ENTITY BLOCK 3 START") == 1
        # All entity ids present.
        for eid in ("char-a", "char-b", "char-c"):
            assert eid in prompt

    def test_return_uncompacted_shares_header(self):
        turn = _make_turn("TURNMARK only once.")
        compact, raw = se.format_detail_batch_prompt(
            turn, self._batch_tasks(), mentioned_ids=set(),
            return_uncompacted=True,
        )
        assert isinstance(compact, str) and isinstance(raw, str)
        # Both still emit the turn text exactly once (header shared).
        assert compact.count("TURNMARK") == 1
        assert raw.count("TURNMARK") == 1


# ===========================================================================
# D. Response parsing
# ===========================================================================

class TestParseBatchResponse:
    def test_envelope(self):
        resp = {"entities": [_valid_entity("char-a"), _valid_entity("char-b")]}
        out = se._parse_batch_detail_response(resp)
        assert set(out.keys()) == {"char-a", "char-b"}

    def test_bare_list(self):
        resp = [_valid_entity("char-a")]
        out = se._parse_batch_detail_response(resp)
        assert set(out.keys()) == {"char-a"}

    def test_single_envelope(self):
        resp = {"entity": _valid_entity("char-a")}
        out = se._parse_batch_detail_response(resp)
        assert set(out.keys()) == {"char-a"}

    def test_drops_idless_and_garbage(self):
        resp = {"entities": [{"name": "no id"}, "garbage", _valid_entity("char-a")]}
        out = se._parse_batch_detail_response(resp)
        assert set(out.keys()) == {"char-a"}

    def test_non_dict_non_list(self):
        assert se._parse_batch_detail_response(None) == {}
        assert se._parse_batch_detail_response("oops") == {}


# ===========================================================================
# E. Batched extraction + solo fallback
# ===========================================================================

class TestBatchedExtraction:
    def _tasks(self):
        return [
            (_make_ref("char-a"), _make_entry("char-a")),
            (_make_ref("char-b"), _make_entry("char-b")),
            (_make_ref("char-c"), _make_entry("char-c")),
        ]

    def test_maps_back_no_fallback(self):
        tasks = self._tasks()
        resp = {"entities": [
            _valid_entity("char-a"), _valid_entity("char-b"), _valid_entity("char-c"),
        ]}
        llm = _FakeLLM(batch_response=resp)
        results = se._extract_batched_entity_detail(
            llm, _make_turn(), tasks, None, mentioned_ids=set(),
        )
        assert llm.batch_calls == 1
        assert llm.solo_calls == 0  # no fallback
        got = {se.get_entity_id(r): data for r, data, err in results}
        assert set(got.keys()) == {"char-a", "char-b", "char-c"}
        assert all(data is not None for data in got.values())

    def test_parse_failure_falls_back_to_solo(self):
        tasks = self._tasks()
        # Batched response is empty/garbage -> nothing parses -> all solo.
        llm = _FakeLLM(batch_response={"unexpected": True})
        results = se._extract_batched_entity_detail(
            llm, _make_turn(), tasks, None, mentioned_ids=set(),
        )
        assert llm.batch_calls == 1
        assert llm.solo_calls == 3  # every entity retried solo
        ids = {se.get_entity_id(r) for r, data, err in results if data is not None}
        assert ids == {"char-a", "char-b", "char-c"}  # none dropped

    def test_llm_error_falls_back_to_solo(self):
        tasks = self._tasks()
        llm = _FakeLLM(batch_exc=se.LLMExtractionError("boom"))
        results = se._extract_batched_entity_detail(
            llm, _make_turn(), tasks, None, mentioned_ids=set(),
        )
        assert llm.batch_calls == 1
        assert llm.solo_calls == 3
        assert len(results) == 3

    def test_missing_entity_falls_back_solo(self):
        tasks = self._tasks()
        # Batched response omits char-c -> only char-c retried solo.
        resp = {"entities": [_valid_entity("char-a"), _valid_entity("char-b")]}
        llm = _FakeLLM(batch_response=resp)
        results = se._extract_batched_entity_detail(
            llm, _make_turn(), tasks, None, mentioned_ids=set(),
        )
        assert llm.batch_calls == 1
        assert llm.solo_calls == 1  # only the missing one
        ids = {se.get_entity_id(r) for r, data, err in results if data is not None}
        assert ids == {"char-a", "char-b", "char-c"}

    def test_quota_exhausted_propagates(self):
        tasks = self._tasks()
        llm = _FakeLLM(batch_exc=se.QuotaExhaustedError())
        try:
            se._extract_batched_entity_detail(
                llm, _make_turn(), tasks, None, mentioned_ids=set(),
            )
        except se.QuotaExhaustedError:
            pass
        else:
            raise AssertionError("QuotaExhaustedError should propagate")


# ===========================================================================
# F. Non-blind measurement
# ===========================================================================

class TestNonBlindMeasurement:
    def test_batched_sent_tokens_below_solo_total(self):
        """A synthetic 3-entity turn: batched recorded tokens < 3x solo.

        Records the entity_detail metric exactly as the pipeline does (via
        _record_prompt_tokens), for the solo path (3 calls) and the batched
        path (1 call), then asserts the batched per-turn total is below the
        solo per-turn total for BOTH the compressed (input_tokens) and the
        #484 uncompacted (raw_input_tokens) fields the paired scorer reads.
        """
        # A realistically long late-turn body (the duplicated term that L2
        # collapses is template + turn text).
        turn = _make_turn("The council fire crackles. " * 80)
        tasks = [
            (_make_ref("char-a"), _make_entry("char-a")),
            (_make_ref("char-b"), _make_entry("char-b")),
            (_make_ref("char-c"), _make_entry("char-c")),
        ]
        sys_solo = se.load_template("entity-detail")
        sys_batch = se.load_template("entity-detail-batch")

        solo_metrics: dict = {}
        for ref, entry in tasks:
            user, raw = se.format_detail_prompt(
                turn, ref, entry, mentioned_ids=set(), return_uncompacted=True,
            )
            se._record_prompt_tokens(
                solo_metrics, "entity_detail", sys_solo, user,
                raw_tokens=se._estimate_tokens(sys_solo + raw),
            )

        batch_metrics: dict = {}
        b_user, b_raw = se.format_detail_batch_prompt(
            turn, tasks, mentioned_ids=set(), return_uncompacted=True,
        )
        se._record_prompt_tokens(
            batch_metrics, "entity_detail", sys_batch, b_user,
            raw_tokens=se._estimate_tokens(sys_batch + b_raw),
        )

        solo = solo_metrics["entity_detail"]
        batch = batch_metrics["entity_detail"]
        # Control made 3 calls; variant made 1.
        assert solo["calls"] == 3
        assert batch["calls"] == 1
        # Per-turn SENT (compressed) tokens dropped — non-blind.
        assert batch["input_tokens"] < solo["input_tokens"]
        # Per-turn RAW (uncompacted, #484) tokens dropped — the field the
        # paired scorer aggregates is NOT blind to L2.
        assert batch["raw_input_tokens"] < solo["raw_input_tokens"]
        # The saving is real and material (well under 3x a single solo call).
        single_solo = solo["input_tokens"] / 3
        assert batch["input_tokens"] < 3 * single_solo
        # And the saving approximates collapsing 2 redundant template+turn copies.
        tmpl_plus_turn = se._estimate_tokens(sys_solo + f"Text:\n{turn['text']}")
        assert (solo["input_tokens"] - batch["input_tokens"]) > tmpl_plus_turn


# ===========================================================================
# G. Per-turn scorer mode (fair metric for L2)
# ===========================================================================

def _run(turn_calls_raw):
    """Build a {turn_id: record} run from {turn_id: (calls, raw_input_tokens)}."""
    run = {}
    for turn_id, (calls, raw) in turn_calls_raw.items():
        run[turn_id] = {
            "turn_id": turn_id,
            "new_entities": 0,
            "prompt_metrics": {
                "entity_detail": {
                    "calls": calls,
                    "input_tokens": raw,
                    "raw_input_tokens": raw,
                }
            },
        }
    return run


class TestPerTurnScorer:
    def test_matched_population_ignores_call_count(self):
        # Control: 6 calls; variant: 2 calls — different counts, same turn.
        a1 = _run({"turn-010": (6, 12000), "turn-011": (6, 12000)})
        a2 = _run({"turn-010": (6, 12100), "turn-011": (6, 12100)})
        b1 = _run({"turn-010": (2, 6000), "turn-011": (2, 6000)})
        b2 = _run({"turn-010": (2, 6100), "turn-011": (2, 6100)})
        # matched-call-COUNT drops everything (counts differ)...
        assert ab.matched_call_turns([a1, a2], [b1, b2]) == []
        # ...but matched-TURN keeps both turns.
        matched = ab.matched_population_turns([a1, a2], [b1, b2])
        assert matched == ["turn-010", "turn-011"]

    def test_per_turn_delta_captures_saving(self):
        a1 = _run({"turn-010": (6, 12000)})
        a2 = _run({"turn-010": (6, 12000)})
        b1 = _run({"turn-010": (2, 6000)})
        b2 = _run({"turn-010": (2, 6000)})
        matched = ab.matched_population_turns([a1, a2], [b1, b2])
        deltas = ab.paired_turn_deltas([a1, a2], [b1, b2], matched)
        assert len(deltas) == 1
        row = deltas[0]
        # Per-turn total tokens: A=12000, B=6000 -> delta -6000 (B saves).
        assert row["a_tokens_per_call"] == 12000
        assert row["b_tokens_per_call"] == 6000
        assert row["delta"] == -6000
