"""Tests for tools/ab_paired_score.py — paired multi-run A/B scoring (#487).

Covers the three scoring primitives the issue calls out:

* paired delta computation on the entity_detail tokens-per-call metric,
* matched-call filtering (only score turns where every run made the same
  number of entity_detail calls), and
* the effect-size-vs-noise-floor summary (weighted Δ, per-turn mean/median,
  Cohen's d, and the separable verdict).
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import ab_paired_score as aps  # noqa: E402


def _rec(turn_id, calls, raw, comp=None):
    """Build a minimal extraction-log record with entity_detail metrics."""
    comp = raw if comp is None else comp
    return {
        "turn_id": turn_id,
        "prompt_metrics": {
            "entity_detail": {
                "calls": calls,
                "raw_input_tokens": raw,
                "compressed_input_tokens": comp,
                "input_tokens": comp,
            }
        },
    }


def _run(records):
    return {r["turn_id"]: r for r in records}


# ---------------------------------------------------------------------------
# phase_call_metric
# ---------------------------------------------------------------------------

def test_phase_call_metric_uses_raw_tokens():
    rec = _rec("turn-001", calls=2, raw=1000, comp=400)
    assert aps.phase_call_metric(rec) == (2, 1000)


def test_phase_call_metric_legacy_falls_back_to_compressed():
    rec = {
        "turn_id": "turn-001",
        "prompt_metrics": {"entity_detail": {"calls": 2, "input_tokens": 800}},
    }
    assert aps.phase_call_metric(rec) == (2, 800)


def test_phase_call_metric_zero_calls_is_none():
    rec = _rec("turn-001", calls=0, raw=0)
    assert aps.phase_call_metric(rec) is None


def test_phase_call_metric_missing_phase_is_none():
    assert aps.phase_call_metric({"turn_id": "t", "prompt_metrics": {}}) is None


# ---------------------------------------------------------------------------
# matched_call_turns — only turns where ALL runs agree on the call count
# ---------------------------------------------------------------------------

def test_matched_call_turns_includes_agreeing_turns():
    a1 = _run([_rec("turn-001", 2, 1000), _rec("turn-002", 3, 1500)])
    a2 = _run([_rec("turn-001", 2, 1100), _rec("turn-002", 3, 1600)])
    b1 = _run([_rec("turn-001", 2, 900), _rec("turn-002", 3, 1400)])
    b2 = _run([_rec("turn-001", 2, 950), _rec("turn-002", 3, 1450)])
    assert aps.matched_call_turns([a1, a2], [b1, b2]) == ["turn-001", "turn-002"]


def test_matched_call_turns_discards_divergent_call_counts():
    a1 = _run([_rec("turn-001", 2, 1000), _rec("turn-002", 3, 1500)])
    a2 = _run([_rec("turn-001", 2, 1100), _rec("turn-002", 4, 1600)])  # 4 != 3
    b1 = _run([_rec("turn-001", 2, 900), _rec("turn-002", 3, 1400)])
    b2 = _run([_rec("turn-001", 2, 950), _rec("turn-002", 3, 1450)])
    # turn-002 diverges (3 vs 4) -> only turn-001 scored.
    assert aps.matched_call_turns([a1, a2], [b1, b2]) == ["turn-001"]


def test_matched_call_turns_requires_presence_in_all_runs():
    a1 = _run([_rec("turn-001", 2, 1000), _rec("turn-002", 3, 1500)])
    b1 = _run([_rec("turn-001", 2, 900)])  # missing turn-002
    assert aps.matched_call_turns([a1], [b1]) == ["turn-001"]


def test_matched_call_turns_sorted_numerically():
    a1 = _run([_rec("turn-010", 1, 100), _rec("turn-002", 1, 100)])
    b1 = _run([_rec("turn-010", 1, 100), _rec("turn-002", 1, 100)])
    assert aps.matched_call_turns([a1], [b1]) == ["turn-002", "turn-010"]


# ---------------------------------------------------------------------------
# paired_deltas — B - A tokens-per-call, averaged across each variant's runs
# ---------------------------------------------------------------------------

def test_paired_deltas_basic():
    # turn-001: A calls=2 raw=1000 -> 500/call ; B raw=900 -> 450/call ; Δ=-50
    a1 = _run([_rec("turn-001", 2, 1000)])
    b1 = _run([_rec("turn-001", 2, 900)])
    matched = aps.matched_call_turns([a1], [b1])
    rows = aps.paired_deltas([a1], [b1], matched)
    assert len(rows) == 1
    row = rows[0]
    assert row["a_tokens_per_call"] == 500.0
    assert row["b_tokens_per_call"] == 450.0
    assert row["delta"] == -50.0


def test_paired_deltas_averages_runs_per_variant():
    # A two runs: raw 1000 & 1200 at calls=2 -> per-call 500 & 600 -> mean 550
    a1 = _run([_rec("turn-001", 2, 1000)])
    a2 = _run([_rec("turn-001", 2, 1200)])
    # B two runs: raw 800 & 900 at calls=2 -> per-call 400 & 450 -> mean 425
    b1 = _run([_rec("turn-001", 2, 800)])
    b2 = _run([_rec("turn-001", 2, 900)])
    matched = aps.matched_call_turns([a1, a2], [b1, b2])
    rows = aps.paired_deltas([a1, a2], [b1, b2], matched)
    row = rows[0]
    assert row["a_tokens_per_call"] == 550.0
    assert row["b_tokens_per_call"] == 425.0
    assert row["delta"] == -125.0
    assert row["a_raw_total"] == 2200
    assert row["a_calls_total"] == 4


# ---------------------------------------------------------------------------
# summarize — effect size vs noise floor
# ---------------------------------------------------------------------------

def test_summarize_empty_is_safe():
    s = aps.summarize([], noise_floor=5.0)
    assert s["n_matched"] == 0
    assert s["separable"] is False
    assert s["weighted_delta"] is None


def test_summarize_weighted_and_separable_above_noise():
    a1 = _run([_rec("turn-001", 1, 1000), _rec("turn-002", 1, 1000)])
    b1 = _run([_rec("turn-001", 1, 1100), _rec("turn-002", 1, 1100)])
    matched = aps.matched_call_turns([a1], [b1])
    rows = aps.paired_deltas([a1], [b1], matched)
    s = aps.summarize(rows, noise_floor=5.0)
    assert s["n_matched"] == 2
    # weighted: A 2000/2=1000, B 2200/2=1100 -> Δ=+100
    assert s["weighted_a"] == 1000.0
    assert s["weighted_b"] == 1100.0
    assert s["weighted_delta"] == 100.0
    assert s["mean_delta"] == 100.0
    assert s["median_delta"] == 100.0
    assert s["effect_vs_noise"] == 100.0 / 5.0
    assert s["separable"] is True


def test_summarize_within_noise_floor_not_separable():
    # Δ weighted of +3 against a noise floor of 5 -> not separable.
    a1 = _run([_rec("turn-001", 1, 100)])
    b1 = _run([_rec("turn-001", 1, 103)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    assert s["weighted_delta"] == 3.0
    assert s["separable"] is False
    assert s["effect_vs_noise"] == 3.0 / 5.0


def test_summarize_cohens_d_zero_variance_is_none():
    a1 = _run([_rec("turn-001", 1, 100), _rec("turn-002", 1, 100)])
    b1 = _run([_rec("turn-001", 1, 110), _rec("turn-002", 1, 110)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    # Both per-turn deltas are +10 -> zero stdev -> Cohen's d undefined.
    assert s["stdev_delta"] == 0.0
    assert s["cohens_d"] is None


def test_summarize_cohens_d_with_variance():
    a1 = _run([_rec("turn-001", 1, 100), _rec("turn-002", 1, 100)])
    b1 = _run([_rec("turn-001", 1, 110), _rec("turn-002", 1, 130)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    # deltas +10, +30 -> mean 20, stdev 14.142 -> d ~ 1.414
    assert s["mean_delta"] == 20.0
    assert round(s["cohens_d"], 3) == round(20.0 / s["stdev_delta"], 3)
    assert s["cohens_d"] > 0


# ---------------------------------------------------------------------------
# load_log + main (end-to-end through JSON output)
# ---------------------------------------------------------------------------

def _write_log(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_load_log_dedups_turn_ids(tmp_path):
    p = tmp_path / "extraction-log.jsonl"
    _write_log(p, [_rec("turn-001", 1, 100), _rec("turn-001", 1, 200)])
    log = aps.load_log(str(p))
    assert log["turn-001"]["prompt_metrics"]["entity_detail"]["raw_input_tokens"] == 200


def test_load_log_accepts_directory(tmp_path):
    _write_log(tmp_path / "extraction-log.jsonl", [_rec("turn-001", 1, 100)])
    log = aps.load_log(str(tmp_path))
    assert "turn-001" in log


def test_main_json_end_to_end(tmp_path, capsys):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write_log(a, [_rec("turn-001", 2, 1000), _rec("turn-002", 2, 1000)])
    _write_log(b, [_rec("turn-001", 2, 1100), _rec("turn-002", 2, 1100)])
    rc = aps.main(["--a", str(a), "--b", str(b), "--noise-floor", "5", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["n_matched"] == 2
    assert out["summary"]["weighted_delta"] == 50.0  # per-call: 500 -> 550
    assert out["summary"]["separable"] is True


def test_main_requires_both_variants(capsys):
    rc = aps.main(["--a", "x.jsonl"])
    assert rc == 2
    assert "required" in capsys.readouterr().err
