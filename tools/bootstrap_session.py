#!/usr/bin/env python3
"""
bootstrap_session.py — Import a large existing transcript into a new session.

Use this when you already have a long session transcript (e.g. copy-pasted from
an AI DM platform) and want to bootstrap the repository structure from it rather
than ingesting turns one-by-one with ingest_turn.py.

Supported transcript formats (auto-detected, or specify with --format):

  markdown   Lines starting with ## or headers like "## turn-NNN [dm]"
             (the format produced by ingest_turn.py itself)

  labeled    Lines starting with a speaker label, e.g.:
               [DM]: ...  or  [Player]: ...
               DM: ...    or  Player: ...
               **DM**: ...

  alternating  Speaker alternates every blank-line-separated block.
               Use --first-speaker to declare who speaks first.

Usage:
    python tools/bootstrap_session.py \\
        --session sessions/session-001 \\
        --file /path/to/full-transcript.txt

    python tools/bootstrap_session.py \\
        --session sessions/session-001 \\
        --file /path/to/chat-export.txt \\
        --format labeled \\
        --dm-label "ChatGPT" \\
        --player-label "You"

    python tools/bootstrap_session.py \\
        --session sessions/session-001 \\
        --file /path/to/session.txt \\
        --format alternating \\
        --first-speaker dm \\
        --dry-run
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Turn(NamedTuple):
    sequence: int
    speaker: str   # "player" or "dm"
    text: str


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_markdown_format(content: str) -> list[Turn]:
    """
    Parse the native full-transcript.md format produced by ingest_turn.py.

    Expected structure:
        ---

        ## turn-001 [player]

        Some player text.

        ---

        ## turn-002 [dm]

        DM response text.
    """
    turns: list[Turn] = []
    # Match section headers like: ## turn-001 [dm] or ## turn-001 [player]
    header_re = re.compile(
        r"^##\s+turn-(\d+)\s+\[(player|dm)\]",
        re.IGNORECASE | re.MULTILINE,
    )

    matches = list(header_re.finditer(content))
    for i, m in enumerate(matches):
        seq = int(m.group(1))
        speaker = m.group(2).lower()
        # Text runs from end of this header to the start of the next (or EOF)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        text = content[start:end].strip()
        # Strip only standalone separators at turn boundaries.
        text = re.sub(r"\A\s*---\s*(?:\r?\n)?", "", text)
        text = re.sub(r"(?:\r?\n)?\s*---\s*\Z", "", text).strip()
        if text:
            turns.append(Turn(sequence=seq, speaker=speaker, text=text))

    return turns


def parse_labeled_format(
    content: str,
    dm_labels: list[str],
    player_labels: list[str],
) -> list[Turn]:
    """
    Parse transcripts where each speaker block starts with a label.

    Examples:
        [DM]: The innkeeper looks up.
        [Player]: I ask about the tower.

        DM: ...
        Player: ...

        **ChatGPT**: ...
        **You**: ...
    """
    # Build a regex that matches any known label at the start of a line.
    # Labels may be wrapped in **, [], or nothing.
    def _label_pattern(labels: list[str]) -> str:
        escaped = [re.escape(l) for l in labels]
        return r"(?:\*{0,2})(?:\[)?(?:" + "|".join(escaped) + r")(?:\])?\*{0,2}"

    dm_label_pattern = _label_pattern(dm_labels)
    player_label_pattern = _label_pattern(player_labels)
    any_label_pattern = r"(?:" + dm_label_pattern + r"|" + player_label_pattern + r")"

    # Match any speaker label at the start of a line
    speaker_re = re.compile(
        r"^(?P<raw_label>" + any_label_pattern + r")\s*:",
        re.IGNORECASE | re.MULTILINE,
    )

    dm_re = re.compile(r"^" + dm_label_pattern + r"$", re.IGNORECASE)

    splits = list(speaker_re.finditer(content))
    if not splits:
        return []

    turns: list[Turn] = []
    seq = 1
    for i, m in enumerate(splits):
        raw_label = m.group("raw_label").strip("*[] ")
        speaker = "dm" if dm_re.match(raw_label) else "player"
        start = m.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        text = content[start:end].strip()
        if text:
            turns.append(Turn(sequence=seq, speaker=speaker, text=text))
            seq += 1

    return turns


def parse_alternating_format(content: str, first_speaker: str) -> list[Turn]:
    """
    Parse a transcript where speakers alternate every blank-line-separated block.
    The first block belongs to first_speaker; subsequent blocks alternate.
    """
    # Split into non-empty blocks separated by one or more blank lines
    raw_blocks = re.split(r"\n{2,}", content.strip())
    blocks = [b.strip() for b in raw_blocks if b.strip()]

    speakers = ["player", "dm"]
    if first_speaker == "dm":
        speakers = ["dm", "player"]

    turns: list[Turn] = []
    for i, block in enumerate(blocks):
        speaker = speakers[i % 2]
        turns.append(Turn(sequence=i + 1, speaker=speaker, text=block))

    return turns


# ---------------------------------------------------------------------------
# Consecutive-speaker warning
# ---------------------------------------------------------------------------

def _warn_consecutive_speakers(turns: list[Turn]) -> None:
    """Emit warnings for consecutive turns from the same speaker."""
    for i in range(1, len(turns)):
        if turns[i].speaker == turns[i - 1].speaker:
            print(
                f"WARNING: turns {turns[i - 1].sequence} and {turns[i].sequence} "
                f"are both {turns[i].speaker} turns — possible parsing error",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(content: str, dm_labels: list[str], player_labels: list[str]) -> str:
    """Heuristically detect the transcript format."""
    # Check for native markdown headers
    if re.search(r"^##\s+turn-\d+\s+\[(player|dm)\]", content, re.IGNORECASE | re.MULTILINE):
        return "markdown"

    # Check for speaker labels
    all_labels = dm_labels + player_labels
    label_pattern = r"^(?:\*{0,2})(?:\[)?(?:" + "|".join(re.escape(l) for l in all_labels) + r")(?:\])?\*{0,2}\s*:"
    if re.search(label_pattern, content, re.IGNORECASE | re.MULTILINE):
        return "labeled"

    return "alternating"


# ---------------------------------------------------------------------------
# Session writers
# ---------------------------------------------------------------------------

def _format_turn_id(seq: int) -> str:
    return f"turn-{seq:03d}"


def write_turn_files(
    transcript_dir: str,
    turns: list[Turn],
    dry_run: bool,
    overwrite: bool,
) -> list[str]:
    """Write individual turn files. Returns list of written paths."""
    os.makedirs(transcript_dir, exist_ok=True)
    written: list[str] = []
    skipped: list[str] = []

    for turn in turns:
        turn_id = _format_turn_id(turn.sequence)
        filename = f"{turn_id}-{turn.speaker}.md"
        filepath = os.path.join(transcript_dir, filename)

        if os.path.exists(filepath) and not overwrite:
            skipped.append(filepath)
            print(f"  [SKIP]   {filepath} (already exists; use --overwrite to replace)")
            continue

        if dry_run:
            print(f"  [DRY]    would write {filepath}")
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {turn_id} — {turn.speaker.upper()}\n\n")
                f.write(turn.text.strip())
                f.write("\n")
            written.append(filepath)
            print(f"  [WRITE]  {filepath}")

    if skipped:
        print(f"\n  {len(skipped)} turn file(s) skipped (already exist).")

    return written


def write_full_transcript(
    raw_dir: str,
    turns: list[Turn],
    dry_run: bool,
    overwrite: bool,
    allow_raw_overwrite: bool,
) -> None:
    """Write or append to raw/full-transcript.md."""
    os.makedirs(raw_dir, exist_ok=True)
    transcript_path = os.path.join(raw_dir, "full-transcript.md")

    if os.path.exists(transcript_path):
        if not overwrite:
            print(f"  [SKIP]   {transcript_path} (already exists; use --overwrite to replace turn files)")
            return
        if not allow_raw_overwrite:
            print(
                f"  [SKIP]   {transcript_path} (raw transcript is immutable by default; "
                "pass --allow-raw-overwrite to force replacement with backup)"
            )
            return

    if dry_run:
        print(f"  [DRY]    would write {transcript_path} ({len(turns)} turns)")
        return

    if os.path.exists(transcript_path):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = f"{transcript_path}.bak-{timestamp}"
        shutil.copy2(transcript_path, backup_path)
        print(f"  [BACKUP] {backup_path}")

    lines: list[str] = ["# Full Transcript\n"]
    for turn in turns:
        turn_id = _format_turn_id(turn.sequence)
        lines.append(f"\n---\n\n## {turn_id} [{turn.speaker}]\n\n{turn.text.strip()}\n")

    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    print(f"  [WRITE]  {transcript_path}")


def ensure_metadata(session_dir: str, turns: list[Turn], dry_run: bool, start_date: str | None = None) -> None:
    """Create metadata.json if it does not exist."""
    metadata_path = os.path.join(session_dir, "metadata.json")
    if os.path.exists(metadata_path):
        # Update turn_count if lower than what we just ingested
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        if metadata.get("turn_count", 0) < len(turns):
            metadata["turn_count"] = len(turns)
            if not dry_run:
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
                    f.write("\n")
                print(f"  [UPDATE] {metadata_path} (turn_count={len(turns)})")
            else:
                print(f"  [DRY]    would update {metadata_path} (turn_count={len(turns)})")
        return

    session_id = os.path.basename(session_dir)
    metadata = {
        "session_id": session_id,
        "title": session_id.replace("-", " ").title(),
        "start_date": start_date,  # null when unknown
        "description": "Bootstrapped from existing transcript. Update this description.",
        "turn_count": len(turns),
    }
    if dry_run:
        print(f"  [DRY]    would create {metadata_path}")
    else:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            f.write("\n")
        print(f"  [WRITE]  {metadata_path}")


def ensure_derived_scaffolds(session_dir: str, latest_turn_id: str, dry_run: bool) -> None:
    """Create empty derived file scaffolds if they don't exist."""
    derived_dir = os.path.join(session_dir, "derived")
    os.makedirs(derived_dir, exist_ok=True)

    scaffolds = {
        "state.json": json.dumps({
            "as_of_turn": latest_turn_id,
            "current_world_state": "TODO: Update from transcript.",
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
        }, indent=2) + "\n",
        "objectives.json": "[]\n",
        "evidence.json": "[]\n",
    }

    for filename, content in scaffolds.items():
        path = os.path.join(derived_dir, filename)
        if os.path.exists(path):
            print(f"  [SKIP]   {path} (already exists)")
            continue
        if dry_run:
            print(f"  [DRY]    would create {path}")
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  [WRITE]  {path}")


def ensure_exports_dir(session_dir: str, dry_run: bool) -> None:
    """Create the exports/ placeholder."""
    exports_dir = os.path.join(session_dir, "exports")
    placeholder = os.path.join(exports_dir, "book-skeleton.md")
    if os.path.exists(placeholder):
        return
    os.makedirs(exports_dir, exist_ok=True)
    if not dry_run:
        with open(placeholder, "w", encoding="utf-8") as f:
            f.write("# Book Skeleton\n\n_Not yet generated. Run tools/export_book_skeleton.py once implemented._\n")
        print(f"  [WRITE]  {placeholder}")
    else:
        print(f"  [DRY]    would create {placeholder}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for bootstrap_session."""
    parser = argparse.ArgumentParser(
        description="Bootstrap a session from an existing large transcript file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--session", required=True, help="Path to the target session directory.")
    parser.add_argument("--file", required=True, help="Path to the source transcript file.")
    parser.add_argument(
        "--format",
        choices=["auto", "markdown", "labeled", "alternating"],
        default="auto",
        help="Transcript format (default: auto-detect).",
    )
    parser.add_argument(
        "--dm-label",
        default="DM",
        help="Speaker label used by the DM in the transcript (default: DM). "
             "Recognised with or without [], **, or trailing :.",
    )
    parser.add_argument(
        "--player-label",
        default="Player",
        help="Speaker label used by the player (default: Player).",
    )
    parser.add_argument(
        "--first-speaker",
        choices=["dm", "player"],
        default="dm",
        help="Who speaks first in alternating format (default: dm).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing turn files if they already exist.",
    )
    parser.add_argument(
        "--allow-raw-overwrite",
        action="store_true",
        help="Allow replacing raw/full-transcript.md when used with --overwrite. "
             "A timestamped backup will be created first.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing any files.",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Source file encoding (default: utf-8-sig, which handles BOM transparently).",
    )
    parser.add_argument(
        "--normalize-quotes",
        action="store_true",
        help="Convert smart quotes and em-dashes in imported transcript content "
             "to ASCII equivalents.",
    )
    parser.add_argument(
        "--framework",
        default="framework",
        help="Path to the framework directory for catalog output "
             "(default: framework). Use e.g. 'framework-local' to keep "
             "extraction output out of the public repo.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Session start date in YYYY-MM-DD format. "
             "Defaults to null for imported sessions where the date is unknown.",
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
    parser.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Skip the stub backfill pass after extraction.",
    )
    parser.add_argument(
        "--segment-size",
        type=int,
        default=None,
        help="Extract in segments of N turns with fresh catalogs, then reconcile. "
             "If omitted, sessions with >150 turns auto-default to 100. "
             "Pass 0 to disable segmentation.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Total cap: stop after processing the first N turns. "
             "Distinct from --segment-size which controls batch granularity. "
             "Post-extraction passes (backfill, PC alias merge) still run "
             "on the partial output. When used with --start-turn, this is "
             "treated as an absolute turn number (upper bound), not a count.",
    )
    parser.add_argument(
        "--start-turn",
        type=int,
        default=None,
        help="Start extraction from turn N (1-based), using existing catalogs as "
             "prior context. Turns before N are skipped. "
             "Example: --start-turn 26 --max-turns 50 extracts turns 26-50.",
    )
    return parser


_AUTO_SEGMENT_THRESHOLD = 150
_AUTO_SEGMENT_SIZE = 100


def _resolve_segment_size(requested_segment_size: int | None, turn_count: int) -> tuple[int, bool]:
    """Resolve effective segment size and whether auto-defaulting was applied."""
    if requested_segment_size is not None:
        return requested_segment_size, False
    if turn_count > _AUTO_SEGMENT_THRESHOLD:
        return _AUTO_SEGMENT_SIZE, True
    return 0, False


def slice_turns(
    turn_dicts: list[dict],
    start_turn: int | None,
    max_turns: int | None,
) -> list[dict]:
    """Slice turn_dicts according to --start-turn and --max-turns flags.

    Args:
        turn_dicts: Full list of turn dicts (1-based turn numbering in IDs).
        start_turn: 1-based start turn (None = from the beginning).
        max_turns: When used alone, a count cap.  When combined with
            start_turn, an absolute turn-number upper bound.

    Returns:
        The sliced list.

    Raises:
        SystemExit: On invalid arguments.
    """
    if start_turn is not None:
        if start_turn < 1:
            print("ERROR: --start-turn must be >= 1.", file=sys.stderr)
            sys.exit(1)
        start_idx = start_turn - 1
        if start_idx >= len(turn_dicts):
            print(f"ERROR: --start-turn {start_turn} exceeds total turns ({len(turn_dicts)}).",
                  file=sys.stderr)
            sys.exit(1)
        print(f"  Starting from turn {start_turn} of {len(turn_dicts)} (--start-turn).")

        # When both flags are set, --max-turns is an absolute turn number
        # (upper bound), not a count.  E.g. --start-turn 26 --max-turns 50
        # extracts turns 26-50.
        if max_turns is not None:
            if max_turns < 1:
                print("ERROR: --max-turns must be >= 1.", file=sys.stderr)
                sys.exit(1)
            if max_turns < start_turn:
                print(f"ERROR: --max-turns ({max_turns}) < --start-turn ({start_turn}).",
                      file=sys.stderr)
                sys.exit(1)
            end_idx = min(max_turns, len(turn_dicts))
            print(f"  Extracting turns {start_turn}-{end_idx} (--start-turn + --max-turns).")
            return turn_dicts[start_idx:end_idx]
        return turn_dicts[start_idx:]

    if max_turns is not None:
        # Standalone --max-turns (no --start-turn) — count cap
        if max_turns < 1:
            print("ERROR: --max-turns must be >= 1.", file=sys.stderr)
            sys.exit(1)
        if max_turns < len(turn_dicts):
            print(f"  Limiting to first {max_turns} of {len(turn_dicts)} turns (--max-turns).")
            return turn_dicts[:max_turns]

    return turn_dicts


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Validate --start-date format if provided
    if args.start_date is not None:
        try:
            datetime.strptime(args.start_date, "%Y-%m-%d")
        except ValueError:
            print(
                f"ERROR: Invalid --start-date '{args.start_date}'. "
                "Expected format: YYYY-MM-DD (e.g. 2026-01-15).",
                file=sys.stderr,
            )
            sys.exit(1)

    # Validate inputs
    if not os.path.isfile(args.file):
        print(f"ERROR: Source file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    # Ensure session directory is under sessions/ (fixes #19)
    # Normalize first so equivalent relative paths like "./sessions/foo" are
    # recognized correctly.  Preserve absolute paths.
    session_dir = os.path.normpath(args.session)
    if not os.path.isabs(session_dir):
        first_component = session_dir.split(os.sep, 1)[0]
        if first_component != "sessions":
            session_dir = os.path.join("sessions", session_dir)
    os.makedirs(session_dir, exist_ok=True)

    # Read source (fixes #20 — encoding detection with fallback)
    content = None
    used_encoding = args.encoding
    for enc in [args.encoding, "utf-8-sig", "utf-8", "latin-1"]:
        if content is not None:
            break
        try:
            with open(args.file, "r", encoding=enc) as f:
                content = f.read()
            used_encoding = enc
        except UnicodeDecodeError:
            continue
    if content is None:
        print(f"ERROR: Cannot read '{args.file}' with any attempted encoding.", file=sys.stderr)
        print("Try --encoding utf-16 or check the file.", file=sys.stderr)
        sys.exit(1)
    if used_encoding != args.encoding:
        print(f"WARNING: Could not read with '{args.encoding}', fell back to '{used_encoding}'.")

    # Normalize smart quotes / em-dashes to ASCII if requested (fixes #20)
    if args.normalize_quotes:
        content = (
            content
            .replace("\u2018", "'")
            .replace("\u2019", "'")
            .replace("\u201C", '"')
            .replace("\u201D", '"')
            .replace("\u2014", "--")
            .replace("\u2013", "-")
            .replace("\u2026", "...")
        )

    if not content.strip():
        print("ERROR: Source file is empty.", file=sys.stderr)
        sys.exit(1)

    dm_labels = [args.dm_label] + [args.dm_label.lower(), args.dm_label.upper()]
    player_labels = [args.player_label] + [args.player_label.lower(), args.player_label.upper()]

    # Detect or apply format
    fmt = args.format
    if fmt == "auto":
        fmt = detect_format(content, dm_labels, player_labels)
        print(f"Auto-detected format: {fmt}")

    # Parse turns
    if fmt == "markdown":
        turns = parse_markdown_format(content)
    elif fmt == "labeled":
        turns = parse_labeled_format(content, dm_labels, player_labels)
    else:
        turns = parse_alternating_format(content, args.first_speaker)

    if not turns:
        print("ERROR: No turns could be parsed from the source file.", file=sys.stderr)
        print("Check --format, --dm-label, --player-label, and --first-speaker.", file=sys.stderr)
        sys.exit(1)

    _warn_consecutive_speakers(turns)

    dm_count = sum(1 for t in turns if t.speaker == "dm")
    player_count = sum(1 for t in turns if t.speaker == "player")
    print(f"\nParsed {len(turns)} turns ({dm_count} DM, {player_count} player).")

    if args.dry_run:
        print("\n[DRY RUN — no files will be written]\n")

    transcript_dir = os.path.join(session_dir, "transcript")
    raw_dir = os.path.join(session_dir, "raw")
    latest_turn_id = f"turn-{turns[-1].sequence:03d}"

    print("\nWriting transcript files:")
    write_turn_files(transcript_dir, turns, dry_run=args.dry_run, overwrite=args.overwrite)

    print("\nWriting raw transcript:")
    write_full_transcript(
        raw_dir,
        turns,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        allow_raw_overwrite=args.allow_raw_overwrite,
    )

    print("\nChecking metadata:")
    ensure_metadata(session_dir, turns, dry_run=args.dry_run, start_date=args.start_date)

    print("\nScaffolding derived files:")
    ensure_derived_scaffolds(session_dir, latest_turn_id, dry_run=args.dry_run)

    print("\nScaffolding exports directory:")
    ensure_exports_dir(session_dir, dry_run=args.dry_run)

    # Build turn_dicts for use by structured and semantic extraction
    turn_dicts = [
        {"turn_id": _format_turn_id(t.sequence), "speaker": t.speaker, "text": t.text}
        for t in turns
    ]
    total_turns = len(turn_dicts)

    # Slice to --start-turn / --max-turns (#251)
    turn_dicts = slice_turns(turn_dicts, args.start_turn, args.max_turns)

    effective_segment_size, auto_segment_enabled = _resolve_segment_size(
        args.segment_size,
        len(turn_dicts),
    )
    if auto_segment_enabled:
        print(
            f"  INFO: Auto-enabled --segment-size {_AUTO_SEGMENT_SIZE} "
            f"(session has {len(turn_dicts)} turns, threshold is {_AUTO_SEGMENT_THRESHOLD}). "
            "Pass --segment-size 0 explicitly to disable.",
            file=sys.stderr,
        )

    # Extract structured data from all turns (#21, #27, #28)
    try:
        from extract_structured_data import (
            extract_all,
            write_extracted_data,
            update_state_temporal,
        )

        print("\nExtracting structured data:")
        data = extract_all(turn_dicts)
        found = (
            len(data["session_events"])
            + len(data["timeline"])
            + len(data["season_summaries"])
        )
        if found > 0:
            derived_dir = os.path.join(session_dir, "derived")
            print(
                f"  {len(data['session_events'])} mechanical event(s), "
                f"{len(data['timeline'])} temporal marker(s), "
                f"{len(data['season_summaries'])} season summary/summaries"
            )
            write_extracted_data(derived_dir, data, dry_run=args.dry_run)
            update_state_temporal(derived_dir, data["timeline"], dry_run=args.dry_run)
        else:
            print("  No structured data detected.")
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
    wiki_generated = False
    try:
        from semantic_extraction import extract_semantic_batch

        llm_overrides = {}
        if args.model:
            llm_overrides["model"] = args.model
        if args.base_url:
            llm_overrides["base_url"] = args.base_url

        print("\nRunning semantic extraction:")
        extract_semantic_batch(
            turn_dicts, session_dir, framework_dir=args.framework, dry_run=args.dry_run,
            overrides=llm_overrides or None,
            segment_size=effective_segment_size,
        )

        # Stub backfill pass (#128, #131 — now runs by default)
        if args.skip_backfill:
            pass  # Explicitly skipped via --skip-backfill
        elif args.dry_run:
            print("  Stub backfill: skipped during dry run")
        else:
            from semantic_extraction import backfill_stubs
            from catalog_merger import load_catalogs, load_events, save_catalogs, save_events
            from llm_client import LLMClient

            catalog_dir = os.path.join(args.framework, "catalogs")
            catalogs = load_catalogs(catalog_dir)
            events_list = load_events(catalog_dir)
            llm = LLMClient("config/llm.json", overrides=llm_overrides or None)
            count = backfill_stubs(turn_dicts, catalogs, events_list, llm)
            if count:
                save_catalogs(catalog_dir, catalogs)
                save_events(catalog_dir, events_list)
            print(f"  Stub backfill: {count} stub(s) enriched")

        # PC alias merge pass (#134)
        if args.dry_run:
            print("  PC alias merge: skipped during dry run")
        else:
            from semantic_extraction import _merge_pc_aliases
            from catalog_merger import load_catalogs, load_events, save_catalogs, save_events

            catalog_dir = os.path.join(args.framework, "catalogs")
            catalogs = load_catalogs(catalog_dir)
            events_list = load_events(catalog_dir)
            merged_aliases = _merge_pc_aliases(catalogs, events_list, catalog_dir)
            if merged_aliases:
                save_catalogs(catalog_dir, catalogs)
                save_events(catalog_dir, events_list)
                print(f"  PC alias merge: merged {len(merged_aliases)} alias(es): {merged_aliases}")

        # Generate wiki pages for human review (#251)
        wiki_generated = False
        if args.dry_run:
            print("  Wiki generation: skipped during dry run")
        else:
            try:
                from generate_wiki_pages import generate_wiki_pages
                catalog_dir = os.path.join(args.framework, "catalogs")
                print("\nGenerating wiki pages for review:")
                generate_wiki_pages(catalog_dir, entity_types=None, index_only=False)
                print(f"  Wiki pages generated. Review {args.framework}/catalogs/*/README.md and individual pages.")
                wiki_generated = True
            except Exception as e:
                print(f"  WARNING: Wiki generation failed: {e}", file=sys.stderr)
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

    # DM profile analysis — extract behavioral patterns from DM turns (#260)
    try:
        from dm_profile_analyzer import analyze_batch as analyze_dm_batch

        dm_turns = [t for t in turn_dicts if t.get("speaker") == "dm"]
        if dm_turns:
            llm_overrides = {}
            if args.model:
                llm_overrides["model"] = args.model
            if args.base_url:
                llm_overrides["base_url"] = args.base_url

            # bootstrap_session's --max-turns is an absolute turn number
            # when combined with --start-turn, but dm_profile_analyzer's
            # max_turns is a count of DM turns.  Scope to the actual
            # DM turns in the extracted batch so analysis stays aligned.
            dm_analysis_max_turns = len(dm_turns)

            print("\nRunning DM profile analysis:")
            analyze_dm_batch(
                session_dir=session_dir,
                framework_dir=args.framework,
                start_turn=args.start_turn or 0,
                max_turns=dm_analysis_max_turns,
                dry_run=args.dry_run,
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

    print()
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to write files.")
    else:
        # Print summary with next-step suggestion (#251)
        first_turn = turn_dicts[0]["turn_id"] if turn_dicts else "?"
        last_turn = turn_dicts[-1]["turn_id"] if turn_dicts else "?"
        print(f"Extraction complete for turns {first_turn} through {last_turn}.")
        if wiki_generated:
            print(f"Wiki pages generated — review {args.framework}/catalogs/ for entity pages.")
        if args.start_turn is not None:
            # Compute next start from actual last extracted absolute turn number
            last_extracted_abs = args.start_turn + len(turn_dicts) - 1
            next_start = last_extracted_abs + 1
            if next_start <= total_turns:
                suggested_end = min(next_start + len(turn_dicts) - 1, total_turns)
                print(f"To continue: --start-turn {next_start} --max-turns {suggested_end}")
            else:
                print("All turns have been extracted.")
        print()
        print("Next steps:")
        print(f"  python tools/update_state.py --session {session_dir}")
        print(f"  python tools/analyze_next_move.py --session {session_dir}")
        print(f"  python tools/validate.py --session {session_dir}")


if __name__ == "__main__":
    main()
