"""Unit tests for the #495 delta-output relationship_mapper lever (epic #477).

Two independent, default-OFF context_optimizations flags plus a delta
system-prompt template and a single template-selector helper. OFF must be
byte-identical to baseline. Covers:

  1. byte-identical-when-OFF: template name + input block unchanged
  2. strict-bool gate parse: only literal ``true`` enables either flag
  3. prompt-metrics resolves the SAME template name as extraction
  4. flag-ON selects the delta template (and that file exists/loads)
  5. drop-history strips input history WITHOUT mutating the catalog
  6. quality contract: the delta template carries the materiality instruction
"""

import copy
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_rel(target_id, source_id="char-player", history=None):
    rel = {
        "source_id": source_id,
        "target_id": target_id,
        "current_relationship": "ally of",
        "type": "social",
        "direction": "outgoing",
        "status": "active",
        "confidence": 0.9,
        "first_seen_turn": "turn-010",
        "last_updated_turn": "turn-040",
    }
    if history is not None:
        rel["history"] = history
    return rel


def _catalog_with_rels():
    cats = _fresh_catalogs()
    cats["characters.json"] = [
        {
            "id": "char-player",
            "name": "Player",
            "relationships": [
                _make_rel(
                    "char-mentor",
                    history=[{"turn": "turn-020", "description": "stranger"}],
                ),
            ],
        },
        {"id": "char-mentor", "name": "Mentor", "relationships": []},
    ]
    return cats


# ---------------------------------------------------------------------------
# 2. Strict-bool gate parse (both flags)
# ---------------------------------------------------------------------------

class TestStrictBoolGates:
    def test_template_name_only_true_enables(self):
        cfg = {"context_optimizations": {"relationship_mapper_delta_output": True}}
        assert se._relationship_mapper_template_name(cfg) == "relationship-mapper-delta"

    def test_template_name_truthy_non_bool_disabled(self):
        for val in ("true", "True", 1, [False], [1], {}, {"x": 1}):
            cfg = {"context_optimizations": {"relationship_mapper_delta_output": val}}
            assert se._relationship_mapper_template_name(cfg) == "relationship-mapper", val

    def test_template_name_false_disables(self):
        cfg = {"context_optimizations": {"relationship_mapper_delta_output": False}}
        assert se._relationship_mapper_template_name(cfg) == "relationship-mapper"

    def test_template_name_missing_key_disables(self):
        assert se._relationship_mapper_template_name({"context_optimizations": {}}) == "relationship-mapper"

    def test_template_name_missing_block_disables(self):
        assert se._relationship_mapper_template_name({}) == "relationship-mapper"

    def test_template_name_none_config_disables(self):
        assert se._relationship_mapper_template_name(None) == "relationship-mapper"

    def test_template_name_malformed_block(self):
        assert se._relationship_mapper_template_name(
            {"context_optimizations": ["nope"]}
        ) == "relationship-mapper"

    def test_drop_history_only_true_enables(self):
        cfg = {"context_optimizations": {"relationship_mapper_drop_history": True}}
        assert se._relationship_mapper_drop_history_enabled(cfg) is True

    def test_drop_history_truthy_non_bool_disabled(self):
        for val in ("true", "True", 1, [False], [1], {}, {"x": 1}):
            cfg = {"context_optimizations": {"relationship_mapper_drop_history": val}}
            assert se._relationship_mapper_drop_history_enabled(cfg) is False, val

    def test_drop_history_false_disables(self):
        cfg = {"context_optimizations": {"relationship_mapper_drop_history": False}}
        assert se._relationship_mapper_drop_history_enabled(cfg) is False

    def test_drop_history_missing_and_none(self):
        assert se._relationship_mapper_drop_history_enabled({"context_optimizations": {}}) is False
        assert se._relationship_mapper_drop_history_enabled({}) is False
        assert se._relationship_mapper_drop_history_enabled(None) is False

    def test_drop_history_malformed_block(self):
        assert se._relationship_mapper_drop_history_enabled(
            {"context_optimizations": ["nope"]}
        ) is False


# ---------------------------------------------------------------------------
# 1 + 4. Template selection byte-identical-OFF / flag-ON
# ---------------------------------------------------------------------------

class TestTemplateSelection:
    def test_off_is_baseline_template(self):
        for cfg in (
            None,
            {},
            {"context_optimizations": {}},
            {"context_optimizations": {"relationship_mapper_delta_output": False}},
        ):
            assert se._relationship_mapper_template_name(cfg) == "relationship-mapper"

    def test_on_selects_delta_template_and_it_loads(self):
        cfg = {"context_optimizations": {"relationship_mapper_delta_output": True}}
        name = se._relationship_mapper_template_name(cfg)
        assert name == "relationship-mapper-delta"
        text = se.load_template(name)
        assert text and isinstance(text, str)


# ---------------------------------------------------------------------------
# 3. prompt-metrics resolves the SAME name as extraction
# ---------------------------------------------------------------------------

class TestPromptMetricsMatchesExtraction:
    def test_off_both_baseline(self):
        cfg = {"context_optimizations": {}}
        # The same helper is the single source of truth at both sites.
        assert (se._relationship_mapper_template_name(cfg)
                == se._relationship_mapper_template_name(cfg)
                == "relationship-mapper")

    def test_on_both_delta(self):
        cfg = {"context_optimizations": {"relationship_mapper_delta_output": True}}
        extraction_name = se._relationship_mapper_template_name(cfg)
        prompt_metrics_name = se._relationship_mapper_template_name(cfg)
        assert extraction_name == prompt_metrics_name == "relationship-mapper-delta"

    def test_single_resolution_and_all_call_sites_use_it(self):
        """Source-wiring guard: the relationship-mapper template name is

        resolved exactly once (from the single helper) and every production
        call site — prompt metrics, parallel, serial, and retry — consumes
        that one resolved variable rather than a hardcoded template string.
        This closes the coverage gap where helper-determinism was asserted but
        the call-site wiring was not.
        """
        src = inspect.getsource(se.extract_and_merge)

        # Resolved exactly once, from the single selector helper.
        assert src.count(
            "_rel_template_name = _relationship_mapper_template_name("
        ) == 1

        # No call site bypasses the resolved variable with a hardcoded name.
        for hardcoded in (
            'load_template("relationship-mapper")',
            "load_template('relationship-mapper')",
            'load_template("relationship-mapper-delta")',
            "load_template('relationship-mapper-delta')",
        ):
            assert hardcoded not in src, (
                f"hardcoded template bypass found: {hardcoded}"
            )

        # Prompt-metrics + serial + retry load via the resolved variable.
        assert src.count("load_template(_rel_template_name)") >= 3
        # Parallel path forwards the same resolved variable to the worker.
        assert "_rel_max_tokens, _rel_template_name," in src


# ---------------------------------------------------------------------------
# 1 + 5. drop-history input block: OFF identical / ON strips, never mutates
# ---------------------------------------------------------------------------

class TestDropHistoryInputBlock:
    def test_off_retains_history_identical_to_baseline(self):
        cats = _catalog_with_rels()
        out = se._collect_existing_relationships(
            cats, ["char-player", "char-mentor"], drop_history=False,
        )
        baseline = se._collect_existing_relationships(
            cats, ["char-player", "char-mentor"],
        )
        assert out == baseline
        assert "history" in out

    def test_on_strips_history_from_block(self):
        cats = _catalog_with_rels()
        out = se._collect_existing_relationships(
            cats, ["char-player", "char-mentor"], drop_history=True,
        )
        parsed = json.loads(out)
        for rels in parsed.values():
            for rel in rels:
                assert "history" not in rel

    def test_on_does_not_mutate_catalog(self):
        cats = _catalog_with_rels()
        before = copy.deepcopy(cats)
        original_rel = cats["characters.json"][0]["relationships"][0]
        original_history_id = id(original_rel["history"])
        se._collect_existing_relationships(
            cats, ["char-player", "char-mentor"], drop_history=True,
        )
        # Catalog object unchanged by value and the history list is the same object.
        assert cats == before
        assert original_rel.get("history") == [{"turn": "turn-020", "description": "stranger"}]
        assert id(cats["characters.json"][0]["relationships"][0]["history"]) == original_history_id

    def test_format_rel_by_tier_drop_history_non_mutating(self):
        rel = _make_rel("char-x", history=[{"turn": "turn-001", "description": "old"}])
        out = se._format_rel_by_tier(1, rel, drop_history=True)
        assert "history" not in out
        # Source dict still carries its history (copy, not mutation).
        assert rel["history"] == [{"turn": "turn-001", "description": "old"}]


# ---------------------------------------------------------------------------
# 6. Quality contract: the delta template carries the materiality instruction
# ---------------------------------------------------------------------------

class TestDeltaTemplateContract:
    def test_materiality_instruction_present(self):
        text = se.load_template("relationship-mapper-delta")
        # delta-output framing + the "omitting does NOT delete" safety contract
        assert "DELTA OUTPUT" in text
        assert "OMITTING a relationship does" in text
        assert "NOT delete it" in text
        # the four materiality triggers
        assert "NEW" in text
        assert "current_relationship has MATERIALLY changed" in text
        assert "type changed" in text
        assert "direction changed" in text
        # confidence drift is explicitly NOT a trigger
        assert "confidence drift alone is NOT a change" in text
        # empty-result contract
        assert '{"relationships": []}' in text

    def test_delta_keeps_type_enum_and_envelope(self):
        base = se.load_template("relationship-mapper")
        delta = se.load_template("relationship-mapper-delta")
        # field/type guidance preserved from the base template
        assert '"kinship", "partnership", "mentorship"' in delta
        assert "SPATIAL RELATIONSHIPS:" in delta
        assert "KINSHIP RELATIONSHIPS:" in delta
        # base template has NO delta instruction (byte-identical-OFF guard)
        assert "DELTA OUTPUT" not in base
