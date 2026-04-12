#!/usr/bin/env python3
"""
analyze_next_move.py — Generate next-move analysis and prompt candidates.

This script reads the current session state and produces a next-move analysis
markdown file and a prompt-candidates JSON file. When turn-context.json is
available (#87), entity context is injected for entity-aware analysis.

Usage:
    python tools/analyze_next_move.py --session sessions/session-001
    python tools/analyze_next_move.py --session sessions/session-001 --mode all_options
    python tools/analyze_next_move.py --session sessions/session-001 --rebuild-context --framework framework/
"""

import argparse
import json
import os
import re
import subprocess
import sys
import warnings


ANALYSIS_TEMPLATE = """# Next-Move Analysis — {session_id} (as of {as_of_turn})

---

## 1. What Changed?

{world_state}

---

## 2. What Is Known vs. Inferred?

### Explicit Evidence (certain)
{explicit_evidence}

### Inferences (unconfirmed)
{inferences}

---

## 3. Potential DM Bait

{dm_bait}

---

## 4. Entity Context

### Scene Entities
{scene_entities}

### Scene Locations
{scene_locations}

### Nearby Entities (background)
{nearby_entities_summary}

---

## 5. Opportunities

{opportunities}

---

## 6. Risks

{risks}

---

## 7. Objectives Affected

{objectives}

---

_Review prompt-candidates.json for suggested next player prompts._
"""


def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_list(items: list, indent: str = "- ") -> str:
    if not items:
        return "_None identified._"
    return "\n".join(f"{indent}{item}" for item in items)


def format_evidence_list(evidence: list, classifications: list[str]) -> str:
    filtered = [e for e in evidence if e.get("classification") in classifications]
    if not filtered:
        return "_None recorded._"
    lines = []
    for e in filtered:
        confidence = e.get("confidence", 1.0)
        stmt = e.get("statement", "")
        if confidence < 1.0:
            lines.append(f"- {stmt} _(confidence: {confidence:.0%})_")
        else:
            lines.append(f"- {stmt}")
    return "\n".join(lines)


def format_objectives(objectives: list) -> str:
    active = [o for o in objectives if o.get("status") == "active"]
    if not active:
        return "_No active objectives established yet._"
    lines = []
    for o in active:
        otype = o.get("type", "unknown")
        title = o.get("title", "Untitled")
        lines.append(f"- **[{otype}]** {title}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entity context formatting (#87)
# ---------------------------------------------------------------------------

def format_scene_entities(entities: list[dict]) -> str:
    """Format scene entities for the analysis template."""
    if not entities:
        return "_No entity context available._"
    lines = []
    for e in entities:
        eid = e.get("id", "?")
        name = e.get("name", "Unknown")
        identity = e.get("identity", "")
        status = e.get("current_status", "")
        line = f"- **{name}** (`{eid}`)"
        if identity:
            line += f": {identity}"
        if status:
            line += f" — _{status}_"
        lines.append(line)
        # Active relationships
        for rel in e.get("active_relationships", []):
            target_name = rel.get("target_name", rel.get("target_id", "?"))
            rel_text = rel.get("relationship", "related to")
            lines.append(f"  - → {target_name}: {rel_text}")
        # Volatile state
        vol = e.get("volatile_state", {})
        if vol:
            vol_parts = []
            if vol.get("condition"):
                vol_parts.append(f"condition: {vol['condition']}")
            if vol.get("location"):
                vol_parts.append(f"at: {vol['location']}")
            if vol.get("equipment"):
                vol_parts.append(f"equipment: {vol['equipment']}")
            if vol_parts:
                lines.append(f"  - _Volatile: {', '.join(vol_parts)}_")
    return "\n".join(lines)


def format_scene_locations(locations: list[dict]) -> str:
    """Format scene locations for the analysis template."""
    if not locations:
        return "_No location context available._"
    lines = []
    for loc in locations:
        lid = loc.get("id", "?")
        name = loc.get("name", "Unknown")
        identity = loc.get("identity", "")
        status = loc.get("current_status", "")
        line = f"- **{name}** (`{lid}`)"
        if identity:
            line += f": {identity}"
        if status:
            line += f" — _{status}_"
        lines.append(line)
    return "\n".join(lines)


def format_nearby_summary(nearby: list[dict]) -> str:
    """Format nearby entity summary for the analysis template."""
    if not nearby:
        return "_No nearby entities detected._"
    lines = []
    for n in nearby:
        name = n.get("name", "Unknown")
        nid = n.get("id", "?")
        summary = n.get("status_summary", "")
        line = f"- {name} (`{nid}`)"
        if summary:
            line += f": {summary}"
        lines.append(line)
    return "\n".join(lines)


def _get_latest_turn_id(session_dir: str) -> str | None:
    """Determine the latest turn ID from transcript files."""
    transcript_dir = os.path.join(session_dir, "transcript")
    if not os.path.isdir(transcript_dir):
        return None
    pattern = re.compile(r"^turn-(\d+)-(player|dm)\.md$")
    max_seq = 0
    for fname in os.listdir(transcript_dir):
        m = pattern.match(fname)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return f"turn-{max_seq:03d}" if max_seq > 0 else None


def load_turn_context(
    session_dir: str,
    rebuild: bool = False,
    framework_dir: str | None = None,
) -> tuple[dict | None, bool]:
    """Load turn-context.json, optionally rebuilding it first.

    Returns (context_dict_or_None, is_stale).
    """
    derived_dir = os.path.join(session_dir, "derived")
    context_path = os.path.join(derived_dir, "turn-context.json")

    if rebuild and framework_dir:
        latest_turn = _get_latest_turn_id(session_dir)
        if latest_turn:
            print(f"  Rebuilding turn-context.json for {latest_turn}...")
            try:
                subprocess.run(
                    [
                        sys.executable,
                        os.path.join(os.path.dirname(__file__), "build_context.py"),
                        "--session", session_dir,
                        "--turn", latest_turn,
                        "--framework", framework_dir,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                warnings.warn(f"Failed to rebuild turn-context.json: {exc.stderr}")
            except FileNotFoundError:
                warnings.warn("build_context.py not found, skipping rebuild.")

    context = load_json(context_path)
    if context is None:
        return None, False

    # Stale detection: compare context as_of_turn vs latest turn
    latest_turn = _get_latest_turn_id(session_dir)
    context_turn = context.get("as_of_turn", "")
    is_stale = False
    if latest_turn and context_turn and context_turn != latest_turn:
        is_stale = True

    return context, is_stale


def generate_analysis(
    session_dir: str,
    mode: str,
    force_regen: bool = True,
    rebuild_context: bool = False,
    framework_dir: str | None = None,
) -> None:
    session_id = os.path.basename(session_dir)
    derived_dir = os.path.join(session_dir, "derived")

    state = load_json(os.path.join(derived_dir, "state.json"), default={})
    evidence = load_json(os.path.join(derived_dir, "evidence.json"), default=[])
    objectives = load_json(os.path.join(derived_dir, "objectives.json"), default=[])

    # Load turn-context.json (#87)
    turn_context, is_stale = load_turn_context(
        session_dir, rebuild=rebuild_context, framework_dir=framework_dir,
    )
    if is_stale:
        ctx_turn = turn_context.get("as_of_turn", "?") if turn_context else "?"
        latest = _get_latest_turn_id(session_dir) or "?"
        warnings.warn(
            f"turn-context.json is stale (as_of_turn={ctx_turn}, "
            f"latest={latest}). Use --rebuild-context to refresh."
        )

    as_of_turn = state.get("as_of_turn", "unknown")
    world_state = state.get("current_world_state", "_Not yet described._")

    explicit = format_evidence_list(evidence, ["explicit_evidence"])
    inferences = format_evidence_list(evidence, ["inference"])
    dm_bait_items = [e for e in evidence if e.get("classification") == "dm_bait"]
    if dm_bait_items:
        bait_text = "\n".join(
            f"- **{e.get('id', '?')}**: {e.get('statement', '')} "
            f"_(confidence: {e.get('confidence', 0.5):.0%})_"
            for e in dm_bait_items
        )
    else:
        bait_text = "_No DM bait identified yet._"

    opportunities = format_list(state.get("opportunities", []))
    risks = format_list(state.get("risks", []))
    objectives_text = format_objectives(objectives)

    # Format entity context
    if turn_context:
        scene_entities_text = format_scene_entities(turn_context.get("scene_entities", []))
        scene_locations_text = format_scene_locations(turn_context.get("scene_locations", []))
        nearby_text = format_nearby_summary(turn_context.get("nearby_entities_summary", []))
    else:
        scene_entities_text = "_No turn-context.json available. Run build_context.py to enable entity-aware analysis._"
        scene_locations_text = "_No location context available._"
        nearby_text = "_No nearby entities detected._"

    analysis = ANALYSIS_TEMPLATE.format(
        session_id=session_id,
        as_of_turn=as_of_turn,
        world_state=world_state,
        explicit_evidence=explicit,
        inferences=inferences,
        dm_bait=bait_text,
        scene_entities=scene_entities_text,
        scene_locations=scene_locations_text,
        nearby_entities_summary=nearby_text,
        opportunities=opportunities,
        risks=risks,
        objectives=objectives_text,
    )

    analysis_file = os.path.join(derived_dir, "next-move-analysis.md")
    with open(analysis_file, "w", encoding="utf-8") as f:
        f.write(analysis)
    print(f"  Written: {analysis_file}")

    generate_prompt_candidates(derived_dir, session_id, as_of_turn, state, objectives, mode, force_regen=force_regen)


def generate_prompt_candidates(
    derived_dir: str,
    session_id: str,
    as_of_turn: str,
    state: dict,
    objectives: list,
    mode: str,
    force_regen: bool = True,
) -> None:
    """Generate prompt candidates. Regenerates by default; pass force_regen=False to preserve existing file."""
    candidates_file = os.path.join(derived_dir, "prompt-candidates.json")

    # Skip regeneration only when explicitly requested
    if os.path.exists(candidates_file) and not force_regen:
        existing = load_json(candidates_file, default=[])
        print(
            f"  Kept existing prompt candidates: {len(existing)} candidate(s). "
            "(regeneration is disabled because you passed --no-regen)"
        )
        return

    # Generate a scaffold with placeholder candidates
    active_obj_ids = [o["id"] for o in objectives if o.get("status") == "active"]
    opportunities = state.get("opportunities", [])
    first_opp = opportunities[0] if opportunities else "Gather more information."

    scaffold = [
        {
            "id": "pc-001",
            "recommendation_mode": mode,
            "style": "probing",
            "proposed_prompt": f"TODO: Write a probing prompt to {first_opp.lower()}",
            "rationale": "Information gathering is the safest first step.",
            "expected_upside": "Learn key facts without committing to a course of action.",
            "risk": "May reveal player's presence or intentions.",
            "objective_refs": active_obj_ids,
        },
        {
            "id": "pc-002",
            "recommendation_mode": mode,
            "style": "safe",
            "proposed_prompt": "TODO: Write a cautious, low-commitment action prompt.",
            "rationale": "Minimize risk while still advancing the situation.",
            "expected_upside": "Avoids triggering threats; keeps options open.",
            "risk": "May be slower to advance objectives.",
            "objective_refs": active_obj_ids,
        },
        {
            "id": "pc-003",
            "recommendation_mode": mode,
            "style": "direct",
            "proposed_prompt": "TODO: Write a direct action prompt.",
            "rationale": "Efficient path to the objective if the situation is stable.",
            "expected_upside": "Fastest route to objective completion.",
            "risk": "May miss important information or trigger consequences.",
            "objective_refs": active_obj_ids,
        },
    ]

    with open(candidates_file, "w", encoding="utf-8") as f:
        json.dump(scaffold, f, indent=2)
        f.write("\n")
    print(f"  Created prompt candidate scaffold: {candidates_file}")
    print("  IMPORTANT: Replace TODO placeholders with real prompts before using.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate next-move analysis and prompt candidates."
    )
    parser.add_argument("--session", required=True, help="Path to the session directory.")
    parser.add_argument(
        "--mode",
        choices=["desired_outcome", "roleplay_consistent", "all_options"],
        default="desired_outcome",
        help="Prompt generation mode (default: desired_outcome).",
    )
    parser.add_argument(
        "--no-regen",
        action="store_true",
        help="Keep existing prompt-candidates.json instead of regenerating it.",
    )
    parser.add_argument(
        "--rebuild-context",
        action="store_true",
        help="Re-run build_context.py before analysis to refresh turn-context.json.",
    )
    parser.add_argument(
        "--framework",
        help="Path to framework directory (required with --rebuild-context).",
    )
    args = parser.parse_args()

    session_dir = args.session
    if not os.path.isdir(session_dir):
        print(f"ERROR: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    derived_dir = os.path.join(session_dir, "derived")
    if not os.path.isdir(derived_dir):
        print(
            f"ERROR: No derived directory found. Run update_state.py first.", file=sys.stderr
        )
        sys.exit(1)

    print(f"Generating next-move analysis for: {session_dir}")
    generate_analysis(
        session_dir,
        args.mode,
        force_regen=not args.no_regen,
        rebuild_context=args.rebuild_context,
        framework_dir=args.framework,
    )

    print()
    print("Done. Review:")
    print(f"  {os.path.join(session_dir, 'derived', 'next-move-analysis.md')}")
    print(f"  {os.path.join(session_dir, 'derived', 'prompt-candidates.json')}")


if __name__ == "__main__":
    main()
