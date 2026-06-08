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

  * N = 2-3 runs PER VARIANT (4-6 logs total for a two-variant comparison).
  * **Matched-call scoring** — a turn is scored only when EVERY provided run
    (across both variants) made the same number of ``entity_detail`` calls for
    that turn.  Turns where the call sets differ are discarded; that divergence
    is the non-determinism we already characterized, not signal.
  * The scored metric is the #484-fixed **pre-compaction** entity_detail
    tokens-per-call (``raw_input_tokens`` / ``calls``).  Before #484 this value
    was pinned to the compressed prompt size (``compression_ratio == 1.0``),
    making the metric blind.
  * Reports a paired effect size (weighted Δ tok/call, per-turn mean / median Δ,
    Cohen's d) **against the measured noise floor** (~5 tok/call weighted from
    the A==A control-vs-rerun baseline).  A result is meaningful only when the
    effect size is separable from that floor.

Running the tool with the two control reruns of a single variant as ``--a`` and
``--b`` reproduces the noise floor itself (that is exactly how the ~5 tok/call
baseline was derived).

Usage:
    python tools/ab_paired_score.py \
        --a framework-ab-a-run1/extraction-log.jsonl \
        --a framework-ab-a-run2/extraction-log.jsonl \
        --b framework-ab-b-run1/extraction-log.jsonl \
        --b framework-ab-b-run2/extraction-log.jsonl \
        [--phase entity_detail] [--noise-floor 5.0] [--json]

Each ``--a`` / ``--b`` accepts either an ``extraction-log.jsonl`` file or a
framework/run directory containing one.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

DEFAULT_PHASE = "entity_detail"

# Measured weighted noise floor (tok/call) from the clean A==A control-vs-rerun
# baseline in #487.  This is the default tolerance band the effect size is
# judged against; re-measure it per model/backend (it is chosen, not inherited).
DEFAULT_NOISE_FLOOR = 5.0


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
    """Turns where EVERY run (both variants) made the same *phase* call count.

    A turn qualifies only when it is present with a non-zero phase call count in
    every provided run and all those call counts are identical.  Returns the
    qualifying turn IDs sorted by their numeric turn index.
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
    mean / median / stdev Δ, Cohen's d (mean / stdev), and the effect size
    relative to *noise_floor*.  ``separable`` is True when the absolute weighted
    Δ strictly exceeds the noise floor (the signal is distinguishable from
    backend run-to-run churn).
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
        "effect_vs_noise": effect_vs_noise,
        "separable": abs(weighted_delta) > noise_floor,
    }


def format_report(
    summary: dict,
    phase: str,
    n_a: int,
    n_b: int,
    total_candidate_turns: int,
) -> str:
    """Render the human-readable scoring report."""
    lines: list[str] = []
    lines.append(f"Paired multi-run A/B score — phase: {phase}")
    lines.append(
        f"  runs: A={n_a}  B={n_b}   "
        f"matched-call turns: {summary['n_matched']} / {total_candidate_turns} "
        "(turns scored / turns common to all runs)"
    )
    if summary["n_matched"] == 0:
        lines.append(
            "  No matched-call turns: every common turn had a divergent "
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
    verdict = (
        "SEPARABLE — effect exceeds the noise floor"
        if summary["separable"]
        else "WITHIN NOISE — effect not separable from run-to-run churn"
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.a_logs or not args.b_logs:
        print(
            "error: at least one --a and one --b log are required "
            "(recommend N=2-3 per variant).",
            file=sys.stderr,
        )
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

    # Candidate turns = turns common to every run (the matched-call denominator).
    all_runs = a_runs + b_runs
    common: set[str] | None = None
    for run in all_runs:
        ids = set(run.keys())
        common = ids if common is None else (common & ids)
    total_candidate = len(common or set())

    if args.json:
        print(json.dumps(
            {
                "phase": args.phase,
                "runs_a": len(a_runs),
                "runs_b": len(b_runs),
                "candidate_turns": total_candidate,
                "summary": summary,
                "per_turn": deltas,
            },
            indent=2,
        ))
    else:
        print(format_report(
            summary, args.phase, len(a_runs), len(b_runs), total_candidate,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
