"""Tests for S1a relevance-scoped entity_detail selection (#498, lever b/A2a).

S1a is the **recover-only** slice: when the ``relevance_scoped_detail`` flag is
ON in ``mode = "recover_only"``, the detail set becomes the cap-6 set UNION the
relevance recoveries (cap-dropped entities the existing #233 relevance signal
marks relevant), bounded by a telemetry-derived ceiling.  It ADDS recoveries
and drops NOTHING currently detailed (a superset of the cap-6 set).  These
tests cover three tiers:

1. Flag-OFF byte-identity — extraction output identical flag-OFF vs the cap-6
   baseline (the A/B control must be sound).
2. Recover-only mechanism — flag-ON detail set = cap-6 UNION cap-dropped-relevant,
   bounded by the ceiling; a SUPERSET of the cap-6 set (never drops a
   currently-detailed entity); PC/new/mentioned always present (floor); the
   ceiling caps a synthetic big scene by trimming the lowest-relevance tail.
3. Edges — a scene with < cap entities (no recovery), a scene exceeding the
   ceiling (floor preserved + top recoveries), and 0 prior entities.
"""

import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_relevance_detail_shadow.py)
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
    """Fake LLM covering every sequential phase of ``extract_and_merge``."""

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


def _ids(tasks):
    return {se.get_entity_id(ref) for ref, _entry in tasks}


# ===========================================================================
# A. Config reader
# ===========================================================================

class TestRelevanceScopedDetailFlag:
    def test_default_off(self):
        enabled, _max, _mode = se._get_relevance_scoped_detail_config(None)
        assert enabled is False
        assert se._get_relevance_scoped_detail_config({})[0] is False
        assert se._get_relevance_scoped_detail_config(
            {"context_optimizations": {}})[0] is False

    def test_enabled_defaults(self):
        cfg = {"context_optimizations": {"relevance_scoped_detail": {"enabled": True}}}
        enabled, max_detail, mode = se._get_relevance_scoped_detail_config(cfg)
        assert enabled is True
        # The default ceiling (_DEFAULT_RELEVANCE_DETAIL_MAX = 10) is below the
        # raised hard cap, so it is clamped up to _MAX_DETAIL_ENTITIES_PER_TURN
        # (recover-only must retain the full cap set).
        assert se._DEFAULT_RELEVANCE_DETAIL_MAX == 10
        assert max_detail == se._MAX_DETAIL_ENTITIES_PER_TURN
        assert mode == "recover_only"

    def test_strict_bool(self):
        for bad in ("true", 1, [True], "yes"):
            cfg = {"context_optimizations": {"relevance_scoped_detail": {"enabled": bad}}}
            assert se._get_relevance_scoped_detail_config(cfg)[0] is False

    def test_malformed_context_optimizations(self):
        assert se._get_relevance_scoped_detail_config(
            {"context_optimizations": []})[0] is False
        assert se._get_relevance_scoped_detail_config(
            {"context_optimizations": "x"})[0] is False

    def test_malformed_subdict(self):
        # relevance_scoped_detail itself malformed -> defaults, OFF.
        cfg = {"context_optimizations": {"relevance_scoped_detail": []}}
        enabled, max_detail, mode = se._get_relevance_scoped_detail_config(cfg)
        assert enabled is False
        # Default ceiling (10) is clamped up to the raised hard cap.
        assert max_detail == se._MAX_DETAIL_ENTITIES_PER_TURN
        assert mode == "recover_only"

    def test_ceiling_parsed_and_clamped(self):
        cfg = {"context_optimizations": {
            "relevance_scoped_detail": {"enabled": True, "max_detail_entities": 25}}}
        # Above the hard cap (22) -> kept as configured.
        assert se._get_relevance_scoped_detail_config(cfg)[1] == 25
        # Below the hard cap -> clamped up to _MAX_DETAIL_ENTITIES_PER_TURN.
        cfg["context_optimizations"]["relevance_scoped_detail"]["max_detail_entities"] = 2
        assert se._get_relevance_scoped_detail_config(cfg)[1] == \
            se._MAX_DETAIL_ENTITIES_PER_TURN
        # Malformed -> default ceiling (10), itself clamped up to the hard cap.
        cfg["context_optimizations"]["relevance_scoped_detail"]["max_detail_entities"] = "ten"
        assert se._get_relevance_scoped_detail_config(cfg)[1] == \
            se._MAX_DETAIL_ENTITIES_PER_TURN

    def test_unknown_mode_normalises_to_recover_only(self):
        cfg = {"context_optimizations": {
            "relevance_scoped_detail": {"enabled": True, "mode": "drop_filler"}}}
        # S1b is not implemented here; an unknown mode falls back to the safe
        # recover-only slice (drops nothing).
        assert se._get_relevance_scoped_detail_config(cfg)[2] == "recover_only"

    def test_shipped_config_default_off(self):
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "llm.json",
        )
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        enabled, max_detail, mode = se._get_relevance_scoped_detail_config(cfg)
        assert enabled is False
        # Shipped ceiling (10) is clamped up to the raised hard cap at runtime.
        assert max_detail == se._MAX_DETAIL_ENTITIES_PER_TURN
        assert mode == "recover_only"


# ===========================================================================
# B. _apply_relevance_ceiling — the bound (floor + lowest-relevance tail drop)
# ===========================================================================

class TestApplyRelevanceCeiling:
    def test_no_bind_returns_unchanged(self):
        detail = [(_make_ref(f"char-{c}"), None) for c in "abc"]
        out = se._apply_relevance_ceiling(detail, {"char-a"}, set(), [], 10)
        assert out == detail

    def test_drops_lowest_relevance_tail_first(self):
        # 5 droppable recoveries d..h (none protected); ceiling 3 -> keep the
        # 3 highest-relevance by ``ordered`` rank, drop the lowest tail.
        detail = [(_make_ref(f"char-{c}"), None) for c in "defgh"]
        ordered = [{"id": f"char-{c}"} for c in "defgh"]  # d most relevant
        out = se._apply_relevance_ceiling(detail, set(), set(), ordered, 3)
        assert _ids(out) == {"char-d", "char-e", "char-f"}
        # Original order preserved.
        assert [se.get_entity_id(r) for r, _ in out] == ["char-d", "char-e", "char-f"]

    def test_never_drops_cap6_set(self):
        # cap_ids are protected even when the ceiling binds.
        detail = [(_make_ref(f"char-{c}"), None) for c in "abcde"]
        ordered = [{"id": f"char-{c}"} for c in "abcde"]
        cap_ids = {"char-a", "char-b", "char-c"}
        out = se._apply_relevance_ceiling(detail, cap_ids, set(), ordered, 4)
        # All cap-6 members kept; 1 recovery slot left -> char-d (most relevant).
        assert cap_ids.issubset(_ids(out))
        assert _ids(out) == {"char-a", "char-b", "char-c", "char-d"}

    def test_floor_may_exceed_ceiling(self):
        # 4 mentioned (floor) entities + 1 droppable; ceiling 2 -> the floor is
        # a HARD lower bound and is never trimmed, so all 4 floor members stay.
        floor = [(_make_ref(f"char-{c}"), None) for c in "abcd"]
        droppable = [(_make_ref("char-x"), None)]
        detail = floor + droppable
        mentioned = {"char-a", "char-b", "char-c", "char-d"}
        out = se._apply_relevance_ceiling(detail, set(), mentioned, [], 2)
        assert mentioned.issubset(_ids(out))
        # The single non-floor droppable is trimmed (no slots).
        assert "char-x" not in _ids(out)

    def test_pc_and_new_protected(self):
        detail = [
            (_make_ref("char-player"), None),
            (_make_ref("char-new", is_new=True), None),
            (_make_ref("char-x"), None),
            (_make_ref("char-y"), None),
        ]
        out = se._apply_relevance_ceiling(detail, set(), set(), [], 2)
        assert "char-player" in _ids(out)
        assert "char-new" in _ids(out)


# ===========================================================================
# C. _select_relevance_scoped_detail_recover — union + superset + recovery
# ===========================================================================

class TestRecoverSelection:
    def _scenario(self):
        """9 existing a..i + locations; char-a/char-i mentioned, char-i low conf."""
        ids = [f"char-{c}" for c in "abcdefghi"]
        names = {eid: eid.replace("-", " ").title() for eid in ids}
        entries = [_make_entry(eid, names[eid]) for eid in ids]
        catalogs = {"characters.json": entries}
        conf = {
            "char-a": 0.95, "char-b": 0.9, "char-c": 0.85, "char-d": 0.8,
            "char-e": 0.75, "char-f": 0.7, "char-g": 0.3, "char-h": 0.2,
            "char-i": 0.1,
        }
        pre_cap = [
            (_make_ref(eid, names[eid], confidence=conf[eid]), entries[i])
            for i, eid in enumerate(ids)
        ]
        return catalogs, pre_cap, names

    def test_recovers_cap_dropped_relevant_and_superset(self):
        catalogs, pre_cap, names = self._scenario()
        # cap-6 keeps top-6 by confidence: a..f.  Dropped: g,h,i.
        cap6 = pre_cap[:6]
        turn_text = f"{names['char-a']} confronts {names['char-i']} at dawn."
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, cap6, catalogs, turn_text, 100, 10,
        )
        # SUPERSET of cap-6 (recover-only drops nothing currently detailed).
        assert _ids(cap6).issubset(_ids(out))
        # char-i (mentioned, cap-dropped) is recovered.
        assert "char-i" in _ids(out)
        # char-g / char-h (not relevant) are NOT recovered.
        assert "char-g" not in _ids(out)
        assert "char-h" not in _ids(out)

    def test_floor_pc_new_mentioned_present(self):
        catalogs, pre_cap, names = self._scenario()
        # Add a PC and a brand-new entity, both cap-dropped.
        pre_cap.append((_make_ref("char-player", confidence=0.05), _make_entry("char-player")))
        pre_cap.append((_make_ref("char-new", is_new=True, confidence=0.05), None))
        cap6 = pre_cap[:6]  # a..f
        turn_text = f"{names['char-a']} confronts {names['char-i']} at dawn."
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, cap6, catalogs, turn_text, 100, 10,
        )
        # Floor force-kept even though cap-dropped and low confidence.
        assert "char-player" in _ids(out)
        assert "char-new" in _ids(out)
        assert "char-i" in _ids(out)  # mentioned

    def test_blocklisted_referenced_entity_recovered(self):
        """Coreference floor (Finding 1, #498): a cap-dropped entity literally
        named with a single word in ``_COMMON_WORD_BLOCKLIST`` (e.g. "Shadow")
        and referenced in the turn must be recovered, even though the
        selection-path mention detector suppresses that name."""
        entries = [_make_entry("char-shadow", "Shadow")]
        entries += [_make_entry(f"char-{c}", f"Char {c.upper()}") for c in "abcdef"]
        catalogs = {"characters.json": entries}
        by_id = {e["id"]: e for e in entries}
        # char-shadow cap-dropped (lowest confidence); a..f kept as cap-6.
        pre_cap = [(_make_ref("char-shadow", "Shadow", confidence=0.05),
                    by_id["char-shadow"])]
        pre_cap += [
            (_make_ref(f"char-{c}", f"Char {c.upper()}", confidence=0.9),
             by_id[f"char-{c}"]) for c in "abcdef"
        ]
        cap6 = pre_cap[1:]  # a..f
        # "Shadow" is in _COMMON_WORD_BLOCKLIST: the blocklist-ON detector would
        # NOT see it, but the coreference floor (blocklist OFF) must.
        turn_text = "Shadow steps forward."
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, cap6, catalogs, turn_text, 100, 10,
        )
        assert "char-shadow" in _ids(out)
        assert _ids(cap6).issubset(_ids(out))  # cap-6 superset preserved

    def test_blocklisted_referenced_entity_protected_by_ceiling(self):
        """A busy scene exceeding the ceiling must NOT trim a literally-referenced
        blocklisted-name entity (Finding 1, #498): the coreference floor binds
        even in the ceiling path."""
        entries = [_make_entry("char-shadow", "Shadow", location="the-tavern")]
        # Six co-located relevance recoveries (droppable by the ceiling).
        entries += [
            _make_entry(eid, f"Char {eid[-1].upper()}", location="the-tavern",
                        last_updated_turn=f"turn-{90 + i}")
            for i, eid in enumerate(["char-d", "char-e", "char-f",
                                     "char-g", "char-h", "char-j"])
        ]
        entries += [_make_entry("char-a", "Char A"), _make_entry("char-b", "Char B")]
        catalogs = {"characters.json": entries}
        by_id = {e["id"]: e for e in entries}
        pre_cap = [
            (_make_ref(e["id"], e["name"], confidence=0.5), by_id[e["id"]])
            for e in entries
        ]
        # cap-6 = char-a, char-b only (pretend the cap kept these 2).
        cap6 = [t for t in pre_cap if se.get_entity_id(t[0]) in {"char-a", "char-b"}]
        # char-shadow referenced (blocklisted name); only it is "mentioned".
        turn_text = "Shadow surveys the tavern."
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, cap6, catalogs, turn_text, 100, 5,
        )
        # Ceiling binds at 5, but char-shadow is a protected coreference floor
        # member and must survive the trim.
        assert "char-shadow" in _ids(out)
        assert {"char-a", "char-b"}.issubset(_ids(out))

    def test_under_cap_no_recovery(self):
        # < cap candidates -> cap6 == pre_cap, nothing dropped, no recovery.
        catalogs = {"characters.json": [_make_entry("char-a"), _make_entry("char-b")]}
        pre_cap = [
            (_make_ref("char-a"), catalogs["characters.json"][0]),
            (_make_ref("char-b"), catalogs["characters.json"][1]),
        ]
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, pre_cap, catalogs, "Char A meets Char B.", 100, 10,
        )
        assert _ids(out) == {"char-a", "char-b"}

    def test_zero_prior_entities(self):
        catalogs = {"characters.json": []}
        pre_cap = [
            (_make_ref("char-new1", is_new=True), None),
            (_make_ref("char-new2", is_new=True), None),
        ]
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, pre_cap, catalogs, "Two strangers appear.", 100, 10,
        )
        assert _ids(out) == {"char-new1", "char-new2"}

    def test_ceiling_caps_synthetic_big_scene(self):
        """A busy scene whose recoveries exceed the ceiling: floor + cap-6 kept,
        only the lowest-relevance recovery tail trimmed."""
        # char-a mentioned, at "the-tavern".  char-d..h are co-located there
        # (relevance recoveries, NOT mentioned -> droppable by the ceiling).
        names = {f"char-{c}": f"Char {c.upper()}" for c in "abcdefgh"}
        entries = [_make_entry("char-a", names["char-a"], location="the-tavern")]
        entries += [
            _make_entry(eid, names[eid], location="the-tavern",
                        last_updated_turn=f"turn-{90 + i}")
            for i, eid in enumerate(["char-d", "char-e", "char-f", "char-g", "char-h"])
        ]
        entries += [_make_entry("char-b", names["char-b"]),
                    _make_entry("char-c", names["char-c"])]
        catalogs = {"characters.json": entries}
        by_name = {e["id"]: e for e in entries}
        pre_cap = [
            (_make_ref(e["id"], names[e["id"]], confidence=0.5), by_name[e["id"]])
            for e in entries
        ]
        # cap-6 = char-a, char-b, char-c (pretend the cap kept these 3).
        cap6 = [t for t in pre_cap if se.get_entity_id(t[0]) in {"char-a", "char-b", "char-c"}]
        turn_text = f"{names['char-a']} surveys the tavern."  # only char-a mentioned
        out = se._select_relevance_scoped_detail_recover(
            pre_cap, cap6, catalogs, turn_text, 100, 5,
        )
        # Ceiling binds at 5: cap-6 (a,b,c) protected + 2 highest-relevance
        # co-located recoveries; total == 5.
        assert len(out) == 5
        assert {"char-a", "char-b", "char-c"}.issubset(_ids(out))
        # Exactly two of the five co-located recoveries are kept.
        recovered = _ids(out) - {"char-a", "char-b", "char-c"}
        assert len(recovered) == 2
        assert recovered.issubset({"char-d", "char-e", "char-f", "char-g", "char-h"})


# ===========================================================================
# D. End-to-end: flag-OFF byte-identity + flag-ON superset of cap-6
# ===========================================================================

class TestEndToEnd:
    def _scenario(self):
        # 24 non-PC entities (a..x) + PC = 25 qualified, exceeding the raised
        # cap (22) so the cap truncation fires: PC + the top-21 non-PC by
        # confidence (char-a..char-u) are kept; char-v/char-w/char-x are
        # dropped.  The mentioned low-confidence entity (char-x) is therefore
        # cap-dropped and available for relevance recovery.
        ids = [f"char-{c}" for c in "abcdefghijklmnopqrstuvwx"]
        names = {eid: eid.replace("-", " ").title() for eid in ids}
        catalogs = {"characters.json": [_make_entry(eid, names[eid]) for eid in ids]}
        catalogs["characters.json"].append(_make_entry("char-player", "Player Character"))
        conf = {eid: round(0.95 - i * 0.03, 4) for i, eid in enumerate(ids)}
        qualified = [_make_ref(eid, names[eid], confidence=conf[eid]) for eid in ids]
        qualified.append(_make_ref("char-player", "Player Character", confidence=0.9))
        turn = {
            "turn_id": "turn-100",
            "speaker": "DM",
            "text": f"{names['char-a']} confronts {names['char-x']} at dawn.",
        }
        return catalogs, qualified, turn

    def _run(self, rsd_config):
        catalogs, qualified, turn = self._scenario()
        config = {"context_optimizations": {}}
        if rsd_config is not None:
            config["context_optimizations"]["relevance_scoped_detail"] = rsd_config
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

    def test_flag_off_byte_identical(self):
        # Baseline: no relevance_scoped_detail key at all.
        cat_base, ev_base, failed_base, log_base, ids_base = self._run(None)
        # Flag present but disabled -> must be byte-identical to baseline.
        cat_off, ev_off, failed_off, log_off, ids_off = self._run(
            {"enabled": False, "max_detail_entities": 10, "mode": "recover_only"})
        assert ids_off == ids_base
        assert json.dumps(cat_off, sort_keys=True) == json.dumps(cat_base, sort_keys=True)
        assert json.dumps(ev_off, sort_keys=True) == json.dumps(ev_base, sort_keys=True)
        assert failed_off == failed_base
        assert log_off.get("entity_detail_capped_count") == \
            log_base.get("entity_detail_capped_count")
        assert log_off.get("entity_detail_capped_count") > 0  # the cap really fired

    def test_flag_on_superset_recovers_dropped(self):
        _, _, _, _, ids_off = self._run(None)
        _, _, _, log_on, ids_on = self._run(
            {"enabled": True, "max_detail_entities": 10, "mode": "recover_only"})
        # Recover-only: flag-ON detail set is a SUPERSET of the cap set.
        assert set(ids_off).issubset(set(ids_on))
        # char-x (mentioned, cap-dropped) is recovered by the flag-ON run.
        assert "char-x" in set(ids_on)
        assert "char-x" not in set(ids_off)
        # The recovery reduced the leftover (capped) count vs the cap-6 control.
        assert log_on.get("entity_detail_capped_count") < \
            self._run(None)[3].get("entity_detail_capped_count")
