#!/usr/bin/env python3
"""entity_retention_diff.py — Per-entity retention diff between two catalog dirs.

Aggregate entity counts (used by the A/B test gate) can mask deletion bugs:
a variant that drops 5 entities but adds 5 others shows a net delta of 0 while
silently losing distinct entities. This tool compares entity IDs between two
extraction outputs (variant A vs variant B) and reports, per entity type:

  - retained: IDs present in both A and B (A ∩ B)
  - removed:  IDs present in A but missing from B (A − B)
  - added:    IDs present in B but missing from A (B − A)

A run is *flagged* when the total number of removed IDs exceeds a configurable
threshold, surfacing deletion regressions such as those seen in #394 (27% loss)
and #441 (stale-sweep over-removal).

Usage:
    python tools/entity_retention_diff.py --variant-a DIR_A --variant-b DIR_B
    python tools/entity_retention_diff.py -a DIR_A -b DIR_B --threshold 3 --json
    python tools/entity_retention_diff.py -a DIR_A -b DIR_B --strict

DIR may be either a framework directory (containing a ``catalogs/`` subdir) or
a ``catalogs/`` directory itself; the layout is auto-detected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from catalog_merger import load_catalogs, load_events

# Friendly entity-type label for each canonical catalog key, plus events.
# Order matches the documented table order (docs/ab-test-standard.md §3.1/§3.4).
_CATALOG_KEY_TO_TYPE = {
    "characters.json": "characters",
    "locations.json": "locations",
    "items.json": "items",
    "factions.json": "factions",
}
_EVENTS_TYPE = "events"
ENTITY_TYPES = list(_CATALOG_KEY_TO_TYPE.values()) + [_EVENTS_TYPE]


def _resolve_catalog_dir(path: str) -> str:
    """Return the catalogs directory for a framework dir or catalogs dir.

    Accepts:
    - A framework directory that contains a ``catalogs/`` subdirectory.
    - A directory whose name is ``catalogs`` (i.e. already the catalogs dir).

    Raises:
        ValueError: If ``path`` is neither a framework dir nor a catalogs dir,
            to prevent silently producing an all-zero report for an invalid path.
    """
    nested = os.path.join(path, "catalogs")
    if os.path.isdir(nested):
        return nested
    if os.path.basename(os.path.normpath(path)) == "catalogs":
        return path
    raise ValueError(
        f"Cannot resolve catalog directory from '{path}': "
        "expected a framework directory containing a 'catalogs/' subdirectory, "
        "or a directory named 'catalogs'."
    )


def _collect_ids(catalog_dir: str) -> dict[str, set[str]]:
    """Return a mapping of entity-type label -> set of entity IDs.

    Reads per-entity catalog files (characters/locations/items/factions) and the
    events array. Entities without an ``id`` field are skipped.
    """
    ids: dict[str, set[str]] = {t: set() for t in ENTITY_TYPES}

    catalogs = load_catalogs(catalog_dir)
    for key, type_label in _CATALOG_KEY_TO_TYPE.items():
        for entity in catalogs.get(key, []):
            entity_id = entity.get("id")
            if entity_id:
                ids[type_label].add(entity_id)

    for event in load_events(catalog_dir):
        if isinstance(event, dict):
            event_id = event.get("id")
            if event_id:
                ids[_EVENTS_TYPE].add(event_id)

    return ids


def diff_id_sets(ids_a: set[str], ids_b: set[str]) -> dict[str, list[str]]:
    """Compare two ID sets, returning sorted retained/removed/added lists.

    - retained: present in both A and B
    - removed:  present in A but not B
    - added:    present in B but not A
    """
    return {
        "retained": sorted(ids_a & ids_b),
        "removed": sorted(ids_a - ids_b),
        "added": sorted(ids_b - ids_a),
    }


def compute_retention_diff(
    dir_a: str,
    dir_b: str,
    removal_threshold: int = 0,
) -> dict:
    """Compute the per-entity retention diff between two extraction outputs.

    Args:
        dir_a: Variant A directory (framework dir or catalogs dir).
        dir_b: Variant B directory (framework dir or catalogs dir).
        removal_threshold: Maximum number of total removed IDs tolerated before
            the report is flagged. Must be >= 0. Defaults to 0 (any removal
            flags the run).

    Returns:
        A report dict with ``by_type``, ``totals``, ``removal_threshold``,
        ``flagged``, and ``flagged_types`` keys.
    """
    if removal_threshold < 0:
        raise ValueError(
            f"removal_threshold must be >= 0, got {removal_threshold!r}"
        )

    ids_a = _collect_ids(_resolve_catalog_dir(dir_a))
    ids_b = _collect_ids(_resolve_catalog_dir(dir_b))

    by_type: dict[str, dict] = {}
    flagged_types: list[str] = []
    total_a = total_b = total_retained = total_removed = total_added = 0

    for entity_type in ENTITY_TYPES:
        a_set = ids_a[entity_type]
        b_set = ids_b[entity_type]
        diff = diff_id_sets(a_set, b_set)

        a_count = len(a_set)
        b_count = len(b_set)
        removed = len(diff["removed"])
        added = len(diff["added"])

        by_type[entity_type] = {
            "a_count": a_count,
            "b_count": b_count,
            "retained": diff["retained"],
            "removed": diff["removed"],
            "added": diff["added"],
            "net_change": b_count - a_count,
        }

        if removed > 0:
            flagged_types.append(entity_type)

        total_a += a_count
        total_b += b_count
        total_retained += len(diff["retained"])
        total_removed += removed
        total_added += added

    return {
        "by_type": by_type,
        "totals": {
            "a": total_a,
            "b": total_b,
            "retained": total_retained,
            "removed": total_removed,
            "added": total_added,
            "net_change": total_b - total_a,
        },
        "removal_threshold": removal_threshold,
        "flagged": total_removed > removal_threshold,
        "flagged_types": flagged_types,
    }


def format_markdown(report: dict) -> str:
    """Render a retention-diff report as a Markdown summary table."""
    lines = ["### Entity Retention Diff", ""]
    lines.append("| Type | A | B | Retained | Removed | Added | Net |")
    lines.append("|---|---|---|---|---|---|---|")
    for entity_type in ENTITY_TYPES:
        row = report["by_type"][entity_type]
        lines.append(
            f"| {entity_type} | {row['a_count']} | {row['b_count']} | "
            f"{len(row['retained'])} | {len(row['removed'])} | "
            f"{len(row['added'])} | {row['net_change']:+d} |"
        )
    totals = report["totals"]
    lines.append(
        f"| **Total** | {totals['a']} | {totals['b']} | {totals['retained']} | "
        f"{totals['removed']} | {totals['added']} | {totals['net_change']:+d} |"
    )
    lines.append("")

    if report["flagged"]:
        lines.append(
            f"**FLAGGED**: {totals['removed']} entity ID(s) removed "
            f"(threshold {report['removal_threshold']}). "
            f"Affected types: {', '.join(report['flagged_types'])}."
        )
        for entity_type in report["flagged_types"]:
            removed = report["by_type"][entity_type]["removed"]
            lines.append(f"- Removed {entity_type}: {', '.join(removed)}")
    else:
        lines.append(
            f"No retention regression "
            f"({totals['removed']} removed, threshold {report['removal_threshold']})."
        )
        for entity_type in ENTITY_TYPES:
            removed = report["by_type"][entity_type]["removed"]
            if removed:
                lines.append(f"- Removed {entity_type}: {', '.join(removed)}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-entity retention diff between two catalog directories."
    )
    parser.add_argument(
        "-a", "--variant-a", required=True,
        help="Variant A directory (framework dir or catalogs dir).",
    )
    parser.add_argument(
        "-b", "--variant-b", required=True,
        help="Variant B directory (framework dir or catalogs dir).",
    )
    parser.add_argument(
        "--threshold", type=int, default=0,
        help="Max total removed IDs tolerated before flagging (default: 0).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the full report as JSON instead of a Markdown table.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit with code 1 when the report is flagged.",
    )
    args = parser.parse_args(argv)

    for label, path in (("variant-a", args.variant_a), ("variant-b", args.variant_b)):
        if not os.path.isdir(path):
            print(f"ERROR: --{label} directory not found: {path}", file=sys.stderr)
            return 2

    try:
        report = compute_retention_diff(
            args.variant_a, args.variant_b, removal_threshold=args.threshold
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_markdown(report))

    if args.strict and report["flagged"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
