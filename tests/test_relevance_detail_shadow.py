"""Tests for the S0 relevance-scoped entity_detail shadow (#494, lever b/A2a).

The S0 slice is MEASUREMENT-ONLY: it logs which entities a relevance-scoped
selector WOULD pick for ``entity_detail`` versus the current cap-6 truncation,
with ZERO behaviour change.  These tests cover:

- ``_get_relevance_shadow_enabled`` — default OFF, strict bool, defensive
  parsing, and the shipped ``config/llm.json`` ships the flag OFF.
- ``_compute_relevance_detail_shadow`` — correctness of the actual-vs-would
  selection, the cap-dropped-but-relevant count, and the critical safety
  metric ``referenced_but_would_drop`` on a synthetic >cap turn, plus the
  <cap (no-truncation) and 0-prior-entities edges.  It must not mutate inputs.
- Output identity — running ``extract_and_merge`` flag-ON vs flag-OFF over a
  >cap turn produces byte-identical catalogs, events, and detail-call
  selection; only the extra ``relevance_detail_shadow`` log key differs.
"""

import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ref(entity_id, name=None, etype="character", is_new=False, confidence=0.5):
    return {
        "name": name or entity_id.replace("-", " ").title(),
        "type": etype,
        "existing_id": entity_id,
        "is_new": is_new,
        "confidence": confidence,
    }


def _make_entry(entity_id, name=None, last_updated_turn="turn-099", location=None):
    entry = {
        "id": entity_id,
        "name": name or entity_id.replace("-", " ").title(),
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": last_updated_turn,
        "identity": "A known entity in the scene.",
        "current_status": "present",
    }
    if location is not None:
        entry["volatile_state"] = {"location": location}
    return entry


def _valid_entity(entity_id, name=None):
    """A schema-valid entity object the fake LLM returns from a detail call."""
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


class _FullFakeLLM:
    """Fake LLM covering every sequential phase of ``extract_and_merge``.

    Discriminates phases by the system template so it can echo a valid entity
    for detail calls (recording the order of requested ids) and return empty
    relationship / event payloads.  ``parallel_workers`` is left unset so the
    sequential path runs (the default control path).
    """

    def __init__(self, config):
        self.config = config
        self.context_length = 8192
        self.max_tokens = 2048
        self.detail_ids = []
        self._detail_tmpl = se.load_template("entity-detail")
        self._rel_tmpl = se.load_template("relationship-mapper")
        self._event_tmpl = se.load_template("event-extractor")

    def extract_json(self, system_prompt, user_prompt, **kwargs):
        if system_prompt == self._detail_tmpl:
            m = _ID_RE.search(user_prompt)
            eid = m.group(1) if m else "char-unknown"
            self.detail_ids.append(eid)
            return {"entity": _valid_entity(eid)}
        if system_prompt == self._rel_tmpl:
            return {"relationships": []}
        if system_prompt == self._event_tmpl:
            return {"events": []}
        return {}

    def delay(self):
        pass


def _prefetched(qualified):
    return {
        "qualified": qualified,
        "turn_failed": False,
        "discovery_proposals": len(qualified),
        "discovery_filtered": 0,
        "phase_log": {"discovery_ok": True, "discovery_error": None},
        "discovery_sys_tmpl": None,
        "discovery_user_prompt": None,
    }


# ===========================================================================
# A. Config reader
# ===========================================================================

class TestRelevanceShadowFlag:
    def test_default_off(self):
        assert se._get_relevance_shadow_enabled(None) is False
        assert se._get_relevance_shadow_enabled({}) is False
        assert se._get_relevance_shadow_enabled({"context_optimizations": {}}) is False

    def test_enabled(self):
        cfg = {"context_optimizations": {"relevance_shadow_logging": True}}
        assert se._get_relevance_shadow_enabled(cfg) is True

    def test_strict_bool(self):
        # A truthy non-bool must NOT enable the shadow.
        for bad in ("true", 1, [True], "yes"):
            cfg = {"context_optimizations": {"relevance_shadow_logging": bad}}
            assert se._get_relevance_shadow_enabled(cfg) is False

    def test_malformed_context_optimizations(self):
        # context_optimizations itself malformed must not crash and stays OFF.
        assert se._get_relevance_shadow_enabled({"context_optimizations": []}) is False
        assert se._get_relevance_shadow_enabled({"context_optimizations": "x"}) is False

    def test_shipped_config_default_off(self):
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "llm.json",
        )
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        assert se._get_relevance_shadow_enabled(cfg) is False


# ===========================================================================
# B. Shadow-record correctness
# ===========================================================================

class TestComputeShadow:
    def _catalogs_and_tasks(self):
        """Build a >cap scenario.

        9 existing candidates char-a..char-i; char-a/char-b/char-c are
        mentioned in the turn text (relevance tier-1).  The cap keeps the 6
        highest-confidence; char-g/char-h/char-i (lowest confidence) are
        dropped — of those, char-a..char-c are mentioned (relevant) but they
        are also high-confidence and therefore kept, so the dropped set is
        unmentioned.  We deliberately give a mentioned entity LOW confidence so
        the cap drops it while relevance would keep it.
        """
        ids = [f"char-{c}" for c in "abcdefghi"]
        catalogs = {"characters.json": [_make_entry(eid) for eid in ids]}
        # Confidence: char-a..f high (0.9..0.4), char-g/h/i low.  char-i is
        # mentioned but lowest confidence -> cap drops it, relevance keeps it.
        conf = {
            "char-a": 0.95, "char-b": 0.9, "char-c": 0.85, "char-d": 0.8,
            "char-e": 0.75, "char-f": 0.7, "char-g": 0.3, "char-h": 0.2,
            "char-i": 0.1,
        }
        names = {eid: e["name"] for e in catalogs["characters.json"] for eid in [e["id"]]}
        pre_cap = [
            (_make_ref(eid, name=names[eid], confidence=conf[eid]), catalogs["characters.json"][i])
            for i, eid in enumerate(ids)
        ]
        return catalogs, pre_cap, names

    def test_over_cap_metrics(self):
        catalogs, pre_cap, names = self._catalogs_and_tasks()
        # Turn mentions char-a (kept, high conf) and char-i (dropped, low conf).
        turn_text = f"{names['char-a']} confronts {names['char-i']} at dawn."
        # Actual cap-6 set: top-6 by confidence = a,b,c,d,e,f.
        actual = pre_cap[:6]
        shadow = se._compute_relevance_detail_shadow(
            pre_cap, actual, catalogs, turn_text, 100,
        )
        assert shadow["actual_detailed_count"] == 6
        # char-i was dropped by the cap.
        assert "char-i" in shadow["cap_dropped_ids"]
        assert shadow["cap_dropped_count"] == 3
        # Relevance WOULD include char-i (mentioned), recovering a dropped one.
        assert "char-i" in shadow["relevance_would_select_ids"]
        assert "char-i" in shadow["cap_dropped_but_would_include_ids"]
        assert shadow["cap_dropped_but_would_include_count"] >= 1
        # SAFETY: no referenced entity is dropped by relevance.  Both mentioned
        # candidates (char-a, char-i) are in the would-select set.
        assert shadow["referenced_candidate_count"] == 2
        assert shadow["referenced_but_would_drop_count"] == 0
        assert shadow["referenced_but_would_drop_ids"] == []

    def test_safety_metric_catches_blocklisted_reference(self):
        """A referenced entity hidden by the selection blocklist is still
        flagged by the safety metric — proving it is NOT zero by construction.

        An entity literally named ``"Shadow"`` (a single-word
        ``_COMMON_WORD_BLOCKLIST`` member) is present in the turn text, but the
        selection-path mention detector suppresses it, so relevance would drop
        it.  The blocklist-free safety detection must still catch the dropped
        reference, making ``referenced_but_would_drop_count`` non-zero.
        """
        catalogs = {"characters.json": [_make_entry("char-shadow", name="Shadow")]}
        # Existing (not new), low confidence, not the PC -> relevance drops it.
        pre_cap = [
            (_make_ref("char-shadow", name="Shadow", is_new=False, confidence=0.1),
             catalogs["characters.json"][0]),
        ]
        turn_text = "A Shadow crept across the wall."
        shadow = se._compute_relevance_detail_shadow(
            pre_cap, pre_cap, catalogs, turn_text, 100,
        )
        # Selection-path relevance does NOT select it (blocklist hides it).
        assert "char-shadow" not in shadow["relevance_would_select_ids"]
        # But the conservative (blocklist-free) safety metric flags the drop.
        assert shadow["referenced_candidate_count"] == 1
        assert shadow["referenced_but_would_drop_count"] == 1
        assert shadow["referenced_but_would_drop_ids"] == ["char-shadow"]
        # The per-entity record marks it referenced but not selected.
        rec = {e["id"]: e for e in shadow["per_entity"]}["char-shadow"]
        assert rec["referenced"] is True
        assert rec["would_select"] is False
        assert rec["reason"] == "none"

    def test_per_entity_and_unchanged_fraction(self):
        catalogs, pre_cap, names = self._catalogs_and_tasks()
        turn_text = f"{names['char-a']} confronts {names['char-i']} at dawn."
        actual = pre_cap[:6]
        shadow = se._compute_relevance_detail_shadow(
            pre_cap, actual, catalogs, turn_text, 100,
        )
        # One per-entity record per pre-cap candidate, with the relevance
        # "score" (discrete tier reason) and the would-select decision.
        assert len(shadow["per_entity"]) == len(pre_cap)
        recs = {e["id"]: e for e in shadow["per_entity"]}
        # char-a is mentioned -> selected with reason "mentioned".
        assert recs["char-a"]["reason"] == "mentioned"
        assert recs["char-a"]["would_select"] is True
        assert recs["char-a"]["referenced"] is True
        # char-d is neither mentioned, new, PC, nor adjacent -> reason "none".
        assert recs["char-d"]["reason"] == "none"
        assert recs["char-d"]["would_select"] is False
        assert recs["char-d"]["referenced"] is False
        # Mentioned-but-unchanged fraction: of the 6 actual detail calls
        # (char-a..char-f), only char-a is selected by relevance, so the other
        # five are the relevance-droppable "mentioned-but-unchanged" set.
        assert shadow["actual_detail_would_drop_count"] == 5
        assert set(shadow["actual_detail_would_drop_ids"]) == {
            "char-b", "char-c", "char-d", "char-e", "char-f",
        }
        assert abs(shadow["mentioned_but_unchanged_fraction"] - round(5 / 6, 4)) < 1e-9

    def test_force_keep_new_and_pc(self):
        catalogs = {"characters.json": [_make_entry("char-x")]}
        pre_cap = [
            (_make_ref("char-player", is_new=False, confidence=0.1), _make_entry("char-player")),
            (_make_ref("char-new", is_new=True, confidence=0.1), None),
            (_make_ref("char-x", is_new=False, confidence=0.1), catalogs["characters.json"][0]),
        ]
        # No mentions at all in the turn text.
        shadow = se._compute_relevance_detail_shadow(
            pre_cap, pre_cap, catalogs, "Nothing relevant here.", 100,
        )
        # PC and the new entity are force-kept even with no mention; char-x is
        # neither mentioned nor new -> not selected by relevance.
        assert "char-player" in shadow["relevance_would_select_ids"]
        assert "char-new" in shadow["relevance_would_select_ids"]
        assert "char-x" not in shadow["relevance_would_select_ids"]

    def test_under_cap_no_truncation(self):
        catalogs = {"characters.json": [_make_entry("char-a"), _make_entry("char-b")]}
        pre_cap = [
            (_make_ref("char-a"), catalogs["characters.json"][0]),
            (_make_ref("char-b"), catalogs["characters.json"][1]),
        ]
        # actual == pre_cap (nothing dropped).
        shadow = se._compute_relevance_detail_shadow(
            pre_cap, pre_cap, catalogs, "char-a meets char-b.", 100,
        )
        assert shadow["cap_dropped_count"] == 0
        assert shadow["cap_dropped_ids"] == []
        assert shadow["cap_dropped_but_would_include_count"] == 0
        assert shadow["referenced_but_would_drop_count"] == 0

    def test_zero_prior_entities(self):
        # Empty catalog, all candidates new.
        catalogs = {"characters.json": []}
        pre_cap = [
            (_make_ref("char-new1", is_new=True), None),
            (_make_ref("char-new2", is_new=True), None),
        ]
        shadow = se._compute_relevance_detail_shadow(
            pre_cap, pre_cap, catalogs, "Two strangers appear.", 100,
        )
        # New entities are force-kept; nothing referenced/dropped.
        assert shadow["relevance_would_select_count"] == 2
        assert shadow["cap_dropped_count"] == 0
        assert shadow["referenced_but_would_drop_count"] == 0

    def test_does_not_mutate_inputs(self):
        catalogs, pre_cap, names = self._catalogs_and_tasks()
        actual = pre_cap[:6]
        catalogs_before = copy.deepcopy(catalogs)
        pre_cap_before = copy.deepcopy(pre_cap)
        actual_before = copy.deepcopy(actual)
        se._compute_relevance_detail_shadow(
            pre_cap, actual, catalogs, "char-a and char-i clash.", 100,
        )
        assert catalogs == catalogs_before
        assert pre_cap == pre_cap_before
        assert actual == actual_before


# ===========================================================================
# C. Output identity — flag ON vs OFF
# ===========================================================================

class TestOutputIdentity:
    def _scenario(self):
        ids = [f"char-{c}" for c in "abcdefghi"]
        names = {eid: eid.replace("-", " ").title() for eid in ids}
        catalogs = {"characters.json": [_make_entry(eid, names[eid]) for eid in ids]}
        catalogs["characters.json"].append(_make_entry("char-player", "Player Character"))
        conf = {
            "char-a": 0.95, "char-b": 0.9, "char-c": 0.85, "char-d": 0.8,
            "char-e": 0.75, "char-f": 0.7, "char-g": 0.3, "char-h": 0.2,
            "char-i": 0.1,
        }
        # >cap qualified set (9 existing + PC = 10) so the cap-6 truncation
        # fires (PC is exempt; 9 non-PC capped to <=6).
        qualified = [_make_ref(eid, names[eid], confidence=conf[eid]) for eid in ids]
        qualified.append(_make_ref("char-player", "Player Character", confidence=0.9))
        turn = {
            "turn_id": "turn-100",
            "speaker": "DM",
            "text": f"{names['char-a']} confronts {names['char-i']} while Char B watches.",
        }
        return catalogs, qualified, turn

    def _run(self, shadow_on):
        catalogs, qualified, turn = self._scenario()
        config = {"context_optimizations": {"relevance_shadow_logging": shadow_on}}
        llm = _FullFakeLLM(config)
        se._reset_pc_failure_tracking()
        out_catalogs, out_events, turn_failed, log = se.extract_and_merge(
            turn,
            copy.deepcopy(catalogs),
            [],
            llm,
            prefetched_discovery=_prefetched(copy.deepcopy(qualified)),
            timeline=None,
        )
        return out_catalogs, out_events, turn_failed, log, llm.detail_ids

    def test_extraction_byte_identical(self):
        cat_off, ev_off, failed_off, log_off, ids_off = self._run(False)
        cat_on, ev_on, failed_on, log_on, ids_on = self._run(True)

        # The detail-call selection (which entities got detailed, in order) is
        # identical regardless of the shadow flag.
        assert ids_on == ids_off
        # The cap actually fired (the scenario is a real >cap turn).
        assert log_off["entity_detail_capped_count"] > 0
        assert log_on["entity_detail_capped_count"] == log_off["entity_detail_capped_count"]
        # Extraction OUTPUT is byte-identical.
        assert json.dumps(cat_on, sort_keys=True) == json.dumps(cat_off, sort_keys=True)
        assert json.dumps(ev_on, sort_keys=True) == json.dumps(ev_off, sort_keys=True)
        assert failed_on == failed_off

    def test_only_shadow_key_differs_in_log(self):
        _, _, _, log_off, _ = self._run(False)
        _, _, _, log_on, _ = self._run(True)
        # Flag OFF: no shadow key at all (log byte-identical to baseline shape).
        assert "relevance_detail_shadow" not in log_off
        # Flag ON: the shadow key is the ONLY structural addition.
        assert "relevance_detail_shadow" in log_on
        assert set(log_on) - set(log_off) == {"relevance_detail_shadow"}
        shadow = log_on["relevance_detail_shadow"]
        assert shadow["actual_detailed_count"] >= 1
        assert shadow["referenced_but_would_drop_count"] == 0
