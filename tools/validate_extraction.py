#!/usr/bin/env python3
"""Post-extraction validation against curated ground truth.

Compares extraction output (entity catalogs) against a ground truth fixture
to catch entity-level problems: false alias merges, missing characters,
coreference fragmentation, and entity staleness.

Usage:
    python tools/validate_extraction.py --catalog-dir framework-local/catalogs
    python tools/validate_extraction.py --catalog-dir framework-local/catalogs \
        --ground-truth tests/fixtures/extraction-ground-truth-full-session.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _turn_number(turn_str: str) -> int | None:
    """Extract numeric turn from strings like 'turn-245'."""
    m = re.search(r"(\d+)", str(turn_str))
    return int(m.group(1)) if m else None


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_character(catalog_dir: Path, char_id: str) -> dict[str, Any] | None:
    """Load a character JSON file by ID from the characters/ subdirectory."""
    char_path = catalog_dir / "characters" / f"{char_id}.json"
    if char_path.exists():
        return _load_json(char_path)
    return None


def _pc_aliases(catalog_dir: Path) -> list[str]:
    """Return the list of PC aliases from char-player.json."""
    pc = _load_character(catalog_dir, "char-player")
    if not pc:
        return []
    aliases_attr = pc.get("stable_attributes", {}).get("aliases", {})
    return [a.lower() for a in aliases_attr.get("value", [])]


def _all_character_ids(catalog_dir: Path) -> list[str]:
    """Return all character IDs found in the characters/ subdirectory."""
    chars_dir = catalog_dir / "characters"
    if not chars_dir.is_dir():
        return []
    ids = []
    for f in chars_dir.iterdir():
        if (
            f.suffix == ".json"
            and not f.name.endswith(".synthesis.json")
            and not f.name.endswith(".arcs.json")
            and f.name != "index.json"
        ):
            ids.append(f.stem)
    return sorted(ids)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

class Result:
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"

    def __init__(self, status: str, label: str, detail: str = ""):
        self.status = status
        self.label = label
        self.detail = detail

    def __str__(self) -> str:
        parts = [f"{self.status:4s}  {self.label:24s}"]
        if self.detail:
            parts.append(self.detail)
        return "  ".join(parts)


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def check_independent_characters(
    ground_truth: dict, catalog_dir: Path, pc_alias_list: list[str],
) -> list[Result]:
    """Check A: each expected independent character exists as its own entity."""
    results: list[Result] = []
    all_ids = _all_character_ids(catalog_dir)

    for entry in ground_truth.get("expected_independent_characters", []):
        name = entry["name"]
        patterns = entry.get("id_patterns", [])
        found_id = None

        for pat in patterns:
            if pat in all_ids:
                found_id = pat
                break

        if not found_id:
            # Check if merged as PC alias
            if name.lower() in pc_alias_list:
                results.append(Result(
                    Result.FAIL, name, "NOT FOUND — merged as PC alias",
                ))
            else:
                results.append(Result(Result.FAIL, name, "NOT FOUND"))
            continue

        # Check staleness
        char_data = _load_character(catalog_dir, found_id)
        last_turn = _turn_number(
            char_data.get("last_updated_turn", "") if char_data else "",
        )
        turn_display = f"turn-{last_turn}" if last_turn else "unknown"

        expected_min = entry.get("expected_last_updated_min")
        if expected_min and last_turn and last_turn < expected_min:
            gap = expected_min - last_turn
            severity = Result.FAIL if gap > 50 else Result.WARN
            results.append(Result(
                severity,
                name,
                f"{found_id:30s} ({turn_display}) — stale by {gap} turns "
                f"(expected ≥{expected_min})",
            ))
        else:
            results.append(Result(
                Result.PASS, name, f"{found_id:30s} ({turn_display})",
            ))

    return results


def check_pc_aliases(
    ground_truth: dict, pc_alias_list: list[str],
) -> list[Result]:
    """Check B: PC aliases match expected set."""
    results: list[Result] = []
    expected = {a.lower() for a in ground_truth.get("expected_pc_aliases", [])}

    # Check expected aliases are present
    for alias in expected:
        if alias in pc_alias_list:
            results.append(Result(Result.PASS, alias, "present"))
        else:
            results.append(Result(Result.FAIL, alias, "MISSING from PC aliases"))

    # Check for false positives
    for alias in pc_alias_list:
        if alias not in expected:
            results.append(Result(
                Result.FAIL, alias, "false positive (in aliases but shouldn't be)",
            ))

    return results


def check_must_not_merge(
    ground_truth: dict, catalog_dir: Path, pc_alias_list: list[str],
) -> list[Result]:
    """Check C: entities that must not be merged."""
    results: list[Result] = []

    for rule in ground_truth.get("must_not_merge", []):
        pair = rule["pair"]
        reason = rule.get("reason", "")
        entity_a = pair[0]
        entity_b = pair[1]

        merged = False
        # If entity_b is char-player, check if entity_a is in PC aliases
        if entity_b == "char-player":
            if entity_a.lower() in pc_alias_list:
                merged = True
        # Also check reverse
        elif entity_a == "char-player":
            if entity_b.lower() in pc_alias_list:
                merged = True
        else:
            # Check if one entity references the other as an alias
            # by loading both and seeing if one has the other's name
            for check_id_name, check_alias_name in [
                (entity_a, entity_b), (entity_b, entity_a),
            ]:
                # Try loading as character ID
                char = _load_character(catalog_dir, check_id_name)
                if char:
                    aliases = char.get("stable_attributes", {}).get(
                        "aliases", {},
                    ).get("value", [])
                    if check_alias_name.lower() in [
                        a.lower() for a in aliases
                    ]:
                        merged = True

        label = f"{entity_a} ↔ {entity_b}"
        if merged:
            results.append(Result(Result.FAIL, label, f"MERGED — {reason}"))
        else:
            results.append(Result(Result.PASS, label, "separate"))

    return results


def check_coreference_groups(
    ground_truth: dict, catalog_dir: Path,
) -> list[Result]:
    """Check D: coreference groups are properly merged."""
    results: list[Result] = []
    all_ids = _all_character_ids(catalog_dir)

    for group in ground_truth.get("coreference_groups", []):
        canonical = group["canonical_name"]
        expected_id = group["expected_id"]
        variants = group.get("variants_to_merge", [])

        # Check canonical exists
        if expected_id not in all_ids:
            results.append(Result(
                Result.FAIL, canonical, f"{expected_id} NOT FOUND",
            ))
            continue

        # Check for fragmented variants
        fragments = []
        for variant in variants:
            # Convert variant name to possible ID
            variant_id = "char-" + variant.lower().replace(" ", "-")
            if variant_id in all_ids:
                fragments.append(variant_id)

        if fragments:
            frag_list = ", ".join([expected_id] + fragments)
            results.append(Result(
                Result.WARN,
                canonical,
                f"{len(fragments) + 1} FRAGMENTS: {frag_list}",
            ))
        else:
            results.append(Result(Result.PASS, canonical, f"{expected_id} — unified"))

    return results


def check_staleness(
    ground_truth: dict, catalog_dir: Path,
) -> list[Result]:
    """Check E: entity staleness against targets."""
    results: list[Result] = []

    for target in ground_truth.get("entity_staleness_targets", []):
        entity_id = target["id"]
        expected_min = target["expected_last_updated_min"]
        reason = target.get("reason", "")

        char_data = _load_character(catalog_dir, entity_id)
        if not char_data:
            results.append(Result(
                Result.FAIL, entity_id, f"NOT FOUND — {reason}",
            ))
            continue

        last_turn = _turn_number(char_data.get("last_updated_turn", ""))
        if last_turn is None:
            results.append(Result(
                Result.FAIL, entity_id, f"no last_updated_turn — {reason}",
            ))
            continue

        gap = expected_min - last_turn
        if gap > 50:
            results.append(Result(
                Result.FAIL, entity_id,
                f"turn-{last_turn} (expected ≥{expected_min}, gap={gap})",
            ))
        elif gap > 20:
            results.append(Result(
                Result.WARN, entity_id,
                f"turn-{last_turn} (expected ≥{expected_min}, gap={gap})",
            ))
        else:
            results.append(Result(
                Result.PASS, entity_id, f"turn-{last_turn}",
            ))

    return results


def check_locations(
    ground_truth: dict, catalog_dir: Path,
) -> list[Result]:
    """Check F: expected late-game locations exist."""
    results: list[Result] = []
    loc_dir = catalog_dir / "locations"
    loc_files = []
    if loc_dir.is_dir():
        loc_files = [f.stem for f in loc_dir.iterdir() if f.suffix == ".json"]

    for entry in ground_truth.get("expected_locations_beyond_turn_100", []):
        name = entry["name"]
        notes = entry.get("notes", "")
        # Simple substring match against location file names
        found = any(
            _name_matches_loc(name, loc_id) for loc_id in loc_files
        )
        if found:
            results.append(Result(Result.PASS, name, notes))
        else:
            results.append(Result(Result.FAIL, name, f"NOT FOUND — {notes}"))

    return results


def _name_matches_loc(name: str, loc_id: str) -> bool:
    """Check if a location name roughly matches a location file ID."""
    name_words = set(name.lower().replace("/", " ").split())
    id_words = set(loc_id.replace("loc-", "").replace("-", " ").split())
    # Match if any significant word overlap
    return len(name_words & id_words) >= 1


def check_factions(
    ground_truth: dict, catalog_dir: Path,
) -> list[Result]:
    """Check G: expected late-game factions exist."""
    results: list[Result] = []
    fac_dir = catalog_dir / "factions"
    fac_files = []
    if fac_dir.is_dir():
        fac_files = [f.stem for f in fac_dir.iterdir() if f.suffix == ".json"]

    for entry in ground_truth.get("expected_factions_beyond_early_game", []):
        name = entry["name"]
        notes = entry.get("notes", "")
        found = any(
            _name_matches_faction(name, fac_id) for fac_id in fac_files
        )
        if found:
            results.append(Result(Result.PASS, name, notes))
        else:
            results.append(Result(Result.FAIL, name, f"NOT FOUND — {notes}"))

    return results


def _name_matches_faction(name: str, fac_id: str) -> bool:
    """Check if a faction name roughly matches a faction file ID."""
    name_words = set(name.lower().replace("/", " ").split())
    id_words = set(fac_id.replace("faction-", "").replace("-", " ").split())
    return len(name_words & id_words) >= 1


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(
    sections: list[tuple[str, int | None, list[Result]]],
    catalog_dir: Path,
    ground_truth_path: Path,
) -> int:
    """Print the validation scorecard. Returns 0 if no FAILs, 1 otherwise."""
    total_pass = 0
    total_warn = 0
    total_fail = 0

    print("=== Extraction Validation Report ===")
    print(f"Date: {date.today().isoformat()}")
    print(f"Catalog: {catalog_dir}")
    print(f"Ground Truth: {ground_truth_path}")
    print()

    for title, expected_count, results in sections:
        count_str = f" ({expected_count} expected)" if expected_count else ""
        print(f"## {title}{count_str}")

        for r in results:
            print(f"  {r}")
            if r.status == Result.PASS:
                total_pass += 1
            elif r.status == Result.WARN:
                total_warn += 1
            else:
                total_fail += 1

        print()

    print("## Summary")
    print(f"  PASS: {total_pass}  WARN: {total_warn}  FAIL: {total_fail}")

    return 0 if total_fail == 0 else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate(catalog_dir: Path, ground_truth_path: Path) -> int:
    """Run all validation checks and print report. Returns exit code."""
    gt = _load_json(ground_truth_path)
    pc_aliases = _pc_aliases(catalog_dir)

    expected_char_count = len(gt.get("expected_independent_characters", []))

    sections = [
        (
            "Independent Characters",
            expected_char_count,
            check_independent_characters(gt, catalog_dir, pc_aliases),
        ),
        (
            "PC Aliases",
            None,
            check_pc_aliases(gt, pc_aliases),
        ),
        (
            "Must-Not-Merge",
            None,
            check_must_not_merge(gt, catalog_dir, pc_aliases),
        ),
        (
            "Coreference Groups",
            None,
            check_coreference_groups(gt, catalog_dir),
        ),
        (
            "Staleness",
            None,
            check_staleness(gt, catalog_dir),
        ),
        (
            "Locations (late-game)",
            None,
            check_locations(gt, catalog_dir),
        ),
        (
            "Factions (late-game)",
            None,
            check_factions(gt, catalog_dir),
        ),
    ]

    return print_report(sections, catalog_dir, ground_truth_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate extraction catalogs against ground truth.",
    )
    parser.add_argument(
        "--catalog-dir",
        default="framework-local/catalogs",
        help="Path to the catalog directory (default: framework-local/catalogs)",
    )
    parser.add_argument(
        "--ground-truth",
        default="tests/fixtures/extraction-ground-truth-full-session.json",
        help="Path to the ground truth fixture",
    )
    args = parser.parse_args()

    catalog_dir = Path(args.catalog_dir)
    ground_truth_path = Path(args.ground_truth)

    if not catalog_dir.is_dir():
        print(f"Error: catalog directory not found: {catalog_dir}", file=sys.stderr)
        sys.exit(2)
    if not ground_truth_path.is_file():
        print(
            f"Error: ground truth file not found: {ground_truth_path}",
            file=sys.stderr,
        )
        sys.exit(2)

    exit_code = validate(catalog_dir, ground_truth_path)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
