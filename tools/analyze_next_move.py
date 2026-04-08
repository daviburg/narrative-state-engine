#!/usr/bin/env python3
"""
analyze_next_move.py — Generate next-move analysis and prompt candidates.

This script reads the current session state and produces a next-move analysis
markdown file and a prompt-candidates JSON file.

Usage:
    python tools/analyze_next_move.py --session sessions/session-001
    python tools/analyze_next_move.py --session sessions/session-001 --mode all_options
"""

import argparse
import json
import os
import sys


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

## 4. Opportunities

{opportunities}

---

## 5. Risks

{risks}

---

## 6. Objectives Affected

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


def generate_analysis(session_dir: str, mode: str, force_regen: bool = True) -> None:
    session_id = os.path.basename(session_dir)
    derived_dir = os.path.join(session_dir, "derived")

    state = load_json(os.path.join(derived_dir, "state.json"), default={})
    evidence = load_json(os.path.join(derived_dir, "evidence.json"), default=[])
    objectives = load_json(os.path.join(derived_dir, "objectives.json"), default=[])

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

    analysis = ANALYSIS_TEMPLATE.format(
        session_id=session_id,
        as_of_turn=as_of_turn,
        world_state=world_state,
        explicit_evidence=explicit,
        inferences=inferences,
        dm_bait=bait_text,
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
        print(f"  Kept existing prompt candidates: {len(existing)} candidate(s). (pass --force to regenerate)")
        return

    # Generate a scaffold with placeholder candidates
    active_obj_ids = [o["id"] for o in objectives if o.get("status") == "active"]
    opportunities = state.get("opportunities", [])
    first_opp = opportunities[0] if opportunities else "Gather more information."

    scaffold = [
        {
            "id": "pc-001",
            "recommendation_mode": "desired_outcome",
            "style": "probing",
            "proposed_prompt": f"TODO: Write a probing prompt to {first_opp.lower()}",
            "rationale": "Information gathering is the safest first step.",
            "expected_upside": "Learn key facts without committing to a course of action.",
            "risk": "May reveal player's presence or intentions.",
            "objective_refs": active_obj_ids,
        },
        {
            "id": "pc-002",
            "recommendation_mode": "desired_outcome",
            "style": "safe",
            "proposed_prompt": "TODO: Write a cautious, low-commitment action prompt.",
            "rationale": "Minimize risk while still advancing the situation.",
            "expected_upside": "Avoids triggering threats; keeps options open.",
            "risk": "May be slower to advance objectives.",
            "objective_refs": active_obj_ids,
        },
        {
            "id": "pc-003",
            "recommendation_mode": "all_options",
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
    generate_analysis(session_dir, args.mode, force_regen=not args.no_regen)

    print()
    print("Done. Review:")
    print(f"  {os.path.join(session_dir, 'derived', 'next-move-analysis.md')}")
    print(f"  {os.path.join(session_dir, 'derived', 'prompt-candidates.json')}")


if __name__ == "__main__":
    main()
