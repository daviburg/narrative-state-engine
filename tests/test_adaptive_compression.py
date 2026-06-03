"""Tests for adaptive, pressure-gated context compression (#460, epic #464).

Covers the PR-2 layer: the pressure gate, discovery floor, centrality backstop,
turn-total budget coordinator, the ``adaptive_compression_config`` resolver, and
the #465 instrumentation wiring (real raw-vs-compressed numbers when active,
faithful no-op when off).

Critical invariant: with the feature off (flag absent or ``enabled: false``)
``format_known_entities_bounded`` is byte-for-byte identical to pre-#460 main.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import (  # noqa: E402
    format_known_entities_bounded,
    adaptive_compression_config,
    compute_entity_centrality,
    centrality_exempt_ids,
    coordinate_turn_total,
    _estimate_tokens,
    _PRESSURE_GATE_FRACTION,
    _DISCOVERY_FLOOR_FRACTION,
    _TURN_TOTAL_BUDGET_FRACTION,
    _CENTRALITY_MIN_DEGREE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(eid, name, etype="character", identity="", aliases=None,
                 last_updated_turn=None, relationships=None):
    e = {"id": eid, "name": name, "type": etype}
    if identity:
        e["identity"] = identity
    if aliases:
        e["stable_attributes"] = {"aliases": {"value": aliases}}
    if last_updated_turn:
        e["last_updated_turn"] = last_updated_turn
    if relationships is not None:
        e["relationships"] = relationships
    return e


def _make_catalogs(entities):
    return {"characters.json": entities}


def _enabled_config(**overrides):
    ac = {
        "enabled": True,
        "pressure_gate_fraction": _PRESSURE_GATE_FRACTION,
        "discovery_floor_fraction": _DISCOVERY_FLOOR_FRACTION,
        "turn_total_budget_fraction": _TURN_TOTAL_BUDGET_FRACTION,
        "centrality_min_degree": _CENTRALITY_MIN_DEGREE,
        "centrality_exempt_top_n": None,
    }
    ac.update(overrides)
    return {"context_optimizations": {"adaptive_compression": ac}}


def _big_catalog(n=80, identity_len=120, turn="turn-300"):
    """A catalog large enough to exceed a small budget and force trimming.

    All entities share a recent ``last_updated_turn`` so that context-aware
    selection does not staleness-exclude them before the adaptive trimming
    passes run (we test the trimming, not the recency filter).
    """
    ents = []
    for i in range(n):
        ents.append(_make_entity(
            f"char-{i:03d}", f"Entity Number {i:03d}",
            identity="x" * identity_len, last_updated_turn=turn,
        ))
    return _make_catalogs(ents)


# ---------------------------------------------------------------------------
# adaptive_compression_config resolver
# ---------------------------------------------------------------------------

class TestResolver:
    def test_none_when_absent(self):
        assert adaptive_compression_config(None) is None
        assert adaptive_compression_config({}) is None
        assert adaptive_compression_config({"context_optimizations": {}}) is None

    def test_none_when_disabled(self):
        cfg = {"context_optimizations": {"adaptive_compression": {"enabled": False}}}
        assert adaptive_compression_config(cfg) is None

    def test_resolved_when_enabled(self):
        cfg = _enabled_config()
        resolved = adaptive_compression_config(cfg)
        assert resolved is not None
        assert resolved["pressure_gate_fraction"] == _PRESSURE_GATE_FRACTION
        assert resolved["discovery_floor_fraction"] == _DISCOVERY_FLOOR_FRACTION
        assert resolved["centrality_min_degree"] == _CENTRALITY_MIN_DEGREE

    def test_invalid_fraction_falls_back_to_default(self):
        cfg = _enabled_config(pressure_gate_fraction=5.0, discovery_floor_fraction="nope")
        resolved = adaptive_compression_config(cfg)
        assert resolved["pressure_gate_fraction"] == _PRESSURE_GATE_FRACTION
        assert resolved["discovery_floor_fraction"] == _DISCOVERY_FLOOR_FRACTION

    def test_top_n_zero_is_none(self):
        cfg = _enabled_config(centrality_exempt_top_n=0)
        assert adaptive_compression_config(cfg)["centrality_exempt_top_n"] is None

    def test_negative_min_degree_clamped_to_default(self):
        # A negative threshold would exempt every entity from trimming, defeating
        # compression — it must clamp back to the default (#468 review).
        cfg = _enabled_config(centrality_min_degree=-5)
        assert adaptive_compression_config(cfg)["centrality_min_degree"] == _CENTRALITY_MIN_DEGREE

    def test_negative_fractional_min_degree_clamped_to_default(self):
        # -0.5 truncates toward 0 with int(); the sign must be checked before
        # truncation so it still falls back to the default (#468 review).
        cfg = _enabled_config(centrality_min_degree=-0.5)
        assert adaptive_compression_config(cfg)["centrality_min_degree"] == _CENTRALITY_MIN_DEGREE

    def test_invalid_min_degree_falls_back_to_default(self):
        cfg = _enabled_config(centrality_min_degree="nope")
        assert adaptive_compression_config(cfg)["centrality_min_degree"] == _CENTRALITY_MIN_DEGREE


# ---------------------------------------------------------------------------
# Default-off pass-through (critical)
# ---------------------------------------------------------------------------

class TestDefaultOffPassThrough:
    def test_byte_identical_with_flag_absent(self):
        catalogs = _big_catalog()
        budget = 200
        baseline = format_known_entities_bounded(
            catalogs, current_turn=200, entity_context_budget=budget,
            turn_text="nothing matches here",
        )
        # adaptive=None is the production default-off state.
        adaptive = format_known_entities_bounded(
            catalogs, current_turn=200, entity_context_budget=budget,
            turn_text="nothing matches here", adaptive=None,
        )
        assert adaptive == baseline

    def test_resolver_returns_none_so_call_site_short_circuits(self):
        # The single guard the call sites rely on.
        assert adaptive_compression_config({"context_optimizations":
                {"adaptive_compression": {"enabled": False}}}) is None

    def test_stats_noop_raw_equals_compressed_when_off(self):
        catalogs = _make_catalogs([
            _make_entity("char-001", "Alice", identity="a hero"),
        ])
        text, stats = format_known_entities_bounded(
            catalogs, current_turn=1, entity_context_budget=100000,
            return_stats=True, adaptive=None,
        )
        assert stats["raw_tokens"] == _estimate_tokens(text)


# ---------------------------------------------------------------------------
# Pressure gate
# ---------------------------------------------------------------------------

class TestPressureGate:
    def test_below_gate_no_trimming(self):
        # Assemble context that exceeds budget but stays below the gate is
        # impossible (gate < budget), so instead: assemble context that is
        # under budget entirely -> full context returned in both paths.
        catalogs = _make_catalogs([
            _make_entity(f"char-{i:03d}", f"Name {i}", identity="short",
                         last_updated_turn="turn-100")
            for i in range(3)
        ])
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        # Large budget: assembled well below the gate -> full pass-through.
        out = format_known_entities_bounded(
            catalogs, current_turn=100, entity_context_budget=100000,
            turn_text="Name 0 Name 1 Name 2", adaptive=adaptive,
            centrality=centrality,
        )
        assert "Note:" not in out
        # All three entities present, none omitted.
        for i in range(3):
            assert f"char-{i:03d}" in out

    def test_gate_boundary_below_skips_trimming(self):
        # Budget chosen so assembled tokens sit just BELOW the gate -> no trim.
        catalogs = _big_catalog(n=40, identity_len=60)
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        full = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=100000,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        assembled = _estimate_tokens(full)
        # Pick a budget where assembled <= gate*budget i.e. budget large enough.
        budget_below = int(assembled / adaptive["pressure_gate_fraction"]) + 50
        out_below = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=budget_below,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        assert out_below == full  # below gate -> untouched

    def test_gate_boundary_above_engages_trimming(self):
        catalogs = _big_catalog(n=40, identity_len=60)
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        full = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=100000,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        # Small budget -> assembled far above gate -> trimming engages.
        small_budget = 150
        out_above = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=small_budget,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        assert _estimate_tokens(out_above) < _estimate_tokens(full)

    def test_gate_fraction_binds_between_gate_and_budget(self):
        # Regression for the #468 review: ``pressure_gate_fraction`` must
        # materially change behavior.  When assembled context sits between
        # ``gate * budget`` and ``budget`` (i.e. under hard overflow but above
        # the gate), a low gate (0.45) compresses proactively while a 1.0 gate
        # (compress only on hard overflow) leaves the context untouched.  If the
        # parameter were inert (the bug), both would be identical.
        catalogs = _big_catalog(n=40, identity_len=60)
        centrality = compute_entity_centrality(catalogs)
        full = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=100000,
            turn_text="zzz",
            adaptive=adaptive_compression_config(_enabled_config()),
            centrality=centrality,
        )
        assembled = _estimate_tokens(full)
        # Budget chosen so assembled ~= 70% of budget: above 0.45*budget but
        # below the budget itself (no hard overflow).
        budget = int(assembled / 0.7)
        low_gate = adaptive_compression_config(
            _enabled_config(pressure_gate_fraction=0.45))
        high_gate = adaptive_compression_config(
            _enabled_config(pressure_gate_fraction=1.0))
        out_low = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=budget,
            turn_text="zzz", adaptive=low_gate, centrality=centrality,
        )
        out_high = format_known_entities_bounded(
            catalogs, current_turn=40, entity_context_budget=budget,
            turn_text="zzz", adaptive=high_gate, centrality=centrality,
        )
        # gate == 1.0 -> no compression below hard budget overflow -> untouched.
        assert out_high == full
        # gate == 0.45 -> compresses proactively -> strictly smaller output.
        assert _estimate_tokens(out_low) < _estimate_tokens(out_high)


# ---------------------------------------------------------------------------
# Discovery floor
# ---------------------------------------------------------------------------

class TestDiscoveryFloor:
    def test_retained_never_below_floor(self):
        catalogs = _big_catalog(n=120, identity_len=200)
        budget = 300
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        out = format_known_entities_bounded(
            catalogs, current_turn=200, entity_context_budget=budget,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        # Strip the truncation note before measuring retained context.
        retained = out.split("\n\n(Note:")[0]
        floor = adaptive["discovery_floor_fraction"] * budget
        assert _estimate_tokens(retained) >= floor

    def test_floor_keeps_more_than_zero_entities_under_heavy_pressure(self):
        catalogs = _big_catalog(n=200, identity_len=300)
        budget = 100
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        out = format_known_entities_bounded(
            catalogs, current_turn=300, entity_context_budget=budget,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        retained = out.split("\n\n(Note:")[0]
        # Floor guarantees discovery is never starved to nothing (#393 guard).
        assert retained.strip() != ""
        assert "char-" in retained

    def test_floor_not_overshot_by_chunky_omissions(self):
        # Large per-entity lines make a single discrete omission able to drop the
        # retained context from above the floor to well below it; the omit passes
        # must revert that last cut rather than violate the floor (#468 review).
        for budget in (120, 180, 240, 360, 500):
            catalogs = _big_catalog(n=150, identity_len=400)
            adaptive = adaptive_compression_config(_enabled_config())
            centrality = compute_entity_centrality(catalogs)
            out = format_known_entities_bounded(
                catalogs, current_turn=300, entity_context_budget=budget,
                turn_text="zzz", adaptive=adaptive, centrality=centrality,
            )
            retained = out.split("\n\n(Note:")[0]
            floor = adaptive["discovery_floor_fraction"] * budget
            assert _estimate_tokens(retained) >= floor, f"floor violated at budget={budget}"


# ---------------------------------------------------------------------------
# Centrality backstop
# ---------------------------------------------------------------------------

class TestCentralityBackstop:
    def test_compute_centrality_counts_degree_and_inbound(self):
        catalogs = _make_catalogs([
            _make_entity("char-hub", "Hub", relationships=[
                {"target_id": "char-a"}, {"target_id": "char-b"},
            ]),
            _make_entity("char-a", "A", relationships=[{"target_id": "char-hub"}]),
            _make_entity("char-b", "B"),
        ])
        c = compute_entity_centrality(catalogs)
        # hub: outbound 2 + inbound 1 (from a) = 3
        assert c["char-hub"] == 3
        # a: outbound 1 + inbound 1 (from hub) = 2
        assert c["char-a"] == 2
        # b: inbound 1 (from hub) = 1
        assert c["char-b"] == 1

    def test_mention_frequency_contributes(self):
        catalogs = _make_catalogs([
            _make_entity("char-x", "X"),
        ])
        catalogs["characters.json"][0]["source_turns"] = ["turn-1", "turn-2", "turn-3"]
        c = compute_entity_centrality(catalogs)
        assert c["char-x"] == 3

    def test_high_centrality_entity_retained_while_peers_degraded(self):
        # One hub entity with high degree, many low-centrality peers.
        peers = [
            _make_entity(f"char-{i:03d}", f"Peer {i:03d}", identity="x" * 150,
                         last_updated_turn="turn-300")
            for i in range(60)
        ]
        hub = _make_entity(
            "char-hub", "Central Hub", identity="x" * 150,
            last_updated_turn="turn-300",
            relationships=[{"target_id": f"char-{i:03d}"} for i in range(10)],
        )
        catalogs = _make_catalogs(peers + [hub])
        budget = 200
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        assert centrality["char-hub"] >= adaptive["centrality_min_degree"]
        out = format_known_entities_bounded(
            catalogs, current_turn=300, entity_context_budget=budget,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        # The high-centrality hub survives at full detail (identity present),
        # even under pressure that degrades/omits its peers.
        assert "char-hub" in out
        assert "char-hub | Central Hub | character — " in out

    def test_exempt_ids_helper(self):
        centrality = {"char-a": 5, "char-b": 1, "char-c": 3}
        adaptive = adaptive_compression_config(_enabled_config(centrality_min_degree=3))
        exempt = centrality_exempt_ids(centrality, adaptive)
        assert exempt == {"char-a", "char-c"}

    def test_top_n_exemption(self):
        centrality = {"char-a": 5, "char-b": 4, "char-c": 1}
        adaptive = adaptive_compression_config(
            _enabled_config(centrality_min_degree=99, centrality_exempt_top_n=2))
        exempt = centrality_exempt_ids(centrality, adaptive)
        assert exempt == {"char-a", "char-b"}

    def test_last_resort_trims_exempt_when_over_hard_budget(self):
        # When *every* entity is centrality-exempt, the gated degrade/omit
        # passes (which skip exempt entities) cannot free any room, so the
        # known-entities section would otherwise exit far above its hard token
        # budget.  The last-resort pass must still omit exempt entities down to
        # the hard budget (while respecting the discovery floor) so the section
        # stays bounded and adaptive compression cannot cause a context overflow.
        n = 40
        catalogs = _big_catalog(n=n, identity_len=120)
        budget = 300
        adaptive = adaptive_compression_config(_enabled_config())
        # Force every entity above centrality_min_degree -> all exempt.
        centrality = {f"char-{i:03d}": 99 for i in range(n)}
        assert centrality_exempt_ids(centrality, adaptive) == set(centrality)
        out = format_known_entities_bounded(
            catalogs, current_turn=300, entity_context_budget=budget,
            turn_text="zzz", adaptive=adaptive, centrality=centrality,
        )
        # Retained content (excluding the truncation note) stays within the
        # hard budget, and some exempt entities were omitted to get there.
        retained = out.split("\n\n(Note:")[0]
        assert _estimate_tokens(retained) <= budget
        assert "Note:" in out
        # The discovery floor is still honored — not everything is stripped.
        floor = adaptive["discovery_floor_fraction"] * budget
        assert _estimate_tokens(retained) >= floor


# ---------------------------------------------------------------------------
# Turn-total budget coordinator
# ---------------------------------------------------------------------------

class TestTurnTotalBudget:
    def test_noop_when_adaptive_none(self):
        phases = [{"name": "a", "tokens": 100}, {"name": "b", "tokens": 200}]
        out = coordinate_turn_total(phases, 1000, None)
        assert sum(p["allocated"] for p in out) == 300

    def test_within_cap_untouched(self):
        adaptive = adaptive_compression_config(_enabled_config())
        phases = [{"name": "a", "tokens": 100, "priority": 0},
                  {"name": "b", "tokens": 100, "priority": 1}]
        out = coordinate_turn_total(phases, 1000, adaptive)
        assert sum(p["allocated"] for p in out) == 200

    def test_caps_total_when_over(self):
        adaptive = adaptive_compression_config(
            _enabled_config(turn_total_budget_fraction=0.5))
        # cap = 0.5 * 1000 = 500, total = 900 -> must trim 400.
        phases = [
            {"name": "discovery", "tokens": 400, "floor": 200, "priority": 1},
            {"name": "event", "tokens": 500, "floor": 0, "priority": 0},
        ]
        out = coordinate_turn_total(phases, 1000, adaptive)
        total = sum(p["allocated"] for p in out)
        assert total <= 500
        alloc = {p["name"]: p["allocated"] for p in out}
        # Discovery floor preserved; low-priority event trimmed first.
        assert alloc["discovery"] >= 200
        assert alloc["event"] <= 100

    def test_floor_never_violated_even_under_extreme_pressure(self):
        adaptive = adaptive_compression_config(
            _enabled_config(turn_total_budget_fraction=0.1))
        phases = [
            {"name": "discovery", "tokens": 500, "floor": 300, "priority": 1},
            {"name": "event", "tokens": 500, "floor": 0, "priority": 0},
        ]
        out = coordinate_turn_total(phases, 1000, adaptive)
        alloc = {p["name"]: p["allocated"] for p in out}
        assert alloc["discovery"] >= 300  # floor held


# ---------------------------------------------------------------------------
# Instrumentation: real numbers when active
# ---------------------------------------------------------------------------

class TestInstrumentationWiring:
    def test_stats_report_real_compression_when_active(self):
        catalogs = _big_catalog(n=120, identity_len=200)
        budget = 250
        adaptive = adaptive_compression_config(_enabled_config())
        centrality = compute_entity_centrality(catalogs)
        text, stats = format_known_entities_bounded(
            catalogs, current_turn=200, entity_context_budget=budget,
            turn_text="zzz", return_stats=True, adaptive=adaptive,
            centrality=centrality,
        )
        raw = stats["raw_tokens"]
        compressed = _estimate_tokens(text)
        assert compressed < raw  # real compression happened
        ratio = 1 - compressed / raw
        assert 0 < ratio < 1
        assert stats["catalog_entries_pruned"] > 0

    def test_compression_lines_emitted_with_real_numbers(self, capsys):
        # Exercise the per-turn compression/retention stderr lines with an
        # active (ratio < 1) finalized-metrics dict.
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import semantic_extraction as se

        finalized = {
            "discovery": {
                "input_tokens": 600,
                "raw_input_tokens": 1000,
                "compressed_input_tokens": 600,
                "compression_ratio": 0.6,
                "catalog_entries_pruned": 5,
                "catalog_entries_degraded": 3,
            },
            "entity_detail": {
                "input_tokens": 400,
                "raw_input_tokens": 400,
                "compressed_input_tokens": 400,
                "compression_ratio": 1.0,
                "catalog_entries_pruned": 2,
                "catalog_entries_degraded": 1,
                "volatile_snapshots_dropped": 0,
            },
            "relationship_mapper": {
                "input_tokens": 100,
                "raw_input_tokens": 100,
                "relationships_pruned": 0,
            },
        }
        turn_compression = se._build_turn_compression(finalized)
        assert turn_compression["compression_ratio_total"] < 1.0
        assert "discovery" in turn_compression["activated_phases"]

        se._print_compression_lines("turn-042", turn_compression, finalized, 7)
        err = capsys.readouterr().err
        assert "[COMPRESSION] turn-042" in err
        assert "[RETENTION]   turn-042" in err
        # Real numbers, not the no-op 1.00 pass-through.
        assert "ratio=0.70" in err or "ratio=0.7" in err
        assert "(ACTIVE)" in err
        assert "discovered=7" in err
