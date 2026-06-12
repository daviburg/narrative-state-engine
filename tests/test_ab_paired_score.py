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


def _rec(turn_id, calls, raw, comp=None, new_entities=0):
    """Build a minimal extraction-log record with entity_detail metrics."""
    comp = raw if comp is None else comp
    return {
        "turn_id": turn_id,
        "new_entities": new_entities,
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
    # 10 matched turns (>= MIN_SEPARABLE_MATCHED_TURNS), each +100 weighted,
    # CI excludes zero, Δ exceeds the noise floor -> SEPARABLE.
    a_recs = [_rec(f"turn-{i:03d}", 1, 1000) for i in range(1, 11)]
    b_recs = [_rec(f"turn-{i:03d}", 1, 1100) for i in range(1, 11)]
    a1 = _run(a_recs)
    b1 = _run(b_recs)
    matched = aps.matched_call_turns([a1], [b1])
    rows = aps.paired_deltas([a1], [b1], matched)
    s = aps.summarize(rows, noise_floor=5.0)
    assert s["n_matched"] == 10
    # weighted: A 10000/10=1000, B 11000/10=1100 -> Δ=+100
    assert s["weighted_a"] == 1000.0
    assert s["weighted_b"] == 1100.0
    assert s["weighted_delta"] == 100.0
    assert s["mean_delta"] == 100.0
    assert s["median_delta"] == 100.0
    assert s["effect_vs_noise"] == 100.0 / 5.0
    assert s["ci_excludes_zero"] is True
    assert s["exceeds_noise_floor"] is True
    assert s["separable"] is True


def test_summarize_not_separable_when_too_few_matched_turns():
    # Same +100 weighted effect but only 2 matched turns -> below the documented
    # MIN_SEPARABLE_MATCHED_TURNS, so NOT separable despite a huge effect.
    a1 = _run([_rec("turn-001", 1, 1000), _rec("turn-002", 1, 1000)])
    b1 = _run([_rec("turn-001", 1, 1100), _rec("turn-002", 1, 1100)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    assert s["n_matched"] == 2
    assert s["exceeds_noise_floor"] is True
    assert s["separable"] is False


def test_summarize_not_separable_when_ci_includes_zero():
    # Large, noisy per-turn deltas: weighted Δ may exceed the floor but the
    # paired-t CI straddles zero -> NOT separable.
    raws_a = [1000] * 12
    raws_b = [1000, 1000, 1000, 1000, 1000, 1000, 3000, 1, 2, 5000, 1, 3000]
    a1 = _run([_rec(f"turn-{i:03d}", 1, raws_a[i]) for i in range(12)])
    b1 = _run([_rec(f"turn-{i:03d}", 1, raws_b[i]) for i in range(12)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    assert s["n_matched"] == 12
    assert s["ci_excludes_zero"] is False
    assert s["separable"] is False


def test_summarize_within_noise_floor_not_separable():
    # Δ weighted of +3 against a noise floor of 5 -> not separable.
    a1 = _run([_rec("turn-001", 1, 100)])
    b1 = _run([_rec("turn-001", 1, 103)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    assert s["weighted_delta"] == 3.0
    assert s["exceeds_noise_floor"] is False
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
    a1 = tmp_path / "a1.jsonl"
    a2 = tmp_path / "a2.jsonl"
    b1 = tmp_path / "b1.jsonl"
    b2 = tmp_path / "b2.jsonl"
    a_recs = [_rec(f"turn-{i:03d}", 2, 1000) for i in range(1, 11)]
    b_recs = [_rec(f"turn-{i:03d}", 2, 1100) for i in range(1, 11)]
    _write_log(a1, a_recs)
    _write_log(a2, a_recs)
    _write_log(b1, b_recs)
    _write_log(b2, b_recs)
    rc = aps.main([
        "--a", str(a1), "--a", str(a2),
        "--b", str(b1), "--b", str(b2),
        "--noise-floor", "5", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["n_matched"] == 10
    assert out["summary"]["weighted_delta"] == 50.0  # per-call: 500 -> 550
    assert out["summary"]["separable"] is True
    assert out["population_turns"] == 10
    assert out["dropped_turns"] == 0
    assert out["prior_state"]["n_divergent"] == 0


def test_main_requires_both_variants(capsys):
    rc = aps.main(["--a", "x.jsonl", "--a", "y.jsonl"])
    assert rc == 2
    assert "required" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# N enforcement — N in {2,3} per variant, equal arms, no N=1 success path
# ---------------------------------------------------------------------------

def _valid_logs(tmp_path, n_a, n_b):
    a_recs = [_rec("turn-001", 2, 1000)]
    b_recs = [_rec("turn-001", 2, 1100)]
    a_paths, b_paths = [], []
    for i in range(n_a):
        p = tmp_path / f"a{i}.jsonl"
        _write_log(p, a_recs)
        a_paths.append(str(p))
    for i in range(n_b):
        p = tmp_path / f"b{i}.jsonl"
        _write_log(p, b_recs)
        b_paths.append(str(p))
    argv = []
    for p in a_paths:
        argv += ["--a", p]
    for p in b_paths:
        argv += ["--b", p]
    return argv


def test_validate_run_counts_accepts_n2_and_n3():
    assert aps._validate_run_counts(2, 2) is None
    assert aps._validate_run_counts(3, 3) is None


def test_validate_run_counts_rejects_n1():
    err = aps._validate_run_counts(1, 1)
    assert err is not None
    assert "N=1" in err


def test_validate_run_counts_rejects_too_many():
    assert aps._validate_run_counts(4, 4) is not None


def test_validate_run_counts_rejects_unequal_arms():
    err = aps._validate_run_counts(2, 3)
    assert err is not None
    assert "unequal" in err.lower()


def test_main_rejects_n1_per_variant(tmp_path, capsys):
    rc = aps.main(_valid_logs(tmp_path, 1, 1))
    assert rc == 2
    assert "N=1" in capsys.readouterr().err


def test_main_rejects_unequal_run_counts(tmp_path, capsys):
    rc = aps.main(_valid_logs(tmp_path, 2, 3))
    assert rc == 2
    assert "unequal" in capsys.readouterr().err.lower()


def test_main_accepts_n2_and_n3(tmp_path):
    assert aps.main(_valid_logs(tmp_path, 2, 2) + ["--json"]) == 0
    assert aps.main(_valid_logs(tmp_path, 3, 3) + ["--json"]) == 0


# ---------------------------------------------------------------------------
# --per-turn noise-floor unit guard (Copilot review): per-turn changes the
# floor unit to tok/turn, so the per-call default must NOT silently apply.
# ---------------------------------------------------------------------------

def _per_turn_logs(tmp_path, n=3, a_calls=6, a_tokens=12000, b_calls=2, b_tokens=6000):
    """N runs/arm where the same turn has a DIFFERENT call count per arm
    (the L2 batching shape): A makes a_calls, B makes b_calls."""
    a_recs = [_rec("turn-010", a_calls, a_tokens), _rec("turn-011", a_calls, a_tokens)]
    b_recs = [_rec("turn-010", b_calls, b_tokens), _rec("turn-011", b_calls, b_tokens)]
    argv = []
    for i in range(n):
        pa = tmp_path / f"pa{i}.jsonl"
        pb = tmp_path / f"pb{i}.jsonl"
        _write_log(pa, a_recs)
        _write_log(pb, b_recs)
        argv += ["--a", str(pa), "--b", str(pb)]
    return argv


def test_main_per_turn_requires_explicit_noise_floor(tmp_path, capsys):
    # No --noise-floor in per-turn mode -> error (the per-call default would be
    # a misleadingly tiny tok/turn floor).
    rc = aps.main(_valid_logs(tmp_path, 2, 2) + ["--per-turn", "--json"])
    assert rc == 2
    assert "noise-floor" in capsys.readouterr().err.lower()


def test_main_per_turn_with_explicit_floor_accepts_3_runs(tmp_path, capsys):
    # 3 runs/arm, per-turn mode, explicit tok/turn floor -> scores the per-turn
    # token saving over the matched-TURN population despite differing call
    # counts (the L2 acceptance scenario).
    rc = aps.main(_per_turn_logs(tmp_path, n=3) + [
        "--per-turn", "--noise-floor", "100", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "per_turn"
    assert out["summary"]["noise_floor"] == 100.0
    # Per-turn total dropped from 12000 to 6000 -> B saves 6000 tok/turn.
    assert out["summary"]["weighted_delta"] == -6000.0
    assert out["summary"]["n_matched"] == 2  # both turns matched by TURN


def test_main_per_call_still_defaults_noise_floor(tmp_path, capsys):
    # Per-call mode keeps the documented tok/call default when none is passed.
    rc = aps.main(_valid_logs(tmp_path, 2, 2) + ["--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["noise_floor"] == aps.DEFAULT_NOISE_FLOOR



# ---------------------------------------------------------------------------
# Survivorship / drop-rate reporting against the FULL population
# ---------------------------------------------------------------------------

def test_population_turns_is_union_not_intersection():
    a1 = _run([_rec("turn-001", 2, 1000), _rec("turn-002", 2, 1000)])
    b1 = _run([_rec("turn-001", 2, 1000), _rec("turn-003", 2, 1000)])
    # Union {001,002,003} = 3, even though only turn-001 is common/matched.
    assert aps.population_turns([a1], [b1]) == 3


def test_main_reports_drop_rate_against_population(tmp_path, capsys):
    # 3-turn population; turn-002 diverges in call count -> 2 matched, 1 dropped.
    a_recs = [_rec("turn-001", 2, 1000), _rec("turn-002", 2, 1000),
              _rec("turn-003", 2, 1000)]
    b_recs = [_rec("turn-001", 2, 1000), _rec("turn-002", 3, 1000),
              _rec("turn-003", 2, 1000)]
    a1, a2 = tmp_path / "a1.jsonl", tmp_path / "a2.jsonl"
    b1, b2 = tmp_path / "b1.jsonl", tmp_path / "b2.jsonl"
    for p in (a1, a2):
        _write_log(p, a_recs)
    for p in (b1, b2):
        _write_log(p, b_recs)
    rc = aps.main([
        "--a", str(a1), "--a", str(a2),
        "--b", str(b1), "--b", str(b2), "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["population_turns"] == 3
    assert out["summary"]["n_matched"] == 2
    assert out["dropped_turns"] == 1


def test_format_report_shows_survivorship_and_survivor_note():
    a1 = _run([_rec("turn-001", 2, 1000)])
    b1 = _run([_rec("turn-001", 2, 1100)])
    rows = aps.paired_deltas([a1], [b1], aps.matched_call_turns([a1], [b1]))
    s = aps.summarize(rows, noise_floor=5.0)
    report = aps.format_report(s, "entity_detail", 1, 1, 344)
    assert "1/344" in report
    assert "343 dropped" in report
    assert "SURVIVOR subset" in report
    assert "not 'not signal'" in report.lower() or "NOT 'not signal'" in report


def test_format_report_zero_matched_turns():
    s = aps.summarize([], noise_floor=5.0)
    report = aps.format_report(s, "entity_detail", 2, 2, 344)
    assert "0/344" in report
    assert "No matched-call-COUNT turns" in report


# ---------------------------------------------------------------------------
# Prior-state divergence (lower bound via cumulative new_entities proxy)
# ---------------------------------------------------------------------------

def test_prior_state_divergence_detects_divergent_prior():
    # turn-001 enters from empty prior in both runs (no divergence);
    # turn-002 enters after differing new_entities (5 vs 9) -> divergent.
    a1 = _run([
        _rec("turn-001", 2, 1000, new_entities=5),
        _rec("turn-002", 2, 1000, new_entities=0),
    ])
    b1 = _run([
        _rec("turn-001", 2, 1000, new_entities=9),
        _rec("turn-002", 2, 1000, new_entities=0),
    ])
    matched = aps.matched_call_turns([a1], [b1])
    res = aps.prior_state_divergence([a1], [b1], matched)
    assert res["n_checked"] == 2
    assert res["n_divergent"] == 1  # only turn-002 has a divergent prior proxy


def test_prior_state_divergence_none_when_priors_match():
    a1 = _run([
        _rec("turn-001", 2, 1000, new_entities=5),
        _rec("turn-002", 2, 1000, new_entities=0),
    ])
    b1 = _run([
        _rec("turn-001", 2, 1000, new_entities=5),
        _rec("turn-002", 2, 1000, new_entities=0),
    ])
    matched = aps.matched_call_turns([a1], [b1])
    res = aps.prior_state_divergence([a1], [b1], matched)
    assert res["n_divergent"] == 0


# ---------------------------------------------------------------------------
# Matched on COUNT only — same count, different (implied) call set still pools
# ---------------------------------------------------------------------------

def test_matched_call_same_count_different_token_load_still_matches():
    # Same call COUNT (2) but very different raw-token loads (different work per
    # call). The scorer matches on COUNT, so the turn is pooled, not dropped.
    a1 = _run([_rec("turn-001", 2, 1000)])
    b1 = _run([_rec("turn-001", 2, 4000)])
    matched = aps.matched_call_turns([a1], [b1])
    assert matched == ["turn-001"]
    rows = aps.paired_deltas([a1], [b1], matched)
    # 4000/2 - 1000/2 = 2000 - 500 = +1500 per-call delta is scored, not dropped.
    assert rows[0]["delta"] == 1500.0
