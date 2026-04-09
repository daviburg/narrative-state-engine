#!/usr/bin/env python3
"""
catalog_merger.py — Merge agent-extracted entities, relationships, and events
into existing catalog files.

Handles:
- New entity insertion with ID prefix validation
- Existing entity updates (attributes, description, last_updated_turn)
- Name changes and alias tracking
- Relationship deduplication by (source_id, target_id, relationship)
- Event deduplication by ID
"""

import json
import os
import re

# Map entity type to catalog filename and ID prefix
TYPE_TO_CATALOG = {
    "character": "characters.json",
    "location": "locations.json",
    "faction": "factions.json",
    "item": "items.json",
    "creature": "characters.json",  # creatures go to characters catalog
    "concept": "items.json",  # concepts go to items catalog
}

TYPE_TO_PREFIX = {
    "character": "char-",
    "location": "loc-",
    "faction": "faction-",
    "item": "item-",
    "creature": "creature-",
    "concept": "concept-",
}


def load_catalogs(catalog_dir: str) -> dict:
    """Load all entity catalog files into a dict keyed by filename."""
    catalogs = {}
    for filename in ["characters.json", "locations.json", "factions.json", "items.json"]:
        filepath = os.path.join(catalog_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                catalogs[filename] = json.load(f)
        else:
            catalogs[filename] = []
    return catalogs


def load_events(catalog_dir: str) -> list:
    """Load the events catalog."""
    filepath = os.path.join(catalog_dir, "events.json")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_catalogs(catalog_dir: str, catalogs: dict, dry_run: bool = False) -> None:
    """Write all catalog files back to disk."""
    for filename, data in catalogs.items():
        filepath = os.path.join(catalog_dir, filename)
        if dry_run:
            print(f"  [DRY RUN] Would write {len(data)} entries to {filepath}")
            continue
        os.makedirs(catalog_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")


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


def find_entity_by_id(catalogs: dict, entity_id: str) -> tuple[str, dict] | None:
    """Find an entity across all catalogs. Returns (catalog_filename, entity) or None."""
    for filename, entities in catalogs.items():
        for entity in entities:
            if entity.get("id") == entity_id:
                return filename, entity
    return None


def format_known_entities(catalogs: dict) -> str:
    """Format all known entities as a compact table for the discovery prompt."""
    lines = []
    for filename, entities in catalogs.items():
        for entity in entities:
            lines.append(f"{entity['id']} | {entity['name']} | {entity['type']}")
    if not lines:
        return "(none — empty catalog)"
    return "\n".join(lines)


def validate_id_prefix(entity_id: str, entity_type: str) -> bool:
    """Check that an entity ID starts with the correct prefix for its type."""
    expected_prefix = TYPE_TO_PREFIX.get(entity_type)
    if not expected_prefix:
        return False
    return entity_id.startswith(expected_prefix)


def merge_entity(catalogs: dict, entity: dict) -> None:
    """Merge a single entity into the appropriate catalog.

    - New entities: validate and append.
    - Existing entities: deep-merge attributes, update description, update last_updated_turn.
    """
    entity_id = entity.get("id")
    entity_type = entity.get("type")

    if not entity_id or not entity_type:
        return

    if not validate_id_prefix(entity_id, entity_type):
        print(f"  WARNING: Entity '{entity_id}' has invalid prefix for type '{entity_type}'. Skipping.")
        return

    catalog_file = TYPE_TO_CATALOG.get(entity_type)
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
        # Ensure required fields for new entity
        required = ["id", "name", "type", "description", "first_seen_turn"]
        if all(entity.get(f) for f in required):
            catalogs[catalog_file].append(entity)
        else:
            missing = [f for f in required if not entity.get(f)]
            print(f"  WARNING: New entity '{entity_id}' missing required fields: {missing}. Skipping.")


def _update_existing_entity(current: dict, update: dict) -> None:
    """Update an existing entity with new information."""
    # Update description if new one adds information
    if update.get("description") and update["description"] != current.get("description"):
        current["description"] = update["description"]

    # Deep-merge attributes
    if update.get("attributes"):
        if "attributes" not in current:
            current["attributes"] = {}
        for key, value in update["attributes"].items():
            current["attributes"][key] = value

    # Handle name changes / aliases
    if update.get("name") and update["name"] != current.get("name"):
        old_name = current["name"]
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


def merge_relationships(catalogs: dict, relationships: list, turn_id: str) -> None:
    """Merge relationship edges into entity catalog entries.

    Deduplicates by (source_id, target_id, relationship).
    """
    for rel in relationships:
        source_id = rel.get("source_id")
        if not source_id:
            continue

        # Find the source entity
        result = find_entity_by_id(catalogs, source_id)
        if result is None:
            continue

        filename, entity = result

        if "relationships" not in entity:
            entity["relationships"] = []

        # Check for existing relationship (dedup)
        existing_rel = None
        for r in entity["relationships"]:
            if (r.get("target_id") == rel.get("target_id") and
                    r.get("relationship") == rel.get("relationship")):
                existing_rel = r
                break

        if existing_rel:
            # Update existing
            existing_rel["last_updated_turn"] = turn_id
            if rel.get("confidence") is not None:
                existing_rel["confidence"] = rel["confidence"]
        else:
            # Add new relationship
            new_rel = {
                "target_id": rel.get("target_id", ""),
                "relationship": rel.get("relationship", ""),
                "type": rel.get("type", "other"),
            }
            if rel.get("direction"):
                new_rel["direction"] = rel["direction"]
            if rel.get("confidence") is not None:
                new_rel["confidence"] = rel["confidence"]
            new_rel["first_seen_turn"] = turn_id
            new_rel["last_updated_turn"] = turn_id
            entity["relationships"].append(new_rel)


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
