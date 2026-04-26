#!/usr/bin/env python3
"""
catalog_merger.py — Merge agent-extracted entities, relationships, and events
into existing catalog files.

Uses the V2 per-entity file layout:
  framework/catalogs/characters/
    index.json            → lightweight roster (regenerated)
    char-player.json      → full entity detail
    char-elder.json       → full entity detail

Handles:
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

# Maps entity types to catalog bucket keys (keyed by legacy filenames for
# backward-compatible dict keys used throughout the pipeline).
TYPE_TO_CATALOG = {
    "character": "characters.json",
    "location": "locations.json",
    "faction": "factions.json",
    "item": "items.json",
    "creature": "characters.json",
    "concept": "items.json",
}

# Canonical catalog bucket keys (one per entity-type directory).
CATALOG_KEYS = ["characters.json", "locations.json", "factions.json", "items.json"]

# Per-entity directory names under catalogs/
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
# Unified load / save
# ---------------------------------------------------------------------------

def load_catalogs(catalog_dir: str) -> dict:
    """Load all entity catalogs into a dict keyed by catalog name.

    Reads per-entity files from V2 type directories.

    The returned dict is keyed by the canonical catalog keys
    (``characters.json``, etc.) for compatibility with callers.

    Raises a warning if stale V1 flat files with real data are found,
    since they are no longer loaded and the data would be silently ignored.
    """
    catalogs: dict[str, list[dict]] = {}

    # Guard: warn if stale V1 flat files contain data that would be lost
    for key in CATALOG_KEYS:
        flat_path = os.path.join(catalog_dir, key)
        if os.path.isfile(flat_path):
            try:
                with open(flat_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    warnings.warn(
                        f"Stale V1 flat file '{key}' contains {len(data)} "
                        f"entries that will NOT be loaded. Remove it or "
                        f"re-run the V1\u2192V2 migration.",
                        UserWarning,
                        stacklevel=2,
                    )
            except (json.JSONDecodeError, OSError):
                pass  # Unreadable flat file — not actionable; skip the guard.

    for dirname, key in zip(_V2_DIRNAMES, CATALOG_KEYS):
        entity_dir = os.path.join(catalog_dir, dirname)
        catalogs[key] = _read_v2_entities(entity_dir)
    return catalogs


def save_catalogs(catalog_dir: str, catalogs: dict, dry_run: bool = False) -> None:
    """Write all catalogs back to disk using V2 per-entity layout.

    Writes per-entity files and regenerates index.json for each type directory.
    """
    for dirname, key in zip(_V2_DIRNAMES, CATALOG_KEYS):
        entities = catalogs.get(key, [])
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
    """Format all known entities as a compact table for the discovery prompt."""
    lines = []
    for filename, entities in catalogs.items():
        for entity in entities:
            desc = entity.get("identity", "")
            aliases = ""
            sa = entity.get("stable_attributes", {}).get("aliases")
            if sa:
                val = sa.get("value", "") if isinstance(sa, dict) else sa
                if isinstance(val, list):
                    aliases = ", ".join(val)
                else:
                    aliases = str(val)
            extra = ""
            if desc:
                extra += f" — {desc}"
            if aliases:
                extra += f" (aliases: {aliases})"
            lines.append(f"{entity['id']} | {entity['name']} | {entity['type']}{extra}")
    if not lines:
        return "(none — empty catalog)"
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return max(1, len(text) // 4)


def _format_entity_full(entity: dict) -> str:
    """Format a single entity with full detail (ID, name, type, identity, aliases)."""
    desc = entity.get("identity", "")
    aliases = ""
    sa = entity.get("stable_attributes", {}).get("aliases")
    if sa:
        val = sa.get("value", "") if isinstance(sa, dict) else sa
        if isinstance(val, list):
            aliases = ", ".join(val)
        else:
            aliases = str(val)
    extra = ""
    if desc:
        extra += f" — {desc}"
    if aliases:
        extra += f" (aliases: {aliases})"
    return f"{entity['id']} | {entity['name']} | {entity['type']}{extra}"


def _format_entity_brief(entity: dict) -> str:
    """Format a single entity with minimal detail (ID, name, type only)."""
    return f"{entity['id']} | {entity['name']} | {entity['type']}"


# Default number of recent turns for which entities get full detail
_DEFAULT_RECENCY_WINDOW = 10

# Default fraction of context_length allocated to the entity list
_DEFAULT_ENTITY_BUDGET_FRACTION = 0.25


def format_known_entities_bounded(
    catalogs: dict,
    *,
    current_turn: int | None = None,
    context_length: int | None = None,
    entity_context_budget: int | None = None,
    recency_window: int | None = None,
) -> str:
    """Format known entities with a configurable token budget.

    Entities active within *recency_window* turns get full detail (identity +
    aliases).  Remaining entities get a brief format (ID | name | type).  If
    the result still exceeds the budget, dormant entities are omitted and a
    note is appended.

    When no budget constraint applies (budget is None / large enough, or there
    are few entities), this produces the same output as
    ``format_known_entities()``.

    Args:
        catalogs: Dict keyed by catalog filename → list of entity dicts.
        current_turn: The numeric turn being processed (e.g. 150 for
            ``turn-150``).  Used to determine recency.  If ``None``, all
            entities are treated as recent.
        context_length: The model's total context window in tokens.  Used to
            derive a default budget when *entity_context_budget* is not set.
        entity_context_budget: Explicit token budget for the entity section.
            Overrides the fraction-based default.
        recency_window: Number of recent turns for which entities get full
            detail.  Defaults to ``_DEFAULT_RECENCY_WINDOW``.

    Returns:
        Formatted entity list string, possibly with a truncation note.
    """
    if recency_window is None:
        recency_window = _DEFAULT_RECENCY_WINDOW

    # Derive budget
    budget: int | None = entity_context_budget
    if budget is None and context_length is not None:
        budget = int(context_length * _DEFAULT_ENTITY_BUDGET_FRACTION)

    # Flatten all entities
    all_entities: list[dict] = []
    for entities in catalogs.values():
        all_entities.extend(entities)

    if not all_entities:
        return "(none — empty catalog)"

    # If no budget constraint, fall back to unbounded format
    if budget is None:
        return format_known_entities(catalogs)

    # Fast-path: if full unbounded output fits within budget, return it
    # directly so that no entities are needlessly degraded to brief format.
    unbounded = format_known_entities(catalogs)
    if _estimate_tokens(unbounded) <= budget:
        return unbounded

    # Partition into recent vs dormant based on last_updated_turn
    recent: list[dict] = []
    dormant: list[dict] = []
    for entity in all_entities:
        turn_num = _parse_turn_number(entity.get("last_updated_turn"))
        if current_turn is None or turn_num is None:
            # If we can't determine recency, treat as recent (safe default)
            recent.append(entity)
        elif current_turn - turn_num <= recency_window:
            recent.append(entity)
        else:
            dormant.append(entity)

    # Sort dormant by last_updated_turn descending so most-recently-seen
    # are added first when budget allows
    dormant.sort(
        key=lambda e: _parse_turn_number(e.get("last_updated_turn")) or 0,
        reverse=True,
    )

    # Phase 1: Recent entities in full detail
    lines: list[str] = [_format_entity_full(e) for e in recent]
    used = _estimate_tokens("\n".join(lines)) if lines else 0

    # Phase 1b: If recent tier alone exceeds budget, degrade the oldest
    # recent entities to brief format until we fit.
    if used > budget and len(recent) > 1:
        # Sort recent by recency (most recent first) so we keep detailed
        # entries for the newest entities and degrade older ones.
        indexed = sorted(
            range(len(recent)),
            key=lambda i: _parse_turn_number(
                recent[i].get("last_updated_turn")) or 0,
            reverse=True,
        )
        # Rebuild lines: try full first, degrade from oldest-recent inward
        lines = [_format_entity_full(e) for e in recent]
        for idx in reversed(indexed):  # oldest-recent first
            lines[idx] = _format_entity_brief(recent[idx])
            used = _estimate_tokens("\n".join(lines))
            if used <= budget:
                break

    # Phase 2: Add dormant entities in brief format while within budget
    omitted = 0
    for entity in dormant:
        line = _format_entity_brief(entity)
        line_cost = _estimate_tokens(line + "\n")
        if used + line_cost <= budget:
            lines.append(line)
            used += line_cost
        else:
            omitted += 1

    result = "\n".join(lines)

    if omitted > 0:
        note = (
            f"\n\n(Note: {omitted} additional entities exist in the catalog "
            f"but are not shown due to context limits. If a mention in the "
            f"turn text might refer to an unlisted entity, mark it as is_new "
            f"and the system will resolve duplicates.)"
        )
        result += note

    return result


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
# Entity ID normalization
# ---------------------------------------------------------------------------

# Known type prefixes in canonical order for stripping during normalization
_ALL_PREFIXES = sorted(TYPE_TO_PREFIX.values(), key=len, reverse=True)


def _strip_any_prefix(entity_id: str) -> str:
    """Strip any known type prefix from an entity ID, returning the name stem."""
    for prefix in _ALL_PREFIXES:
        if entity_id.startswith(prefix):
            return entity_id[len(prefix):]
    # Also strip unknown single-word prefixes (e.g. "entity-", "npc-")
    m = re.match(r'^[a-z]+-', entity_id)
    if m:
        return entity_id[m.end():]
    return entity_id


def _infer_type_from_prefix(entity_id: str) -> str:
    """Infer the entity type from the prefix of an ID string."""
    for etype, prefix in TYPE_TO_PREFIX.items():
        if entity_id.startswith(prefix):
            return etype
    return "character"  # default assumption for unknown prefixes


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,
                prev_row[j + 1] + 1,
                prev_row[j] + cost,
            ))
        prev_row = curr_row
    return prev_row[-1]


def normalize_entity_id(raw_id: str, known_ids: set[str]) -> str:
    """Normalize a raw entity ID against a set of known catalog IDs.

    Steps:
    1. Lowercase the ID.
    2. Validate/fix the type prefix using ``fix_id_prefix()`` logic.
    3. Attempt exact match against known IDs.
    4. Fuzzy-match against known IDs (Levenshtein distance ≤ 2 OR token overlap).
    5. Return the canonical ID if a match is found, the normalized ID otherwise.
    """
    if not raw_id:
        return raw_id

    # Step 1: lowercase
    normalized = raw_id.lower()

    # Step 2: fix prefix — infer type from the prefix, then validate
    inferred_type = _infer_type_from_prefix(normalized)
    if not validate_id_prefix(normalized, inferred_type):
        normalized = fix_id_prefix(normalized, inferred_type)

    # Step 3: exact match after lowercasing
    if normalized in known_ids:
        return normalized

    # Also check if any known ID matches case-insensitively (handles
    # known_ids that were already lowercased differently)
    known_lower = {kid.lower(): kid for kid in known_ids}
    if normalized in known_lower:
        return known_lower[normalized]

    # Step 4: fuzzy match against known IDs
    norm_stem = _strip_any_prefix(normalized)
    if not norm_stem:
        return normalized

    best_match: str | None = None
    best_distance = 999

    for kid in known_ids:
        kid_stem = _strip_any_prefix(kid.lower())
        if not kid_stem:
            continue

        # 4a: Levenshtein distance on name stems
        dist = _levenshtein(norm_stem, kid_stem)
        if dist <= 2 and dist < best_distance:
            # Additional guard: require stems to share the same first character
            # and be similar length to avoid false positives
            if norm_stem[0] == kid_stem[0] and abs(len(norm_stem) - len(kid_stem)) <= 2:
                best_distance = dist
                best_match = kid

        # 4b: Token overlap — if the normalized stem's tokens are a superset
        # or subset of a known ID's tokens, it's likely the same entity
        norm_tokens = set(norm_stem.split("-"))
        kid_tokens = set(kid_stem.split("-"))
        if norm_tokens and kid_tokens:
            overlap = norm_tokens & kid_tokens
            smaller = min(len(norm_tokens), len(kid_tokens))
            # Require full overlap of the smaller set, and smaller set >= 1 token
            if smaller >= 1 and len(overlap) == smaller:
                # Prefer the shorter (simpler) known ID
                if best_match is None or len(kid) < len(best_match):
                    best_match = kid
                    best_distance = 0  # token match is high confidence

    if best_match is not None:
        return best_match

    return normalized


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

    # Map type to the catalog bucket key
    catalog_file = TYPE_TO_CATALOG.get(entity_type)
    if not catalog_file or catalog_file not in catalogs:
        return

    # Check if entity already exists
    existing = None
    for i, e in enumerate(catalogs[catalog_file]):
        if e.get("id") == entity_id:
            existing = (i, e)
            break

    # Guard: repair empty first_seen_turn before merge (#241)
    fst = entity.get("first_seen_turn")
    if not fst or (isinstance(fst, str) and not re.match(r"^turn-[0-9]{3,}$", fst)):
        fallback = entity.get("last_updated_turn", "")
        if fallback and re.match(r"^turn-[0-9]{3,}$", fallback):
            entity["first_seen_turn"] = fallback
        else:
            entity.pop("first_seen_turn", None)

    if existing is not None:
        idx, current = existing
        _update_existing_entity(current, entity)
        catalogs[catalog_file][idx] = current
    else:
        # Ensure required fields for new entity
        required_base = ["id", "name", "type", "first_seen_turn"]
        has_desc = entity.get("identity")
        if all(entity.get(f) for f in required_base) and has_desc:
            catalogs[catalog_file].append(entity)
        else:
            missing = [f for f in required_base if not entity.get(f)]
            if not has_desc:
                missing.append("identity")
            print(f"  WARNING: New entity '{entity_id}' missing required fields: {missing}. Skipping.")


def _update_existing_entity(current: dict, update: dict) -> None:
    """Update an existing entity with new information."""
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

    # Handle name changes / aliases via stable_attributes.aliases
    if update.get("name") and update["name"] != current.get("name"):
        old_name = current["name"]
        if "stable_attributes" not in current:
            current["stable_attributes"] = {}
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

    # Final safety dedup — collapse any lingering duplicates (#183)
    if current.get("relationships"):
        current["relationships"] = _dedup_relationships(current["relationships"])


# Public alias for cross-module use (e.g. post-batch dedup in semantic_extraction)
dedup_merge_entity = _update_existing_entity


# ---------------------------------------------------------------------------
# Relationship dedup / dangling cleanup (#183, #184)
# ---------------------------------------------------------------------------

def _dedup_relationships(relationships: list) -> list:
    """Consolidate duplicate relationship entries by target_id.

    If multiple entries share the same target_id, keep the one with the
    latest last_updated_turn, preserve the earliest first_seen_turn, and
    merge history arrays (deduplicated by (turn, description) pair).
    """
    seen: dict[str, dict] = {}
    for rel in relationships:
        tid = rel.get("target_id", "")
        if tid in seen:
            existing = seen[tid]
            if (_parse_turn_number(rel.get("last_updated_turn"))
                    or 0) > (_parse_turn_number(existing.get("last_updated_turn"))
                             or 0):
                winner, loser = rel, existing
            else:
                winner, loser = existing, rel

            # Preserve the earliest first_seen_turn
            w_first = _parse_turn_number(winner.get("first_seen_turn"))
            l_first = _parse_turn_number(loser.get("first_seen_turn"))
            if l_first is not None and (w_first is None or l_first < w_first):
                winner["first_seen_turn"] = loser["first_seen_turn"]

            # Merge and deduplicate history by (turn, description)
            merged_history = list(winner.get("history", []))
            existing_keys = {
                (h.get("turn"), h.get("description")) for h in merged_history
            }
            for h in loser.get("history", []):
                key = (h.get("turn"), h.get("description"))
                if key not in existing_keys:
                    merged_history.append(h)
                    existing_keys.add(key)
            # Sort chronologically
            merged_history.sort(
                key=lambda h: _parse_turn_number(h.get("turn")) or 0
            )
            if merged_history:
                winner["history"] = merged_history

            seen[tid] = winner
        else:
            seen[tid] = rel
    return list(seen.values())


def cleanup_dangling_relationships(catalogs: dict) -> dict[str, list[str]]:
    """Remove relationships whose target_id doesn't exist in any catalog.

    Returns a dict of entity_id -> list of removed target_ids for logging.
    """
    known_ids: set[str] = set()
    for _filename, entities in catalogs.items():
        for entity in entities:
            known_ids.add(entity.get("id", ""))

    removed: dict[str, list[str]] = {}
    for _filename, entities in catalogs.items():
        for entity in entities:
            eid = entity.get("id", "")
            rels = entity.get("relationships", [])
            clean = []
            for rel in rels:
                tid = rel.get("target_id", "")
                if tid in known_ids:
                    clean.append(rel)
                else:
                    removed.setdefault(eid, []).append(tid)
            if len(clean) != len(rels):
                entity["relationships"] = clean

    return removed


# ---------------------------------------------------------------------------
# Relationship consolidation
# ---------------------------------------------------------------------------

_RELATIONSHIP_TYPE_MAP = {
    # Schema enum identity mappings (pass-through)
    "kinship": "kinship", "partnership": "partnership", "mentorship": "mentorship",
    "political": "political", "factional": "factional", "social": "social",
    "adversarial": "adversarial", "romantic": "romantic", "other": "other",
    # social
    "ally": "social", "ally_of": "social", "ally of": "social",
    "friend": "social", "companion": "social", "supporter": "social",
    "supports": "social",
    "collaborating": "partnership", "collaborating with": "partnership",
    "collaborator": "partnership",
    # adversarial
    "captive": "adversarial", "captive of": "adversarial",
    "prisoner": "adversarial", "captor": "adversarial",
    "rival": "adversarial", "competitor": "adversarial",
    "enemy": "adversarial", "caught by": "adversarial",
    "ensnared by": "adversarial",
    # mentorship
    "mentor": "mentorship", "teacher": "mentorship",
    "student": "mentorship", "apprentice": "mentorship",
    "teaching": "mentorship",
    # kinship
    "family": "kinship", "parent": "kinship", "child": "kinship",
    "sibling": "kinship", "kin": "kinship", "mother": "kinship",
    "father": "kinship", "daughter": "kinship", "son": "kinship",
    # partnership
    "cooperating": "partnership", "partner": "partnership",
    "working with": "partnership",
    # romantic
    "lover": "romantic", "romantic partner": "romantic",
    # political
    "diplomatic": "political",
    # factional
    "faction": "factional", "guild": "factional", "tribal": "factional",
    # other — explicit task/using relationships stay as other
    "task related to": "other", "using": "other",
}


def _coerce_relationship_type(type_value: str) -> str:
    """Map common LLM relationship labels to schema enum values (#126)."""
    normalized = type_value.lower().strip()
    if not normalized:
        return "other"
    return _RELATIONSHIP_TYPE_MAP.get(normalized, "other")


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
            # Coerce relationship type to schema enum (#126)
            if entry.get("type"):
                entry["type"] = _coerce_relationship_type(entry["type"])
            existing_rels.append(entry)
            by_target[target_id] = len(existing_rels) - 1


def _consolidate_relationship(existing: dict, update: dict) -> None:
    """Consolidate an update into an existing relationship for the same pair.

    Pushes the old current_relationship into history and updates to the new one.
    """
    # Coerce relationship type to schema enum (#126)
    if update.get("type"):
        update["type"] = _coerce_relationship_type(update["type"])

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
            # Coerce relationship type to schema enum (#126)
            new_rel["type"] = _coerce_relationship_type(new_rel["type"])
            entity["relationships"].append(new_rel)

        # Safety dedup — collapse any lingering duplicates (#242)
        if entity.get("relationships"):
            entity["relationships"] = _dedup_relationships(entity["relationships"])


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
