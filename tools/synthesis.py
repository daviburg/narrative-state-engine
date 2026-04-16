#!/usr/bin/env python3
"""
synthesis.py — Data assembly layer for wiki narrative synthesis.

Provides the foundation components for transforming raw extraction data
(events, entity catalogs, relationships) into structured inputs suitable
for LLM-powered narrative generation.

Components:
  1. Event-entity grouping with ID alias resolution
  2. Event-derived entity profiles for catalog-less entities
  3. Phase segmentation for narrative biography generation
  4. Relationship history merger (dedup + consolidate)
  5. Relationship arc summarizer (rule-based chunking + LLM naming)
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# ID Alias mapping — pragmatic bridge until #108 normalization
# ---------------------------------------------------------------------------

ID_ALIASES: dict[str, str] = {
    "char-Kael": "char-kael",
    "char-broad-figure": "char-kael",
    "faction-warrior-chief-gorok": "char-gorok",
    "char-ananya": "char-anya",
    "char-anxa": "char-anya",
    "npc-ananya": "char-anya",
    "entity-healer": "char-healer",
}

# Build a case-insensitive version for quick lookup
_ALIAS_LOWER: dict[str, str] = {k.lower(): v for k, v in ID_ALIASES.items()}

# Prefix-to-type mapping
_PREFIX_TYPE_MAP: dict[str, str] = {
    "char": "character",
    "npc": "character",
    "loc": "location",
    "faction": "faction",
    "item": "item",
    "creature": "creature",
    "concept": "concept",
    "entity": "unknown",
}


# ---------------------------------------------------------------------------
# Turn-number helpers
# ---------------------------------------------------------------------------

def _parse_turn_number(turn_id: str) -> int:
    """Extract numeric part from a turn ID like ``turn-078``.

    Returns 0 if not parseable.
    """
    m = re.search(r"(\d+)", turn_id or "")
    return int(m.group(1)) if m else 0


def _sort_key_turn(turn_id: str) -> int:
    return _parse_turn_number(turn_id)


# ---------------------------------------------------------------------------
# Step 1: Event-Entity Grouping
# ---------------------------------------------------------------------------

def resolve_entity_id(raw_id: str) -> str:
    """Resolve an entity ID through the alias table with case-insensitive matching.

    If a ``normalize_entity_id`` function is available from catalog_merger,
    that is preferred for full fuzzy matching. This function provides the
    lightweight fallback described in design-synthesis-layer.md §4.2.
    """
    if not raw_id:
        return raw_id
    # Check alias table (case-insensitive)
    lowered = raw_id.lower()
    if lowered in _ALIAS_LOWER:
        return _ALIAS_LOWER[lowered]
    # Default: lowercase the ID
    return lowered


def group_events_by_entity(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by resolved entity ID.

    For each event, iterates over ``related_entities`` and builds a mapping
    from canonical entity ID to the list of events referencing that entity.
    Each entity's events are sorted by ``source_turns[0]``.

    Args:
        events: List of event dicts (matching event.schema.json).

    Returns:
        Mapping of ``entity_id → [events]``, sorted by first source turn.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)

    for event in events:
        related = event.get("related_entities", [])
        for raw_id in related:
            canonical = resolve_entity_id(raw_id)
            grouped[canonical].append(event)

    # Sort each entity's events by their first source turn
    for entity_id in grouped:
        grouped[entity_id].sort(
            key=lambda e: _sort_key_turn(
                e.get("source_turns", [""])[0] if e.get("source_turns") else ""
            )
        )

    return dict(grouped)


# ---------------------------------------------------------------------------
# Step 2: Event-Derived Entity Profiles
# ---------------------------------------------------------------------------

def _infer_name_from_id(entity_id: str) -> str:
    """Infer a display name from an entity ID.

    Strips the type prefix, replaces hyphens with spaces, and title-cases.
    ``"char-kael"`` → ``"Kael"``
    ``"loc-the-settlement"`` → ``"The Settlement"``
    """
    # Remove known prefixes
    for prefix in ("char-", "npc-", "loc-", "faction-", "item-",
                   "creature-", "concept-", "entity-"):
        if entity_id.startswith(prefix):
            stem = entity_id[len(prefix):]
            return stem.replace("-", " ").title()
    # Fallback: just title-case the whole thing
    return entity_id.replace("-", " ").title()


def _infer_type_from_id(entity_id: str) -> str:
    """Infer entity type from the ID prefix.

    ``"char-kael"`` → ``"character"``
    ``"loc-settlement"`` → ``"location"``
    """
    for prefix, entity_type in _PREFIX_TYPE_MAP.items():
        if entity_id.startswith(prefix + "-"):
            return entity_type
    return "unknown"


def _extract_co_occurrences(entity_id: str, events: list[dict]) -> list[str]:
    """Collect all other resolved entity IDs that appear in the same events."""
    co_ids: set[str] = set()
    for event in events:
        for raw_id in event.get("related_entities", []):
            canonical = resolve_entity_id(raw_id)
            if canonical != entity_id:
                co_ids.add(canonical)
    return sorted(co_ids)


def _count_event_types(events: list[dict]) -> dict[str, int]:
    """Count events by their ``type`` field."""
    counter: Counter[str] = Counter()
    for event in events:
        counter[event.get("type", "other")] += 1
    return dict(counter)


def build_event_derived_profile(entity_id: str, events: list[dict]) -> dict:
    """Build a minimal entity profile from event data alone.

    For entities that exist in events but have no catalog JSON file.

    Args:
        entity_id: The canonical entity ID.
        events: List of events referencing this entity (pre-sorted).

    Returns:
        A derived profile dict.
    """
    first_turn = ""
    last_turn = ""
    if events:
        first_turns = events[0].get("source_turns", [])
        last_turns = events[-1].get("source_turns", [])
        first_turn = first_turns[0] if first_turns else ""
        last_turn = last_turns[0] if last_turns else ""

    return {
        "id": entity_id,
        "name": _infer_name_from_id(entity_id),
        "type": _infer_type_from_id(entity_id),
        "source": "events_only",
        "first_event_turn": first_turn,
        "last_event_turn": last_turn,
        "event_count": len(events),
        "co_occurring_entities": _extract_co_occurrences(entity_id, events),
        "event_types": _count_event_types(events),
    }


# ---------------------------------------------------------------------------
# Step 3: Phase Segmentation
# ---------------------------------------------------------------------------

def segment_phases(events: list[dict], entity_type: str, entity_id: str) -> list[dict]:
    """Divide an entity's event timeline into narrative phases.

    Rules (from design-synthesis-layer.md §3.3):
      - PC (char-player): Target 4–8 phases using gap/density detection, fallback
        to equal chunks of ~40 events.
      - Major NPCs (10+ events): 2–4 phases, split at largest event gaps.
      - Minor entities (<10 events): Single phase, no segmentation.

    Args:
        events: Sorted list of events for this entity.
        entity_type: ``"character"``, ``"location"``, etc.
        entity_id: The entity's canonical ID (used to detect PC).

    Returns:
        List of phase dicts with ``name``, ``turn_range``, ``events``, ``event_count``.
    """
    if not events:
        return []

    # Minor entities or non-characters with few events: single phase
    if len(events) < 10:
        return [_make_phase(events)]

    is_pc = entity_id == "char-player"

    if is_pc:
        return _segment_pc_phases(events)
    else:
        return _segment_npc_phases(events)


def _get_event_turn(event: dict) -> str:
    """Get the first source turn from an event."""
    turns = event.get("source_turns", [])
    return turns[0] if turns else ""


def _make_phase(events: list[dict], name: str | None = None) -> dict:
    """Create a phase dict from a list of events."""
    first_turn = _get_event_turn(events[0]) if events else ""
    last_turn = _get_event_turn(events[-1]) if events else ""
    return {
        "name": name,
        "turn_range": [first_turn, last_turn],
        "events": events,
        "event_count": len(events),
    }


def _find_gap_indices(events: list[dict], min_gap: int = 10) -> list[tuple[int, int]]:
    """Find indices where there are gaps of min_gap+ turns between consecutive events.

    Returns list of ``(index, gap_size)`` — the gap is between events[index] and events[index+1].
    """
    gaps: list[tuple[int, int]] = []
    for i in range(len(events) - 1):
        t1 = _parse_turn_number(_get_event_turn(events[i]))
        t2 = _parse_turn_number(_get_event_turn(events[i + 1]))
        gap = t2 - t1
        if gap >= min_gap:
            gaps.append((i, gap))
    return gaps


def _detect_type_shift_indices(events: list[dict]) -> list[int]:
    """Find indices where the dominant event type changes.

    Looks at rolling windows of 5 events and detects shifts in the most
    common event type.
    """
    if len(events) < 10:
        return []

    shifts: list[int] = []
    window = 5
    prev_dominant = None

    for i in range(0, len(events) - window + 1, window):
        chunk = events[i : i + window]
        types = [e.get("type", "other") for e in chunk]
        dominant = Counter(types).most_common(1)[0][0]
        if prev_dominant is not None and dominant != prev_dominant:
            shifts.append(i)
        prev_dominant = dominant

    return shifts


def _segment_pc_phases(events: list[dict]) -> list[dict]:
    """Segment PC events into 4–8 narrative phases."""
    # Collect potential breakpoints from gap detection and type shifts
    gap_indices = _find_gap_indices(events, min_gap=10)
    type_shifts = _detect_type_shift_indices(events)

    # Combine breakpoint candidates (index after which to split)
    # Gap indices are the split-after points; type shifts are split-at points
    breakpoints: set[int] = set()
    for idx, _gap in gap_indices:
        breakpoints.add(idx + 1)  # Split after this event
    for idx in type_shifts:
        breakpoints.add(idx)

    sorted_bps = sorted(breakpoints)

    if sorted_bps:
        # Filter breakpoints to get 4–8 phases
        phases = _split_at_breakpoints(events, sorted_bps, target_min=4, target_max=8)
    else:
        # Fallback: equal chunks of ~40 events
        phases = _split_equal_chunks(events, chunk_size=40)

    # Ensure we're in the 4–8 range
    if len(phases) < 4:
        # Not enough natural breakpoints; fallback to equal chunks
        phases = _split_equal_chunks(events, chunk_size=max(1, len(events) // 6))
    elif len(phases) > 8:
        # Too many breakpoints; merge smallest adjacent phases
        phases = _merge_to_target(phases, target=7)

    return phases


def _segment_npc_phases(events: list[dict]) -> list[dict]:
    """Segment major NPC events into 2–4 phases."""
    gap_indices = _find_gap_indices(events, min_gap=10)

    if not gap_indices:
        # No large gaps: split in half or keep as one
        if len(events) >= 20:
            mid = len(events) // 2
            return [
                _make_phase(events[:mid]),
                _make_phase(events[mid:]),
            ]
        return [_make_phase(events)]

    # Sort gaps by size (largest first) and use the top 1–3
    gap_indices.sort(key=lambda x: x[1], reverse=True)
    split_points = sorted([idx + 1 for idx, _gap in gap_indices[:3]])

    phases = _split_at_breakpoints(events, split_points, target_min=2, target_max=4)

    if len(phases) > 4:
        phases = _merge_to_target(phases, target=3)

    return phases


def _split_at_breakpoints(
    events: list[dict],
    breakpoints: list[int],
    target_min: int,
    target_max: int,
) -> list[dict]:
    """Split events at the given breakpoint indices into phases."""
    # Filter breakpoints that are within bounds
    valid_bps = [bp for bp in breakpoints if 0 < bp < len(events)]

    if not valid_bps:
        return [_make_phase(events)]

    # If too many breakpoints, select evenly spaced ones
    if len(valid_bps) + 1 > target_max:
        # Pick breakpoints that create the most evenly sized phases
        step = len(valid_bps) / (target_max - 1)
        selected = []
        for i in range(target_max - 1):
            idx = int(i * step)
            if idx < len(valid_bps):
                selected.append(valid_bps[idx])
        valid_bps = selected

    phases: list[dict] = []
    prev = 0
    for bp in valid_bps:
        if bp > prev:
            phases.append(_make_phase(events[prev:bp]))
        prev = bp
    if prev < len(events):
        phases.append(_make_phase(events[prev:]))

    return phases


def _split_equal_chunks(events: list[dict], chunk_size: int) -> list[dict]:
    """Split events into roughly equal chunks."""
    if chunk_size <= 0:
        chunk_size = 1
    phases: list[dict] = []
    for i in range(0, len(events), chunk_size):
        chunk = events[i : i + chunk_size]
        if chunk:
            phases.append(_make_phase(chunk))
    return phases


def _merge_to_target(phases: list[dict], target: int) -> list[dict]:
    """Merge the smallest adjacent phases until we reach the target count."""
    while len(phases) > target:
        # Find the pair of adjacent phases with the smallest combined event count
        min_combined = float("inf")
        min_idx = 0
        for i in range(len(phases) - 1):
            combined = phases[i]["event_count"] + phases[i + 1]["event_count"]
            if combined < min_combined:
                min_combined = combined
                min_idx = i

        # Merge phases[min_idx] and phases[min_idx + 1]
        merged_events = phases[min_idx]["events"] + phases[min_idx + 1]["events"]
        merged = _make_phase(merged_events)
        phases = phases[:min_idx] + [merged] + phases[min_idx + 2 :]

    return phases


# ---------------------------------------------------------------------------
# Step 4: Relationship History Merger
# ---------------------------------------------------------------------------

def merge_relationship_histories(relationships: list[dict]) -> dict[str, list[dict]]:
    """Merge all relationship entries for the same target_id into unified timelines.

    Handles:
      - Case-insensitive target_id matching
      - ID alias resolution
      - Chronological sorting by turn number
      - Deduplication of entries at the same turn

    Args:
        relationships: List of relationship dicts from an entity's catalog entry.
            Each has ``target_id``, ``history`` (list of dicts with ``turn``, ``type``,
            ``description``), and other relationship metadata.

    Returns:
        Mapping of ``canonical_target_id → merged_sorted_history``.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)

    for rel in relationships:
        raw_target = rel.get("target_id", "")
        canonical = resolve_entity_id(raw_target)
        history = rel.get("history", [])
        grouped[canonical].extend(history)

    result: dict[str, list[dict]] = {}
    for target_id, entries in grouped.items():
        # Sort by turn number
        entries.sort(key=lambda e: _parse_turn_number(e.get("turn", "")))
        # Deduplicate: keep the entry with more detail at the same turn
        deduped = _deduplicate_history(entries)
        result[target_id] = deduped

    return result


def _deduplicate_history(entries: list[dict]) -> list[dict]:
    """Remove duplicate entries at the same turn, keeping the one with more detail."""
    seen: dict[str, dict] = {}  # turn → best entry
    for entry in entries:
        turn = entry.get("turn", "")
        if turn not in seen:
            seen[turn] = entry
        else:
            existing = seen[turn]
            # Keep the entry with the longer description
            existing_desc = existing.get("description", "")
            new_desc = entry.get("description", "")
            if len(new_desc) > len(existing_desc):
                seen[turn] = entry
    # Return in turn order
    result = list(seen.values())
    result.sort(key=lambda e: _parse_turn_number(e.get("turn", "")))
    return result


# ---------------------------------------------------------------------------
# Step 5: Relationship Arc Summarizer
# ---------------------------------------------------------------------------

def chunk_relationship_arcs(history: list[dict]) -> list[dict]:
    """Rule-based chunking: Phase A of the arc summarizer.

    Groups history entries by:
      1. Type field transitions (when ``type`` changes, start a new chunk)
      2. Within same type, clustering entries within 20 turns of each other

    Args:
        history: Merged, sorted relationship history.

    Returns:
        List of chunk dicts, each with ``turn_range``, ``type``, ``entries``.
        Returns empty list if history has ≤ 3 entries (too little data).
    """
    if len(history) <= 3:
        return []

    chunks: list[dict] = []
    current_chunk_entries: list[dict] = []
    current_type: str | None = None

    for entry in history:
        entry_type = entry.get("type", "other")
        entry_turn = _parse_turn_number(entry.get("turn", ""))

        if current_type is None:
            # First entry
            current_type = entry_type
            current_chunk_entries = [entry]
            continue

        # Check for type transition
        if entry_type != current_type:
            # Save current chunk and start new one
            chunks.append(_make_arc_chunk(current_chunk_entries, current_type))
            current_type = entry_type
            current_chunk_entries = [entry]
            continue

        # Same type: check temporal proximity (within 20 turns)
        last_turn = _parse_turn_number(
            current_chunk_entries[-1].get("turn", "")
        )
        if entry_turn - last_turn > 20:
            # Gap too large, start new chunk even though type is same
            chunks.append(_make_arc_chunk(current_chunk_entries, current_type))
            current_chunk_entries = [entry]
            continue

        current_chunk_entries.append(entry)

    # Don't forget the last chunk
    if current_chunk_entries:
        chunks.append(_make_arc_chunk(current_chunk_entries, current_type or "other"))

    return chunks


def _make_arc_chunk(entries: list[dict], rel_type: str) -> dict:
    """Create an arc chunk dict from history entries."""
    first_turn = entries[0].get("turn", "") if entries else ""
    last_turn = entries[-1].get("turn", "") if entries else ""
    return {
        "turn_range": [first_turn, last_turn],
        "type": rel_type,
        "entries": entries,
    }


def _build_arc_prompt(source_name: str, target_name: str, chunks: list[dict]) -> str:
    """Build the LLM prompt for arc naming and summarization."""
    lines = [
        f"Given these interaction phases for the relationship between "
        f"{source_name} and {target_name},",
        "name each phase and write a 1-2 sentence summary. "
        "Merge or split phases if the narrative warrants it.",
        "",
    ]
    for i, chunk in enumerate(chunks, 1):
        start = chunk["turn_range"][0]
        end = chunk["turn_range"][1]
        rel_type = chunk["type"]
        lines.append(f"Phase {i} (turns {start}-{end}, type: {rel_type}):")
        for entry in chunk["entries"]:
            turn = entry.get("turn", "?")
            desc = entry.get("description", "")
            lines.append(f"  - {turn}: {desc}")
        lines.append("")

    lines.append("Respond with JSON:")
    lines.append('[')
    lines.append(
        '  {"phase": "Phase Name", "turn_range": ["turn-NNN", "turn-NNN"], '
        '"type": "...", "summary": "...", "key_turns": ["turn-NNN"]}'
    )
    lines.append(']')

    return "\n".join(lines)


def _fallback_arc_summaries(chunks: list[dict]) -> list[dict]:
    """Generate fallback arc summaries when LLM is unavailable or fails."""
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        summaries.append({
            "phase": f"Phase {i}",
            "turn_range": chunk["turn_range"],
            "type": chunk["type"],
            "summary": "",
            "key_turns": [chunk["turn_range"][0]],
        })
    return summaries


def summarize_relationship_arcs(
    source_id: str,
    source_name: str,
    relationships: list[dict],
    llm_client: object | None = None,
) -> dict:
    """Full arc summarization pipeline: merge → chunk → LLM → parse → store format.

    Args:
        source_id: The entity ID of the relationship source.
        source_name: Display name of the source entity.
        relationships: List of relationship dicts from the entity's catalog.
        llm_client: An LLMClient instance (from tools/llm_client.py).
            If None, only rule-based chunking is performed.

    Returns:
        Dict in the sidecar format::

            {
                "entity_id": "char-player",
                "generated_at": "...",
                "arcs": {
                    "target-id": {
                        "arc_summary": [...],
                        "current_relationship": "...",
                        "interaction_count": N
                    }
                }
            }
    """
    merged = merge_relationship_histories(relationships)

    # Build a lookup for current_relationship from the original relationship entries
    current_rel_lookup: dict[str, str] = {}
    for rel in relationships:
        canonical = resolve_entity_id(rel.get("target_id", ""))
        cur_rel = rel.get("current_relationship", "")
        if cur_rel:
            current_rel_lookup[canonical] = cur_rel

    arcs: dict[str, dict] = {}

    for target_id, history in merged.items():
        chunks = chunk_relationship_arcs(history)

        if not chunks:
            # Too few interactions, skip
            continue

        target_name = _infer_name_from_id(target_id)

        arc_summary: list[dict]
        if llm_client is not None:
            arc_summary = _llm_summarize_arcs(
                llm_client, source_name, target_name, chunks
            )
        else:
            arc_summary = _fallback_arc_summaries(chunks)

        arcs[target_id] = {
            "arc_summary": arc_summary,
            "current_relationship": current_rel_lookup.get(target_id, ""),
            "interaction_count": len(history),
        }

    return {
        "entity_id": source_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "arcs": arcs,
    }


def _llm_summarize_arcs(
    llm_client: object,
    source_name: str,
    target_name: str,
    chunks: list[dict],
) -> list[dict]:
    """Call the LLM to name and summarize relationship arc chunks.

    Falls back to rule-based naming on parse failure.
    """
    prompt = _build_arc_prompt(source_name, target_name, chunks)

    system_prompt = (
        "You are a narrative analyst for an RPG campaign wiki. "
        "Given interaction phases between two characters, name each phase "
        "and write a 1-2 sentence summary. Respond with a JSON array only."
    )

    try:
        # llm_client.extract_json returns parsed JSON
        result = llm_client.extract_json(  # type: ignore[union-attr]
            system_prompt=system_prompt,
            user_prompt=prompt,
        )
        # extract_json returns a dict (due to json_object mode), but we
        # expect an array. It may be wrapped in a key.
        if isinstance(result, dict):
            # Try common wrapper keys
            for key in ("phases", "arcs", "arc_summary", "result", "data"):
                if key in result and isinstance(result[key], list):
                    return result[key]
            # If the dict contains a single list value, use it
            for v in result.values():
                if isinstance(v, list):
                    return v
            return _fallback_arc_summaries(chunks)
        if isinstance(result, list):
            return result
        return _fallback_arc_summaries(chunks)
    except Exception:
        return _fallback_arc_summaries(chunks)


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------

def write_arc_sidecar(arc_data: dict, output_dir: str) -> str:
    """Write arc summaries to a ``{entity_id}.arcs.json`` sidecar file.

    Args:
        arc_data: The dict returned by ``summarize_relationship_arcs()``.
        output_dir: Directory to write the file into (e.g. the entity's
            catalog type directory).

    Returns:
        The path of the written file.
    """
    entity_id = arc_data.get("entity_id", "unknown")
    filename = f"{entity_id}.arcs.json"
    filepath = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(arc_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return filepath


def load_events(framework_dir: str) -> list[dict]:
    """Load events.json from the framework catalogs directory.

    Args:
        framework_dir: Path to the framework directory (e.g., ``framework/``).

    Returns:
        List of event dicts. Empty list if file not found or invalid.
    """
    events_path = os.path.join(framework_dir, "catalogs", "events.json")
    if not os.path.isfile(events_path):
        return []
    try:
        with open(events_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []
