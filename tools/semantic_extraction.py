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

# Default confidence threshold — entities below this are logged but not cataloged
DEFAULT_MIN_CONFIDENCE = 0.6

# Directory containing prompt templates (relative to repo root)
TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates",
    "extraction",
)


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
        return catalogs, events_list

    discovered = discovery_result.get("entities", [])
    if not discovered:
        return catalogs, events_list

    # Post-process discovery results: ensure provenance and fix ID prefixes
    for entity in discovered:
        # Ensure source_turn is always set (smaller models may omit it)
        if not entity.get("source_turn"):
            entity["source_turn"] = turn_id
        # Fix proposed_id prefix for factions (models may use "fac-" instead of "faction-")
        pid = entity.get("proposed_id", "")
        if entity.get("type") == "faction" and pid and not pid.startswith("faction-"):
            entity["proposed_id"] = "faction-" + pid.lstrip("fac-").lstrip("-")

    # Filter by confidence
    qualified = filter_by_confidence(discovered, min_confidence)
    if not qualified:
        return catalogs, events_list

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
        if entity_data:
            merge_entity(catalogs, entity_data)

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
    entity_ids = [get_entity_id(e) for e in qualified if get_entity_id(e)]

    try:
        event_result = llm.extract_json(
            system_prompt=load_template("event-extractor"),
            user_prompt=format_event_prompt(turn, next_evt_id, entity_ids),
        )
        new_events = event_result.get("events", [])
        if new_events:
            merge_events(events_list, new_events)
    except LLMExtractionError as e:
        print(f"  WARNING: Event extraction failed for {turn_id}: {e}", file=sys.stderr)

    llm.delay()
    return catalogs, events_list


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
            pass

    total = len(turn_dicts)
    entities_before = sum(len(v) for v in catalogs.values())

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
