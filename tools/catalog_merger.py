#!/usr/bin/env python3
"""
catalog_merger.py — Merge agent-extracted entities, relationships, and events
into existing catalog files.

Supports both V2 per-entity file layout (preferred) and V1 flat file fallback.

V2 layout (per-entity files):
  framework/catalogs/characters/
    index.json            → lightweight roster (regenerated)
    char-player.json      → full entity detail
    char-elder.json       → full entity detail

V1 layout (flat files — deprecated):
  framework/catalogs/characters.json

Handles:
- Format detection (V2 directory vs V1 flat file)
- New entity insertion with ID prefix validation
- Existing entity updates (identity, status, attributes, last_updated_turn)
- Name changes and alias tracking
- Relationship consolidation per (source_id, target_id) pair
- Relationship dormancy marking for inactive entities
- Index.json generation after merge
- Event deduplication by ID
"""

from __future__ import annotations

import json
import os
import re
import warnings

# V1 flat file mapping (for backward compatibility)
TYPE_TO_CATALOG_V1 = {
    "character": "characters.json",
    "location": "locations.json",
    "faction": "factions.json",
    "item": "items.json",
    "creature": "characters.json",
    "concept": "items.json",
}

# Flat file names used by the V1 format
_V1_FILENAMES = ["characters.json", "locations.json", "factions.json", "items.json"]

# Directory names used by the V2 format
_V2_DIRNAMES = ["characters", "locations", "factions", "items"]

TYPE_TO_PREFIX = {
    "character": "char-",
    "location": "loc-",
    "faction": "faction-",
    "item": "item-",
    "creature": "creature-",
    "concept": "concept-",
}

DEFAULT_DORMANCY_THRESHOLD = 10


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(catalog_dir: str) -> str:
    """Detect whether catalog_dir uses V2 (per-entity dirs) or V1 (flat files).

    Returns ``"v2"`` only when the layout is unambiguously V2: either all
    expected entity-type subdirectories exist, or at least one V2 directory
    exists and no legacy V1 flat files remain.  Mixed layouts fall back to
    ``"v1"`` so legacy flat files are still loaded during migration.
    """
    existing_v2_dirs = [
        d for d in _V2_DIRNAMES
        if os.path.isdir(os.path.join(catalog_dir, d))
    ]
    existing_v1_files = [
        f for f in _V1_FILENAMES
        if os.path.isfile(os.path.join(catalog_dir, f))
    ]
    if len(existing_v2_dirs) == len(_V2_DIRNAMES):
        return "v2"
    if existing_v2_dirs and not existing_v1_files:
        return "v2"
    return "v1"


# ---------------------------------------------------------------------------
# V2 per-entity I/O
# ---------------------------------------------------------------------------

def _read_v2_entities(entity_dir: str) -> list[dict]:
    """Read all per-entity JSON files from a V2 directory.

    Skips ``index.json`` which is a derived artifact.
    """
    entities = []
    if not os.path.isdir(entity_dir):
        return entities
    for fname in sorted(os.listdir(entity_dir)):
        if fname == "index.json" or not fname.endswith(".json"):
            continue
        fpath = os.path.join(entity_dir, fname)
        with open(fpath, "r", encoding="utf-8-sig") as f:
            entities.append(json.load(f))
    return entities


def _write_v2_entity(entity_dir: str, entity: dict) -> None:
    """Write a single entity to its per-entity JSON file."""
    os.makedirs(entity_dir, exist_ok=True)
    entity_id = entity["id"]
    fpath = os.path.join(entity_dir, f"{entity_id}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(entity, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _generate_index(entity_dir: str, entities: list[dict]) -> None:
    """Generate index.json from per-entity data."""
    index = []
    for entity in entities:
        active_rels = sum(
            1 for r in entity.get("relationships", [])
            if r.get("status") == "active"
        )
        index.append({
            "id": entity["id"],
            "name": entity["name"],
            "type": entity["type"],
            "status_summary": (entity.get("current_status") or entity.get("identity", ""))[:80],
            "first_seen_turn": entity.get("first_seen_turn"),
            "last_updated_turn": entity.get("last_updated_turn"),
            "active_relationship_count": active_rels,
        })
    fpath = os.path.join(entity_dir, "index.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Unified load / save (V2 preferred, V1 fallback)
# ---------------------------------------------------------------------------

def load_catalogs(catalog_dir: str) -> dict:
    """Load all entity catalogs into a dict keyed by catalog name.

    V2 mode: reads per-entity files from type directories.
    V1 mode: reads flat JSON files (emits deprecation warning).

    The returned dict is keyed by the canonical V1 filenames
    (``characters.json``, etc.) for backward compatibility with callers.
    """
    fmt = detect_format(catalog_dir)
    catalogs: dict[str, list[dict]] = {}

    if fmt == "v2":
        for dirname, filename in zip(_V2_DIRNAMES, _V1_FILENAMES):
            entity_dir = os.path.join(catalog_dir, dirname)
            catalogs[filename] = _read_v2_entities(entity_dir)
    else:
        warnings.warn(
            "V1 flat catalog format detected; run migrate_catalogs_v2.py to upgrade",
            DeprecationWarning,
            stacklevel=2,
        )
        for filename in _V1_FILENAMES:
            filepath = os.path.join(catalog_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8-sig") as f:
                    catalogs[filename] = json.load(f)
            else:
                catalogs[filename] = []
    return catalogs


def _has_real_v1_data(catalog_dir: str) -> bool:
    """Return True if any V1 flat file contains meaningful data (not just ``[]``)."""
    for fname in _V1_FILENAMES:
        fpath = os.path.join(catalog_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            size = os.path.getsize(fpath)
        except OSError:
            continue
        if size <= 3:
            # File is empty or just "[]" / "[]\n"
            continue
        # Read and check for non-empty array
        try:
            with open(fpath, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


def save_catalogs(catalog_dir: str, catalogs: dict, dry_run: bool = False, prefer_v2: bool = True) -> None:
    """Write all catalogs back to disk.

    V2 mode: writes per-entity files and regenerates index.json.
    V1 mode: writes flat JSON files.

    When *prefer_v2* is True (default) and ``detect_format()`` returns
    ``"v1"`` but no flat file contains real data (all empty or just ``[]``),
    the output format is upgraded to V2 automatically.  This prevents a clean
    extraction start from defaulting to the deprecated V1 layout.
    """
    fmt = detect_format(catalog_dir)

    # On a clean start (neither V1 nor V2 exist), default to V2 if preferred
    if fmt == "v1" and prefer_v2 and not _has_real_v1_data(catalog_dir):
        fmt = "v2"

    if fmt == "v2":
        for dirname, filename in zip(_V2_DIRNAMES, _V1_FILENAMES):
            entities = catalogs.get(filename, [])
            entity_dir = os.path.join(catalog_dir, dirname)
            if dry_run:
                print(f"  [DRY RUN] Would write {len(entities)} entities to {entity_dir}/")
                continue
            os.makedirs(entity_dir, exist_ok=True)
            # Remove stale per-entity files for entities no longer in memory
            # (e.g. after dedup merges)
            live_ids = {e["id"] for e in entities}
            for fname in os.listdir(entity_dir):
                if fname == "index.json" or not fname.endswith(".json"):
                    continue
                stem = fname[:-5]  # strip .json
                if stem not in live_ids:
                    os.remove(os.path.join(entity_dir, fname))
            for entity in entities:
                _write_v2_entity(entity_dir, entity)
            _generate_index(entity_dir, entities)
    else:
        for filename, data in catalogs.items():
            filepath = os.path.join(catalog_dir, filename)
            if dry_run:
                print(f"  [DRY RUN] Would write {len(data)} entries to {filepath}")
                continue
            os.makedirs(catalog_dir, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")


def load_events(catalog_dir: str) -> list:
    """Load the events catalog."""
    filepath = os.path.join(catalog_dir, "events.json")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return []


def save_events(catalog_dir: str, events: list, dry_run: bool = False) -> None:
    """Write the events catalog back to disk."""
    filepath = os.path.join(catalog_dir, "events.json")
    if dry_run:
        print(f"  [DRY RUN] Would write {len(events)} events to {filepath}")
        return
    os.makedirs(catalog_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Entity lookup and formatting
# ---------------------------------------------------------------------------

def find_entity_by_id(catalogs: dict, entity_id: str) -> tuple[str, dict] | None:
    """Find an entity across all catalogs. Returns (catalog_filename, entity) or None."""
    for filename, entities in catalogs.items():
        for entity in entities:
            if entity.get("id") == entity_id:
                return filename, entity
    return None


def format_known_entities(catalogs: dict) -> str:
    """Format all known entities as a compact table for the discovery prompt.

    Supports both V2 (identity + stable_attributes.aliases) and
    V1 (description + attributes.aliases) entity shapes.
    """
    lines = []
    for filename, entities in catalogs.items():
        for entity in entities:
            # V2: identity field; V1: description field
            desc = entity.get("identity") or entity.get("description", "")
            # V2: stable_attributes.aliases.value; V1: attributes.aliases (string)
            aliases = ""
            sa = entity.get("stable_attributes", {}).get("aliases")
            if sa:
                val = sa.get("value", "") if isinstance(sa, dict) else sa
                if isinstance(val, list):
                    aliases = ", ".join(val)
                else:
                    aliases = str(val)
            if not aliases:
                aliases = entity.get("attributes", {}).get("aliases", "")
            extra = ""
            if desc:
                extra += f" — {desc}"
            if aliases:
                extra += f" (aliases: {aliases})"
            lines.append(f"{entity['id']} | {entity['name']} | {entity['type']}{extra}")
    if not lines:
        return "(none — empty catalog)"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ID prefix validation and correction
# ---------------------------------------------------------------------------

def validate_id_prefix(entity_id: str, entity_type: str) -> bool:
    """Check that an entity ID starts with the correct prefix for its type."""
    expected_prefix = TYPE_TO_PREFIX.get(entity_type)
    if not expected_prefix:
        return False
    return entity_id.startswith(expected_prefix)


def fix_id_prefix(entity_id: str, entity_type: str) -> str:
    """Return a corrected entity ID with the proper prefix for its type.

    If the ID already starts with the wrong type prefix, strip it and apply
    the correct one.  Also handles common model abbreviations like ``fac-``
    for ``faction-``.  Returns the original ID if the type is unknown.
    """
    expected_prefix = TYPE_TO_PREFIX.get(entity_type)
    if not expected_prefix:
        return entity_id
    if entity_id.startswith(expected_prefix):
        return entity_id  # already correct
    # Strip any existing known prefix (canonical or abbreviated)
    for prefix in TYPE_TO_PREFIX.values():
        if entity_id.startswith(prefix):
            return expected_prefix + entity_id[len(prefix):]
    # Strip common model-generated abbreviation prefixes (e.g. "fac-", "char-")
    m = re.match(r'^[a-z]+-', entity_id)
    if m:
        return expected_prefix + entity_id[m.end():]
    # No prefix found at all — just prepend the correct one
    return expected_prefix + entity_id


# ---------------------------------------------------------------------------
# Turn number helpers
# ---------------------------------------------------------------------------

def _parse_turn_number(turn_id: str | None) -> int | None:
    """Extract the numeric part from a turn ID like ``turn-078``.

    Returns ``None`` if the turn_id is missing or not parseable.
    """
    if not turn_id:
        return None
    m = re.match(r"^turn-(\d+)$", turn_id)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Entity merge
# ---------------------------------------------------------------------------

def merge_entity(catalogs: dict, entity: dict) -> None:
    """Merge a single entity into the appropriate catalog.

    - New entities: validate and append.
    - Existing entities: deep-merge attributes/identity/status, update last_updated_turn.
    """
    entity_id = entity.get("id")
    entity_type = entity.get("type")

    if not entity_id or not entity_type:
        return

    if not validate_id_prefix(entity_id, entity_type):
        print(f"  WARNING: Entity '{entity_id}' has invalid prefix for type '{entity_type}'. Skipping.")
        return

    # Map type to the V1 filename key used in the catalogs dict
    catalog_file = TYPE_TO_CATALOG_V1.get(entity_type)
    if not catalog_file or catalog_file not in catalogs:
        return

    # Check if entity already exists
    existing = None
    for i, e in enumerate(catalogs[catalog_file]):
        if e.get("id") == entity_id:
            existing = (i, e)
            break

    if existing is not None:
        idx, current = existing
        _update_existing_entity(current, entity)
        catalogs[catalog_file][idx] = current
    else:
        # Ensure required fields for new entity (V2: identity; V1: description)
        required_base = ["id", "name", "type", "first_seen_turn"]
        has_desc = entity.get("identity") or entity.get("description")
        if all(entity.get(f) for f in required_base) and has_desc:
            catalogs[catalog_file].append(entity)
        else:
            missing = [f for f in required_base if not entity.get(f)]
            if not has_desc:
                missing.append("identity/description")
            print(f"  WARNING: New entity '{entity_id}' missing required fields: {missing}. Skipping.")


def _update_existing_entity(current: dict, update: dict) -> None:
    """Update an existing entity with new information.

    Supports both V2 (identity/current_status/stable_attributes/volatile_state)
    and V1 (description/attributes) entity shapes.
    """
    # --- V2 fields ---
    if update.get("identity") and update["identity"] != current.get("identity"):
        current["identity"] = update["identity"]

    if update.get("current_status"):
        current["current_status"] = update["current_status"]
        if update.get("status_updated_turn"):
            current["status_updated_turn"] = update["status_updated_turn"]

    # Deep-merge stable_attributes
    if update.get("stable_attributes"):
        if "stable_attributes" not in current:
            current["stable_attributes"] = {}
        for key, value in update["stable_attributes"].items():
            current["stable_attributes"][key] = value

    # Merge volatile_state
    if update.get("volatile_state"):
        if "volatile_state" not in current:
            current["volatile_state"] = {}
        for key, value in update["volatile_state"].items():
            current["volatile_state"][key] = value

    # --- V1 fields (backward compat) ---
    if update.get("description") and update["description"] != current.get("description"):
        current["description"] = update["description"]

    if update.get("attributes"):
        if "attributes" not in current:
            current["attributes"] = {}
        for key, value in update["attributes"].items():
            current["attributes"][key] = value

    # Handle name changes / aliases (V2: stable_attributes.aliases; V1: attributes.aliases)
    if update.get("name") and update["name"] != current.get("name"):
        old_name = current["name"]
        # V2 path
        if "stable_attributes" in current:
            sa = current["stable_attributes"]
            existing_aliases = sa.get("aliases", {})
            if isinstance(existing_aliases, dict):
                val = existing_aliases.get("value", [])
                if isinstance(val, str):
                    val = [a.strip() for a in val.split(",") if a.strip()]
                if old_name not in val:
                    val.append(old_name)
                alias_turn = (update.get("last_updated_turn")
                              or current.get("last_updated_turn")
                              or current.get("first_seen_turn", ""))
                sa["aliases"] = {"value": val, "inference": False,
                                 "source_turn": alias_turn}
            else:
                alias_turn = (update.get("last_updated_turn")
                              or current.get("last_updated_turn")
                              or current.get("first_seen_turn", ""))
                sa["aliases"] = {"value": [old_name], "inference": False,
                                 "source_turn": alias_turn}
        else:
            # V1 path
            if "attributes" not in current:
                current["attributes"] = {}
            existing_aliases = current["attributes"].get("aliases", "")
            alias_list = [a.strip() for a in existing_aliases.split(",")] if existing_aliases else []
            if old_name not in alias_list:
                if existing_aliases:
                    current["attributes"]["aliases"] = f"{existing_aliases}, {old_name}"
                else:
                    current["attributes"]["aliases"] = old_name
        current["name"] = update["name"]

    # Update last_updated_turn
    if update.get("last_updated_turn"):
        current["last_updated_turn"] = update["last_updated_turn"]

    # Update notes
    if update.get("notes"):
        current["notes"] = update["notes"]

    # Merge relationships — consolidated per (target_id) pair
    if update.get("relationships"):
        if "relationships" not in current:
            current["relationships"] = []
        _merge_entity_relationships(current["relationships"], update["relationships"])


# Public alias for cross-module use (e.g. post-batch dedup in semantic_extraction)
dedup_merge_entity = _update_existing_entity


# ---------------------------------------------------------------------------
# Relationship consolidation
# ---------------------------------------------------------------------------

def _merge_entity_relationships(existing_rels: list[dict], new_rels: list[dict]) -> None:
    """Merge new relationships into existing, consolidating per (target_id) pair.

    If a pair already exists: update current_relationship, push old to history.
    If a pair is new: append with status=active.
    """
    # Build index of existing relationships by target_id
    by_target: dict[str, int] = {}
    for idx, rel in enumerate(existing_rels):
        tid = rel.get("target_id")
        if tid and tid not in by_target:
            by_target[tid] = idx

    for new_rel in new_rels:
        target_id = new_rel.get("target_id")
        if not target_id:
            continue

        if target_id in by_target:
            # Update existing pair
            existing = existing_rels[by_target[target_id]]
            _consolidate_relationship(existing, new_rel)
        else:
            # New pair — ensure required V2 fields
            entry = dict(new_rel)
            entry.setdefault("status", "active")
            if "current_relationship" not in entry and "relationship" in entry:
                entry["current_relationship"] = entry.pop("relationship")
            existing_rels.append(entry)
            by_target[target_id] = len(existing_rels) - 1


def _consolidate_relationship(existing: dict, update: dict) -> None:
    """Consolidate an update into an existing relationship for the same pair.

    Pushes the old current_relationship into history and updates to the new one.
    """
    # Determine the new description
    new_desc = update.get("current_relationship") or update.get("relationship", "")
    old_desc = existing.get("current_relationship") or existing.get("relationship", "")

    # Only push to history if the description actually changed
    if new_desc and new_desc != old_desc and old_desc:
        if "history" not in existing:
            existing["history"] = []
        history_turn = existing.get("last_updated_turn") or existing.get("first_seen_turn", "")
        existing["history"].append({
            "turn": history_turn,
            "description": old_desc,
        })

    # Update current
    if new_desc:
        existing["current_relationship"] = new_desc
        # Also keep the old field for V1 compat if it exists
        if "relationship" in existing:
            existing["relationship"] = new_desc

    # Update other fields
    if update.get("type"):
        existing["type"] = update["type"]
    if update.get("direction"):
        existing["direction"] = update["direction"]
    if update.get("confidence") is not None:
        existing["confidence"] = update["confidence"]
    if update.get("last_updated_turn"):
        existing["last_updated_turn"] = update["last_updated_turn"]
    if update.get("status"):
        existing["status"] = update["status"]


def merge_relationships(catalogs: dict, relationships: list, turn_id: str) -> None:
    """Merge relationship edges into entity catalog entries.

    V2: consolidates per (source_id, target_id) pair.
    """
    for rel in relationships:
        source_id = rel.get("source_id")
        target_id = rel.get("target_id")
        relationship = rel.get("relationship") or rel.get("current_relationship")
        rel_type = rel.get("type")
        if not source_id or not target_id or not relationship or not rel_type:
            continue

        # Find the source entity
        result = find_entity_by_id(catalogs, source_id)
        if result is None:
            continue

        filename, entity = result

        if "relationships" not in entity:
            entity["relationships"] = []

        # Check for existing relationship by target_id (V2 per-pair dedup)
        existing_rel = None
        for r in entity["relationships"]:
            if r.get("target_id") == target_id:
                existing_rel = r
                break

        if existing_rel:
            # Consolidate into existing pair
            update_rel = {
                "current_relationship": relationship,
                "type": rel_type,
                "last_updated_turn": turn_id,
            }
            if rel.get("direction"):
                update_rel["direction"] = rel["direction"]
            if rel.get("confidence") is not None:
                update_rel["confidence"] = rel["confidence"]
            _consolidate_relationship(existing_rel, update_rel)
        else:
            # Add new relationship
            new_rel = {
                "target_id": target_id,
                "current_relationship": relationship,
                "type": rel_type,
                "status": "active",
            }
            if rel.get("direction"):
                new_rel["direction"] = rel["direction"]
            if rel.get("confidence") is not None:
                new_rel["confidence"] = rel["confidence"]
            new_rel["first_seen_turn"] = turn_id
            new_rel["last_updated_turn"] = turn_id
            entity["relationships"].append(new_rel)


# ---------------------------------------------------------------------------
# Dormancy marking
# ---------------------------------------------------------------------------

def mark_dormant_relationships(
    catalogs: dict,
    current_turn_id: str,
    dormancy_threshold: int = DEFAULT_DORMANCY_THRESHOLD,
) -> int:
    """Mark relationships as dormant when both source and target are inactive.

    A relationship is marked dormant if:
    - Its status is ``active``
    - Neither the source entity nor the target entity has been updated in the
      last *dormancy_threshold* turns.

    Returns the number of relationships marked dormant.
    """
    current_num = _parse_turn_number(current_turn_id)
    if current_num is None:
        return 0

    # Build a lookup of entity last_updated_turn numbers
    last_updated: dict[str, int] = {}
    for _filename, entities in catalogs.items():
        for entity in entities:
            eid = entity.get("id")
            turn_num = _parse_turn_number(entity.get("last_updated_turn"))
            if eid and turn_num is not None:
                last_updated[eid] = turn_num

    marked = 0
    for _filename, entities in catalogs.items():
        for entity in entities:
            source_id = entity.get("id")
            source_num = last_updated.get(source_id)
            for rel in entity.get("relationships", []):
                if rel.get("status") != "active":
                    continue
                target_id = rel.get("target_id")
                target_num = last_updated.get(target_id)
                # Both source and target must be stale
                source_stale = (
                    source_num is not None
                    and (current_num - source_num) >= dormancy_threshold
                )
                target_stale = (
                    target_num is not None
                    and (current_num - target_num) >= dormancy_threshold
                ) if target_num is not None else True  # unknown target = stale
                if source_stale and target_stale:
                    rel["status"] = "dormant"
                    marked += 1
    return marked


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def merge_events(events_list: list, new_events: list) -> None:
    """Merge new events into the events list, deduplicating by ID."""
    existing_ids = {e.get("id") for e in events_list}
    for event in new_events:
        if event.get("id") not in existing_ids:
            events_list.append(event)
            existing_ids.add(event["id"])


def get_next_event_id(events_list: list) -> int:
    """Get the next sequential event number."""
    max_num = 0
    for event in events_list:
        eid = event.get("id", "")
        m = re.match(r"^evt-(\d+)$", eid)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return max_num + 1
