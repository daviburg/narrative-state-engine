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
    _entity_detail_raw_instrumentation_enabled,
    _finalize_prompt_metrics,
    _format_relationships_budgeted,
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
    sig = _build_compression_signals(2, 1, mentioned, "turn-007")
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


def test_format_relationships_budgeted_returns_assigned_tiers_when_fits():
    """With ample budget no degradation occurs; effective == assigned."""
    scored = [
        (1, "char-a", {"source_id": "char-a", "target_id": "char-b",
                       "relationship_type": "ally"}),
        (3, "char-a", {"source_id": "char-a", "target_id": "char-c",
                       "relationship_type": "knows"}),
    ]
    text, effective = _format_relationships_budgeted(
        scored, budget=100000, return_effective=True,
    )
    assert isinstance(text, str)
    assert effective == scored
    assert sum(1 for t, _, _ in effective if t >= 4) == 0


def test_format_relationships_budgeted_reports_budget_driven_omission():
    """A tiny budget forces tier-3 -> omit; the EFFECTIVE tier list must
    reflect the omission so relationships_pruned is not under-reported
    (#464 reviewer finding)."""
    scored = [
        (3, "char-a", {"source_id": "char-a", "target_id": "char-b",
                       "relationship_type": "knows", "status": "active"}),
        (3, "char-a", {"source_id": "char-a", "target_id": "char-c",
                       "relationship_type": "knows", "status": "active"}),
    ]
    # Pre-budget assignment has zero tier-4 (no omissions); a budget of 1 token
    # forces both tier-3 rows to be degraded to omit.
    assert sum(1 for t, _, _ in scored if t >= 4) == 0
    _text, effective = _format_relationships_budgeted(
        scored, budget=1, return_effective=True,
    )
    assert sum(1 for t, _, _ in effective if t >= 4) == 2


def test_format_relationships_budgeted_default_returns_str_only():
    scored = [
        (1, "char-a", {"source_id": "char-a", "target_id": "char-b",
                       "relationship_type": "ally"}),
    ]
    out = _format_relationships_budgeted(scored, budget=100000)
    assert isinstance(out, str)


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


def test_aggregate_bands_skips_quota_exception_records():
    """Records with a turn_id but neither prompt_metrics nor turn_compression
    (e.g. quota / exception records) must NOT be counted as successful turns."""
    quota_record = {"turn_id": "turn-005"}  # neither field present
    normal = _make_record(10)
    agg = agg_compression.aggregate_bands([quota_record, normal])
    assert agg["1-20"]["n"] == 1, "quota record must not inflate turn count"
    assert agg["1-20"]["raw_total"] == normal["turn_compression"]["raw_input_tokens_total"]


def test_parse_turn_number():
    assert agg_compression.parse_turn_number("turn-042") == 42
    assert agg_compression.parse_turn_number(None) is None
    assert agg_compression.parse_turn_number("nope") is None


def test_estimate_tokens_is_shared():
    # Sanity: the aggregator and instrumentation share the catalog tokenizer.
    assert _estimate_tokens("abcdef") >= 1


# ---------------------------------------------------------------------------
# entity_detail raw_tokens captures TRUE pre-compaction size (#484)
# ---------------------------------------------------------------------------

def _detail_turn():
    return {"turn_id": "turn-100", "speaker": "dm", "text": "A quiet hallway."}


def test_entity_detail_raw_exceeds_compressed_when_compaction_fires():
    """A heavy entity whose relationships/volatile state get trimmed must
    record raw_tokens > compressed_tokens at the entity_detail call site (#484).

    Before the fix the call site passed no raw_tokens, so raw defaulted to the
    compressed size and the ratio was structurally pinned to 1.0.
    """
    from semantic_extraction import format_detail_prompt

    sys_tmpl = "entity-detail system template "
    # Many stale, unmentioned relationships -> scene filtering drops most of
    # them; many volatile snapshots per key -> digest/cap trims the tail.
    entry = {
        "id": "char-npc", "name": "Npc", "type": "character",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-002",
        "relationships": [
            {"source_id": "char-npc", "target_id": f"char-old-{i}",
             "relationship_type": "knows", "status": "active",
             "last_updated_turn": "turn-001",
             "notes": "a stale acquaintance from long ago " * 5}
            for i in range(40)
        ],
        "volatile_state": {
            "mood": [f"feeling number {i} described at length " * 4
                     for i in range(40)],
        },
    }
    ref = {"name": "Npc", "type": "character", "existing_id": "char-npc",
           "is_new": False}

    compacted, uncompacted = format_detail_prompt(
        _detail_turn(), ref, entry, mentioned_ids=set(), return_uncompacted=True,
    )
    comp_tokens = _estimate_tokens(sys_tmpl + compacted)
    raw_tokens = _estimate_tokens(sys_tmpl + uncompacted)
    assert raw_tokens > comp_tokens

    metrics: dict = {}
    _record_prompt_tokens(
        metrics, "entity_detail", sys_tmpl, compacted, raw_tokens=raw_tokens,
    )
    out = _finalize_prompt_metrics(metrics)["entity_detail"]
    assert out["raw_input_tokens"] > out["compressed_input_tokens"]
    assert out["compression_ratio"] < 1.0
    tc = _build_turn_compression(_finalize_prompt_metrics(metrics))
    assert tc["activated_phases"] == ["entity_detail"]


def test_entity_detail_raw_equals_compressed_when_nothing_to_compact():
    """A light entity with nothing to trim records raw == compressed, so
    the no-op case produces no false-positive compression signal (#484)."""
    from semantic_extraction import format_detail_prompt

    sys_tmpl = "entity-detail system template "
    entry = {
        "id": "char-npc", "name": "Npc", "type": "character",
        "first_seen_turn": "turn-001", "last_updated_turn": "turn-100",
        "identity": "a quiet figure",
        "stable_attributes": {"species": "human"},
    }
    ref = {"name": "Npc", "type": "character", "existing_id": "char-npc",
           "is_new": False}

    compacted, uncompacted = format_detail_prompt(
        _detail_turn(), ref, entry, mentioned_ids=set(), return_uncompacted=True,
    )
    assert compacted == uncompacted
    comp_tokens = _estimate_tokens(sys_tmpl + compacted)
    raw_tokens = _estimate_tokens(sys_tmpl + uncompacted)
    assert raw_tokens == comp_tokens

    metrics: dict = {}
    _record_prompt_tokens(
        metrics, "entity_detail", sys_tmpl, compacted, raw_tokens=raw_tokens,
    )
    out = _finalize_prompt_metrics(metrics)["entity_detail"]
    assert out["raw_input_tokens"] == out["compressed_input_tokens"]
    assert out["compression_ratio"] == 1.0


def test_format_detail_prompt_default_returns_str_and_unchanged():
    """return_uncompacted defaults False -> str return; the sent prompt is
    byte-identical to the compacted element of the tuple form (no change to
    what the model receives, #484)."""
    from semantic_extraction import format_detail_prompt

    entry = {
        "id": "char-npc", "name": "Npc", "type": "character",
        "relationships": [
            {"source_id": "char-npc", "target_id": f"char-old-{i}",
             "relationship_type": "knows", "status": "active",
             "last_updated_turn": "turn-001"}
            for i in range(20)
        ],
    }
    ref = {"name": "Npc", "type": "character", "existing_id": "char-npc",
           "is_new": False}
    plain = format_detail_prompt(_detail_turn(), ref, entry, mentioned_ids=set())
    assert isinstance(plain, str)
    compacted, _ = format_detail_prompt(
        _detail_turn(), ref, entry, mentioned_ids=set(), return_uncompacted=True,
    )
    assert plain == compacted


def test_entity_detail_raw_instrumentation_flag_default_and_gate():
    """The raw-token guardrail (#485) defaults ON and respects the config flag.

    The flag lets operators skip the per-entity uncompacted serialisation on
    long sessions so instrumentation cannot become a bottleneck.
    """
    # Default ON (preserves the #484 measurement) for missing/None/empty config.
    assert _entity_detail_raw_instrumentation_enabled(None) is True
    assert _entity_detail_raw_instrumentation_enabled({}) is True
    assert _entity_detail_raw_instrumentation_enabled("not-a-dict") is True
    # Explicit opt-out disables the uncompacted path.
    assert _entity_detail_raw_instrumentation_enabled(
        {"entity_detail_raw_instrumentation": False}) is False
    # Explicit opt-in stays ON.
    assert _entity_detail_raw_instrumentation_enabled(
        {"entity_detail_raw_instrumentation": True}) is True


def test_entity_detail_raw_disabled_falls_back_to_compressed():
    """With the guardrail OFF, omitting raw_tokens records raw == compressed
    (the pre-#484 behaviour), so a heavy entity no longer signals compression.

    This mirrors the disabled-path call site, which records the compacted
    prompt without supplying a raw_tokens value.
    """
    from semantic_extraction import format_detail_prompt

    sys_tmpl = "entity-detail system template "
    entry = {
        "id": "char-npc", "name": "Npc", "type": "character",
        "relationships": [
            {"source_id": "char-npc", "target_id": f"char-old-{i}",
             "relationship_type": "knows", "status": "active",
             "last_updated_turn": "turn-001",
             "notes": "a stale acquaintance from long ago " * 5}
            for i in range(40)
        ],
    }
    ref = {"name": "Npc", "type": "character", "existing_id": "char-npc",
           "is_new": False}

    # Disabled path: only the compacted (string) prompt is built; no raw_tokens.
    compacted = format_detail_prompt(_detail_turn(), ref, entry, mentioned_ids=set())
    assert isinstance(compacted, str)

    metrics: dict = {}
    _record_prompt_tokens(metrics, "entity_detail", sys_tmpl, compacted)
    out = _finalize_prompt_metrics(metrics)["entity_detail"]
    assert out["raw_input_tokens"] == out["compressed_input_tokens"]
    assert out["compression_ratio"] == 1.0


# ---------------------------------------------------------------------------
# _parse_args — label-to-path pairing preserves positional order
# ---------------------------------------------------------------------------

def test_parse_args_label_precedes_path():
    """``--label A run_a.jsonl --label B run_b.jsonl`` pairs correctly."""
    result = agg_compression._parse_args(
        ["--label", "A", "run_a.jsonl", "--label", "B", "run_b.jsonl"]
    )
    assert result == [("A", "run_a.jsonl"), ("B", "run_b.jsonl")]


def test_parse_args_label_after_first_path():
    """``run_a.jsonl --label B run_b.jsonl`` gives the first path no label."""
    result = agg_compression._parse_args(
        ["run_a.jsonl", "--label", "B", "run_b.jsonl"]
    )
    assert result == [(None, "run_a.jsonl"), ("B", "run_b.jsonl")]


def test_parse_args_no_labels():
    """Paths without labels all carry ``None``."""
    result = agg_compression._parse_args(["a.jsonl", "b.jsonl"])
    assert result == [(None, "a.jsonl"), (None, "b.jsonl")]


def test_parse_args_label_equals_form():
    """``--label=X path`` (equals form) is accepted."""
    result = agg_compression._parse_args(["--label=X", "x.jsonl"])
    assert result == [("X", "x.jsonl")]


# ---------------------------------------------------------------------------
# extract_and_merge — turn_compression and compression land in log record
# ---------------------------------------------------------------------------

def _make_stub_llm():
    """Minimal stub LLM that returns empty extraction results for all phases."""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.parallel_workers = 1
    llm.delay = MagicMock()
    llm.config = {}

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None):
        p = system_prompt.lower()
        if "discover" in p:
            return {"entities": []}
        if "detail" in p:
            return {"entity": {}}
        if "relationship" in p:
            return {"relationships": []}
        if "event" in p:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def test_extract_and_merge_log_record_has_compression_fields(monkeypatch):
    """extract_and_merge must include ``turn_compression`` and ``compression``
    in the returned log record so the aggregator and monitoring tooling can
    read both fields from the extraction-log.jsonl output."""
    import semantic_extraction as se
    from catalog_merger import CATALOG_KEYS

    monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
    se._reset_pc_failure_tracking()

    turn = {"turn_id": "turn-001", "speaker": "dm", "text": "All is quiet."}
    catalogs = {fn: [] for fn in CATALOG_KEYS}
    llm = _make_stub_llm()

    _cats, _events, _failed, log = se.extract_and_merge(
        turn, catalogs, [], llm, min_confidence=0.5,
    )

    assert "turn_compression" in log, "log record must contain 'turn_compression'"
    assert "compression" in log, "log record must contain 'compression'"

    # No-op run: ratio == 1.0, no phases activated.
    tc = log["turn_compression"]
    assert tc["compression_ratio_total"] == 1.0
    assert tc["activated_phases"] == []

    # compression quality-signal block must carry the strategy key.
    assert log["compression"]["strategy"] == "baseline"
