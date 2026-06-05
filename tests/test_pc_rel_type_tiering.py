"""Unit tests for type-tiered PC relationship cap (L1+L4, epic #477).

Covers:
  (a) flag OFF: byte-identical output to pre-#477 behaviour (golden)
  (b) flag ON: keeps ALL permanent-bond types + force-keeps mentioned-this-turn
      + caps the volatile tail
  (c) synthetic 109-relationship PC shrinks to ~30-40 WITHOUT dropping any
      permanent-type or mentioned relationship
  (d) a <=t100 kinship/adversarial callback anchor SURVIVES
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_CFG_OFF = {
    "context_optimizations": {
        "relationship_type_tiering": False,
    }
}

_CFG_ON = {
    "context_optimizations": {
        "relationship_type_tiering": True,
        "pc_rel_permanent_types": [
            "kinship", "adversarial", "mentorship", "political",
            "partnership", "factional", "social",
        ],
        "pc_rel_volatile_tail_cap": 10,
    }
}

# Golden snapshot directory holding the pre-#477-main flag-OFF output captured
# as a frozen fixture (Finding #1).  Comparing flag-OFF output against a frozen
# literal — rather than only config=None vs config=_CFG_OFF (new-vs-new) — locks
# byte-identity to main with a test, not just code inspection.
_GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden", "pc_rel_tiering")


def _load_prod_config_on():
    """Return the production config/llm.json context_optimizations block with the
    tiering flag forced ON.

    This mirrors exactly what the A/B flag-ON arm runs (the permanent 5-type list
    ``kinship/adversarial/mentorship/political/partnership`` + a volatile tail cap
    of 10), so a shrink test built on it reflects real merged behaviour rather
    than the 7-type test ``_CFG_ON`` override (Finding #4).
    """
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "config", "llm.json"
    )
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    ctx_opt = dict(cfg.get("context_optimizations", {}))
    ctx_opt["relationship_type_tiering"] = True
    return {"context_optimizations": ctx_opt}


def _make_rel(
    target_id,
    rel_type="ally",
    status="active",
    last_updated_turn="turn-090",
    source_id="char-player",
    history=None,
):
    rel = {
        "source_id": source_id,
        "target_id": target_id,
        "type": rel_type,
        "status": status,
        "last_updated_turn": last_updated_turn,
        "current_relationship": f"test rel to {target_id}",
    }
    if history is not None:
        rel["history"] = history
    return rel


def _make_pc_entry(relationships=None):
    entry = {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-100",
        "identity": "The protagonist",
        "current_status": "active",
        "stable_attributes": {"species": "human", "class": "ranger", "aliases": ["Hero"]},
        "volatile_state": {"equipment": [{"turn": "turn-100", "value": "sword"}]},
    }
    if relationships is not None:
        entry["relationships"] = relationships
    return entry


# ===========================================================================
# _get_type_tiering_config
# ===========================================================================

class TestGetTypeTieringConfig:
    def test_returns_disabled_by_default(self):
        enabled, perm, cap = se._get_type_tiering_config(None)
        assert enabled is False

    def test_returns_disabled_when_flag_false(self):
        enabled, perm, cap = se._get_type_tiering_config(_CFG_OFF)
        assert enabled is False

    def test_returns_enabled_when_flag_true(self):
        enabled, perm, cap = se._get_type_tiering_config(_CFG_ON)
        assert enabled is True

    @pytest.mark.parametrize(
        "bad_flag",
        ["false", "False", "true", "0", "1", [False], [True], 1, {}, [], 0.0],
    )
    def test_non_bool_flag_does_not_enable(self, bad_flag):
        """Finding #5: a non-bool (truthy or not) must NOT enable the
        default-OFF safety gate.  Permissive ``bool(...)`` would treat the
        string ``"false"`` or ``[False]`` as enabled; strict parsing accepts
        only a real JSON ``true``.  Behaviour-neutral for the A/B configs, which
        use real JSON booleans."""
        cfg = {"context_optimizations": {"relationship_type_tiering": bad_flag}}
        enabled, _perm, _cap = se._get_type_tiering_config(cfg)
        assert enabled is False

    def test_real_true_bool_enables(self):
        """Only a real JSON ``true`` flips the gate ON (strict parse, #5)."""
        cfg = {"context_optimizations": {"relationship_type_tiering": True}}
        enabled, _perm, _cap = se._get_type_tiering_config(cfg)
        assert enabled is True

    def test_default_permanent_types(self):
        # Omit pc_rel_permanent_types so the built-in default list is exercised.
        cfg = {"context_optimizations": {"relationship_type_tiering": True}}
        enabled, perm, cap = se._get_type_tiering_config(cfg)
        assert "kinship" in perm
        assert "adversarial" in perm
        assert "mentorship" in perm
        assert "political" in perm
        assert "partnership" in perm

    def test_default_volatile_tail_cap(self):
        # Omit pc_rel_volatile_tail_cap so the built-in default is exercised.
        cfg = {"context_optimizations": {"relationship_type_tiering": True}}
        enabled, perm, cap = se._get_type_tiering_config(cfg)
        assert cap == 10

    def test_custom_cap(self):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_volatile_tail_cap": 5,
            }
        }
        _, _, cap = se._get_type_tiering_config(cfg)
        assert cap == 5

    def test_custom_permanent_types(self):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_permanent_types": ["kinship"],
            }
        }
        _, perm, _ = se._get_type_tiering_config(cfg)
        assert perm == frozenset({"kinship"})

    def test_default_permanent_types_subset_of_schema_enum(self):
        """The built-in default permanent-type list must be a subset of the
        relationship `type` enum in schemas/entity.schema.json (N2 fragility
        guard, #477).  A type not in that enum can never appear on a validated
        relationship, so listing it here would be dead config; conversely this
        keeps the permanent list honest as the schema evolves.  Operators may
        still override with arbitrary custom types — only the *default* is
        pinned to the schema enum.
        """
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "entity.schema.json"
        )
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
        rel_type_enum = set(
            schema["properties"]["relationships"]["items"]["properties"]["type"]["enum"]
        )
        default_perm = set(se._PC_REL_PERMANENT_TYPES_DEFAULT)
        assert default_perm, "default permanent-type list must not be empty"
        missing = default_perm - rel_type_enum
        assert not missing, (
            f"default pc_rel_permanent_types contains types not in the "
            f"relationship `type` schema enum: {sorted(missing)}"
        )


# ===========================================================================
# _apply_pc_rel_type_tier
# ===========================================================================

class TestApplyPcRelTypeTier:
    """Tests for the _apply_pc_rel_type_tier helper."""

    _PERM = frozenset({"kinship", "adversarial", "mentorship", "political",
                       "partnership", "factional", "social"})

    def test_keeps_all_permanent_types(self):
        """All permanent-bond type rels survive regardless of count."""
        rels = [
            _make_rel(f"char-kin-{i}", rel_type="kinship") for i in range(15)
        ] + [
            _make_rel(f"char-adv-{i}", rel_type="adversarial") for i in range(10)
        ]
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=10)
        result_ids = {r["target_id"] for r in result}
        for i in range(15):
            assert f"char-kin-{i}" in result_ids
        for i in range(10):
            assert f"char-adv-{i}" in result_ids

    def test_force_keeps_mentioned_volatile(self):
        """Mentioned-this-turn target is kept regardless of type/position."""
        rels = [
            _make_rel("char-social-99", rel_type="ally"),  # volatile, mentioned
        ] + [
            _make_rel(f"char-vol-{i}", rel_type="ally") for i in range(20)
        ]
        result = se._apply_pc_rel_type_tier(
            rels, {"char-social-99"}, self._PERM, volatile_tail_cap=5
        )
        result_ids = {r["target_id"] for r in result}
        assert "char-social-99" in result_ids

    def test_volatile_tail_cap_applied(self):
        """Volatile (non-permanent, non-mentioned) rels capped at volatile_tail_cap."""
        rels = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(30)
        ]
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=10)
        assert len(result) == 10

    def test_permanent_not_counted_against_volatile_cap(self):
        """Permanent bonds are kept on top of the volatile tail cap."""
        rels = (
            [_make_rel(f"char-kin-{i}", rel_type="kinship") for i in range(5)]
            + [_make_rel(f"char-vol-{i}", rel_type="ally",
                         last_updated_turn=f"turn-{100 - i:03d}") for i in range(20)]
        )
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=10)
        perm_count = sum(1 for r in result if r["type"] == "kinship")
        vol_count = sum(1 for r in result if r["type"] == "ally")
        assert perm_count == 5
        assert vol_count == 10

    def test_history_trimmed_to_3(self):
        """History arrays are trimmed to the last 3 entries."""
        rels = [
            _make_rel("char-x", rel_type="kinship",
                      history=[{"turn": f"turn-{i}"} for i in range(10)]),
        ]
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=10)
        assert len(result[0]["history"]) == 3

    def test_volatile_sorted_by_recency(self):
        """Volatile tail keeps the most recently updated rels."""
        rels = [
            _make_rel("char-old", rel_type="ally", last_updated_turn="turn-001"),
            _make_rel("char-new", rel_type="ally", last_updated_turn="turn-099"),
        ]
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=1)
        assert result[0]["target_id"] == "char-new"
        assert all(r["target_id"] != "char-old" for r in result)

    def test_early_kinship_survives(self):
        """A kinship relationship first seen at turn-001 survives the cap (d)."""
        early_kinship = _make_rel(
            "char-parent", rel_type="kinship", last_updated_turn="turn-001"
        )
        volatile_filler = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(50)
        ]
        rels = [early_kinship] + volatile_filler
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=10)
        result_ids = {r["target_id"] for r in result}
        assert "char-parent" in result_ids

    def test_early_adversarial_survives(self):
        """An adversarial relationship first seen at turn-050 survives (d)."""
        adv = _make_rel("char-villain", rel_type="adversarial", last_updated_turn="turn-050")
        volatile_filler = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(50)
        ]
        rels = [adv] + volatile_filler
        result = se._apply_pc_rel_type_tier(rels, set(), self._PERM, volatile_tail_cap=10)
        assert any(r["target_id"] == "char-villain" for r in result)


# ===========================================================================
# _format_prior_entity_context — flag OFF golden test (a)
# ===========================================================================

class TestFormatPriorEntityContextFlagOff:
    """(a) flag OFF must produce byte-identical output to pre-#477 baseline."""

    def _make_rels(self, n=5):
        return [
            _make_rel(f"char-npc-{i}", rel_type="ally") for i in range(n)
        ]

    def test_flag_off_no_relationships_unchanged(self):
        """PC entry with no relationships: flag OFF == no config (golden)."""
        entry = _make_pc_entry(relationships=None)
        out_none = se._format_prior_entity_context(
            entry, config=None, mentioned_ids=set(), current_turn_num=100
        )
        out_off = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        assert out_none == out_off

    def test_flag_off_with_relationships_unchanged(self):
        """PC with relationships: flag OFF produces same output as no config."""
        rels = self._make_rels(10)
        entry = _make_pc_entry(relationships=rels)
        out_none = se._format_prior_entity_context(
            entry, config=None, mentioned_ids=set(), current_turn_num=100
        )
        out_off = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        assert out_none == out_off

    def test_flag_off_preserves_all_relationships(self):
        """PC with 20 rels and flag OFF: all 20 included (no count cap)."""
        rels = [_make_rel(f"char-x-{i}") for i in range(20)]
        entry = _make_pc_entry(relationships=rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        parsed = json.loads(out)
        assert len(parsed["relationships"]) == 20


# ===========================================================================
# (a) flag OFF == pre-#477-main golden snapshot (Finding #1)
# ===========================================================================

class TestFlagOffMainGolden:
    """Finding #1: assert flag-OFF output is byte-identical to a *frozen*
    pre-#477-main golden snapshot for BOTH PC paths (no-arcs and arcs).

    The other flag-OFF tests compare the new code path config=None vs
    config=_CFG_OFF (new-vs-new); they prove the two new branches agree but not
    that either matches the actual pre-PR main output.  These goldens are
    captured literals checked in under tests/golden/pc_rel_tiering/, so any
    future change to the OFF path — by either branch — breaks the test.
    """

    def _golden_entry(self):
        """The exact deterministic entry used to capture the golden fixtures."""
        rels = [
            _make_rel(
                "char-mom", rel_type="kinship", last_updated_turn="turn-001",
                history=[
                    {"turn": f"turn-{i:03d}", "note": f"event {i}"}
                    for i in range(1, 6)
                ],
            ),
            _make_rel("char-rival", rel_type="adversarial", last_updated_turn="turn-050"),
            _make_rel("char-ally-1", rel_type="ally", last_updated_turn="turn-080"),
            _make_rel("char-ally-2", rel_type="ally", last_updated_turn="turn-095"),
        ]
        return _make_pc_entry(relationships=rels)

    def _arcs(self):
        return {
            "arcs": {
                tid: {
                    "arc_summary": [{"phase": "met"}, {"phase": "allied"}],
                    "current_relationship": "trusted ally",
                }
                for tid in ("char-mom", "char-rival", "char-ally-1", "char-ally-2")
            }
        }

    @staticmethod
    def _load_golden(name):
        with open(os.path.join(_GOLDEN_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_flag_off_noarcs_matches_main_golden(self):
        """No-arcs PC path: flag OFF == frozen pre-#477-main golden."""
        golden = self._load_golden("flag_off_noarcs.json")
        entry = self._golden_entry()
        out_none = se._format_prior_entity_context(
            entry, config=None, mentioned_ids=set(), current_turn_num=100
        )
        out_off = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        assert out_none == golden
        assert out_off == golden

    def test_flag_off_arcs_matches_main_golden(self):
        """Arcs PC path: flag OFF == frozen pre-#477-main golden."""
        golden = self._load_golden("flag_off_arcs.json")
        entry = self._golden_entry()
        arcs = self._arcs()
        out_none = se._format_prior_entity_context(
            entry, arcs_data=arcs, config=None, mentioned_ids=set(), current_turn_num=100
        )
        out_off = se._format_prior_entity_context(
            entry, arcs_data=arcs, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        assert out_none == golden
        assert out_off == golden


# ===========================================================================
# _format_prior_entity_context — flag ON tests (b)
# ===========================================================================

class TestFormatPriorEntityContextFlagOn:
    """(b) flag ON: keeps ALL permanent-bond types + force-keeps mentioned +
    caps volatile tail."""

    def test_keeps_all_permanent_bond_types(self):
        """All permanent-bond type rels survive when flag ON."""
        perm_rels = [
            _make_rel("char-mom", rel_type="kinship", last_updated_turn="turn-001"),
            _make_rel("char-enemy", rel_type="adversarial", last_updated_turn="turn-050"),
            _make_rel("char-mentor", rel_type="mentorship", last_updated_turn="turn-010"),
            _make_rel("char-king", rel_type="political", last_updated_turn="turn-020"),
            _make_rel("char-partner", rel_type="partnership", last_updated_turn="turn-030"),
            _make_rel("char-factional", rel_type="factional", last_updated_turn="turn-060"),
            _make_rel("char-social", rel_type="social", last_updated_turn="turn-070"),
        ]
        volatile_rels = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(50)
        ]
        entry = _make_pc_entry(relationships=perm_rels + volatile_rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=100
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        for rel in perm_rels:
            assert rel["target_id"] in result_ids, (
                f"Permanent-bond rel {rel['target_id']} ({rel['type']}) missing from output"
            )

    def test_force_keeps_mentioned_volatile(self):
        """A volatile rel whose target is mentioned this turn is force-kept."""
        volatile_rels = [
            _make_rel("char-mentioned", rel_type="ally", last_updated_turn="turn-001"),
        ] + [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(30)
        ]
        entry = _make_pc_entry(relationships=volatile_rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON,
            mentioned_ids={"char-mentioned"},
            current_turn_num=100,
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        assert "char-mentioned" in result_ids

    def test_volatile_tail_capped(self):
        """Volatile rels beyond the cap are dropped when flag ON."""
        volatile_rels = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(50)
        ]
        entry = _make_pc_entry(relationships=volatile_rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=100
        )
        parsed = json.loads(out)
        # volatile_tail_cap=10, 0 permanent, 0 mentioned
        assert len(parsed["relationships"]) == 10

    def test_flag_on_reduces_total_count(self):
        """Flag ON reduces relationship count vs flag OFF for large PC."""
        rels = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(30)
        ]
        entry = _make_pc_entry(relationships=rels)
        out_off = se._format_prior_entity_context(
            entry, config=_CFG_OFF, mentioned_ids=set(), current_turn_num=100
        )
        out_on = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=100
        )
        count_off = len(json.loads(out_off)["relationships"])
        count_on = len(json.loads(out_on)["relationships"])
        assert count_on < count_off


# ===========================================================================
# 109-relationship synthetic PC (c)
# ===========================================================================

class TestSyntheticPC109Relationships:
    """(c) Synthetic 109-relationship PC web shrinks to ~30-40."""

    def _build_109_rels(self):
        """Build a realistic 109-relationship distribution."""
        rels = []
        # ~20 permanent bonds
        for i in range(4):
            rels.append(_make_rel(f"char-kin-{i}", rel_type="kinship",
                                  last_updated_turn=f"turn-{10 + i:03d}"))
        for i in range(5):
            rels.append(_make_rel(f"char-adv-{i}", rel_type="adversarial",
                                  last_updated_turn=f"turn-{20 + i:03d}"))
        for i in range(3):
            rels.append(_make_rel(f"char-mentor-{i}", rel_type="mentorship",
                                  last_updated_turn=f"turn-{15 + i:03d}"))
        for i in range(3):
            rels.append(_make_rel(f"char-pol-{i}", rel_type="political",
                                  last_updated_turn=f"turn-{30 + i:03d}"))
        for i in range(3):
            rels.append(_make_rel(f"char-part-{i}", rel_type="partnership",
                                  last_updated_turn=f"turn-{40 + i:03d}"))
        rels.append(_make_rel("char-factional-0", rel_type="factional",
                              last_updated_turn="turn-060"))
        rels.append(_make_rel("char-social-0", rel_type="social",
                              last_updated_turn="turn-070"))
        # ~89 volatile rels
        for i in range(89):
            rels.append(_make_rel(f"char-vol-{i}", rel_type="ally",
                                  last_updated_turn=f"turn-{100 - (i % 90):03d}"))
        assert len(rels) == 109
        return rels

    def test_shrinks_to_30_40(self):
        """109-rel PC with flag ON shrinks to ~30-40 total retained."""
        rels = self._build_109_rels()
        entry = _make_pc_entry(relationships=rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=344
        )
        parsed = json.loads(out)
        count = len(parsed["relationships"])
        assert 25 <= count <= 45, (
            f"Expected 25-45 retained relationships, got {count}"
        )

    def test_no_permanent_type_dropped(self):
        """No permanent-bond type relationship is dropped in the 109-rel case."""
        rels = self._build_109_rels()
        entry = _make_pc_entry(relationships=rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=344
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        perm_types = {
            "kinship", "adversarial", "mentorship", "political",
            "partnership", "factional", "social",
        }
        perm_rels = [r for r in rels if r["type"] in perm_types]
        for rel in perm_rels:
            assert rel["target_id"] in result_ids, (
                f"Permanent rel {rel['target_id']} ({rel['type']}) was dropped"
            )

    def test_mentioned_rel_survives_109(self):
        """A mentioned-this-turn volatile rel survives even in the 109-rel case."""
        rels = self._build_109_rels()
        # Make char-vol-88 mentioned this turn
        mentioned_target = "char-vol-88"
        entry = _make_pc_entry(relationships=rels)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON,
            mentioned_ids={mentioned_target},
            current_turn_num=344,
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        assert mentioned_target in result_ids

    def test_shrinks_with_production_default_config(self):
        """Finding #4: shrink test driven by the PRODUCTION config/llm.json
        type list (the permanent 5-type set + cap 10), not the 7-type test
        ``_CFG_ON`` override.

        This reflects what actually merges/runs in the A/B flag-ON arm.  Under
        the 5-type list, ``factional`` and ``social`` are volatile (not
        permanent), so they fall into the capped tail; only the 18 permanent
        bonds (kinship/adversarial/mentorship/political/partnership) are kept
        uncapped.  The 109-rel web must shrink to ~30-40 with no permanent bond
        dropped.
        """
        cfg = _load_prod_config_on()
        rels = self._build_109_rels()
        entry = _make_pc_entry(relationships=rels)
        out = se._format_prior_entity_context(
            entry, config=cfg, mentioned_ids=set(), current_turn_num=344
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        count = len(parsed["relationships"])
        assert 25 <= count <= 45, (
            f"Expected 25-45 retained relationships with production config, "
            f"got {count}"
        )
        # No permanent-bond (5-type production list) relationship dropped.
        prod_perm_types = {
            "kinship", "adversarial", "mentorship", "political", "partnership",
        }
        perm_rels = [r for r in rels if r["type"] in prod_perm_types]
        assert len(perm_rels) == 18  # sanity: matches the synthetic distribution
        for rel in perm_rels:
            assert rel["target_id"] in result_ids, (
                f"Permanent rel {rel['target_id']} ({rel['type']}) was dropped "
                f"under the production 5-type config"
            )


# ===========================================================================
# (d) Early kinship/adversarial callback anchor survives
# ===========================================================================

class TestEarlyCallbackAnchorSurvives:
    """(d) A <=t100 kinship or adversarial bond survives regardless of position."""

    def test_kinship_turn_001_survives(self):
        """A kinship bond first seen at turn-001 survives a large volatile tail."""
        early_kinship = _make_rel(
            "char-family-member", rel_type="kinship", last_updated_turn="turn-001"
        )
        volatile_filler = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{300 - i:03d}")
            for i in range(100)
        ]
        entry = _make_pc_entry(relationships=[early_kinship] + volatile_filler)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=344
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        assert "char-family-member" in result_ids

    def test_adversarial_turn_050_survives(self):
        """An adversarial bond from turn-050 survives a large volatile tail."""
        adv = _make_rel(
            "char-arch-enemy", rel_type="adversarial", last_updated_turn="turn-050"
        )
        volatile_filler = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{300 - i:03d}")
            for i in range(100)
        ]
        entry = _make_pc_entry(relationships=[adv] + volatile_filler)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=344
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        assert "char-arch-enemy" in result_ids

    def test_mentorship_early_survives(self):
        """A mentorship bond from turn-100 survives across 100 volatile rels."""
        mentor_rel = _make_rel(
            "char-mentor", rel_type="mentorship", last_updated_turn="turn-100"
        )
        volatile_filler = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{300 - i:03d}")
            for i in range(100)
        ]
        entry = _make_pc_entry(relationships=[mentor_rel] + volatile_filler)
        out = se._format_prior_entity_context(
            entry, config=_CFG_ON, mentioned_ids=set(), current_turn_num=344
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        assert "char-mentor" in result_ids


# ===========================================================================
# L4: _collect_existing_relationships type-tiering
# ===========================================================================

class TestCollectExistingRelationshipsL4:
    """L4 tests: permanent-bond types survive budget trim in relmap."""

    def _make_catalog_with_pc_rels(self, rels):
        """Build a minimal catalogs dict with the PC having the given rels."""
        pc_entry = {
            "id": "char-player",
            "name": "Player",
            "type": "character",
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-100",
            "relationships": rels,
        }
        return {
            "characters": [pc_entry],
            "locations": [],
            "items": [],
            "factions": [],
        }

    def test_flag_off_byte_identical_to_no_config(self):
        """L4: flag OFF produces same tier assignment as no config (golden)."""
        rels = [
            _make_rel("char-kin", rel_type="kinship"),
            _make_rel("char-vol", rel_type="ally"),
        ]
        catalogs = self._make_catalog_with_pc_rels(rels)
        out_none = se._collect_existing_relationships(
            catalogs, ["char-player"],
            config=None, current_turn_num=100
        )
        out_off = se._collect_existing_relationships(
            catalogs, ["char-player"],
            config=_CFG_OFF, current_turn_num=100
        )
        assert out_none == out_off

    def test_permanent_bond_promoted_over_volatile(self):
        """L4 ON: permanent bond not demoted to omit while volatile at tier-3."""
        # With a tight budget, tier-3 gets dropped first.
        # A permanent bond (kinship) + many volatile rels:
        # flag ON should protect the kinship rel.
        rels = (
            [_make_rel("char-kin", rel_type="kinship",
                       source_id="char-player", last_updated_turn="turn-001")]
            + [
                _make_rel(f"char-vol-{i}", rel_type="ally",
                          source_id="char-player",
                          last_updated_turn=f"turn-{80 + i:03d}")
                for i in range(20)
            ]
        )
        catalogs = self._make_catalog_with_pc_rels(rels)
        # char-player is mentioned, making one_mentioned=True for all PC rels
        out = se._collect_existing_relationships(
            catalogs, ["char-player"],
            config=_CFG_ON, current_turn_num=100,
            context_length=4096,  # small budget to force degradation
        )
        # kinship should appear in output
        assert "char-kin" in out

    def test_flag_on_same_result_structure(self):
        """L4 ON: output is still valid JSON."""
        rels = [
            _make_rel("char-kin", rel_type="kinship"),
            _make_rel("char-ally", rel_type="ally"),
        ]
        catalogs = self._make_catalog_with_pc_rels(rels)
        out = se._collect_existing_relationships(
            catalogs, ["char-player"],
            config=_CFG_ON, current_turn_num=100
        )
        if out:
            parsed = json.loads(out)
            assert isinstance(parsed, dict)


# ===========================================================================
# Defensive config parsing (Copilot review: malformed cap must not crash)
# ===========================================================================

class TestVolatileTailCapParsing:
    """A malformed pc_rel_volatile_tail_cap must never crash extraction,
    especially while the feature is OFF (the reader runs every turn)."""

    @pytest.mark.parametrize("bad_cap", [None, "ten", "10", [], {}, 3.5])
    def test_malformed_cap_does_not_crash_when_off(self, bad_cap):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": False,
                "pc_rel_volatile_tail_cap": bad_cap,
            }
        }
        enabled, _perm, cap = se._get_type_tiering_config(cfg)
        assert enabled is False
        assert isinstance(cap, int)
        assert cap >= 0

    @pytest.mark.parametrize("bad_cap", [None, "ten", [], {}])
    def test_non_numeric_cap_falls_back_to_default(self, bad_cap):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_volatile_tail_cap": bad_cap,
            }
        }
        _enabled, _perm, cap = se._get_type_tiering_config(cfg)
        assert cap == se._PC_REL_VOLATILE_TAIL_CAP_DEFAULT

    def test_numeric_string_cap_is_coerced(self):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_volatile_tail_cap": "5",
            }
        }
        _enabled, _perm, cap = se._get_type_tiering_config(cfg)
        assert cap == 5

    def test_negative_cap_clamped_to_zero(self):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_volatile_tail_cap": -3,
            }
        }
        _enabled, _perm, cap = se._get_type_tiering_config(cfg)
        assert cap == 0


class TestPermanentTypesParsing:
    """A malformed context_optimizations block or pc_rel_permanent_types value
    must never crash extraction. The reader runs every turn, so a bad config
    must degrade to defaults rather than raise — even while the feature is OFF."""

    @pytest.mark.parametrize("bad_ctx_opt", [[], "tiering", 42, None])
    def test_non_dict_context_optimizations_does_not_crash(self, bad_ctx_opt):
        cfg = {"context_optimizations": bad_ctx_opt}
        enabled, perm, cap = se._get_type_tiering_config(cfg)
        assert enabled is False
        assert perm == se._PC_REL_PERMANENT_TYPES_DEFAULT
        assert cap == se._PC_REL_VOLATILE_TAIL_CAP_DEFAULT

    def test_unhashable_permanent_type_entries_are_skipped(self):
        """A list containing unhashable values (e.g. {} / []) must not raise;
        only the string entries are kept."""
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_permanent_types": ["kinship", {}, [], 7, "political"],
            }
        }
        _enabled, perm, _cap = se._get_type_tiering_config(cfg)
        assert perm == frozenset({"kinship", "political"})

    @pytest.mark.parametrize("bad_types", ["kinship", 5, {"kinship": True}])
    def test_non_list_permanent_types_falls_back_to_default(self, bad_types):
        cfg = {
            "context_optimizations": {
                "relationship_type_tiering": True,
                "pc_rel_permanent_types": bad_types,
            }
        }
        _enabled, perm, _cap = se._get_type_tiering_config(cfg)
        assert perm == se._PC_REL_PERMANENT_TYPES_DEFAULT


# ===========================================================================
# PC + arcs path recency (Copilot review: arc summaries drop last_updated_turn)
# ===========================================================================

class TestPcArcsPathRecency:
    """On the PC+arcs path, arc-summarised rels lose last_updated_turn during
    compaction. Tiering must run on the RAW rels so recency ordering of the
    volatile tail is preserved, and flag-OFF must stay byte-identical."""

    def _arcs_for(self, target_ids):
        return {
            "arcs": {
                tid: {
                    "arc_summary": [{"phase": "met"}, {"phase": "allied"}],
                    "current_relationship": "trusted ally",
                }
                for tid in target_ids
            }
        }

    def test_recent_arc_summarised_volatile_kept_over_older(self):
        """A recent volatile rel that has an arc summary must outrank older
        volatile rels in the tail (it must not sort as turn-0)."""
        # 5 volatile rels, all with arc summaries; cap is 10 so all survive,
        # but we assert the most-recent ones are present and ordered by recency.
        recent = _make_rel("char-recent", rel_type="ally", last_updated_turn="turn-099")
        older = [
            _make_rel(f"char-old-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{10 + i:03d}")
            for i in range(15)
        ]
        all_rels = older + [recent]
        target_ids = [r["target_id"] for r in all_rels]
        entry = _make_pc_entry(relationships=all_rels)
        out = se._format_prior_entity_context(
            entry, arcs_data=self._arcs_for(target_ids),
            config=_CFG_ON, mentioned_ids=set(), current_turn_num=100,
        )
        parsed = json.loads(out)
        result_ids = [r["target_id"] for r in parsed["relationships"]]
        # cap=10 volatile: the most recent rel must survive the tail trim.
        assert "char-recent" in result_ids
        # The oldest rel (turn-010) should be trimmed before the recent one.
        assert "char-old-0" not in result_ids

    def test_arcs_path_flag_off_byte_identical(self):
        """Flag OFF on the PC+arcs path must equal the pre-feature output."""
        rels = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{50 + i:03d}")
            for i in range(8)
        ]
        target_ids = [r["target_id"] for r in rels]
        arcs = self._arcs_for(target_ids)
        entry = _make_pc_entry(relationships=rels)
        out_none = se._format_prior_entity_context(
            entry, arcs_data=arcs, config=None,
            mentioned_ids=set(), current_turn_num=100,
        )
        out_off = se._format_prior_entity_context(
            entry, arcs_data=arcs, config=_CFG_OFF,
            mentioned_ids=set(), current_turn_num=100,
        )
        assert out_none == out_off

    def test_arcs_path_permanent_bond_survives(self):
        """A permanent bond with an arc summary survives a large volatile tail
        on the PC+arcs path."""
        kin = _make_rel("char-kin", rel_type="kinship", last_updated_turn="turn-001")
        volatile = [
            _make_rel(f"char-vol-{i}", rel_type="ally",
                      last_updated_turn=f"turn-{100 - i:03d}")
            for i in range(50)
        ]
        all_rels = [kin] + volatile
        target_ids = [r["target_id"] for r in all_rels]
        entry = _make_pc_entry(relationships=all_rels)
        out = se._format_prior_entity_context(
            entry, arcs_data=self._arcs_for(target_ids),
            config=_CFG_ON, mentioned_ids=set(), current_turn_num=344,
        )
        parsed = json.loads(out)
        result_ids = {r["target_id"] for r in parsed["relationships"]}
        assert "char-kin" in result_ids
