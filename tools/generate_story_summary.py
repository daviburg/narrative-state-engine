#!/usr/bin/env python3
"""
generate_story_summary.py — Generate a high-level story arc summary from
extracted narrative data.

Reads entity catalogs, events, plot threads, and timeline data from the
framework directory and produces a narrative arc summary in
``framework/story/summary.md``.

Two modes:
  1. **LLM mode** (default): Uses the configured LLM to synthesize a
     narrative summary from structured data.
  2. **Data-only mode** (``--no-llm``): Produces a structured markdown
     summary from catalog data without LLM calls.

Usage:
    python tools/generate_story_summary.py --framework framework/
    python tools/generate_story_summary.py --framework framework/ --no-llm
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

from synthesis import (
    group_events_by_entity,
    load_events,
    resolve_entity_id,
    _infer_name_from_id,
    _parse_turn_number,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for story summary generation
# ---------------------------------------------------------------------------

STORY_SUMMARY_SYSTEM_PROMPT = """\
You are a narrative analyst for an RPG campaign. Your task is to produce
a concise high-level story arc summary from structured campaign data.

Rules:
1. Write a brief overview paragraph (2–4 sentences) summarizing the overall
   campaign arc.
2. Identify the major narrative arcs/threads and describe each in 1–3
   sentences.  Focus on trajectory and unresolved tensions, not individual
   events.
3. For each arc, note its current status (active, dormant, resolved).
4. Include a short section on the player character's current situation and
   key relationships.
5. Note any open questions or unresolved tensions that drive the story
   forward.
6. Use ONLY facts present in the provided data. Do not invent events,
   dialogue, motivations, or backstory.
7. Cite source turns inline using [turn-NNN] notation for key claims.
8. Keep the total summary concise — aim for roughly 300–500 words.
9. Write in third person present tense for current state, past tense
   for history.
10. Do not use markdown headings — the caller will add section structure.
    Separate sections with a blank line.
"""


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> list | dict:
    """Load a JSON file, returning empty list on failure."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def load_plot_threads(framework_dir: str) -> list[dict]:
    """Load plot-threads.json from the framework catalogs."""
    return _load_json(os.path.join(framework_dir, "catalogs", "plot-threads.json"))


def load_timeline(framework_dir: str) -> list[dict]:
    """Load timeline.json from the framework catalogs."""
    return _load_json(os.path.join(framework_dir, "catalogs", "timeline.json"))


def load_entity_catalog(framework_dir: str, entity_type: str) -> list[dict]:
    """Load a per-type entity catalog (characters.json, etc.)."""
    return _load_json(
        os.path.join(framework_dir, "catalogs", f"{entity_type}.json"))


# ---------------------------------------------------------------------------
# Data summarisation (structured, no LLM)
# ---------------------------------------------------------------------------

def _get_pc_data(characters: list[dict]) -> dict | None:
    """Find the player character entry from the characters catalog."""
    for ch in characters:
        if ch.get("id") == "char-player":
            return ch
    return None


def _top_entities_by_events(
    grouped_events: dict[str, list[dict]],
    exclude: set[str] | None = None,
    limit: int = 8,
) -> list[tuple[str, int]]:
    """Return the top N entity IDs by event count."""
    exclude = exclude or set()
    counts = [
        (eid, len(evts))
        for eid, evts in grouped_events.items()
        if eid not in exclude
    ]
    counts.sort(key=lambda x: x[1], reverse=True)
    return counts[:limit]


def _critical_events(events: list[dict], limit: int = 15) -> list[dict]:
    """Select the most narratively significant events."""
    critical_types = {
        "birth", "death", "arrival", "departure", "decision",
        "ritual", "discovery", "recruitment", "combat",
    }
    critical = [e for e in events if e.get("type") in critical_types]
    # Sort by turn number
    critical.sort(
        key=lambda e: _parse_turn_number(
            e.get("source_turns", [""])[0] if e.get("source_turns") else ""
        )
    )
    return critical[:limit]


def _format_turn_range(events: list[dict]) -> tuple[str, str]:
    """Get the earliest and latest turn referenced by the event list."""
    if not events:
        return ("?", "?")

    first_turn = "?"
    last_turn = "?"
    first_turn_number = None
    last_turn_number = None

    for event in events:
        for turn in event.get("source_turns", []):
            turn_number = _parse_turn_number(turn)
            if turn_number <= 0:
                continue
            if first_turn_number is None or turn_number < first_turn_number:
                first_turn_number = turn_number
                first_turn = turn
            if last_turn_number is None or turn_number > last_turn_number:
                last_turn_number = turn_number
                last_turn = turn

    return (first_turn, last_turn)


# ---------------------------------------------------------------------------
# LLM prompt assembly
# ---------------------------------------------------------------------------

def assemble_story_summary_input(
    events: list[dict],
    plot_threads: list[dict],
    characters: list[dict],
    timeline: list[dict],
) -> dict:
    """Build the structured LLM prompt for story summary generation.

    Returns:
        Dict with ``system_prompt`` and ``user_prompt``.
    """
    grouped = group_events_by_entity(events)
    pc_data = _get_pc_data(characters)
    first_turn, last_turn = _format_turn_range(events)

    parts: list[str] = []

    # -- Overview context --
    parts.append(f"## Campaign Scope")
    parts.append(f"Turns covered: {first_turn} through {last_turn}")
    parts.append(f"Total events: {len(events)}")
    parts.append(f"Total entities tracked: {len(grouped)}")
    parts.append("")

    # -- Player character --
    if pc_data:
        parts.append("## Player Character")
        parts.append(f"Name: {pc_data.get('name', 'Unknown')}")
        identity = pc_data.get("identity", "")
        if identity:
            parts.append(f"Identity: {identity}")
        status = pc_data.get("current_status", "")
        if status:
            status_turn = pc_data.get("status_updated_turn",
                                      pc_data.get("last_updated_turn", ""))
            parts.append(f"Current status (as of {status_turn}): {status}")
        # Key relationships
        rels = pc_data.get("relationships", [])
        if rels:
            parts.append("Key relationships:")
            for rel in rels[:8]:
                target = rel.get("target_id", "")
                canonical_target = resolve_entity_id(target) if target else ""
                name = (
                    _infer_name_from_id(canonical_target)
                    if canonical_target else "Unknown"
                )
                current = rel.get("current_relationship", "")
                rtype = rel.get("type", "")
                parts.append(
                    f"  - {name} ({canonical_target}): {current} [{rtype}]"
                )
        parts.append("")

    # -- Plot threads --
    if plot_threads:
        parts.append("## Plot Threads")
        for pt in plot_threads:
            status = pt.get("status", "unknown")
            title = pt.get("title", pt.get("id", ""))
            desc = pt.get("description", "")
            key_turns = pt.get("key_turns", [])
            turns_str = ", ".join(key_turns[:5]) if key_turns else "none"
            parts.append(f"- [{status.upper()}] {title}: {desc}")
            parts.append(f"  Key turns: {turns_str}")
            open_q = pt.get("open_questions", [])
            if open_q:
                for q in open_q[:3]:
                    parts.append(f"  Open question: {q}")
        parts.append("")

    # -- Key events --
    critical = _critical_events(events)
    if critical:
        parts.append("## Key Events")
        for evt in critical:
            turns = evt.get("source_turns", [])
            turn_str = ", ".join(turns) if turns else "?"
            etype = evt.get("type", "other")
            desc = evt.get("description", "")
            related = evt.get("related_entities", [])
            resolved_related = list(dict.fromkeys(
                resolve_entity_id(r) for r in related
            ))
            related_str = ", ".join(resolved_related[:4]) if resolved_related else ""
            parts.append(f"- [{turn_str}] ({etype}) {desc}")
            if related_str:
                parts.append(f"  Involving: {related_str}")
        parts.append("")

    # -- Major NPCs by event count --
    top = _top_entities_by_events(grouped, exclude={"char-player"}, limit=8)
    if top:
        parts.append("## Major Entities (by event frequency)")
        for eid, count in top:
            name = _infer_name_from_id(eid)
            parts.append(f"- {name} ({eid}): {count} events")
        parts.append("")

    # -- Timeline context --
    if timeline:
        parts.append("## Timeline Markers")
        for tm in timeline[:10]:
            turn = tm.get("source_turn", "")
            ttype = tm.get("type", "")
            desc = tm.get("description", tm.get("raw_text", ""))
            season = tm.get("season", "")
            parts.append(f"- [{turn}] {ttype}: {desc}")
            if season:
                parts.append(f"  Season: {season}")
        parts.append("")

    # -- Task --
    parts.append("## Task")
    parts.append(
        "Write a concise campaign story summary covering the narrative arcs, "
        "the player character's journey, unresolved tensions, and the current "
        "state of the story. Organize by narrative arc, not chronologically."
    )

    return {
        "system_prompt": STORY_SUMMARY_SYSTEM_PROMPT,
        "user_prompt": "\n".join(parts),
    }


# ---------------------------------------------------------------------------
# LLM-based generation
# ---------------------------------------------------------------------------

def generate_story_summary_llm(llm_client, summary_input: dict) -> str:
    """Call the LLM to generate the story summary prose.

    Returns the raw generated text, or empty string on failure.
    """
    try:
        text = llm_client.generate_text(
            system_prompt=summary_input["system_prompt"],
            user_prompt=summary_input["user_prompt"],
        )
        llm_client.delay()
        return text.strip()
    except Exception as e:
        logger.error("Story summary LLM generation failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Data-only (no-LLM) fallback summary
# ---------------------------------------------------------------------------

def generate_story_summary_data_only(
    events: list[dict],
    plot_threads: list[dict],
    characters: list[dict],
    timeline: list[dict],
) -> str:
    """Generate a structured markdown summary without LLM calls.

    This provides a useful overview even when no LLM is configured.
    """
    grouped = group_events_by_entity(events)
    pc_data = _get_pc_data(characters)
    first_turn, last_turn = _format_turn_range(events)

    lines: list[str] = []

    # Overview
    lines.append(
        f"Campaign spans {first_turn} through {last_turn} "
        f"with {len(events)} tracked events across "
        f"{len(grouped)} entities."
    )
    lines.append("")

    # PC status
    if pc_data:
        name = pc_data.get("name", "The player character")
        identity = pc_data.get("identity", "")
        status = pc_data.get("current_status", "")
        if identity:
            lines.append(f"**{name}** — {identity}")
        else:
            lines.append(f"**{name}**")
        if status:
            turn = pc_data.get("status_updated_turn",
                               pc_data.get("last_updated_turn", ""))
            lines.append(f"Current status (as of {turn}): {status}")
        # Relationships
        rels = pc_data.get("relationships", [])
        if rels:
            lines.append("")
            lines.append("Key relationships:")
            for rel in rels[:6]:
                target = rel.get("target_id", "")
                name_r = _infer_name_from_id(target)
                current = rel.get("current_relationship", "")
                lines.append(f"- {name_r}: {current}")
        lines.append("")

    # Active plot threads
    active_threads = [pt for pt in plot_threads if pt.get("status") == "active"]
    dormant_threads = [pt for pt in plot_threads if pt.get("status") == "dormant"]
    resolved_threads = [pt for pt in plot_threads if pt.get("status") == "resolved"]

    if active_threads:
        lines.append("**Active plot threads:**")
        for pt in active_threads:
            title = pt.get("title", pt.get("id", ""))
            desc = pt.get("description", "")
            lines.append(f"- {title}: {desc}")
            for q in pt.get("open_questions", [])[:2]:
                lines.append(f"  - Open: {q}")
        lines.append("")

    if dormant_threads:
        lines.append("**Dormant threads:**")
        for pt in dormant_threads:
            title = pt.get("title", pt.get("id", ""))
            lines.append(f"- {title}")
        lines.append("")

    if resolved_threads:
        lines.append("**Resolved threads:**")
        for pt in resolved_threads:
            title = pt.get("title", pt.get("id", ""))
            lines.append(f"- {title}")
        lines.append("")

    # Key events
    critical = _critical_events(events)
    if critical:
        lines.append("**Key events:**")
        for evt in critical:
            turns = evt.get("source_turns", [])
            turn_str = turns[0] if turns else "?"
            desc = evt.get("description", "")
            lines.append(f"- [{turn_str}] {desc}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def assemble_summary_page(
    summary_prose: str,
    events: list[dict],
    plot_threads: list[dict],
    *,
    generated_by: str = "unknown",
) -> str:
    """Assemble the final summary.md page content.

    Args:
        summary_prose: The generated summary text (LLM or data-only).
        events: Full events list (for metadata).
        plot_threads: Plot threads (for open questions appendix).
        generated_by: Label for the generation method.

    Returns:
        Complete markdown content for summary.md.
    """
    first_turn, last_turn = _format_turn_range(events)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines: list[str] = []
    lines.append("# Story Summary")
    lines.append("")
    lines.append(f"*Generated {now} — turns {first_turn} through "
                 f"{last_turn} — {len(events)} events — "
                 f"method: {generated_by}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Main summary
    lines.append("## Arc Overview")
    lines.append("")
    lines.append(summary_prose)
    lines.append("")

    # Open questions from plot threads
    open_questions: list[str] = []
    for pt in plot_threads:
        if pt.get("status") in ("active", "dormant"):
            for q in pt.get("open_questions", []):
                open_questions.append(q)

    if open_questions:
        lines.append("---")
        lines.append("")
        lines.append("## Open Questions")
        lines.append("")
        for q in open_questions:
            lines.append(f"- {q}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_story_summary(
    framework_dir: str,
    llm_client=None,
    no_llm: bool = False,
) -> str:
    """Generate and write the story summary.

    Args:
        framework_dir: Path to the framework directory.
        llm_client: Optional LLMClient instance. Created from config if
            None and ``no_llm`` is False.
        no_llm: If True, generate data-only summary without LLM.

    Returns:
        The generated summary markdown content.
    """
    # Load data
    events = load_events(framework_dir)
    plot_threads = load_plot_threads(framework_dir)
    characters = load_entity_catalog(framework_dir, "characters")
    timeline = load_timeline(framework_dir)

    if not events:
        logger.warning("No events found in %s — generating stub summary",
                       framework_dir)
        content = (
            "# Story Summary\n\n"
            "_No events extracted yet. Run extraction to populate this file._\n"
        )
        output_path = os.path.join(framework_dir, "story", "summary.md")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return content

    if no_llm:
        summary_prose = generate_story_summary_data_only(
            events, plot_threads, characters, timeline)
        method = "data-only"
    else:
        summary_prose = ""
        method = "data-only"

        if llm_client is None:
            try:
                from llm_client import LLMClient
                llm_client = LLMClient()
            except Exception as exc:
                logger.warning(
                    "LLM client initialization failed (%s) — "
                    "falling back to data-only summary", exc,
                )
                summary_prose = generate_story_summary_data_only(
                    events, plot_threads, characters, timeline)
                method = "data-only (LLM unavailable)"
                llm_client = None

        if llm_client is not None:
            summary_input = assemble_story_summary_input(
                events, plot_threads, characters, timeline)
            summary_prose = generate_story_summary_llm(llm_client, summary_input)

            if not summary_prose:
                logger.warning("LLM generation returned empty — falling back to data-only")
                summary_prose = generate_story_summary_data_only(
                    events, plot_threads, characters, timeline)
                method = "data-only (LLM fallback)"
            else:
                method = f"llm ({getattr(llm_client, 'model', 'unknown')})"

    page = assemble_summary_page(
        summary_prose, events, plot_threads,
        generated_by=method)

    output_path = os.path.join(framework_dir, "story", "summary.md")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)

    logger.info("Story summary written to %s (%d chars)", output_path, len(page))
    return page


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate story arc summary from extracted narrative data.")
    parser.add_argument(
        "--framework", default="framework/",
        help="Path to the framework directory (default: framework/).")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Generate data-only summary without LLM calls.")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    page = generate_story_summary(
        framework_dir=args.framework,
        no_llm=args.no_llm,
    )
    print(f"Summary generated ({len(page)} chars)")


if __name__ == "__main__":
    main()
