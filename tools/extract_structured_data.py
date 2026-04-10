#!/usr/bin/env python3
"""
extract_structured_data.py — Extract structured data from session turns.

Handles three types of extraction:
  1. Inline game markers (#21): HP changes, long rests, spell usage, item acquisitions
  2. Temporal markers (#27): Season transitions, time progression
  3. Season summary blocks (#28): Structured season summary data from DM turns

Can be run standalone or imported by ingest_turn.py / bootstrap_session.py.

Usage:
    python tools/extract_structured_data.py --session sessions/session-001
    python tools/extract_structured_data.py --session sessions/session-001 --dry-run
"""

import argparse
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Marker extraction (#21)
# ---------------------------------------------------------------------------

MARKER_PATTERNS = {
    "hp_loss": re.compile(
        r"\U0001FA78\s*\n?\s*(\d+)\s*HP\s+lost",
        re.IGNORECASE,
    ),
    "hp_restore": re.compile(
        r"\u2764\uFE0F\u200D\U0001FA79\s*\n?\s*(\d+)\s*HP\s+restored",
        re.IGNORECASE,
    ),
    "long_rest": re.compile(
        r"---\s*Long\s+rest\s*---",
        re.IGNORECASE,
    ),
    "spell_use": re.compile(
        r"^Used\s+(.+?)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "item_acquired": re.compile(
        r"New\s+item\s+acquired:\s*(.+?)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "level_up": re.compile(
        r"(?:Level(?:ed)?\s+up|Reached\s+level)\s+(\d+)",
        re.IGNORECASE,
    ),
}

# Maximum words in a "Used X" match to avoid false positives on narrative text
_SPELL_MAX_WORDS = 6


def extract_markers(text: str, turn_id: str, start_id: int = 1) -> list[dict]:
    """Extract inline game markers from turn text.

    Returns a list of mechanical event dicts matching session-events.schema.json.
    """
    events: list[dict] = []
    seq = start_id

    for m in MARKER_PATTERNS["hp_loss"].finditer(text):
        events.append({
            "id": f"mech-{seq:03d}",
            "source_turn": turn_id,
            "type": "hp_change",
            "details": {"amount": -int(m.group(1)), "description": f"{m.group(1)} HP lost"},
            "raw_marker": m.group(0).strip(),
        })
        seq += 1

    for m in MARKER_PATTERNS["hp_restore"].finditer(text):
        events.append({
            "id": f"mech-{seq:03d}",
            "source_turn": turn_id,
            "type": "hp_change",
            "details": {"amount": int(m.group(1)), "description": f"{m.group(1)} HP restored"},
            "raw_marker": m.group(0).strip(),
        })
        seq += 1

    for m in MARKER_PATTERNS["long_rest"].finditer(text):
        events.append({
            "id": f"mech-{seq:03d}",
            "source_turn": turn_id,
            "type": "long_rest",
            "details": {},
            "raw_marker": m.group(0).strip(),
        })
        seq += 1

    for m in MARKER_PATTERNS["spell_use"].finditer(text):
        spell_name = m.group(1).strip()
        if len(spell_name.split()) <= _SPELL_MAX_WORDS:
            events.append({
                "id": f"mech-{seq:03d}",
                "source_turn": turn_id,
                "type": "spell_use",
                "details": {"spell_name": spell_name},
                "raw_marker": m.group(0).strip(),
            })
            seq += 1

    for m in MARKER_PATTERNS["item_acquired"].finditer(text):
        events.append({
            "id": f"mech-{seq:03d}",
            "source_turn": turn_id,
            "type": "item_acquired",
            "details": {"item_name": m.group(1).strip()},
            "raw_marker": m.group(0).strip(),
        })
        seq += 1

    for m in MARKER_PATTERNS["level_up"].finditer(text):
        events.append({
            "id": f"mech-{seq:03d}",
            "source_turn": turn_id,
            "type": "level_up",
            "details": {"new_level": int(m.group(1))},
            "raw_marker": m.group(0).strip(),
        })
        seq += 1

    return events


# ---------------------------------------------------------------------------
# Temporal extraction (#27)
# ---------------------------------------------------------------------------

SEASON_NAMES = ["spring", "summer", "fall", "winter", "autumn"]
_SEASON_ALT = "|".join(SEASON_NAMES)

SEASON_TRANSITION_PATTERNS = [
    re.compile(
        r"(?:As\s+)?(?:the\s+)?(" + _SEASON_ALT + r")\s+"
        r"(?:arrives?|begins?|comes?|sets?\s+in|deepens?|fades?|wanes?"
        r"|months?\s+(?:bring|pass))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:The\s+)?(" + _SEASON_ALT + r")\s+"
        r"(?:season|months?)\s+(?:bring|arrive|begin|pass|come)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:With\s+the\s+(?:arrival|coming|onset)\s+of\s+)(" + _SEASON_ALT + r")",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:Early|Mid|Late)\s+)?(" + _SEASON_ALT + r")\s+"
        r"(?:has\s+)?(?:come|arrived|begun|settled)",
        re.IGNORECASE,
    ),
]

YEAR_PATTERN_NUMERIC = re.compile(r"[Yy]ear\s+(\d+)")
ORDINAL_MAP = {"second": 2, "third": 3, "fourth": 4, "fifth": 5}
YEAR_PATTERN_ORDINAL = re.compile(
    r"(?:the\s+)?(" + "|".join(ORDINAL_MAP) + r")\s+year",
    re.IGNORECASE,
)
def _normalize_season(name: str) -> str:
    """Normalize season name (autumn -> fall)."""
    name = name.lower().strip()
    return "fall" if name == "autumn" else name


def _detect_year(text: str) -> int | None:
    """Try to extract an in-game year number from text."""
    m = YEAR_PATTERN_NUMERIC.search(text)
    if m:
        return int(m.group(1))
    m = YEAR_PATTERN_ORDINAL.search(text)
    if m:
        return ORDINAL_MAP.get(m.group(1).lower())
    return None


def extract_temporal_markers(
    text: str, turn_id: str, start_id: int = 1
) -> list[dict]:
    """Extract temporal markers from turn text (typically DM turns).

    Returns a list of timeline entry dicts matching timeline.schema.json.
    """
    entries: list[dict] = []
    seq = start_id
    seen_seasons: set[str] = set()

    for pattern in SEASON_TRANSITION_PATTERNS:
        for m in pattern.finditer(text):
            season = _normalize_season(m.group(1))
            if season in seen_seasons:
                continue
            seen_seasons.add(season)

            year = _detect_year(text)

            entry: dict = {
                "id": f"time-{seq:03d}",
                "source_turn": turn_id,
                "type": "season_transition",
                "season": season,
                "description": f"Transition to {season}"
                + (f" (year {year})" if year else ""),
                "raw_text": m.group(0).strip(),
            }
            if year is not None:
                entry["year"] = year
            entries.append(entry)
            seq += 1

    return entries


# ---------------------------------------------------------------------------
# Season summary extraction (#28)
# ---------------------------------------------------------------------------

_SECTION_HEADER_RE = re.compile(
    r"^(\*{0,2})"
    r"(?P<name>"
    r"Regional\s+Changes?"
    r"|Faction\s+Actions?"
    r"|Economic\s+(?:(?:or\s+)?Ecological\s+)?(?:Shifts?|Changes?|Updates?)"
    r"|Environmental\s+(?:Notes?|Conditions?|Changes?)"
    r"|Rumors?\s+(?:Reaching|and\s+).*"
    r"|Consequences?\s+(?:of\s+).*"
    r")"
    r":?\s*\1\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_SECTION_NAME_MAP = {
    "regional": "regional_changes",
    "faction": "faction_actions",
    "economic": "economic_shifts",
    "environmental": "environmental_notes",
    "rumors": "rumors",
    "rumor": "rumors",
    "consequences": "consequences",
    "consequence": "consequences",
}

SEASON_SUMMARY_BLOCK_PATTERN = re.compile(
    r"(?:^|\n)(?:\*{0,2})?(?:"
    r"Season\s+(?:\d+\s+)?Summary"
    r"|(?:" + _SEASON_ALT + r")\s+(?:Summary|Overview|Report|Update)"
    r")(?:\*{0,2})?\s*(?::\s*(?:\*{0,2})?(?:" + _SEASON_ALT + r")?(?:\*{0,2})?)?\s*\n",
    re.IGNORECASE,
)

_HEADER_SEASON_RE = re.compile(
    r"Season\s+(?:\d+\s+)?Summary\s*:\s*(?:\*{0,2})?(?P<season>" + _SEASON_ALT + r")",
    re.IGNORECASE,
)

_MIN_SECTIONS_FOR_SUMMARY = 2
_RAW_TEXT_LIMIT = 500


def _parse_season_sections(text: str) -> tuple[dict[str, str], int, int]:
    """Parse season summary sections by finding headers and slicing between them.

    Returns (sections_dict, block_start, block_end) where block_start/end
    mark the span of the detected sections in *text*.
    """
    headers = list(_SECTION_HEADER_RE.finditer(text))
    if not headers:
        return {}, 0, 0

    sections: dict[str, str] = {}
    block_start = headers[0].start()
    block_end = headers[-1].end()

    for i, hdr in enumerate(headers):
        raw_name = hdr.group("name").strip()
        key = raw_name.split()[0].lower()
        section_key = _SECTION_NAME_MAP.get(key)
        if not section_key:
            continue

        content_start = hdr.end()
        if i + 1 < len(headers):
            content_end = headers[i + 1].start()
        else:
            # Last section: content runs to the next blank-line-separated block
            # or end of text.  Stop at a double newline that is followed by text
            # that doesn't look like a continuation (not starting with - or *).
            rest = text[content_start:]
            double_nl = re.search(r"\n\n(?=[^\s\-\*])", rest)
            if double_nl:
                content_end = content_start + double_nl.start()
            else:
                content_end = len(text)

        content = text[content_start:content_end].strip()
        block_end = max(block_end, content_start + len(text[content_start:content_end].rstrip()))
        if content:
            sections[section_key] = content

    return sections, block_start, block_end


def extract_season_summaries(
    text: str, turn_id: str, start_id: int = 1
) -> list[dict]:
    """Extract season summary blocks from DM turn text.

    A season summary is detected when:
      1. An explicit "Season Summary" / "<season> Summary" header is found, OR
      2. At least two recognized section headers appear in a single turn.

    Returns a list of season summary dicts matching season-summary.schema.json.
    """
    summaries: list[dict] = []
    seq = start_id

    sections, block_start, block_end = _parse_season_sections(text)

    has_explicit_header = bool(SEASON_SUMMARY_BLOCK_PATTERN.search(text))
    has_enough_sections = len(sections) >= _MIN_SECTIONS_FOR_SUMMARY

    if not has_explicit_header and not has_enough_sections:
        return []

    # Detect which season this summary covers — first try the header line
    season = None
    hdr_season_match = _HEADER_SEASON_RE.search(text)
    if hdr_season_match:
        season = _normalize_season(hdr_season_match.group("season"))
    else:
        for sn in SEASON_NAMES:
            if re.search(r"\b" + sn + r"\b", text[:300], re.IGNORECASE):
                season = _normalize_season(sn)
                break

    year = _detect_year(text)

    # Include explicit header in block span if present
    header_match = SEASON_SUMMARY_BLOCK_PATTERN.search(text)
    if header_match:
        block_start = min(block_start, header_match.start()) if block_start > 0 else header_match.start()
    raw_block = text[block_start:block_end].strip() if block_end > block_start else ""

    summary: dict = {
        "id": f"ss-{seq:03d}",
        "source_turn": turn_id,
        "sections": sections if sections else {},
        "raw_text": raw_block[:_RAW_TEXT_LIMIT]
        + ("..." if len(raw_block) > _RAW_TEXT_LIMIT else ""),
    }
    if season:
        summary["season"] = season
    if year is not None:
        summary["year"] = year
    summaries.append(summary)

    return summaries


# ---------------------------------------------------------------------------
# Full extraction pipeline
# ---------------------------------------------------------------------------

def extract_all(turns: list[dict]) -> dict:
    """Run full extraction on a list of turn dicts.

    Each turn dict must have keys: turn_id, speaker, text.

    Returns::

        {
            "session_events": [...],
            "timeline": [...],
            "season_summaries": [...],
        }
    """
    all_events: list[dict] = []
    all_timeline: list[dict] = []
    all_summaries: list[dict] = []

    mech_seq = 1
    time_seq = 1
    ss_seq = 1

    for turn in turns:
        turn_id = turn["turn_id"]
        speaker = turn["speaker"]
        text = turn["text"]

        # Marker extraction applies to all turns (primarily player turns)
        markers = extract_markers(text, turn_id, start_id=mech_seq)
        all_events.extend(markers)
        mech_seq += len(markers)

        # Temporal and season summary extraction applies to DM turns
        if speaker == "dm":
            temporal = extract_temporal_markers(text, turn_id, start_id=time_seq)
            all_timeline.extend(temporal)
            time_seq += len(temporal)

            summaries = extract_season_summaries(text, turn_id, start_id=ss_seq)
            all_summaries.extend(summaries)
            ss_seq += len(summaries)

    return {
        "session_events": all_events,
        "timeline": all_timeline,
        "season_summaries": all_summaries,
    }


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _load_json_array(path: str) -> list:
    """Load a JSON array from a file, returning [] if missing or empty."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _next_seq(items: list[dict], prefix: str) -> int:
    """Determine the next sequence number from existing items with the given ID prefix."""
    pattern = re.compile(r"^" + re.escape(prefix) + r"-(\d+)$")
    max_seq = 0
    for item in items:
        m = pattern.match(item.get("id", ""))
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def write_extracted_data(
    derived_dir: str, data: dict, dry_run: bool = False
) -> None:
    """Write extracted data to derived files."""
    os.makedirs(derived_dir, exist_ok=True)
    files = {
        "session-events.json": data["session_events"],
        "timeline.json": data["timeline"],
        "season-summaries.json": data["season_summaries"],
    }
    for filename, content in files.items():
        path = os.path.join(derived_dir, filename)
        if dry_run:
            print(f"  [DRY]    would write {path} ({len(content)} entries)")
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(content, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print(f"  [WRITE]  {path} ({len(content)} entries)")


def update_state_temporal(
    derived_dir: str, timeline: list[dict], dry_run: bool = False
) -> None:
    """Update state.json with temporal information from the timeline."""
    if not timeline:
        return

    state_file = os.path.join(derived_dir, "state.json")
    if not os.path.exists(state_file):
        return

    latest = timeline[-1]

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    temporal: dict = {
        "last_temporal_turn": latest["source_turn"],
    }
    if "season" in latest:
        temporal["current_season"] = latest["season"]
    if "year" in latest:
        temporal["current_year"] = latest["year"]

    if dry_run:
        print(f"  [DRY]    would update temporal in {state_file}: {temporal}")
    else:
        state["temporal"] = temporal
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"  [UPDATE] {state_file} temporal: {temporal}")


def extract_and_merge_single_turn(
    session_dir: str,
    turn_id: str,
    speaker: str,
    text: str,
    dry_run: bool = False,
) -> dict:
    """Extract structured data from a single new turn and merge into existing
    derived files.  Used by ingest_turn.py for incremental updates.

    Returns the extraction results dict.
    """
    derived_dir = os.path.join(session_dir, "derived")
    os.makedirs(derived_dir, exist_ok=True)

    # Load existing data to determine next IDs
    existing_events = _load_json_array(os.path.join(derived_dir, "session-events.json"))
    existing_timeline = _load_json_array(os.path.join(derived_dir, "timeline.json"))
    existing_summaries = _load_json_array(os.path.join(derived_dir, "season-summaries.json"))

    mech_seq = _next_seq(existing_events, "mech")
    time_seq = _next_seq(existing_timeline, "time")
    ss_seq = _next_seq(existing_summaries, "ss")

    # Extract from the single turn
    new_events = extract_markers(text, turn_id, start_id=mech_seq)
    new_timeline: list[dict] = []
    new_summaries: list[dict] = []

    if speaker == "dm":
        new_timeline = extract_temporal_markers(text, turn_id, start_id=time_seq)
        new_summaries = extract_season_summaries(text, turn_id, start_id=ss_seq)

    # Merge and write
    merged = {
        "session_events": existing_events + new_events,
        "timeline": existing_timeline + new_timeline,
        "season_summaries": existing_summaries + new_summaries,
    }

    found = len(new_events) + len(new_timeline) + len(new_summaries)
    if found > 0:
        print(f"\nStructured data extraction ({turn_id}):")
        if new_events:
            print(f"  {len(new_events)} mechanical event(s)")
        if new_timeline:
            print(f"  {len(new_timeline)} temporal marker(s)")
        if new_summaries:
            print(f"  {len(new_summaries)} season summary/summaries")
        write_extracted_data(derived_dir, merged, dry_run=dry_run)
        if new_timeline:
            update_state_temporal(derived_dir, merged["timeline"], dry_run=dry_run)

    return {
        "session_events": new_events,
        "timeline": new_timeline,
        "season_summaries": new_summaries,
    }


# ---------------------------------------------------------------------------
# Turn listing (shared helper, same logic as update_state.py)
# ---------------------------------------------------------------------------

def list_turns(transcript_dir: str) -> list[dict]:
    """Read all turns from transcript directory."""
    if not os.path.isdir(transcript_dir):
        return []
    pattern = re.compile(r"^turn-(\d+)-(player|dm)\.md$")
    turns: list[dict] = []
    for fname in sorted(os.listdir(transcript_dir)):
        m = pattern.match(fname)
        if m:
            seq = int(m.group(1))
            speaker = m.group(2)
            turn_id = f"turn-{seq:03d}"
            filepath = os.path.join(transcript_dir, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            lines = content.split("\n")
            text_lines = [l for l in lines if not l.startswith("# turn-")]
            text = "\n".join(text_lines).strip()
            turns.append({
                "turn_id": turn_id,
                "sequence_number": seq,
                "speaker": speaker,
                "text": text,
            })
    return turns


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract structured data from session turns: "
            "inline markers (#21), temporal markers (#27), "
            "season summary blocks (#28)."
        ),
    )
    parser.add_argument(
        "--session", required=True, help="Path to the session directory."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be extracted without writing files.",
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

    print(f"Extracting structured data from {len(turns)} turn(s)...")
    data = extract_all(turns)

    print(f"\nFound:")
    print(f"  {len(data['session_events'])} mechanical event(s)")
    print(f"  {len(data['timeline'])} temporal marker(s)")
    print(f"  {len(data['season_summaries'])} season summary/summaries")

    print("\nWriting derived files:")
    write_extracted_data(derived_dir, data, dry_run=args.dry_run)
    update_state_temporal(derived_dir, data["timeline"], dry_run=args.dry_run)

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to write files.")
    else:
        print("\nExtraction complete.")
        print()
        print("Next steps:")
        print(f"  python tools/validate.py --session {session_dir}")


if __name__ == "__main__":
    main()
