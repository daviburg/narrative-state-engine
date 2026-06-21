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


_TURN_FILE_RE = re.compile(r"^turn-(\d+)-(player|dm)\.md$")


def parse_turn_filename(path: str):
    """Return (turn_id, speaker) parsed from an existing turn file name.

    Returns None if the basename does not match the turn-NNN-(player|dm).md
    convention used by write_turn_file().
    """
    m = _TURN_FILE_RE.match(os.path.basename(path))
    if not m:
        return None
    return f"turn-{int(m.group(1)):03d}", m.group(2)


def strip_turn_header(text: str) -> str:
    """Drop the leading "# turn-NNN — SPEAKER" header line if present.

    Turn files written by write_turn_file() begin with a markdown header that
    is not part of the narrative text; remove it before extraction so the LLM
    sees the same content as a freshly ingested turn.
    """
    lines = text.lstrip("\n").splitlines()
    if lines and lines[0].lstrip().startswith("# turn-"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def run_semantic_extraction(turn_id, speaker, text, session_dir, args) -> bool:
    """Run LLM-based semantic extraction (and DM profile analysis) for a turn.

    Shared by the normal --extract flow and the --extract-only flow so both
    use exactly the same extraction code path.

    Returns:
        ``True`` when semantic extraction completed successfully for the turn,
        ``False`` when it could not run or reported a failure (LLM client
        unavailable, the ``semantic_extraction`` module missing, a per-phase
        extraction failure, or an unexpected error). Callers that need to
        surface failure via the process exit code (e.g. ``--extract-only``)
        can act on this; the normal ``--extract`` flow ignores it, preserving
        existing behaviour. DM profile analysis failures do not affect the
        returned extraction status.
    """
    extraction_succeeded = False
    try:
        from semantic_extraction import extract_semantic_single

        llm_overrides = {}
        if args.model:
            llm_overrides["model"] = args.model
        if args.base_url:
            llm_overrides["base_url"] = args.base_url

        extraction_succeeded = bool(extract_semantic_single(
            turn_id, speaker, text, session_dir, framework_dir=args.framework,
            overrides=llm_overrides or None,
        ))
    except ModuleNotFoundError as exc:
        if exc.name == "semantic_extraction":
            print(
                "WARNING: Semantic extraction skipped because "
                "'semantic_extraction' is not available.",
                file=sys.stderr,
            )
        else:
            raise
    except Exception as exc:
        print(f"WARNING: Semantic extraction failed: {exc}", file=sys.stderr)

    # DM profile analysis — update behavioral profile from DM turns (#260)
    if speaker == "dm":
        try:
            from dm_profile_analyzer import analyze_single_turn

            llm_overrides = {}
            if args.model:
                llm_overrides["model"] = args.model
            if args.base_url:
                llm_overrides["base_url"] = args.base_url

            analyze_single_turn(
                turn_id, speaker, text,
                framework_dir=args.framework,
                overrides=llm_overrides or None,
            )
        except ModuleNotFoundError as exc:
            if exc.name == "dm_profile_analyzer":
                print(
                    "WARNING: DM profile analysis skipped because "
                    "'dm_profile_analyzer' is not available.",
                    file=sys.stderr,
                )
            else:
                raise
        except Exception as exc:
            print(f"WARNING: DM profile analysis failed: {exc}", file=sys.stderr)

    return extraction_succeeded


def build_parser() -> argparse.ArgumentParser:
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
    group.add_argument("--file", help="Path to a file containing the turn text.")
    parser.add_argument(
        "--extract",
        action="store_true",
        default=False,
        help="Run LLM-based semantic extraction after ingesting the turn.",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        default=False,
        help="Re-run semantic extraction against an EXISTING turn file "
             "(given via --file) without creating a new turn file or "
             "modifying the transcript. Requires --file.",
    )
    parser.add_argument(
        "--framework",
        default="framework",
        help="Path to the framework directory for catalog output "
             "(default: framework). Use e.g. 'framework-local' to keep "
             "extraction output out of the public repo.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the LLM model name from config/llm.json for this run.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the LLM API base URL from config/llm.json for this run.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    session_dir = args.session
    if not os.path.isdir(session_dir):
        print(f"ERROR: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # --extract-only: re-run semantic extraction against an EXISTING turn file
    # without creating a new turn or modifying any raw/transcript file (#71).
    if args.extract_only:
        if args.extract:
            print(
                "ERROR: --extract-only and --extract are mutually exclusive; "
                "--extract-only re-extracts an existing turn without ingesting, "
                "so it cannot be combined with --extract.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not args.file:
            print(
                "ERROR: --extract-only requires --file pointing to an existing "
                "turn file (e.g. transcript/turn-022-dm.md).",
                file=sys.stderr,
            )
            sys.exit(1)
        if not os.path.isfile(args.file):
            print(f"ERROR: Turn file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        # Guard against pointing --file at a turn file outside this session's
        # transcript/ directory, which would pollute the wrong session's
        # framework outputs. The file must live in <session>/transcript/.
        expected_dir = os.path.realpath(os.path.join(session_dir, "transcript"))
        actual_dir = os.path.realpath(os.path.dirname(args.file))
        if actual_dir != expected_dir:
            print(
                "ERROR: --extract-only --file must be a turn file inside this "
                f"session's transcript directory ({expected_dir}); got "
                f"{os.path.realpath(args.file)}.",
                file=sys.stderr,
            )
            sys.exit(1)
        parsed = parse_turn_filename(args.file)
        if parsed is None:
            print(
                "ERROR: --file must be a turn file named like "
                "'turn-NNN-(player|dm).md' for --extract-only; got "
                f"{os.path.basename(args.file)}",
                file=sys.stderr,
            )
            sys.exit(1)
        turn_id, file_speaker = parsed
        if file_speaker != args.speaker:
            print(
                f"WARNING: --speaker {args.speaker} does not match the turn file "
                f"speaker '{file_speaker}'; using '{file_speaker}' from the file.",
                file=sys.stderr,
            )
        speaker = file_speaker
        with open(args.file, "r", encoding="utf-8") as f:
            text = strip_turn_header(f.read())
        if not text:
            print(f"ERROR: Turn file is empty: {args.file}", file=sys.stderr)
            sys.exit(1)

        print(f"Re-extracting {turn_id} ({speaker}) from {args.file}")
        extraction_succeeded = run_semantic_extraction(
            turn_id, speaker, text, session_dir, args)
        if not extraction_succeeded:
            print(
                f"ERROR: Semantic extraction failed for {turn_id}; "
                "the turn was not re-extracted successfully.",
                file=sys.stderr,
            )
            sys.exit(1)
        return

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
        run_semantic_extraction(turn_id, args.speaker, text, session_dir, args)

    print()
    print("Next steps:")
    print(f"  python tools/update_state.py --session {session_dir}")
    print(f"  python tools/analyze_next_move.py --session {session_dir}")


if __name__ == "__main__":
    main()
