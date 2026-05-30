#!/usr/bin/env python3
"""entity_retention_diff.py — Per-entity retention diff between two catalog dirs.

Aggregate entity counts (used by the A/B test gate) can mask deletion bugs:
a variant that drops 5 entities but adds 5 others shows a net delta of 0 while
silently losing distinct entities. This tool compares entities between two
extraction outputs (variant A vs variant B) and reports, per entity type:

  - retained:  entities matched between A and B with the *same* ID
  - renamed:   entities matched by name/alias but with a *different* ID
               (an ID-scheme rename, e.g. ``char-elder`` -> ``char-elder-001``)
  - removed:   entities in A with no ID *or* name/alias match in B (TRUE removal)
  - added:     entities in B with no ID *or* name/alias match in A (TRUE addition)

Pure ID matching produces phantom churn when two branches use different ID
schemes for the same entity (e.g. main's bare slug ``char-elder`` vs the
compression branch's ``char-elder-001``): the entity looks both removed and
added even though it is the same character. The ``--match-by`` flag controls how
entities are paired:

  - ``id``:   exact ID only (legacy/fast path).
  - ``name``: normalized name + aliases (within the same catalog type).
  - ``auto``: exact ID first, then a name/alias fallback for entities left
              unmatched (default). Same-name pairs with differing IDs are
              reported as renames, not as churn.

A run is *flagged* when the total number of TRUE removed entities exceeds a
configurable threshold, surfacing deletion regressions such as those seen in
#394 (27% loss) and #441 (stale-sweep over-removal). ID renames never flag.

Usage:
    python tools/entity_retention_diff.py --variant-a DIR_A --variant-b DIR_B
    python tools/entity_retention_diff.py -a DIR_A -b DIR_B --threshold 3 --json
    python tools/entity_retention_diff.py -a DIR_A -b DIR_B --strict
    python tools/entity_retention_diff.py -a DIR_A -b DIR_B --match-by id

DIR may be either a framework directory (containing a ``catalogs/`` subdir) or
a ``catalogs/`` directory itself; the layout is auto-detected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
        ValueError: If ``path`` does not resolve to an existing catalogs
            directory, to prevent silently producing an all-zero report for an
            invalid or non-existent path.
    """
    nested = os.path.join(path, "catalogs")
    if os.path.isdir(nested):
        return nested
    if os.path.basename(os.path.normpath(path)) == "catalogs" and os.path.isdir(path):
        return path
    raise ValueError(
        f"Cannot resolve catalog directory from '{path}': "
        "expected an existing framework directory containing a 'catalogs/' subdirectory, "
        "or an existing directory named 'catalogs'."
    )


def _normalize_name(name: str) -> str:
    """Normalize a display name for cross-variant comparison.

    Lowercases, replaces any run of non-alphanumeric characters with a single
    space, and trims. This makes matching robust to punctuation and whitespace
    differences (e.g. "The Elder" vs "the  elder"). It intentionally does NOT
    use any domain-specific word lists (Rule 9) — only structural normalization.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def _entity_name_keys(entity: dict) -> set[str]:
    """Return the set of normalized name + alias keys for an entity.

    Pulls the ``name`` field and any aliases stored under
    ``stable_attributes.aliases`` (string, list, or ``{"value": ...}`` shapes).
    Entities without a name (e.g. events) yield an empty set, which means they
    can only ever be matched by ID.
    """
    keys: set[str] = set()
    name = entity.get("name")
    if isinstance(name, str) and name.strip():
        norm = _normalize_name(name)
        if norm:
            keys.add(norm)

    sa = entity.get("stable_attributes")
    aliases = sa.get("aliases") if isinstance(sa, dict) else None
    if aliases is not None:
        val = aliases.get("value") if isinstance(aliases, dict) else aliases
        candidates = val if isinstance(val, list) else [val]
        for cand in candidates:
            if isinstance(cand, str) and cand.strip():
                norm = _normalize_name(cand)
                if norm:
                    keys.add(norm)

    return keys


def _collect_entities(catalog_dir: str) -> dict[str, list[dict]]:
    """Return a mapping of entity-type label -> list of entity records.

    Each record has ``id`` and ``name_keys`` (set of normalized name/alias
    strings). Reads per-entity catalog files (characters/locations/items/
    factions) and the events array. Entities without an ``id`` are skipped.
    """
    by_type: dict[str, list[dict]] = {t: [] for t in ENTITY_TYPES}

    catalogs = load_catalogs(catalog_dir)
    for key, type_label in _CATALOG_KEY_TO_TYPE.items():
        for entity in catalogs.get(key, []):
            entity_id = entity.get("id")
            if entity_id:
                by_type[type_label].append(
                    {"id": entity_id, "name_keys": _entity_name_keys(entity)}
                )

    for event in load_events(catalog_dir):
        if isinstance(event, dict):
            event_id = event.get("id")
            if event_id:
                by_type[_EVENTS_TYPE].append(
                    {"id": event_id, "name_keys": _entity_name_keys(event)}
                )

    return by_type


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


def match_entities(
    entities_a: list[dict],
    entities_b: list[dict],
    match_by: str = "auto",
) -> dict:
    """Pair entities of one type between variant A and B.

    Args:
        entities_a: Records (``{"id", "name_keys"}``) from variant A.
        entities_b: Records from variant B.
        match_by: ``"id"`` (exact ID only), ``"name"`` (name/alias only), or
            ``"auto"`` (exact ID first, then a name/alias fallback).

    Returns:
        A dict with sorted ``retained`` and ``removed``/``added`` ID lists plus
        a ``renamed`` list of ``{"old_id", "new_id"}`` pairs (entities matched
        by name/alias but with a different ID).
    """
    a_by_id = {e["id"]: e for e in entities_a}
    b_by_id = {e["id"]: e for e in entities_b}

    if match_by == "id":
        a_ids = set(a_by_id)
        b_ids = set(b_by_id)
        return {
            "retained": sorted(a_ids & b_ids),
            "removed": sorted(a_ids - b_ids),
            "added": sorted(b_ids - a_ids),
            "renamed": [],
        }

    matched_a: set[str] = set()
    matched_b: set[str] = set()
    retained: list[str] = []
    renamed: list[dict] = []

    # Phase 1 (auto only): exact-ID match — the fast path.
    if match_by == "auto":
        for entity_id in a_by_id:
            if entity_id in b_by_id:
                retained.append(entity_id)
                matched_a.add(entity_id)
                matched_b.add(entity_id)

    # Phase 2: name/alias fallback for still-unmatched entities. Matching is
    # confined to a single catalog type (the caller iterates per type), so a
    # name collision across types (e.g. a character and an item) never matches.
    for a_id in sorted(a_by_id):
        if a_id in matched_a:
            continue
        a_keys = a_by_id[a_id]["name_keys"]
        if not a_keys:
            continue
        for b_id in sorted(b_by_id):
            if b_id in matched_b:
                continue
            if a_keys & b_by_id[b_id]["name_keys"]:
                matched_a.add(a_id)
                matched_b.add(b_id)
                if b_id == a_id:
                    retained.append(a_id)
                else:
                    renamed.append({"old_id": a_id, "new_id": b_id})
                break

    removed = sorted(set(a_by_id) - matched_a)
    added = sorted(set(b_by_id) - matched_b)
    return {
        "retained": sorted(retained),
        "removed": removed,
        "added": added,
        "renamed": renamed,
    }



def compute_retention_diff(
    dir_a: str,
    dir_b: str,
    removal_threshold: int = 0,
    match_by: str = "auto",
) -> dict:
    """Compute the per-entity retention diff between two extraction outputs.

    Args:
        dir_a: Variant A directory (framework dir or catalogs dir).
        dir_b: Variant B directory (framework dir or catalogs dir).
        removal_threshold: Maximum number of total TRUE removed entities
            tolerated before the report is flagged. Must be >= 0. Defaults to 0
            (any TRUE removal flags the run). ID renames never count toward this.
        match_by: Entity pairing strategy — ``"id"`` (exact ID only, legacy),
            ``"name"`` (name/alias only), or ``"auto"`` (exact ID then a
            name/alias fallback). Defaults to ``"auto"``.

    Returns:
        A report dict with ``by_type``, ``totals``, ``match_by``,
        ``removal_threshold``, ``flagged``, and ``flagged_types`` keys.
    """
    if removal_threshold < 0:
        raise ValueError(
            f"removal_threshold must be >= 0, got {removal_threshold!r}"
        )
    if match_by not in ("id", "name", "auto"):
        raise ValueError(
            f"match_by must be one of 'id', 'name', 'auto', got {match_by!r}"
        )

    entities_a = _collect_entities(_resolve_catalog_dir(dir_a))
    entities_b = _collect_entities(_resolve_catalog_dir(dir_b))

    by_type: dict[str, dict] = {}
    total_a = total_b = total_retained = total_removed = total_added = 0
    total_renamed = 0

    for entity_type in ENTITY_TYPES:
        a_list = entities_a[entity_type]
        b_list = entities_b[entity_type]
        result = match_entities(a_list, b_list, match_by=match_by)

        a_count = len(a_list)
        b_count = len(b_list)
        removed = len(result["removed"])
        added = len(result["added"])
        renamed = len(result["renamed"])

        by_type[entity_type] = {
            "a_count": a_count,
            "b_count": b_count,
            "retained": result["retained"],
            "renamed": result["renamed"],
            "removed": result["removed"],
            "added": result["added"],
            "net_change": b_count - a_count,
        }

        total_a += a_count
        total_b += b_count
        total_retained += len(result["retained"])
        total_removed += removed
        total_added += added
        total_renamed += renamed

    flagged = total_removed > removal_threshold
    flagged_types = (
        [et for et in ENTITY_TYPES if len(by_type[et]["removed"]) > 0]
        if flagged
        else []
    )

    return {
        "by_type": by_type,
        "totals": {
            "a": total_a,
            "b": total_b,
            "retained": total_retained,
            "renamed": total_renamed,
            "removed": total_removed,
            "added": total_added,
            "net_change": total_b - total_a,
        },
        "match_by": match_by,
        "removal_threshold": removal_threshold,
        "flagged": flagged,
        "flagged_types": flagged_types,
    }



def format_markdown(report: dict) -> str:
    """Render a retention-diff report as a Markdown summary table."""
    lines = ["### Entity Retention Diff", ""]
    lines.append(f"Match strategy: `{report.get('match_by', 'auto')}`")
    lines.append("")
    lines.append("| Type | A | B | Retained | Renamed | Removed | Added | Net |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for entity_type in ENTITY_TYPES:
        row = report["by_type"][entity_type]
        lines.append(
            f"| {entity_type} | {row['a_count']} | {row['b_count']} | "
            f"{len(row['retained'])} | {len(row['renamed'])} | "
            f"{len(row['removed'])} | {len(row['added'])} | "
            f"{row['net_change']:+d} |"
        )
    totals = report["totals"]
    lines.append(
        f"| **Total** | {totals['a']} | {totals['b']} | {totals['retained']} | "
        f"{totals['renamed']} | {totals['removed']} | {totals['added']} | "
        f"{totals['net_change']:+d} |"
    )
    lines.append("")

    # ID renames are NOT churn; surface them separately so they are not
    # mistaken for removals/additions.
    if totals["renamed"]:
        lines.append(
            f"ID renames (matched by name/alias, different ID): {totals['renamed']}."
        )
        for entity_type in ENTITY_TYPES:
            for pair in report["by_type"][entity_type]["renamed"]:
                lines.append(
                    f"- Renamed {entity_type}: {pair['old_id']} -> {pair['new_id']}"
                )

    if report["flagged"]:
        lines.append(
            f"**FLAGGED**: {totals['removed']} entity(ies) removed "
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
        "--match-by", choices=("id", "name", "auto"), default="auto",
        help=(
            "Entity pairing strategy: 'id' (exact ID only, legacy), "
            "'name' (normalized name/alias), or 'auto' (exact ID then a "
            "name/alias fallback; default). 'auto' reports ID-scheme renames "
            "separately instead of as removed+added churn."
        ),
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
            args.variant_a, args.variant_b,
            removal_threshold=args.threshold,
            match_by=args.match_by,
        )
    except (ValueError, json.JSONDecodeError, OSError) as exc:
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
