#!/usr/bin/env python3
"""
build_context.py — Build focused entity context for a specific turn.

Scans turn transcripts for entity mentions, expands via one-hop active
relationships, loads full entity detail for scene entities, and produces
turn-context.json for the analysis agent.

Usage:
    python tools/build_context.py --session sessions/session-001 --turn turn-345 --framework framework/
    python tools/build_context.py --session sessions/session-001 --turn turn-345 --framework framework-local/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings

from build_scene_graph import load_scene_graph, query_nearby_from_index

# V2 per-entity directory names
_V2_DIRNAMES = ["characters", "locations", "factions", "items"]


# ---------------------------------------------------------------------------
# Step A: Read turn transcript
# ---------------------------------------------------------------------------

def read_turn_text(session_dir: str, turn_id: str) -> str:
    """Load DM and player turn files, return combined text.

    Raises FileNotFoundError if neither file exists.
    """
    transcript_dir = os.path.join(session_dir, "transcript")
    dm_path = os.path.join(transcript_dir, f"{turn_id}-dm.md")
    player_path = os.path.join(transcript_dir, f"{turn_id}-player.md")

    parts: list[str] = []
    for path in (dm_path, player_path):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                parts.append(f.read())

    if not parts:
        raise FileNotFoundError(
            f"No transcript files found for {turn_id} in {transcript_dir}. "
            f"Looked for {dm_path} and {player_path}"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Step B: Entity mention detection
# ---------------------------------------------------------------------------

def load_indexes(catalog_dir: str) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Load all index.json files and build name/ID lookup dictionaries.

    Returns (name_lookup, id_lookup) where:
    - name_lookup maps lowercased name -> list of index entries
    - id_lookup maps entity id -> index entry
    """
    name_lookup: dict[str, list[dict]] = {}
    id_lookup: dict[str, dict] = {}

    for dirname in _V2_DIRNAMES:
        index_path = os.path.join(catalog_dir, dirname, "index.json")
        if not os.path.isfile(index_path):
            continue
        with open(index_path, "r", encoding="utf-8-sig") as f:
            entries = json.load(f)
        for entry in entries:
            eid = entry["id"]
            id_lookup[eid] = entry
            name_lower = entry["name"].lower()
            name_lookup.setdefault(name_lower, []).append(entry)

    return name_lookup, id_lookup


def find_mentions(
    turn_text: str,
    name_lookup: dict[str, list[dict]],
    id_lookup: dict[str, dict],
) -> set[str]:
    """Scan turn text for entity names and IDs. Return set of matched entity IDs."""
    mentioned_ids: set[str] = set()
    text_lower = turn_text.lower()

    # Check entity IDs in text using identifier boundaries so one ID does not
    # falsely match inside another (e.g. "char-player" inside "char-player2").
    for eid in id_lookup:
        pattern = r"(?<![a-z0-9-])" + re.escape(eid) + r"(?![a-z0-9-])"
        if re.search(pattern, text_lower):
            mentioned_ids.add(eid)

    # Check entity names with boundary-aware matching
    # Sort by name length descending so longer names match first
    sorted_names = sorted(name_lookup.keys(), key=len, reverse=True)
    for name in sorted_names:
        if len(name) < 3:
            # Skip very short names to avoid false positives
            continue
        if " " in name:
            # Multi-word: require non-word boundaries at both ends so
            # phrases do not match inside larger words (e.g. "the campfire")
            pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        else:
            # Single-word (3+ chars): use word boundary
            pattern = r"\b" + re.escape(name) + r"\b"

        if re.search(pattern, text_lower):
            for entry in name_lookup[name]:
                mentioned_ids.add(entry["id"])

    return mentioned_ids


# ---------------------------------------------------------------------------
# Step C: One-hop relationship expansion
# ---------------------------------------------------------------------------

def load_entity_file(catalog_dir: str, entity_id: str, id_lookup: dict[str, dict]) -> dict | None:
    """Load a per-entity JSON file by ID. Returns None if not found."""
    entry = id_lookup.get(entity_id)
    if not entry:
        return None

    etype = entry.get("type", "")
    # Map type to directory name
    type_to_dir = {
        "character": "characters",
        "creature": "characters",
        "location": "locations",
        "faction": "factions",
        "item": "items",
        "concept": "items",
    }
    dirname = type_to_dir.get(etype)
    if not dirname:
        return None

    fpath = os.path.join(catalog_dir, dirname, f"{entity_id}.json")
    if not os.path.isfile(fpath):
        warnings.warn(f"Per-entity file not found: {fpath}", stacklevel=2)
        return None

    with open(fpath, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def expand_one_hop(
    mentioned_ids: set[str],
    catalog_dir: str,
    id_lookup: dict[str, dict],
) -> set[str]:
    """For each mentioned entity, find active relationship targets.

    Returns entity IDs that are relationship-expanded (not directly mentioned).
    """
    expanded_ids: set[str] = set()
    for eid in list(mentioned_ids):
        entity = load_entity_file(catalog_dir, eid, id_lookup)
        if not entity:
            continue
        for rel in entity.get("relationships", []):
            if rel.get("status", "active") == "active":
                target_id = rel["target_id"]
                if target_id not in mentioned_ids:
                    expanded_ids.add(target_id)
    return expanded_ids


# ---------------------------------------------------------------------------
# Step D: Load full entity detail
# ---------------------------------------------------------------------------

def build_scene_entity(
    entity: dict,
    id_lookup: dict[str, dict],
) -> dict:
    """Build a scene entity record from a full entity dict."""
    result: dict = {
        "id": entity["id"],
        "name": entity["name"],
        "identity": entity.get("identity", ""),
    }
    if entity.get("current_status"):
        result["current_status"] = entity["current_status"]
    if entity.get("volatile_state"):
        result["volatile_state"] = entity["volatile_state"]

    # Filter to active relationships and resolve target names
    active_rels = []
    for rel in entity.get("relationships", []):
        if rel.get("status", "active") == "active":
            rel_text = rel.get("current_relationship", "")
            rel_record: dict = {
                "target_id": rel["target_id"],
                "relationship": rel_text,
            }
            # Resolve target name from index
            target_entry = id_lookup.get(rel["target_id"])
            if target_entry:
                rel_record["target_name"] = target_entry["name"]
            if rel.get("type"):
                rel_record["type"] = rel["type"]
            if rel.get("status"):
                rel_record["status"] = rel["status"]
            active_rels.append(rel_record)
    if active_rels:
        result["active_relationships"] = active_rels

    return result


def build_scene_location(entity: dict) -> dict:
    """Build a scene location record from a full entity dict."""
    result: dict = {
        "id": entity["id"],
        "name": entity["name"],
    }
    if entity.get("identity"):
        result["identity"] = entity["identity"]
    if entity.get("current_status"):
        result["current_status"] = entity["current_status"]
    return result


def parse_turn_number(turn_id: str) -> int:
    """Extract numeric turn number from a turn ID like 'turn-345'."""
    m = re.match(r"^turn-(\d+)$", turn_id)
    if m:
        return int(m.group(1))
    return 0


def build_nearby_summary(
    id_lookup: dict[str, dict],
    scene_ids: set[str],
    current_turn: str,
    nearby_turns: int,
) -> list[dict]:
    """Build nearby_entities_summary for entities not in scene but recently updated."""
    current_num = parse_turn_number(current_turn)
    if current_num == 0:
        return []

    nearby = []
    for eid, entry in id_lookup.items():
        if eid in scene_ids:
            continue
        last_updated = entry.get("last_updated_turn", "")
        last_num = parse_turn_number(last_updated)
        if last_num == 0:
            continue
        if current_num - last_num <= nearby_turns:
            record: dict = {
                "id": entry["id"],
                "name": entry["name"],
            }
            if entry.get("status_summary"):
                record["status_summary"] = entry["status_summary"]
            nearby.append(record)

    # Sort by ID for deterministic output
    nearby.sort(key=lambda x: x["id"])
    return nearby


# ---------------------------------------------------------------------------
# Step E: Assemble and write turn-context.json
# ---------------------------------------------------------------------------

def build_context(
    session_dir: str,
    turn_id: str,
    framework_dir: str,
    nearby_turns: int = 10,
    output_path: str | None = None,
    use_scene_graph: bool = True,
) -> dict:
    """Main context-building pipeline. Returns the turn-context dict."""
    # Validate turn_id format early to avoid producing schema-invalid output
    if not re.match(r"^turn-[0-9]{3,}$", turn_id):
        raise ValueError(
            f"Invalid turn ID '{turn_id}'. Expected format: turn-NNN (e.g. turn-078)"
        )

    catalog_dir = os.path.join(framework_dir, "catalogs")

    # Step A: Read turn transcript
    turn_text = read_turn_text(session_dir, turn_id)

    # Step B: Load indexes and find mentions
    name_lookup, id_lookup = load_indexes(catalog_dir)

    if not id_lookup:
        warnings.warn("No entity catalogs found. Producing empty context.")
        context: dict = {
            "as_of_turn": turn_id,
            "scene_entities": [],
            "scene_locations": [],
            "nearby_entities_summary": [],
        }
        _write_output(context, session_dir, output_path)
        return context

    mentioned_ids = find_mentions(turn_text, name_lookup, id_lookup)

    # Step C: One-hop expansion
    expanded_ids = expand_one_hop(mentioned_ids, catalog_dir, id_lookup)
    all_scene_ids = mentioned_ids | expanded_ids

    # Step D: Load full detail
    scene_entities: list[dict] = []
    scene_locations: list[dict] = []
    location_ids_from_volatile: set[str] = set()

    for eid in sorted(all_scene_ids):
        entity = load_entity_file(catalog_dir, eid, id_lookup)
        if not entity:
            continue

        entry = id_lookup.get(eid, {})
        etype = entry.get("type", entity.get("type", ""))

        if etype == "location":
            scene_locations.append(build_scene_location(entity))
        else:
            scene_entities.append(build_scene_entity(entity, id_lookup))

        # Check volatile_state.location for location references
        vol_loc = entity.get("volatile_state", {}).get("location", "")
        if vol_loc and vol_loc.startswith("loc-") and vol_loc not in all_scene_ids:
            location_ids_from_volatile.add(vol_loc)

    # Load locations referenced by volatile_state
    for loc_id in sorted(location_ids_from_volatile):
        loc_entity = load_entity_file(catalog_dir, loc_id, id_lookup)
        if loc_entity:
            scene_locations.append(build_scene_location(loc_entity))
            all_scene_ids.add(loc_id)

    # Step D (nearby): entities not in scene but recently updated
    # Use scene graph index when available for O(T) instead of O(N) lookup
    scene_graph = load_scene_graph(framework_dir) if use_scene_graph else None
    if scene_graph and scene_graph.get("turn_activity"):
        nearby_eids = query_nearby_from_index(
            scene_graph, all_scene_ids, turn_id, nearby_turns,
        )
        nearby = []
        for eid in nearby_eids:
            entry = id_lookup.get(eid)
            if not entry:
                continue
            record: dict = {"id": entry["id"], "name": entry["name"]}
            if entry.get("status_summary"):
                record["status_summary"] = entry["status_summary"]
            nearby.append(record)
    else:
        nearby = build_nearby_summary(id_lookup, all_scene_ids, turn_id, nearby_turns)

    # Step E: Assemble output
    context = {
        "as_of_turn": turn_id,
        "scene_entities": scene_entities,
    }
    if scene_locations:
        context["scene_locations"] = scene_locations
    if nearby:
        context["nearby_entities_summary"] = nearby

    _write_output(context, session_dir, output_path)
    return context


def _write_output(context: dict, session_dir: str, output_path: str | None) -> None:
    """Write turn-context.json to disk."""
    if output_path is None:
        derived_dir = os.path.join(session_dir, "derived")
        os.makedirs(derived_dir, exist_ok=True)
        output_path = os.path.join(derived_dir, "turn-context.json")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build focused entity context for a specific turn.",
    )
    parser.add_argument(
        "--session", required=True,
        help="Path to session directory, e.g. sessions/session-001",
    )
    parser.add_argument(
        "--turn", required=True,
        help="Turn ID, e.g. turn-345",
    )
    parser.add_argument(
        "--framework", required=True,
        help="Path to framework directory, e.g. framework/ or framework-local/",
    )
    parser.add_argument(
        "--nearby-turns", type=int, default=10,
        help="Recency threshold for nearby entities (default: 10)",
    )
    parser.add_argument(
        "--output",
        help="Override output path (default: {session}/derived/turn-context.json)",
    )
    parser.add_argument(
        "--no-scene-graph", action="store_true",
        help="Disable scene graph index for nearby entity lookup.",
    )
    args = parser.parse_args()

    try:
        build_context(
            session_dir=args.session,
            turn_id=args.turn,
            framework_dir=args.framework,
            nearby_turns=args.nearby_turns,
            output_path=args.output,
            use_scene_graph=not args.no_scene_graph,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
