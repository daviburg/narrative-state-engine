#!/usr/bin/env python3
"""
narrative_synthesis.py — LLM-powered narrative wiki page generation.

Transforms structured event data into readable wiki pages with full
provenance tracking.  Builds on the synthesis data foundation in
``synthesis.py`` (event grouping, phase segmentation, arc summaries).

Components:
  1. Input assembly — structured LLM prompt input per entity/phase
  2. Narrative biography generator — per-phase LLM calls
  3. Entity type adaptations — characters, locations, factions, items
  4. Page assembly — combine LLM output + data into final markdown
  5. Provenance validation — flag hallucinated / omitted turn citations
  6. Sidecar generation — .synthesis.json metadata
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from synthesis import (
    ID_ALIASES,
    build_event_derived_profile,
    resolve_entity_id,
    segment_phases,
    _infer_name_from_id,
    _infer_type_from_id,
    _parse_turn_number,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for narrative biography generation
# ---------------------------------------------------------------------------

BIOGRAPHY_SYSTEM_PROMPT = """\
You are a narrative biographer for an RPG campaign wiki. Your task is to
transform structured event data into readable prose.

Rules:
1. Use ONLY facts present in the provided data. Do not invent events,
   dialogue, motivations, or backstory not supported by the input.
2. Cite source turns inline using [turn-NNN] notation after each factual
   claim.
3. Write in third person past tense for biography sections.
4. Write in third person present tense for "Current Status" sections.
5. Organize biography by narrative phases, not by individual turns.
6. For relationships, describe the arc trajectory, not individual
   micro-interactions.
7. If uncertain about a connection (e.g., whether two entity IDs refer
   to the same person), note the uncertainty explicitly.
8. Keep each biography phase to 3–6 sentences.
9. Begin your response with a single line "TITLE: <title>" where <title>
   is a concise descriptive label for this phase (e.g., "Capture and
   Integration", "Building the Settlement"). Do NOT use generic labels
   like "Phase" or "Part". Do NOT include turn numbers in the title.
   Then write the biography prose below that line, without any markdown
   headings.
"""

LEDE_SYSTEM_PROMPT = """\
You are a narrative biographer for an RPG campaign wiki. Given biography
sections for a character, write a concise 2–3 sentence summary of this
entity's overall story arc. Write in third person present tense for the
summary. Do not add information not present in the biography sections.\
"""

LOCATION_SYSTEM_PROMPT = """\
You are a narrative encyclopedist for an RPG campaign wiki. Given events
that occurred at or involve a location, write a 2–4 sentence summary of
this location's significance in the campaign. Use only facts present in
the provided data. Cite source turns inline using [turn-NNN] notation.\
"""

FACTION_SYSTEM_PROMPT = """\
You are a narrative historian for an RPG campaign wiki. Given events
involving a faction, write a concise history section describing the
faction's stance, trajectory, and key moments. Use only facts present in
the provided data. Cite source turns inline using [turn-NNN] notation.
Keep to 3–6 sentences.\
"""

ITEM_SYSTEM_PROMPT = """\
You are a narrative encyclopedist for an RPG campaign wiki. Given events
involving a notable item, write a 2–4 sentence summary of this item's
significance in the campaign. Use only facts present in the provided
data. Cite source turns inline using [turn-NNN] notation.\
"""


# ---------------------------------------------------------------------------
# Step 1: Input Assembly
# ---------------------------------------------------------------------------

def _build_id_alias_section(entity_id: str) -> str:
    """Build the ID variant mapping section for an entity."""
    aliases = [raw for raw, canon in ID_ALIASES.items()
               if canon == entity_id or raw.lower() == entity_id]
    if not aliases:
        return f"Canonical ID: {entity_id} (no known variants)"
    return f"Canonical ID: {entity_id}\nKnown variants: {', '.join(aliases)}"


def _format_events_for_prompt(events: list[dict]) -> str:
    """Format a list of events as LLM-readable text lines."""
    lines = []
    for evt in events:
        turns = evt.get("source_turns", [])
        turn_str = ", ".join(turns) if turns else "unknown"
        etype = evt.get("type", "other")
        desc = evt.get("description", "")
        lines.append(f"[{turn_str}] ({etype}): {desc}")
    return "\n".join(lines)


def _format_catalog_section(catalog_data: dict | None) -> str:
    """Format catalog data or absence note."""
    if catalog_data is None:
        return ("No catalog entry exists for this entity. "
                "Generate the biography from events only.")

    parts = []
    identity = catalog_data.get("identity", "")
    if identity:
        parts.append(f"Identity: {identity}")

    stable = catalog_data.get("stable_attributes", {})
    if stable:
        attr_lines = []
        for key, attr in stable.items():
            if isinstance(attr, dict):
                val = attr.get("value", "")
            else:
                val = attr
            attr_lines.append(f"  {key}: {val}")
        parts.append("Stable attributes:\n" + "\n".join(attr_lines))

    current = catalog_data.get("current_status", "")
    if current:
        status_turn = catalog_data.get("status_updated_turn",
                                       catalog_data.get("last_updated_turn", ""))
        parts.append(f"Current status (as of {status_turn}): {current}")

    volatile = catalog_data.get("volatile_state", {})
    if volatile:
        vol_lines = []
        for k, v in volatile.items():
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            vol_lines.append(f"  {k}: {v}")
        parts.append("Volatile state:\n" + "\n".join(vol_lines))

    return "\n\n".join(parts) if parts else "Catalog entry exists but contains no data."


def _format_arc_section(arc_summaries: dict | None,
                        max_arcs: int = 5) -> str:
    """Format relationship arc summaries for the LLM prompt."""
    if not arc_summaries:
        return "No relationship arc summaries available."

    arcs = arc_summaries.get("arcs", {})
    if not arcs:
        return "No relationship arc summaries available."

    # Sort by interaction count, take top N
    sorted_targets = sorted(arcs.keys(),
                            key=lambda t: arcs[t].get("interaction_count", 0),
                            reverse=True)[:max_arcs]

    sections = []
    for target_id in sorted_targets:
        arc = arcs[target_id]
        target_name = _infer_name_from_id(target_id)
        current = arc.get("current_relationship", "")
        phases = arc.get("arc_summary", [])

        lines = [f"### {target_name} ({target_id})"]
        if current:
            lines.append(f"Current: {current}")
        for phase in phases:
            pname = phase.get("phase", "")
            tr = phase.get("turn_range", ["?", "?"])
            if not isinstance(tr, list) or len(tr) < 2:
                tr = [tr[0] if isinstance(tr, list) and tr else "?", "?"]
            summary = phase.get("summary", "")
            lines.append(f"- {pname} (turns {tr[0]}–{tr[1]}): {summary}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def assemble_synthesis_input(entity_id: str, phase: dict,
                             catalog_data: dict | None,
                             arc_summaries: dict | None) -> dict:
    """Assemble structured input for the narrative LLM.

    Args:
        entity_id: Canonical entity ID.
        phase: Phase dict with ``name``, ``turn_range``, ``events``.
        catalog_data: Entity catalog JSON or None.
        arc_summaries: Arc sidecar data or None.

    Returns:
        Dict with ``system_prompt``, ``user_prompt``, ``events_used``,
        ``turn_range``.
    """
    entity_name = (catalog_data or {}).get("name") or _infer_name_from_id(entity_id)
    entity_type = (catalog_data or {}).get("type") or _infer_type_from_id(entity_id)

    events = phase.get("events", [])
    turn_range = phase.get("turn_range", ["?", "?"])
    phase_name = phase.get("name") or f"Phase (turns {turn_range[0]}–{turn_range[1]})"

    user_prompt_parts = [
        "## Entity",
        f"Name: {entity_name}",
        f"ID: {entity_id}",
        f"Type: {entity_type}",
    ]
    identity = (catalog_data or {}).get("identity", "")
    if identity:
        user_prompt_parts.append(f"Identity: {identity}")

    user_prompt_parts += [
        "",
        "## Known ID Variants",
        _build_id_alias_section(entity_id),
        "",
        "## Catalog Data",
        _format_catalog_section(catalog_data),
        "",
        f"## Events (turns {turn_range[0]}–{turn_range[1]})",
        _format_events_for_prompt(events),
        "",
        "## Relationship Arcs",
        _format_arc_section(arc_summaries),
        "",
        "## Task",
        (f"Write the \"{phase_name}\" section of this entity's biography, "
         f"covering turns {turn_range[0]} through {turn_range[1]}. "
         "Cite turns inline."),
    ]

    # Collect event IDs used
    events_used = [e.get("id", "") for e in events if e.get("id")]

    # Collect available turns from input events
    available_turns: set[str] = set()
    for evt in events:
        for t in evt.get("source_turns", []):
            available_turns.add(t)

    return {
        "system_prompt": BIOGRAPHY_SYSTEM_PROMPT,
        "user_prompt": "\n".join(user_prompt_parts),
        "events_used": events_used,
        "turn_range": turn_range,
        "phase_name": phase_name,
        "available_turns": sorted(available_turns,
                                  key=_parse_turn_number),
    }


def assemble_lede_input(entity_id: str, biography_sections: list[str],
                        catalog_data: dict | None) -> dict:
    """Assemble input for the final lede/summary LLM call."""
    entity_name = (catalog_data or {}).get("name") or _infer_name_from_id(entity_id)
    combined = "\n\n".join(biography_sections)
    user_prompt = (
        f"Entity: {entity_name} ({entity_id})\n\n"
        f"Biography sections:\n\n{combined}\n\n"
        "Given these biography sections, write a 2–3 sentence summary of "
        "this entity's overall story arc."
    )
    return {
        "system_prompt": LEDE_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
    }


def assemble_location_input(entity_id: str, events: list[dict],
                            catalog_data: dict | None) -> dict:
    """Assemble input for a location significance summary."""
    entity_name = (catalog_data or {}).get("name") or _infer_name_from_id(entity_id)
    user_prompt = (
        f"Location: {entity_name} ({entity_id})\n\n"
        f"## Catalog Data\n{_format_catalog_section(catalog_data)}\n\n"
        f"## Events at this location\n{_format_events_for_prompt(events)}\n\n"
        "Write a 2–4 sentence summary of this location's significance."
    )
    return {
        "system_prompt": LOCATION_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
    }


def assemble_faction_input(entity_id: str, events: list[dict],
                           catalog_data: dict | None) -> dict:
    """Assemble input for a faction history section."""
    entity_name = (catalog_data or {}).get("name") or _infer_name_from_id(entity_id)
    user_prompt = (
        f"Faction: {entity_name} ({entity_id})\n\n"
        f"## Catalog Data\n{_format_catalog_section(catalog_data)}\n\n"
        f"## Events involving this faction\n{_format_events_for_prompt(events)}\n\n"
        "Write a concise history of this faction's role and trajectory."
    )
    return {
        "system_prompt": FACTION_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
    }


def assemble_item_input(entity_id: str, events: list[dict],
                        catalog_data: dict | None) -> dict:
    """Assemble input for a notable item significance summary."""
    entity_name = (catalog_data or {}).get("name") or _infer_name_from_id(entity_id)
    user_prompt = (
        f"Item: {entity_name} ({entity_id})\n\n"
        f"## Catalog Data\n{_format_catalog_section(catalog_data)}\n\n"
        f"## Events involving this item\n{_format_events_for_prompt(events)}\n\n"
        "Write a 2–4 sentence summary of this item's significance."
    )
    return {
        "system_prompt": ITEM_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
    }


# ---------------------------------------------------------------------------
# Step 2: Narrative Biography Generator (LLM)
# ---------------------------------------------------------------------------

def generate_phase_biography(llm_client, synthesis_input: dict) -> tuple[str, dict]:
    """Generate biography prose for a single phase via LLM.

    Args:
        llm_client: An LLMClient instance with a ``generate_text`` method.
        synthesis_input: Dict from ``assemble_synthesis_input()``.

    Returns:
        Tuple of (markdown_text, phase_metadata).
        On failure, returns a fallback message and metadata with error info.
    """
    fallback_name = synthesis_input.get("phase_name", "")
    try:
        raw_text = llm_client.generate_text(
            system_prompt=synthesis_input["system_prompt"],
            user_prompt=synthesis_input["user_prompt"],
        )
        llm_client.delay()

        title, prose = _parse_biography_response(raw_text, fallback_name)
        prose = _normalize_subheadings(prose)

        metadata = {
            "name": synthesis_input.get("phase_name", ""),
            "title": title,
            "turn_range": synthesis_input.get("turn_range", []),
            "events_used": synthesis_input.get("events_used", []),
            "llm_model": getattr(llm_client, "model", "unknown"),
            "tokens_used": _estimate_tokens(synthesis_input["user_prompt"], raw_text),
        }
        return prose, metadata

    except Exception as e:
        logger.error("Phase biography generation failed: %s", e)
        fallback = ("[Generation failed — source events available in "
                    "timeline below]")
        metadata = {
            "name": synthesis_input.get("phase_name", ""),
            "title": fallback_name,
            "turn_range": synthesis_input.get("turn_range", []),
            "events_used": synthesis_input.get("events_used", []),
            "llm_model": getattr(llm_client, "model", "unknown"),
            "tokens_used": 0,
            "error": str(e),
        }
        return fallback, metadata


def generate_lede(llm_client, lede_input: dict) -> str:
    """Generate the summary/lede paragraph from combined biography sections."""
    try:
        text = llm_client.generate_text(
            system_prompt=lede_input["system_prompt"],
            user_prompt=lede_input["user_prompt"],
        )
        llm_client.delay()
        return text
    except Exception as e:
        logger.error("Lede generation failed: %s", e)
        return ""


def generate_location_summary(llm_client, loc_input: dict) -> str:
    """Generate significance summary for a location."""
    try:
        text = llm_client.generate_text(
            system_prompt=loc_input["system_prompt"],
            user_prompt=loc_input["user_prompt"],
        )
        llm_client.delay()
        return text
    except Exception as e:
        logger.error("Location summary generation failed: %s", e)
        return ""


def generate_faction_history(llm_client, faction_input: dict) -> str:
    """Generate history section for a faction."""
    try:
        text = llm_client.generate_text(
            system_prompt=faction_input["system_prompt"],
            user_prompt=faction_input["user_prompt"],
        )
        llm_client.delay()
        return text
    except Exception as e:
        logger.error("Faction history generation failed: %s", e)
        return ""


def generate_item_summary(llm_client, item_input: dict) -> str:
    """Generate significance summary for a notable item."""
    try:
        text = llm_client.generate_text(
            system_prompt=item_input["system_prompt"],
            user_prompt=item_input["user_prompt"],
        )
        llm_client.delay()
        return text
    except Exception as e:
        logger.error("Item summary generation failed: %s", e)
        return ""


def _estimate_tokens(prompt: str, response: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return (len(prompt) + len(response)) // 4


def _parse_biography_response(raw_response: str, fallback_name: str) -> tuple[str, str]:
    """Extract title and prose from LLM biography response.

    The LLM is instructed to begin with ``TITLE: <descriptive title>``
    followed by the biography prose.  If the prefix is missing, the
    *fallback_name* is used and the entire response is treated as prose.
    """
    lines = raw_response.strip().split("\n", 1)
    if lines and lines[0].upper().startswith("TITLE:"):
        title = lines[0].split(":", 1)[1].strip()
        prose = lines[1].strip() if len(lines) > 1 else ""
        if not title:
            title = fallback_name
    else:
        title = fallback_name
        prose = raw_response.strip()
    return title, prose


def _normalize_subheadings(prose: str) -> str:
    """Ensure LLM-generated subheadings don't conflict with phase headings."""
    return re.sub(r'^###\s', '#### ', prose, flags=re.MULTILINE)


# Critical event types that should be cited in the biography
_CRITICAL_EVENT_TYPES = frozenset({
    "birth", "death", "arrival", "departure", "decision", "ritual",
    "discovery", "recruitment", "combat",
})


def _collect_critical_turns(events: list[dict]) -> list[str]:
    """Collect source turns from critical events for omission checking."""
    turns: list[str] = []
    for evt in events:
        if evt.get("type", "") in _CRITICAL_EVENT_TYPES:
            for t in evt.get("source_turns", []):
                turns.append(t)
    return sorted(set(turns), key=_parse_turn_number)


# ---------------------------------------------------------------------------
# Step 3: should_synthesize — entity type threshold
# ---------------------------------------------------------------------------

def should_synthesize(entity_id: str, event_count: int, entity_type: str) -> bool:
    """Determine whether an entity warrants narrative synthesis."""
    if entity_type == "character":
        return event_count >= 3
    if entity_type in ("location", "faction"):
        return event_count >= 3
    if entity_type == "item":
        return event_count >= 3
    return False


# ---------------------------------------------------------------------------
# Step 4: Page Assembly
# ---------------------------------------------------------------------------


# -- Cross-page link helpers ------------------------------------------------

def _resolve_link(target_id: str, name_index: dict[str, tuple[str, str]],
                  source_type_dir: str) -> str | None:
    """Return a markdown link string for *target_id*, or None."""
    if not name_index or not source_type_dir or target_id not in name_index:
        return None
    name, generic_path = name_index[target_id]
    # generic_path is always ../type/id.md — use simple id.md for same-dir
    target_type_dir = generic_path.split("/")[1] if "/" in generic_path else ""
    src_dir = os.path.basename(source_type_dir)
    if target_type_dir == src_dir:
        return f"[{name}]({target_id}.md)"
    return f"[{name}]({generic_path})"


def _safe_replace_first(text: str, name: str, link: str) -> str:
    """Replace first occurrence of *name* that isn't inside a markdown link."""
    idx = 0
    while True:
        pos = text.find(name, idx)
        if pos == -1:
            return text
        before = text[:pos]
        if before.count("[") > before.count("]"):
            idx = pos + len(name)
            continue
        return text[:pos] + link + text[pos + len(name):]


def _linkify_prose(prose: str, name_index: dict[str, tuple[str, str]] | None,
                   source_type_dir: str | None, self_id: str,
                   linked_entities: set[str]) -> str:
    """Link first mention of each known entity name in *prose*."""
    if not name_index or not source_type_dir:
        return prose

    candidates: list[tuple[str, str, str]] = []  # (name, eid, link)
    for eid, (name, _rel) in name_index.items():
        if eid == self_id or eid in linked_entities or len(name) < 4:
            continue
        link = _resolve_link(eid, name_index, source_type_dir)
        if link:
            candidates.append((name, eid, link))

    # Longest-first to avoid partial matches
    candidates.sort(key=lambda x: len(x[0]), reverse=True)

    for name, eid, link in candidates:
        new_prose = _safe_replace_first(prose, name, link)
        if new_prose != prose:
            prose = new_prose
            linked_entities.add(eid)

    return prose


def _escape_table_cell(text: str) -> str:
    """Escape text for safe inclusion in a markdown table cell."""
    text = str(text)
    text = text.replace("|", "\\|")
    text = text.replace("\n", " ")
    text = text.replace("\r", "")
    return text


def _build_infobox(entity_id: str, catalog_data: dict | None,
                   derived_profile: dict | None) -> str:
    """Build an infobox table from catalog or event-derived profile."""
    lines = ["| | |", "|---|---|"]

    if catalog_data:
        entity_type = catalog_data.get("type", "character").replace("_", " ").title()
        lines.append(f"| **Type** | {entity_type} |")
        first_seen = catalog_data.get("first_seen_turn", "")
        last_updated = catalog_data.get("last_updated_turn", "")
        if first_seen:
            lines.append(f"| **First Seen** | {first_seen} |")
        if last_updated:
            lines.append(f"| **Last Updated** | {last_updated} |")
        stable = catalog_data.get("stable_attributes", {})
        for key, attr in stable.items():
            if key == "aliases":
                continue
            if isinstance(attr, dict):
                val = attr.get("value", "")
            else:
                val = attr
            display_val = _escape_table_cell(str(val) if not isinstance(val, list)
                                             else ", ".join(str(v) for v in val))
            if len(display_val) <= 80:
                lines.append(f"| **{key.replace('_', ' ').title()}** | {display_val} |")
    elif derived_profile:
        entity_type = derived_profile.get("type", "unknown").replace("_", " ").title()
        lines.append(f"| **Type** | {entity_type} |")
        lines.append(f"| **Source** | Events only (no catalog entry) |")
        first = derived_profile.get("first_event_turn", "")
        last = derived_profile.get("last_event_turn", "")
        if first:
            lines.append(f"| **First Event** | {first} |")
        if last:
            lines.append(f"| **Last Event** | {last} |")
        lines.append(f"| **Event Count** | {derived_profile.get('event_count', 0)} |")

    return "\n".join(lines)


def _build_event_timeline(events: list[dict], *,
                          name_index: dict[str, tuple[str, str]] | None = None,
                          source_type_dir: str | None = None,
                          self_id: str = "",
                          linked_entities: set[str] | None = None) -> str:
    """Build a chronological event timeline table."""
    if linked_entities is None:
        linked_entities = set()
    lines = ["| Turn | Type | Description |", "|---|---|---|"]
    for evt in events:
        turns = evt.get("source_turns", [])
        turn_str = ", ".join(turns) if turns else "—"
        etype = _escape_table_cell(evt.get("type", "other"))
        desc = evt.get("description", "")
        # Linkify related entities in description
        if name_index and source_type_dir:
            for eid in evt.get("related_entities", []):
                if eid == self_id or eid in linked_entities:
                    continue
                if eid not in name_index:
                    continue
                ename, _ = name_index[eid]
                if len(ename) < 4:
                    continue
                link = _resolve_link(eid, name_index, source_type_dir)
                if link and ename in desc:
                    desc = _safe_replace_first(desc, ename, link)
                    linked_entities.add(eid)
        desc = _escape_table_cell(desc)
        lines.append(f"| {turn_str} | {etype} | {desc} |")
    return "\n".join(lines)


def _build_relationship_table(arc_summaries: dict | None, *,
                              name_index: dict[str, tuple[str, str]] | None = None,
                              source_type_dir: str | None = None,
                              self_id: str = "",
                              linked_entities: set[str] | None = None) -> str:
    """Build a relationship arcs table from sidecar data."""
    if not arc_summaries:
        return ""

    arcs = arc_summaries.get("arcs", {})
    if not arcs:
        return ""

    if linked_entities is None:
        linked_entities = set()

    lines = ["| Entity | Current Relationship | Interactions |",
             "|---|---|---|"]
    for target_id in sorted(arcs.keys()):
        arc = arcs[target_id]
        link = _resolve_link(target_id, name_index, source_type_dir) if name_index else None
        if link:
            display = link
            linked_entities.add(target_id)
        else:
            name = _infer_name_from_id(target_id)
            display = f"{name} ({target_id})"
        current = _escape_table_cell(arc.get("current_relationship", ""))
        count = arc.get("interaction_count", 0)
        lines.append(f"| {display} | {current} | {count} |")

    return "\n".join(lines)


def _build_current_status(catalog_data: dict | None, events: list[dict]) -> str:
    """Build current status section from catalog or latest event."""
    if catalog_data:
        status = catalog_data.get("current_status", "")
        turn = catalog_data.get("status_updated_turn",
                                catalog_data.get("last_updated_turn", ""))
        if status:
            return f"*As of {turn}:*\n\n{status}"

    # Derive from latest event
    if events:
        last = events[-1]
        turns = last.get("source_turns", [])
        turn = turns[0] if turns else "unknown"
        desc = last.get("description", "")
        return f"*Derived from latest event ({turn}):*\n\n{desc}"

    return ""


def assemble_character_page(entity_id: str, entity_name: str,
                            lede: str, phase_texts: list[tuple[str, str]],
                            catalog_data: dict | None,
                            derived_profile: dict | None,
                            arc_summaries: dict | None,
                            all_events: list[dict], *,
                            name_index: dict[str, tuple[str, str]] | None = None,
                            source_type_dir: str | None = None) -> str:
    """Assemble a full character wiki page.

    Args:
        entity_id: Canonical entity ID.
        entity_name: Display name.
        lede: Summary paragraph from LLM.
        phase_texts: List of (phase_name, markdown_text) tuples.
        catalog_data: Catalog JSON or None.
        derived_profile: Event-derived profile or None.
        arc_summaries: Arc sidecar data or None.
        all_events: All events for this entity (for timeline).
        name_index: Entity ID → (name, relative_md_path) mapping.
        source_type_dir: Directory name of this entity's type.

    Returns:
        Complete markdown page string.
    """
    linked: set[str] = set()

    lines = [f"# {entity_name}", ""]

    if lede:
        lines += [f"> {lede}", ""]

    # Overview / infobox
    lines += ["## Overview", "",
              _build_infobox(entity_id, catalog_data, derived_profile), ""]

    # Biography — linkify prose
    lines += ["## Biography", ""]
    for phase_name, text in phase_texts:
        if phase_name:
            lines += [f"### {phase_name}", ""]
        text = _linkify_prose(text, name_index, source_type_dir,
                              entity_id, linked)
        lines += [text, ""]

    # Relationships
    rel_table = _build_relationship_table(
        arc_summaries, name_index=name_index,
        source_type_dir=source_type_dir, self_id=entity_id,
        linked_entities=linked)
    if rel_table:
        lines += ["## Relationships", "", rel_table, ""]

    # Current Status
    status = _build_current_status(catalog_data, all_events)
    if status:
        lines += ["## Current Status", "", status, ""]

    # Event Timeline
    lines += ["## Event Timeline", "",
              _build_event_timeline(all_events, name_index=name_index,
                                    source_type_dir=source_type_dir,
                                    self_id=entity_id,
                                    linked_entities=linked), ""]

    # Footer
    lines += ["---",
              f"*Generated from events data — do not edit manually.*"]

    return "\n".join(lines) + "\n"


def assemble_location_page(entity_id: str, entity_name: str,
                           significance: str,
                           catalog_data: dict | None,
                           derived_profile: dict | None,
                           all_events: list[dict], *,
                           name_index: dict[str, tuple[str, str]] | None = None,
                           source_type_dir: str | None = None) -> str:
    """Assemble a location wiki page."""
    linked: set[str] = set()
    lines = [f"# {entity_name}", ""]

    identity = (catalog_data or {}).get("identity", "")
    if identity:
        lines += [f"> {identity}", ""]

    # Overview / infobox
    lines += ["## Overview", "",
              _build_infobox(entity_id, catalog_data, derived_profile), ""]

    # Significance — linkify prose
    if significance:
        significance = _linkify_prose(significance, name_index,
                                      source_type_dir, entity_id, linked)
        lines += ["## Significance", "", significance, ""]

    # Key Events
    lines += ["## Key Events", "",
              _build_event_timeline(all_events, name_index=name_index,
                                    source_type_dir=source_type_dir,
                                    self_id=entity_id,
                                    linked_entities=linked), ""]

    # Connected entities (from catalog relationships)
    if catalog_data and catalog_data.get("relationships"):
        lines += ["## Connected Entities", "",
                  "| Entity | Connection |", "|---|---|"]
        for rel in catalog_data["relationships"]:
            target = rel.get("target_id", "")
            link = _resolve_link(target, name_index, source_type_dir) if name_index else None
            if link:
                display = link
                linked.add(target)
            else:
                name = _infer_name_from_id(target)
                display = f"{name} ({target})"
            cur = _escape_table_cell(rel.get("current_relationship", ""))
            lines.append(f"| {display} | {cur} |")
        lines.append("")

    lines += ["---",
              f"*Generated from events data — do not edit manually.*"]
    return "\n".join(lines) + "\n"


def assemble_faction_page(entity_id: str, entity_name: str,
                          history: str,
                          catalog_data: dict | None,
                          derived_profile: dict | None,
                          all_events: list[dict], *,
                          name_index: dict[str, tuple[str, str]] | None = None,
                          source_type_dir: str | None = None) -> str:
    """Assemble a faction wiki page."""
    linked: set[str] = set()
    lines = [f"# {entity_name}", ""]

    identity = (catalog_data or {}).get("identity", "")
    if identity:
        lines += [f"> {identity}", ""]

    # Overview / infobox
    lines += ["## Overview", "",
              _build_infobox(entity_id, catalog_data, derived_profile), ""]

    # History — linkify prose
    if history:
        history = _linkify_prose(history, name_index, source_type_dir,
                                 entity_id, linked)
        lines += ["## History", "", history, ""]

    # Members (from event co-occurrences)
    member_ids: set[str] = set()
    for evt in all_events:
        for raw_id in evt.get("related_entities", []):
            canon = resolve_entity_id(raw_id)
            if canon != entity_id and _infer_type_from_id(canon) == "character":
                member_ids.add(canon)

    if member_ids:
        lines += ["## Known Members", "",
                  "| Member | First Seen |", "|---|---|"]
        for mid in sorted(member_ids):
            link = _resolve_link(mid, name_index, source_type_dir) if name_index else None
            if link:
                display = link
                linked.add(mid)
            else:
                name = _infer_name_from_id(mid)
                display = f"{name} ({mid})"
            # Find first event with this member
            first_turn = ""
            for evt in all_events:
                resolved = [resolve_entity_id(r) for r in evt.get("related_entities", [])]
                if mid in resolved:
                    turns = evt.get("source_turns", [])
                    first_turn = turns[0] if turns else ""
                    break
            lines.append(f"| {display} | {first_turn} |")
        lines.append("")

    # Key Events
    lines += ["## Key Events", "",
              _build_event_timeline(all_events, name_index=name_index,
                                    source_type_dir=source_type_dir,
                                    self_id=entity_id,
                                    linked_entities=linked), ""]

    lines += ["---",
              f"*Generated from events data — do not edit manually.*"]
    return "\n".join(lines) + "\n"


def assemble_item_page(entity_id: str, entity_name: str,
                       significance: str,
                       catalog_data: dict | None,
                       derived_profile: dict | None,
                       all_events: list[dict], *,
                       name_index: dict[str, tuple[str, str]] | None = None,
                       source_type_dir: str | None = None) -> str:
    """Assemble an item wiki page."""
    linked: set[str] = set()
    lines = [f"# {entity_name}", ""]

    identity = (catalog_data or {}).get("identity", "")
    if identity:
        lines += [f"> {identity}", ""]

    # Overview / infobox
    lines += ["## Overview", "",
              _build_infobox(entity_id, catalog_data, derived_profile), ""]

    # Significance — linkify prose
    if significance:
        significance = _linkify_prose(significance, name_index,
                                      source_type_dir, entity_id, linked)
        lines += ["## Significance", "", significance, ""]

    # Key Events
    lines += ["## Key Events", "",
              _build_event_timeline(all_events, name_index=name_index,
                                    source_type_dir=source_type_dir,
                                    self_id=entity_id,
                                    linked_entities=linked), ""]

    lines += ["---",
              f"*Generated from events data — do not edit manually.*"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Step 5: Provenance Validation
# ---------------------------------------------------------------------------

def extract_cited_turns(markdown: str) -> list[str]:
    """Extract all [turn-NNN] references from generated markdown."""
    return sorted(set(re.findall(r"\[turn-\d+\]", markdown)),
                  key=lambda t: _parse_turn_number(t.strip("[]")))


def validate_provenance(markdown: str, available_turns: list[str],
                        critical_event_turns: list[str] | None = None
                        ) -> dict:
    """Run post-generation provenance validation.

    Args:
        markdown: Generated markdown text.
        available_turns: Turns that were provided as input to the LLM.
        critical_event_turns: Turns of critical events that should be cited.

    Returns:
        Provenance check dict with hallucination_flags and
        uncited_critical_events.
    """
    cited = extract_cited_turns(markdown)
    cited_bare = [t.strip("[]") for t in cited]

    available_set = set(available_turns)
    hallucination_flags = [t for t in cited_bare if t not in available_set]
    uncited = []
    if critical_event_turns:
        cited_set = set(cited_bare)
        uncited = [t for t in critical_event_turns if t not in cited_set]

    return {
        "turns_cited": cited_bare,
        "turns_available": sorted(available_turns,
                                  key=_parse_turn_number),
        "hallucination_flags": hallucination_flags,
        "uncited_critical_events": uncited,
    }


def add_provenance_warning(markdown: str, entity_id: str) -> str:
    """Prepend a provenance warning banner to the page."""
    warning = (
        f"> \u26a0\ufe0f **Provenance warning**: This page cites turns not "
        f"present in source data.\n"
        f"> Review flagged citations in {entity_id}.synthesis.json.\n\n"
    )
    return warning + markdown


# ---------------------------------------------------------------------------
# Step 6: Sidecar Generation
# ---------------------------------------------------------------------------

def build_synthesis_sidecar(entity_id: str, events: list[dict],
                            catalog_available: bool,
                            catalog_last_updated: str,
                            arc_count: int,
                            phase_metadata: list[dict],
                            provenance: dict) -> dict:
    """Build the .synthesis.json sidecar data."""
    turn_range = ["", ""]
    if events:
        first_turns = events[0].get("source_turns", [])
        last_turns = events[-1].get("source_turns", [])
        if first_turns:
            turn_range[0] = first_turns[0]
        if last_turns:
            turn_range[1] = last_turns[0]

    return {
        "entity_id": entity_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_data": {
            "events_count": len(events),
            "events_turn_range": turn_range,
            "catalog_available": catalog_available,
            "catalog_last_updated": catalog_last_updated,
            "relationship_arcs_count": arc_count,
        },
        "phases": phase_metadata,
        "provenance_check": provenance,
    }


def write_synthesis_sidecar(sidecar: dict, output_path: str) -> str:
    """Write the .synthesis.json sidecar file."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return output_path


def load_synthesis_sidecar(path: str) -> dict | None:
    """Load an existing synthesis sidecar. Returns None if not found."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Step 7: Full synthesis pipeline for a single entity
# ---------------------------------------------------------------------------

def synthesize_entity(entity_id: str, entity_events: list[dict],
                      catalog_data: dict | None,
                      arc_summaries: dict | None,
                      llm_client,
                      entity_type: str | None = None, *,
                      name_index: dict[str, tuple[str, str]] | None = None,
                      source_type_dir: str | None = None) -> tuple[str, dict]:
    """Run the full synthesis pipeline for a single entity.

    Args:
        entity_id: Canonical entity ID.
        entity_events: Sorted events for this entity.
        catalog_data: Catalog entry dict or None.
        arc_summaries: Arc sidecar data or None.
        llm_client: LLMClient instance.
        entity_type: Override entity type (otherwise inferred).
        name_index: Entity ID → (name, relative_md_path) mapping.
        source_type_dir: Directory name of this entity's type.

    Returns:
        Tuple of (markdown_page, sidecar_dict).
    """
    etype = entity_type or (catalog_data or {}).get("type") or _infer_type_from_id(entity_id)
    entity_name = (catalog_data or {}).get("name") or _infer_name_from_id(entity_id)
    derived_profile = None if catalog_data else build_event_derived_profile(entity_id, entity_events)

    link_kw = dict(name_index=name_index, source_type_dir=source_type_dir)

    if etype == "location":
        return _synthesize_location(entity_id, entity_name, entity_events,
                                    catalog_data, derived_profile, llm_client,
                                    **link_kw)
    elif etype == "faction":
        return _synthesize_faction(entity_id, entity_name, entity_events,
                                   catalog_data, derived_profile, llm_client,
                                   **link_kw)
    elif etype == "item":
        return _synthesize_item(entity_id, entity_name, entity_events,
                                catalog_data, derived_profile, llm_client,
                                **link_kw)
    else:
        return _synthesize_character(entity_id, entity_name, entity_events,
                                     catalog_data, derived_profile,
                                     arc_summaries, llm_client,
                                     **link_kw)


def _synthesize_character(entity_id, entity_name, events, catalog_data,
                          derived_profile, arc_summaries, llm_client, *,
                          name_index=None, source_type_dir=None):
    """Synthesize a character page with per-phase biography."""
    phases = segment_phases(events, entity_id)

    all_available_turns: set[str] = set()
    phase_texts = []
    phase_metadata = []

    for phase in phases:
        synth_input = assemble_synthesis_input(
            entity_id, phase, catalog_data, arc_summaries)
        for t in synth_input["available_turns"]:
            all_available_turns.add(t)

        text, meta = generate_phase_biography(llm_client, synth_input)
        turn_range = synth_input.get("turn_range", ["?", "?"])
        title = meta.get("title", synth_input["phase_name"])
        fallback = synth_input["phase_name"]
        # Only append turn range if the title is descriptive (not the
        # generic fallback which already contains the range).
        if title != fallback:
            display_name = f"{title} (turns {turn_range[0]}\u2013{turn_range[1]})"
        else:
            display_name = fallback
        phase_texts.append((display_name, text))
        phase_metadata.append(meta)

    # Generate lede
    bio_sections = [text for _, text in phase_texts]
    lede_input = assemble_lede_input(entity_id, bio_sections, catalog_data)
    lede = generate_lede(llm_client, lede_input)

    # Assemble page
    page = assemble_character_page(
        entity_id, entity_name, lede, phase_texts,
        catalog_data, derived_profile, arc_summaries, events,
        name_index=name_index, source_type_dir=source_type_dir)

    # Provenance validation across all phases
    available = sorted(all_available_turns, key=_parse_turn_number)
    # Identify critical events (births, deaths, arrivals) for omission checking
    critical_turns = _collect_critical_turns(events)
    provenance = validate_provenance(page, available, critical_turns)

    if provenance["hallucination_flags"]:
        page = add_provenance_warning(page, entity_id)

    # Sidecar
    catalog_last_updated = (catalog_data or {}).get("last_updated_turn", "")
    arc_count = len((arc_summaries or {}).get("arcs", {}))
    sidecar = build_synthesis_sidecar(
        entity_id, events, catalog_data is not None,
        catalog_last_updated, arc_count, phase_metadata, provenance)

    return page, sidecar


def _synthesize_location(entity_id, entity_name, events, catalog_data,
                         derived_profile, llm_client, *,
                         name_index=None, source_type_dir=None):
    """Synthesize a location page."""
    loc_input = assemble_location_input(entity_id, events, catalog_data)
    significance = generate_location_summary(llm_client, loc_input)

    page = assemble_location_page(entity_id, entity_name, significance,
                                  catalog_data, derived_profile, events,
                                  name_index=name_index,
                                  source_type_dir=source_type_dir)

    available_turns = set()
    for evt in events:
        for t in evt.get("source_turns", []):
            available_turns.add(t)
    available = sorted(available_turns, key=_parse_turn_number)
    provenance = validate_provenance(page, available)
    if provenance["hallucination_flags"]:
        page = add_provenance_warning(page, entity_id)

    sidecar = build_synthesis_sidecar(
        entity_id, events, catalog_data is not None,
        (catalog_data or {}).get("last_updated_turn", ""),
        0, [], provenance)

    return page, sidecar


def _synthesize_faction(entity_id, entity_name, events, catalog_data,
                        derived_profile, llm_client, *,
                        name_index=None, source_type_dir=None):
    """Synthesize a faction page."""
    fac_input = assemble_faction_input(entity_id, events, catalog_data)
    history = generate_faction_history(llm_client, fac_input)

    page = assemble_faction_page(entity_id, entity_name, history,
                                 catalog_data, derived_profile, events,
                                 name_index=name_index,
                                 source_type_dir=source_type_dir)

    available_turns = set()
    for evt in events:
        for t in evt.get("source_turns", []):
            available_turns.add(t)
    available = sorted(available_turns, key=_parse_turn_number)
    provenance = validate_provenance(page, available)
    if provenance["hallucination_flags"]:
        page = add_provenance_warning(page, entity_id)

    sidecar = build_synthesis_sidecar(
        entity_id, events, catalog_data is not None,
        (catalog_data or {}).get("last_updated_turn", ""),
        0, [], provenance)

    return page, sidecar


def _synthesize_item(entity_id, entity_name, events, catalog_data,
                     derived_profile, llm_client, *,
                     name_index=None, source_type_dir=None):
    """Synthesize an item page."""
    item_input = assemble_item_input(entity_id, events, catalog_data)
    significance = generate_item_summary(llm_client, item_input)

    page = assemble_item_page(entity_id, entity_name, significance,
                              catalog_data, derived_profile, events,
                              name_index=name_index,
                              source_type_dir=source_type_dir)

    available_turns = set()
    for evt in events:
        for t in evt.get("source_turns", []):
            available_turns.add(t)
    available = sorted(available_turns, key=_parse_turn_number)
    provenance = validate_provenance(page, available)
    if provenance["hallucination_flags"]:
        page = add_provenance_warning(page, entity_id)

    sidecar = build_synthesis_sidecar(
        entity_id, events, catalog_data is not None,
        (catalog_data or {}).get("last_updated_turn", ""),
        0, [], provenance)

    return page, sidecar


# ---------------------------------------------------------------------------
# Incremental awareness
# ---------------------------------------------------------------------------

def needs_regeneration(entity_id: str, event_count: int,
                       sidecar_path: str, force: bool = False) -> bool:
    """Check if an entity needs synthesis regeneration.

    Args:
        entity_id: The entity ID.
        event_count: Current number of events for this entity.
        sidecar_path: Path to existing .synthesis.json.
        force: If True, always regenerate.

    Returns:
        True if regeneration is needed.
    """
    if force:
        return True

    existing = load_synthesis_sidecar(sidecar_path)
    if existing is None:
        return True

    prev_count = existing.get("source_data", {}).get("events_count", 0)
    if event_count != prev_count:
        return True

    # Regenerate if any cached phase is missing a descriptive title
    for phase in existing.get("phases", []):
        if "title" not in phase:
            return True

    return False
