#!/usr/bin/env python3
"""
ingest_turn.py — Add a new turn to a session.

Usage:
    python tools/ingest_turn.py --session sessions/session-001 --speaker dm \
        --text "The innkeeper looks up as you enter..."

    # Or read text from a file:
    python tools/ingest_turn.py --session sessions/session-001 --speaker player \
        --file /tmp/my-prompt.txt
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def find_next_sequence_number(transcript_dir: str) -> int:
    """Return the next sequence number based on existing turn files."""
    if not os.path.isdir(transcript_dir):
        return 1
    pattern = re.compile(r"^turn-(\d+)-(?:player|dm)\.md$")
    max_seq = 0
    for fname in os.listdir(transcript_dir):
        m = pattern.match(fname)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def format_turn_id(seq: int) -> str:
    return f"turn-{seq:03d}"


def write_turn_file(transcript_dir: str, turn_id: str, speaker: str, text: str) -> str:
    filename = f"{turn_id}-{speaker}.md"
    filepath = os.path.join(transcript_dir, filename)
    if os.path.exists(filepath):
        print(f"ERROR: Turn file already exists: {filepath}", file=sys.stderr)
        sys.exit(1)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {turn_id} — {speaker.upper()}\n\n")
        f.write(text.strip())
        f.write("\n")
    return filepath


def append_to_full_transcript(raw_dir: str, turn_id: str, speaker: str, text: str) -> None:
    os.makedirs(raw_dir, exist_ok=True)
    transcript_file = os.path.join(raw_dir, "full-transcript.md")
    with open(transcript_file, "a", encoding="utf-8") as f:
        f.write(f"\n---\n\n## {turn_id} [{speaker}]\n\n")
        f.write(text.strip())
        f.write("\n")


def update_metadata(session_dir: str, turn_count: int) -> None:
    metadata_file = os.path.join(session_dir, "metadata.json")
    if not os.path.exists(metadata_file):
        return
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    metadata["turn_count"] = turn_count
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a new turn into a session.")
    parser.add_argument("--session", required=True, help="Path to the session directory.")
    parser.add_argument(
        "--speaker",
        required=True,
        choices=["player", "dm"],
        help="Who produced this turn.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="Turn text provided inline.")
    group.add_argument("--file", help="Path to a file containing the turn text.")    parser.add_argument(
        "--extract",
        action="store_true",
        default=False,
        help="Run LLM-based semantic extraction after ingesting the turn.",
    )    args = parser.parse_args()

    session_dir = args.session
    if not os.path.isdir(session_dir):
        print(f"ERROR: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    transcript_dir = os.path.join(session_dir, "transcript")
    raw_dir = os.path.join(session_dir, "raw")
    os.makedirs(transcript_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)

    if args.text:
        text = args.text
    else:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()

    text = text.strip()
    if not text:
        print("ERROR: Turn text is empty.", file=sys.stderr)
        sys.exit(1)

    seq = find_next_sequence_number(transcript_dir)
    turn_id = format_turn_id(seq)

    turn_file = write_turn_file(transcript_dir, turn_id, args.speaker, text)
    append_to_full_transcript(raw_dir, turn_id, args.speaker, text)
    update_metadata(session_dir, seq)

    print(f"Ingested {turn_id} ({args.speaker}) -> {turn_file}")
    print(f"Appended to {os.path.join(raw_dir, 'full-transcript.md')}")

    # Extract structured data from the new turn (#21, #27, #28)
    try:
        from extract_structured_data import extract_and_merge_single_turn

        extract_and_merge_single_turn(
            session_dir, turn_id, args.speaker, text,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "extract_structured_data":
            print(
                "WARNING: Structured extraction skipped because "
                "'extract_structured_data' is not available.",
                file=sys.stderr,
            )
        else:
            raise

    # Semantic extraction — LLM-based entity/relationship/event extraction (#43)
    if args.extract:
        try:
            from semantic_extraction import extract_semantic_single

            extract_semantic_single(
                turn_id, args.speaker, text, session_dir, framework_dir="framework"
            )
        except ImportError:
            print(
                "WARNING: Semantic extraction skipped (openai not installed). "
                "Install with: pip install -r requirements-llm.txt",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"WARNING: Semantic extraction failed: {exc}", file=sys.stderr)

    print()
    print("Next steps:")
    print(f"  python tools/update_state.py --session {session_dir}")
    print(f"  python tools/analyze_next_move.py --session {session_dir}")


if __name__ == "__main__":
    main()
