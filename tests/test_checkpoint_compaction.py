"""Unit tests for Phase A0 periodic checkpoint-compaction digest body.

Epic #477, issue #482, design docs/design-context-architecture-bounded.md §9.1
(Spike F10).  The A0 backend compacts accumulated volatile state on a fixed
K-turn cadence into a deterministic, extractive checkpoint snapshot plus an
append-only recent-delta buffer (no extra LLM call), bounding staleness to <= K.

Covers:
  - _read_compaction_config: default OFF, strict bool parse, cadence validation,
    and that compaction_interval_k is a SEPARATE key from the disk-persistence
    checkpoint_interval (#220/#212).
  - Shipped config/llm.json default is OFF (mirror of #480's shipped-default
    test, inverted).
  - _build_checkpoint_compacted_volatile: K-boundary snapshot, delta append,
    determinism, staleness bound.
  - Flag-OFF byte-identity golden (the make-or-break test).
  - Flag-ON reduced body (entity_detail prior-state + discovery known-block).
  - No LLM call in the digest path.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se  # noqa: E402
import catalog_merger as cm  # noqa: E402

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_CFG_OFF = {"context_optimizations": {"checkpoint_compaction": False}}
_CFG_ON = {
    "context_optimizations": {
        "checkpoint_compaction": True,
        "compaction_interval_k": 25,
    }
}

_GOLDEN_DIR = os.path.join(
    os.path.dirname(__file__), "golden", "checkpoint_compaction"
)


def _load_shipped_config():
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "config", "llm.json"
    )
    with open(cfg_path, encoding="utf-8") as fh:
        return json.load(fh)


def _vol_entry(num_turns=100):
    """Deterministic PC entry with a long-running volatile block.

    Matches tests/gen_a0_golden.py so the frozen golden stays reproducible.
    """
    return {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": f"turn-{num_turns:03d}",
        "identity": "The protagonist",
        "current_status": "active",
        "stable_attributes": {
            "species": "human",
            "class": "ranger",
            "aliases": {"value": ["Hero"]},
        },
        "volatile_state": {
            "location": [
                {"turn": f"turn-{i:03d}", "value": f"place-{i}"}
                for i in range(1, num_turns + 1)
            ],
            "mood": [
                {"turn": f"turn-{i:03d}", "value": f"feeling-{i}"}
                for i in range(10, num_turns + 1, 10)
            ],
        },
    }


# ===========================================================================
# _read_compaction_config
# ===========================================================================

class TestReadCompactionConfig:
    def test_default_disabled_none(self):
        enabled, k = se._read_compaction_config(None)
        assert enabled is False
        assert k == 25

    def test_default_disabled_empty(self):
        enabled, k = se._read_compaction_config({})
        assert enabled is False
        assert k == 25

    def test_disabled_when_flag_false(self):
        enabled, _ = se._read_compaction_config(_CFG_OFF)
        assert enabled is False

    def test_enabled_when_flag_true(self):
        enabled, k = se._read_compaction_config(_CFG_ON)
        assert enabled is True
        assert k == 25

    @pytest.mark.parametrize(
        "bad_flag",
        ["false", "False", "true", "0", "1", [False], [True], 1, {}, [], 0.0],
    )
    def test_non_bool_flag_does_not_enable(self, bad_flag):
        cfg = {"context_optimizations": {"checkpoint_compaction": bad_flag}}
        enabled, _ = se._read_compaction_config(cfg)
        assert enabled is False

    def test_real_true_bool_enables(self):
        cfg = {"context_optimizations": {"checkpoint_compaction": True}}
        enabled, _ = se._read_compaction_config(cfg)
        assert enabled is True

    def test_default_interval_k_when_missing(self):
        cfg = {"context_optimizations": {"checkpoint_compaction": True}}
        _, k = se._read_compaction_config(cfg)
        assert k == 25

    def test_custom_interval_k(self):
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": 50,
            }
        }
        _, k = se._read_compaction_config(cfg)
        assert k == 50

    @pytest.mark.parametrize(
        "bad_k", [None, "ten", [], {}, [25], 0, -5, -1]
    )
    def test_invalid_interval_k_falls_back_to_default(self, bad_k):
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": bad_k,
            }
        }
        _, k = se._read_compaction_config(cfg)
        assert k == 25

    @pytest.mark.parametrize(
        "bad_k",
        [
            "1",     # string "1" must NOT coerce to the degenerate K=1 cadence
            "7",     # arbitrary numeric string -> malformed -> default
            "25",    # even the default-valued string is still a non-int -> default
            1.9,     # float must NOT truncate to 1 via int()
            25.9,    # float must NOT truncate to 25 via int()
            10 ** 9,  # huge int is uncapped/refresh-disabling -> default
            2 ** 63,  # very large int beyond the sane upper bound -> default
        ],
    )
    def test_malformed_non_int_or_out_of_range_interval_k_falls_back(self, bad_k):
        # The cadence contract (mirroring catalog_merger.py:807-818): a real int
        # in [1, _MAX_COMPACTION_INTERVAL_K] is honored; everything else —
        # numeric strings, floats, and refresh-disabling huge ints — is malformed
        # and falls back to the default 25.  ``int(...)`` coercion is forbidden
        # because it would silently accept "1"->1, 1.9->1, 25.9->25, etc.
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": bad_k,
            }
        }
        _, k = se._read_compaction_config(cfg)
        assert k == 25

    def test_interval_k_at_upper_bound_is_honored(self):
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": se._MAX_COMPACTION_INTERVAL_K,
            }
        }
        _, k = se._read_compaction_config(cfg)
        assert k == se._MAX_COMPACTION_INTERVAL_K

    def test_interval_k_just_over_upper_bound_falls_back(self):
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": se._MAX_COMPACTION_INTERVAL_K + 1,
            }
        }
        _, k = se._read_compaction_config(cfg)
        assert k == 25

    @pytest.mark.parametrize("bad_k", [True, False])
    def test_bool_interval_k_falls_back_to_default(self, bad_k):
        # A JSON bool is NOT a valid interval: because ``bool`` subclasses
        # ``int``, an unguarded ``int(True) == 1`` / ``int(False) == 0`` would
        # otherwise bypass the documented "malformed -> default 25" fallback
        # (``true`` would silently set a degenerate cadence of 1).
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": bad_k,
            }
        }
        _, k = se._read_compaction_config(cfg)
        assert k == 25

    def test_malformed_context_optimizations_does_not_crash(self):
        for bad in ([], "nope", 7, None):
            cfg = {"context_optimizations": bad}
            enabled, k = se._read_compaction_config(cfg)
            assert enabled is False
            assert k == 25


# ===========================================================================
# SEPARATE cadence key — must NOT overload disk-persistence checkpoint_interval
# ===========================================================================

class TestCadenceKeyIsSeparate:
    """compaction_interval_k (context compaction) and checkpoint_interval (disk
    crash-recovery, #220/#212) are distinct concerns and must not be overloaded.
    """

    def test_compaction_reads_its_own_key_not_disk_interval(self):
        # Disk checkpoint_interval set to a different value; compaction must read
        # ONLY compaction_interval_k.
        cfg = {
            "checkpoint_interval": 99,
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": 7,
            },
        }
        _, k = se._read_compaction_config(cfg)
        assert k == 7

    def test_disk_interval_unaffected_by_compaction_key(self):
        # Only the context-compaction cadence is set; the disk reader must still
        # return its own default, proving the keys are independent.
        cfg = {
            "context_optimizations": {
                "checkpoint_compaction": True,
                "compaction_interval_k": 7,
            }
        }
        assert se._read_checkpoint_interval(cfg) == se._DEFAULT_CHECKPOINT_INTERVAL

    def test_disk_interval_reads_its_own_key(self):
        cfg = {"checkpoint_interval": 99}
        assert se._read_checkpoint_interval(cfg) == 99
        # ...and compaction falls back to its own default, not 99.
        _, k = se._read_compaction_config(cfg)
        assert k == 25


# ===========================================================================
# Shipped default OFF (config/llm.json) — mirror of #480, inverted
# ===========================================================================

class TestShippedDefaultOff:
    def test_shipped_config_checkpoint_compaction_off(self):
        cfg = _load_shipped_config()
        ctx_opt = cfg.get("context_optimizations", {})
        assert ctx_opt.get("checkpoint_compaction") is False

    def test_shipped_config_parses_to_disabled(self):
        cfg = _load_shipped_config()
        enabled, k = se._read_compaction_config(cfg)
        assert enabled is False
        assert k == 25

    def test_shipped_compaction_interval_k_is_25(self):
        cfg = _load_shipped_config()
        assert cfg["context_optimizations"]["compaction_interval_k"] == 25

    def test_shipped_disk_checkpoint_interval_still_present(self):
        # Guard against accidentally overloading/removing the disk key.
        cfg = _load_shipped_config()
        assert cfg.get("checkpoint_interval") == 25
        assert "compaction_interval_k" not in cfg  # disk key lives at top level


# ===========================================================================
# _build_checkpoint_compacted_volatile
# ===========================================================================

class TestBuildCheckpointCompactedVolatile:
    def _vol(self, last_turn):
        return {
            "events": [
                {"turn": f"turn-{i:03d}", "value": f"event-{i}"}
                for i in range(1, last_turn + 1)
            ]
        }

    def test_k_boundary_snapshot_generation(self):
        # At turn 50 with K=25 the most recent boundary is 50: all entries
        # through turn-50 fold into a snapshot summary PLUS the latest
        # pre-boundary item kept verbatim; delta is empty.
        out = se._build_checkpoint_compacted_volatile(self._vol(50), 50, 25)
        events = out["events"]
        assert isinstance(events[0], str)
        assert events[0].startswith("[checkpoint turn-50:")
        # Summary + the latest-at-checkpoint item (turn-050), no delta.
        assert len(events) == 2
        assert events[1] == {"turn": "turn-050", "value": "event-50"}

    def test_latest_pre_boundary_value_survives_at_boundary(self):
        # P1 (epic #477): a key whose ONLY current fact sits at/before the
        # boundary must NOT be erased — the latest value stays verbatim at t=K.
        vol = {"location": [{"turn": "turn-050", "value": "loc-castle"}]}
        out = se._build_checkpoint_compacted_volatile(vol, 50, 25)
        loc = out["location"]
        assert loc[0].startswith("[checkpoint turn-50:")
        assert {"turn": "turn-050", "value": "loc-castle"} in loc

    def test_delta_append_between_checkpoints(self):
        # Turn 60, K=25 -> boundary 50.  Entries 1..50 snapshot (summary +
        # latest turn-050); 51..60 delta, appended verbatim and in order.
        out = se._build_checkpoint_compacted_volatile(self._vol(60), 60, 25)
        events = out["events"]
        assert events[0].startswith("[checkpoint turn-50:")
        # Index 1 is the latest-at-checkpoint item; the rest is the delta.
        assert events[1] == {"turn": "turn-050", "value": "event-50"}
        delta = events[2:]
        assert [e["value"] for e in delta] == [f"event-{i}" for i in range(51, 61)]

    def test_no_snapshot_before_first_checkpoint(self):
        # Turn 10, K=25 -> boundary 0.  Nothing at/before turn 0, so the whole
        # buffer is the delta (no snapshot summary line).
        out = se._build_checkpoint_compacted_volatile(self._vol(10), 10, 25)
        events = out["events"]
        assert all(isinstance(e, dict) for e in events)
        assert len(events) == 10

    def test_empty_passthrough(self):
        assert se._build_checkpoint_compacted_volatile({}, 100, 25) == {}

    def test_non_list_value_passthrough(self):
        vol = {"flag": "on", "events": [{"turn": "turn-010", "value": "x"}]}
        out = se._build_checkpoint_compacted_volatile(vol, 100, 25)
        assert out["flag"] == "on"

    def test_deterministic_identical_input(self):
        a = se._build_checkpoint_compacted_volatile(self._vol(60), 60, 25)
        b = se._build_checkpoint_compacted_volatile(self._vol(60), 60, 25)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_staleness_bounded_by_k(self):
        # Delta entries are strictly after the checkpoint; the single latest-
        # at-checkpoint item sits exactly AT the checkpoint.  The checkpoint is
        # within K turns of the current turn -> staleness < K.
        k = 25
        for t in (37, 60, 99, 110, 250):
            out = se._build_checkpoint_compacted_volatile(self._vol(t), t, k)
            checkpoint = (t // k) * k
            assert t - checkpoint < k
            events = out["events"]
            # When a snapshot summary is present, index 1 is the latest-at-
            # checkpoint item (turn == checkpoint); everything else is delta.
            has_snapshot = events and isinstance(events[0], str)
            for idx, e in enumerate(events):
                if not isinstance(e, dict):
                    continue
                tn = se._extract_turn_number(e)
                if has_snapshot and idx == 1:
                    assert tn == checkpoint
                else:
                    assert tn > checkpoint


# ===========================================================================
# Flag-OFF byte-identity (make-or-break)
# ===========================================================================

class TestFlagOffGolden:
    """The make-or-break test: flag-OFF prior-state render is byte-identical to
    main (the A/B control).  Pinned against a frozen golden literal captured
    from the unchanged OFF path (mirrors the #385 / pc_rel TestFlagOffMainGolden
    discipline)."""

    @staticmethod
    def _load_golden():
        with open(
            os.path.join(_GOLDEN_DIR, "flag_off_prior.json"), encoding="utf-8"
        ) as fh:
            return fh.read()

    def test_flag_off_matches_main_golden(self):
        golden = self._load_golden()
        entry = _vol_entry(100)
        out_none = se._format_prior_entity_context(
            entry, config=None, mentioned_ids=set(), current_turn_num=100
        )
        out_off = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        out_explicit_false = se._format_prior_entity_context(
            entry,
            config={"context_optimizations": {"checkpoint_compaction": False}},
            mentioned_ids=set(),
            current_turn_num=100,
        )
        assert out_none == golden
        assert out_off == golden
        assert out_explicit_false == golden

    def test_flag_on_differs_from_golden(self):
        # Sanity: the ON path actually changes the body (otherwise the golden
        # test would be vacuous).
        golden = self._load_golden()
        entry = _vol_entry(100)
        out_on = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=100
        )
        assert out_on != golden


# ===========================================================================
# Flag-ON reduced body — entity_detail prior-state
# ===========================================================================

class TestFlagOnReducedBodyPriorState:
    def test_on_digest_smaller_than_sliding_digest_high_turn(self):
        # At a high turn the checkpoint snapshot collapses far more history than
        # the sliding _DIGEST_WINDOW digest, so the A0 digest BODY is smaller.
        vol = {
            "events": [
                {"turn": f"turn-{i:03d}", "value": f"event-{i}"}
                for i in range(1, 211)
            ]
        }
        off = se._build_volatile_digest(vol, 210)
        on = se._build_checkpoint_compacted_volatile(vol, 210, 25)
        off_size = len(json.dumps(off))
        on_size = len(json.dumps(on))
        assert on_size < off_size
        # OFF keeps the full sliding window (50) verbatim; ON keeps only the
        # post-checkpoint delta (<= K=25).
        assert len(on["events"]) < len(off["events"])

    def test_on_prior_context_not_larger_than_off(self):
        entry = _vol_entry(100)
        off = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        on = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=100
        )
        assert on != off
        assert len(on) <= len(off)

    @pytest.mark.parametrize("current_turn", [50, 51])
    def test_latest_volatile_value_visible_at_and_after_boundary(self, current_turn):
        # P1 (epic #477): an entity whose ONLY current location fact is set at
        # turn-050 must keep that value visible in the entity_detail prior-state
        # both AT the K boundary (t=50) and the turn AFTER (t=51) — the snapshot
        # must not collapse it into a count/theme summary and erase current state.
        entry = {
            "id": "char-mara", "name": "Mara", "type": "character",
            "first_seen_turn": "turn-050", "last_updated_turn": f"turn-{current_turn:03d}",
            "identity": "A wanderer",
            "volatile_state": {
                "location": [{"turn": "turn-050", "value": "loc-castle"}],
            },
        }
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(),
            current_turn_num=current_turn,
        )
        parsed = json.loads(out)
        loc = parsed["volatile_state"]["location"]
        # The current value survives byte-for-byte as a verbatim item.
        assert {"turn": "turn-050", "value": "loc-castle"} in loc
        assert "loc-castle" in out


# ===========================================================================
# Flag-ON reduced body — discovery known-block
# ===========================================================================

class TestDiscoveryKnownBlock:
    def _catalogs(self):
        # Recent (full both), brief-tier (id|name|type both OFF and ON), and
        # deeply-stale (id-only both) entities at current_turn=110, K=25
        # (checkpoint boundary = 100), recency_window=10, brief threshold=20.
        # A0 must NOT weaken the discovery anchors: OFF and ON are byte-identical
        # because the id|name|type anchor is the coreference floor.
        def ent(eid, turn):
            return {
                "id": eid,
                "name": eid.replace("-", " ").title(),
                "type": "character",
                "identity": f"identity of {eid}",
                "last_updated_turn": f"turn-{turn:03d}",
            }

        entities = [
            ent("zeta-recent-a", 110),
            ent("zeta-recent-b", 105),
            ent("zeta-snap-a", 99),   # age 11 -> OFF brief, ON id-only
            ent("zeta-snap-b", 95),   # age 15 -> OFF brief, ON id-only
            ent("zeta-snap-c", 90),   # age 20 -> OFF brief, ON id-only
            ent("zeta-stale-a", 85),  # age 25 -> id-only both
        ]
        return {"characters.json": entities}

    def test_flag_off_byte_identical_to_default(self):
        cats = self._catalogs()
        default_out = cm.format_known_entities_bounded(
            cats, current_turn=110, context_length=32768,
            turn_text="a quiet uneventful moment passes",
        )
        off_out = cm.format_known_entities_bounded(
            cats, current_turn=110, context_length=32768,
            turn_text="a quiet uneventful moment passes",
            checkpoint_compaction=False, compaction_interval_k=25,
        )
        assert off_out == default_out

    def test_flag_on_smaller_known_block(self):
        cats = self._catalogs()
        off_out = cm.format_known_entities_bounded(
            cats, current_turn=110, context_length=32768,
            turn_text="a quiet uneventful moment passes",
            checkpoint_compaction=False,
        )
        on_out = cm.format_known_entities_bounded(
            cats, current_turn=110, context_length=32768,
            turn_text="a quiet uneventful moment passes",
            checkpoint_compaction=True, compaction_interval_k=25,
        )
        assert on_out != off_out
        assert len(on_out) < len(off_out)
        # Recent entities keep full detail (coreference floor preserved).
        assert "identity of zeta-recent-a" in on_out

    @pytest.mark.parametrize("bad_k", ["25", None, [], {}, 0, -5, True, False])
    def test_non_int_interval_k_coerced_to_default(self, bad_k):
        # ``compaction_interval_k`` is a public parameter, so a caller may pass
        # a non-int (string/bool/list) or a non-positive int.  The defensive
        # coercion must mirror the config reader's "malformed -> default 25"
        # contract: never raise ``TypeError`` / ``ZeroDivisionError``, and
        # produce the same block as the valid K=25 call.
        cats = self._catalogs()
        good_out = cm.format_known_entities_bounded(
            cats, current_turn=110, context_length=32768,
            turn_text="a quiet uneventful moment passes",
            checkpoint_compaction=True, compaction_interval_k=25,
        )
        bad_out = cm.format_known_entities_bounded(
            cats, current_turn=110, context_length=32768,
            turn_text="a quiet uneventful moment passes",
            checkpoint_compaction=True, compaction_interval_k=bad_k,
        )
        assert bad_out == good_out


# ===========================================================================
# No LLM call in the digest path
# ===========================================================================

class TestNoLlmInDigestPath:
    def test_digest_path_makes_no_llm_call(self, monkeypatch):
        """The A0 digest is extractive: instantiating an LLM client anywhere in
        the digest path must be unnecessary.  Guard by making LLMClient explode
        if constructed, then exercise both digest entry points."""

        def _boom(*args, **kwargs):
            raise AssertionError("digest path must not construct an LLM client")

        monkeypatch.setattr(se, "LLMClient", _boom)

        vol = {"events": [{"turn": f"turn-{i:03d}", "value": str(i)} for i in range(1, 60)]}
        se._build_checkpoint_compacted_volatile(vol, 60, 25)
        se._format_prior_entity_context(
            _vol_entry(60), config=_CFG_ON, mentioned_ids=set(), current_turn_num=60
        )
        cm.format_known_entities_bounded(
            {"characters.json": [
                {"id": "zeta-x", "name": "Zeta X", "type": "character",
                 "last_updated_turn": "turn-010"}
            ]},
            current_turn=60, context_length=32768, turn_text="text",
            checkpoint_compaction=True, compaction_interval_k=25,
        )
