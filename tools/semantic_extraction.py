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
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

from catalog_merger import (
    load_catalogs,
    load_events,
    save_catalogs,
    save_events,
    format_known_entities,
    format_known_entities_bounded,
    find_entity_by_id,
    merge_entity,
    merge_relationships,
    merge_events,
    get_next_event_id,
    mark_dormant_relationships,
    normalize_entity_id,
    cleanup_dangling_relationships,
    TYPE_TO_CATALOG,
    _infer_type_from_prefix,
    _strip_any_prefix,
    _levenshtein,
    CATALOG_KEYS,
)
from llm_client import LLMClient, LLMExtractionError, QuotaExhaustedError

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

# Consecutive PC extraction failure tracking (#133)
_pc_consecutive_failures = 0  # noqa — used via `global` in extract_and_merge / _reset_pc_failure_tracking
_pc_skipped_turns = 0
_pc_turns_since_cooldown = 0  # Tracks turns since cooldown started (increments every turn, skip or attempt)
_PC_FAILURE_WARN_THRESHOLD = 10
_PC_SKIP_COOLDOWN = 50       # Skip PC extraction for this many turns after threshold failures
_PC_RETRY_WINDOW = 5         # Retry for this many turns before entering cooldown again
_PC_SKIP_THRESHOLD = 20      # Enter cooldown after this many consecutive failures (#149)
_PC_DEBUG_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "framework-local",
    "pc-extraction-debug.jsonl",
)

# Schema-compliant turn ID pattern (matches e.g. turn-001, turn-1234)
_TURN_ID_RE = re.compile(r"^turn-[0-9]{3,}$")


def _reset_pc_failure_tracking() -> None:
    """Reset per-run char-player extraction failure state.

    This counter is module-level state, so top-level extraction entry points
    must call this at the start of each new batch/single run to avoid
    carrying failure counts across unrelated invocations in long-lived
    processes.

    After ``_PC_SKIP_THRESHOLD`` consecutive failures, PC extraction enters
    a cooldown cycle: it skips ``_PC_SKIP_COOLDOWN`` turns then retries for
    ``_PC_RETRY_WINDOW`` turns, repeating until a success resets the counter.
    """
    global _pc_consecutive_failures, _pc_skipped_turns, _pc_turns_since_cooldown
    _pc_consecutive_failures = 0
    _pc_skipped_turns = 0
    _pc_turns_since_cooldown = 0


def _should_skip_pc(consecutive_failures: int, turns_since_cooldown: int) -> bool:
    """Return True if PC extraction should be skipped this turn.

    Uses the cooldown model: after ``_PC_SKIP_THRESHOLD`` consecutive
    failures, skip ``_PC_SKIP_COOLDOWN`` turns then allow attempts for
    ``_PC_RETRY_WINDOW`` turns, repeating.  ``turns_since_cooldown``
    is a separate counter that advances every turn (skip or attempt)
    once the threshold is reached.
    """
    if consecutive_failures < _PC_SKIP_THRESHOLD:
        return False
    cycle_position = turns_since_cooldown % (_PC_SKIP_COOLDOWN + _PC_RETRY_WINDOW)
    return cycle_position < _PC_SKIP_COOLDOWN


def _write_pc_debug_record(
    turn_id: str,
    failure_reason: str,
    response_keys: list | str,
    partial_data_preview: str,
    merge_result: bool | None = None,
) -> None:
    """Write a debug record for a PC extraction failure to a JSONL file (#199)."""
    import json as _json

    record = {
        "turn_id": turn_id,
        "failure_reason": failure_reason,
        "response_keys": response_keys,
        "partial_entity_data_preview": partial_data_preview,
        "merge_result": merge_result,
    }
    try:
        debug_path = os.path.abspath(_PC_DEBUG_LOG)
        os.makedirs(os.path.dirname(debug_path), exist_ok=True)
        with open(debug_path, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(record, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        # Debug logging must never interrupt extraction.
        return


def _filter_pc_attributes(entity_data: dict) -> dict:
    """Strip non-allowed attribute keys from char-player entities."""
    if entity_data.get("id") != "char-player":
        return entity_data
    sa = entity_data.get("stable_attributes", {})
    if sa:
        disallowed = {k for k in sa if k not in PC_ALLOWED_ATTRS}
        if disallowed:
            print(
                f"  WARNING: Dropping non-allowed char-player stable_attributes: {sorted(disallowed)}",
                file=sys.stderr,
            )
            entity_data["stable_attributes"] = {k: v for k, v in sa.items() if k in PC_ALLOWED_ATTRS}
    return entity_data


def _sanitize_pc_catalog_entry(catalogs: dict) -> None:
    """Purge non-allowed attribute keys and invalid aliases from char-player.

    Ensures historical action-sprawl attributes are cleaned up even if they
    were merged before the filter was in place.  Also prunes volatile_state
    keys and validates aliases (#214).
    """
    for entity in catalogs.get("characters.json", []):
        if entity.get("id") != "char-player":
            continue
        sa = entity.get("stable_attributes", {})
        if sa:
            disallowed = {k for k in sa if k not in PC_ALLOWED_ATTRS}
            if disallowed:
                print(
                    f"  WARNING: Purging stale char-player catalog stable_attributes: {sorted(disallowed)}",
                    file=sys.stderr,
                )
                entity["stable_attributes"] = {k: v for k, v in sa.items() if k in PC_ALLOWED_ATTRS}
            # Filter aliases (#214)
            aliases_attr = sa.get("aliases")
            if isinstance(aliases_attr, dict):
                aliases_attr["value"] = _filter_pc_aliases(aliases_attr.get("value", []))
        # Prune volatile_state (#214)
        _prune_pc_volatile_state(entity)
        break


def _filter_pc_aliases(aliases: list[str] | str) -> list[str]:
    """Remove invalid PC aliases: blocklisted words, too-short, common words (#214).

    Accepts either a list of aliases or a comma-separated string and returns
    a consistently filtered list (capped at _PC_ALIAS_MAX_COUNT).
    """
    if isinstance(aliases, str):
        aliases = [part.strip() for part in aliases.split(",") if part.strip()]
    elif not isinstance(aliases, list):
        aliases = []
    cleaned = []
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip():
            continue
        alias = alias.strip()
        low = alias.lower()
        if low in _PC_ALIAS_BLOCKLIST:
            print(f"  COERCE: rejected PC alias '{alias}' (meta-label)", file=sys.stderr)
            continue
        if low in _PC_ALIAS_WORD_BLOCKLIST:
            print(f"  COERCE: rejected PC alias '{alias}' (common word)", file=sys.stderr)
            continue
        if len(alias) < _PC_ALIAS_MIN_LENGTH:
            print(f"  COERCE: rejected PC alias '{alias}' (too short)", file=sys.stderr)
            continue
        cleaned.append(alias)
    if len(cleaned) > _PC_ALIAS_MAX_COUNT:
        cleaned = cleaned[-_PC_ALIAS_MAX_COUNT:]
    return cleaned


def _prune_pc_volatile_state(pc_entity: dict) -> None:
    """Prune PC volatile_state to max keys, keeping core keys and most recent (#214)."""
    vs = pc_entity.get("volatile_state")
    if not isinstance(vs, dict) or len(vs) <= _PC_VOLATILE_STATE_MAX_KEYS:
        return

    core = {k: v for k, v in vs.items() if k in _PC_VOLATILE_STATE_CORE_KEYS}
    non_core = {k: v for k, v in vs.items() if k not in _PC_VOLATILE_STATE_CORE_KEYS}

    budget = _PC_VOLATILE_STATE_MAX_KEYS - len(core)
    if len(non_core) > budget:
        # Keep only the last `budget` keys (insertion order ≈ recency in Python 3.7+)
        keep_keys = list(non_core.keys())[-budget:]
        keep_set = set(keep_keys)
        dropped = [k for k in non_core if k not in keep_set]
        non_core = {k: non_core[k] for k in keep_keys}
        print(
            f"  PRUNE: Removed {len(dropped)} stale volatile_state keys from PC "
            f"(kept {len(core) + len(non_core)})",
            file=sys.stderr,
        )

    pc_entity["volatile_state"] = {**core, **non_core}


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

    # Repair empty first_seen_turn / last_updated_turn (#241)
    # An empty string passes `.get()` fallback checks but fails schema
    # validation (pattern: ^turn-[0-9]{3,}$).  Remove the key so downstream
    # code falls through to its default turn-ID logic.
    for turn_field in ("first_seen_turn", "last_updated_turn"):
        val = entity_data.get(turn_field)
        if isinstance(val, str) and not _TURN_ID_RE.match(val):
            entity_data.pop(turn_field)
            if val:
                print(f"  COERCE: removed invalid {turn_field}: {val!r}", file=sys.stderr)

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

    # --- LLM output normalization ---
    # Strip "_new" suffix from non-schema keys (#172) BEFORE field mapping
    # so that e.g. "description_new" becomes "description" and is then
    # handled by the description → identity mapping below.
    _schema_keys = {
        "id", "name", "type", "identity", "current_status",
        "status_updated_turn", "stable_attributes", "volatile_state",
        "relationships", "first_seen_turn", "last_updated_turn", "notes",
    }
    for key in list(entity_data.keys()):
        if key.endswith("_new") and key not in _schema_keys:
            base = key[:-4]  # strip "_new"
            if base not in entity_data:
                entity_data[base] = entity_data.pop(key)
                print(f"  COERCE: {key} → {base} (stripped _new suffix)", file=sys.stderr)
            else:
                # base key already present — discard the delta variant
                entity_data.pop(key)
                print(f"  COERCE: discarded delta key '{key}' (base '{base}' exists)", file=sys.stderr)

    # If LLM returned "description" but not "identity", map description → identity.
    # Always strip the non-schema "description" field afterward so the entity
    # validates against the V2 schema (additionalProperties: false).
    if "description" in entity_data:
        if "identity" not in entity_data:
            entity_data["identity"] = entity_data["description"]
            if "current_status" not in entity_data:
                entity_data["current_status"] = ""
            print("  COERCE: description → identity (LLM field normalization)", file=sys.stderr)
        entity_data.pop("description", None)

    # If LLM returned flat "attributes" but not "stable_attributes", classify them.
    # Always strip the non-schema "attributes" field afterward so the entity
    # validates against the V2 schema.
    if "attributes" in entity_data:
        attrs = entity_data.pop("attributes")
        if "stable_attributes" not in entity_data and isinstance(attrs, dict) and attrs:
            # Keys that represent volatile state
            volatile_keys = {"condition", "equipment", "location", "hp_change"}
            stable = {}
            volatile = {}
            turn_id = entity_data.get("last_updated_turn", "")
            # Only include source_turn / last_updated_turn when a valid
            # turn ID is available (schema requires pattern ^turn-[0-9]{3,}$).
            has_valid_turn = bool(turn_id and _TURN_ID_RE.match(turn_id))
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
                    entry = {
                        "value": val,
                        "inference": inference,
                        "confidence": 0.7 if inference else 1.0,
                    }
                    if has_valid_turn:
                        entry["source_turn"] = turn_id
                    stable[key] = entry
            if stable:
                entity_data["stable_attributes"] = stable
            if volatile:
                if has_valid_turn:
                    volatile["last_updated_turn"] = turn_id
                entity_data["volatile_state"] = volatile
            print("  COERCE: flat attributes → stable_attributes/volatile_state (LLM normalization)", file=sys.stderr)

    # --- Remap non-standard top-level keys into V2 slots (#170, #172) ---
    # The LLM often returns useful data under keys that don't exist in the
    # V2 entity schema, causing additionalProperties validation failures.
    # Remap them into the correct V2 locations before validation.
    turn_id = entity_data.get("last_updated_turn", "")
    has_valid_turn = bool(turn_id and _TURN_ID_RE.match(turn_id))

    # Keys that should become volatile_state entries
    _volatile_remap = {
        "equipment": "equipment",
        "inventory": "equipment",
        "equipment_and_tools": "equipment",
        "item_equipment": "equipment",
        "item_inventory": "equipment",
        "location": "location",
        "current_location": "location",
        "locations": "location",
        "status": "condition",
        "current_situation": "condition",
        "emotional_state": "condition",
        "physical_state": "condition",
        "health_status": "condition",
        "status_effects": "condition",
    }
    for src_key, vs_key in _volatile_remap.items():
        if src_key in entity_data:
            val = entity_data.pop(src_key)
            if "volatile_state" not in entity_data:
                entity_data["volatile_state"] = {}
            vs = entity_data["volatile_state"]
            # equipment/inventory → array of strings
            if vs_key == "equipment":
                if isinstance(val, str):
                    val = [v.strip() for v in val.split(",") if v.strip()]
                elif isinstance(val, list):
                    val = [str(v) for v in val]
                else:
                    val = [str(val)]
            elif not isinstance(val, str):
                val = str(val) if val is not None else ""
            # Only set if target slot is empty (don't overwrite existing data)
            if vs_key not in vs:
                vs[vs_key] = val
            print(f"  COERCE: {src_key} → volatile_state.{vs_key}", file=sys.stderr)

    # Keys that should become stable_attributes entries
    _stable_remap = {
        "abilities": "abilities",
        "skills_and_abilities": "abilities",
        "name_aliases": "aliases",
        "alignment": "alignment",
        "weaknesses": "weaknesses",
        "age": "age",
        "gender": "gender",
        "occupation": "occupation",
    }
    for src_key, sa_key in _stable_remap.items():
        if src_key in entity_data:
            val = entity_data.pop(src_key)
            if "stable_attributes" not in entity_data:
                entity_data["stable_attributes"] = {}
            sa = entity_data["stable_attributes"]
            if sa_key not in sa and val:
                # Wrap in V2 attribute object format
                if isinstance(val, list):
                    attr_val = [str(v) for v in val]
                elif isinstance(val, str):
                    attr_val = val
                else:
                    attr_val = str(val)
                entry: dict = {
                    "value": attr_val,
                    "inference": False,
                    "confidence": 1.0,
                }
                if has_valid_turn:
                    entry["source_turn"] = turn_id
                sa[sa_key] = entry
            print(f"  COERCE: {src_key} → stable_attributes.{sa_key}", file=sys.stderr)

    # abilities_and_traits → stable_attributes.abilities (Run 11 variant)
    if "abilities_and_traits" in entity_data:
        val = entity_data.pop("abilities_and_traits")
        if "stable_attributes" not in entity_data:
            entity_data["stable_attributes"] = {}
        sa = entity_data["stable_attributes"]
        if "abilities" not in sa and val:
            if isinstance(val, list):
                attr_val = [str(v) for v in val]
            elif isinstance(val, str):
                attr_val = val
            else:
                attr_val = str(val)
            entry: dict = {"value": attr_val, "inference": False, "confidence": 1.0}
            if has_valid_turn:
                entry["source_turn"] = turn_id
            sa["abilities"] = entry
        print("  COERCE: abilities_and_traits → stable_attributes.abilities", file=sys.stderr)

    # additional_items_equipped → volatile_state.equipment (append, don't overwrite)
    if "additional_items_equipped" in entity_data:
        val = entity_data.pop("additional_items_equipped")
        if isinstance(val, str):
            val = [v.strip() for v in val.split(",") if v.strip()]
        elif isinstance(val, list):
            val = [str(v) for v in val]
        else:
            val = [str(val)]
        if "volatile_state" not in entity_data:
            entity_data["volatile_state"] = {}
        vs = entity_data["volatile_state"]
        existing = vs.get("equipment", [])
        if isinstance(existing, list):
            vs["equipment"] = existing + [v for v in val if v not in existing]
        else:
            vs["equipment"] = val
        print("  COERCE: additional_items_equipped → volatile_state.equipment (appended)", file=sys.stderr)

    # Relationship key variants → relationships
    _rel_keys = ["relations", "character_relations", "faction_relations",
                 "item_relations", "items_relations", "social_connections",
                 "current_relationships"]
    for rk in _rel_keys:
        if rk in entity_data:
            val = entity_data.pop(rk)
            # Only adopt if no relationships already present
            if "relationships" not in entity_data:
                if isinstance(val, list):
                    entity_data["relationships"] = val
                elif isinstance(val, dict):
                    entity_data["relationships"] = [val]
            print(f"  COERCE: {rk} → relationships", file=sys.stderr)

    # Discard known noise keys that carry no recoverable structured data
    _discard_keys = {
        "current_activity", "actions", "last_activity", "recent_events",
        "future_plans", "image_url", "quests", "history", "emotions",
        "tools_used", "background_story", "personality_traits", "traits",
        "faction", "confidence",
        # Added in #172: ephemeral / history keys observed in Run 10a
        "events", "activities", "activity_history",
        "abilities_used_in_last_turn", "description_of_activity",
        "description_of_recent_activity", "recent_emotional_states",
        "recent_relationship_changes", "name_changes",
        "equipment_history", "faction_relations_history",
        "relationships_history",
        # Added in #196: ephemeral / history keys observed in Run 11
        "recent_activities", "current_activities", "updated_turn",
        "history_highlights", "goals", "background", "abilities_description",
    }
    for dk in list(entity_data.keys()):
        if dk in _discard_keys:
            entity_data.pop(dk)
            print(f"  COERCE: discarded non-schema key '{dk}'", file=sys.stderr)

    # Strip status_updated_turn from volatile_state if present — it belongs at top level only.
    # If the LLM nested it under volatile_state and top-level is missing, promote it first.
    vs = entity_data.get("volatile_state")
    if isinstance(vs, dict) and "status_updated_turn" in vs:
        nested_status_updated_turn = vs.pop("status_updated_turn")
        if "status_updated_turn" not in entity_data:
            entity_data["status_updated_turn"] = nested_status_updated_turn
            print("  COERCE: promoted volatile_state.status_updated_turn → top-level status_updated_turn", file=sys.stderr)
        else:
            print("  COERCE: stripped status_updated_turn from volatile_state (top-level only)", file=sys.stderr)

    # Strip stable_attributes entries where the LLM returned null for value (#178).
    # The schema requires value to be string or string[] — null is not valid.
    sa = entity_data.get("stable_attributes")
    if isinstance(sa, dict):
        null_keys = [k for k, v in sa.items()
                     if isinstance(v, dict) and v.get("value") is None]
        for k in null_keys:
            del sa[k]
            print(f"  COERCE: stable_attributes.{k}.value was null — removed", file=sys.stderr)

    # Coerce non-object stable_attributes values to proper attribute objects (#219)
    sa = entity_data.get("stable_attributes")
    if isinstance(sa, dict):
        for k, v in list(sa.items()):
            if v is None:
                continue  # handled above
            if not isinstance(v, dict):
                # Wrap raw value (string, list, int, etc.) in attribute object
                if isinstance(v, str):
                    wrapped_value = v
                elif isinstance(v, list):
                    wrapped_value = [str(item) for item in v]
                else:
                    wrapped_value = str(v)
                wrapped = {
                    "value": wrapped_value,
                    "inference": True,
                    "confidence": 0.5,
                }
                if has_valid_turn:
                    wrapped["source_turn"] = turn_id
                sa[k] = wrapped
                print(f"  COERCE: stable_attributes.{k} was {type(v).__name__} — wrapped to attribute object", file=sys.stderr)

    # Ensure volatile_state has last_updated_turn if we populated it
    vs = entity_data.get("volatile_state")
    if isinstance(vs, dict) and "last_updated_turn" not in vs and has_valid_turn:
        vs["last_updated_turn"] = turn_id

    # Coerce LLM relationship fields to V2 format
    for rel in entity_data.get("relationships", []):
        if "relationship" in rel and "current_relationship" not in rel:
            rel["current_relationship"] = rel.pop("relationship")
        # Always remove source_turn (not in V2 schema which has
        # additionalProperties: false on relationships), mapping it
        # into first_seen_turn / last_updated_turn as needed.
        if "source_turn" in rel:
            source_turn = rel.pop("source_turn")
            if "first_seen_turn" not in rel:
                rel["first_seen_turn"] = source_turn
            if "last_updated_turn" not in rel:
                rel["last_updated_turn"] = source_turn

    # Coerce relationship type values to schema enum (#218)
    _REL_TYPE_MAP = {
        "knowledge exchange": "mentorship",
        "knowledge sharing": "mentorship",
        "teaching": "mentorship",
        "learning": "mentorship",
        "trade": "social",
        "trading": "social",
        "alliance": "political",
        "rivalry": "adversarial",
        "enemy": "adversarial",
        "friend": "social",
        "friendship": "social",
        "family": "kinship",
        "parent": "kinship",
        "child": "kinship",
        "sibling": "kinship",
        "spouse": "romantic",
        "lover": "romantic",
        "companion": "social",
        "follower": "factional",
        "leader": "political",
        "subordinate": "factional",
        "protector": "social",
        "caretaker": "social",
    }
    _VALID_REL_TYPES = {"kinship", "partnership", "mentorship", "political", "factional", "social", "adversarial", "romantic", "other"}
    for rel in entity_data.get("relationships", []):
        rt = rel.get("type", "")
        if isinstance(rt, str):
            normalized_rt = rt.strip().lower()
            if normalized_rt in _VALID_REL_TYPES:
                mapped = normalized_rt
            elif normalized_rt:
                mapped = _REL_TYPE_MAP.get(normalized_rt, "other")
            else:
                mapped = "other"
        else:
            mapped = "other"

        if rel.get("type") != mapped:
            rel["type"] = mapped
            if mapped == "other":
                print(f"  COERCE: relationship type '{rt}' → 'other' (unmapped)", file=sys.stderr)
            else:
                print(f"  COERCE: relationship type '{rt}' → '{mapped}'", file=sys.stderr)

    return entity_data


def _filter_concept_prefix_from_items(entity_data: dict) -> bool:
    """Return False (reject) if the entity has a concept- prefix but type=item.

    Concept-prefix entities should not be routed to the items catalog.
    Returns True if the entity should be kept.
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


# Valid event types from event.schema.json / event-extractor template
_VALID_EVENT_TYPES = {
    "birth", "death", "arrival", "departure", "construction", "decision",
    "encounter", "recruitment", "discovery", "anomaly", "capture", "trap",
    "ritual", "healing", "communication", "examination", "release",
    "offering", "other",
}

# Map of invalid type values → nearest valid type
_EVENT_TYPE_REMAP: dict[str, str] = {
    "acquisition": "discovery",
    "trade": "offering",
    "purchase": "offering",
    "sale": "offering",
    "injury": "other",
    "conflict": "encounter",
    "combat": "encounter",
    "negotiation": "decision",
    "exploration": "discovery",
    "investigation": "examination",
    "rest": "other",
    "travel": "arrival",
    "reward": "other",
    "quest": "other",
    "interaction": "encounter",
    "observation": "examination",
    "transformation": "other",
}


def _coerce_event_fields(event: dict) -> dict | None:
    """Coerce invalid event field values to schema-valid equivalents.

    Currently handles: type remapping for invalid enum values.
    If type cannot be remapped to a valid value, falls back to 'other'.
    Returns None for non-dict events (caller should skip them).
    """
    if not isinstance(event, dict):
        print(
            f"  COERCE: non-dict event {event!r} → skipped",
            file=sys.stderr,
        )
        return None

    event_type = event.get("type")
    if event_type is None:
        return event

    if not isinstance(event_type, str):
        print(
            f"  COERCE: non-string event type {event_type!r} → 'other'",
            file=sys.stderr,
        )
        event["type"] = "other"
        return event

    normalized_type = event_type.strip().lower()
    if normalized_type in _VALID_EVENT_TYPES:
        if normalized_type != event_type:
            event["type"] = normalized_type
        return event

    remapped = _EVENT_TYPE_REMAP.get(normalized_type, "other")
    print(
        f"  COERCE: event type '{event_type}' → '{remapped}'",
        file=sys.stderr,
    )
    event["type"] = remapped
    return event


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


# Maximum turn distance for event source_turns to be considered valid (#127)
_MAX_SOURCE_TURN_DISTANCE = 5


def _fix_event_source_turns(events: list[dict], current_turn_id: str) -> None:
    """Validate and correct event source_turns that don't match the current turn (#127).

    If an event's source_turns entries are more than _MAX_SOURCE_TURN_DISTANCE
    away from the current turn, replace them with the current turn ID.
    """
    current_num = _parse_turn_number(current_turn_id)
    if current_num is None:
        return

    for event in events:
        source_turns = event.get("source_turns")
        if not isinstance(source_turns, list) or not source_turns:
            continue
        corrected = False
        for i, st in enumerate(source_turns):
            st_num = _parse_turn_number(st)
            if st_num is None:
                continue
            if abs(current_num - st_num) > _MAX_SOURCE_TURN_DISTANCE:
                source_turns[i] = current_turn_id
                corrected = True
        if corrected:
            # Deduplicate after correction
            event["source_turns"] = list(dict.fromkeys(source_turns))
            print(
                f"  WARNING: Corrected event {event.get('id', '?')} source_turns "
                f"to [{current_turn_id}] (was mismatched by >{_MAX_SOURCE_TURN_DISTANCE} turns)",
                file=sys.stderr,
            )


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


# Stable attribute keys considered when trimming PC prior context.
#
# Why these are included:
# - species/race/class: high-signal identity anchors that improve continuity.
# - aliases: needed so newly revealed names can merge back into char-player.
#
# Why many keys are excluded here (for example: level, background, deity,
# equipment, hp_change, condition, quest, allegiance, status): they are either
# volatile, frequently rewritten by the model, or expensive in token budget.
# The detail prompt must only promise keys from this set to avoid fabricated
# "carried-forward" attributes.
_PC_KEY_STABLE_ATTRS = {"species", "race", "class", "aliases"}

# Maximum number of volatile_state snapshots to include for PC
_PC_MAX_VOLATILE_SNAPSHOTS = 3

# Number of turns beyond which volatile state entries are digested (#121)
_DIGEST_WINDOW = 50


def _parse_turn_number(turn_id: str | None) -> int | None:
    """Parse turn number from a turn ID string like 'turn-042'."""
    if not turn_id or not isinstance(turn_id, str):
        return None
    m = re.search(r'turn-(\d+)', turn_id)
    if m:
        return int(m.group(1))
    return None


def _extract_turn_number(item) -> int | None:
    """Try to extract a turn number from a volatile state entry."""
    if isinstance(item, dict):
        turn = item.get("turn") or item.get("source_turn") or item.get("turn_id")
        if turn:
            return _parse_turn_number(str(turn)) if not isinstance(turn, int) else turn
    elif isinstance(item, str):
        m = re.search(r'turn-(\d+)', item)
        if m:
            return int(m.group(1))
    return None


def _extract_themes(items: list) -> list[str]:
    """Extract key themes from a list of volatile state entries for digest."""
    _THEME_KEYWORDS = [
        "pregnancy", "birth", "construction", "harvest",
        "defense", "ritual", "expedition", "council",
        "illness", "healing", "teaching", "craft",
    ]
    themes = set()
    for item in items:
        text = str(item) if not isinstance(item, str) else item
        text_lower = text[:200].lower()
        for keyword in _THEME_KEYWORDS:
            if keyword in text_lower:
                themes.add(keyword)
            if len(themes) >= 5:
                break
        if len(themes) >= 5:
            break
    if not themes:
        themes.add(f"{len(items)} observations")
    return sorted(themes)


def _build_volatile_digest(volatile_state: dict, current_turn_num: int) -> dict:
    """Compress old volatile state entries into a rolling summary (#121).

    Entries older than ``_DIGEST_WINDOW`` turns are replaced with a count +
    theme summary.  Recent entries are kept verbatim.
    """
    if not volatile_state:
        return volatile_state

    result = {}
    for key, value in volatile_state.items():
        if not isinstance(value, list):
            result[key] = value
            continue

        recent_items = []
        old_items = []
        for item in value:
            turn_num = _extract_turn_number(item)
            if turn_num is not None and current_turn_num - turn_num > _DIGEST_WINDOW:
                old_items.append(item)
            else:
                recent_items.append(item)

        if old_items:
            cutoff = current_turn_num - _DIGEST_WINDOW
            themes = _extract_themes(old_items)
            summary = (
                f"[{len(old_items)} earlier entries through ~turn-{cutoff}"
                f", including: {', '.join(themes[:5])}]"
            )
            result[key] = [summary] + recent_items
        else:
            result[key] = recent_items

    return result


def _compact_relationships_with_arcs(
    relationships: list, arcs_data: dict
) -> list:
    """Replace raw relationship histories with arc summaries (#120).

    When ``arcs_data`` contains arc summaries for a relationship target,
    the raw history is replaced with a compact representation.  Otherwise
    the history is trimmed to the last 3 entries.
    """
    compact_rels = []
    arcs_map = arcs_data.get("arcs", {})
    for rel in relationships:
        target_id = rel.get("target_id", "")
        arc_info = arcs_map.get(target_id)
        if arc_info and arc_info.get("arc_summary"):
            compact_rel = {
                "target_id": target_id,
                "type": rel.get("type", ""),
                "status": rel.get("status", "active"),
                "arc_phases": len(arc_info["arc_summary"]),
                "current": arc_info.get("current_relationship", ""),
                "summary": " → ".join(
                    p.get("phase", "") for p in arc_info["arc_summary"]
                ),
            }
            compact_rels.append(compact_rel)
        else:
            trimmed = dict(rel)
            if "history" in trimmed and isinstance(trimmed["history"], list):
                trimmed["history"] = trimmed["history"][-3:]
            compact_rels.append(trimmed)
    return compact_rels


def _format_prior_entity_context(
    current_entry: dict | None,
    arcs_data: dict | None = None,
) -> str:
    """Format the prior entity state for injection into the detail prompt.

    Extracts identity, current_status, stable_attributes, and volatile_state
    from the existing entity.

    For ``char-player``, trims the context to keep prompt size manageable:
    - Always includes identity and current_status
    - Only key stable_attributes (species, race, class, aliases)
    - Last 3 volatile_state snapshots only
    - Relationship histories replaced with arc summaries when available (#120)
    - Old volatile state entries digested into count + themes (#121)
    """
    if not current_entry:
        return "{}"

    is_pc = current_entry.get("id") == "char-player"

    prior: dict = {}
    # V2 fields
    if "identity" in current_entry:
        prior["identity"] = current_entry["identity"]
    if "current_status" in current_entry:
        prior["current_status"] = current_entry["current_status"]
    if "status_updated_turn" in current_entry:
        prior["status_updated_turn"] = current_entry["status_updated_turn"]

    # stable_attributes — trimmed for PC
    sa = current_entry.get("stable_attributes")
    if sa:
        if is_pc:
            trimmed_sa = {k: v for k, v in sa.items() if k in _PC_KEY_STABLE_ATTRS}
            if trimmed_sa:
                prior["stable_attributes"] = trimmed_sa
        else:
            prior["stable_attributes"] = sa

    # volatile_state — digest first, then cap recent entries for PC (#121)
    vs = current_entry.get("volatile_state")
    if vs:
        if is_pc and isinstance(vs, dict):
            current_turn_num = _parse_turn_number(
                current_entry.get("last_updated_turn", "")
            )
            # Digest old entries BEFORE trimming so history is compressed
            # rather than silently dropped.
            if current_turn_num:
                digested_vs = _build_volatile_digest(vs, current_turn_num)
            else:
                digested_vs = dict(vs)
            # Cap recent list-valued entries to keep prompt size bounded
            trimmed_vs = {}
            for k, v in digested_vs.items():
                if isinstance(v, list) and len(v) > _PC_MAX_VOLATILE_SNAPSHOTS:
                    # If the digest produced a summary at index 0 (a string),
                    # preserve it and cap the rest to last N entries.
                    if v and isinstance(v[0], str) and v[0].startswith("["):
                        trimmed_vs[k] = v[:1] + v[-(  _PC_MAX_VOLATILE_SNAPSHOTS):]
                    else:
                        trimmed_vs[k] = v[-_PC_MAX_VOLATILE_SNAPSHOTS:]
                else:
                    trimmed_vs[k] = v
            prior["volatile_state"] = trimmed_vs
        else:
            prior["volatile_state"] = vs

    # Relationships — compact with arc summaries for PC (#120)
    rels = current_entry.get("relationships")
    if rels and is_pc and arcs_data:
        prior["relationships"] = _compact_relationships_with_arcs(rels, arcs_data)
    elif rels and is_pc:
        # No arc data — trim history to last 3 entries
        compact_rels = []
        for rel in rels:
            trimmed = dict(rel)
            if "history" in trimmed and isinstance(trimmed["history"], list):
                trimmed["history"] = trimmed["history"][-3:]
            compact_rels.append(trimmed)
        prior["relationships"] = compact_rels

    # Always include basic metadata
    for key in ("id", "name", "type", "first_seen_turn", "last_updated_turn", "notes"):
        if key in current_entry:
            prior[key] = current_entry[key]
    return json.dumps(prior, indent=2)


def _unwrap_entity_response(detail_result: dict) -> dict | None:
    """Extract entity data from LLM response, handling missing envelope.

    The entity-detail template asks the LLM to return ``{"entity": {...}}``,
    but smaller models sometimes return the entity object flat at the top
    level.  This function accepts both formats.
    """
    # Standard envelope format
    entity_data = detail_result.get("entity")
    if isinstance(entity_data, dict):
        return entity_data
    # Flat format — LLM returned the entity directly
    if "id" in detail_result and "type" in detail_result:
        return dict(detail_result)  # shallow copy to avoid mutating the raw response
    return None


def format_detail_prompt(
    turn: dict,
    entity_ref: dict,
    current_entry: dict | None,
    arcs_data: dict | None = None,
) -> str:
    """Format the user prompt for entity detail extraction."""
    prior_json = _format_prior_entity_context(current_entry, arcs_data=arcs_data)
    entity_id = entity_ref.get('existing_id') or entity_ref.get('proposed_id')
    is_pc = (entity_id == "char-player")
    prompt = (
        f"## Current Turn\n"
        f"Turn ID: {turn['turn_id']}\n"
        f"Speaker: {turn['speaker']}\n"
        f"Text:\n{turn['text']}\n\n"
        f"## Entity to Extract/Update\n"
        f"Entity ID: {entity_id}\n"
        f"Entity Name: {entity_ref['name']}\n"
        f"Entity Type: {entity_ref['type']}\n\n"
        f"## Prior entity state (for reference, update as needed):\n"
        f"```json\n{prior_json}\n```"
    )
    # For PC, skip the full catalog entry to avoid context bloat (#119).
    # The trimmed prior_json already contains all essential entity context.
    if not is_pc:
        entry_json = json.dumps(current_entry, indent=2) if current_entry else "{}"
        prompt += f"\n\n## Current Catalog Entry\n```json\n{entry_json}\n```"
    return prompt


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
        return ""
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


def _pc_partial_merge(catalogs: dict, entity_data: dict, turn_id: str) -> bool:
    """Attempt to merge valid individual fields from a near-valid PC extraction.

    When full schema validation fails for char-player, this function extracts
    whatever valid fields are present and merges them into the existing
    catalog entry rather than losing the entire response (#107).

    Returns True if at least one field was successfully merged.
    """
    pc_result = find_entity_by_id(catalogs, "char-player")
    if not pc_result:
        return False
    _, pc_entry = pc_result

    attempted_fields = []
    merged_fields = []

    # Merge current_status if present and non-empty
    if entity_data.get("current_status"):
        attempted_fields.append("current_status")
        if isinstance(entity_data["current_status"], str):
            pc_entry["current_status"] = entity_data["current_status"]
            merged_fields.append("current_status")
        else:
            print(
                f"  PC partial merge: current_status skipped (not a string) at {turn_id}",
                file=sys.stderr,
            )

    # Merge volatile_state if present
    vs = entity_data.get("volatile_state")
    if vs is not None:
        attempted_fields.append("volatile_state")
        if isinstance(vs, dict) and vs:
            if "volatile_state" not in pc_entry:
                pc_entry["volatile_state"] = {}
            for k, v in vs.items():
                pc_entry["volatile_state"][k] = v
            merged_fields.append("volatile_state")
        else:
            print(
                f"  PC partial merge: volatile_state skipped (empty or not a dict) at {turn_id}",
                file=sys.stderr,
            )

    # Merge individual stable_attributes that are in the allowed set
    # and conform to the expected entity schema shape.
    sa = entity_data.get("stable_attributes")
    if sa is not None:
        attempted_fields.append("stable_attributes")
        if isinstance(sa, dict) and sa:
            stable_attr_merged = False
            for k, v in sa.items():
                if k not in PC_ALLOWED_ATTRS:
                    continue
                if not isinstance(v, dict) or "value" not in v:
                    continue
                if "stable_attributes" not in pc_entry:
                    pc_entry["stable_attributes"] = {}
                pc_entry["stable_attributes"][k] = v
                stable_attr_merged = True
            if stable_attr_merged:
                merged_fields.append("stable_attributes")

    # Update last_updated_turn if we merged anything
    if merged_fields:
        pc_entry["last_updated_turn"] = turn_id
        if entity_data.get("status_updated_turn"):
            pc_entry["status_updated_turn"] = entity_data["status_updated_turn"]
        # Post-merge quality guards (#214)
        _prune_pc_volatile_state(pc_entry)
        sa_entry = pc_entry.get("stable_attributes", {})
        aliases_attr = sa_entry.get("aliases")
        if isinstance(aliases_attr, dict) and isinstance(aliases_attr.get("value"), list):
            aliases_attr["value"] = _filter_pc_aliases(aliases_attr["value"])
        print(
            f"  PC partial merge: merged {merged_fields} at {turn_id} "
            f"(attempted: {attempted_fields}, last_updated_turn => {turn_id})",
            file=sys.stderr,
        )
        return True
    else:
        _resp_keys = sorted(entity_data.keys()) if isinstance(entity_data, dict) else "non-dict"
        print(
            f"  WARNING: PC partial merge at {turn_id}: no fields could be merged. "
            f"Response keys: {_resp_keys} (attempted: {attempted_fields})",
            file=sys.stderr,
        )
        return False


def _collect_all_entity_ids(catalogs: dict) -> set[str]:
    """Collect all entity IDs from all catalogs."""
    ids: set[str] = set()
    for _filename, entities in catalogs.items():
        for entity in entities:
            eid = entity.get("id")
            if eid:
                ids.add(eid)
    return ids


# IDs that should never produce stub entities — they are always present
# or represent generic/unnamed references.
_SKIP_STUB_IDS = {"char-player"}
_GENERIC_STEMS = {
    "stranger", "figure", "someone", "person", "creature", "thing",
    "guard", "villager", "traveler", "merchant", "soldier", "voice",
    "shadow", "spirit", "beast", "animal",
    # Pronouns — should never become entity IDs
    "she", "he", "they", "it", "her", "him", "them",
    "his", "hers", "its", "their", "theirs",
}

# Meta-labels that should never be accepted as PC aliases (#186)
_PC_ALIAS_BLOCKLIST = {
    "player character", "pc", "player", "the player",
    "main character", "protagonist", "the protagonist",
    "hero", "the hero", "adventurer", "the adventurer",
}

# Minimum alias length — single short words are unlikely to be real aliases (#214)
_PC_ALIAS_MIN_LENGTH = 3
# Maximum number of aliases retained for the PC entity (#214)
_PC_ALIAS_MAX_COUNT = 10

# Common English words that appear in RPG narratives but are not character names (#214, #239)
_PC_ALIAS_WORD_BLOCKLIST = {
    "broken", "covering", "disruption", "hunters", "method", "one", "pattern",
    "plague", "precision", "protocol", "treatment",
    "the", "a", "an", "this", "that", "it", "you", "me",
    "attack", "defense", "move", "action", "skill", "ability",
    "fire", "water", "earth", "wind", "light", "dark", "shadow",
    "death", "life", "blood", "spirit", "soul", "magic",
    # Additional false-positive words from full extraction runs (#239)
    "echo", "field", "geometric", "head", "new", "quiet",
    "reinforced", "song", "southern", "triangular", "tribes", "two", "weave",
    # NPC role descriptors that are not PC names (#239)
    "chief", "elder", "healer", "raider", "warrior",
    "guard", "scout", "shaman", "merchant", "captain",
}

# Maximum volatile_state keys retained for PC after merge (#214)
_PC_VOLATILE_STATE_MAX_KEYS = 30
# Core volatile_state keys that are always preserved during pruning (#214)
_PC_VOLATILE_STATE_CORE_KEYS = {
    "condition", "equipment", "location", "current_activity", "last_updated_turn",
}

# Turn-tag suffix pattern appended by LLMs to entity IDs (e.g. "char-shaman-turn-082")
_TURN_TAG_RE = re.compile(r"-turn-\d{3,}$")


def _normalize_entity_id(eid: str, known_ids: set[str]) -> str:
    """Strip turn-tag suffix if canonical ID exists or turn-tagged form is unknown.

    E.g., 'char-shaman-turn-082' -> 'char-shaman' if 'char-shaman' exists in
    *known_ids*, or if 'char-shaman-turn-082' itself is not in *known_ids*.
    """
    canonical = _TURN_TAG_RE.sub("", eid)
    if canonical != eid and (canonical in known_ids or eid not in known_ids):
        return canonical
    return eid


def _create_orphan_stubs(catalogs: dict, events: list, turn_id: str,
                        all_events: list | None = None) -> None:
    """Create stub catalog entries for entity IDs referenced in events but missing from catalogs.

    Stubs contain id, inferred name (from ID), inferred type (from prefix),
    first_seen_turn, and a source marker.

    *all_events* is the full accumulated event list used for earliest-mention
    lookup.  When ``None``, falls back to *events* (current-turn only).
    """
    if all_events is None:
        all_events = events
    known_ids = _collect_all_entity_ids(catalogs)

    orphan_ids: set[str] = set()
    for event in events:
        new_related = []
        for eid in event.get("related_entities", []):
            if not eid:
                new_related.append(eid)
                continue
            # Normalize turn-tagged IDs before orphan check
            eid = _normalize_entity_id(eid, known_ids)
            new_related.append(eid)
            if eid not in known_ids and eid not in _SKIP_STUB_IDS:
                orphan_ids.add(eid)
        event["related_entities"] = new_related

    for eid in sorted(orphan_ids):
        # Skip concept-prefix entities — abstract concepts, not catalogue entities
        if eid.startswith("concept-"):
            continue
        # Skip generic/unnamed entity references
        stem = _strip_any_prefix(eid)
        if stem in _GENERIC_STEMS:
            continue

        # Infer type and name from the ID
        inferred_type = _infer_type_from_prefix(eid)
        # Build a human-readable name: strip prefix, replace hyphens, title-case
        inferred_name = stem.replace("-", " ").title()

        catalog_file = TYPE_TO_CATALOG.get(inferred_type)
        if not catalog_file:
            catalog_file = "characters.json"  # default fallback

        earliest = _find_earliest_mention(eid, inferred_name, all_events)
        effective_turn = earliest or turn_id
        stub = {
            "id": eid,
            "name": inferred_name,
            "type": inferred_type,
            "identity": f"Entity referenced in events (stub — auto-created from event data).",
            "first_seen_turn": effective_turn,
            "last_updated_turn": turn_id,
            "notes": "Auto-created by event-stub.",
        }
        catalogs.setdefault(catalog_file, []).append(stub)
        print(f"  STUB: Created stub entity '{eid}' ({inferred_name}) from event data at {turn_id}")


def _ensure_birth_entities(events_list: list, catalogs: dict) -> list[str]:
    """Ensure entities named in birth events exist as catalog entries.

    For birth-type events, detects "named X" patterns in descriptions and
    creates character entities if they don't already exist.  Also adds the
    child ID to the event's ``related_entities`` so downstream consumers
    (backfill, wiki, etc.) can find them.

    Returns a list of newly created entity IDs.
    """
    known_ids = _collect_all_entity_ids(catalogs)
    created: list[str] = []

    for event in events_list:
        if event.get("type") != "birth":
            continue
        desc = event.get("description", "")
        # Look for "named X" pattern in birth descriptions
        match = re.search(r'\bnamed\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', desc)
        if not match:
            continue

        child_name = match.group(1)
        # Sanitize to valid entity ID chars: lowercase alphanumeric + hyphens
        slug = re.sub(r'[^a-z0-9]+', '-', child_name.lower()).strip('-')
        if not slug:
            continue
        child_id = f"char-{slug}"
        source_turns = event.get("source_turns", [])
        # Pick the earliest source turn (by parsed number) for accuracy
        first_turn = None
        first_turn_num = None
        for st in source_turns:
            sn = _parse_turn_number(st)
            if sn is not None and (first_turn_num is None or sn < first_turn_num):
                first_turn = st
                first_turn_num = sn
        if first_turn is None:
            first_turn = event.get("source_turn")

        # Ensure child is in related_entities regardless of whether entity exists
        rel = event.get("related_entities", [])
        if child_id not in rel:
            event.setdefault("related_entities", []).append(child_id)

        if child_id in known_ids:
            continue

        # Create a proper character entity (not a hollow stub)
        entity = {
            "id": child_id,
            "name": child_name,
            "type": "character",
            "identity": f"Child born during the narrative, named {child_name}.",
            "first_seen_turn": first_turn or "turn-001",
            "last_updated_turn": first_turn or "turn-001",
            "notes": "Auto-created from birth event.",
        }
        if not _validate_entity(entity):
            print(f"  WARNING: Birth entity '{child_id}' failed validation, skipping",
                  file=sys.stderr)
            continue
        merge_entity(catalogs, entity)
        known_ids.add(child_id)
        created.append(child_id)
        print(f"  BIRTH: Created entity '{child_id}' ({child_name}) from birth event")

    return created


def extract_and_merge(
    turn: dict,
    catalogs: dict,
    events_list: list,
    llm: LLMClient,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    catalog_dir: str | None = None,
) -> tuple[dict, list, bool, dict]:
    """Process one turn through all extraction agents.

    Args:
        turn: Dict with keys turn_id, speaker, text.
        catalogs: Dict keyed by catalog filename, values are entity arrays.
        events_list: Current list of events.
        llm: LLM client instance.
        min_confidence: Minimum confidence to catalog an entity.
        catalog_dir: Optional path to the catalog directory, used to load
            arc sidecar files for relationship compaction (#120).

    Returns:
        Tuple of (catalogs, events_list, turn_failed, log_record) where
        turn_failed is True when a non-PC extraction agent encountered an
        LLM error (e.g. 429, quota exhaustion, timeout).  PC extraction
        failures are tracked separately via ``_pc_consecutive_failures``
        and the ``pc_ok`` / ``pc_error`` fields in log_record.
        log_record is a dict summarising per-phase outcomes for this turn;
        phases that were skipped have their ``*_ok`` field set to None.
    """
    global _pc_consecutive_failures, _pc_skipped_turns, _pc_turns_since_cooldown
    turn_id = turn["turn_id"]
    _t0 = time.monotonic()
    _entities_before = sum(len(v) for v in catalogs.values())
    _events_before = len(events_list)

    # Per-phase tracking for extraction log (#217)
    # Phases that can be skipped start as None so the log does not report
    # a successful extraction when that phase never actually ran.
    _phase_log: dict = {
        "discovery_ok": True, "discovery_error": None,
        "detail_ok": True, "detail_error": None,
        "pc_ok": None, "pc_error": None,
        "relationships_ok": None, "relationships_error": None,
        "events_ok": True, "events_error": None,
    }

    # Load arc sidecar for PC relationship compaction (#120)
    # V2 layout stores per-entity files under <catalog_dir>/characters/;
    # fall back to catalog root for legacy/V1 layouts.
    pc_arcs_data = None
    if catalog_dir:
        arcs_candidates = [
            os.path.join(catalog_dir, "characters", "char-player.arcs.json"),
            os.path.join(catalog_dir, "char-player.arcs.json"),
        ]
        for arcs_path in arcs_candidates:
            if os.path.isfile(arcs_path):
                try:
                    with open(arcs_path, "r", encoding="utf-8-sig") as f:
                        pc_arcs_data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass  # Ignore corrupt/unreadable arcs file
                break

    # --- 1. Entity Discovery ---
    turn_failed = False
    current_turn_num = _parse_turn_number(turn_id)
    _ctx_len = getattr(llm, "context_length", None)
    if not isinstance(_ctx_len, int):
        _ctx_len = None
    _cfg = getattr(llm, "config", None)
    _entity_budget = _cfg.get("entity_context_budget") if isinstance(_cfg, dict) else None
    known = format_known_entities_bounded(
        catalogs,
        current_turn=current_turn_num,
        context_length=_ctx_len,
        entity_context_budget=_entity_budget,
    )
    try:
        discovery_result = llm.extract_json(
            system_prompt=load_template("entity-discovery"),
            user_prompt=format_discovery_prompt(turn, known),
        )
    except QuotaExhaustedError:
        raise
    except LLMExtractionError as e:
        print(f"  WARNING: Entity discovery failed for {turn_id}: {e}", file=sys.stderr)
        discovery_result = {"entities": []}
        turn_failed = True
        _phase_log["discovery_ok"] = False
        _phase_log["discovery_error"] = str(e)

    if not isinstance(discovery_result, dict):
        print(f"  WARNING: Discovery returned non-dict for {turn_id}, skipping", file=sys.stderr)
        discovery_result = {"entities": []}
        turn_failed = True
        _phase_log["discovery_ok"] = False
        _phase_log["discovery_error"] = _phase_log["discovery_error"] or "non-dict response"

    discovered = discovery_result.get("entities", [])

    # Defensive shape check: ensure entities is a list of dicts (#250 review)
    if not isinstance(discovered, list):
        print(f"  WARNING: Discovery entities is not a list for {turn_id}, skipping", file=sys.stderr)
        discovered = []
        turn_failed = True
        _phase_log["discovery_ok"] = False
        _phase_log["discovery_error"] = _phase_log["discovery_error"] or "entities field is not a list"
    else:
        discovered = [e for e in discovered if isinstance(e, dict)]

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

    # Build discovery log entries for all proposed entities (#250)
    # Use None for missing fields to distinguish absent from default values.
    _discovery_proposals = []
    for entity in discovered:
        _discovery_proposals.append({
            "name": entity.get("name"),
            "is_new": entity.get("is_new"),
            "proposed_id": entity.get("proposed_id"),
            "existing_id": entity.get("existing_id"),
            "confidence": entity.get("confidence"),
        })

    # Filter by confidence, tracking rejections (#250)
    _discovery_filtered = []
    qualified = []
    for entity in discovered:
        if entity.get("confidence", 0) >= min_confidence:
            qualified.append(entity)
        else:
            eid = get_entity_id(entity)
            _discovery_filtered.append({
                "name": entity.get("name", ""),
                "id": eid,
                "reason": "below_confidence_threshold",
            })

    # Reject concept-prefix entities at discovery acceptance time (#124)
    filtered_qualified = []
    for entity_ref in qualified:
        eid = get_entity_id(entity_ref)
        if eid and eid.lower().startswith("concept-"):
            print(f"  Skipping concept-prefix entity from discovery: {eid}", file=sys.stderr)
            _discovery_filtered.append({
                "name": entity_ref.get("name", ""),
                "id": eid,
                "reason": "concept_prefix",
            })
            continue
        filtered_qualified.append(entity_ref)
    qualified = filtered_qualified

    # --- 2. Entity Detail Extraction (per entity above threshold) ---
    for entity_ref in qualified:
        entity_id = get_entity_id(entity_ref)
        if not entity_id:
            continue

        # Skip pronoun / generic-stem IDs that slipped through discovery
        stem = _strip_any_prefix(entity_id)
        if stem.lower() in _GENERIC_STEMS:
            continue

        # Look up current entry for existing entities
        current_entry = None
        if not entity_ref.get("is_new", True):
            result = find_entity_by_id(catalogs, entity_id)
            if result:
                _, current_entry = result

        try:
            entity_arcs = pc_arcs_data if entity_id == "char-player" else None
            detail_result = llm.extract_json(
                system_prompt=load_template("entity-detail"),
                user_prompt=format_detail_prompt(turn, entity_ref, current_entry,
                                                 arcs_data=entity_arcs),
            )
        except QuotaExhaustedError:
            raise
        except LLMExtractionError as e:
            print(f"  WARNING: Detail extraction failed for {entity_id} at {turn_id}: {e}", file=sys.stderr)
            turn_failed = True
            _phase_log["detail_ok"] = False
            _phase_log["detail_error"] = str(e)
            continue

        entity_data = _unwrap_entity_response(detail_result)
        if entity_data:
            entity_data = _coerce_entity_fields(entity_data)
        if entity_data and not _filter_concept_prefix_from_items(entity_data):
            continue
        if entity_data and _validate_entity(entity_data):
            _filter_pc_attributes(entity_data)
            merge_entity(catalogs, entity_data)
            # Clear residual stub notes only when the effective identity is
            # non-stub; placeholder stub identities still carry useful notes (#152).
            _effective_identity = entity_data.get("identity")
            if not _effective_identity and current_entry:
                _effective_identity = current_entry.get("identity")
            if (
                current_entry
                and isinstance(_effective_identity, str)
                and _effective_identity.strip()
                and "stub" not in _effective_identity.lower()
            ):
                _clear_stub_notes(current_entry)
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
        # Skip PC extraction after too many consecutive failures (#149)
        _skip_pc = _should_skip_pc(_pc_consecutive_failures, _pc_turns_since_cooldown)
        if _pc_consecutive_failures >= _PC_SKIP_THRESHOLD:
            _pc_turns_since_cooldown += 1  # lgtm[py/unused-global-variable]
            if _skip_pc:
                _pc_skipped_turns += 1
        if not _skip_pc:
            pc_result = find_entity_by_id(catalogs, "char-player")
            pc_entry = pc_result[1] if pc_result else dict(PLAYER_CHARACTER_SEED)
            # Sanitize existing entry before sending to LLM so stale keys
            # don't appear in the prompt and get echoed back.
            _sanitize_pc_catalog_entry(catalogs)
            pc_ref = {"name": pc_entry["name"], "type": "character",
                      "existing_id": "char-player", "is_new": False}
            # Use extended timeout for PC extraction — context is larger (#107)
            pc_timeout = max(llm.default_timeout * 2, 120)
            pc_updated = False
            try:
                detail_result = llm.extract_json(
                    system_prompt=load_template("entity-detail"),
                    user_prompt=format_detail_prompt(turn, pc_ref, pc_entry,
                                                     arcs_data=pc_arcs_data),
                    timeout=pc_timeout,
                    max_tokens=llm.pc_max_tokens,
                )
                entity_data = _unwrap_entity_response(detail_result)
                if entity_data:
                    entity_data = _coerce_entity_fields(entity_data)
                if entity_data and _validate_entity(entity_data):
                    _filter_pc_attributes(entity_data)
                    merge_entity(catalogs, entity_data)
                    # Purge any stale keys that survived the merge
                    _sanitize_pc_catalog_entry(catalogs)
                    pc_updated = True
                elif entity_data is not None:
                    # Validation failed — attempt partial merge fallback (#107)
                    # Log structure and partial values of the raw response to aid diagnosis (#125, #199)
                    if isinstance(entity_data, dict):
                        try:
                            _data_keys = sorted(entity_data.keys())
                        except Exception:
                            _data_keys = list(map(str, entity_data.keys()))
                        try:
                            _data_preview = json.dumps(entity_data, default=str)[:500]
                        except Exception:
                            _data_preview = repr(entity_data)[:500]
                    else:
                        _data_keys = "non-dict"
                        _data_preview = str(entity_data)[:500]
                    print(
                        f"  PC detail extraction: validation failed at {turn_id}, "
                        f"response keys={_data_keys}, falling back to partial merge",
                        file=sys.stderr,
                    )
                    print(
                        f"  PC detail extraction: raw response preview (500 chars): {_data_preview}",
                        file=sys.stderr,
                    )
                    # Partial merge success counts as an update (#133)
                    pc_updated = _pc_partial_merge(catalogs, entity_data, turn_id)
                    _write_pc_debug_record(
                        turn_id,
                        "validation_failed",
                        _data_keys,
                        _data_preview,
                        merge_result=pc_updated,
                    )
                else:
                    # entity_data is None — extraction returned nothing (#133)
                    print(
                        f"  WARNING: PC detail extraction returned None at {turn_id}. "
                        f"Consecutive failures: {_pc_consecutive_failures + 1}",
                        file=sys.stderr,
                    )
                    _write_pc_debug_record(turn_id, "none_returned", [], "", merge_result=False)
            except QuotaExhaustedError:
                raise
            except LLMExtractionError as e:
                print(f"  WARNING: PC detail extraction failed at {turn_id}: {e}", file=sys.stderr)
                _phase_log["pc_ok"] = False
                _phase_log["pc_error"] = str(e)

            # Track consecutive PC extraction failures (#133)
            if pc_updated:
                _phase_log["pc_ok"] = True
                _pc_consecutive_failures = 0  # lgtm[py/unused-global-variable]
                _pc_turns_since_cooldown = 0  # lgtm[py/unused-global-variable]
            else:
                _phase_log["pc_ok"] = False
                if not _phase_log["pc_error"]:
                    _phase_log["pc_error"] = "validation_failed_or_none"
                _pc_consecutive_failures += 1  # lgtm[py/unused-global-variable]
                if _pc_consecutive_failures == _PC_FAILURE_WARN_THRESHOLD:
                    print(
                        f"  WARNING: PC extraction has failed for {_pc_consecutive_failures} "
                        f"consecutive turns (last update: "
                        f"{pc_entry.get('last_updated_turn', 'unknown')}). "
                        f"Context may be too large for reliable extraction.",
                        file=sys.stderr,
                    )
                elif _pc_consecutive_failures == _PC_SKIP_THRESHOLD:
                    print(
                        f"  WARNING: PC extraction entering cooldown after "
                        f"{_PC_SKIP_THRESHOLD} consecutive failures "
                        f"(skip {_PC_SKIP_COOLDOWN}, retry {_PC_RETRY_WINDOW}).",
                        file=sys.stderr,
                    )
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
            _phase_log["relationships_ok"] = True
        except QuotaExhaustedError:
            raise
        except LLMExtractionError as e:
            print(f"  WARNING: Relationship mapping failed for {turn_id}: {e}", file=sys.stderr)
            turn_failed = True
            _phase_log["relationships_ok"] = False
            _phase_log["relationships_error"] = str(e)

    # --- 4. Event Extraction ---
    next_evt_id = get_next_event_id(events_list)
    entity_ids = [e["id"] for e in mentioned_entities]

    try:
        event_result = llm.extract_json(
            system_prompt=load_template("event-extractor"),
            user_prompt=format_event_prompt(turn, next_evt_id, entity_ids),
        )
        new_events = event_result.get("events", [])

        # --- Phase 0: Validate event source_turns match processing turn (#127) ---
        _fix_event_source_turns(new_events, turn_id)

        # --- Phase 1: Normalize event related_entities IDs (#108) ---
        known_ids = _collect_all_entity_ids(catalogs)
        for event in new_events:
            related = event.get("related_entities", [])
            if related:
                event["related_entities"] = [
                    normalize_entity_id(eid, known_ids) for eid in related
                ]

        valid_events = [e for e in (_coerce_event_fields(ev) for ev in new_events) if e is not None and _validate_event(e)]

        # --- Phase 2: Create stub entities for orphan IDs (#106) ---
        if valid_events:
            _create_orphan_stubs(catalogs, valid_events, turn_id,
                                all_events=events_list + valid_events)
            merge_events(events_list, valid_events)

        # --- Phase 3: Create entities for birth events (#136) ---
        _ensure_birth_entities(events_list, catalogs)
    except QuotaExhaustedError:
        raise
    except LLMExtractionError as e:
        print(f"  WARNING: Event extraction failed for {turn_id}: {e}", file=sys.stderr)
        turn_failed = True
        _phase_log["events_ok"] = False
        _phase_log["events_error"] = str(e)

    llm.delay()

    # Build extraction log record (#217)
    _elapsed_ms = int((time.monotonic() - _t0) * 1000)
    _entities_after = sum(len(v) for v in catalogs.values())
    _events_after = len(events_list)
    _log_record = {
        "turn_id": turn_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discovery_ok": _phase_log["discovery_ok"],
        "discovery_error": _phase_log["discovery_error"],
        "detail_ok": _phase_log["detail_ok"],
        "detail_error": _phase_log["detail_error"],
        "pc_ok": _phase_log["pc_ok"],
        "pc_error": _phase_log["pc_error"],
        "relationships_ok": _phase_log["relationships_ok"],
        "relationships_error": _phase_log["relationships_error"],
        "events_ok": _phase_log["events_ok"],
        "events_error": _phase_log["events_error"],
        "new_entities": max(0, _entities_after - _entities_before),
        "new_events": max(0, _events_after - _events_before),
        "discovery_proposals": _discovery_proposals,
        "discovery_filtered": _discovery_filtered,
        "elapsed_ms": _elapsed_ms,
    }
    return catalogs, events_list, turn_failed, _log_record


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

    # Pre-pass: normalize turn-tagged entity IDs so that e.g.
    # char-shaman-turn-082 is rewritten to char-shaman when the canonical
    # form is already present in the known IDs; otherwise the tagged ID is
    # left unchanged.
    known_ids = _collect_all_entity_ids(catalogs)
    normalize_map: dict[str, str] = {}  # old_id -> normalized_id
    for _filename, entities in catalogs.items():
        for entity in entities:
            eid = entity.get("id", "")
            normalized = _normalize_entity_id(eid, known_ids)
            if normalized != eid:
                entity["id"] = normalized
                normalize_map[eid] = normalized

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
        STOPWORDS = {
            "a", "an", "the", "of", "and", "with", "in", "on", "to",
            # RPG-context generic words that cause false dedup
            "figure", "material", "party", "bowl", "tool", "small", "large",
            "old", "new", "dark", "light", "some", "other",
        }
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
                    # Guard: require smaller set to have at least 2 tokens
                    smaller_set = tokens_a if len(tokens_a) <= len(tokens_b) else tokens_b
                    if len(smaller_set) >= 2 and (tokens_a.issubset(tokens_b) or tokens_b.issubset(tokens_a)):
                        union(idx_a, idx_b)
                        print(f"  DEDUP (substring): linking '{name_a}' and '{name_b}' as duplicates")
                        continue

                    # Rule 2: Token overlap
                    # Guard: require 100% overlap when smaller set has <= 2 tokens
                    overlap = tokens_a & tokens_b
                    smaller = min(len(tokens_a), len(tokens_b))
                    threshold = 1.0 if smaller <= 2 else 0.5
                    if smaller > 0 and len(overlap) / smaller >= threshold:
                        union(idx_a, idx_b)
                        print(f"  DEDUP (token-overlap): linking '{name_a}' and '{name_b}' as duplicates")
                        continue

                # Rule 3: ID stem overlap (hyphen-segment containment)
                # Guard: require smaller stem set to have at least 2 segments
                id_a = entities[idx_a].get("proposed_id", entities[idx_a].get("id", ""))
                id_b = entities[idx_b].get("proposed_id", entities[idx_b].get("id", ""))
                stem_a = id_a.split("-", 1)[1] if "-" in id_a else id_a
                stem_b = id_b.split("-", 1)[1] if "-" in id_b else id_b
                if stem_a and stem_b:
                    parts_a = set(stem_a.split("-"))
                    parts_b = set(stem_b.split("-"))
                    smaller_parts = parts_a if len(parts_a) <= len(parts_b) else parts_b
                    if len(smaller_parts) >= 2 and (parts_a.issubset(parts_b) or parts_b.issubset(parts_a)):
                        union(idx_a, idx_b)
                        print(f"  DEDUP (id-stem): linking '{name_a}' ({id_a}) and '{name_b}' ({id_b}) as duplicates")
                        continue

                    # Rule 4: Levenshtein distance on ID stems (#129, #132)
                    if (len(stem_a) >= 6 and len(stem_b) >= 6
                            and stem_a[0] == stem_b[0]
                            and abs(len(stem_a) - len(stem_b)) <= 2):
                        dist = _levenshtein(stem_a, stem_b)
                        if dist <= 2:
                            union(idx_a, idx_b)
                            print(
                                f"  DEDUP (levenshtein): linking '{name_a}' ({id_a}) "
                                f"and '{name_b}' ({id_b}) as duplicates (distance={dist})"
                            )

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

    # Include turn-tag normalizations in merge_map so _rewrite_stale_ids
    # can update event related_entities and relationship source/target IDs.
    for old_id, new_id in normalize_map.items():
        merge_map.setdefault(old_id, new_id)

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


# Minimum number of event references for an orphan ID to warrant a post-batch stub
_POST_BATCH_ORPHAN_MIN_REFS = 3          # Default for characters
_POST_BATCH_ORPHAN_MIN_REFS_LOC = 2      # Locations (appear less often in events)
_POST_BATCH_ORPHAN_MIN_REFS_FACTION = 1  # Factions (rare, usually named once)


def _post_batch_orphan_sweep(catalogs: dict, events_list: list) -> int:
    """Create stub entities for orphan event IDs that exceed type-specific thresholds.

    Runs after dedup to catch any remaining orphan IDs that weren't resolved
    by per-turn normalization or the dedup merge map.

    Returns the number of stubs created.
    """
    known_ids = _collect_all_entity_ids(catalogs)

    # Count references per orphan ID
    orphan_counts: dict[str, list[str]] = {}  # id -> list of turn_ids
    for event in events_list:
        turn_id = event.get("turn_id", "")
        for eid in event.get("related_entities", []):
            if not eid or eid in _SKIP_STUB_IDS:
                continue
            # Normalize turn-tagged IDs before counting
            eid = _normalize_entity_id(eid, known_ids)
            if eid not in known_ids:
                orphan_counts.setdefault(eid, []).append(turn_id)

    stubs_created = 0
    for eid, turn_ids in sorted(orphan_counts.items()):
        # Skip concept-prefix entities
        if eid.startswith("concept-"):
            continue
        stem = _strip_any_prefix(eid)
        if stem in _GENERIC_STEMS:
            continue

        inferred_type = _infer_type_from_prefix(eid)

        # Type-specific thresholds
        if inferred_type == "location":
            min_refs = _POST_BATCH_ORPHAN_MIN_REFS_LOC
        elif inferred_type == "faction":
            min_refs = _POST_BATCH_ORPHAN_MIN_REFS_FACTION
        else:
            min_refs = _POST_BATCH_ORPHAN_MIN_REFS

        if len(turn_ids) < min_refs:
            continue
        inferred_name = stem.replace("-", " ").title()
        catalog_file = TYPE_TO_CATALOG.get(inferred_type, "characters.json")

        # Sort turn IDs numerically to get the true first turn
        valid_turns = [t for t in turn_ids if t]
        if valid_turns:
            first_turn = min(valid_turns, key=lambda t: int(t.split("-")[1]) if "-" in t and t.split("-")[1].isdigit() else 0)
        else:
            first_turn = ""
        stub = {
            "id": eid,
            "name": inferred_name,
            "type": inferred_type,
            "identity": f"Entity referenced in {len(turn_ids)} events (stub — auto-created post-batch).",
            "first_seen_turn": first_turn,
            "last_updated_turn": first_turn,
            "notes": "Auto-created by post-batch-orphan-sweep.",
        }
        catalogs.setdefault(catalog_file, []).append(stub)
        stubs_created += 1
        print(f"  POST-BATCH STUB: '{eid}' ({inferred_name}), {len(turn_ids)} event refs")

    return stubs_created


# Minimum event-description mentions for name-mention discovery to create a stub
_NAME_MENTION_MIN_EVENTS = 2

# Words that are common titles/roles but NOT character names — skip these
_NAME_MENTION_STOPWORDS = {
    "the", "a", "an", "and", "but", "or", "for", "with", "from",
    "this", "that", "they", "their", "them", "there", "then",
    "what", "when", "where", "which", "while", "who", "whom",
    "have", "has", "had", "been", "being", "does", "did",
    "will", "would", "could", "should", "shall", "might",
    "into", "upon", "about", "after", "before", "between",
    "through", "during", "above", "below", "each", "every",
    "some", "many", "much", "more", "most", "less", "few",
    "also", "just", "only", "still", "even", "already",
    "here", "however", "perhaps", "though", "yet", "both",
}


def _name_mention_discovery(catalogs: dict, events_list: list) -> int:
    """Create stubs for proper names found in event descriptions but not in catalogs.

    Scans all event descriptions for capitalized words (potential character
    names) that do not match any existing entity name or alias.  Names appearing
    in >= _NAME_MENTION_MIN_EVENTS distinct events get a character stub.

    Returns the number of stubs created.
    """
    # Build a set of known names (lowercased) from all catalog entries
    known_names: set[str] = set()
    known_ids = _collect_all_entity_ids(catalogs)
    for _filename, entities in catalogs.items():
        for entity in entities:
            name = entity.get("name", "").strip().lower()
            if name:
                known_names.add(name)
            # Also collect aliases
            aliases_str = entity.get("attributes", {}).get("aliases", "")
            if aliases_str and isinstance(aliases_str, str):
                for a in aliases_str.split(","):
                    a = a.strip().lower()
                    if a:
                        known_names.add(a)
            sa = entity.get("stable_attributes", {}).get("aliases")
            if isinstance(sa, dict):
                val = sa.get("value", "")
                if isinstance(val, list):
                    for a in val:
                        if isinstance(a, str):
                            known_names.add(a.strip().lower())
                elif isinstance(val, str) and val:
                    for a in val.split(","):
                        known_names.add(a.strip().lower())

    # Scan event descriptions for capitalized proper names
    # Pattern: a capitalized word that looks like a name (not all-caps, not short)
    name_re = re.compile(r"\b([A-Z][a-z]{2,})\b")

    name_events: dict[str, set[str]] = {}  # lowered name -> set of event IDs
    name_canonical: dict[str, str] = {}     # lowered name -> original casing

    for event in events_list:
        desc = event.get("description", "")
        if not desc:
            continue
        event_id = event.get("id", event.get("turn_id", ""))
        for match in name_re.finditer(desc):
            word = match.group(1)
            word_lower = word.lower()
            if word_lower in _NAME_MENTION_STOPWORDS:
                continue
            if word_lower in _GENERIC_STEMS:
                continue
            if word_lower in known_names:
                continue
            name_events.setdefault(word_lower, set()).add(event_id)
            if word_lower not in name_canonical:
                name_canonical[word_lower] = word

    stubs_created = 0
    for name_lower, event_ids in sorted(name_events.items()):
        if len(event_ids) < _NAME_MENTION_MIN_EVENTS:
            continue

        canonical = name_canonical[name_lower]
        char_id = f"char-{name_lower}"

        # Skip if this ID already exists
        if char_id in known_ids:
            continue

        # Determine first_seen turn from events
        first_turn = ""
        first_num: int | None = None
        for event in events_list:
            eid = event.get("id", event.get("turn_id", ""))
            if eid in event_ids:
                turn = event.get("turn_id", "")
                t_num = _parse_turn_number(turn) if turn else None
                if t_num is not None and (first_num is None or t_num < first_num):
                    first_num = t_num
                    first_turn = turn

        stub = {
            "id": char_id,
            "name": canonical,
            "type": "character",
            "identity": f"Character mentioned by name in {len(event_ids)} event descriptions (stub — auto-created by name-mention discovery).",
            "first_seen_turn": first_turn,
            "last_updated_turn": first_turn,
            "notes": "Auto-created by name-mention-discovery.",
        }
        catalogs.setdefault("characters.json", []).append(stub)
        known_ids.add(char_id)
        known_names.add(name_lower)
        stubs_created += 1
        print(f"  NAME-MENTION STUB: '{char_id}' ({canonical}), {len(event_ids)} event desc mentions")

    return stubs_created


# ---------------------------------------------------------------------------
# Stub backfill (#128)
# ---------------------------------------------------------------------------


# Known auto-created stub note values
_STUB_NOTE_MARKERS = {
    "auto-created by event-stub.",
    "auto-created by post-batch orphan sweep.",
    "auto-created by post-batch-orphan-sweep.",
    "auto-created by name-mention-discovery.",
}

# Notes to clear from enriched entities (#152) — includes backfill markers
_STUB_CLEANUP_MARKERS = _STUB_NOTE_MARKERS | {
    "backfilled from stub.",
}


def _has_real_identity(entity: dict) -> bool:
    """Return True if the entity has a non-stub identity string."""
    identity = entity.get("identity", "")
    return bool(identity) and isinstance(identity, str) and "stub" not in identity.lower()


def _clear_stub_notes(entity: dict) -> bool:
    """Clear stub-marker notes from enriched entities. Returns True if notes were cleared."""
    notes = entity.get("notes", "")
    if not notes:
        return False
    notes_lower = notes.strip().lower().rstrip(".")
    for marker in _STUB_CLEANUP_MARKERS:
        if marker.lower().rstrip(".") in notes_lower:
            entity["notes"] = ""
            return True
    return False


def _is_stub_entity(entity: dict) -> bool:
    """Return True if the entity is a hollow stub needing backfill."""
    notes = entity.get("notes", "")
    if isinstance(notes, str) and notes.lower().strip().rstrip(".") + "." in _STUB_NOTE_MARKERS:
        return True
    identity = entity.get("identity", "")
    if not identity or identity == "":
        return True
    return False


def _find_earliest_mention(entity_id: str, entity_name: str | None,
                           events_list: list) -> str | None:
    """Find the earliest turn where entity appears by ID or name in events.

    Uses parsed turn numbers for comparison so that turn-1000 sorts after
    turn-999 regardless of string ordering.
    """
    earliest: str | None = None
    earliest_num: int | None = None
    name_lower = entity_name.lower() if entity_name and len(entity_name) >= 3 else None
    for event in events_list:
        turns = event.get("source_turns", [])
        st_single = event.get("source_turn")
        if st_single and st_single not in turns:
            turns = list(turns) + [st_single]
        matched = False
        if entity_id in event.get("related_entities", []):
            matched = True
        if name_lower and name_lower in event.get("description", "").lower():
            matched = True
        if matched:
            for t in turns:
                t_num = _parse_turn_number(t)
                if t_num is None:
                    continue
                if earliest_num is None or t_num < earliest_num:
                    earliest = t
                    earliest_num = t_num
    return earliest


def _collect_stub_context(entity_id: str, events_list: list, turn_dicts: list,
                          first_seen_turn: str | None,
                          entity_name: str | None = None) -> str:
    """Gather turn text around an entity's event references for backfill context."""
    # Find all turns that reference this entity via events
    ref_turns: set[str] = set()
    name_lower = entity_name.lower() if entity_name and len(entity_name) >= 3 else None
    for event in events_list:
        related = event.get("related_entities", [])
        id_match = entity_id in related
        name_match = name_lower and name_lower in event.get("description", "").lower()
        if id_match or name_match:
            for st in event.get("source_turns", []):
                ref_turns.add(st)
            st_single = event.get("source_turn")
            if st_single:
                ref_turns.add(st_single)

    # Build turn lookup and preserve transcript order for neighbor selection
    turn_lookup = {t["turn_id"]: t for t in turn_dicts}
    ordered_turn_ids = [t["turn_id"] for t in turn_dicts]
    turn_index = {turn_id: idx for idx, turn_id in enumerate(ordered_turn_ids)}

    # Also include first_seen_turn and its neighbors
    if first_seen_turn:
        ref_turns.add(first_seen_turn)
        idx = turn_index.get(first_seen_turn)
        if idx is not None:
            if idx > 0:
                ref_turns.add(ordered_turn_ids[idx - 1])
            if idx + 1 < len(ordered_turn_ids):
                ref_turns.add(ordered_turn_ids[idx + 1])

    # Collect context text from referenced turns
    context_parts = []
    for tid in sorted(ref_turns):
        turn = turn_lookup.get(tid)
        if turn:
            context_parts.append(f"[{tid}] {turn['text'][:500]}")

    return "\n".join(context_parts[:10])  # limit context size


def backfill_stubs(
    turn_dicts: list,
    catalogs: dict,
    events_list: list,
    llm: "LLMClient",
) -> int:
    """Re-extract stub entities using gathered context (#128).

    Returns the number of stubs successfully backfilled.
    """
    stubs: list[tuple[str, dict, str]] = []  # (catalog_file, entity, entity_id)
    for filename, entities in catalogs.items():
        for entity in entities:
            if _is_stub_entity(entity):
                stubs.append((filename, entity, entity.get("id", "")))

    if not stubs:
        return 0

    print(f"  Backfill: found {len(stubs)} stub(s) to re-extract")
    backfilled = 0

    for _filename, entity, entity_id in stubs:
        if not entity_id:
            continue

        first_seen = entity.get("first_seen_turn", "turn-001")
        entity_name = entity.get("name", "")
        context_text = _collect_stub_context(entity_id, events_list, turn_dicts, first_seen,
                                             entity_name=entity_name)
        if not context_text:
            print(f"  Backfill: no context found for stub '{entity_id}', skipping")
            continue

        # Build a synthetic turn for detail extraction
        synthetic_turn = {
            "turn_id": first_seen,
            "speaker": "DM",
            "text": context_text,
        }
        entity_ref = {
            "name": entity.get("name", ""),
            "type": entity.get("type", "character"),
            "existing_id": entity_id,
            "is_new": False,
        }

        try:
            detail_result = llm.extract_json(
                system_prompt=load_template("entity-detail"),
                user_prompt=format_detail_prompt(synthetic_turn, entity_ref, entity),
            )
            entity_data = _unwrap_entity_response(detail_result)
            if entity_data:
                entity_data = _coerce_entity_fields(entity_data)
            if entity_data and _validate_entity(entity_data):
                # Preserve first_seen_turn from original stub
                entity_data["first_seen_turn"] = first_seen
                merge_entity(catalogs, entity_data)
                # Clear stub marker so entity won't be re-flagged (#128)
                merged = find_entity_by_id(catalogs, entity_id)
                if merged:
                    merged[1]["notes"] = "Backfilled from stub."
                backfilled += 1
                print(f"  Backfill: successfully enriched stub '{entity_id}'")
        except QuotaExhaustedError:
            raise
        except LLMExtractionError as e:
            print(f"  WARNING: Backfill failed for {entity_id}: {e}", file=sys.stderr)

        llm.delay()

    # Clear residual stub notes from enriched entities (#152)
    for entities in catalogs.values():
        for entity in entities:
            if _has_real_identity(entity) and _clear_stub_notes(entity):
                print(f"  Cleared stub notes from enriched entity: {entity.get('id', 'unknown')}")

    return backfilled


# ---------------------------------------------------------------------------
# Entity refresh — periodic re-extraction of stale entities (#161)
# ---------------------------------------------------------------------------

# Defaults used when config keys are absent
_DEFAULT_REFRESH_INTERVAL = 50
_DEFAULT_REFRESH_BATCH_SIZE = 10
_MAX_REFRESH_BATCH_SIZE = 25
_DEFAULT_CHECKPOINT_INTERVAL = 25


def _read_checkpoint_interval(llm_config: dict) -> int:
    """Return checkpoint interval from config dict, with validation.

    Falls back to _DEFAULT_CHECKPOINT_INTERVAL on missing, invalid, or
    non-positive values and prints a warning to stderr.
    """
    raw = llm_config.get("checkpoint_interval", _DEFAULT_CHECKPOINT_INTERVAL) if isinstance(llm_config, dict) else _DEFAULT_CHECKPOINT_INTERVAL
    try:
        value = int(raw)
    except (TypeError, ValueError):
        print(
            f"  WARNING: Invalid checkpoint_interval {raw!r}; using default {_DEFAULT_CHECKPOINT_INTERVAL}",
            file=sys.stderr,
        )
        return _DEFAULT_CHECKPOINT_INTERVAL
    if value < 1:
        print(
            f"  WARNING: checkpoint_interval must be >= 1, got {value!r}; using default {_DEFAULT_CHECKPOINT_INTERVAL}",
            file=sys.stderr,
        )
        return _DEFAULT_CHECKPOINT_INTERVAL
    return value

# Type-aware slot allocation shares (must sum to 1.0)
_REFRESH_TYPE_SHARES: dict[str, float] = {
    "characters.json": 0.5,
    "locations.json": 0.2,
    "items.json": 0.2,
    "factions.json": 0.1,
}


def find_stale_entities(
    current_turn_number: int,
    catalogs: dict,
    turn_dicts: list,
    refresh_interval: int = _DEFAULT_REFRESH_INTERVAL,
    batch_size: int = _DEFAULT_REFRESH_BATCH_SIZE,
    events_list: list | None = None,
) -> list[tuple[str, dict]]:
    """Find entities whose ``last_updated_turn`` is stale and that are still
    mentioned in the transcript since their last update.

    Returns up to *effective_batch* ``(catalog_file, entity)`` tuples sorted
    by staleness (most stale first) with type-aware slot allocation so that
    narratively important entity types (characters) get proportional refresh
    priority.  The player character (``char-player``) is excluded because it
    is already extracted every turn.

    The effective batch size equals *batch_size* for catalogs with fewer than
    60 entities.  For larger catalogs it scales to
    ``max(batch_size, catalog_size // 5)`` capped at
    ``_MAX_REFRESH_BATCH_SIZE``.

    If *events_list* is provided, entities referenced in more events win
    ties between entities with similar staleness gaps.
    """
    if refresh_interval <= 0:
        return []

    # --- Dynamic batch scaling ---
    catalog_size = sum(len(v) for v in catalogs.values())
    effective_batch = batch_size
    if catalog_size >= 60:
        effective_batch = max(batch_size, catalog_size // 5)
    effective_batch = min(effective_batch, _MAX_REFRESH_BATCH_SIZE)

    # --- Build event-count index for tiebreaking ---
    event_counts: dict[str, int] = {}
    if events_list:
        for ev in events_list:
            for ref in ev.get("related_entities", []):
                event_counts[ref] = event_counts.get(ref, 0) + 1

    # --- Collect stale entities grouped by catalog type ---
    # Tuple: (gap, -event_count, filename, entity)
    by_type: dict[str, list[tuple[int, int, str, dict]]] = {}

    for filename, entities in catalogs.items():
        for entity in entities:
            entity_id = entity.get("id", "")
            if entity_id == "char-player":
                continue  # PC is always refreshed

            last_turn = _parse_turn_number(entity.get("last_updated_turn"))
            if last_turn is None:
                continue
            gap = current_turn_number - last_turn
            if gap <= refresh_interval:
                continue

            # Only refresh if the entity is mentioned in transcript since last update
            entity_name = entity.get("name", "")
            if not _entity_mentioned_since(entity_id, entity_name, last_turn, turn_dicts):
                continue

            neg_events = -(event_counts.get(entity_id, 0))
            by_type.setdefault(filename, []).append((gap, neg_events, filename, entity))

    # --- Type-aware slot allocation ---
    # Compute per-type slots so the sum equals effective_batch exactly.
    # Floor each share, then distribute the remainder by largest fractional
    # part (largest-remainder method).  Types may receive 0 slots when the
    # batch is very small.
    type_order = list(_REFRESH_TYPE_SHARES.keys())
    raw = [(t, effective_batch * _REFRESH_TYPE_SHARES[t]) for t in type_order]
    floors = [(t, int(v)) for t, v in raw]
    remainder = effective_batch - sum(f for _, f in floors)
    fracs = sorted(
        [(t, v - int(v)) for t, v in raw],
        key=lambda x: x[1],
        reverse=True,
    )
    bonus = {t for t, _ in fracs[:remainder]}
    type_slots = {t: f + (1 if t in bonus else 0) for t, f in floors}

    # First pass: allocate slots proportionally per type
    selected: set[str] = set()  # entity IDs already selected
    result: list[tuple[int, int, str, dict]] = []

    for type_file in type_order:
        slots = type_slots[type_file]
        candidates = by_type.get(type_file, [])
        # Sort: staleness descending, then event count descending
        candidates.sort(key=lambda x: (-x[0], x[1]))
        for c in candidates[:slots]:
            eid = c[3].get("id", "")
            if eid not in selected:
                result.append(c)
                selected.add(eid)

    # Second pass: fill remaining capacity from all stale entities not yet taken
    if len(result) < effective_batch:
        remaining: list[tuple[int, int, str, dict]] = []
        for candidates in by_type.values():
            for c in candidates:
                eid = c[3].get("id", "")
                if eid not in selected:
                    remaining.append(c)
        remaining.sort(key=lambda x: (-x[0], x[1]))
        for c in remaining:
            if len(result) >= effective_batch:
                break
            result.append(c)

    # Final sort by staleness, then event count
    result.sort(key=lambda x: (-x[0], x[1]))
    return [(fn, ent) for _gap, _neg_ev, fn, ent in result[:effective_batch]]


def _entity_mentioned_since(
    entity_id: str,
    entity_name: str,
    last_turn_number: int,
    turn_dicts: list,
) -> bool:
    """Return True if entity is mentioned in any turn after *last_turn_number*.

    Checks both the entity ID and entity name (case-insensitive, minimum 3
    chars) against the turn text.
    """
    name_lower = entity_name.strip().lower() if entity_name and len(entity_name.strip()) >= 3 else None
    id_lower = entity_id.strip().lower() if entity_id else None

    for turn in turn_dicts:
        turn_num = _parse_turn_number(turn.get("turn_id"))
        if turn_num is None or turn_num <= last_turn_number:
            continue
        text = turn.get("text", "")
        text_lower = text.lower()
        if id_lower and id_lower in text_lower:
            return True
        if name_lower and name_lower in text_lower:
            return True
    return False


def refresh_entities(
    stale_entities: list[tuple[str, dict]],
    current_turn_id: str,
    turn_dicts: list,
    catalogs: dict,
    llm: "LLMClient",
) -> int:
    """Re-extract detail for stale entities using recent transcript context.

    For each entity, gathers transcript turns where the entity is mentioned
    after its ``last_updated_turn``, builds a synthetic context, and calls the
    LLM to update the entity detail.  New details are merged (not overwritten)
    into the existing entity.

    Returns the number of entities successfully refreshed.
    """
    refreshed = 0
    for _catalog_file, entity in stale_entities:
        entity_id = entity.get("id", "")
        if not entity_id:
            continue  # Cannot refresh an entity without an ID
        entity_name = entity.get("name", "")
        entity_type = entity.get("type", "character")
        last_updated = _parse_turn_number(entity.get("last_updated_turn")) or 0

        # Gather recent context: turns where entity is mentioned since last update
        id_lower = entity_id.lower()
        name_lower = entity_name.strip().lower() if entity_name and len(entity_name.strip()) >= 3 else None
        context_parts: list[str] = []
        for turn in turn_dicts:
            t_num = _parse_turn_number(turn.get("turn_id"))
            if t_num is None or t_num <= last_updated:
                continue
            text = turn.get("text", "")
            text_lower = text.lower()
            if id_lower in text_lower or (name_lower and name_lower in text_lower):
                context_parts.append(f"[{turn['turn_id']}] {text[:500]}")
        context_text = "\n".join(context_parts[:15])  # cap context size

        if not context_text:
            continue

        # Build a synthetic turn containing collected context
        synthetic_turn = {
            "turn_id": current_turn_id,
            "speaker": "DM",
            "text": context_text,
        }
        entity_ref = {
            "name": entity_name,
            "type": entity_type,
            "existing_id": entity_id,
            "is_new": False,
        }

        try:
            detail_result = llm.extract_json(
                system_prompt=load_template("entity-detail"),
                user_prompt=format_detail_prompt(synthetic_turn, entity_ref, entity),
            )
            entity_data = _unwrap_entity_response(detail_result)
            if entity_data:
                entity_data = _coerce_entity_fields(entity_data)
            if entity_data and _validate_entity(entity_data):
                # Preserve first_seen_turn from original entity
                entity_data["first_seen_turn"] = entity.get("first_seen_turn",
                                                             current_turn_id)
                # Explicitly advance last_updated_turn so the entity won't
                # be re-queued on the next refresh interval if the LLM
                # omitted or backdated the field.
                current_num = _parse_turn_number(current_turn_id)
                existing_num = _parse_turn_number(
                    entity.get("last_updated_turn"))
                if (
                    current_num is not None
                    and existing_num is not None
                    and existing_num > current_num
                ):
                    entity_data["last_updated_turn"] = entity.get(
                        "last_updated_turn")
                else:
                    entity_data["last_updated_turn"] = current_turn_id
                merge_entity(catalogs, entity_data)
                refreshed += 1
                print(f"  REFRESH: Updated stale entity '{entity_id}' "
                      f"(was stuck at {entity.get('last_updated_turn', '?')})")
        except QuotaExhaustedError:
            raise
        except LLMExtractionError as e:
            print(f"  WARNING: Refresh failed for {entity_id}: {e}", file=sys.stderr)

        llm.delay()

    return refreshed


def _merge_pc_aliases(
    catalogs: dict,
    events_list: list,
    catalog_dir: str,
    dry_run: bool = False,
) -> list[str]:
    """Identify and merge character entities that are aliases of char-player (#134).

    Scans event descriptions involving char-player for proper names, then checks
    whether any other character entity's name appears frequently enough to be an alias.

    Returns list of entity IDs that were merged.
    """
    pc_result = find_entity_by_id(catalogs, "char-player")
    if not pc_result:
        return []
    _, pc_entry = pc_result

    # Strip any blocklisted/invalid aliases from the PC entity (#186, #214)
    sa = pc_entry.get("stable_attributes", {})
    aliases_attr = sa.get("aliases")
    if isinstance(aliases_attr, dict):
        alias_value = aliases_attr.get("value", [])
        # Normalize to list of strings (handles str, comma-separated str, list)
        if isinstance(alias_value, str):
            normalized = [p.strip() for p in alias_value.split(",") if p.strip()]
        elif isinstance(alias_value, list):
            normalized = []
            for item in alias_value:
                if item is None:
                    continue
                text = str(item).strip()
                if text:
                    normalized.append(text)
        else:
            normalized = []
        cleaned = _filter_pc_aliases(normalized)
        if cleaned != normalized:
            aliases_attr["value"] = cleaned

    # Collect text from events that reference char-player
    pc_events = [
        e for e in events_list
        if "char-player" in e.get("related_entities", [])
    ]
    pc_text = " ".join(e.get("description", "") for e in pc_events)
    if not pc_text:
        return []

    merged = []
    merge_map: dict[str, str] = {}
    chars_catalog = "characters.json"
    entities = catalogs.get(chars_catalog, [])

    # Precompute set of entity IDs that co-occur with char-player in events
    pc_cooccurring_ids: set[str] = set()
    for e in events_list:
        rel = e.get("related_entities", [])
        if "char-player" in rel:
            pc_cooccurring_ids.update(rel)
    pc_cooccurring_ids.discard("char-player")

    for entity in list(entities):
        eid = entity.get("id", "")
        if eid == "char-player" or not eid:
            continue

        name = entity.get("name", "")
        if not name or len(name) < 3:
            continue

        # Skip meta-labels that aren't real character names (#186)
        if name.lower() in _PC_ALIAS_BLOCKLIST:
            continue

        # Skip names whose full lowercase form matches the common-word blocklist (#239)
        if name.lower() in _PC_ALIAS_WORD_BLOCKLIST:
            continue

        # Skip names starting with "The " — typically NPC titles (#239)
        if name.lower().startswith("the "):
            continue

        # Count whole-name occurrences in PC event text, case-insensitively,
        # to avoid matching substrings inside larger words or names.
        name_pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        occurrences = len(re.findall(name_pattern, pc_text, re.IGNORECASE))
        if occurrences < 2:
            continue

        # Check candidate has minimal data (≤3 turns span)
        first = entity.get("first_seen_turn", "")
        last = entity.get("last_updated_turn", "")
        if first and last:
            try:
                first_num = int(first.replace("turn-", ""))
                last_num = int(last.replace("turn-", ""))
                if last_num - first_num > 3:
                    continue  # Too much independent data — likely a real NPC
            except ValueError:
                continue

        # Guard: skip if candidate co-occurs with char-player in any event
        # (co-occurrence indicates distinct entity, not alias)
        if eid in pc_cooccurring_ids:
            continue

        # Guard: skip if candidate has a relationship with char-player
        candidate_rels = entity.get("relationships", [])
        if any(r.get("target_id") == "char-player" for r in candidate_rels):
            continue

        # Guard: skip if char-player has a relationship targeting this candidate
        pc_rels = pc_entry.get("relationships", [])
        if any(r.get("target_id") == eid for r in pc_rels):
            continue

        # Guard: skip if candidate has relationships to non-PC entities (#239)
        # Entities with their own relationship graph are independent characters.
        # Allow 1 non-PC relationship (alias may have an accidentally-attributed
        # relationship), but 2+ indicates genuine independence.
        non_pc_rels = [
            r for r in candidate_rels
            if r.get("target_id") and r.get("target_id") != "char-player"
        ]
        if len(non_pc_rels) >= 2:
            continue

        # Guard: skip if candidate is independently referenced in events (#239)
        # Entities appearing in ≥2 events without char-player are independent.
        # Counts are precomputed once (single pass) to avoid O(candidates × events).
        if "_independent_event_counts" not in locals():
            _independent_event_counts: dict[str, int] = {}
            for e in events_list:
                rel = e.get("related_entities") or []
                if "char-player" in rel:
                    continue
                for ref_eid in rel:
                    _independent_event_counts[ref_eid] = (
                        _independent_event_counts.get(ref_eid, 0) + 1
                    )
        independent_refs = _independent_event_counts.get(eid, 0)
        if independent_refs >= 2:
            continue

        # Merge into char-player: add name as alias
        alias_source_turn = first or last or ""
        sa = pc_entry.setdefault("stable_attributes", {})
        # Only include source_turn if it's a valid turn ID (schema requires turn-NNN pattern)
        alias_default = {"value": []}
        if alias_source_turn:
            alias_default["source_turn"] = alias_source_turn
        aliases = sa.setdefault("aliases", alias_default)
        alias_list = aliases.get("value", [])
        if isinstance(alias_list, list) and name not in alias_list:
            alias_list.append(name)
            aliases["value"] = alias_list
            # Keep source_turn pointing to the earliest alias origin
            existing_source = aliases.get("source_turn", "")
            if not existing_source:
                aliases["source_turn"] = alias_source_turn
            elif alias_source_turn:
                try:
                    existing_num = int(existing_source.replace("turn-", ""))
                    candidate_num = int(alias_source_turn.replace("turn-", ""))
                    if candidate_num < existing_num:
                        aliases["source_turn"] = alias_source_turn
                except ValueError:
                    pass  # Non-numeric turn IDs — keep existing source_turn

        # Absorb unique relationships from the candidate
        pc_rels = pc_entry.get("relationships", [])
        for rel in entity.get("relationships", []):
            target = rel.get("target_id", "")
            rel_type = rel.get("type", "")
            if target and not any(
                r.get("target_id") == target and r.get("type") == rel_type
                for r in pc_rels
            ):
                pc_rels.append(rel)
        pc_entry["relationships"] = pc_rels

        # Remove the candidate entity from the catalog
        entities.remove(entity)

        # Delete entity file from disk if it exists (V2 layout)
        if catalog_dir and not dry_run:
            entity_file = os.path.join(catalog_dir, "characters", f"{eid}.json")
            if os.path.isfile(entity_file):
                os.remove(entity_file)

        merge_map[eid] = "char-player"
        merged.append(eid)
        print(f"  PC alias merge: merged '{eid}' ({name}) into char-player")

    # Rewrite dangling references to merged alias IDs across events and relationships
    if merge_map:
        _rewrite_stale_ids(catalogs, events_list, merge_map)

    return merged


# ---------------------------------------------------------------------------
# Coreference hints — deterministic merging (#162)
# ---------------------------------------------------------------------------

def apply_coreference_hints(
    catalogs: dict,
    events_list: list,
    catalog_dir: str,
    hints_path: str,
    dry_run: bool = False,
) -> list[str]:
    """Apply manual coreference hints to merge known entity variants.

    Loads a coreference-hints.json file and merges variant entities into their
    canonical counterpart.  This is a deterministic merge for cases where the
    same character is referred to by multiple names/IDs across the transcript.

    Returns list of entity IDs that were merged away.
    """
    from catalog_merger import dedup_merge_entity

    if not os.path.isfile(hints_path):
        return []

    try:
        with open(hints_path, "r", encoding="utf-8") as f:
            hints = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  WARNING: failed to load coreference hints '{hints_path}': {exc}", file=sys.stderr)
        return []

    groups = hints.get("character_groups", [])
    if not groups:
        return []

    merged_ids: list[str] = []
    merge_map: dict[str, str] = {}

    for group in groups:
        canonical_id = group.get("canonical_id", "")
        canonical_name = group.get("canonical_name", "")
        variant_id_patterns = set(group.get("variant_id_patterns", []))
        variant_names = {n.strip().lower() for n in group.get("variant_names", [])}

        if not canonical_id:
            continue

        # Find canonical entity
        canonical_result = find_entity_by_id(catalogs, canonical_id)
        if not canonical_result:
            print(f"  Coreference hints: canonical entity '{canonical_id}' not found, skipping group '{canonical_name}'")
            continue
        canonical_catalog, canonical_entry = canonical_result

        # Find and merge all variants
        entities = catalogs.get(canonical_catalog, [])
        variants_to_remove = []

        for entity in entities:
            eid = entity.get("id", "")
            if eid == canonical_id or not eid:
                continue

            ename = entity.get("name", "").strip().lower()
            matched = eid in variant_id_patterns or ename in variant_names
            if not matched:
                continue

            # Merge variant into canonical, but prevent dedup_merge_entity
            # from overwriting the canonical name with the variant's name.
            saved_name = canonical_entry.get("name")
            variant_copy = dict(entity)
            variant_copy.pop("name", None)
            dedup_merge_entity(canonical_entry, variant_copy)
            canonical_entry["name"] = saved_name
            variants_to_remove.append(entity)
            merge_map[eid] = canonical_id
            merged_ids.append(eid)
            print(f"  Coreference hints: merged '{eid}' ({entity.get('name', '')}) into '{canonical_id}'")

        # Remove merged variants from catalog list
        for variant in variants_to_remove:
            entities.remove(variant)

        # Delete variant files from disk (V2 layout) — per-group only
        if catalog_dir and not dry_run:
            # Determine subdirectory from entity type prefix
            prefix = canonical_id.split("-", 1)[0] if "-" in canonical_id else ""
            type_dirs = {
                "char": "characters",
                "loc": "locations",
                "faction": "factions",
                "item": "items",
                "creature": "creatures",
                "concept": "concepts",
            }
            subdir = type_dirs.get(prefix, "")
            if subdir:
                for variant in variants_to_remove:
                    eid = variant.get("id", "")
                    if eid:
                        entity_file = os.path.join(catalog_dir, subdir, f"{eid}.json")
                        if os.path.isfile(entity_file):
                            os.remove(entity_file)

        # Add variant names as aliases on the canonical entry
        sa = canonical_entry.setdefault("stable_attributes", {})
        aliases = sa.get("aliases", {"value": []})
        if not isinstance(aliases, dict):
            aliases = {"value": []}
        alias_list = aliases.get("value", [])
        if isinstance(alias_list, str):
            alias_list = [a.strip() for a in alias_list.split(",") if a.strip()]
        existing_aliases_lower = {a.strip().lower() for a in alias_list if isinstance(a, str) and a.strip()}
        canonical_lower = canonical_name.strip().lower()
        for vname in group.get("variant_names", []):
            stripped = vname.strip() if isinstance(vname, str) else ""
            if not stripped:
                continue
            if stripped.lower() not in existing_aliases_lower and stripped.lower() != canonical_lower:
                alias_list.append(stripped)
                existing_aliases_lower.add(stripped.lower())
        aliases["value"] = alias_list
        sa["aliases"] = aliases

    # Rewrite dangling references to merged variant IDs
    if merge_map:
        _rewrite_stale_ids(catalogs, events_list, merge_map)

    if merged_ids:
        print(f"  Coreference hints: merged {len(merged_ids)} variant(s) total")

    return merged_ids


# ---------------------------------------------------------------------------
# Segmented extraction helpers (#141)
# ---------------------------------------------------------------------------

def _compare_turns(turn_a, turn_b):
    """Compare two turn IDs numerically. Returns -1, 0, or 1.

    Falls back to string comparison if parsing fails.
    """
    na = _parse_turn_number(turn_a)
    nb = _parse_turn_number(turn_b)
    if na is not None and nb is not None:
        return (na > nb) - (na < nb)
    # Fallback to string comparison for non-standard turn IDs
    return (turn_a > turn_b) - (turn_a < turn_b)


def _find_canonical(eid, ename, entity_map, id_aliases):
    """Find canonical entity ID matching by ID or name within the same entity type."""
    # Direct ID match
    if eid in entity_map:
        return eid
    # Alias match
    if eid in id_aliases:
        return id_aliases[eid]

    normalized_name = (ename or "").strip().lower()
    if not normalized_name:
        return None

    incoming_type = _infer_type_from_prefix(eid) if eid else None
    name_matches = []

    # Name match (case-insensitive) against existing entities, scoped by type
    for existing_id, existing_entity in entity_map.items():
        existing_name = existing_entity.get("name", "").strip().lower()
        if existing_name != normalized_name:
            continue

        existing_type = existing_entity.get("type") or _infer_type_from_prefix(existing_id)
        if incoming_type:
            if existing_type == incoming_type:
                return existing_id
        else:
            name_matches.append(existing_id)

    # If the incoming ID does not reveal a type, only accept an unambiguous match
    if len(name_matches) == 1:
        return name_matches[0]
    return None


def _is_empty_attr_value(value):
    """Check if a stable_attribute value is empty/missing."""
    return value is None or value == "" or value == [] or value == {}


def _merge_entity_across_segments(target, source):
    """Merge a source entity into a target entity from a different segment."""
    # Update last_updated_turn to the later of the two (numeric comparison)
    src_turn = source.get("last_updated_turn", "")
    tgt_turn = target.get("last_updated_turn", "")
    if _compare_turns(src_turn, tgt_turn) > 0:
        target["last_updated_turn"] = src_turn

    # Update first_seen_turn to the earlier of the two (numeric comparison)
    src_first = source.get("first_seen_turn", "")
    tgt_first = target.get("first_seen_turn", "")
    if src_first and (not tgt_first or _compare_turns(src_first, tgt_first) < 0):
        target["first_seen_turn"] = src_first

    # Merge identity — prefer longer/non-stub
    src_identity = source.get("identity", "")
    tgt_identity = target.get("identity", "")
    if len(src_identity) > len(tgt_identity) and "stub" not in src_identity.lower():
        target["identity"] = src_identity

    # Merge current_status — prefer the later segment's
    if source.get("current_status") and _compare_turns(src_turn, tgt_turn) >= 0:
        target["current_status"] = source["current_status"]

    # Merge stable_attributes — handles both V1 scalar and V2 dict formats
    src_attrs = source.get("stable_attributes", {})
    tgt_attrs = target.setdefault("stable_attributes", {})
    for key, val in src_attrs.items():
        tgt_val = tgt_attrs.get(key)

        # V1 scalar format (backward compatibility)
        if not isinstance(val, dict):
            if key not in tgt_attrs or not tgt_val:
                tgt_attrs[key] = val
            continue

        # V2 dict format: {value, inference, confidence, source_turn}
        if key not in tgt_attrs or not isinstance(tgt_val, dict):
            # Target missing or legacy scalar — take the full V2 object
            if key not in tgt_attrs or not tgt_val or not _is_empty_attr_value(val.get("value")):
                tgt_attrs[key] = dict(val)
            continue

        src_value = val.get("value")
        tgt_value = tgt_val.get("value")
        src_attr_turn = val.get("source_turn", "")
        tgt_attr_turn = tgt_val.get("source_turn", "")

        # Prefer non-empty source value when target is empty or source is newer
        if not _is_empty_attr_value(src_value) and (
            _is_empty_attr_value(tgt_value) or _compare_turns(src_attr_turn, tgt_attr_turn) >= 0
        ):
            merged_attr = dict(tgt_val)
            merged_attr["value"] = src_value
            for meta_key in ("inference", "confidence", "source_turn"):
                if meta_key in val and val.get(meta_key) is not None:
                    merged_attr[meta_key] = val[meta_key]
            tgt_attrs[key] = merged_attr
        else:
            # Preserve existing value, but backfill missing provenance
            for meta_key in ("inference", "confidence", "source_turn"):
                if (
                    meta_key not in tgt_val or tgt_val.get(meta_key) is None
                ) and val.get(meta_key) is not None:
                    tgt_val[meta_key] = val[meta_key]

    # Merge relationships — update existing by target_id, append new
    src_rels = source.get("relationships", [])
    tgt_rels = target.setdefault("relationships", [])
    tgt_rel_index = {r.get("target_id"): i for i, r in enumerate(tgt_rels)}
    for rel in src_rels:
        tid = rel.get("target_id")
        if tid in tgt_rel_index:
            # Merge into existing relationship
            existing = tgt_rels[tgt_rel_index[tid]]
            rel_src_turn = rel.get("last_updated_turn", "")
            rel_tgt_turn = existing.get("last_updated_turn", "")
            if _compare_turns(rel_src_turn, rel_tgt_turn) >= 0:
                existing["current_relationship"] = rel.get(
                    "current_relationship", existing.get("current_relationship", "")
                )
                existing["last_updated_turn"] = rel_src_turn or rel_tgt_turn
            # Merge history entries
            src_history = rel.get("history", [])
            tgt_history = existing.setdefault("history", [])
            existing_descs = {(h.get("turn"), h.get("description")) for h in tgt_history}
            for h in src_history:
                if (h.get("turn"), h.get("description")) not in existing_descs:
                    tgt_history.append(h)
        else:
            tgt_rels.append(rel)
            tgt_rel_index[tid] = len(tgt_rels) - 1

    # Replace stub notes with real data
    if "stub" in target.get("notes", "").lower() and "stub" not in source.get("notes", "").lower():
        target["notes"] = source.get("notes", "")


def _dedup_events(events):
    """Remove duplicate events across segments."""
    seen = set()
    unique = []
    for event in events:
        # Key: first source_turn + normalized description
        key_turn = event.get("source_turns", [""])[0]
        key_desc = event.get("description", "")[:100].strip().lower()
        key = (key_turn, key_desc)
        if key not in seen:
            seen.add(key)
            unique.append(event)
    return unique


def _reconcile_segments(segments):
    """Merge catalogs and events from multiple extraction segments."""
    merged_catalogs = {fn: [] for fn in CATALOG_KEYS}
    merged_events = []

    # Entity reconciliation: match across segments by ID and name
    entity_map = {}  # canonical_id -> merged entity
    id_aliases = {}  # segment_id -> canonical_id

    for seg in segments:
        for filename, entities in seg["catalogs"].items():
            for entity in entities:
                eid = entity["id"]
                ename = entity.get("name", "").lower()

                # Check for existing entity by ID or name
                canonical = _find_canonical(eid, ename, entity_map, id_aliases)

                if canonical:
                    # Merge into existing entity
                    _merge_entity_across_segments(entity_map[canonical], entity)
                    if eid != canonical:
                        id_aliases[eid] = canonical
                else:
                    # New entity — add to map, record which catalog file it belongs to
                    entry = entity.copy()
                    entry["_catalog_file"] = filename
                    entity_map[eid] = entry

        # Accumulate events, rewriting entity IDs through alias map
        for event in seg["events"]:
            event_copy = event.copy()
            # Rewrite related_entities through alias map
            if "related_entities" in event_copy:
                event_copy["related_entities"] = [
                    id_aliases.get(eid, eid) for eid in event_copy["related_entities"]
                ]
            merged_events.append(event_copy)

    # Rewrite relationship target_ids through alias map in merged entities
    if id_aliases:
        for entity in entity_map.values():
            for rel in entity.get("relationships", []):
                tid = rel.get("target_id")
                if tid and tid in id_aliases:
                    rel["target_id"] = id_aliases[tid]
                sid = rel.get("source_id")
                if sid and sid in id_aliases:
                    rel["source_id"] = id_aliases[sid]

    # Distribute merged entities back into catalog structure
    for eid, entity in entity_map.items():
        target_file = entity.pop("_catalog_file", None)
        if not target_file:
            etype = entity.get("type", "character")
            target_file = TYPE_TO_CATALOG.get(etype, "characters.json")
        merged_catalogs[target_file].append(entity)

    # Deduplicate events by (source_turn, description hash)
    merged_events = _dedup_events(merged_events)

    # Re-sort events by source_turn (numeric, not lexicographic)
    merged_events.sort(
        key=lambda e: (
            _parse_turn_number(e.get("source_turns", [""])[0])
            if _parse_turn_number(e.get("source_turns", [""])[0]) is not None
            else float("inf")
        )
    )

    return merged_catalogs, merged_events


def _print_retry_stats(llm) -> None:
    """Print API retry statistics from the LLM client (#215)."""
    stats = getattr(llm, "stats", None)
    if stats is None or not hasattr(stats, "summary"):
        return
    summary = stats.summary()
    if summary["total_requests"] == 0:
        return
    total = summary["total_requests"]
    ok = summary["successful_requests"]
    retried = summary["retried_requests"]
    errors = summary["error_breakdown"]
    error_total = sum(errors.values())
    if error_total > 0:
        error_rate = error_total / total * 100
        parts = [f"{k}: {v}" for k, v in sorted(errors.items(), key=lambda x: -x[1])]
        print(f"\n  API stats: {total} requests, {ok} succeeded, "
              f"{error_total} errors ({error_rate:.1f}%), {retried} retried")
        print(f"  Error breakdown: {', '.join(parts)}")
        if summary["retry_after_headers_seen"]:
            print(f"  Retry-After headers seen: {summary['retry_after_headers_seen']}")
        if summary["max_consecutive_rate_limits"] > 0:
            print(f"  Max consecutive rate limits: {summary['max_consecutive_rate_limits']}")
    else:
        print(f"\n  API stats: {total} requests, all succeeded")


def _extract_segmented(
    turn_dicts, session_dir, framework_dir, catalog_dir,
    llm, min_confidence, dry_run, segment_size,
):
    """Extract in segments with fresh catalogs, then reconcile."""
    segments = []
    total = len(turn_dicts)
    progress_file = os.path.join(session_dir, "derived", "extraction-progress.json")
    extraction_log_path = os.path.join(framework_dir, "extraction-log.jsonl")
    quota_exhausted = False

    for start in range(0, total, segment_size):
        end = min(start + segment_size, total)
        segment_turns = turn_dicts[start:end]
        segment_id = f"segment-{start // segment_size + 1}"

        print(
            f"\n  === {segment_id}: turns {segment_turns[0]['turn_id']}"
            f" \u2013 {segment_turns[-1]['turn_id']} ({len(segment_turns)} turns) ==="
        )

        # Fresh catalog for each segment
        seg_catalogs = {fn: [] for fn in CATALOG_KEYS}
        seg_events = []

        # Pre-seed player character (always present)
        _ensure_player_character(seg_catalogs, segment_turns[0]["turn_id"])

        # Pre-flight context check for first segment only (#222)
        if start == 0:
            seg_entity_count = sum(len(v) for v in seg_catalogs.values())
            preflight_context_check(
                turn_count=total,
                context_length=llm.context_length,
                max_tokens=llm.max_tokens,
                existing_entity_count=seg_entity_count,
                segment_size=segment_size,
                model=llm.model,
            )

        # Entity refresh config (#161)
        _seg_refresh_cfg = getattr(llm, "config", None) or {}
        seg_refresh_interval = _seg_refresh_cfg.get("entity_refresh_interval", _DEFAULT_REFRESH_INTERVAL) if isinstance(_seg_refresh_cfg, dict) else _DEFAULT_REFRESH_INTERVAL
        seg_refresh_batch = _seg_refresh_cfg.get("entity_refresh_batch_size", _DEFAULT_REFRESH_BATCH_SIZE) if isinstance(_seg_refresh_cfg, dict) else _DEFAULT_REFRESH_BATCH_SIZE
        seg_checkpoint_interval = _read_checkpoint_interval(_seg_refresh_cfg)

        seg_failed_turns: list[str] = []

        # Process this segment's turns
        for i, turn in enumerate(segment_turns):
            turn_id = turn["turn_id"]

            if i % 25 == 0 and i > 0:
                entities_now = sum(len(v) for v in seg_catalogs.values())
                print(f"  ... {turn_id} ({start + i + 1}/{total}, {entities_now} entities)")

            try:
                seg_catalogs, seg_events, seg_turn_failed, seg_turn_log = extract_and_merge(
                    turn, seg_catalogs, seg_events, llm, min_confidence,
                    catalog_dir=None,
                )
            except QuotaExhaustedError as e:
                print(f"\n  QUOTA EXHAUSTED at {turn_id}: {e}", file=sys.stderr)
                print(f"  Stopping extraction to preserve quota.", file=sys.stderr)
                seg_failed_turns.append(turn_id)
                # Mark remaining turns in this segment as failed
                for j in range(i + 1, len(segment_turns)):
                    seg_failed_turns.append(segment_turns[j]["turn_id"])
                if not dry_run:
                    _write_extraction_log(extraction_log_path, {
                        "turn_id": turn_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "discovery_ok": False, "discovery_error": f"QuotaExhaustedError: {e}",
                        "detail_ok": False, "detail_error": None,
                        "pc_ok": False, "pc_error": None,
                        "relationships_ok": False, "relationships_error": None,
                        "events_ok": False, "events_error": None,
                        "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
                    })
                quota_exhausted = True
                break
            except Exception as e:
                print(f"  ERROR at {turn_id}: {e}", file=sys.stderr)
                seg_turn_failed = True
                seg_turn_log = {
                    "turn_id": turn_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "discovery_ok": False, "discovery_error": str(e),
                    "detail_ok": False, "detail_error": None,
                    "pc_ok": False, "pc_error": None,
                    "relationships_ok": False, "relationships_error": None,
                    "events_ok": False, "events_error": None,
                    "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
                }

            # Write per-turn extraction log (#217)
            if not dry_run:
                _write_extraction_log(extraction_log_path, seg_turn_log)

            if seg_turn_failed:
                seg_failed_turns.append(turn_id)
                continue

            # --- Entity refresh pass (#161) ---
            seg_turn_number = _parse_turn_number(turn_id)
            if (
                seg_refresh_interval > 0
                and seg_turn_number is not None
                and seg_turn_number % seg_refresh_interval == 0
            ):
                stale = find_stale_entities(
                    seg_turn_number, seg_catalogs, segment_turns[:i + 1],
                    refresh_interval=seg_refresh_interval,
                    batch_size=seg_refresh_batch,
                    events_list=seg_events,
                )
                if stale:
                    print(f"  REFRESH: {len(stale)} stale entity/entities at {turn_id}")
                    refreshed = refresh_entities(stale, turn_id,
                                                 segment_turns[:i + 1],
                                                 seg_catalogs, llm)
                    if refreshed:
                        print(f"  REFRESH: Successfully refreshed {refreshed} entity/entities")

            # Intra-segment checkpoint: persist catalogs periodically (#220)
            if (i + 1) % seg_checkpoint_interval == 0 and not dry_run:
                interim = segments + [{
                    "id": f"{segment_id}-partial",
                    "catalogs": seg_catalogs,
                    "events": seg_events,
                    "turn_range": (segment_turns[0]["turn_id"], turn_id),
                }]
                interim_catalogs, interim_events = _reconcile_segments(interim)
                _save_progress(
                    progress_file, turn_id, total, interim_catalogs, dry_run,
                    metadata={"segment": segment_id, "mode": "segmented",
                              "completed": False},
                )
                save_catalogs(catalog_dir, interim_catalogs)
                save_events(catalog_dir, interim_events)

        # --- Final entity refresh for segment — catch entities stale since last modulo checkpoint (#212) ---
        # Skip refresh if quota was exhausted — no further LLM calls (#215)
        if not quota_exhausted and seg_refresh_interval > 0 and segment_turns:
            seg_final_number = _parse_turn_number(segment_turns[-1]["turn_id"])
            if seg_final_number is not None and (seg_final_number % seg_refresh_interval != 0):
                seg_final_batch = max(seg_refresh_batch, _MAX_REFRESH_BATCH_SIZE)
                seg_final_stale = find_stale_entities(
                    current_turn_number=seg_final_number,
                    catalogs=seg_catalogs,
                    turn_dicts=segment_turns,
                    refresh_interval=seg_refresh_interval,
                    batch_size=seg_final_batch,
                    events_list=seg_events,
                )
                if seg_final_stale:
                    print(f"  REFRESH: {len(seg_final_stale)} stale entity/entities at end of {segment_id}")
                    refreshed = refresh_entities(seg_final_stale, segment_turns[-1]["turn_id"],
                                                 segment_turns, seg_catalogs, llm)
                    if refreshed:
                        print(f"  REFRESH: Successfully refreshed {refreshed} entity/entities at end of {segment_id}")

        seg_entity_count = sum(len(v) for v in seg_catalogs.values())
        print(f"  {segment_id} complete: {seg_entity_count} entities, {len(seg_events)} events")
        if seg_failed_turns:
            print(f"  {segment_id}: {len(seg_failed_turns)} turn(s) had extraction failures", file=sys.stderr)

        segments.append({
            "id": segment_id,
            "catalogs": seg_catalogs,
            "events": seg_events,
            "turn_range": (segment_turns[0]["turn_id"], segment_turns[-1]["turn_id"]),
            "failed_turns": seg_failed_turns,
        })

        # Save checkpoint after each segment — use separate progress key
        # so an interrupted segmented run cannot confuse the legacy resume logic
        _save_progress(progress_file, segment_turns[-1]["turn_id"], total,
                       seg_catalogs, dry_run,
                       metadata={"segment": segment_id, "mode": "segmented",
                                 "completed": False})

        # Persist reconciled catalogs after each completed segment (#220)
        # so interrupted runs retain entities from all finished segments.
        if not dry_run:
            interim_catalogs, interim_events = _reconcile_segments(segments)
            save_catalogs(catalog_dir, interim_catalogs)
            save_events(catalog_dir, interim_events)

        # Stop processing further segments if quota was exhausted (#215)
        if quota_exhausted:
            # Mark all turns in remaining segments as failed
            for future_start in range(end, total, segment_size):
                future_end = min(future_start + segment_size, total)
                for t in turn_dicts[future_start:future_end]:
                    # Append to the last segment's failed list for reporting
                    seg_failed_turns.append(t["turn_id"])
            # Update the segment record with any additional failed turns
            segments[-1]["failed_turns"] = seg_failed_turns
            break

    # Reconcile all segments into a single catalog
    print(f"\n  === Reconciliation: merging {len(segments)} segments ===")
    final_catalogs, final_events = _reconcile_segments(segments)

    # Run standard post-batch passes on the reconciled result
    dupes_merged, merge_map = _dedup_catalogs(final_catalogs)
    if dupes_merged:
        _rewrite_stale_ids(final_catalogs, final_events, merge_map)
        print(f"  Post-reconciliation dedup merged {dupes_merged} duplicate(s)")

    # Clean up dangling relationship targets (#184)
    dangling_removed = cleanup_dangling_relationships(final_catalogs)
    if dangling_removed:
        total_removed = sum(len(v) for v in dangling_removed.values())
        print(f"  Removed {total_removed} dangling relationship target(s)")

    orphan_stubs = _post_batch_orphan_sweep(final_catalogs, final_events)
    if orphan_stubs:
        print(f"  Post-reconciliation orphan sweep: {orphan_stubs} stub(s)")

    # Name-mention discovery (#185) — catch named characters missed by LLM
    name_stubs = _name_mention_discovery(final_catalogs, final_events)
    if name_stubs:
        print(f"  Post-reconciliation name-mention discovery: {name_stubs} stub(s)")

    entities_final = sum(len(v) for v in final_catalogs.values())

    # Collect all failed turns across segments (#211)
    all_failed_turns = []
    for seg in segments:
        all_failed_turns.extend(seg.get("failed_turns", []))
    if all_failed_turns:
        print(f"\n  WARNING: {len(all_failed_turns)} turn(s) had extraction failures:", file=sys.stderr)
        for tid in all_failed_turns:
            print(f"    - {tid}", file=sys.stderr)
        print(f"  These turns should be re-extracted when the LLM is available.", file=sys.stderr)

    print(f"  Segmented extraction complete: {entities_final} entities, {len(final_events)} events")

    # Report API retry statistics (#215)
    _print_retry_stats(llm)

    if not dry_run:
        save_catalogs(catalog_dir, final_catalogs)
        save_events(catalog_dir, final_events)
        _save_progress(progress_file, turn_dicts[-1]["turn_id"] if turn_dicts else "",
                       total, final_catalogs, dry_run=False, completed=True,
                       metadata={"failed_turns": all_failed_turns} if all_failed_turns else None)


# ---------------------------------------------------------------------------
# Pre-flight context window sufficiency check (#222)
# ---------------------------------------------------------------------------

# Estimation constants — intentionally conservative (over-estimate usage)
_ENTITY_TOKENS = 30          # Avg tokens per entity line in discovery prompt
_ENTITY_GROWTH_RATE = 0.4    # Avg new entities discovered per turn
_TURN_TEXT_TOKENS = 300      # Avg tokens for a typical DM turn text
_TEMPLATE_OVERHEAD_TOKENS = 2000  # System prompt template tokens (discovery is ~1900)
_PROMPT_FRAMING_TOKENS = 100     # Tokens for the user prompt structure (headers, etc.)


def estimate_peak_context(
    turn_count: int,
    context_length: int | None,
    max_tokens: int,
    existing_entity_count: int = 0,
    segment_size: int = 0,
) -> dict:
    """Estimate peak context window usage for an extraction run.

    Returns a dict with estimation details:
        - ``estimated_peak_tokens``: projected worst-case token usage
        - ``context_length``: the configured context window
        - ``sufficient``: whether the context window appears sufficient
        - ``headroom_pct``: percentage of context window remaining at peak
        - ``projected_entity_count``: estimated entity count at peak
        - ``warnings``: list of human-readable warning strings
        - ``suggestions``: list of actionable mitigation suggestions
    """
    if context_length is None or not isinstance(context_length, (int, float)):
        return {
            "estimated_peak_tokens": 0,
            "context_length": None,
            "sufficient": True,
            "headroom_pct": 100.0,
            "projected_entity_count": existing_entity_count,
            "warnings": [],
            "suggestions": [],
        }

    # When segmentation is enabled, entities accumulate only within each segment
    effective_turns = segment_size if segment_size > 0 else turn_count
    projected_entities = existing_entity_count + math.ceil(effective_turns * _ENTITY_GROWTH_RATE)

    # Peak demand: system template + entity roster + turn text + output reserve
    entity_tokens = projected_entities * _ENTITY_TOKENS
    peak_input = (
        _TEMPLATE_OVERHEAD_TOKENS
        + _PROMPT_FRAMING_TOKENS
        + entity_tokens
        + _TURN_TEXT_TOKENS
    )
    estimated_peak = peak_input + max_tokens

    headroom = context_length - estimated_peak
    headroom_pct = (headroom / context_length * 100) if context_length > 0 else 0.0

    warnings = []
    suggestions = []
    sufficient = True

    if headroom_pct < 0:
        sufficient = False
        warnings.append(
            f"Estimated peak usage ({estimated_peak:,} tokens) exceeds "
            f"context window ({context_length:,} tokens) by "
            f"{-headroom:,} tokens."
        )
        warnings.append(
            f"Projected ~{projected_entities} entities after "
            f"{effective_turns} turns would consume ~{entity_tokens:,} "
            f"tokens in the discovery prompt alone."
        )
    elif headroom_pct < 15:
        warnings.append(
            f"Tight context budget: estimated peak {estimated_peak:,} tokens "
            f"leaves only {headroom_pct:.0f}% headroom in "
            f"{context_length:,}-token window."
        )

    if not sufficient or headroom_pct < 15:
        if context_length < 32768:
            suggestions.append(
                "Increase context_length in config/llm.json (32K+ recommended "
                "for sessions over 100 turns)."
            )
        if segment_size == 0 and turn_count > 100:
            suggestions.append(
                "Enable segmented extraction (--segment-size 100) to limit "
                "entity accumulation per segment."
            )
        if not sufficient:
            suggestions.append(
                "Use a model with a larger context window, or reduce the "
                "session size."
            )

    return {
        "estimated_peak_tokens": estimated_peak,
        "context_length": context_length,
        "sufficient": sufficient,
        "headroom_pct": round(headroom_pct, 1),
        "projected_entity_count": projected_entities,
        "warnings": warnings,
        "suggestions": suggestions,
    }


def preflight_context_check(
    turn_count: int,
    context_length: int | None,
    max_tokens: int,
    existing_entity_count: int = 0,
    segment_size: int = 0,
    model: str = "",
) -> dict:
    """Run a pre-flight context window sufficiency check.

    Prints warnings to stderr if the configuration looks insufficient.
    Returns the estimation dict from :func:`estimate_peak_context`.
    """
    result = estimate_peak_context(
        turn_count=turn_count,
        context_length=context_length,
        max_tokens=max_tokens,
        existing_entity_count=existing_entity_count,
        segment_size=segment_size,
    )

    if result["warnings"]:
        model_info = f" (model: {model})" if model else ""
        print(
            f"\n  === Pre-flight Context Check{model_info} ===",
            file=sys.stderr,
        )
        for w in result["warnings"]:
            print(f"  WARNING: {w}", file=sys.stderr)
        if result["suggestions"]:
            print("  Suggestions:", file=sys.stderr)
            for s in result["suggestions"]:
                print(f"    - {s}", file=sys.stderr)
        if not result["sufficient"]:
            print(
                "  The extraction will proceed, but quality may degrade as "
                "context fills up.",
                file=sys.stderr,
            )
        print(file=sys.stderr)

    return result


def extract_semantic_batch(
    turn_dicts: list,
    session_dir: str,
    framework_dir: str = "framework",
    config_path: str = "config/llm.json",
    dry_run: bool = False,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    overrides: dict | None = None,
    segment_size: int = 0,
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
        segment_size: Extract in segments of N turns with fresh catalogs, then
            reconcile. 0 = no segmentation (legacy behavior).
    """
    _reset_pc_failure_tracking()

    try:
        llm = LLMClient(config_path, overrides=overrides)
    except (ImportError, LLMExtractionError, FileNotFoundError) as e:
        print(f"  WARNING: Semantic extraction not available: {e}", file=sys.stderr)
        return

    catalog_dir = os.path.join(framework_dir, "catalogs")
    extraction_log_path = os.path.join(framework_dir, "extraction-log.jsonl")

    # Segmented extraction: process in chunks with fresh catalogs, then reconcile
    if segment_size > 0 and len(turn_dicts) > segment_size:
        _extract_segmented(
            turn_dicts, session_dir, framework_dir, catalog_dir,
            llm, min_confidence, dry_run, segment_size,
        )
        return

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
            # Ignore incomplete segmented checkpoints — catalogs won't be on disk
            if progress.get("mode") == "segmented" and not progress.get("completed"):
                pass
            else:
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
    failed_turns: list[str] = []
    quota_exhausted = False

    # Re-attempt turns that failed in a previous run (e.g. due to quota exhaustion)
    previously_failed = []
    if os.path.exists(progress_file) and start_from > 0:
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                prev_progress = json.load(f)
            previously_failed = prev_progress.get("failed_turns", [])
            if previously_failed:
                print(f"  Re-attempting {len(previously_failed)} previously failed turn(s)...")
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(
                f"  WARNING: Could not load previously failed turns from progress file: {e}. "
                "Continuing without retry list.",
                file=sys.stderr,
            )
            previously_failed = []

    print(f"  Processing {total - start_from} turns for semantic extraction...")

    # Pre-flight context window sufficiency check (#222)
    existing_entity_count = sum(len(ents) for ents in catalogs.values())
    preflight_context_check(
        turn_count=total - start_from,
        context_length=llm.context_length,
        max_tokens=llm.max_tokens,
        existing_entity_count=existing_entity_count,
        segment_size=segment_size,
        model=llm.model,
    )

    # Entity refresh config (#161)
    _refresh_cfg = getattr(llm, "config", None) or {}
    refresh_interval = _refresh_cfg.get("entity_refresh_interval", _DEFAULT_REFRESH_INTERVAL) if isinstance(_refresh_cfg, dict) else _DEFAULT_REFRESH_INTERVAL
    refresh_batch_size = _refresh_cfg.get("entity_refresh_batch_size", _DEFAULT_REFRESH_BATCH_SIZE) if isinstance(_refresh_cfg, dict) else _DEFAULT_REFRESH_BATCH_SIZE
    checkpoint_interval = _read_checkpoint_interval(_refresh_cfg)

    # Re-attempt previously failed turns before continuing
    if previously_failed:
        retry_map = {t["turn_id"]: t for t in turn_dicts}
        for retry_tid in previously_failed:
            retry_turn = retry_map.get(retry_tid)
            if not retry_turn:
                continue
            try:
                catalogs, events_list, retry_disc_failed, retry_log = extract_and_merge(
                    retry_turn, catalogs, events_list, llm, min_confidence,
                    catalog_dir=catalog_dir,
                )
                if not dry_run:
                    _write_extraction_log(extraction_log_path, retry_log)
                if retry_disc_failed:
                    failed_turns.append(retry_tid)
                    print(f"  Retry still failed for {retry_tid}", file=sys.stderr)
                else:
                    print(f"  Retry succeeded for {retry_tid}")
            except QuotaExhaustedError as e:
                print(f"\n  QUOTA EXHAUSTED during retry of {retry_tid}: {e}", file=sys.stderr)
                failed_turns.append(retry_tid)
                if not dry_run:
                    _write_extraction_log(extraction_log_path, {
                        "turn_id": retry_tid,
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "discovery_ok": False, "discovery_error": f"QuotaExhaustedError: {e}",
                        "detail_ok": False, "detail_error": None,
                        "pc_ok": False, "pc_error": None,
                        "relationships_ok": False, "relationships_error": None,
                        "events_ok": False, "events_error": None,
                        "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
                    })
                quota_exhausted = True
                break
            except Exception as e:
                print(f"  Retry ERROR for {retry_tid}: {e}", file=sys.stderr)
                failed_turns.append(retry_tid)

    # If quota was exhausted during retries, mark all remaining turns as failed,
    # save progress, and skip the main extraction loop entirely (#215).
    if quota_exhausted:
        for i in range(start_from, total):
            failed_turns.append(turn_dicts[i]["turn_id"])
        _save_progress(progress_file, turn_dicts[start_from - 1]["turn_id"] if start_from > 0 else "",
                       total, catalogs, dry_run,
                       metadata={"failed_turns": failed_turns} if failed_turns else None)
        if not dry_run:
            save_catalogs(catalog_dir, catalogs)
            save_events(catalog_dir, events_list)
        _print_retry_stats(llm)
        return

    for i in range(start_from, total):
        turn = turn_dicts[i]
        turn_id = turn["turn_id"]

        if (i - start_from) % 25 == 0 and i > start_from:
            entities_now = sum(len(v) for v in catalogs.values())
            print(f"  ... {turn_id} ({i + 1}/{total}, {entities_now} entities)")

        turn_failed = False
        _turn_log = None
        try:
            catalogs, events_list, turn_failed, _turn_log = extract_and_merge(
                turn, catalogs, events_list, llm, min_confidence,
                catalog_dir=catalog_dir,
            )
        except QuotaExhaustedError as e:
            print(f"\n  QUOTA EXHAUSTED at {turn_id}: {e}", file=sys.stderr)
            print(f"  Stopping extraction to preserve quota.", file=sys.stderr)
            failed_turns.append(turn_id)
            # Mark remaining turns as failed
            for j in range(i + 1, total):
                failed_turns.append(turn_dicts[j]["turn_id"])
            # Save progress so interrupted run can resume (#220)
            _save_progress(progress_file, turn_dicts[i - 1]["turn_id"] if i > 0 else "",
                           total, catalogs, dry_run,
                           metadata={"failed_turns": failed_turns} if failed_turns else None)
            if not dry_run:
                save_catalogs(catalog_dir, catalogs)
                save_events(catalog_dir, events_list)
                _write_extraction_log(extraction_log_path, {
                    "turn_id": turn_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "discovery_ok": False, "discovery_error": f"QuotaExhaustedError: {e}",
                    "detail_ok": False, "detail_error": None,
                    "pc_ok": False, "pc_error": None,
                    "relationships_ok": False, "relationships_error": None,
                    "events_ok": False, "events_error": None,
                    "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
                })
            quota_exhausted = True
            break
        except Exception as e:
            print(f"  ERROR at {turn_id}: {e}", file=sys.stderr)
            turn_failed = True
            _turn_log = {
                "turn_id": turn_id,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "discovery_ok": False, "discovery_error": str(e),
                "detail_ok": False, "detail_error": None,
                "pc_ok": False, "pc_error": None,
                "relationships_ok": False, "relationships_error": None,
                "events_ok": False, "events_error": None,
                "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
            }
            # Save progress and catalogs so entities survive interruption (#220)
            _save_progress(progress_file, turn_dicts[i - 1]["turn_id"] if i > 0 else "",
                           total, catalogs, dry_run,
                           metadata={"failed_turns": failed_turns} if failed_turns else None)
            if not dry_run:
                save_catalogs(catalog_dir, catalogs)
                save_events(catalog_dir, events_list)

        # Write per-turn extraction log (#217)
        if _turn_log and not dry_run:
            _write_extraction_log(extraction_log_path, _turn_log)

        if turn_failed:
            failed_turns.append(turn_id)
            continue

        # --- Entity refresh pass (#161) ---
        current_turn_number = _parse_turn_number(turn_id)
        if (
            refresh_interval > 0
            and current_turn_number is not None
            and current_turn_number % refresh_interval == 0
        ):
            stale = find_stale_entities(
                current_turn_number, catalogs, turn_dicts[:i + 1],
                refresh_interval=refresh_interval,
                batch_size=refresh_batch_size,
                events_list=events_list,
            )
            if stale:
                print(f"  REFRESH: {len(stale)} stale entity/entities at {turn_id}")
                refreshed = refresh_entities(stale, turn_id, turn_dicts[:i + 1],
                                             catalogs, llm)
                if refreshed:
                    print(f"  REFRESH: Successfully refreshed {refreshed} entity/entities")

        # Checkpoint every N turns (configurable via config/llm.json checkpoint_interval, default 25)
        if (i + 1) % checkpoint_interval == 0:
            _save_progress(progress_file, turn_id, total, catalogs, dry_run,
                           metadata={"failed_turns": failed_turns} if failed_turns else None)
            if not dry_run:
                save_catalogs(catalog_dir, catalogs)
                save_events(catalog_dir, events_list)

    # --- Final entity refresh — catch entities stale since last modulo checkpoint (#212) ---
    # Skip refresh if quota was exhausted — no further LLM calls (#215)
    if not quota_exhausted and refresh_interval > 0 and turn_dicts:
        final_turn_number = _parse_turn_number(turn_dicts[-1]["turn_id"])
        if final_turn_number is not None and (final_turn_number % refresh_interval != 0):
            final_batch = max(refresh_batch_size, _MAX_REFRESH_BATCH_SIZE)
            final_stale = find_stale_entities(
                current_turn_number=final_turn_number,
                catalogs=catalogs,
                turn_dicts=turn_dicts,
                refresh_interval=refresh_interval,
                batch_size=final_batch,
                events_list=events_list,
            )
            if final_stale:
                print(f"\n  REFRESH: {len(final_stale)} stale entity/entities at end-of-run (after {turn_dicts[-1]['turn_id']})")
                refreshed = refresh_entities(final_stale, turn_dicts[-1]["turn_id"],
                                             turn_dicts, catalogs, llm)
                if refreshed:
                    print(f"  REFRESH: Successfully refreshed {refreshed} entity/entities at end-of-run")
            else:
                print("\n  REFRESH: No stale entities at end-of-run")

    # Final save
    entities_after = sum(len(v) for v in catalogs.values())
    events_after = len(events_list)

    # Report failed turns (#211)
    if failed_turns:
        print(f"\n  WARNING: {len(failed_turns)} turn(s) had extraction failures:", file=sys.stderr)
        for tid in failed_turns:
            print(f"    - {tid}", file=sys.stderr)
        print(f"  These turns should be re-extracted when the LLM is available.", file=sys.stderr)

    # Post-batch dedup: merge entities that share the same name/aliases but got separate IDs
    dupes_merged, merge_map = _dedup_catalogs(catalogs)
    if dupes_merged:
        _rewrite_stale_ids(catalogs, events_list, merge_map)
        entities_after = sum(len(v) for v in catalogs.values())
        print(f"  Post-batch dedup merged {dupes_merged} duplicate(s); {entities_after} entities remain")

    # Clean up dangling relationship targets (#184)
    dangling_removed = cleanup_dangling_relationships(catalogs)
    if dangling_removed:
        total_removed = sum(len(v) for v in dangling_removed.values())
        print(f"  Removed {total_removed} dangling relationship target(s) from {len(dangling_removed)} entit(ies)")

    # Apply coreference hints (manual merge rules, #162)
    hints_path = os.path.join(session_dir, "coreference-hints.json")
    if os.path.isfile(hints_path):
        coref_merged = apply_coreference_hints(catalogs, events_list, catalog_dir, hints_path, dry_run)
        if coref_merged:
            entities_after = sum(len(v) for v in catalogs.values())
            print(f"  Coreference hints applied; {entities_after} entities remain")

    # --- Phase 4: Post-batch orphan sweep (#106) ---
    orphan_stubs = _post_batch_orphan_sweep(catalogs, events_list)
    if orphan_stubs:
        entities_after = sum(len(v) for v in catalogs.values())
        print(f"  Post-batch orphan sweep created {orphan_stubs} stub(s); {entities_after} entities now")

    # --- Phase 4b: Name-mention discovery (#185) ---
    name_stubs = _name_mention_discovery(catalogs, events_list)
    if name_stubs:
        entities_after = sum(len(v) for v in catalogs.values())
        print(f"  Name-mention discovery created {name_stubs} stub(s); {entities_after} entities now")

    # Report if PC extraction was skipped due to consecutive failures (#149)
    if _pc_skipped_turns > 0:
        print(
            f"  PC extraction skipped for {_pc_skipped_turns} turn(s) "
            f"(cooldown after {_PC_SKIP_THRESHOLD} consecutive failures, "
            f"retrying for {_PC_RETRY_WINDOW} turn(s) every "
            f"{_PC_SKIP_COOLDOWN + _PC_RETRY_WINDOW} turns)",
        )

    print(f"  Semantic extraction complete: {entities_after} entities, {events_after} events")

    # Report API retry statistics (#215)
    _print_retry_stats(llm)

    if not dry_run:
        # Skip dormancy pass in batch mode — the threshold is too small relative
        # to a full transcript and would mark all relationships dormant.  Dormancy
        # is only meaningful during incremental single-turn extraction.
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        _save_progress(progress_file, turn_dicts[-1]["turn_id"] if turn_dicts else "",
                       total, catalogs, dry_run=False, completed=True,
                       metadata={"failed_turns": failed_turns} if failed_turns else None)


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
    _reset_pc_failure_tracking()

    try:
        llm = LLMClient(config_path, overrides=overrides)
    except (ImportError, LLMExtractionError, FileNotFoundError) as e:
        print(f"  WARNING: Semantic extraction not available: {e}", file=sys.stderr)
        return

    catalog_dir = os.path.join(framework_dir, "catalogs")
    extraction_log_path = os.path.join(framework_dir, "extraction-log.jsonl")
    catalogs = load_catalogs(catalog_dir)
    events_list = load_events(catalog_dir)

    # Pre-seed the player character so it can be tracked every turn
    _ensure_player_character(catalogs, turn_id)

    turn = {"turn_id": turn_id, "speaker": speaker, "text": text}

    print(f"  Running semantic extraction for {turn_id}...")
    try:
        catalogs, events_list, _turn_failed, _turn_log = extract_and_merge(
            turn, catalogs, events_list, llm, min_confidence,
            catalog_dir=catalog_dir,
        )
    except Exception as e:
        _turn_log = {
            "turn_id": turn_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "discovery_ok": False, "discovery_error": str(e),
            "detail_ok": False, "detail_error": None,
            "pc_ok": False, "pc_error": None,
            "relationships_ok": False, "relationships_error": None,
            "events_ok": False, "events_error": None,
            "new_entities": 0, "new_events": 0, "elapsed_ms": 0,
        }
        _write_extraction_log(extraction_log_path, _turn_log)
        print(f"  ERROR at {turn_id}: {e}", file=sys.stderr)
        save_catalogs(catalog_dir, catalogs)
        save_events(catalog_dir, events_list)
        return

    _write_extraction_log(extraction_log_path, _turn_log)

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
    metadata: dict | None = None,
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
    if metadata:
        progress.update(metadata)
    os.makedirs(os.path.dirname(progress_file), exist_ok=True)
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
        f.write("\n")


def _write_extraction_log(log_path: str, record: dict) -> None:
    """Append a per-turn extraction log record to a JSONL file (#217).

    Each line is a self-contained JSON object recording the outcome of
    one turn's extraction phases, errors encountered, counts of new
    entities/events, and elapsed time.  The file is append-only so it
    survives interruptions and can be consulted post-mortem.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        # Logging must never interrupt extraction.
        pass
