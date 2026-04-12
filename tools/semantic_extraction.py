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
    mark_dormant_relationships,
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
    """Strip non-allowed attribute keys from char-player entities.

    Handles both V2 (stable_attributes) and V1 (attributes) formats.
    """
    if entity_data.get("id") != "char-player":
        return entity_data
    # V2: stable_attributes
    sa = entity_data.get("stable_attributes", {})
    if sa:
        disallowed = {k for k in sa if k not in PC_ALLOWED_ATTRS}
        if disallowed:
            print(
                f"  WARNING: Dropping non-allowed char-player stable_attributes: {sorted(disallowed)}",
                file=sys.stderr,
            )
            entity_data["stable_attributes"] = {k: v for k, v in sa.items() if k in PC_ALLOWED_ATTRS}
    # V1: attributes (backward compat)
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
    were merged before the filter was in place.  Handles both V2
    (stable_attributes) and V1 (attributes) formats.
    """
    for entity in catalogs.get("characters.json", []):
        if entity.get("id") != "char-player":
            continue
        # V2: stable_attributes
        sa = entity.get("stable_attributes", {})
        if sa:
            disallowed = {k for k in sa if k not in PC_ALLOWED_ATTRS}
            if disallowed:
                print(
                    f"  WARNING: Purging stale char-player catalog stable_attributes: {sorted(disallowed)}",
                    file=sys.stderr,
                )
                entity["stable_attributes"] = {k: v for k, v in sa.items() if k in PC_ALLOWED_ATTRS}
        # V1: attributes
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


def _coerce_entity_fields(entity_data) -> dict | None:
    """Auto-coerce common LLM output quirks before schema validation.

    Fixes:
    - Non-dict entity → return None with warning
    - Single-element arrays → unwrap to string
    - Multi-element arrays → join with ', '
    - Empty arrays → empty string
    - Dict/list/numeric/bool/None values in attributes → stringify
    """
    if not isinstance(entity_data, dict):
        print(f"  WARNING: entity_data is {type(entity_data).__name__}, expected dict — skipping",
              file=sys.stderr)
        return None

    # Top-level string fields that the LLM sometimes wraps in arrays
    string_fields = ["name", "description", "identity", "current_status",
                     "type", "proposed_id", "first_seen_turn", "last_updated_turn"]
    for field in string_fields:
        val = entity_data.get(field)
        if isinstance(val, list):
            if len(val) == 1:
                entity_data[field] = str(val[0])
            elif len(val) > 1:
                entity_data[field] = ", ".join(str(v) for v in val)
            else:
                entity_data[field] = ""
            print(f"  COERCE: {field} array → string: {val!r}", file=sys.stderr)

    # If proposed_id or id contains commas, the LLM crammed multiple IDs into
    # one field.  Pick the one whose prefix matches the declared entity type.
    etype = entity_data.get("type", "")
    for id_field in ("proposed_id", "id"):
        pid = entity_data.get(id_field, "")
        if isinstance(pid, str) and "," in pid:
            from catalog_merger import validate_id_prefix
            parts = [p.strip() for p in pid.split(",") if p.strip()]
            matched = [p for p in parts if etype and validate_id_prefix(p, etype)]
            chosen = matched[0] if matched else parts[0] if parts else pid
            print(f"  COERCE: {id_field} comma-split: {pid!r} → {chosen!r}", file=sys.stderr)
            entity_data[id_field] = chosen

    # Attributes: values must be strings per schema
    attrs = entity_data.get("attributes", {})
    if isinstance(attrs, dict):
        for key, val in list(attrs.items()):
            if isinstance(val, str):
                continue
            if isinstance(val, list):
                attrs[key] = ", ".join(str(v) for v in val)
            elif isinstance(val, dict):
                attrs[key] = json.dumps(val)
            elif val is None:
                attrs[key] = ""
            else:
                attrs[key] = str(val)
            print(f"  COERCE: attributes.{key} {type(val).__name__} → string", file=sys.stderr)

    # Relationships: should be an array of objects, but sometimes a single dict
    rels = entity_data.get("relationships")
    if isinstance(rels, dict):
        entity_data["relationships"] = [rels]
        print("  COERCE: relationships dict → single-element array", file=sys.stderr)

    # --- V1 → V2 coercion ---
    # If LLM returned "description" but not "identity", map description → identity
    if "description" in entity_data and "identity" not in entity_data:
        entity_data["identity"] = entity_data.pop("description")
        if "current_status" not in entity_data:
            entity_data["current_status"] = ""
        print("  COERCE: description → identity (V1→V2 fallback)", file=sys.stderr)

    # If LLM returned flat "attributes" but not "stable_attributes", classify them
    if "attributes" in entity_data and "stable_attributes" not in entity_data:
        attrs = entity_data.pop("attributes")
        if isinstance(attrs, dict) and attrs:
            # Keys that represent volatile state
            volatile_keys = {"condition", "equipment", "location", "hp_change"}
            stable = {}
            volatile = {}
            turn_id = entity_data.get("last_updated_turn", "")
            for key, val in attrs.items():
                if key in volatile_keys:
                    if key == "equipment" and isinstance(val, str):
                        volatile[key] = [v.strip() for v in val.split(",")]
                    else:
                        volatile[key] = val
                else:
                    # Detect [inference] suffix from V1 format
                    inference = False
                    if isinstance(val, str) and val.endswith(" [inference]"):
                        val = val[: -len(" [inference]")]
                        inference = True
                    stable[key] = {
                        "value": val,
                        "inference": inference,
                        "confidence": 0.7 if inference else 1.0,
                        "source_turn": turn_id,
                    }
            if stable:
                entity_data["stable_attributes"] = stable
            if volatile:
                volatile["last_updated_turn"] = turn_id
                entity_data["volatile_state"] = volatile
            print("  COERCE: flat attributes → stable_attributes/volatile_state (V1→V2)", file=sys.stderr)

    # Coerce V1 relationship fields to V2 format
    for rel in entity_data.get("relationships", []):
        if "relationship" in rel and "current_relationship" not in rel:
            rel["current_relationship"] = rel.pop("relationship")
        if "source_turn" in rel and "first_seen_turn" not in rel:
            rel["first_seen_turn"] = rel["source_turn"]
            if "last_updated_turn" not in rel:
                rel["last_updated_turn"] = rel.pop("source_turn")
            else:
                del rel["source_turn"]

    return entity_data


def _filter_concept_prefix_from_items(entity_data: dict) -> bool:
    """Return False (reject) if the entity has a concept- prefix but type=item.

    Concept-prefix entities should not be routed to the items catalog.
    Also rejects any entity whose type is 'concept' from being treated as an
    item.  Returns True if the entity should be kept.
    """
    eid = entity_data.get("id") or entity_data.get("proposed_id") or ""
    etype = entity_data.get("type", "")
    if eid.startswith("concept-") and etype == "item":
        print(f"  FILTER: dropping concept-prefix entity from items: {eid}", file=sys.stderr)
        return False
    return True


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
    "identity": "The player character (referred to as 'you' in DM narration).",
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


def _format_prior_entity_context(current_entry: dict | None) -> str:
    """Format the prior entity state for injection into the detail prompt.

    Extracts identity, current_status, stable_attributes, and volatile_state
    from the existing entity (V2 fields).  Falls back to V1 description and
    attributes when V2 fields are absent.
    """
    if not current_entry:
        return "{}"
    prior: dict = {}
    # V2 fields
    for key in ("identity", "current_status", "status_updated_turn",
                "stable_attributes", "volatile_state"):
        if key in current_entry:
            prior[key] = current_entry[key]
    # V1 fallback: keep description/attributes if no V2 counterparts
    if "identity" not in prior and "description" in current_entry:
        prior["description"] = current_entry["description"]
    if "stable_attributes" not in prior and "attributes" in current_entry:
        prior["attributes"] = current_entry["attributes"]
    # Always include basic metadata
    for key in ("id", "name", "type", "first_seen_turn", "last_updated_turn", "notes"):
        if key in current_entry:
            prior[key] = current_entry[key]
    return json.dumps(prior, indent=2)


def format_detail_prompt(turn: dict, entity_ref: dict, current_entry: dict | None) -> str:
    """Format the user prompt for entity detail extraction."""
    entry_json = json.dumps(current_entry, indent=2) if current_entry else "{}"
    prior_json = _format_prior_entity_context(current_entry)
    return (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Entity to Extract/Update\n"
        f"Entity ID: {entity_ref.get('existing_id') or entity_ref.get('proposed_id')}\n"
        f"Entity Name: {entity_ref['name']}\n"
        f"Entity Type: {entity_ref['type']}\n\n"
        f"## Prior entity state (for reference, update as needed):\n"
        f"```json\n{prior_json}\n```\n\n"
        f"## Current Catalog Entry\n```json\n{entry_json}\n```"
    )


def _collect_existing_relationships(catalogs: dict, entity_ids: list[str]) -> str:
    """Gather existing relationships for the given entities and format as JSON.

    Returns a compact JSON string containing per-entity relationships so the
    relationship-mapper LLM can update rather than duplicate them.
    """
    result: dict[str, list] = {}
    for eid in entity_ids:
        found = find_entity_by_id(catalogs, eid)
        if found:
            _, entity = found
            rels = entity.get("relationships", [])
            if rels:
                result[eid] = rels
    if not result:
        return "(none — no existing relationships)"
    return json.dumps(result, indent=2)


def format_relationship_prompt(turn: dict, mentioned_entities: list,
                               existing_relationships_json: str = "") -> str:
    """Format the user prompt for relationship mapping."""
    entities_text = "\n".join(
        f"- {e['id']}: {e['name']} ({e['type']})"
        for e in mentioned_entities
    )
    prompt = (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Entities Mentioned in This Turn\n{entities_text}"
    )
    if existing_relationships_json:
        prompt += (
            f"\n\n## Existing relationships for these entities:\n"
            f"```json\n{existing_relationships_json}\n```"
        )
    return prompt


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
        if entity_data:
            entity_data = _coerce_entity_fields(entity_data)
        if entity_data and not _filter_concept_prefix_from_items(entity_data):
            continue
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
            if entity_data:
                entity_data = _coerce_entity_fields(entity_data)
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
        # Collect existing relationships for context injection
        entity_id_list = [e["id"] for e in mentioned_entities]
        existing_rels_json = _collect_existing_relationships(catalogs, entity_id_list)
        try:
            rel_result = llm.extract_json(
                system_prompt=load_template("relationship-mapper"),
                user_prompt=format_relationship_prompt(turn, mentioned_entities,
                                                       existing_rels_json),
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
            # V1: attributes.aliases (string)
            aliases_str = entity.get("attributes", {}).get("aliases", "")
            if aliases_str:
                for a in aliases_str.split(","):
                    a = a.strip().lower()
                    if a:
                        names.add(a)
            # V2: stable_attributes.aliases.value (list or string)
            sa_aliases = entity.get("stable_attributes", {}).get("aliases")
            if isinstance(sa_aliases, dict):
                val = sa_aliases.get("value", "")
                if isinstance(val, list):
                    for a in val:
                        a = a.strip().lower()
                        if a:
                            names.add(a)
                elif isinstance(val, str) and val:
                    for a in val.split(","):
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

        # --- Fuzzy pass: substring and token overlap (same catalog only) ---
        STOPWORDS = {"a", "an", "the", "of", "and", "with", "in", "on", "to"}
        all_names = [(idx, entity.get("name", "").strip().lower()) for idx, entity in enumerate(entities)]

        for i, (idx_a, name_a) in enumerate(all_names):
            if not name_a:
                continue
            for idx_b, name_b in all_names[i + 1:]:
                if not name_b:
                    continue
                if find(idx_a) == find(idx_b):
                    continue  # already in same group

                # Tokenize once for Rules 1 and 2
                tokens_a = set(name_a.replace("-", " ").split()) - STOPWORDS
                tokens_b = set(name_b.replace("-", " ").split()) - STOPWORDS
                if tokens_a and tokens_b:
                    # Rule 1: Whole-token subset containment
                    if tokens_a.issubset(tokens_b) or tokens_b.issubset(tokens_a):
                        union(idx_a, idx_b)
                        print(f"  DEDUP (substring): linking '{name_a}' and '{name_b}' as duplicates")
                        continue

                    # Rule 2: Token overlap >= 50%
                    overlap = tokens_a & tokens_b
                    smaller = min(len(tokens_a), len(tokens_b))
                    if len(overlap) / smaller >= 0.5:
                        union(idx_a, idx_b)
                        print(f"  DEDUP (token-overlap): linking '{name_a}' and '{name_b}' as duplicates")
                        continue

                # Rule 3: ID stem overlap (hyphen-segment containment)
                id_a = entities[idx_a].get("proposed_id", entities[idx_a].get("id", ""))
                id_b = entities[idx_b].get("proposed_id", entities[idx_b].get("id", ""))
                stem_a = id_a.split("-", 1)[1] if "-" in id_a else id_a
                stem_b = id_b.split("-", 1)[1] if "-" in id_b else id_b
                if stem_a and stem_b:
                    parts_a = set(stem_a.split("-"))
                    parts_b = set(stem_b.split("-"))
                    if parts_a.issubset(parts_b) or parts_b.issubset(parts_a):
                        union(idx_a, idx_b)
                        print(f"  DEDUP (id-stem): linking '{name_a}' ({id_a}) and '{name_b}' ({id_b}) as duplicates")

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
    overrides: dict | None = None,
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
        overrides: Optional runtime overrides for LLM client configuration.
            Supported keys include provider settings such as ``model`` and
            ``base_url``. Any keys supplied here take precedence over values
            loaded from ``config_path``; settings not provided in ``overrides``
            continue to use the configuration file values.
    """
    try:
        llm = LLMClient(config_path, overrides=overrides)
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
        # Post-merge dormancy pass
        last_turn = turn_dicts[-1]["turn_id"] if turn_dicts else ""
        dormant_count = mark_dormant_relationships(catalogs, last_turn)
        if dormant_count:
            print(f"  Marked {dormant_count} relationship(s) as dormant")

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
    overrides: dict | None = None,
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
        overrides: Optional dictionary of config key/value overrides passed to
            ``LLMClient`` to override settings loaded from ``config_path``.
    """
    try:
        llm = LLMClient(config_path, overrides=overrides)
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

    # Post-merge dormancy pass
    dormant_count = mark_dormant_relationships(catalogs, turn_id)
    if dormant_count:
        print(f"  Marked {dormant_count} relationship(s) as dormant")

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
