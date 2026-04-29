#!/usr/bin/env python3
"""
build_scene_graph.py — Build a cross-type spatial and temporal index from entity catalogs.

Produces scene-graph.json: an inverted index that supports scene-resolution
queries without scanning every entity file.

Three index structures:
  - location_index: location ID → entities present (via volatile_state.location)
  - turn_activity: turn ID → entity IDs introduced or updated on that turn
  - location_connections: spatial edges between locations from relationships

Usage:
    python tools/build_scene_graph.py --framework framework/
    python tools/build_scene_graph.py --framework framework/ --output framework/catalogs/scene-graph.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone


# V2 per-entity directory names
_V2_DIRNAMES = ["characters", "locations", "factions", "items"]


def parse_turn_number(turn_id: str) -> int:
    """Extract numeric turn number from a turn ID like 'turn-345'."""
    m = re.match(r"^turn-(\d+)$", turn_id)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Index loading
# ---------------------------------------------------------------------------

def load_all_entities(catalog_dir: str) -> list[dict]:
    """Load all per-entity JSON files from V2 catalog directories.

    Returns a flat list of entity dicts.
    """
    entities: list[dict] = []
    for dirname in _V2_DIRNAMES:
        dirpath = os.path.join(catalog_dir, dirname)
        if not os.path.isdir(dirpath):
            continue
        for filename in sorted(os.listdir(dirpath)):
            if filename == "index.json" or not filename.endswith(".json"):
                continue
            filepath = os.path.join(dirpath, filename)
            with open(filepath, "r", encoding="utf-8-sig") as f:
                entity = json.load(f)
            entities.append(entity)
    return entities


# ---------------------------------------------------------------------------
# Build location index
# ---------------------------------------------------------------------------

def build_location_index(
    entities: list[dict],
    location_names: dict[str, str],
) -> dict[str, dict]:
    """Build inverted index: location ID → entities present there.

    Args:
        entities: All loaded entity dicts.
        location_names: Mapping of location ID to display name.

    Returns:
        Dict keyed by location ID with location_name and entities list.
    """
    index: dict[str, list[dict]] = {}

    for entity in entities:
        vol_loc = entity.get("volatile_state", {}).get("location", "")
        if not vol_loc or not vol_loc.startswith("loc-"):
            continue

        entry = {
            "id": entity["id"],
            "name": entity["name"],
            "type": entity.get("type", ""),
        }
        last_updated = entity.get("last_updated_turn", "")
        if last_updated:
            entry["last_updated_turn"] = last_updated

        index.setdefault(vol_loc, []).append(entry)

    # Wrap with location names
    result: dict[str, dict] = {}
    for loc_id, ents in sorted(index.items()):
        result[loc_id] = {
            "location_name": location_names.get(loc_id, loc_id),
            "entities": sorted(ents, key=lambda e: e["id"]),
        }
    return result


# ---------------------------------------------------------------------------
# Build turn activity index
# ---------------------------------------------------------------------------

def build_turn_activity(entities: list[dict]) -> dict[str, list[str]]:
    """Build index: turn ID → entity IDs introduced or updated on that turn.

    Records both first_seen_turn and last_updated_turn for each entity.
    """
    activity: dict[str, set[str]] = {}

    for entity in entities:
        eid = entity["id"]
        for field in ("first_seen_turn", "last_updated_turn"):
            turn = entity.get(field, "")
            if turn and parse_turn_number(turn) > 0:
                activity.setdefault(turn, set()).add(eid)

    # Convert sets to sorted lists for deterministic output
    return {
        turn: sorted(eids)
        for turn, eids in sorted(activity.items(), key=lambda x: parse_turn_number(x[0]))
    }


# ---------------------------------------------------------------------------
# Build location connections
# ---------------------------------------------------------------------------

def build_location_connections(entities: list[dict]) -> list[dict]:
    """Extract spatial connections between locations from relationship data.

    Looks at location entities' relationships targeting other locations.
    """
    connections: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for entity in entities:
        if entity.get("type") != "location":
            continue
        source_id = entity["id"]
        for rel in entity.get("relationships", []):
            target_id = rel.get("target_id", "")
            if not target_id.startswith("loc-"):
                continue
            # Deduplicate bidirectional edges and emit in canonical order
            edge = tuple(sorted([source_id, target_id]))
            if edge in seen:
                continue
            seen.add(edge)

            conn: dict = {
                "source": edge[0],
                "target": edge[1],
            }
            if rel.get("current_relationship"):
                conn["relationship"] = rel["current_relationship"]
            status = rel.get("status", "active")
            if status:
                conn["status"] = status
            connections.append(conn)

    connections.sort(key=lambda c: (c["source"], c["target"]))
    return connections


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_scene_graph(
    framework_dir: str,
    output_path: str | None = None,
) -> dict:
    """Build the scene graph index from existing catalog files.

    Args:
        framework_dir: Path to framework directory.
        output_path: Override output path. Defaults to framework/catalogs/scene-graph.json.

    Returns:
        The scene graph dict.
    """
    catalog_dir = os.path.join(framework_dir, "catalogs")
    entities = load_all_entities(catalog_dir)

    # Collect location names for the location index
    location_names: dict[str, str] = {}
    for entity in entities:
        if entity.get("type") == "location":
            location_names[entity["id"]] = entity["name"]

    # Also check index.json for location names (entities without per-entity files)
    loc_index_path = os.path.join(catalog_dir, "locations", "index.json")
    if os.path.isfile(loc_index_path):
        with open(loc_index_path, "r", encoding="utf-8-sig") as f:
            for entry in json.load(f):
                if entry["id"] not in location_names:
                    location_names[entry["id"]] = entry["name"]

    location_index = build_location_index(entities, location_names)
    turn_activity = build_turn_activity(entities)
    location_connections = build_location_connections(entities)

    # Determine latest turn
    max_turn = "turn-000"
    max_num = 0
    for entity in entities:
        for field in ("first_seen_turn", "last_updated_turn"):
            turn = entity.get(field, "")
            num = parse_turn_number(turn)
            if num > max_num:
                max_num = num
                max_turn = turn

    scene_graph: dict = {
        "as_of_turn": max_turn,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "location_index": location_index,
        "turn_activity": turn_activity,
        "location_connections": location_connections,
        "entity_count": len(entities),
    }

    # Write output
    if output_path is None:
        output_path = os.path.join(catalog_dir, "scene-graph.json")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scene_graph, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {output_path} ({len(entities)} entities, "
          f"{len(location_index)} locations, "
          f"{len(turn_activity)} turns, "
          f"{len(location_connections)} connections)")

    return scene_graph


# ---------------------------------------------------------------------------
# Query helpers (used by build_context.py integration)
# ---------------------------------------------------------------------------

def load_scene_graph(framework_dir: str) -> dict | None:
    """Load scene-graph.json if it exists. Returns None if not found."""
    path = os.path.join(framework_dir, "catalogs", "scene-graph.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def query_entities_at_location(scene_graph: dict, location_id: str) -> list[dict]:
    """Return entities present at a given location."""
    loc_entry = scene_graph.get("location_index", {}).get(location_id)
    if not loc_entry:
        return []
    return loc_entry.get("entities", [])


def _format_turn_id(turn_num: int) -> str:
    """Format a turn number as a zero-padded turn ID (e.g. 5 -> 'turn-005')."""
    return f"turn-{turn_num:03d}"


def query_active_in_turn_range(
    scene_graph: dict,
    start_turn: int,
    end_turn: int,
) -> set[str]:
    """Return entity IDs that were active (introduced or updated) in a turn range.

    O(T) where T = end_turn - start_turn, using direct dict lookups.
    """
    result: set[str] = set()
    turn_activity = scene_graph.get("turn_activity", {})
    for turn_num in range(start_turn, end_turn + 1):
        turn_id = _format_turn_id(turn_num)
        eids = turn_activity.get(turn_id)
        if eids:
            result.update(eids)
    return result


def query_nearby_from_index(
    scene_graph: dict,
    scene_ids: set[str],
    current_turn: str,
    nearby_turns: int,
) -> list[str]:
    """Return entity IDs not in scene_ids but active within nearby_turns of current_turn.

    O(T) where T = nearby_turns, using direct dict lookups into turn_activity
    rather than O(N) scan over all entities.
    """
    current_num = parse_turn_number(current_turn)
    if current_num == 0:
        return []

    start = max(1, current_num - nearby_turns)
    nearby_ids: set[str] = set()
    turn_activity = scene_graph.get("turn_activity", {})
    for turn_num in range(start, current_num + 1):
        turn_id = _format_turn_id(turn_num)
        eids = turn_activity.get(turn_id)
        if eids:
            nearby_ids.update(eids)

    # Exclude entities already in the scene
    return sorted(nearby_ids - scene_ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build scene graph index from entity catalogs.",
    )
    parser.add_argument(
        "--framework", required=True,
        help="Path to framework directory, e.g. framework/",
    )
    parser.add_argument(
        "--output",
        help="Override output path (default: framework/catalogs/scene-graph.json)",
    )
    args = parser.parse_args()

    build_scene_graph(
        framework_dir=args.framework,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
