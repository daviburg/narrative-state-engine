#!/usr/bin/env python3
"""
update_state.py — Update derived state files after new turns are ingested.

This script reads the session transcript and prompts the user (or Copilot) to
update the derived state files. In automated mode it rebuilds the turn summary
from the transcript files.

Usage:
    python tools/update_state.py --session sessions/session-001
    python tools/update_state.py --session sessions/session-001 --dry-run
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def list_turns(transcript_dir: str) -> list[dict]:
    """Return all turns sorted by sequence number."""
    if not os.path.isdir(transcript_dir):
        return []
    pattern = re.compile(r"^turn-(\d+)-(player|dm)\.md$")
    turns = []
    for fname in sorted(os.listdir(transcript_dir)):
        m = pattern.match(fname)
        if m:
            seq = int(m.group(1))
            speaker = m.group(2)
            turn_id = f"turn-{seq:03d}"
            filepath = os.path.join(transcript_dir, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            # Strip the heading line to get the raw text
            lines = content.split("\n")
            text_lines = [l for l in lines if not l.startswith("# turn-")]
            text = "\n".join(text_lines).strip()
            turns.append(
                {
                    "turn_id": turn_id,
                    "sequence_number": seq,
                    "speaker": speaker,
                    "text": text,
                }
            )
    return turns


def get_latest_turn_id(turns: list[dict]) -> str | None:
    if not turns:
        return None
    return turns[-1]["turn_id"]


def rebuild_turn_summary(derived_dir: str, turns: list[dict], full: bool = False) -> None:
    """Rebuild the turn summary markdown from the transcript."""
    os.makedirs(derived_dir, exist_ok=True)
    latest = get_latest_turn_id(turns)
    summary_file = os.path.join(derived_dir, "turn-summary.md")

    dm_turns = [t for t in turns if t["speaker"] == "dm"]
    if not dm_turns:
        return

    # Auto-detect bulk import: warn if most DM turns would be unsummarized (fixes #24)
    if not full and len(dm_turns) > 3:
        unsummarized = len(dm_turns) - 3
        pct = unsummarized / len(dm_turns) * 100
        if pct >= 90:
            print(
                f"  WARNING: Summary covers only last 3 of {len(dm_turns)} DM turns "
                f"({pct:.0f}% unsummarized). Use --full for complete coverage."
            )

    selected = dm_turns if full else dm_turns[-3:]

    # Cap full-mode output to stay concise per repo guidelines
    FULL_MODE_CAP = 50
    capped = False
    if full and len(selected) > FULL_MODE_CAP:
        capped = True
        selected = selected[:FULL_MODE_CAP]

    lines = [f"# Turn Summary (as of {latest})", ""]
    lines.append("_This summary was auto-generated. Review and refine with Copilot._")
    lines.append("")
    if full:
        if capped:
            lines.append(
                f"_Full summary mode: showing first {FULL_MODE_CAP} of "
                f"{len(dm_turns)} DM turn(s). Run a dedicated summarization "
                f"pass for complete coverage._"
            )
        else:
            lines.append(f"_Full summary mode: {len(selected)} DM turn(s) included._")
        lines.append("")
    for t in selected:
        lines.append(f"## {t['turn_id']} [dm]")
        lines.append("")
        # Show first 200 chars as a preview
        preview = t["text"][:200].replace("\n", " ")
        if len(t["text"]) > 200:
            preview += "..."
        lines.append(f"> {preview}")
        lines.append("")

    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")
    print(f"  Updated: {summary_file}")


def ensure_state_scaffold(derived_dir: str, latest_turn: str | None) -> None:
    """Create state.json if it does not exist."""
    os.makedirs(derived_dir, exist_ok=True)
    state_file = os.path.join(derived_dir, "state.json")
    if os.path.exists(state_file):
        # Update as_of_turn if we have a newer turn
        if latest_turn:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("as_of_turn") != latest_turn:
                state["as_of_turn"] = latest_turn
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                    f.write("\n")
                print(f"  Updated as_of_turn in: {state_file}")
        return

    scaffold = {
        "as_of_turn": latest_turn or "turn-001",
        "current_world_state": "TODO: Describe current world state from transcript.",
        "player_state": {
            "location": "Unknown",
            "condition": "Unknown",
            "inventory_notes": "Not established",
            "relationships_summary": "No NPCs contacted yet",
        },
        "known_constraints": [],
        "inferred_constraints": [],
        "opportunities": [],
        "risks": [],
        "active_threads": [],
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(scaffold, f, indent=2)
        f.write("\n")
    print(f"  Created: {state_file}")


def ensure_objectives_scaffold(derived_dir: str) -> None:
    """Create objectives.json if it does not exist."""
    os.makedirs(derived_dir, exist_ok=True)
    obj_file = os.path.join(derived_dir, "objectives.json")
    if not os.path.exists(obj_file):
        with open(obj_file, "w", encoding="utf-8") as f:
            f.write("[]\n")
        print(f"  Created: {obj_file}")


def ensure_evidence_scaffold(derived_dir: str) -> None:
    """Create evidence.json if it does not exist."""
    os.makedirs(derived_dir, exist_ok=True)
    ev_file = os.path.join(derived_dir, "evidence.json")
    if not os.path.exists(ev_file):
        with open(ev_file, "w", encoding="utf-8") as f:
            f.write("[]\n")
        print(f"  Created: {ev_file}")


def print_instructions(session_dir: str, turns: list[dict]) -> None:
    latest = get_latest_turn_id(turns)
    print()
    print("=" * 60)
    print("MANUAL UPDATE REQUIRED")
    print("=" * 60)
    print(f"Latest turn: {latest}")
    print()
    print("Please review and update the following derived files:")
    print(f"  {session_dir}/derived/state.json")
    print(f"  {session_dir}/derived/objectives.json")
    print(f"  {session_dir}/derived/evidence.json")
    print()
    print("With Copilot, use prompts like:")
    print('  "Update state.json based on the latest DM turn."')
    print('  "Add any new evidence from the latest turn to evidence.json."')
    print()
    print("Then run:")
    print(f"  python tools/analyze_next_move.py --session {session_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update derived state files after ingesting new turns."
    )
    parser.add_argument("--session", required=True, help="Path to the session directory.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing files.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Generate summary covering all DM turns instead of only the last 3. "
             "Recommended for bulk imports.",
    )
    args = parser.parse_args()

    session_dir = args.session
    if not os.path.isdir(session_dir):
        print(f"ERROR: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    transcript_dir = os.path.join(session_dir, "transcript")
    derived_dir = os.path.join(session_dir, "derived")

    turns = list_turns(transcript_dir)
    if not turns:
        print("No turns found in transcript directory.")
        sys.exit(0)

    latest = get_latest_turn_id(turns)
    print(f"Found {len(turns)} turn(s). Latest: {latest}")

    if args.dry_run:
        print("[dry-run] Would update derived files for session:", session_dir)
        return

    print("Updating derived files...")
    rebuild_turn_summary(derived_dir, turns, full=args.full)
    ensure_state_scaffold(derived_dir, latest)
    ensure_objectives_scaffold(derived_dir)
    ensure_evidence_scaffold(derived_dir)

    print_instructions(session_dir, turns)


if __name__ == "__main__":
    main()
