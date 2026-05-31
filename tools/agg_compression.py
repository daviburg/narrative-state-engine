#!/usr/bin/env python3
"""
agg_compression.py — Aggregate per-turn compression instrumentation from an
extraction log and bucket it by turn-index band.

Successor to the ad-hoc ``_agg_pr463.py`` script.  Reads one or more
``extraction-log.jsonl`` files and reports, per turn-index band
(1-20, 21-50, 51-100, 101+), the raw-vs-compressed token totals and per-phase
breakdown emitted by the PR-1 instrumentation:

  * ``prompt_metrics.<phase>.raw_input_tokens`` / ``compressed_input_tokens``
    / ``compression_ratio``
  * ``turn_compression.{raw,compressed}_input_tokens_total`` and
    ``compression_ratio_total``

Turn-band bucketing is the report that gates PR-2: it is exactly the late-vs-
early split that hid the #393/#394/#463 regressions when only session totals
were checked.

Usage:
    python tools/agg_compression.py <extraction-log.jsonl> [<more.jsonl> ...]
    python tools/agg_compression.py --label A run_a/log.jsonl --label B run_b/log.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Turn-index bands.  An open-ended 101+ band catches longer sessions.
BANDS: list[tuple[str, int, int | None]] = [
    ("1-20", 1, 20),
    ("21-50", 21, 50),
    ("51-100", 51, 100),
    ("101+", 101, None),
]

PHASES = ("discovery", "entity_detail", "relationship_mapper", "event_extractor")


def parse_turn_number(turn_id: str | None) -> int | None:
    """Parse the numeric turn index from a turn ID like ``turn-042``."""
    if not turn_id or not isinstance(turn_id, str):
        return None
    m = re.search(r"turn-(\d+)", turn_id)
    return int(m.group(1)) if m else None


def _band_for_turn(turn_num: int | None) -> str | None:
    if turn_num is None:
        return None
    for label, lo, hi in BANDS:
        if turn_num >= lo and (hi is None or turn_num <= hi):
            return label
    return None


def _empty_band() -> dict:
    band = {
        "n": 0,
        "raw_total": 0,
        "comp_total": 0,
        "phases": {},
    }
    for phase in PHASES:
        band["phases"][phase] = {"raw": 0, "comp": 0, "calls": 0}
    return band


def aggregate_bands(records: list[dict]) -> dict[str, dict]:
    """Bucket extraction-log records by turn-index band.

    Returns a dict keyed by band label.  Each band carries token totals plus a
    per-phase ``raw`` / ``comp`` / ``calls`` breakdown and a computed
    ``ratio`` (compressed / raw).
    """
    agg: dict[str, dict] = {label: _empty_band() for label, _, _ in BANDS}
    for rec in records:
        band_label = _band_for_turn(parse_turn_number(rec.get("turn_id")))
        if band_label is None:
            continue
        band = agg[band_label]
        band["n"] += 1
        pm = rec.get("prompt_metrics", {}) or {}
        rec_raw = 0
        rec_comp = 0
        for phase in PHASES:
            ph = pm.get(phase, {}) or {}
            comp = ph.get(
                "compressed_input_tokens", ph.get("input_tokens", 0)
            )
            raw = ph.get("raw_input_tokens", comp)
            band["phases"][phase]["raw"] += raw
            band["phases"][phase]["comp"] += comp
            band["phases"][phase]["calls"] += ph.get("calls", 0)
            rec_raw += raw
            rec_comp += comp
        tc = rec.get("turn_compression") or {}
        if tc:
            band["raw_total"] += tc.get("raw_input_tokens_total", 0)
            band["comp_total"] += tc.get("compressed_input_tokens_total", 0)
        else:
            # Legacy record without a turn_compression block (e.g. an older
            # extraction-log.jsonl): fall back to THIS record's own per-phase
            # totals, not the cumulative per-band accumulator.
            band["raw_total"] += rec_raw
            band["comp_total"] += rec_comp

    for band in agg.values():
        band["ratio"] = (
            round(band["comp_total"] / band["raw_total"], 4)
            if band["raw_total"]
            else 1.0
        )
    return agg


def format_band_table(agg: dict[str, dict], label: str | None = None) -> str:
    """Render the per-band aggregation as a fixed-width text table."""
    lines: list[str] = []
    title = "Compression by turn band" + (f" — {label}" if label else "")
    lines.append(title)
    header = (
        f"{'band':<8} {'turns':>6} {'raw':>10} {'comp':>10} {'ratio':>7} "
        f"{'detail_raw':>11} {'detail_comp':>12} {'rel_comp':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for band_label, _, _ in BANDS:
        band = agg[band_label]
        det = band["phases"]["entity_detail"]
        rel = band["phases"]["relationship_mapper"]
        lines.append(
            f"{band_label:<8} {band['n']:>6} {band['raw_total']:>10} "
            f"{band['comp_total']:>10} {band['ratio']:>7.4f} "
            f"{det['raw']:>11} {det['comp']:>12} {rel['comp']:>9}"
        )
    return "\n".join(lines)


def load_records(path: str) -> list[dict]:
    """Load JSONL records from *path*, skipping blank lines."""
    records: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _parse_args(argv: list[str]) -> list[tuple[str | None, str]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", help="extraction-log.jsonl file(s)"
    )
    parser.add_argument(
        "--label", action="append", default=[],
        help="optional label for the next path (repeatable)",
    )
    args = parser.parse_args(argv)
    labeled: list[tuple[str | None, str]] = []
    for i, path in enumerate(args.paths):
        label = args.label[i] if i < len(args.label) else None
        labeled.append((label, path))
    return labeled


def main(argv: list[str] | None = None) -> int:
    targets = _parse_args(argv if argv is not None else sys.argv[1:])
    if not targets:
        print("usage: agg_compression.py <extraction-log.jsonl> [...]",
              file=sys.stderr)
        return 2
    for label, path in targets:
        records = load_records(path)
        agg = aggregate_bands(records)
        print(format_band_table(agg, label=label or path))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
