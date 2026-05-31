"""Tests for PR-1 compression instrumentation (faithful no-op).

These tests assert the measurement scaffolding added in PR-1 of the
smart-context-compression rework (#464):

* ``_record_prompt_tokens`` defaults ``raw_input_tokens`` to the compressed
  size, so ``compression_ratio == 1.0`` on every record.
* The new per-phase keys exist with sane defaults.
* ``turn_compression`` and the ``compression`` quality-signal block have the
  expected no-op shape and types.
* The three compressors expose backward-compatible ``return_stats`` paths.
* The turn-band aggregator emits a per-band table.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import agg_compression  # noqa: E402
from catalog_merger import (  # noqa: E402
    _estimate_tokens,
    format_known_entities_bounded,
)
from semantic_extraction import (  # noqa: E402
    _SECTION_COUNTER_KEYS,
    _build_compression_signals,
    _build_turn_compression,
    _collect_existing_relationships,
    _finalize_prompt_metrics,
    _record_prompt_tokens,
    _trim_entry_for_scene,
)


# ---------------------------------------------------------------------------
# _record_prompt_tokens / _finalize_prompt_metrics — faithful no-op
# ---------------------------------------------------------------------------

def test_record_no_raw_defaults_to_compressed():
    metrics: dict = {}
    _record_prompt_tokens(metrics, "discovery", "system ", "user prompt")
    entry = metrics["discovery"]
    assert entry["raw_input_tokens"] == entry["input_tokens"]
    assert entry["calls"] == 1


def test_finalize_ratio_is_one_for_noop():
    metrics: dict = {}
    _record_prompt_tokens(metrics, "discovery", "system ", "user prompt")
    _record_prompt_tokens(metrics, "discovery", "more ", "tokens here")
    out = _finalize_prompt_metrics(metrics)["discovery"]
    assert out["compressed_input_tokens"] == out["input_tokens"]
    assert out["raw_input_tokens"] == out["input_tokens"]
    assert out["compression_ratio"] == 1.0
    # Multi-call phase also gets an average.
    assert out["avg_per_call"] == out["input_tokens"] // out["calls"]


def test_record_default_section_counters_present_and_zero():
    metrics: dict = {}
    _record_prompt_tokens(metrics, "entity_detail", "s", "u")
    entry = metrics["entity_detail"]
    for key in _SECTION_COUNTER_KEYS:
        assert key in entry
        assert entry[key] == 0


def test_record_accumulates_section_stats():
    metrics: dict = {}
    _record_prompt_tokens(
        metrics, "entity_detail", "s", "u",
        section_stats={"volatile_snapshots_dropped": 3, "relationships_pruned": 2},
    )
    _record_prompt_tokens(
        metrics, "entity_detail", "s", "u",
        section_stats={"volatile_snapshots_dropped": 1},
    )
    entry = metrics["entity_detail"]
    assert entry["volatile_snapshots_dropped"] == 4
    assert entry["relationships_pruned"] == 2


def test_record_explicit_raw_tokens_drives_ratio():
    metrics: dict = {}
    _record_prompt_tokens(metrics, "relationship_mapper", "s", "u", raw_tokens=1000)
    out = _finalize_prompt_metrics(metrics)["relationship_mapper"]
    # Explicit raw larger than compressed -> ratio < 1 (this is PR-2 behavior;
    # PR-1 call sites never pass raw_tokens, keeping ratio == 1.0).
    assert out["raw_input_tokens"] == 1000
    assert out["compression_ratio"] < 1.0


def test_finalize_zero_raw_is_safe():
    out = _finalize_prompt_metrics(
        {"discovery": {"input_tokens": 0, "raw_input_tokens": 0, "calls": 1}}
    )["discovery"]
    assert out["compression_ratio"] == 1.0


# ---------------------------------------------------------------------------
# turn_compression block
# ---------------------------------------------------------------------------

def test_build_turn_compression_noop():
    metrics: dict = {}
    _record_prompt_tokens(metrics, "discovery", "s", "u")
    _record_prompt_tokens(metrics, "entity_detail", "ss", "uu")
    finalized = _finalize_prompt_metrics(metrics)
    tc = _build_turn_compression(finalized)
    assert tc["raw_input_tokens_total"] == tc["compressed_input_tokens_total"]
    assert tc["compression_ratio_total"] == 1.0
    assert tc["activated_phases"] == []


def test_build_turn_compression_activated_when_ratio_below_one():
    metrics: dict = {}
    _record_prompt_tokens(metrics, "entity_detail", "s", "u", raw_tokens=5000)
    finalized = _finalize_prompt_metrics(metrics)
    tc = _build_turn_compression(finalized)
    assert tc["activated_phases"] == ["entity_detail"]
    assert tc["compression_ratio_total"] < 1.0


# ---------------------------------------------------------------------------
# compression quality-signal block (9 signals) — no-op shape
# ---------------------------------------------------------------------------

def test_compression_signals_noop_shape():
    catalogs = {
        "characters": [
            {"id": "char-a", "name": "A", "type": "character",
             "relationships": [{"target_id": "char-b"}]},
            {"id": "char-b", "name": "B", "type": "character"},
        ],
    }
    mentioned = [{"id": "char-a"}, {"id": "char-b"}]
    sig = _build_compression_signals(catalogs, mentioned, "turn-007")
    assert sig["strategy"] == "baseline"
    assert sig["params"] == {}
    assert sig["dropped_entity_ids"] == []
    assert sig["dropped_entity_count"] == {}
    assert sig["dropped_relationship_count"] == 0
    assert sig["dropped_event_ids"] == []
    assert sig["dropped_plot_thread_ids"] == []
    assert sig["context_window_turn_floor"] is None
    assert sig["dropped_then_referenced"] == []
    ivt = sig["included_vs_total"]
    assert ivt["entities_included"] == ivt["entities_total"] == 2
    assert ivt["relationships_included"] == ivt["relationships_total"] == 1


# ---------------------------------------------------------------------------
# compressor return_stats backward-compatible plumbing
# ---------------------------------------------------------------------------

def test_format_known_entities_bounded_default_returns_str():
    catalogs = {"characters": [{"id": "char-a", "name": "A", "type": "character"}]}
    out = format_known_entities_bounded(catalogs)
    assert isinstance(out, str)


def test_format_known_entities_bounded_return_stats_tuple():
    catalogs = {"characters": [{"id": "char-a", "name": "A", "type": "character"}]}
    text, stats = format_known_entities_bounded(catalogs, return_stats=True)
    assert isinstance(text, str)
    assert set(stats) == {
        "raw_tokens", "catalog_entries_pruned", "catalog_entries_degraded",
    }
    # No budget pressure -> nothing pruned or degraded.
    assert stats["catalog_entries_pruned"] == 0
    assert stats["catalog_entries_degraded"] == 0
    assert stats["raw_tokens"] >= 0


def test_format_known_entities_bounded_stats_match_str_path():
    catalogs = {"characters": [
        {"id": f"char-{i}", "name": f"N{i}", "type": "character"}
        for i in range(5)
    ]}
    plain = format_known_entities_bounded(catalogs)
    text, _ = format_known_entities_bounded(catalogs, return_stats=True)
    assert text == plain


def test_collect_existing_relationships_default_returns_str():
    catalogs = {"characters": [
        {"id": "char-a", "name": "A", "type": "character",
         "relationships": [{"source_id": "char-a", "target_id": "char-b",
                            "relationship_type": "ally"}]},
    ]}
    out = _collect_existing_relationships(catalogs, ["char-a"])
    assert isinstance(out, str)


def test_collect_existing_relationships_return_stats():
    catalogs = {"characters": [
        {"id": "char-a", "name": "A", "type": "character",
         "relationships": [{"source_id": "char-a", "target_id": "char-b",
                            "relationship_type": "ally"}]},
    ]}
    out, stats = _collect_existing_relationships(
        catalogs, ["char-a"], return_stats=True,
    )
    assert isinstance(out, str)
    assert set(stats) == {
        "raw_tokens", "compressed_tokens",
        "relationships_pruned", "relationships_degraded",
    }


def test_collect_existing_relationships_empty_stats():
    out, stats = _collect_existing_relationships(
        {}, ["char-x"], return_stats=True,
    )
    assert out == ""
    assert stats["relationships_pruned"] == 0


def test_trim_entry_for_scene_default_returns_dict():
    entry = {"id": "char-a", "name": "A", "type": "character"}
    out = _trim_entry_for_scene(entry)
    assert isinstance(out, dict)


def test_trim_entry_for_scene_return_stats():
    entry = {
        "id": "char-a", "name": "A", "type": "character",
        "volatile_state": {"mood": ["a", "b", "c", "d", "e", "f"]},
    }
    out, stats = _trim_entry_for_scene(entry, return_stats=True)
    assert isinstance(out, dict)
    assert "volatile_snapshots_dropped" in stats
    assert "relationships_pruned" in stats
    # Six snapshots capped to _ARC_AWARE_MAX_VOLATILE_SNAPSHOTS (3) -> 3 dropped.
    assert stats["volatile_snapshots_dropped"] == 3


# ---------------------------------------------------------------------------
# aggregator — turn-band bucketing
# ---------------------------------------------------------------------------

def _make_record(turn_num: int, detail_tokens: int = 100) -> dict:
    return {
        "turn_id": f"turn-{turn_num:03d}",
        "prompt_metrics": {
            "entity_detail": {
                "input_tokens": detail_tokens,
                "raw_input_tokens": detail_tokens,
                "compressed_input_tokens": detail_tokens,
                "compression_ratio": 1.0,
                "calls": 6,
            },
            "relationship_mapper": {
                "input_tokens": 50,
                "raw_input_tokens": 50,
                "compressed_input_tokens": 50,
                "compression_ratio": 1.0,
                "calls": 1,
            },
        },
        "turn_compression": {
            "raw_input_tokens_total": detail_tokens + 50,
            "compressed_input_tokens_total": detail_tokens + 50,
            "compression_ratio_total": 1.0,
            "activated_phases": [],
        },
    }


def test_aggregate_bands_buckets_by_turn_index():
    records = [_make_record(n) for n in (5, 15, 30, 75)]
    agg = agg_compression.aggregate_bands(records)
    assert agg["1-20"]["n"] == 2
    assert agg["21-50"]["n"] == 1
    assert agg["51-100"]["n"] == 1
    assert agg["101+"]["n"] == 0
    # No-op data -> ratio 1.0 in every populated band.
    assert agg["1-20"]["ratio"] == 1.0
    assert agg["51-100"]["ratio"] == 1.0


def test_aggregate_bands_legacy_record_no_double_count():
    """A legacy record lacking ``turn_compression`` must contribute only its
    own per-phase totals, not the cumulative per-band accumulator (#464)."""
    legacy = [
        {
            "turn_id": "turn-001",
            "prompt_metrics": {
                "entity_detail": {"input_tokens": 100, "calls": 1},
                "relationship_mapper": {"input_tokens": 50, "calls": 1},
            },
        },
        {
            "turn_id": "turn-002",
            "prompt_metrics": {
                "entity_detail": {"input_tokens": 200, "calls": 1},
                "relationship_mapper": {"input_tokens": 60, "calls": 1},
            },
        },
    ]
    agg = agg_compression.aggregate_bands(legacy)
    band = agg["1-20"]
    assert band["n"] == 2
    # Per-record fallback: (100+50) + (200+60) = 410, NOT a cumulative
    # double-count (which would have yielded 150 + 410 = 560).
    assert band["raw_total"] == 410
    assert band["comp_total"] == 410
    # Per-phase accumulators are still the simple cross-record sums.
    assert band["phases"]["entity_detail"]["raw"] == 300
    assert band["phases"]["relationship_mapper"]["raw"] == 110
    # No-op: raw == comp -> ratio 1.0.
    assert band["ratio"] == 1.0


def test_format_band_table_lists_all_bands():
    records = [_make_record(n) for n in (5, 30, 75)]
    agg = agg_compression.aggregate_bands(records)
    table = agg_compression.format_band_table(agg, label="A")
    for label, _, _ in agg_compression.BANDS:
        assert label in table
    assert "Compression by turn band" in table


def test_parse_turn_number():
    assert agg_compression.parse_turn_number("turn-042") == 42
    assert agg_compression.parse_turn_number(None) is None
    assert agg_compression.parse_turn_number("nope") is None


def test_estimate_tokens_is_shared():
    # Sanity: the aggregator and instrumentation share the catalog tokenizer.
    assert _estimate_tokens("abcdef") >= 1
