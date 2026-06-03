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
import sys
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

# Reverse of TYPE_TO_PREFIX — infer entity type from ID prefix.
_PREFIX_TO_TYPE = {v: k for k, v in TYPE_TO_PREFIX.items()}

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
# Relationship index (bidirectional lookups)
# ---------------------------------------------------------------------------

def generate_relationship_index(catalogs: dict) -> dict:
    """Build a bidirectional relationship index from all entity catalogs.

    Returns a dict mapping entity IDs to their forward and reverse
    relationships, suitable for writing to ``relationship-index.json``.
    """
    # Build name/type lookup for all entities
    entity_meta: dict[str, dict] = {}
    for _key, entities in catalogs.items():
        for entity in entities:
            eid = entity.get("id")
            if eid:
                entity_meta[eid] = {
                    "name": entity.get("name", ""),
                    "type": entity.get("type", ""),
                }

    def _infer_type(entity_id: str) -> str:
        """Infer entity type from ID prefix for dangling targets."""
        for prefix, etype in _PREFIX_TO_TYPE.items():
            if entity_id.startswith(prefix):
                return etype
        return ""

    # Collect forward and reverse edges
    forward: dict[str, list[dict]] = {}
    reverse: dict[str, list[dict]] = {}

    for _key, entities in catalogs.items():
        for entity in entities:
            source_id = entity.get("id")
            if not source_id:
                continue
            source_name = entity.get("name", "")
            for rel in entity.get("relationships", []):
                target_id = rel.get("target_id")
                if not target_id:
                    continue
                target_meta = entity_meta.get(target_id, {})
                edge = {
                    "source_id": source_id,
                    "source_name": source_name,
                    "target_id": target_id,
                    "target_name": target_meta.get("name", ""),
                    "current_relationship": rel.get("current_relationship", ""),
                    "type": rel.get("type", "other"),
                    "status": rel.get("status", "active"),
                }
                if rel.get("direction"):
                    edge["direction"] = rel["direction"]
                if rel.get("first_seen_turn"):
                    edge["first_seen_turn"] = rel["first_seen_turn"]
                if rel.get("last_updated_turn"):
                    edge["last_updated_turn"] = rel["last_updated_turn"]

                forward.setdefault(source_id, []).append(edge)
                reverse.setdefault(target_id, []).append(edge)

    # Build entries for every entity that participates in at least one
    # relationship (as source or target).
    all_ids = set(forward) | set(reverse)
    entries: dict[str, dict] = {}
    for eid in sorted(all_ids):
        meta = entity_meta.get(eid, {})
        entries[eid] = {
            "entity_name": meta.get("name", ""),
            "entity_type": meta.get("type") or _infer_type(eid),
            "forward": forward.get(eid, []),
            "reverse": reverse.get(eid, []),
        }

    return entries


def save_relationship_index(
    catalog_dir: str,
    catalogs: dict,
    turn_id: str | None = None,
    dry_run: bool = False,
) -> None:
    """Generate and write ``relationship-index.json`` to *catalog_dir*."""
    entries = generate_relationship_index(catalogs)
    if dry_run:
        total_edges = sum(len(e["forward"]) for e in entries.values())
        print(
            f"  [DRY RUN] Would write relationship index "
            f"({len(entries)} entities, {total_edges} edges) "
            f"to {catalog_dir}/relationship-index.json"
        )
        return

    # Determine generated_turn: use explicit arg, else scan for the max
    # last_updated_turn across all entities.
    if not turn_id:
        max_num = 0
        for _key, entities in catalogs.items():
            for entity in entities:
                num = _parse_turn_number(entity.get("last_updated_turn"))
                if num is not None and num > max_num:
                    max_num = num
        turn_id = f"turn-{max_num:03d}" if max_num else "turn-000"

    index_doc = {
        "generated_turn": turn_id,
        "entries": entries,
    }
    fpath = os.path.join(catalog_dir, "relationship-index.json")
    os.makedirs(catalog_dir, exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(index_doc, f, indent=2, ensure_ascii=False)
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

    # Regenerate the cross-catalog relationship index
    save_relationship_index(catalog_dir, catalogs, dry_run=dry_run)


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
    """Rough token estimate: ~3 characters per token.

    BPE tokenizers (Qwen, Llama, GPT) average 2.5–3.5 characters per
    token depending on content.  Using 3 as the divisor is conservative
    enough to avoid under-counting while keeping the estimate cheap.
    """
    return max(1, len(text) // 3)


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


def _format_entity_id_only(entity: dict) -> str:
    """Format a single entity with ID and name only (no type)."""
    return f"{entity['id']} | {entity['name']}"


# Default number of recent turns for which entities get full detail
_DEFAULT_RECENCY_WINDOW = 10

# Default fraction of context_length allocated to the entity list
_DEFAULT_ENTITY_BUDGET_FRACTION = 0.25

# Non-priority entities older than this get id-only format instead of brief
_DEFAULT_BRIEF_STALENESS_THRESHOLD = 20

# Backfill entities older than this many turns are excluded from the prompt
# (priority entities — mentioned, co-located, one-hop — are always kept)
_DEFAULT_STALENESS_THRESHOLD = 50

# Minimum entity name length to avoid false-positive keyword matches
_MIN_NAME_LENGTH_FOR_MATCH = 3

# Common English words that appear as entity names due to extraction artifacts.
# Single-word names matching these are skipped during mention detection to avoid
# false positives. Multi-word names containing these words are NOT filtered.
_COMMON_WORD_BLOCKLIST: set[str] = {
    # Generic nouns/adjectives observed as extraction artifacts
    "any", "echo", "field", "fire", "head", "land", "new",
    "quiet", "snow", "song", "two", "weave",
    # Combat/game terms
    "attack", "defense", "move", "action", "skill", "ability",
    # Elements/concepts
    "water", "earth", "wind", "light", "dark", "shadow",
    "death", "life", "blood", "spirit", "soul", "magic",
    # Role descriptors
    "disruption", "reinforced", "geometric", "southern", "triangular",
}

# Maximum fraction of catalog that priority + one-hop can occupy before
# one-hop expansion is skipped entirely (prevents false-positive cascade).
_ONE_HOP_PRIORITY_CAP = 0.5

# Minimum catalog size for the one-hop cap to apply; small catalogs don't
# suffer from false-positive cascade so the cap would break valid one-hop.
_ONE_HOP_CAP_MIN_ENTITIES = 20

# ---------------------------------------------------------------------------
# Adaptive, pressure-gated compression (PR-2, epic #464, #460)
#
# Every constant below is a Rule-10 magic threshold: it is a named module
# constant whose default is overridden from
# ``config/llm.json → context_optimizations.adaptive_compression`` and is
# documented in ``docs/architecture.md``.  All values must be RECALIBRATED ON
# MODEL CHANGE — the effective char/token ratio used by ``_estimate_tokens``
# and the context window both shift with the model.
# ---------------------------------------------------------------------------

# justification: below this fill ratio there is no real budget pressure, so
# trimming early/low-fill turns only starves discovery (the #393 regression).
# A phase is compressed only once its assembled context exceeds this fraction
# of the phase budget.  Recalibrate on model change.
_PRESSURE_GATE_FRACTION = 0.45

# justification: protects against the #393 discovery-starvation entity loss —
# discovery context is never trimmed below this fraction of its budget, even
# under heavy pressure (only the turn-total cap may override, and only after
# trimming other phases first).  Recalibrate on model change.
_DISCOVERY_FLOOR_FRACTION = 0.25

# justification: hard cap on the sum of assembled context across all phases of
# a turn, as a fraction of the model context window — leaves headroom for the
# response.  Recalibrate on model change (context_length and response budget
# both shift).
_TURN_TOTAL_BUDGET_FRACTION = 0.85

# justification: entities whose structural centrality (relationship degree +
# inbound references + mention frequency) is at or above this are exempt from
# the degrade/omit passes.  Purely structural — no domain word list (Rule 9).
# Recalibrate on model change.
_CENTRALITY_MIN_DEGREE = 2

# justification: optional top-N centrality exemption; ``None`` derives the
# exemption purely from ``_CENTRALITY_MIN_DEGREE``.  Recalibrate on model change.
_CENTRALITY_EXEMPT_TOP_N = None


def _coerce_fraction(value, default: float) -> float:
    """Return ``value`` as a float in (0, 1], else ``default``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if 0.0 < f <= 1.0:
        return f
    return default


def adaptive_compression_config(config: dict | None) -> dict | None:
    """Resolve the adaptive-compression settings, or ``None`` when disabled.

    Reads ``context_optimizations.adaptive_compression`` from the llm config.
    Returns ``None`` (so every call site short-circuits with a single guard)
    when the block is absent or ``enabled`` is falsey — guaranteeing
    byte-for-byte identical behaviour to pre-#460 ``main`` when the feature is
    off.  When enabled, returns a dict of the resolved, validated thresholds.
    """
    if not isinstance(config, dict):
        return None
    opt = config.get("context_optimizations")
    if not isinstance(opt, dict):
        return None
    ac = opt.get("adaptive_compression")
    if not isinstance(ac, dict) or not ac.get("enabled"):
        return None
    top_n = ac.get("centrality_exempt_top_n", _CENTRALITY_EXEMPT_TOP_N)
    if top_n is not None:
        try:
            top_n = int(top_n)
            if top_n <= 0:
                top_n = None
        except (TypeError, ValueError):
            top_n = None
    min_deg = ac.get("centrality_min_degree", _CENTRALITY_MIN_DEGREE)
    try:
        min_deg_f = float(min_deg)
    except (TypeError, ValueError):
        min_deg = _CENTRALITY_MIN_DEGREE
    else:
        # A negative threshold would exempt *every* entity from the degrade/omit
        # passes (any degree >= a negative number), silently defeating
        # compression.  Check the sign on the raw value *before* int truncation
        # so a fractional negative (e.g. -0.5, which would truncate toward 0 and
        # escape an int-only clamp) also falls back to the default.
        min_deg = int(min_deg_f)
        if min_deg_f < 0:
            min_deg = _CENTRALITY_MIN_DEGREE
    return {
        "pressure_gate_fraction": _coerce_fraction(
            ac.get("pressure_gate_fraction"), _PRESSURE_GATE_FRACTION
        ),
        "discovery_floor_fraction": _coerce_fraction(
            ac.get("discovery_floor_fraction"), _DISCOVERY_FLOOR_FRACTION
        ),
        "turn_total_budget_fraction": _coerce_fraction(
            ac.get("turn_total_budget_fraction"), _TURN_TOTAL_BUDGET_FRACTION
        ),
        "centrality_min_degree": min_deg,
        "centrality_exempt_top_n": top_n,
    }


def compute_entity_centrality(catalogs: dict) -> dict[str, int]:
    """Structural centrality score per entity id.

    The score is purely structural (Rule 9 — no domain word lists): it sums the
    entity's outbound ``relationships[]`` degree, the count of inbound
    references from other entities' relationships, and a mention-frequency term
    (``mention_count`` when present, else ``len(source_turns)``).  Used as the
    centrality backstop so high-degree / frequently-referenced entities survive
    the degrade/omit passes under budget pressure.
    """
    outbound: dict[str, int] = {}
    inbound: dict[str, int] = {}
    for entities in catalogs.values():
        for e in entities:
            eid = e.get("id")
            if not eid:
                continue
            rels = e.get("relationships") or []
            outbound[eid] = outbound.get(eid, 0) + len(rels)
            for rel in rels:
                if not isinstance(rel, dict):
                    continue
                tgt = rel.get("target_id")
                if tgt:
                    inbound[tgt] = inbound.get(tgt, 0) + 1
    score: dict[str, int] = {}
    for entities in catalogs.values():
        for e in entities:
            eid = e.get("id")
            if not eid:
                continue
            s = outbound.get(eid, 0) + inbound.get(eid, 0)
            mc = e.get("mention_count")
            if isinstance(mc, int):
                s += mc
            else:
                st = e.get("source_turns")
                if isinstance(st, list):
                    s += len(st)
            score[eid] = score.get(eid, 0) + s
    return score


def centrality_exempt_ids(
    centrality: dict[str, int] | None, adaptive: dict | None
) -> set[str]:
    """Return the set of entity ids exempt from degrade/omit passes."""
    if not centrality or adaptive is None:
        return set()
    min_deg = adaptive["centrality_min_degree"]
    exempt = {eid for eid, s in centrality.items() if s >= min_deg}
    top_n = adaptive["centrality_exempt_top_n"]
    if top_n:
        top = sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        exempt |= {eid for eid, _ in top}
    return exempt


def coordinate_turn_total(
    phases: list[dict], context_length: int | None, adaptive: dict | None
) -> list[dict]:
    """Enforce the per-turn total context budget across phases.

    This is the smallest-possible turn-level aggregation point (the brief
    forbids restructuring the pipeline).  Each ``phases`` entry is a dict with:

    * ``name``     — phase identifier,
    * ``tokens``   — assembled (uncompressed) token estimate,
    * ``floor``    — minimum retainable tokens (e.g. the discovery floor, or
                     the centrality-exempt minimum); defaults to 0,
    * ``priority`` — lower trims first.

    Returns the phases with an added ``allocated`` token count.  When
    ``adaptive`` is ``None`` the allocation equals ``tokens`` (no-op).  When the
    sum exceeds ``turn_total_budget_fraction * context_length`` the overflow is
    trimmed from the lowest-priority phases first, never below each phase's
    ``floor`` — so the discovery floor and centrality-exempt content are
    preserved while lower-priority content is trimmed first.

    Cap enforcement is **best-effort**: floors are inviolable, so when the sum
    of all phase ``floor`` values already exceeds the cap there is nothing left
    to trim and the returned total allocation will still exceed the cap.  In
    that case the floors win and the cap is intentionally not met.
    """
    out = [dict(p, allocated=p.get("tokens", 0)) for p in phases]
    if adaptive is None or not context_length:
        return out
    cap = int(context_length * adaptive["turn_total_budget_fraction"])
    total = sum(p["allocated"] for p in out)
    if total <= cap:
        return out
    over = total - cap
    for p in sorted(out, key=lambda x: x.get("priority", 0)):
        if over <= 0:
            break
        trimmable = p["allocated"] - int(p.get("floor", 0))
        if trimmable <= 0:
            continue
        cut = min(trimmable, over)
        p["allocated"] -= cut
        over -= cut
    return out


def _entity_names(entity: dict) -> list[str]:
    """Return the entity's name and aliases as a list of strings."""
    names = [entity["name"]]
    sa = entity.get("stable_attributes", {}).get("aliases")
    if sa:
        val = sa.get("value", "") if isinstance(sa, dict) else sa
        if isinstance(val, list):
            names.extend(val)
        elif isinstance(val, str) and val:
            names.append(val)
    return names


def _find_mentioned_entities(
    all_entities: list[dict],
    turn_text: str,
) -> set[str]:
    """Return IDs of entities whose name or alias appears in *turn_text*.

    Uses word-boundary-aware matching to avoid false positives (e.g.
    "elder" inside "beelder", or "the camp" inside "campfire").
    Multi-word names use non-word-character boundaries; single words
    use ``\\b``.  Names shorter than ``_MIN_NAME_LENGTH_FOR_MATCH``
    characters are skipped.
    """
    if not turn_text:
        return set()

    text_lower = turn_text.lower()
    mentioned: set[str] = set()
    for entity in all_entities:
        for name in _entity_names(entity):
            if len(name) < _MIN_NAME_LENGTH_FOR_MATCH:
                continue
            # Skip common-word names that cause false positives.
            # Strip leading articles so "the land" -> "land" is caught too.
            name_lower = name.lower()
            _stripped = re.sub(r"^(?:the|a|an)\s+", "", name_lower)
            if " " not in _stripped and _stripped in _COMMON_WORD_BLOCKLIST:
                continue
            escaped = re.escape(name_lower)
            if " " in name:
                # Multi-word: non-word boundaries at both ends
                pattern = r"(?<!\w)" + escaped + r"(?!\w)"
            else:
                # Single-word: word boundary
                pattern = r"\b" + escaped + r"\b"
            if re.search(pattern, text_lower):
                mentioned.add(entity["id"])
                break
    return mentioned


def _get_entity_location(entity: dict) -> str | None:
    """Return the entity's volatile_state.location, or None."""
    vs = entity.get("volatile_state")
    if isinstance(vs, dict):
        loc = vs.get("location")
        if loc and isinstance(loc, str):
            return loc
    return None


def _collect_relevant_locations(mentioned_ids: set[str], by_id: dict[str, dict]) -> set[str]:
    """Build the set of relevant location identifiers from mentioned entities.

    Includes both entity names/aliases (lowered) **and** entity IDs for
    location-type entities, plus the ``volatile_state.location`` value of
    every mentioned entity.  This ensures matching works whether catalogs
    store locations as human-readable names or as ``loc-*`` IDs.
    """
    relevant: set[str] = set()
    for eid in mentioned_ids:
        entity = by_id.get(eid)
        if entity is None:
            continue
        # If a mentioned entity IS a location, its ID and names are
        # location references
        if entity.get("type") == "location":
            relevant.add(entity["id"].lower())
            for n in _entity_names(entity):
                relevant.add(n.lower())
        # If a mentioned entity has a location, that location is relevant
        loc = _get_entity_location(entity)
        if loc:
            relevant.add(loc.lower())
    return relevant


def _find_colocated_entities(
    all_entities: list[dict],
    mentioned_ids: set[str],
    relevant_locations: set[str],
) -> set[str]:
    """Return IDs of entities co-located with mentioned entities."""
    colocated: set[str] = set()
    if not relevant_locations:
        return colocated
    for entity in all_entities:
        if entity["id"] in mentioned_ids:
            continue
        # Check if entity's location matches a relevant location
        loc = _get_entity_location(entity)
        if loc and loc.lower() in relevant_locations:
            colocated.add(entity["id"])
        # Check if entity IS a location that's relevant (by ID or name)
        if entity.get("type") == "location":
            if entity["id"].lower() in relevant_locations:
                colocated.add(entity["id"])
                continue
            for n in _entity_names(entity):
                if n.lower() in relevant_locations:
                    colocated.add(entity["id"])
                    break
    return colocated


def _find_one_hop_targets(
    mentioned_ids: set[str],
    exclude_ids: set[str],
    by_id: dict[str, dict],
) -> set[str]:
    """Return IDs of active relationship targets of mentioned entities.

    Only ``active`` (or unset) relationship status is followed; ``dormant``
    and ``resolved`` relationships are skipped to avoid dragging in stale
    entities.
    """
    one_hop: set[str] = set()
    for eid in mentioned_ids:
        entity = by_id.get(eid)
        if entity is None:
            continue
        for rel in entity.get("relationships") or []:
            status = rel.get("status", "active")
            if status != "active":
                continue
            target = rel.get("target_id")
            if (target and target in by_id
                    and target not in mentioned_ids
                    and target not in exclude_ids):
                one_hop.add(target)
    return one_hop


def _select_context_aware_entities(
    all_entities: list[dict],
    turn_text: str | None,
    current_turn: int | None,
    recency_window: int,
) -> tuple[list[dict], set[str]]:
    """Return entities ordered by contextual relevance and their priority IDs.

    Priority tiers (entities appear at most once, in the highest tier they
    qualify for):

    1. **Mentioned** — entity name or alias appears in turn text.
    2. **Co-located** — shares a location with a mentioned entity, or is at
       a mentioned location.  Matches both location names and ``loc-*`` IDs.
    3. **One-hop relationships** — is an *active* relationship target of a
       mentioned entity (dormant/resolved relationships are skipped).
    4. **Recency backfill** — most recently updated entities not already
       selected.

    Within each tier, entities are sorted by ``last_updated_turn`` descending.

    When *turn_text* is ``None`` or empty, falls back to pure recency
    ordering (equivalent to the pre-#233 behaviour).

    Returns:
        A tuple of (ordered_entities, priority_ids) where *priority_ids*
        is the union of mentioned, co-located, and one-hop entity IDs.
    """
    # Build an id→entity lookup
    by_id: dict[str, dict] = {e["id"]: e for e in all_entities}

    # --- Tier 1: Mentioned entities ---
    mentioned_ids = _find_mentioned_entities(all_entities, turn_text or "")

    # --- Tier 2: Co-located entities ---
    colocated_ids: set[str] = set()
    if mentioned_ids:
        relevant_locations = _collect_relevant_locations(mentioned_ids, by_id)
        colocated_ids = _find_colocated_entities(
            all_entities, mentioned_ids, relevant_locations)

    # --- Tier 3: One-hop relationship targets (active only) ---
    one_hop_ids: set[str] = set()
    if mentioned_ids:
        one_hop_ids = _find_one_hop_targets(
            mentioned_ids, mentioned_ids | colocated_ids, by_id)

    # Cap one-hop expansion: if priority + one-hop > 50% of catalog,
    # skip one-hop entirely to prevent false-positive cascade (#297).
    # Only applies to large catalogs where cascade is a real problem.
    if (len(all_entities) >= _ONE_HOP_CAP_MIN_ENTITIES
            and len(mentioned_ids | colocated_ids | one_hop_ids)
            > len(all_entities) * _ONE_HOP_PRIORITY_CAP):
        one_hop_ids = set()

    # --- Tier 4: Recency backfill (staleness-filtered) ---
    priority_ids = mentioned_ids | colocated_ids | one_hop_ids

    def _sort_key(e: dict) -> int:
        return _parse_turn_number(e.get("last_updated_turn")) or 0

    # Staleness cutoff: backfill entities older than this are excluded
    # Only applies when context-aware selection is active (turn_text provided)
    staleness_cutoff: int | None = None
    if current_turn is not None and turn_text:
        staleness_cutoff = current_turn - _DEFAULT_STALENESS_THRESHOLD

    # Build ordered result: each tier sorted by recency descending
    result: list[dict] = []

    for tier_ids in (mentioned_ids, colocated_ids, one_hop_ids):
        tier = [by_id[eid] for eid in tier_ids if eid in by_id]
        tier.sort(key=_sort_key, reverse=True)
        result.extend(tier)

    # Backfill: remaining entities sorted by recency, excluding stale ones
    backfill = [e for e in all_entities if e["id"] not in priority_ids]
    if staleness_cutoff is not None:
        backfill = [
            e for e in backfill
            if (_parse_turn_number(e.get("last_updated_turn")) or 0)
            >= staleness_cutoff
        ]
    backfill.sort(key=_sort_key, reverse=True)
    result.extend(backfill)

    return result, priority_ids


def format_known_entities_bounded(
    catalogs: dict,
    *,
    current_turn: int | None = None,
    context_length: int | None = None,
    entity_context_budget: int | None = None,
    recency_window: int | None = None,
    turn_text: str | None = None,
    return_stats: bool = False,
    adaptive: dict | None = None,
    centrality: dict[str, int] | None = None,
) -> str | tuple[str, dict]:
    """Format known entities with a configurable token budget.

    When *turn_text* is provided, entities are selected by contextual
    relevance (#233): mentioned entities first, then co-located, then
    one-hop relationship targets, then recency backfill.  Within each
    tier, entities active within *recency_window* turns get full detail
    (identity + aliases); others get brief format (ID | name | type).

    When *turn_text* is ``None``, falls back to pure recency-based
    selection (the pre-#233 behaviour).

    If the result still exceeds the budget, lower-priority entities are
    omitted and a note is appended.

    When no budget constraint applies (budget is None / large enough, or
    there are few entities), this produces the same output as
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
        turn_text: The current turn's text content.  When provided, enables
            context-aware entity selection based on mentions, co-location,
            and relationships.
        return_stats: When True, return ``(text, stats)`` where ``stats`` holds
            the pre-bound token estimate and section counters
            (``raw_tokens``, ``catalog_entries_pruned``,
            ``catalog_entries_degraded``).  The default (``False``) keeps the
            original string-only return so existing callers are unchanged.  The
            stats path is the backward-compatible plumbing PR-2 wires into the
            per-turn metrics.
            adaptive: Resolved adaptive-compression settings from
                ``adaptive_compression_config()``, or ``None`` (default) to disable
                all PR-2 (#460) code paths.  When ``None`` the function behaves
                byte-for-byte identically to pre-#460 ``main``.  When provided it
                enables the pressure gate (skip trimming below
                ``pressure_gate_fraction`` of budget), the discovery floor (never
                trim below ``discovery_floor_fraction`` of budget), and the
                centrality backstop.
            centrality: Optional per-entity structural centrality map from
                ``compute_entity_centrality()``.  Entities at or above the
                configured centrality threshold are exempt from the degrade/omit
                passes.  Only consulted when *adaptive* is provided.

    Returns:
        Formatted entity list string, possibly with a truncation note.  When
        ``return_stats`` is True, a ``(text, stats)`` tuple instead.
    """
    if recency_window is None:
        recency_window = _DEFAULT_RECENCY_WINDOW

    def _ret(
        text: str,
        raw_tokens: int,
        pruned: int,
        degraded: int,
        pre_compression: int | None = None,
    ):
        if return_stats:
            return text, {
                "raw_tokens": raw_tokens,
                # Section token count after baseline context-aware selection and
                # recency-based formatting but *before* the adaptive trim passes.
                # This is the faithful basis for attributing a token delta to
                # adaptive compression (#460): subtracting from ``raw_tokens``
                # (the fully unbounded all-entities estimate) would wrongly count
                # baseline budgeting/staleness pruning as compression.  For the
                # no-trim early returns it equals the returned text's own
                # estimate, so the derived delta is 0.
                "pre_compression_tokens": (
                    raw_tokens if pre_compression is None else pre_compression
                ),
                "catalog_entries_pruned": pruned,
                "catalog_entries_degraded": degraded,
            }
        return text

    # Derive budget
    budget: int | None = entity_context_budget
    if budget is None and context_length is not None:
        budget = int(context_length * _DEFAULT_ENTITY_BUDGET_FRACTION)

    # Flatten all entities
    all_entities: list[dict] = []
    for entities in catalogs.values():
        all_entities.extend(entities)

    if not all_entities:
        _empty = "(none — empty catalog)"
        return _ret(_empty, _estimate_tokens(_empty), 0, 0)

    # If no budget constraint and no turn text for context-aware filtering,
    # fall back to unbounded format
    if budget is None and not turn_text:
        _full = format_known_entities(catalogs)
        return _ret(_full, _estimate_tokens(_full), 0, 0)

    # Fast-path: if full unbounded output fits within budget AND no turn
    # text is available for context-aware filtering, return it directly so
    # that no entities are needlessly degraded to brief format.
    # When turn_text IS provided, we always use context-aware selection to
    # proactively trim stale entities even when under budget.
    unbounded = format_known_entities(catalogs)
    _unbounded_tokens = _estimate_tokens(unbounded)
    if not turn_text and _unbounded_tokens <= budget:
        return _ret(unbounded, _unbounded_tokens, 0, 0)

    # Order entities by contextual relevance when turn text is available,
    # otherwise fall back to recency-only partitioning.
    ordered, priority_ids = _select_context_aware_entities(
        all_entities, turn_text, current_turn, recency_window,
    )
    # Stale backfill entities excluded by context-aware selection are absent
    # from the formatted context just like budget-popped entries, so count
    # them toward catalog_entries_pruned for accurate instrumentation.
    context_excluded = len(all_entities) - len(ordered)

    # Build lines in context-aware order
    lines: list[str] = []
    for entity in ordered:
        turn_num = _parse_turn_number(entity.get("last_updated_turn"))
        is_recent = (
            current_turn is None
            or turn_num is None
            or current_turn - turn_num <= recency_window
        )
        is_priority = entity["id"] in priority_ids
        if is_recent or is_priority:
            lines.append(_format_entity_full(entity))
        else:
            age = (
                (current_turn - turn_num)
                if current_turn is not None and turn_num is not None
                else 0
            )
            if age <= _DEFAULT_BRIEF_STALENESS_THRESHOLD:
                lines.append(_format_entity_brief(entity))
            else:
                lines.append(_format_entity_id_only(entity))

    used = _estimate_tokens("\n".join(lines)) if lines else 0
    # Snapshot the assembled section size *before* any trimming so callers can
    # attribute the adaptive-compression delta to the trim passes alone, not to
    # the baseline context-aware selection / staleness formatting above.
    _pre_trim_tokens = used

    # Adaptive pressure gate (#460): ``pressure_gate_fraction`` is both the
    # activation threshold *and* the trim-down target for the adaptive path.
    # Compression engages once the assembled context exceeds
    # ``pressure_gate_fraction * budget`` (proactively, before a hard budget
    # overflow) and trims back toward that threshold — never below the
    # discovery floor.  This lets early/low-fill turns pass through untouched
    # (the #393 starvation fix) while making the fraction materially control
    # how early and how hard a phase compresses: a value of ``1.0`` reproduces
    # the original "compress only on hard budget overflow" behavior, while
    # ``0.45`` compresses once the phase is ~45% full.  When ``adaptive`` is
    # None this is inert and the original trimming logic runs unchanged.
    _gate_target: float | None = None
    if adaptive is not None and budget is not None:
        _gate_target = adaptive["pressure_gate_fraction"] * budget

    if adaptive is None:
        # --- Original (default-off) trimming path: byte-for-byte unchanged ---
        # If output exceeds budget, degrade lowest-priority entities from the
        # end of the list (backfill tier first, then one-hop, etc.).
        if budget is not None and used > budget and len(lines) > 1:
            # Pass 1: degrade full → brief from the tail (skip already-brief
            # and id-only lines to avoid accidentally upgrading them)
            for i in range(len(ordered) - 1, -1, -1):
                brief_line = _format_entity_brief(ordered[i])
                id_only_line = _format_entity_id_only(ordered[i])
                # Only degrade if current line is longer than brief
                if lines[i] != brief_line and lines[i] != id_only_line:
                    lines[i] = brief_line
                    used = _estimate_tokens("\n".join(lines))
                    if used <= budget:
                        break

        # Pass 2: degrade brief → id-only from the tail
        if budget is not None and used > budget and len(lines) > 1:
            for i in range(len(ordered) - 1, -1, -1):
                id_only_line = _format_entity_id_only(ordered[i])
                if lines[i] != id_only_line:
                    lines[i] = id_only_line
                    used = _estimate_tokens("\n".join(lines))
                    if used <= budget:
                        break

        # Pass 3: if still over budget, omit from the end
        omitted = 0
        if budget is not None and used > budget and len(lines) > 1:
            while lines and used > budget:
                lines.pop()
                omitted += 1
                used = _estimate_tokens("\n".join(lines)) if lines else 0
    else:
        # --- Adaptive path (#460): pressure-gated, floor-protected, centrality
        # backstop.  Engages once assembled context exceeds the pressure gate
        # (``_gate_target``) and trims back toward it, never below the floor.
        omitted = 0
        exempt = centrality_exempt_ids(centrality, adaptive)
        floor_tokens = (
            adaptive["discovery_floor_fraction"] * budget
            if budget is not None
            else 0
        )
        # The trim target is the pressure gate, not the raw budget: this is what
        # makes ``pressure_gate_fraction`` bind.  With the gate at 1.0 the target
        # equals the budget (original hard-overflow behavior); below 1.0 the
        # phase is compressed earlier and harder, down to the gate (bounded by
        # the discovery floor).
        _trim = (
            budget is not None
            and _gate_target is not None
            and used > _gate_target
            and len(lines) > 1
        )
        if _trim:
            # Pass 1: degrade full → brief, tail-first, skipping centrality
            # exempt entities and stopping at the discovery floor.
            for i in range(len(ordered) - 1, -1, -1):
                if ordered[i]["id"] in exempt:
                    continue
                brief_line = _format_entity_brief(ordered[i])
                id_only_line = _format_entity_id_only(ordered[i])
                if lines[i] != brief_line and lines[i] != id_only_line:
                    lines[i] = brief_line
                    used = _estimate_tokens("\n".join(lines))
                    if used <= _gate_target or used <= floor_tokens:
                        break
            # Pass 2: degrade brief → id-only, tail-first, centrality exempt.
            if used > _gate_target and used > floor_tokens:
                for i in range(len(ordered) - 1, -1, -1):
                    if ordered[i]["id"] in exempt:
                        continue
                    id_only_line = _format_entity_id_only(ordered[i])
                    if lines[i] != id_only_line:
                        lines[i] = id_only_line
                        used = _estimate_tokens("\n".join(lines))
                        if used <= _gate_target or used <= floor_tokens:
                            break
            # Pass 3: omit non-exempt entities tail-first, but never trim the
            # retained discovery context below the discovery floor (#393 guard).
            # Because entities are removed in discrete chunks, a single omission
            # can drop ``used`` from above the floor to below it — so each cut is
            # evaluated *before* it is committed and reverted if it would cross
            # the floor.
            if used > _gate_target and used > floor_tokens:
                keep = [True] * len(lines)
                for i in range(len(ordered) - 1, -1, -1):
                    if used <= _gate_target or used <= floor_tokens:
                        break
                    if ordered[i]["id"] in exempt:
                        continue
                    keep[i] = False
                    candidate = _estimate_tokens(
                        "\n".join(l for j, l in enumerate(lines) if keep[j])
                    ) if any(keep) else 0
                    if candidate < floor_tokens:
                        # Reverting this omission keeps us at/above the floor.
                        # Skip to a (smaller) earlier entity rather than stop, so
                        # a single large tail entity can't block otherwise-safe
                        # trims toward the gate.
                        keep[i] = True
                        continue
                    used = candidate
                    omitted += 1
                # Rebuild lines/ordered together to keep indices aligned for the
                # degraded-count computation below.
                lines = [l for j, l in enumerate(lines) if keep[j]]
                ordered = [o for j, o in enumerate(ordered) if keep[j]]
            # Pass 4 (last resort): when so many entities are centrality-exempt
            # that the gated passes above could not free enough room, the
            # known-entities section can still exit *above the hard budget*.
            # Omit exempt entities tail-first as well — but only as far as the
            # hard ``budget`` requires, and never below the discovery floor — so
            # the section stays within its configured token budget and adaptive
            # compression cannot itself trigger a context overflow.
            if (
                budget is not None
                and used > budget
                and used > floor_tokens
                and len(lines) > 1
            ):
                keep = [True] * len(lines)
                for i in range(len(ordered) - 1, -1, -1):
                    if used <= budget or used <= floor_tokens:
                        break
                    keep[i] = False
                    candidate = _estimate_tokens(
                        "\n".join(l for j, l in enumerate(lines) if keep[j])
                    ) if any(keep) else 0
                    if candidate < floor_tokens:
                        # Same floor guard as Pass 3: revert the last cut rather
                        # than push the retained context below the floor, and keep
                        # scanning for a smaller entity that can be trimmed safely
                        # so the section still tries to get under the hard budget.
                        keep[i] = True
                        continue
                    used = candidate
                    omitted += 1
                lines = [l for j, l in enumerate(lines) if keep[j]]
                ordered = [o for j, o in enumerate(ordered) if keep[j]]

    result = "\n".join(lines)

    if omitted > 0:
        note = (
            f"\n\n(Note: {omitted} additional entities exist in the catalog "
            f"but are not shown due to context limits. If a mention in the "
            f"turn text might refer to an unlisted entity, mark it as is_new "
            f"and the system will resolve duplicates.)"
        )
        result += note

    # Count kept entities rendered below full detail (brief / id-only).
    # Only computed when stats are requested — re-running _format_entity_full
    # per kept line is wasted work on the hot path when the value is discarded.
    degraded = 0
    if return_stats:
        degraded = sum(
            1 for i in range(len(lines))
            if lines[i] != _format_entity_full(ordered[i])
        )
    return _ret(
        result,
        _unbounded_tokens,
        omitted + context_excluded,
        degraded,
        pre_compression=_pre_trim_tokens,
    )


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
# Alias cross-reference guard (#302)
# ---------------------------------------------------------------------------

def _filter_entity_aliases(aliases: list[str], entity_name: str, known_entity_names: set[str]) -> list[str]:
    """Remove aliases that conflict with other entities' primary names (#302).

    Rejects aliases matching another entity's name (case-insensitive).
    The entity's own name is excluded from the conflict check.
    """
    if not known_entity_names:
        return aliases
    own_lower = entity_name.strip().lower() if entity_name else ""
    filter_set = known_entity_names - {own_lower} if own_lower else known_entity_names
    cleaned = []
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip():
            continue
        if alias.strip().lower() in filter_set:
            print(f"  COERCE: rejected alias '{alias}' (conflicts with existing entity)", file=sys.stderr)
            continue
        cleaned.append(alias)
    return cleaned


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
    # Primary stamping is now done in _coerce_entity_fields (programmatic
    # layer), but keep this as a safety net for direct merge_entity callers.
    fst = entity.get("first_seen_turn")
    if not fst or (isinstance(fst, str) and not re.match(r"^turn-[0-9]{3,}$", fst)):
        fallback = entity.get("last_updated_turn", "")
        if fallback and re.match(r"^turn-[0-9]{3,}$", fallback):
            entity["first_seen_turn"] = fallback
        else:
            entity.pop("first_seen_turn", None)

    # Lazily build known entity names only when alias filtering is needed (#302)
    def _get_known_names() -> set[str]:
        names: set[str] = set()
        for _cat_entities in catalogs.values():
            for _ent in _cat_entities:
                _n = _ent.get("name", "")
                if _n and isinstance(_n, str):
                    names.add(_n.strip().lower())
        return names

    if existing is not None:
        idx, current = existing
        # Only compute known names when the update touches aliases
        _needs_alias_check = bool(
            entity.get("stable_attributes", {}).get("aliases")
            or (entity.get("name") and entity["name"] != current.get("name"))
        )
        known_entity_names = _get_known_names() if _needs_alias_check else None
        _update_existing_entity(current, entity, known_entity_names=known_entity_names)
        catalogs[catalog_file][idx] = current
    else:
        # Ensure required fields for new entity
        required_base = ["id", "name", "type", "first_seen_turn"]
        has_desc = entity.get("identity")
        if all(entity.get(f) for f in required_base) and has_desc:
            # Filter aliases on new entities before appending (#302)
            sa = entity.get("stable_attributes", {})
            aliases_attr = sa.get("aliases")
            if isinstance(aliases_attr, dict) and isinstance(aliases_attr.get("value"), list):
                known_entity_names = _get_known_names()
                entity_name = entity.get("name", "")
                aliases_attr["value"] = _filter_entity_aliases(
                    aliases_attr["value"], entity_name, known_entity_names
                )
            catalogs[catalog_file].append(entity)
        else:
            missing = [f for f in required_base if not entity.get(f)]
            if not has_desc:
                missing.append("identity")
            print(f"  WARNING: New entity '{entity_id}' missing required fields: {missing}. Skipping.")


def _update_existing_entity(current: dict, update: dict, *, known_entity_names: set[str] | None = None, skip_name_guard: bool = False) -> None:
    """Update an existing entity with new information."""
    is_pc = current.get("id") == "char-player"

    # Upfront mismatch detection: if the update's name has zero word overlap
    # with the current name, skip all merges to prevent identity corruption (#339).
    _name_mismatch = False
    if not skip_name_guard and not is_pc and update.get("name") and current.get("name"):
        update_name_lower = update["name"].lower()
        current_name_lower = current["name"].lower()
        if update_name_lower != current_name_lower:
            _trivial = {"a", "an", "the", "of", "and", "with"}
            old_tokens = set(current_name_lower.replace("-", " ").split()) - _trivial
            new_tokens = set(update_name_lower.replace("-", " ").split()) - _trivial
            if old_tokens and new_tokens and not (old_tokens & new_tokens):
                _name_mismatch = True
                print(f"  GUARD: name mismatch for '{current.get('id')}' — "
                      f"'{current['name']}' vs '{update['name']}' "
                      f"(no word overlap — skipping all merges)", file=sys.stderr)

    if _name_mismatch:
        # Only advance last_updated_turn (safe metadata), skip all content merges
        if update.get("last_updated_turn"):
            existing_num = _parse_turn_number(current.get("last_updated_turn"))
            update_num = _parse_turn_number(update["last_updated_turn"])
            if not existing_num or (update_num and update_num >= existing_num):
                current["last_updated_turn"] = update["last_updated_turn"]
        return

    # Never overwrite the PC's identity from a per-turn update — the PC's
    # identity is established early and should not be replaced by an NPC
    # description that happens to land on the same entity ID.
    if update.get("identity") and update["identity"] != current.get("identity"):
        if not is_pc:
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
            normalized = key.lower()
            # Remove case-variant duplicates (#336)
            for existing_key in list(current["stable_attributes"].keys()):
                if existing_key.lower() == normalized and existing_key != normalized:
                    del current["stable_attributes"][existing_key]
            current["stable_attributes"][normalized] = value
        # Cross-reference guard: reject aliases matching other entity names (#302)
        if known_entity_names:
            sa_merged = current["stable_attributes"]
            aliases_merged = sa_merged.get("aliases")
            if isinstance(aliases_merged, dict) and isinstance(aliases_merged.get("value"), list):
                entity_name = current.get("name", "")
                aliases_merged["value"] = _filter_entity_aliases(
                    aliases_merged["value"], entity_name, known_entity_names
                )

    # Merge volatile_state
    if update.get("volatile_state"):
        if "volatile_state" not in current:
            current["volatile_state"] = {}
        for key, value in update["volatile_state"].items():
            normalized = key.lower()
            # Remove case-variant duplicates (#336)
            for existing_key in list(current["volatile_state"].keys()):
                if existing_key.lower() == normalized and existing_key != normalized:
                    del current["volatile_state"][existing_key]
            current["volatile_state"][normalized] = value

    # Handle name changes / aliases via stable_attributes.aliases
    # For char-player, never overwrite the canonical name — but still record
    # the mismatched name as an alias for downstream coreference (#247).
    if update.get("name") and update["name"] != current.get("name"):
        if is_pc:
            # Record the LLM-proposed name as a PC alias without renaming
            from semantic_extraction import _filter_pc_aliases
            candidate_name = update["name"]
            filtered = _filter_pc_aliases([candidate_name], known_entity_names)
            if filtered:  # Only add if it passes alias validation
                sa_pc = current.setdefault("stable_attributes", {})
                aliases_pc = sa_pc.setdefault("aliases", {"value": []})
                alias_list_pc = aliases_pc.get("value", [])
                if isinstance(alias_list_pc, str):
                    alias_list_pc = [a.strip() for a in alias_list_pc.split(",") if a.strip()]
                if candidate_name not in alias_list_pc:
                    alias_list_pc.append(candidate_name)
                    aliases_pc["value"] = alias_list_pc
        else:
            # Guard: reject name changes with no word overlap (#339)
            _trivial = {"a", "an", "the", "of", "and", "with"}
            old_tokens = set(current["name"].lower().replace("-", " ").split()) - _trivial
            new_tokens = set(update["name"].lower().replace("-", " ").split()) - _trivial
            if old_tokens and new_tokens and not (old_tokens & new_tokens):
                print(f"  GUARD: rejecting name change '{current['name']}' → "
                      f"'{update['name']}' (no word overlap — possible identity "
                      f"corruption)", file=sys.stderr)
            else:
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

    # Update last_updated_turn — keep the latest (max) to prevent
    # re-extraction of earlier turns from regressing the value (#314).
    if update.get("last_updated_turn"):
        existing_num = _parse_turn_number(current.get("last_updated_turn"))
        update_num = _parse_turn_number(update["last_updated_turn"])
        if not existing_num or (update_num and update_num >= existing_num):
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

            # Push the loser's current_relationship into history so
            # intermediate state is not silently dropped (#242).
            loser_desc = loser.get("current_relationship", "")
            winner_desc = winner.get("current_relationship", "")
            loser_turn = (loser.get("last_updated_turn")
                          or loser.get("first_seen_turn", ""))
            if loser_desc and loser_desc != winner_desc and loser_turn:
                loser_history_entry = {
                    "turn": loser_turn,
                    "description": loser_desc,
                }
            else:
                loser_history_entry = None

            # Merge and deduplicate history by (turn, description)
            merged_history = list(winner.get("history", []))
            existing_keys = {
                (h.get("turn"), h.get("description")) for h in merged_history
            }
            if loser_history_entry:
                key = (loser_history_entry["turn"],
                       loser_history_entry["description"])
                if key not in existing_keys:
                    merged_history.append(loser_history_entry)
                    existing_keys.add(key)
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
    "adversarial": "adversarial", "romantic": "romantic", "spatial": "spatial",
    "other": "other",
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
    # spatial — entity-to-location relationships
    "resides at": "spatial", "resides_at": "spatial",
    "located at": "spatial", "located_at": "spatial",
    "traveling to": "spatial", "traveling_to": "spatial",
    "departed from": "spatial", "departed_from": "spatial",
    "visited": "spatial", "stationed at": "spatial", "stationed_at": "spatial",
    "moved to": "spatial", "moved_to": "spatial",
    "lives in": "spatial", "lives_in": "spatial",
    "headquartered at": "spatial", "headquartered_at": "spatial",
    "based in": "spatial", "based_in": "spatial",
    "connected to": "spatial", "adjacent to": "spatial",
    "near": "spatial", "inside": "spatial", "contains": "spatial",
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

        # Pre-dedup: collapse any pre-existing duplicates for this target
        # *before* selecting an existing_rel, so consolidation always finds
        # the single canonical entry and intermediate state is preserved in
        # history by _dedup_relationships rather than silently dropped (#242).
        entity["relationships"] = _dedup_relationships(entity["relationships"])

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
