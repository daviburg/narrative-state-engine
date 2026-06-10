#!/usr/bin/env python3
"""
ab_paired_score.py — Paired, multi-run statistical A/B scoring on the
instrumented entity_detail token metric.

Replaces the single-pass byte-diff A/B comparison, which is invalid for this
(non-deterministic Vulkan / MoE) stack: a clean full-depth A==A determinism
test diverged on 84/344 turns for ``new_entities`` and 51/344 for
``new_events`` even with identical git HEAD, sampler, flag, mode, and seed.
A single-run diff therefore conflates real signal with run-to-run noise.

This tool implements the small paired multi-run methodology from #487:

  * N = 2-3 runs PER VARIANT, balanced (equal N on both arms; 4-6 logs total for
    a two-variant comparison).  N=1 and unequal arms are rejected: a single run
    cannot separate signal from the characterized run-to-run noise.
  * **Matched-call-COUNT scoring** — a turn is scored only when EVERY provided
    run (across both variants) made the same NUMBER of ``entity_detail`` calls
    for that turn.  The extraction log records only the per-phase call COUNT
    (``prompt_metrics.<phase>.calls``), not which entities were detailed in each
    call, so this matches on COUNT, not on the call *set*: two turns with the
    same count may still have detailed different entities.

    IMPORTANT — this is a SURVIVOR subset, not a neutral filter.  The A0 flag
    changes model output, which changes ``entity_detail`` call counts, so
    dropping divergent-count turns conditions the estimate on a POST-TREATMENT
    variable.  The result therefore describes only the surviving matched-count
    turns; the dropped turns are NOT "not signal".  The drop rate (matched /
    full population) is reported so a reader can judge how representative the
    survivor subset is.

    IMPORTANT — matched COUNT does NOT imply equivalent PRIOR catalog STATE.
    Because the flag desyncs catalog state cumulatively across turns, two turns
    with the same (turn, count) can enter from different prior catalogs, so the
    per-call token comparison is only strictly valid where prior state is
    equivalent.  The log does not record a prior-state hash, but it does record
    per-turn ``new_entities``; the tool reports, as a LOWER BOUND, how many
    matched turns have a divergent cumulative prior-entity-count proxy across
    runs (equal proxy does NOT prove equal content).
  * The scored metric is the #484-fixed **pre-compaction** entity_detail
    tokens-per-call (``raw_input_tokens`` / ``calls``).  Before #484 this value
    was pinned to the compressed prompt size (``compression_ratio == 1.0``),
    making the metric blind.
  * Reports a paired effect size (weighted Δ tok/call, per-turn mean / median Δ,
    Cohen's d, and a 95% paired-t confidence interval) **against the measured
    noise floor** (~5 tok/call weighted from the A==A control-vs-rerun
    baseline).  A result is declared SEPARABLE only when the paired-t 95% CI of
    the per-turn deltas excludes zero AND the weighted Δ exceeds the noise floor
    AND at least ``MIN_SEPARABLE_MATCHED_TURNS`` turns were matched — not on a
    bare 1x-noise cutoff.

Running the tool with two control reruns per side of a single variant (e.g.
``--a run1 --a run2 --b run3 --b run4``) reproduces the noise floor itself (that
is how the ~5 tok/call baseline was derived); it still requires the balanced
N=2-3 per side enforced for any comparison.

Usage:
    python tools/ab_paired_score.py \
        --a framework-ab-a-run1/extraction-log.jsonl \
        --a framework-ab-a-run2/extraction-log.jsonl \
        --b framework-ab-b-run1/extraction-log.jsonl \
        --b framework-ab-b-run2/extraction-log.jsonl \
        [--phase entity_detail] [--noise-floor 5.0] [--json]

Each ``--a`` / ``--b`` accepts either an ``extraction-log.jsonl`` file or a
framework/run directory containing one.  N must be 2 or 3 per variant and equal
on both arms.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys

DEFAULT_PHASE = "entity_detail"

# Measured weighted noise floor (tok/call) from the clean A==A control-vs-rerun
# baseline in #487.  This is the default tolerance band the effect size is
# judged against; re-measure it per model/backend (it is chosen, not inherited).
DEFAULT_NOISE_FLOOR = 5.0

# Balanced paired design: N runs per variant must be in this inclusive range and
# equal on both arms (#487 AC3).  N=1 cannot separate signal from the
# characterized run-to-run noise and is rejected.
MIN_RUNS_PER_VARIANT = 2
MAX_RUNS_PER_VARIANT = 3

# A SEPARABLE verdict additionally requires at least this many matched turns so
# the paired-t CI rests on a non-trivial sample (Rule 10: chosen and documented,
# not a bare cutoff).  Re-justify if the session length changes materially.
MIN_SEPARABLE_MATCHED_TURNS = 10

# Two-sided 95% Student-t critical values by degrees of freedom (df = n-1).
# Used for the paired-t CI on per-turn deltas; df > 30 falls back to the normal
# approximation (z ~ 1.96).  Avoids a scipy dependency for this stdlib tool.
_T_CRIT_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


def _t_critical_95(df: int) -> float:
    """Two-sided 95% t critical value for *df* degrees of freedom."""
    if df <= 0:
        return float("inf")
    if df in _T_CRIT_95:
        return _T_CRIT_95[df]
    return statistics.NormalDist().inv_cdf(0.975)


def _resolve_log_path(path: str) -> str:
    """Resolve *path* to an extraction-log.jsonl file.

    Accepts the JSONL file directly, a run/framework directory containing
    ``extraction-log.jsonl`` (optionally under a ``framework/`` subdir), and
    raises ``FileNotFoundError`` with a helpful message otherwise.
    """
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        for candidate in (
            os.path.join(path, "extraction-log.jsonl"),
            os.path.join(path, "framework", "extraction-log.jsonl"),
        ):
            if os.path.isfile(candidate):
                return candidate
    raise FileNotFoundError(
        f"No extraction-log.jsonl found at {path!r} "
        "(pass the .jsonl file or a directory containing it)."
    )


def load_log(path: str) -> dict[str, dict]:
    """Load an extraction log into a ``{turn_id: record}`` map.

    Later records for the same ``turn_id`` overwrite earlier ones, matching the
    resume/overwrite semantics of the extraction pipeline.  Records without a
    ``turn_id`` are skipped.
    """
    resolved = _resolve_log_path(path)
    records: dict[str, dict] = {}
    with open(resolved, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            turn_id = rec.get("turn_id")
            if turn_id:
                records[turn_id] = rec
    return records


def phase_call_metric(
    record: dict, phase: str = DEFAULT_PHASE
) -> tuple[int, int] | None:
    """Return ``(calls, raw_tokens)`` for *phase* in *record*.

    ``raw_tokens`` is the pre-compaction ``raw_input_tokens`` (#484); it falls
    back to ``input_tokens`` / ``compressed_input_tokens`` only for legacy
    records that predate the raw-token field.  Returns ``None`` when the phase
    is absent or made zero calls (nothing to score).
    """
    pm = record.get("prompt_metrics") or {}
    entry = pm.get(phase)
    if not entry:
        return None
    calls = int(entry.get("calls", 0) or 0)
    if calls <= 0:
        return None
    raw = entry.get("raw_input_tokens")
    if raw is None:
        raw = entry.get(
            "compressed_input_tokens", entry.get("input_tokens", 0)
        )
    return calls, int(raw or 0)


def matched_call_turns(
    a_runs: list[dict[str, dict]],
    b_runs: list[dict[str, dict]],
    phase: str = DEFAULT_PHASE,
) -> list[str]:
    """Turns where EVERY run (both variants) made the same *phase* call COUNT.

    A turn qualifies only when it is present with a non-zero phase call count in
    every provided run and all those call counts are identical.  This matches on
    the call COUNT only — the log does not record which entities were detailed
    per call — and it conditions on a post-treatment variable, so the qualifying
    set is a SURVIVOR subset (see :func:`population_turns` for the drop rate).
    Returns the qualifying turn IDs sorted by their numeric turn index.
    """
    all_runs = list(a_runs) + list(b_runs)
    if not all_runs:
        return []
    # Candidate turns = intersection of turn IDs across every run.
    common: set[str] | None = None
    for run in all_runs:
        ids = set(run.keys())
        common = ids if common is None else (common & ids)
    if not common:
        return []
    matched: list[str] = []
    for turn_id in common:
        call_counts: set[int] = set()
        ok = True
        for run in all_runs:
            metric = phase_call_metric(run[turn_id], phase)
            if metric is None:
                ok = False
                break
            call_counts.add(metric[0])
        if ok and len(call_counts) == 1:
            matched.append(turn_id)
    matched.sort(key=_turn_sort_key)
    return matched


def _turn_sort_key(turn_id: str) -> tuple[int, str]:
    """Sort key that orders ``turn-007`` before ``turn-012`` numerically."""
    digits = "".join(ch for ch in turn_id if ch.isdigit())
    return (int(digits) if digits else 0, turn_id)


def population_turns(
    a_runs: list[dict[str, dict]], b_runs: list[dict[str, dict]]
) -> int:
    """Size of the FULL turn population (union of turn IDs across all runs).

    This is the survivorship denominator: matched-count turns are scored against
    this full population (≈ the 344-turn session), NOT against the smaller
    intersection of turns common to every run, so the reported drop rate
    reflects how representative the survivor subset really is.
    """
    union: set[str] = set()
    for run in list(a_runs) + list(b_runs):
        union |= set(run.keys())
    return len(union)


def _cumulative_prior_entities(
    run: dict[str, dict],
) -> dict[str, int]:
    """Map each turn to the cumulative ``new_entities`` of all EARLIER turns.

    A proxy for the prior catalog SIZE entering each turn.  Equal proxies do not
    prove identical prior content, but a divergent proxy proves the prior state
    differs — so this gives a lower bound on prior-state divergence.
    """
    ordered = sorted(run.keys(), key=_turn_sort_key)
    prior: dict[str, int] = {}
    running = 0
    for turn_id in ordered:
        prior[turn_id] = running
        running += int(run[turn_id].get("new_entities", 0) or 0)
    return prior


def prior_state_divergence(
    a_runs: list[dict[str, dict]],
    b_runs: list[dict[str, dict]],
    matched_turns: list[str],
) -> dict:
    """Count matched turns whose prior-entity-count proxy differs across runs.

    Returns ``{"n_checked", "n_divergent"}``.  ``n_divergent`` is a LOWER BOUND
    on how many matched turns enter from a non-equivalent prior catalog state
    (the per-call comparison is only strictly valid where prior state matches).
    Equal proxies are inconclusive — same size can still mean different content.
    """
    all_runs = list(a_runs) + list(b_runs)
    priors = [_cumulative_prior_entities(run) for run in all_runs]
    n_divergent = 0
    for turn_id in matched_turns:
        proxies = {p.get(turn_id) for p in priors}
        if len(proxies) > 1:
            n_divergent += 1
    return {"n_checked": len(matched_turns), "n_divergent": n_divergent}


def _variant_turn_metric(
    runs: list[dict[str, dict]], turn_id: str, phase: str
) -> tuple[float, int, int]:
    """Mean tokens-per-call for *turn_id* across one variant's *runs*.

    Because the turn is matched-call, every run shares the same call count, so
    the mean per-call metric is ``mean(raw)/calls``.  Returns
    ``(mean_tokens_per_call, total_raw, total_calls)`` where the totals sum over
    the variant's runs (used for the weighted aggregate).
    """
    per_call: list[float] = []
    total_raw = 0
    total_calls = 0
    for run in runs:
        calls, raw = phase_call_metric(run[turn_id], phase)  # type: ignore[misc]
        per_call.append(raw / calls)
        total_raw += raw
        total_calls += calls
    return statistics.fmean(per_call), total_raw, total_calls


def paired_deltas(
    a_runs: list[dict[str, dict]],
    b_runs: list[dict[str, dict]],
    matched_turns: list[str],
    phase: str = DEFAULT_PHASE,
) -> list[dict]:
    """Per-turn paired deltas (B - A) of the tokens-per-call metric.

    Each entry carries the per-turn A/B means, their difference, and the raw/
    call totals used by the weighted aggregate.
    """
    rows: list[dict] = []
    for turn_id in matched_turns:
        a_mean, a_raw, a_calls = _variant_turn_metric(a_runs, turn_id, phase)
        b_mean, b_raw, b_calls = _variant_turn_metric(b_runs, turn_id, phase)
        rows.append(
            {
                "turn_id": turn_id,
                "a_tokens_per_call": a_mean,
                "b_tokens_per_call": b_mean,
                "delta": b_mean - a_mean,
                "a_raw_total": a_raw,
                "a_calls_total": a_calls,
                "b_raw_total": b_raw,
                "b_calls_total": b_calls,
            }
        )
    return rows


def summarize(deltas: list[dict], noise_floor: float = DEFAULT_NOISE_FLOOR) -> dict:
    """Summarize paired deltas into an effect-size-vs-noise-floor report.

    Reports the weighted Δ (total raw / total calls, B minus A), the per-turn
    mean / median / stdev Δ, Cohen's d, a 95% paired-t confidence interval on
    the per-turn deltas, and the effect size relative to *noise_floor*.

    ``separable`` is True only when ALL of the following hold (Rule 10 — a
    documented statistical rule, not a bare 1x-noise cutoff):

      * the 95% paired-t CI of the per-turn deltas EXCLUDES zero, AND
      * the absolute weighted Δ EXCEEDS the noise floor, AND
      * at least ``MIN_SEPARABLE_MATCHED_TURNS`` turns were matched.
    """
    n = len(deltas)
    if n == 0:
        return {
            "n_matched": 0,
            "noise_floor": noise_floor,
            "weighted_a": None,
            "weighted_b": None,
            "weighted_delta": None,
            "mean_delta": None,
            "median_delta": None,
            "stdev_delta": None,
            "cohens_d": None,
            "ci95_low": None,
            "ci95_high": None,
            "ci_excludes_zero": False,
            "exceeds_noise_floor": False,
            "min_matched_turns": MIN_SEPARABLE_MATCHED_TURNS,
            "effect_vs_noise": None,
            "separable": False,
        }
    per_turn = [d["delta"] for d in deltas]
    a_raw = sum(d["a_raw_total"] for d in deltas)
    a_calls = sum(d["a_calls_total"] for d in deltas)
    b_raw = sum(d["b_raw_total"] for d in deltas)
    b_calls = sum(d["b_calls_total"] for d in deltas)
    weighted_a = a_raw / a_calls if a_calls else 0.0
    weighted_b = b_raw / b_calls if b_calls else 0.0
    weighted_delta = weighted_b - weighted_a
    mean_delta = statistics.fmean(per_turn)
    median_delta = statistics.median(per_turn)
    stdev_delta = statistics.stdev(per_turn) if n > 1 else 0.0
    cohens_d = (mean_delta / stdev_delta) if stdev_delta else None
    effect_vs_noise = (
        abs(weighted_delta) / noise_floor if noise_floor else None
    )

    # 95% paired-t CI on the per-turn deltas.  Needs n >= 2 for a stdev; with
    # zero variance the CI collapses to the mean (excludes zero iff mean != 0).
    ci95_low = ci95_high = None
    ci_excludes_zero = False
    if n >= 2:
        margin = _t_critical_95(n - 1) * stdev_delta / math.sqrt(n)
        ci95_low = mean_delta - margin
        ci95_high = mean_delta + margin
        ci_excludes_zero = (ci95_low > 0) or (ci95_high < 0)

    exceeds_noise_floor = (
        noise_floor is not None and abs(weighted_delta) > noise_floor
    )
    separable = (
        n >= MIN_SEPARABLE_MATCHED_TURNS
        and ci_excludes_zero
        and exceeds_noise_floor
    )
    return {
        "n_matched": n,
        "noise_floor": noise_floor,
        "weighted_a": weighted_a,
        "weighted_b": weighted_b,
        "weighted_delta": weighted_delta,
        "mean_delta": mean_delta,
        "median_delta": median_delta,
        "stdev_delta": stdev_delta,
        "cohens_d": cohens_d,
        "ci95_low": ci95_low,
        "ci95_high": ci95_high,
        "ci_excludes_zero": ci_excludes_zero,
        "exceeds_noise_floor": exceeds_noise_floor,
        "min_matched_turns": MIN_SEPARABLE_MATCHED_TURNS,
        "effect_vs_noise": effect_vs_noise,
        "separable": separable,
    }


def format_report(
    summary: dict,
    phase: str,
    n_a: int,
    n_b: int,
    population_total: int,
    prior_state: dict | None = None,
) -> str:
    """Render the human-readable scoring report."""
    matched = summary["n_matched"]
    dropped = max(0, population_total - matched)
    pct = (matched / population_total * 100.0) if population_total else 0.0
    lines: list[str] = []
    lines.append(f"Paired multi-run A/B score — phase: {phase}")
    lines.append(
        f"  runs: A={n_a}  B={n_b}   "
        f"matched-call-COUNT turns: {matched}/{population_total} "
        f"({pct:.1f}%); {dropped} dropped (missing from a run, "
        f"zero phase-calls, or divergent call count)"
    )
    lines.append(
        "  NOTE: matched turns are a SURVIVOR subset (matching conditions on a "
        "post-treatment\n"
        "        call count); dropped turns are NOT 'not signal'."
    )
    if prior_state is not None and prior_state.get("n_checked"):
        div = prior_state["n_divergent"]
        chk = prior_state["n_checked"]
        lines.append(
            f"  prior-state proxy:  {div}/{chk} matched turns have a divergent "
            "prior-entity-count\n"
            "        proxy across runs (lower bound; equal proxy does NOT prove "
            "equal prior state)."
        )
    if matched == 0:
        lines.append(
            "  No matched-call-COUNT turns: every turn was missing from a run, "
            "had zero entity_detail phase-calls, or had a divergent "
            "entity_detail call count across runs. Nothing to score."
        )
        return "\n".join(lines)
    nf = summary["noise_floor"]
    lines.append(f"  noise floor (weighted): {nf:.3f} tok/call")
    lines.append("")
    lines.append(
        f"  weighted tok/call:  A={summary['weighted_a']:.3f}  "
        f"B={summary['weighted_b']:.3f}  Δ={summary['weighted_delta']:+.3f}"
    )
    lines.append(
        f"  per-turn Δ:         mean={summary['mean_delta']:+.3f}  "
        f"median={summary['median_delta']:+.3f}  "
        f"stdev={summary['stdev_delta']:.3f}"
    )
    if summary["ci95_low"] is not None:
        lines.append(
            "  95% paired-t CI:    "
            f"[{summary['ci95_low']:+.3f}, {summary['ci95_high']:+.3f}]  "
            + ("(excludes 0)" if summary["ci_excludes_zero"] else "(includes 0)")
        )
    d = summary["cohens_d"]
    lines.append(
        "  Cohen's d:          "
        + ("n/a (zero variance)" if d is None else f"{d:+.3f}")
    )
    ev = summary["effect_vs_noise"]
    lines.append(
        "  effect vs noise:    "
        + ("n/a" if ev is None else f"{ev:.2f}× the noise floor")
    )
    if summary["separable"]:
        verdict = (
            "SEPARABLE — paired-t CI excludes 0, weighted Δ exceeds the noise "
            "floor, and n is sufficient"
        )
    else:
        verdict = (
            "NOT SEPARABLE — fails the CI-excludes-0 / Δ>noise / "
            f"n≥{summary['min_matched_turns']} decision rule"
        )
    lines.append(f"  verdict:            {verdict}")
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--a", metavar="LOG", action="append", default=[], dest="a_logs",
        help="variant-A extraction log (file or run dir); repeatable (N=2-3)",
    )
    parser.add_argument(
        "--b", metavar="LOG", action="append", default=[], dest="b_logs",
        help="variant-B extraction log (file or run dir); repeatable (N=2-3)",
    )
    parser.add_argument(
        "--phase", default=DEFAULT_PHASE,
        help=f"prompt_metrics phase to score (default: {DEFAULT_PHASE})",
    )
    parser.add_argument(
        "--noise-floor", type=float, default=DEFAULT_NOISE_FLOOR,
        help=(
            "weighted tok/call noise floor to judge the effect against "
            f"(default: {DEFAULT_NOISE_FLOOR}; re-measure per model/backend)"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the summary as JSON",
    )
    return parser.parse_args(argv)


def _validate_run_counts(n_a: int, n_b: int) -> str | None:
    """Return an error message if the run counts violate the balanced design.

    Enforces N in {2, 3} per variant and equal N on both arms (#487 AC3).  N=1
    and unequal/insufficient arms are rejected — a single run cannot separate
    signal from the characterized run-to-run noise.
    """
    lo, hi = MIN_RUNS_PER_VARIANT, MAX_RUNS_PER_VARIANT
    if not (lo <= n_a <= hi) or not (lo <= n_b <= hi):
        return (
            f"need N in {{{lo},{hi}}} runs per variant "
            f"(got A={n_a}, B={n_b}). N=1 is not allowed: a single run cannot "
            "separate signal from run-to-run noise."
        )
    if n_a != n_b:
        return (
            f"unequal run counts (A={n_a}, B={n_b}); the paired design requires "
            "the same N on both arms."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.a_logs or not args.b_logs:
        print(
            "error: at least one --a and one --b log are required "
            "(N=2-3 per variant, equal on both arms).",
            file=sys.stderr,
        )
        return 2
    count_error = _validate_run_counts(len(args.a_logs), len(args.b_logs))
    if count_error:
        print(f"error: {count_error}", file=sys.stderr)
        return 2
    try:
        a_runs = [load_log(p) for p in args.a_logs]
        b_runs = [load_log(p) for p in args.b_logs]
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    matched = matched_call_turns(a_runs, b_runs, args.phase)
    deltas = paired_deltas(a_runs, b_runs, matched, args.phase)
    summary = summarize(deltas, noise_floor=args.noise_floor)

    # Survivorship denominator = the FULL turn population (union across runs),
    # so the drop rate shows how representative the matched survivor subset is.
    population_total = population_turns(a_runs, b_runs)
    prior_state = prior_state_divergence(a_runs, b_runs, matched)

    if args.json:
        print(json.dumps(
            {
                "phase": args.phase,
                "runs_a": len(a_runs),
                "runs_b": len(b_runs),
                "population_turns": population_total,
                "dropped_turns": max(0, population_total - summary["n_matched"]),
                "prior_state": prior_state,
                "summary": summary,
                "per_turn": deltas,
            },
            indent=2,
        ))
    else:
        print(format_report(
            summary, args.phase, len(a_runs), len(b_runs),
            population_total, prior_state,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
