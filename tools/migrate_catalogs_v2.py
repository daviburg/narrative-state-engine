#!/usr/bin/env python3
"""One-time migration script: V1 flat catalogs → V2 per-entity file layout.

Usage:
    python tools/migrate_catalogs_v2.py --framework framework/
    python tools/migrate_catalogs_v2.py --framework framework-local/
    python tools/migrate_catalogs_v2.py --framework framework/ --force
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATALOG_TYPES = {
    "characters": "character",
    "locations": "location",
    "factions": "faction",
    "items": "item",
}

# V2 relationship type enum from schemas/entity.schema.json
VALID_RELATIONSHIP_TYPES = {
    "kinship", "partnership", "mentorship", "political",
    "factional", "social", "adversarial", "romantic", "other",
}

VOLATILE_KEYS = {
    "condition", "status", "equipment", "hp_change", "location",
    "last_action",
}

INFERENCE_TAG_RE = re.compile(r"\s*\[inference\]\s*$", re.IGNORECASE)

TURN_NUM_RE = re.compile(r"turn-(\d+)")

DEFAULT_DORMANCY_THRESHOLD = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_turn_number(turn_id: str) -> int | None:
    """Extract numeric part from a turn ID like 'turn-078'."""
    m = TURN_NUM_RE.search(turn_id or "")
    return int(m.group(1)) if m else None


def read_json(path: Path) -> list | dict:
    """Read JSON with BOM-safe encoding."""
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    """Write JSON with 2-space indent, no BOM, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def strip_inference_tag(value: str) -> tuple[str, bool]:
    """Strip '[inference]' tag from a string value. Returns (clean_value, is_inference)."""
    if isinstance(value, str) and INFERENCE_TAG_RE.search(value):
        return INFERENCE_TAG_RE.sub("", value).rstrip(), True
    return value, False


def wrap_stable_attribute(value, first_seen_turn: str | None) -> dict:
    """Wrap a raw attribute value into the V2 stable_attributes format."""
    if isinstance(value, str):
        clean, inferred = strip_inference_tag(value)
        attr = {"value": clean}
        if inferred:
            attr["inference"] = True
            attr["confidence"] = 0.7
        else:
            attr["inference"] = False
        if first_seen_turn:
            attr["source_turn"] = first_seen_turn
        return attr

    if isinstance(value, list):
        cleaned_items = []
        any_inferred = False
        for item in value:
            if isinstance(item, str):
                clean, inferred = strip_inference_tag(item)
                cleaned_items.append(clean)
                any_inferred = any_inferred or inferred
            else:
                cleaned_items.append(item)
        attr = {"value": cleaned_items}
        attr["inference"] = any_inferred
        if any_inferred:
            attr["confidence"] = 0.7
        if first_seen_turn:
            attr["source_turn"] = first_seen_turn
        return attr

    # Fallback: wrap as-is
    attr = {"value": value, "inference": False}
    if first_seen_turn:
        attr["source_turn"] = first_seen_turn
    return attr


def normalize_aliases(raw_val: str | list) -> list[str]:
    """Normalize an aliases value to a list of strings."""
    if isinstance(raw_val, list):
        return raw_val
    if isinstance(raw_val, str):
        # Could be comma-separated or a single value
        parts = [p.strip() for p in raw_val.split(",")]
        return [p for p in parts if p]
    return [str(raw_val)]


def map_relationship_type(raw_type: str) -> str:
    """Map a V1 relationship type to the V2 enum."""
    if raw_type in VALID_RELATIONSHIP_TYPES:
        return raw_type
    return "other"


def find_max_turn(framework_dir: Path) -> int:
    """Find the highest turn number across all entities in catalogs."""
    max_turn = 0
    catalogs_dir = framework_dir / "catalogs"
    for filename in CATALOG_TYPES:
        flat_file = catalogs_dir / f"{filename}.json"
        if not flat_file.exists():
            continue
        entities = read_json(flat_file)
        if not isinstance(entities, list):
            continue
        for entity in entities:
            for field in ("last_updated_turn", "first_seen_turn"):
                t = parse_turn_number(entity.get(field, ""))
                if t is not None and t > max_turn:
                    max_turn = t
            for rel in entity.get("relationships", []):
                for field in ("last_updated_turn", "first_seen_turn"):
                    t = parse_turn_number(rel.get(field, ""))
                    if t is not None and t > max_turn:
                        max_turn = t
    return max_turn


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------


def classify_attributes(
    attributes: dict, first_seen_turn: str | None
) -> tuple[dict, dict]:
    """Split V1 freeform attributes into stable_attributes and volatile_state."""
    stable = {}
    volatile = {}

    for key, value in attributes.items():
        if key in VOLATILE_KEYS:
            # Volatile state: store value directly
            if key == "equipment" and isinstance(value, str):
                # Parse comma-separated equipment into list
                volatile[key] = [
                    item.strip() for item in value.split(",") if item.strip()
                ]
            else:
                volatile[key] = value
        else:
            # Stable (known stable OR unknown keys default to stable)
            if key == "aliases":
                raw = normalize_aliases(value)
                # Strip inference tags from each alias
                cleaned = []
                any_inferred = False
                for alias in raw:
                    clean, inf = strip_inference_tag(alias)
                    cleaned.append(clean)
                    any_inferred = any_inferred or inf
                attr = {"value": cleaned, "inference": any_inferred}
                if any_inferred:
                    attr["confidence"] = 0.7
                if first_seen_turn:
                    attr["source_turn"] = first_seen_turn
                stable[key] = attr
            else:
                stable[key] = wrap_stable_attribute(value, first_seen_turn)

    return stable, volatile


def consolidate_relationships(
    relationships: list[dict],
    entity_first_seen: str | None,
    max_turn: int,
    entity_last_updated_turns: dict[str, str] | None = None,
) -> list[dict]:
    """Group V1 relationships by (source, target) pair into consolidated V2 relationships."""
    if not relationships:
        return []

    # Group by target_id
    groups: dict[str, list[dict]] = {}
    for rel in relationships:
        target = rel.get("target_id", "unknown")
        groups.setdefault(target, []).append(rel)

    consolidated = []
    for target_id, rels in groups.items():
        # Sort by turn number (use last_updated_turn, fall back to first_seen_turn)
        def sort_key(r):
            t = parse_turn_number(r.get("last_updated_turn", "")) or 0
            if t == 0:
                t = parse_turn_number(r.get("first_seen_turn", "")) or 0
            return t

        sorted_rels = sorted(rels, key=sort_key)
        most_recent = sorted_rels[-1]

        # Build history from all but the most recent
        history = []
        for r in sorted_rels[:-1]:
            turn = r.get("last_updated_turn") or r.get("first_seen_turn")
            if not turn:
                continue
            entry = {
                "description": r.get("relationship", ""),
                "turn": turn,
            }
            history.append(entry)

        # Determine first_seen and last_updated across all rels in the group
        all_first = [
            parse_turn_number(r.get("first_seen_turn", "")) for r in sorted_rels
        ]
        all_last = [
            parse_turn_number(r.get("last_updated_turn", "")) for r in sorted_rels
        ]
        all_first = [t for t in all_first if t is not None]
        all_last = [t for t in all_last if t is not None]

        first_turn_num = min(all_first) if all_first else None
        last_turn_num = max(all_last) if all_last else (
            max(all_first) if all_first else None
        )

        first_turn_id = (
            entity_first_seen
            if first_turn_num is None
            else f"turn-{first_turn_num:03d}"
        )
        last_turn_id = (
            f"turn-{last_turn_num:03d}" if last_turn_num is not None else None
        )

        # Determine dormancy status — dormant if either the relationship
        # or the target entity hasn't been updated within the threshold.
        status = "active"
        if max_turn > 0:
            rel_stale = (
                last_turn_num is not None
                and max_turn - last_turn_num > DEFAULT_DORMANCY_THRESHOLD
            )
            target_stale = False
            if entity_last_updated_turns:
                target_turn = parse_turn_number(
                    entity_last_updated_turns.get(target_id, "")
                )
                if target_turn is not None:
                    target_stale = (
                        max_turn - target_turn > DEFAULT_DORMANCY_THRESHOLD
                    )
            if rel_stale or target_stale:
                status = "dormant"

        v2_rel = {
            "target_id": target_id,
            "current_relationship": most_recent.get("relationship", ""),
            "type": map_relationship_type(most_recent.get("type", "other")),
            "status": status,
            "first_seen_turn": first_turn_id,
        }

        # Optional fields
        if most_recent.get("direction"):
            v2_rel["direction"] = most_recent["direction"]
        if most_recent.get("confidence") is not None:
            v2_rel["confidence"] = most_recent["confidence"]
        if last_turn_id:
            v2_rel["last_updated_turn"] = last_turn_id
        if history:
            v2_rel["history"] = history

        consolidated.append(v2_rel)

    return consolidated


def convert_entity(
    entity: dict,
    max_turn: int,
    entity_last_updated_turns: dict[str, str] | None = None,
) -> dict:
    """Convert a V1 entity dict to V2 per-entity format.

    If the entity already carries V2 fields (identity, current_status,
    stable_attributes, volatile_state), those fields are preserved so that
    LLM-populated data is not overwritten with migration placeholders.
    """
    first_seen = entity.get("first_seen_turn")
    last_updated = entity.get("last_updated_turn")

    # Detect existing V2 fields (use key presence, not truthiness,
    # so explicitly-present empty dicts/strings are still preserved)
    has_v2_identity = "identity" in entity
    has_v2_status = (
        "current_status" in entity
        and "migrated from V1" not in entity.get("current_status", "")
    )
    has_v2_stable = "stable_attributes" in entity
    has_v2_volatile = "volatile_state" in entity

    # Identity / status split — only convert from description when V2 fields absent
    if has_v2_identity:
        identity = entity["identity"]
    else:
        description = entity.get("description", "")
        identity = description if description else entity.get("name", "Unknown entity")

    if has_v2_status:
        current_status = entity["current_status"]
    else:
        current_status = "Status unknown \u2014 migrated from V1 catalog."

    # Attributes — only classify from V1 attributes when V2 fields absent
    if has_v2_stable or has_v2_volatile:
        stable_attrs = entity.get("stable_attributes", {})
        volatile_state = entity.get("volatile_state", {})
    else:
        raw_attrs = entity.get("attributes", {})
        stable_attrs, volatile_state = classify_attributes(raw_attrs, first_seen)

    # Add last_updated_turn to volatile_state if we have volatile keys
    if volatile_state and last_updated and not has_v2_volatile:
        volatile_state["last_updated_turn"] = last_updated

    # Relationships
    raw_rels = entity.get("relationships", [])
    relationships = consolidate_relationships(
        raw_rels, first_seen, max_turn, entity_last_updated_turns
    )

    # Build V2 entity
    v2 = {
        "id": entity["id"],
        "name": entity["name"],
        "type": entity["type"],
        "identity": identity,
        "current_status": current_status,
        "first_seen_turn": first_seen,
    }

    if last_updated:
        v2["status_updated_turn"] = entity.get("status_updated_turn") or last_updated
        v2["last_updated_turn"] = last_updated

    if stable_attrs:
        v2["stable_attributes"] = stable_attrs
    if volatile_state:
        v2["volatile_state"] = volatile_state
    if relationships:
        v2["relationships"] = relationships

    if entity.get("notes"):
        v2["notes"] = entity["notes"]

    return v2


def build_index_entry(v2_entity: dict) -> dict:
    """Build a lightweight index entry from a V2 entity."""
    current_status = v2_entity.get("current_status", "")
    identity = v2_entity.get("identity", "")
    status_source = current_status if current_status else identity
    status_summary = status_source[:80] if status_source else ""

    active_count = sum(
        1
        for r in v2_entity.get("relationships", [])
        if r.get("status") == "active"
    )

    entry = {
        "id": v2_entity["id"],
        "name": v2_entity["name"],
        "type": v2_entity["type"],
        "first_seen_turn": v2_entity["first_seen_turn"],
    }

    if status_summary:
        entry["status_summary"] = status_summary
    if v2_entity.get("last_updated_turn"):
        entry["last_updated_turn"] = v2_entity["last_updated_turn"]

    entry["active_relationship_count"] = active_count

    return entry


# ---------------------------------------------------------------------------
# Migration driver
# ---------------------------------------------------------------------------


def migrate_catalog(
    framework_dir: Path, catalog_name: str, max_turn: int, force: bool
) -> tuple[int, list[str]]:
    """Migrate a single flat catalog file to per-entity directory layout.

    Returns (entity_count, list_of_warnings).
    """
    catalogs_dir = framework_dir / "catalogs"
    flat_file = catalogs_dir / f"{catalog_name}.json"

    if not flat_file.exists():
        return 0, [f"Skipped {catalog_name}: {flat_file} does not exist"]

    entities = read_json(flat_file)
    if not isinstance(entities, list):
        return 0, [f"Skipped {catalog_name}: expected array, got {type(entities).__name__}"]

    if not entities:
        return 0, [f"Skipped {catalog_name}: empty array"]

    entity_dir = catalogs_dir / catalog_name

    # Idempotency guard
    if entity_dir.exists() and any(entity_dir.iterdir()):
        if not force:
            return 0, [
                f"ABORT: {entity_dir} already exists and is not empty. "
                f"Use --force to overwrite."
            ]
        # Force mode: remove existing files in the directory
        for child in entity_dir.iterdir():
            if child.is_file():
                child.unlink()

    entity_dir.mkdir(parents=True, exist_ok=True)

    warnings = []
    index_entries = []

    # Build entity_id → last_updated_turn map for two-sided dormancy checks
    entity_last_updated_turns = {}
    for e in entities:
        if isinstance(e, dict) and "id" in e:
            lut = e.get("last_updated_turn") or e.get("first_seen_turn", "")
            if lut:
                entity_last_updated_turns[e["id"]] = lut

    for entity in entities:
        if not isinstance(entity, dict):
            warnings.append(f"Skipped malformed entity in {catalog_name}")
            continue
        missing_keys = [k for k in ("id", "name", "type") if k not in entity]
        if missing_keys:
            warnings.append(
                f"Skipped malformed entity in {catalog_name}: "
                f"missing {', '.join(missing_keys)}"
            )
            continue

        v2 = convert_entity(entity, max_turn, entity_last_updated_turns)
        entity_file = entity_dir / f"{v2['id']}.json"
        write_json(entity_file, v2)

        index_entries.append(build_index_entry(v2))

    # Write index
    if index_entries:
        write_json(entity_dir / "index.json", index_entries)

    # Backup original flat file
    backup_name = f"{catalog_name}.v1.json"
    backup_path = catalogs_dir / backup_name
    flat_file.replace(backup_path)

    return len(index_entries), warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate V1 flat catalog files to V2 per-entity layout."
    )
    parser.add_argument(
        "--framework",
        required=True,
        help="Path to framework directory (e.g. framework/ or framework-local/)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing per-entity directories",
    )
    args = parser.parse_args()

    framework_dir = Path(args.framework)
    if not framework_dir.is_dir():
        print(f"Error: {framework_dir} is not a directory", file=sys.stderr)
        return 1

    catalogs_dir = framework_dir / "catalogs"
    if not catalogs_dir.is_dir():
        print(f"Error: {catalogs_dir} does not exist", file=sys.stderr)
        return 1

    # Find max turn across all catalogs for dormancy calculation
    max_turn = find_max_turn(framework_dir)
    print(f"Max turn detected: turn-{max_turn:03d}")

    # Preflight: check all target directories before migrating any
    if not args.force:
        blockers = []
        for catalog_name in CATALOG_TYPES:
            entity_dir = catalogs_dir / catalog_name
            if entity_dir.exists() and any(entity_dir.iterdir()):
                blockers.append(str(entity_dir))
        if blockers:
            print("ABORT: the following directories already exist and are not empty:")
            for b in blockers:
                print(f"  - {b}")
            print("Use --force to overwrite.")
            return 1

    total_entities = 0
    all_warnings = []

    for catalog_name in CATALOG_TYPES:
        count, warnings = migrate_catalog(
            framework_dir, catalog_name, max_turn, args.force
        )
        total_entities += count
        all_warnings.extend(warnings)

        if count > 0:
            print(f"  {catalog_name}: migrated {count} entities")

    if all_warnings:
        print("\nWarnings:")
        for w in all_warnings:
            print(f"  - {w}")

    print(f"\nMigration complete: {total_entities} entities migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
