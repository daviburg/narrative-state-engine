#!/usr/bin/env python3
"""
semantic_extraction.py — Core orchestrator for LLM-based semantic extraction.

Processes RPG session transcript turns through four agent roles:
1. Entity Discovery — identify entities mentioned in a turn
2. Entity Detail Extractor — extract/update attributes per entity
3. Relationship Mapper — identify cross-entity relationships
4. Event Extractor — identify narrative events

Works in both batch (bootstrap) and incremental (ingest) modes.
"""

import json
import os
import sys

from catalog_merger import (
    load_catalogs,
    load_events,
    save_catalogs,
    save_events,
    format_known_entities,
    find_entity_by_id,
    merge_entity,
    merge_relationships,
    merge_events,
    get_next_event_id,
)
from llm_client import LLMClient, LLMExtractionError

try:
    import jsonschema
except ImportError:
    jsonschema = None

# Default confidence threshold — entities below this are logged but not cataloged
DEFAULT_MIN_CONFIDENCE = 0.6

# Allowed attribute keys for the player character — safety net for prompt discipline
PC_ALLOWED_ATTRS = {
    "race", "class", "abilities", "appearance", "hp_change",
    "condition", "equipment", "quest", "allegiance", "status", "aliases",
}


def _filter_pc_attributes(entity_data: dict) -> dict:
    """Strip non-allowed attribute keys from char-player entities."""
    if entity_data.get("id") != "char-player":
        return entity_data
    attrs = entity_data.get("attributes", {})
    disallowed = {k for k in attrs if k not in PC_ALLOWED_ATTRS}
    if disallowed:
        print(
            f"  WARNING: Dropping non-allowed char-player attributes: {sorted(disallowed)}",
            file=sys.stderr,
        )
        entity_data["attributes"] = {k: v for k, v in attrs.items() if k in PC_ALLOWED_ATTRS}
    return entity_data


def _sanitize_pc_catalog_entry(catalogs: dict) -> None:
    """Purge non-allowed attribute keys from the stored char-player catalog entry.

    Ensures historical action-sprawl attributes are cleaned up even if they
    were merged before the filter was in place.
    """
    for entity in catalogs.get("characters.json", []):
        if entity.get("id") != "char-player":
            continue
        attrs = entity.get("attributes", {})
        disallowed = {k for k in attrs if k not in PC_ALLOWED_ATTRS}
        if disallowed:
            print(
                f"  WARNING: Purging stale char-player catalog attributes: {sorted(disallowed)}",
                file=sys.stderr,
            )
            entity["attributes"] = {k: v for k, v in attrs.items() if k in PC_ALLOWED_ATTRS}
        break

# Directory containing prompt templates (relative to repo root)
TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates",
    "extraction",
)

# Directory containing JSON schemas (relative to repo root)
SCHEMAS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "schemas",
)

_schema_cache: dict = {}


def _load_schema(name: str) -> dict | None:
    """Load and cache a JSON schema by filename (e.g. 'entity.schema.json')."""
    if jsonschema is None:
        return None
    if name not in _schema_cache:
        path = os.path.join(SCHEMAS_DIR, name)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            _schema_cache[name] = json.load(f)
    return _schema_cache.get(name)


def _validate_entity(entity_data: dict) -> bool:
    """Validate an entity dict against entity.schema.json. Returns True if valid."""
    schema = _load_schema("entity.schema.json")
    if schema is None:
        return True  # Skip validation if jsonschema not available
    try:
        jsonschema.validate(entity_data, schema)
        return True
    except jsonschema.ValidationError as e:
        print(f"  WARNING: Entity failed schema validation: {e.message}", file=sys.stderr)
        return False


def _validate_event(event_data: dict) -> bool:
    """Validate an event dict against event.schema.json. Returns True if valid."""
    schema = _load_schema("event.schema.json")
    if schema is None:
        return True
    try:
        jsonschema.validate(event_data, schema)
        return True
    except jsonschema.ValidationError as e:
        print(f"  WARNING: Event failed schema validation: {e.message}", file=sys.stderr)
        return False


PLAYER_CHARACTER_SEED = {
    "id": "char-player",
    "name": "Player Character",
    "type": "character",
    "description": "The player character (referred to as 'you' in DM narration).",
    "attributes": {},
    "first_seen_turn": "turn-001",
    "last_updated_turn": "turn-001",
}


def _ensure_player_character(catalogs: dict, first_turn_id: str | None = None) -> None:
    """Pre-seed the player character entry if it doesn't already exist.

    *first_turn_id* overrides the default ``turn-001`` provenance so the
    seed is correct when extraction starts from a later turn.
    """
    for entity in catalogs.get("characters.json", []):
        if entity.get("id") == "char-player":
            return
    seed = dict(PLAYER_CHARACTER_SEED)
    if first_turn_id:
        seed["first_seen_turn"] = first_turn_id
        seed["last_updated_turn"] = first_turn_id
    catalogs.setdefault("characters.json", []).append(seed)


def load_template(name: str) -> str:
    """Load a prompt template by name (without .md extension)."""
    filepath = os.path.join(TEMPLATES_DIR, f"{name}.md")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def format_discovery_prompt(turn: dict, known_entities: str) -> str:
    """Format the user prompt for entity discovery."""
    return (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Known Entities\n{known_entities}"
    )


def format_detail_prompt(turn: dict, entity_ref: dict, current_entry: dict | None) -> str:
    """Format the user prompt for entity detail extraction."""
    entry_json = json.dumps(current_entry, indent=2) if current_entry else "{}"
    return (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Entity to Extract/Update\n"
        f"Entity ID: {entity_ref.get('existing_id') or entity_ref.get('proposed_id')}\n"
        f"Entity Name: {entity_ref['name']}\n"
        f"Entity Type: {entity_ref['type']}\n\n"
        f"## Current Catalog Entry\n```json\n{entry_json}\n```"
    )


def format_relationship_prompt(turn: dict, mentioned_entities: list) -> str:
    """Format the user prompt for relationship mapping."""
    entities_text = "\n".join(
        f"- {e['id']}: {e['name']} ({e['type']})"
        for e in mentioned_entities
    )
    return (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Entities Mentioned in This Turn\n{entities_text}"
    )


def format_event_prompt(turn: dict, next_event_id: int, entity_ids: list) -> str:
    """Format the user prompt for event extraction."""
    ids_text = ", ".join(entity_ids) if entity_ids else "(none)"
    return (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Next Event ID\n{next_event_id}\n\n"
        f"## Known Entity IDs in This Turn\n{ids_text}"
    )


def filter_by_confidence(discovered: list, min_confidence: float) -> list:
    """Filter discovered entities by confidence threshold."""
    return [e for e in discovered if e.get("confidence", 0) >= min_confidence]


def get_entity_id(entity_ref: dict) -> str:
    """Get the entity ID from a discovery result (existing_id or proposed_id)."""
    return entity_ref.get("existing_id") or entity_ref.get("proposed_id") or ""


def extract_and_merge(
    turn: dict,
    catalogs: dict,
    events_list: list,
    llm: LLMClient,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> tuple[dict, list]:
    """Process one turn through all extraction agents.

    Args:
        turn: Dict with keys turn_id, speaker, text.
        catalogs: Dict keyed by catalog filename, values are entity arrays.
        events_list: Current list of events.
        llm: LLM client instance.
        min_confidence: Minimum confidence to catalog an entity.

    Returns:
        Updated (catalogs, events_list) tuple.
    """
    turn_id = turn["turn_id"]

    # --- 1. Entity Discovery ---
    known = format_known_entities(catalogs)
    try:
        discovery_result = llm.extract_json(
            system_prompt=load_template("entity-discovery"),
            user_prompt=format_discovery_prompt(turn, known),
        )
    except LLMExtractionError as e:
        print(f"  WARNING: Entity discovery failed for {turn_id}: {e}", file=sys.stderr)
        discovery_result = {"entities": []}

    if not isinstance(discovery_result, dict):
        print(f"  WARNING: Discovery returned non-dict for {turn_id}, skipping", file=sys.stderr)
        discovery_result = {"entities": []}

    discovered = discovery_result.get("entities", [])

    # Post-process discovery results: ensure provenance and fix ID prefixes
    for entity in discovered:
        # Ensure source_turn is always set (smaller models may omit it)
        if not entity.get("source_turn"):
            entity["source_turn"] = turn_id
        # Fix proposed_id prefix if it doesn't match the declared type
        pid = entity.get("proposed_id", "")
        etype = entity.get("type", "")
        if pid and etype:
            from catalog_merger import fix_id_prefix, validate_id_prefix
            if not validate_id_prefix(pid, etype):
                entity["proposed_id"] = fix_id_prefix(pid, etype)

    # Filter by confidence
    qualified = filter_by_confidence(discovered, min_confidence)

    # --- 2. Entity Detail Extraction (per entity above threshold) ---
    for entity_ref in qualified:
        entity_id = get_entity_id(entity_ref)
        if not entity_id:
            continue

        # Look up current entry for existing entities
        current_entry = None
        if not entity_ref.get("is_new", True):
            result = find_entity_by_id(catalogs, entity_id)
            if result:
                _, current_entry = result

        try:
            detail_result = llm.extract_json(
                system_prompt=load_template("entity-detail"),
                user_prompt=format_detail_prompt(turn, entity_ref, current_entry),
            )
        except LLMExtractionError as e:
            print(f"  WARNING: Detail extraction failed for {entity_id} at {turn_id}: {e}", file=sys.stderr)
            continue

        entity_data = detail_result.get("entity")
        if entity_data and _validate_entity(entity_data):
            _filter_pc_attributes(entity_data)
            merge_entity(catalogs, entity_data)
            # If this was char-player, also purge stale keys from catalog
            if entity_data.get("id") == "char-player":
                _sanitize_pc_catalog_entry(catalogs)

        llm.delay()

    # --- 2b. Always run detail extraction for the player character ---
    # The PC is "you" in DM narration, so they won't be "discovered" but are
    # affected by almost every turn.
    pc_already_extracted = any(
        get_entity_id(e) == "char-player" for e in qualified
    )
    if not pc_already_extracted:
        pc_result = find_entity_by_id(catalogs, "char-player")
        pc_entry = pc_result[1] if pc_result else dict(PLAYER_CHARACTER_SEED)
        # Sanitize existing entry before sending to LLM so stale keys
        # don't appear in the prompt and get echoed back.
        _sanitize_pc_catalog_entry(catalogs)
        pc_ref = {"name": pc_entry["name"], "type": "character",
                  "existing_id": "char-player", "is_new": False}
        try:
            detail_result = llm.extract_json(
                system_prompt=load_template("entity-detail"),
                user_prompt=format_detail_prompt(turn, pc_ref, pc_entry),
            )
            entity_data = detail_result.get("entity")
            if entity_data and _validate_entity(entity_data):
                _filter_pc_attributes(entity_data)
                merge_entity(catalogs, entity_data)
                # Purge any stale keys that survived the merge
                _sanitize_pc_catalog_entry(catalogs)
        except LLMExtractionError as e:
            print(f"  WARNING: PC detail extraction failed at {turn_id}: {e}", file=sys.stderr)
        llm.delay()

    # --- 3. Relationship Mapping ---
    mentioned_entities = []
    for entity_ref in qualified:
        eid = get_entity_id(entity_ref)
        if eid:
            mentioned_entities.append({
                "id": eid,
                "name": entity_ref["name"],
                "type": entity_ref["type"],
            })

    # Always include the player character in relationships and events
    if not any(e["id"] == "char-player" for e in mentioned_entities):
        pc_result = find_entity_by_id(catalogs, "char-player")
        pc_name = pc_result[1]["name"] if pc_result else "Player Character"
        mentioned_entities.append({
            "id": "char-player",
            "name": pc_name,
            "type": "character",
        })

    if len(mentioned_entities) >= 2:
        try:
            rel_result = llm.extract_json(
                system_prompt=load_template("relationship-mapper"),
                user_prompt=format_relationship_prompt(turn, mentioned_entities),
            )
            relationships = rel_result.get("relationships", [])
            if relationships:
                merge_relationships(catalogs, relationships, turn_id)
        except LLMExtractionError as e:
            print(f"  WARNING: Relationship mapping failed for {turn_id}: {e}", file=sys.stderr)

    # --- 4. Event Extraction ---
    next_evt_id = get_next_event_id(events_list)
    entity_ids = [e["id"] for e in mentioned_entities]

    try:
        event_result = llm.extract_json(
            system_prompt=load_template("event-extractor"),
            user_prompt=format_event_prompt(turn, next_evt_id, entity_ids),
        )
        new_events = event_result.get("events", [])
        valid_events = [e for e in new_events if _validate_event(e)]
        if valid_events:
            merge_events(events_list, valid_events)
    except LLMExtractionError as e:
        print(f"  WARNING: Event extraction failed for {turn_id}: {e}", file=sys.stderr)

    llm.delay()
    return catalogs, events_list


def _dedup_catalogs(catalogs: dict) -> tuple[int, dict[str, str]]:
    """Post-batch deduplication pass.

    Merges entities within each catalog file that share the same lowercased
    name or have overlapping aliases.  The entry seen earliest
    (lowest first_seen_turn) is kept as the survivor; later duplicates are
    merged into it via ``dedup_merge_entity`` from catalog_merger.
    Returns (merge_count, merge_map) where merge_map maps each removed
    entity ID to its survivor ID.
    """
    from catalog_merger import dedup_merge_entity

    merged_count = 0
    merge_map: dict[str, str] = {}

    for filename, entities in catalogs.items():
        # Build lookup: normalised name -> list of indices
        name_map: dict[str, list[int]] = {}
        for idx, entity in enumerate(entities):
            # Collect all names this entity is known by
            names = {entity.get("name", "").strip().lower()}
            aliases_str = entity.get("attributes", {}).get("aliases", "")
            if aliases_str:
                for a in aliases_str.split(","):
                    a = a.strip().lower()
                    if a:
                        names.add(a)
            for n in names:
                if n:
                    name_map.setdefault(n, []).append(idx)

        # Identify groups of indices that should be merged (connected-component)
        parent: dict[int, int] = {}

        def find(x: int) -> int:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for _name, idxs in name_map.items():
            for i in range(1, len(idxs)):
                union(idxs[0], idxs[i])

        # Group by root
        groups: dict[int, list[int]] = {}
        for idx in range(len(entities)):
            root = find(idx)
            groups.setdefault(root, []).append(idx)

        # Merge groups with more than one member
        to_remove: set[int] = set()
        for _root, members in groups.items():
            if len(members) < 2:
                continue
            # Keep the entry with the earliest first_seen_turn
            members.sort(key=lambda i: entities[i].get("first_seen_turn", ""))
            survivor_idx = members[0]
            survivor_id = entities[survivor_idx].get("id", "")
            for dup_idx in members[1:]:
                removed_id = entities[dup_idx].get("id", "")
                dedup_merge_entity(entities[survivor_idx], entities[dup_idx])
                to_remove.add(dup_idx)
                if removed_id and survivor_id:
                    merge_map[removed_id] = survivor_id
                merged_count += 1

        if to_remove:
            catalogs[filename] = [e for i, e in enumerate(entities) if i not in to_remove]

    return merged_count, merge_map


def _rewrite_stale_ids(catalogs: dict, events_list: list, merge_map: dict[str, str]) -> None:
    """Replace dangling entity IDs left by dedup with their survivor IDs."""
    if not merge_map:
        return

    # Rewrite event related_entities
    for event in events_list:
        related = event.get("related_entities", [])
        event["related_entities"] = [merge_map.get(eid, eid) for eid in related]

    # Rewrite relationship source_id and target_id in catalog entries
    for _filename, entities in catalogs.items():
        for entity in entities:
            for rel in entity.get("relationships", []):
                if rel.get("source_id") in merge_map:
                    rel["source_id"] = merge_map[rel["source_id"]]
                if rel.get("target_id") in merge_map:
                    rel["target_id"] = merge_map[rel["target_id"]]


def extract_semantic_batch(
    turn_dicts: list,
    session_dir: str,
    framework_dir: str = "framework",
    config_path: str = "config/llm.json",
    dry_run: bool = False,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> None:
    """Run semantic extraction over all turns in batch mode.

    Called from bootstrap_session.py after extract_all().

    Args:
        turn_dicts: List of dicts with keys turn_id, speaker, text.
        session_dir: Path to the session directory.
        framework_dir: Path to the framework directory containing catalogs.
        config_path: Path to LLM configuration file.
        dry_run: If True, don't write files.
        min_confidence: Minimum confidence to catalog an entity.
    """
    try:
        llm = LLMClient(config_path)
    except (ImportError, LLMExtractionError, FileNotFoundError) as e:
        print(f"  WARNING: Semantic extraction not available: {e}", file=sys.stderr)
        return

    catalog_dir = os.path.join(framework_dir, "catalogs")
    catalogs = load_catalogs(catalog_dir)
    events_list = load_events(catalog_dir)

    # Pre-seed the player character so it can be tracked every turn
    first_turn = turn_dicts[0]["turn_id"] if turn_dicts else None
    _ensure_player_character(catalogs, first_turn)

    # Progress tracking
    progress_file = os.path.join(session_dir, "derived", "extraction-progress.json")
    start_from = 0

    # Resume from last checkpoint if available
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                progress = json.load(f)
            last_completed = progress.get("last_completed_turn", "")
            if last_completed:
                for i, t in enumerate(turn_dicts):
                    if t["turn_id"] == last_completed:
                        start_from = i + 1
                        break
                if start_from > 0:
                    print(f"  Resuming from turn {start_from + 1} (after {last_completed})")
                    # Reload catalogs since they may have been partially written
                    catalogs = load_catalogs(catalog_dir)
                    events_list = load_events(catalog_dir)
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupted progress file; start from beginning

    total = len(turn_dicts)

    print(f"  Processing {total - start_from} turns for semantic extraction...")

    for i in range(start_from, total):
        turn = turn_dicts[i]
        turn_id = turn["turn_id"]

        if (i - start_from) % 25 == 0 and i > start_from:
            entities_now = sum(len(v) for v in catalogs.values())
            print(f"  ... {turn_id} ({i + 1}/{total}, {entities_now} entities)")

        try:
            catalogs, events_list = extract_and_merge(
                turn, catalogs, events_list, llm, min_confidence
            )
        except Exception as e:
            print(f"  ERROR at {turn_id}: {e}", file=sys.stderr)
            # Save progress and continue
            _save_progress(progress_file, turn_dicts[i - 1]["turn_id"] if i > 0 else "",
                           total, catalogs, dry_run)
            continue

        # Checkpoint every 50 turns
        if (i + 1) % 50 == 0:
            _save_progress(progress_file, turn_id, total, catalogs, dry_run)
            if not dry_run:
                save_catalogs(catalog_dir, catalogs)
                save_events(catalog_dir, events_list)

    # Final save
    entities_after = sum(len(v) for v in catalogs.values())
    events_after = len(events_list)

    # Post-batch dedup: merge entities that share the same name/aliases but got separate IDs
    dupes_merged, merge_map = _dedup_catalogs(catalogs)
    if dupes_merged:
        _rewrite_stale_ids(catalogs, events_list, merge_map)
        entities_after = sum(len(v) for v in catalogs.values())
        print(f"  Post-batch dedup merged {dupes_merged} duplicate(s); {entities_after} entities remain")

    print(f"  Semantic extraction complete: {entities_after} entities, {events_after} events")

    if not dry_run:
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        _save_progress(progress_file, turn_dicts[-1]["turn_id"] if turn_dicts else "",
                       total, catalogs, dry_run=False, completed=True)


def extract_semantic_single(
    turn_id: str,
    speaker: str,
    text: str,
    session_dir: str,
    framework_dir: str = "framework",
    config_path: str = "config/llm.json",
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> None:
    """Run semantic extraction for a single new turn.

    Called from ingest_turn.py after writing the turn file.

    Args:
        turn_id: The turn identifier (e.g. "turn-345").
        speaker: "player" or "dm".
        text: The turn text.
        session_dir: Path to the session directory.
        framework_dir: Path to the framework directory containing catalogs.
        config_path: Path to LLM configuration file.
        min_confidence: Minimum confidence to catalog an entity.
    """
    try:
        llm = LLMClient(config_path)
    except (ImportError, LLMExtractionError, FileNotFoundError) as e:
        print(f"  WARNING: Semantic extraction not available: {e}", file=sys.stderr)
        return

    catalog_dir = os.path.join(framework_dir, "catalogs")
    catalogs = load_catalogs(catalog_dir)
    events_list = load_events(catalog_dir)

    # Pre-seed the player character so it can be tracked every turn
    _ensure_player_character(catalogs, turn_id)

    turn = {"turn_id": turn_id, "speaker": speaker, "text": text}

    print(f"  Running semantic extraction for {turn_id}...")
    catalogs, events_list = extract_and_merge(
        turn, catalogs, events_list, llm, min_confidence
    )

    entities_total = sum(len(v) for v in catalogs.values())
    print(f"  Catalog now has {entities_total} entities, {len(events_list)} events")

    save_catalogs(catalog_dir, catalogs)
    save_events(catalog_dir, events_list)


def _save_progress(
    progress_file: str,
    last_turn: str,
    total: int,
    catalogs: dict,
    dry_run: bool,
    completed: bool = False,
) -> None:
    """Save extraction progress for resumption."""
    if dry_run:
        return
    entities = sum(len(v) for v in catalogs.values())
    progress = {
        "last_completed_turn": last_turn,
        "total_turns": total,
        "entities_discovered": entities,
        "completed": completed,
    }
    os.makedirs(os.path.dirname(progress_file), exist_ok=True)
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
        f.write("\n")
