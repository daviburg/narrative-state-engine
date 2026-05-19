"""Post-extraction dedup audit tool.

Usage:
    python tools/dedup_audit.py [--catalog-dir DIR] [--auto-merge] [--review-file PATH]

Phases:
    1. Candidate generation (programmatic - edit distance, substring, same-turn)
    2. LLM scoring (confidence + rationale for each pair)
    3. Action: auto-merge (>=0.9), flag for review (0.6-0.9), discard (<0.6)
"""

import argparse
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

from catalog_merger import load_catalogs, save_catalogs, load_events, save_events, CATALOG_KEYS
from llm_client import LLMClient

AUTO_MERGE_THRESHOLD = 0.9
REVIEW_THRESHOLD = 0.6

SCORING_SYSTEM_PROMPT = """\
You are a deduplication assistant for an RPG entity catalog.

Given two entities from the same campaign, determine if they refer to the same in-world thing.

Respond with JSON:
{"same_entity": true/false, "confidence": 0.0-1.0, "canonical_id": "<id to keep — must be one of the two entity IDs provided>", "rationale": "<one sentence>"}

Rules:
- Consider name similarity, description overlap, relationships, and source turns
- If names differ only by typo/article/hyphenation, confidence should be high
- If they share the same first_seen_turn and similar role, confidence should be high
- If both have independent relationship graphs (different targets), they are likely distinct"""


def _normalize_name(name: str) -> str:
    """Normalize entity name for comparison: lowercase, strip articles."""
    n = name.lower().strip()
    n = re.sub(r"^(the|a|an)\s+", "", n)
    return n


def _edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr_row = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr_row.append(min(
                curr_row[j] + 1,
                prev_row[j + 1] + 1,
                prev_row[j] + cost,
            ))
        prev_row = curr_row
    return prev_row[-1]


def _relationship_targets(entity: dict) -> set:
    """Return the set of distinct relationship target IDs for an entity."""
    rels = entity.get("relationships", [])
    if not rels:
        return set()
    return {r["target_id"] for r in rels if "target_id" in r}


def _type_prefix(entity_id: str) -> str:
    """Extract the type prefix from an entity ID (e.g., 'char-' from 'char-elara')."""
    match = re.match(r"^(char|loc|faction|item|creature|concept)-", entity_id)
    return match.group(0) if match else ""


def generate_candidates(catalogs: dict) -> list[tuple[str, str, str]]:
    """Return list of (id_a, id_b, reason) candidate pairs.

    Groups entities by type prefix and compares within groups using:
    - Levenshtein distance <= 3 on normalized names
    - One name is substring of other (after article stripping)
    - Same first_seen_turn + similar name (ratio >= 0.6)

    Excludes pairs where both entities have >= 3 relationships to distinct targets.
    """
    candidates = []
    seen_pairs = set()

    # Collect all entities grouped by type prefix
    type_groups: dict[str, list[dict]] = {}
    for key in CATALOG_KEYS:
        entities = catalogs.get(key, [])
        for entity in entities:
            eid = entity.get("id", "")
            prefix = _type_prefix(eid)
            if prefix:
                type_groups.setdefault(prefix, []).append(entity)

    for prefix, entities in type_groups.items():
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                a = entities[i]
                b = entities[j]
                id_a = a["id"]
                id_b = b["id"]

                pair_key = tuple(sorted([id_a, id_b]))
                if pair_key in seen_pairs:
                    continue

                # Skip if both have rich independent relationship graphs
                targets_a = _relationship_targets(a)
                targets_b = _relationship_targets(b)
                if len(targets_a) >= 3 and len(targets_b) >= 3:
                    # Check if targets are largely distinct
                    overlap = targets_a & targets_b
                    if len(overlap) < min(len(targets_a), len(targets_b)) * 0.5:
                        continue

                name_a = _normalize_name(a.get("name", ""))
                name_b = _normalize_name(b.get("name", ""))

                if not name_a or not name_b:
                    continue

                reason = None

                # Check Levenshtein distance
                dist = _edit_distance(name_a, name_b)
                if dist <= 3 and dist > 0:
                    reason = f"edit_distance={dist}"

                # Check substring relationship
                if reason is None:
                    if len(name_a) >= 3 and len(name_b) >= 3:
                        if name_a in name_b or name_b in name_a:
                            reason = "substring"

                # Check same first_seen_turn + similar name
                if reason is None:
                    turn_a = a.get("first_seen_turn", "")
                    turn_b = b.get("first_seen_turn", "")
                    if turn_a and turn_b and turn_a == turn_b:
                        ratio = difflib.SequenceMatcher(
                            None, name_a, name_b
                        ).ratio()
                        if ratio >= 0.6:
                            reason = f"same_turn+similar(ratio={ratio:.2f})"

                if reason:
                    seen_pairs.add(pair_key)
                    candidates.append((id_a, id_b, reason))

    return candidates


def score_pair(client: "LLMClient", entity_a: dict, entity_b: dict) -> dict:
    """Ask LLM if two entities are the same.

    Returns {same_entity, confidence, canonical_id, rationale}.
    """
    # Build concise representations for the prompt
    json_a = json.dumps(entity_a, indent=2, default=str)
    json_b = json.dumps(entity_b, indent=2, default=str)

    user_prompt = f"Entity A:\n{json_a}\n\nEntity B:\n{json_b}"

    result = client.extract_json(
        system_prompt=SCORING_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    # Validate and normalize response
    if not isinstance(result, dict):
        return {
            "same_entity": False,
            "confidence": 0.0,
            "canonical_id": entity_a.get("id", ""),
            "rationale": "LLM returned invalid response",
        }

    # Defensive confidence parsing
    try:
        confidence = float(result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # Validate canonical_id is one of the pair IDs
    id_a = entity_a.get("id", "")
    id_b = entity_b.get("id", "")
    canonical_id = result.get("canonical_id", id_a)
    if canonical_id not in (id_a, id_b):
        canonical_id = id_a

    return {
        "same_entity": bool(result.get("same_entity", False)),
        "confidence": confidence,
        "canonical_id": canonical_id,
        "rationale": result.get("rationale", ""),
    }


def process_results(
    scored_pairs: list[dict],
    catalog_dir: str,
    auto_merge: bool,
    review_file: str,
    dry_run: bool = False,
) -> dict:
    """Apply auto-merges, write review file, return summary stats.

    Returns dict with keys: auto_merged, flagged_for_review, discarded.
    """
    auto_merge_list = []
    review_list = []
    discarded = 0

    for pair in scored_pairs:
        confidence = pair.get("confidence", 0.0)
        same = pair.get("same_entity", False)

        if not same or confidence < REVIEW_THRESHOLD:
            discarded += 1
        elif confidence >= AUTO_MERGE_THRESHOLD:
            auto_merge_list.append(pair)
        else:
            review_list.append({
                "entity_a": pair["entity_a_id"],
                "entity_b": pair["entity_b_id"],
                "confidence": confidence,
                "rationale": pair.get("rationale", ""),
                "canonical_id": pair.get("canonical_id", ""),
                "action": None,
            })

    # Write review file
    if review_list and not dry_run:
        with open(review_file, "w", encoding="utf-8") as f:
            json.dump(review_list, f, indent=2)
        print(f"  Wrote {len(review_list)} pairs to {review_file}")

    # Auto-merge high-confidence pairs
    if auto_merge_list and auto_merge and not dry_run:
        hints = _build_coreference_hints(auto_merge_list)
        hints_path = os.path.join(catalog_dir, "coreference-hints.json")

        # Load existing hints if present and merge
        existing_groups = []
        if os.path.isfile(hints_path):
            with open(hints_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_groups = existing.get("character_groups", [])

        existing_groups.extend(hints["character_groups"])
        merged_hints = {"character_groups": existing_groups}

        with open(hints_path, "w", encoding="utf-8") as f:
            json.dump(merged_hints, f, indent=2)
        print(f"  Wrote {len(auto_merge_list)} merge hints to {hints_path}")

        # Apply the hints
        from semantic_extraction import apply_coreference_hints

        catalogs = load_catalogs(catalog_dir)
        events_list = load_events(catalog_dir)

        merged_ids = apply_coreference_hints(
            catalogs, events_list, catalog_dir, hints_path
        )

        # Persist mutated catalogs and events back to disk
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        print(f"  Applied merges, removed IDs: {merged_ids}")

    return {
        "auto_merged": len(auto_merge_list),
        "flagged_for_review": len(review_list),
        "discarded": discarded,
    }


def _build_coreference_hints(merge_pairs: list[dict]) -> dict:
    """Convert scored merge pairs into coreference-hints format."""
    groups = []
    for pair in merge_pairs:
        canonical_id = pair.get("canonical_id", pair["entity_a_id"])
        variant_id = (
            pair["entity_b_id"]
            if canonical_id == pair["entity_a_id"]
            else pair["entity_a_id"]
        )
        groups.append({
            "canonical_name": pair.get("canonical_name", canonical_id),
            "canonical_id": canonical_id,
            "variant_names": [pair.get("variant_name", variant_id)],
            "variant_id_patterns": [variant_id],
            "notes": f"Auto-merged by dedup_audit (confidence={pair['confidence']:.2f}): {pair.get('rationale', '')}",
        })
    return {"character_groups": groups}


def apply_review_file(review_file: str, catalog_dir: str, dry_run: bool = False) -> dict:
    """Read a human-reviewed dedup-review.json and apply approved merges.

    Entries with ``"action": "merge"`` are converted to coreference hints
    and applied.  Returns summary stats.
    """
    with open(review_file, "r", encoding="utf-8") as f:
        entries = json.load(f)

    merge_pairs = []
    kept_separate = 0
    pending = 0

    for entry in entries:
        action = entry.get("action")
        if action == "merge":
            merge_pairs.append({
                "entity_a_id": entry["entity_a"],
                "entity_b_id": entry["entity_b"],
                "confidence": entry.get("confidence", 1.0),
                "rationale": entry.get("rationale", "human-approved"),
                "canonical_id": entry.get("canonical_id", entry["entity_a"]),
                "canonical_name": entry.get("canonical_id", entry["entity_a"]),
                "variant_name": entry["entity_b"] if entry.get("canonical_id", entry["entity_a"]) == entry["entity_a"] else entry["entity_a"],
            })
        elif action == "keep_separate":
            kept_separate += 1
        else:
            pending += 1

    merged = 0
    if merge_pairs and not dry_run:
        hints = _build_coreference_hints(merge_pairs)
        hints_path = os.path.join(catalog_dir, "coreference-hints.json")

        existing_groups = []
        if os.path.isfile(hints_path):
            with open(hints_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_groups = existing.get("character_groups", [])

        existing_groups.extend(hints["character_groups"])
        merged_hints = {"character_groups": existing_groups}

        with open(hints_path, "w", encoding="utf-8") as f:
            json.dump(merged_hints, f, indent=2)

        from semantic_extraction import apply_coreference_hints

        catalogs = load_catalogs(catalog_dir)
        events_list = load_events(catalog_dir)
        apply_coreference_hints(catalogs, events_list, catalog_dir, hints_path)
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        merged = len(merge_pairs)
        print(f"  Applied {merged} human-approved merges")

    return {"merged": merged, "kept_separate": kept_separate, "pending": pending}


def _entity_lookup(catalogs: dict) -> dict[str, dict]:
    """Build a lookup dict of entity_id -> entity from catalogs."""
    lookup = {}
    for key in CATALOG_KEYS:
        for entity in catalogs.get(key, []):
            lookup[entity["id"]] = entity
    return lookup


def main():
    parser = argparse.ArgumentParser(description="Post-extraction dedup audit")
    parser.add_argument("--catalog-dir", default="framework/catalogs")
    parser.add_argument(
        "--auto-merge",
        action="store_true",
        help="Apply high-confidence merges automatically",
    )
    parser.add_argument("--review-file", default="dedup-review.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score pairs but don't write any files",
    )
    parser.add_argument("--config", default="config/llm.json", help="LLM config file")
    parser.add_argument(
        "--apply-review",
        action="store_true",
        help="Apply merges from a human-reviewed dedup-review.json",
    )
    args = parser.parse_args()

    print("=== Dedup Audit ===")
    print(f"Catalog dir: {args.catalog_dir}")

    # --apply-review mode: consume a human-reviewed file
    if args.apply_review:
        if not os.path.isfile(args.review_file):
            print(f"  Review file not found: {args.review_file}")
            return
        print(f"\nApplying reviewed merges from {args.review_file}...")
        summary = apply_review_file(
            args.review_file, args.catalog_dir, dry_run=args.dry_run,
        )
        print("\n=== Summary ===")
        print(f"  Merged:         {summary['merged']}")
        print(f"  Kept separate:  {summary['kept_separate']}")
        print(f"  Pending:        {summary['pending']}")
        return

    # Phase 1: Load catalogs and generate candidates
    print("\n[Phase 1] Generating candidates...")
    catalogs = load_catalogs(args.catalog_dir)
    candidates = generate_candidates(catalogs)
    print(f"  Found {len(candidates)} candidate pairs")

    if not candidates:
        print("  No candidates found. Exiting.")
        return

    # Phase 2: Score with LLM
    print("\n[Phase 2] Scoring with LLM...")
    client = LLMClient(config_path=args.config)
    lookup = _entity_lookup(catalogs)

    scored_pairs = []
    for id_a, id_b, reason in candidates:
        entity_a = lookup.get(id_a)
        entity_b = lookup.get(id_b)
        if not entity_a or not entity_b:
            continue

        print(f"  Scoring: {id_a} vs {id_b} ({reason})")
        score = score_pair(client, entity_a, entity_b)
        scored_pairs.append({
            "entity_a_id": id_a,
            "entity_b_id": id_b,
            "entity_a_name": entity_a.get("name", ""),
            "entity_b_name": entity_b.get("name", ""),
            "canonical_name": entity_a.get("name", "") if score["canonical_id"] == id_a else entity_b.get("name", ""),
            "variant_name": entity_b.get("name", "") if score["canonical_id"] == id_a else entity_a.get("name", ""),
            "reason": reason,
            **score,
        })

    # Phase 3: Process results
    print("\n[Phase 3] Processing results...")
    summary = process_results(
        scored_pairs,
        catalog_dir=args.catalog_dir,
        auto_merge=args.auto_merge,
        review_file=args.review_file,
        dry_run=args.dry_run,
    )

    print("\n=== Summary ===")
    print(f"  Auto-merged:       {summary['auto_merged']}")
    print(f"  Flagged for review: {summary['flagged_for_review']}")
    print(f"  Discarded:         {summary['discarded']}")


if __name__ == "__main__":
    main()
