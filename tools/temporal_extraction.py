#!/usr/bin/env python3
"""
temporal_extraction.py — Extract temporal signals from turn text and events.

Provides pattern-based detection of season markers, biological time markers
(pregnancies/births), construction milestones, and time-skip language.
Optionally runs an LLM-based estimator for ambiguous turns.

Returns intermediate temporal signal dicts that are assigned IDs and
merged into timeline entries conforming to schemas/timeline.schema.json
via ``merge_temporal_signals()``.
"""

import json
import os
import re

# Maximum length for captured signal text to avoid storing full paragraphs
MAX_SIGNAL_TEXT_LENGTH = 120

# ---------------------------------------------------------------------------
# Season keyword patterns
# ---------------------------------------------------------------------------

SEASON_PATTERNS: dict[str, list[str]] = {
    "winter": [
        r"\bsnow\b", r"\bfrost\b", r"\bice\b", r"\bfrozen\b",
        r"\bcold\b", r"\bbiting cold\b", r"\bdeep winter\b",
        r"\bfirst snow\b", r"\bwinter\b",
    ],
    "spring": [
        r"\bthaw\b", r"\bmelt\b", r"\bsprout\b", r"\bbloom\b",
        r"\bspring\b", r"\bfirst green\b", r"\bsnow melt\b",
        r"\bsnow gives way\b",
    ],
    "summer": [
        r"\bharvest\b", r"\bwarm\b", r"\bgrowth\b",
        r"\bsummer\b", r"\bheat\b", r"\bfull span of summer\b",
    ],
    "autumn": [
        r"\bleaves\b", r"\bcooling\b", r"\bautumn\b",
        r"\bfall\b", r"\bpreparation\b", r"\bfirst frost\b",
    ],
}

# Finer season detection patterns (early/mid/late)
SEASON_REFINEMENT: list[tuple[str, str]] = [
    (r"\bearly[_ ]winter\b", "early_winter"),
    (r"\bmid[_ ]winter\b", "mid_winter"),
    (r"\blate[_ ]winter\b", "late_winter"),
    (r"\bdeep winter\b", "mid_winter"),
    (r"\bfirst snow\b", "early_winter"),
    (r"\bwinter.*settle[ds]?\b", "early_winter"),
    (r"\bearly[_ ]spring\b", "early_spring"),
    (r"\bmid[_ ]spring\b", "mid_spring"),
    (r"\blate[_ ]spring\b", "late_spring"),
    (r"\bfirst.*signs? of thaw\b", "early_spring"),
    (r"\bthaw\b", "early_spring"),
    (r"\bsnow melt\b", "early_spring"),
    (r"\bearly[_ ]summer\b", "early_summer"),
    (r"\bmid[_ ]summer\b", "mid_summer"),
    (r"\blate[_ ]summer\b", "late_summer"),
    (r"\bfull span of summer\b", "mid_summer"),
    (r"\bearly[_ ]autumn\b", "early_autumn"),
    (r"\bmid[_ ]autumn\b", "mid_autumn"),
    (r"\blate[_ ]autumn\b", "late_autumn"),
    (r"\bautumn does not linger\b", "late_autumn"),
]

# Time-of-day markers
TIME_OF_DAY_PATTERNS: list[tuple[str, str]] = [
    (r"\bat first light\b", "dawn"),
    (r"\bfirst light\b", "dawn"),
    (r"\bfirst blush of dawn\b", "dawn"),
    (r"\bdawn\b", "dawn"),
    (r"\bdusk\b", "dusk"),
    (r"\bnightfall\b", "night"),
    (r"\bby night\b", "night"),
    (r"\bmorning\b", "morning"),
]

# Biological markers
BIOLOGICAL_PATTERNS: list[tuple[str, str]] = [
    (r"\bpregnan\w*\b", "pregnancy"),
    (r"\bbelly.*?swell\w*\b", "pregnancy_progression"),
    (r"\bmorning sickness\b", "pregnancy_early"),
    (r"\blife.*?taken root\b", "pregnancy_discovery"),
    (r"\blabor\b", "labor"),
    (r"\bbirth\b", "birth"),
    (r"\bborn\b", "birth"),
    (r"\bnew ?born\b", "birth"),
]

# Time-skip language
TIME_SKIP_PATTERNS: list[tuple[str, str]] = [
    (r"\bdays? (?:pass|bleed|unfold)\b", "days_pass"),
    (r"\bweeks? (?:pass|continue|unfold)\b", "weeks_pass"),
    (r"\bmonths? pass\b", "months_pass"),
    (r"\btime (?:pass|advance)[ds]?\b", "time_passes"),
    (r"\bfollowing months?\b", "months_pass"),
    (r"\bthree weeks\b", "weeks_pass"),
]


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


def _cap_signal_text(text: str) -> str:
    """Truncate signal text to MAX_SIGNAL_TEXT_LENGTH with ellipsis if needed."""
    if len(text) <= MAX_SIGNAL_TEXT_LENGTH:
        return text
    return text[:MAX_SIGNAL_TEXT_LENGTH - 3] + "..."


def _detect_base_season(text: str) -> str | None:
    """Detect the dominant base season from text using keyword counts.

    Requires at least 2 distinct keyword matches for the winning season,
    and a margin of at least 2 over the runner-up to avoid false positives
    from ambiguous text.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    distinct_matches: dict[str, int] = {}
    for season, patterns in SEASON_PATTERNS.items():
        count = 0
        distinct = 0
        for pattern in patterns:
            hits = len(re.findall(pattern, text_lower))
            if hits > 0:
                distinct += 1
                count += hits
        if count > 0:
            scores[season] = count
            distinct_matches[season] = distinct
    if not scores:
        return None
    best = max(scores, key=scores.get)
    # Require at least 2 distinct keyword patterns matched
    if distinct_matches.get(best, 0) < 2:
        return None
    # Require margin of 2 over runner-up
    runner_up = max((v for k, v in scores.items() if k != best), default=0)
    if scores[best] - runner_up < 2:
        return None
    return best


def _detect_fine_season(text: str) -> str | None:
    """Detect a fine-grained season label (early/mid/late) from text."""
    text_lower = text.lower()
    for pattern, label in SEASON_REFINEMENT:
        if re.search(pattern, text_lower):
            return label
    return None


def _detect_biological_markers(text: str) -> list[tuple[str, str]]:
    """Detect biological temporal markers (pregnancy, birth)."""
    text_lower = text.lower()
    found = []
    for pattern, marker_type in BIOLOGICAL_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            found.append((marker_type, _cap_signal_text(match.group())))
    return found


def _detect_time_skips(text: str) -> list[tuple[str, str]]:
    """Detect explicit time-skip language."""
    text_lower = text.lower()
    found = []
    for pattern, skip_type in TIME_SKIP_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            found.append((skip_type, _cap_signal_text(match.group())))
    return found


def _detect_time_of_day(text: str) -> list[str]:
    """Detect time-of-day markers."""
    text_lower = text.lower()
    found = []
    for pattern, label in TIME_OF_DAY_PATTERNS:
        if re.search(pattern, text_lower):
            found.append(label)
    return found


def extract_temporal_signals(turn_text: str, turn_id: str,
                             events: list[dict] | None = None) -> list[dict]:
    """Extract temporal signals from turn text and associated events.

    Returns a list of timeline entry dicts (not yet assigned IDs).
    Each entry has at minimum: source_turn, type, and signals.
    """
    signals: list[dict] = []

    # 1. Season detection
    fine_season = _detect_fine_season(turn_text)
    base_season = _detect_base_season(turn_text)
    if fine_season:
        signals.append({
            "source_turn": turn_id,
            "type": "season_transition",
            "season": fine_season,
            "signals": [f"fine season: {fine_season}"],
            "confidence": 0.8,
        })
    elif base_season:
        # Map base season to mid_ variant
        mapped = f"mid_{base_season}"
        signals.append({
            "source_turn": turn_id,
            "type": "season_transition",
            "season": mapped,
            "signals": [f"base season: {base_season}"],
            "confidence": 0.5,
        })

    # 2. Time-skip markers
    skips = _detect_time_skips(turn_text)
    for skip_type, raw in skips:
        signals.append({
            "source_turn": turn_id,
            "type": "time_skip",
            "signals": [f"{skip_type}: {raw}"],
            "confidence": 0.6,
            "raw_text": raw,
        })

    # 3. Biological markers
    bio_markers = _detect_biological_markers(turn_text)
    for marker_type, raw in bio_markers:
        signals.append({
            "source_turn": turn_id,
            "type": "biological_marker",
            "signals": [f"{marker_type}: {raw}"],
            "confidence": 0.7,
            "raw_text": raw,
        })

    # 4. Check events for construction/birth types
    if events:
        for evt in events:
            evt_turns = evt.get("source_turns", [])
            if turn_id not in evt_turns:
                continue
            evt_type = evt.get("type", "")
            if evt_type == "construction":
                signals.append({
                    "source_turn": turn_id,
                    "type": "construction_milestone",
                    "signals": [f"construction event: {evt.get('id', '')}"],
                    "description": evt.get("description", "")[:120],
                    "confidence": 0.7,
                })
            elif evt_type == "birth":
                signals.append({
                    "source_turn": turn_id,
                    "type": "biological_marker",
                    "signals": [f"birth event: {evt.get('id', '')}"],
                    "description": evt.get("description", "")[:120],
                    "confidence": 0.9,
                })

    return signals


# ---------------------------------------------------------------------------
# Day estimation
# ---------------------------------------------------------------------------

# Default anchor: turn-001 = Day 0
DEFAULT_ANCHOR = {"turn": "turn-001", "label": "Day 0", "day": 0}


def _parse_turn_number(turn_id: str | None) -> int | None:
    """Extract numeric turn number from 'turn-NNN' format."""
    if not turn_id or not isinstance(turn_id, str):
        return None
    m = re.match(r"^turn-0*(\d+)$", turn_id)
    return int(m.group(1)) if m else None


def estimate_day_from_anchor(turn_id: str, anchor: dict | None = None,
                             days_per_turn: float = 3.5) -> dict:
    """Estimate an in-game day offset from a reference anchor.

    Args:
        turn_id: The turn to estimate for.
        anchor: Dict with 'turn', 'label', 'day' keys. Defaults to turn-001 = Day 0.
        days_per_turn: Average days per turn (estimated from calibration data).

    Returns:
        Dict with 'estimated_day', 'anchor_ref', and 'confidence'.
    """
    if anchor is None:
        anchor = DEFAULT_ANCHOR

    turn_num = _parse_turn_number(turn_id)
    anchor_num = _parse_turn_number(anchor["turn"])
    if turn_num is None or anchor_num is None:
        return {"estimated_day": 0, "anchor_ref": anchor.get("label", "Day 0"),
                "confidence": 0.0}

    delta_turns = turn_num - anchor_num
    estimated_day = anchor.get("day", 0) + round(delta_turns * days_per_turn)

    # Confidence decreases with distance from anchor
    distance = abs(delta_turns)
    if distance <= 10:
        confidence = 0.7
    elif distance <= 50:
        confidence = 0.5
    elif distance <= 150:
        confidence = 0.3
    else:
        confidence = 0.2

    return {
        "estimated_day": estimated_day,
        "anchor_ref": anchor.get("label", "Day 0"),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Timeline catalog management
# ---------------------------------------------------------------------------

def load_timeline(catalog_dir: str) -> list[dict]:
    """Load timeline entries from catalog directory."""
    path = os.path.join(catalog_dir, "timeline.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_timeline(catalog_dir: str, timeline: list[dict]) -> None:
    """Save timeline entries to catalog directory."""
    path = os.path.join(catalog_dir, "timeline.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_next_timeline_id(timeline: list[dict]) -> int:
    """Get the next sequential timeline entry ID number."""
    max_id = 0
    for entry in timeline:
        eid = entry.get("id", "")
        m = re.match(r"^time-0*(\d+)$", eid)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return max_id + 1


def _dedup_key(entry: dict) -> tuple:
    """Build a dedup key from a timeline entry or signal.

    Uses ``(source_turn, type, season, raw_text)`` so distinct signals
    of the same type within a single turn (e.g., multiple biological
    markers) are preserved.
    """
    return (
        entry.get("source_turn"),
        entry.get("type"),
        entry.get("season"),
        entry.get("raw_text"),
    )


def merge_temporal_signals(timeline: list[dict], signals: list[dict],
                           next_id: int | None = None) -> list[dict]:
    """Merge new temporal signals into the timeline, avoiding duplicates.

    Signals sharing the same ``(source_turn, type, season, raw_text)``
    are considered duplicates.  Returns the updated timeline.
    """
    if next_id is None:
        next_id = get_next_timeline_id(timeline)

    existing = {_dedup_key(e) for e in timeline}

    for signal in signals:
        key = _dedup_key(signal)
        if key in existing:
            continue
        signal["id"] = f"time-{next_id:03d}"
        next_id += 1
        timeline.append(signal)
        existing.add(key)

    return timeline


# ---------------------------------------------------------------------------
# Season summary helpers
# ---------------------------------------------------------------------------

SEASON_ORDER = [
    "early_winter", "mid_winter", "late_winter",
    "early_spring", "mid_spring", "late_spring",
    "early_summer", "mid_summer", "late_summer",
    "early_autumn", "mid_autumn", "late_autumn",
]


def format_season_label(season: str) -> str:
    """Format a season enum value as a human-readable label."""
    return season.replace("_", " ").title()


def get_season_at_turn(timeline: list[dict], turn_id: str) -> str | None:
    """Get the most recent season label at or before the given turn.

    Looks backward through timeline entries to find the latest season marker.
    """
    turn_num = _parse_turn_number(turn_id)
    if turn_num is None:
        return None

    best_season = None
    best_turn = -1
    for entry in timeline:
        if entry.get("type") != "season_transition":
            continue
        entry_turn = _parse_turn_number(entry.get("source_turn"))
        if entry_turn is None or entry_turn > turn_num:
            continue
        if entry_turn > best_turn:
            best_turn = entry_turn
            best_season = entry.get("season")

    return best_season


def get_current_timeline_summary(timeline: list[dict],
                                 anchor: dict | None = None,
                                 latest_turn: str | None = None) -> dict:
    """Build a summary of the current timeline state.

    Returns a dict with estimated_day, season, anchor info, turn_span.
    """
    if anchor is None:
        anchor = DEFAULT_ANCHOR

    if not timeline:
        return {
            "estimated_day": 0,
            "season": None,
            "anchor_label": anchor.get("label", "Day 0"),
            "anchor_turn": anchor.get("turn", "turn-001"),
            "turn_span": 0,
        }

    # Find the latest turn in the timeline
    max_turn_num = 0
    for entry in timeline:
        t = _parse_turn_number(entry.get("source_turn"))
        if t and t > max_turn_num:
            max_turn_num = t

    if latest_turn:
        t = _parse_turn_number(latest_turn)
        if t and t > max_turn_num:
            max_turn_num = t

    latest_turn_id = f"turn-{max_turn_num:03d}" if max_turn_num > 0 else "turn-001"

    day_info = estimate_day_from_anchor(latest_turn_id, anchor)
    season = get_season_at_turn(timeline, latest_turn_id)
    anchor_num = _parse_turn_number(anchor.get("turn")) or 1

    return {
        "estimated_day": day_info["estimated_day"],
        "season": season,
        "season_label": format_season_label(season) if season else None,
        "anchor_label": anchor.get("label", "Day 0"),
        "anchor_turn": anchor.get("turn", "turn-001"),
        "turn_span": max_turn_num - anchor_num,
        "confidence": day_info["confidence"],
    }


# ---------------------------------------------------------------------------
# Season flicker filtering
# ---------------------------------------------------------------------------

def _base_season(fine_season: str) -> str:
    """Extract the base season name from a fine-grained label."""
    for base in ("winter", "spring", "summer", "autumn"):
        if base in fine_season:
            return base
    return fine_season


def filter_season_flicker(timeline: list[dict],
                          min_confidence: float = 0.6,
                          min_support: int = 1,
                          window_size: int = 5) -> list[dict]:
    """Filter out season transition noise (flicker) from a timeline.

    A season transition is kept only if:
    - Its confidence >= min_confidence, OR
    - Within a sliding window of up to ``window_size`` entries on each side
      (total window up to ``2 * window_size`` neighbors, excluding the
      current entry), at least ``min_support`` neighbors share the same
      base season — confirming it's not an isolated blip.

    A single outlier season sandwiched between many entries of a different
    season is discarded even if its base season has high total count.

    Non-season entries are always preserved.

    Args:
        timeline: Full timeline entry list.
        min_confidence: Minimum confidence to auto-accept a season signal.
        min_support: Minimum number of neighbors (excluding the current
            entry) within the window that share the same base season.
        window_size: Per-side radius — how many season entries to inspect
            on each side of the current entry (total window is up to
            ``2 * window_size`` neighbors).

    Returns:
        Filtered timeline list (new list; original is not modified).
    """
    # Separate season transitions from other entries
    non_season = [e for e in timeline if e.get("type") != "season_transition"]
    season_entries = [e for e in timeline if e.get("type") == "season_transition"]

    if not season_entries:
        return list(timeline)

    # Sort season entries by turn number
    season_entries.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)

    # Mark which entries to keep
    kept: list[dict] = []

    for i, entry in enumerate(season_entries):
        conf = entry.get("confidence", 0.0)
        if conf >= min_confidence:
            kept.append(entry)
            continue

        # Sliding window: check neighbors for same base season
        base = _base_season(entry.get("season", ""))
        window_start = max(0, i - window_size)
        window_end = min(len(season_entries), i + window_size + 1)
        neighbor_support = sum(
            1 for j in range(window_start, window_end)
            if j != i and _base_season(season_entries[j].get("season", "")) == base
        )
        if neighbor_support >= min_support:
            kept.append(entry)

    # Combine and sort by turn number
    result = non_season + kept
    result.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
    return result


# ---------------------------------------------------------------------------
# Anchor event detection
# ---------------------------------------------------------------------------

def detect_anchor_event(timeline: list[dict]) -> dict:
    """Detect the most significant anchor event from timeline data.

    Chooses the first anchor_event entry if present, otherwise falls back
    to the first time_skip or biological_marker. Returns DEFAULT_ANCHOR
    if no significant events are found.

    Returns an anchor dict with 'turn', 'label', 'day' keys.
    """
    if not timeline:
        return DEFAULT_ANCHOR

    # Prefer explicit anchor_event entries
    anchors = [e for e in timeline if e.get("type") == "anchor_event"]
    if anchors:
        anchors.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
        first = anchors[0]
        return {
            "turn": first["source_turn"],
            "label": first.get("description", "Anchor event"),
            "day": first.get("estimated_day", 0),
        }

    # Fall back to first significant event (time_skip or biological_marker)
    significant = [e for e in timeline
                   if e.get("type") in ("time_skip", "biological_marker")]
    if significant:
        significant.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
        first = significant[0]
        # Build a human-readable label from the entry
        label = first.get("description")
        if not label:
            raw = first.get("raw_text", "")
            if raw and len(raw) <= 80:
                label = raw.strip().capitalize()
            else:
                # Derive from type without duplicating the signal prefix
                etype = first.get("type", "event")
                label = f"First {etype.replace('_', ' ')}"
        return {
            "turn": first["source_turn"],
            "label": label,
            "day": first.get("estimated_day", 0),
        }

    return DEFAULT_ANCHOR


# ---------------------------------------------------------------------------
# Narrative timeline summary
# ---------------------------------------------------------------------------

def generate_narrative_timeline(timeline: list[dict],
                                anchor: dict | None = None,
                                latest_turn: str | None = None,
                                *,
                                entities: list[dict] | None = None,
                                events: list[dict] | None = None,
                                relationships: dict | None = None) -> str:
    """Generate a natural-language narrative summary of the temporal arc.

    When catalog data (entities/events) is provided, produces a structured
    markdown summary with story progression beats grouped by period.
    Without catalog data, produces a concise 3-sentence fallback.

    This is a template-based generator (no LLM required).

    Args:
        timeline: Full timeline entry list.
        anchor: Anchor event dict. Auto-detected if None.
        latest_turn: Current latest turn ID.
        entities: Entity dicts from catalogs (optional).
        events: Event dicts from events.json (optional).
        relationships: Relationship index dict (optional).

    Returns:
        Narrative summary as a markdown string.
    """
    if not timeline:
        return "*No temporal data available yet.*"

    # Filter flicker
    filtered = filter_season_flicker(timeline)

    if anchor is None:
        anchor = detect_anchor_event(filtered)

    # Get current state
    summary = get_current_timeline_summary(filtered, anchor, latest_turn)

    # Group timeline events by type
    season_transitions = []
    time_skips = []
    bio_markers = []

    for entry in filtered:
        etype = entry.get("type", "")
        if etype == "season_transition":
            season_transitions.append(entry)
        elif etype == "time_skip":
            time_skips.append(entry)
        elif etype == "biological_marker":
            bio_markers.append(entry)

    # If catalog data available, produce rich structured output
    if events or entities:
        return _build_rich_narrative(
            summary, anchor, season_transitions, time_skips, bio_markers,
            entities=entities, events=events, relationships=relationships,
        )

    # Fallback: concise summary without catalog data
    return _build_fallback_narrative(summary, anchor, season_transitions, time_skips, bio_markers)


def _build_fallback_narrative(summary: dict, anchor: dict,
                              season_transitions: list[dict],
                              time_skips: list[dict],
                              bio_markers: list[dict]) -> str:
    """Produce a concise 3-5 sentence narrative without catalog data."""
    sentences = []

    # Elapsed time
    anchor_label = anchor.get("label", "the beginning")
    est_day = summary.get("estimated_day", 0)
    if est_day > 0:
        sentences.append(
            f"As of the current turn, {_format_elapsed(est_day)} have elapsed "
            f"since {anchor_label}."
        )

    # Season arc (first → current only)
    if season_transitions:
        first_label = format_season_label(season_transitions[0].get("season", ""))
        last_label = format_season_label(season_transitions[-1].get("season", ""))
        if first_label != last_label:
            sentences.append(
                f"The story began in {first_label} and has progressed to {last_label}."
            )
        else:
            sentences.append(f"The story has remained in {first_label} throughout.")

    # Major time passages count
    if time_skips:
        sentences.append(
            f"There have been {len(time_skips)} notable time passage"
            f"{'s' if len(time_skips) != 1 else ''}."
        )

    # Confidence note
    conf = summary.get("confidence", 0.0)
    if conf < 0.4:
        sentences.append(
            "*Note: Temporal estimates have low confidence due to limited anchor data.*"
        )

    return " ".join(sentences) if sentences else "*No temporal narrative available.*"


def _build_rich_narrative(summary: dict, anchor: dict,
                          season_transitions: list[dict],
                          time_skips: list[dict],
                          bio_markers: list[dict],
                          *,
                          entities: list[dict] | None = None,
                          events: list[dict] | None = None,
                          relationships: dict | None = None) -> str:
    """Produce a structured narrative using catalog data."""
    lines = []

    # --- Temporal Arc ---
    anchor_label = anchor.get("label", "the beginning")
    est_day = summary.get("estimated_day", 0)
    season_label = summary.get("season_label") or "unknown"

    arc_parts = []
    if est_day > 0:
        arc_parts.append(f"{_format_elapsed(est_day)} have elapsed since {anchor_label}")
    if season_label and season_label != "unknown":
        arc_parts.append(f"currently in {season_label}")
    if arc_parts:
        lines.append(f"**Temporal Arc**: {'; '.join(arc_parts)}.")
    else:
        lines.append("**Temporal Arc**: Timeline position uncertain.")
    lines.append("")

    # --- Story Progression (from events) ---
    if events:
        story_section = _build_story_progression(events, season_transitions)
        if story_section:
            lines.append("**Story Progression**:")
            lines.extend(story_section)
            lines.append("")

    # --- Lifecycle Events (grouped bio markers) ---
    if bio_markers:
        lifecycle = _build_lifecycle_summary(bio_markers, entities)
        if lifecycle:
            lines.append(f"**Lifecycle Events**: {lifecycle}")
            lines.append("")

    # --- Time Passages (brief summary of major skips only) ---
    if time_skips:
        passages = _build_time_passages_summary(time_skips)
        if passages:
            lines.append(f"**Time Passages**: {passages}")
            lines.append("")

    # Confidence note
    conf = summary.get("confidence", 0.0)
    if conf < 0.4:
        lines.append(
            "*Note: Temporal estimates have low confidence due to limited anchor data.*"
        )

    return "\n".join(lines).strip() if lines else "*No temporal narrative available.*"


def _format_elapsed(est_day: int) -> str:
    """Format estimated days as a human-readable phrase."""
    if est_day < 14:
        return f"approximately {est_day} days"
    elif est_day < 60:
        weeks = round(est_day / 7)
        return f"approximately {weeks} week{'s' if weeks != 1 else ''}"
    elif est_day < 365:
        months = round(est_day / 30)
        return f"approximately {months} month{'s' if months != 1 else ''}"
    else:
        years = round(est_day / 365, 1)
        return f"approximately {years} year{'s' if years != 1 else ''}"


# Priority event types for story progression.
# These values match the valid `type` enums in schemas/event.schema.json.
_PRIORITY_EVENT_TYPES = {
    "decision",
    "discovery",
    "encounter",
    "capture",
    "birth",
    "death",
    "arrival",
    "departure",
    "construction",
}


def _build_story_progression(events: list[dict],
                             season_transitions: list[dict]) -> list[str]:
    """Group events into temporal periods and summarize."""
    if not events:
        return []

    # Build period labels from season transitions
    period_labels = _build_period_labels(season_transitions)

    # Assign events to periods (25-turn buckets)
    periods: dict[int, list[dict]] = {}
    for evt in events:
        turns = evt.get("source_turns", [])
        if not turns:
            continue
        parsed_turns = []
        for turn in turns:
            parsed = _parse_turn_number(turn)
            if parsed is not None:
                parsed_turns.append(parsed)
        if not parsed_turns:
            continue
        turn_num = min(parsed_turns)
        if turn_num < 1:
            continue
        bucket = (turn_num - 1) // 25  # 0-indexed bucket
        periods.setdefault(bucket, []).append(evt)

    if not periods:
        return []

    lines = []
    for bucket in sorted(periods.keys()):
        bucket_events = periods[bucket]
        start_turn = bucket * 25 + 1
        end_turn = start_turn + 24

        # Pick representative events (prioritize key types)
        priority = [e for e in bucket_events if e.get("type") in _PRIORITY_EVENT_TYPES]
        if priority:
            selected = priority[:3]
        else:
            selected = bucket_events[:2]

        # Build period label
        label = period_labels.get(bucket, f"Turns {start_turn}\u2013{end_turn}")

        # Build summary from selected events
        descs = []
        for evt in selected:
            desc = evt.get("description", "").strip()
            if desc:
                # Truncate long descriptions
                if len(desc) > 120:
                    desc = desc[:117] + "..."
                descs.append(desc)

        if descs:
            summary_text = " ".join(descs)
            lines.append(f"- **{label}**: {summary_text}")

    return lines


def _build_period_labels(season_transitions: list[dict]) -> dict[int, str]:
    """Map 25-turn buckets to season-based labels."""
    labels: dict[int, str] = {}
    for entry in season_transitions:
        turn_num = _parse_turn_number(entry.get("source_turn"))
        if turn_num is None:
            continue
        bucket = (turn_num - 1) // 25
        season = format_season_label(entry.get("season", ""))
        turn_range = f"turns {bucket * 25 + 1}\u2013{bucket * 25 + 25}"
        if bucket not in labels:
            labels[bucket] = f"{season} ({turn_range})"
    return labels


def _build_lifecycle_summary(bio_markers: list[dict],
                             entities: list[dict] | None = None) -> str:
    """Group bio markers into pregnancy-birth cycles and summarize."""
    if not bio_markers:
        return ""

    # Sort by turn
    sorted_markers = sorted(
        bio_markers,
        key=lambda e: _parse_turn_number(e.get("source_turn")) or 0
    )

    # Detect cycles: a cycle starts with pregnancy/pregnancy_progression
    # and ends with birth/born. Cluster entries within ~20 turns.
    cycles: list[dict] = []
    current_cycle: dict | None = None

    for marker in sorted_markers:
        signals = marker.get("signals", [])
        turn_num = _parse_turn_number(marker.get("source_turn")) or 0
        signal_text = signals[0] if signals else ""
        marker_type = signal_text.split(":")[0].strip() if ":" in signal_text else ""

        if marker_type in ("pregnancy", "pregnancy_progression"):
            if current_cycle is None or (turn_num - current_cycle["end_turn"] > 20):
                # Start new cycle
                if current_cycle:
                    cycles.append(current_cycle)
                current_cycle = {
                    "start_turn": turn_num,
                    "end_turn": turn_num,
                    "has_birth": False,
                    "count": 1,
                }
            else:
                current_cycle["end_turn"] = turn_num
                current_cycle["count"] += 1
        elif marker_type in ("birth", "born"):
            if current_cycle and (turn_num - current_cycle["end_turn"] <= 20):
                current_cycle["end_turn"] = turn_num
                current_cycle["has_birth"] = True
                current_cycle["count"] += 1
            else:
                # Orphaned birth — standalone cycle
                cycles.append({
                    "start_turn": turn_num,
                    "end_turn": turn_num,
                    "has_birth": True,
                    "count": 1,
                })
        else:
            # Other bio marker — attach to current cycle or ignore
            if current_cycle and (turn_num - current_cycle["end_turn"] <= 20):
                current_cycle["end_turn"] = turn_num
                current_cycle["count"] += 1

    if current_cycle:
        cycles.append(current_cycle)

    if not cycles:
        return f"{len(sorted_markers)} biological markers recorded."

    births = sum(1 for c in cycles if c["has_birth"])
    if births and len(cycles) == 1:
        c = cycles[0]
        return (
            f"A pregnancy cycle spanned turns {c['start_turn']}\u2013{c['end_turn']}, "
            f"culminating in birth."
        )
    elif births:
        turn_range_start = min(c["start_turn"] for c in cycles)
        turn_range_end = max(c["end_turn"] for c in cycles)
        return (
            f"{births} birth{'s' if births != 1 else ''} occurred across "
            f"turns {turn_range_start}\u2013{turn_range_end}."
        )
    else:
        total = sum(c["count"] for c in cycles)
        return f"{total} lifecycle markers recorded across {len(cycles)} period{'s' if len(cycles) != 1 else ''}."


def _build_time_passages_summary(time_skips: list[dict]) -> str:
    """Summarize time passages briefly — only major skips get detail."""
    if not time_skips:
        return ""

    if len(time_skips) <= 3:
        descs = []
        for skip in time_skips:
            signals = skip.get("signals", [])
            if signals:
                desc = signals[0].split(": ", 1)[-1] if ": " in signals[0] else signals[0]
                turn = skip.get("source_turn", "")
                descs.append(f"{desc} ({turn})")
        return "; ".join(descs) + "." if descs else ""
    else:
        return (
            f"{len(time_skips)} time passages noted, including early transitions at "
            f"{time_skips[0].get('source_turn', '?')} through "
            f"{time_skips[-1].get('source_turn', '?')}."
        )


# ---------------------------------------------------------------------------
# Timeline wiki page generation
# ---------------------------------------------------------------------------

def generate_timeline_wiki_page(timeline: list[dict],
                                anchor: dict | None = None,
                                latest_turn: str | None = None,
                                *,
                                entities: list[dict] | None = None,
                                events: list[dict] | None = None,
                                relationships: dict | None = None) -> str:
    """Generate a full timeline wiki page with narrative summary and data tables.

    The page structure:
    1. Anchor date / current position header
    2. Narrative temporal summary (structured with catalog data)
    3. Season progression table (filtered for quality)
    4. Time skip table
    5. Biological markers table

    Args:
        timeline: Full timeline entry list.
        anchor: Anchor event dict. Auto-detected if None.
        latest_turn: Current latest turn ID.
        entities: Entity dicts from catalogs (optional).
        events: Event dicts from events.json (optional).
        relationships: Relationship index dict (optional).

    Returns:
        Complete markdown page content.
    """
    if anchor is None:
        anchor = detect_anchor_event(timeline)

    filtered = filter_season_flicker(timeline)
    summary = get_current_timeline_summary(filtered, anchor, latest_turn)

    lines = []
    lines.append("# Timeline\n")

    # --- Anchor / current position ---
    est_day = summary.get("estimated_day", 0)
    season_label = summary.get("season_label") or "Unknown"
    anchor_label = anchor.get("label") or "Day 0"
    anchor_turn = anchor.get("turn", "turn-001")

    lines.append("## Current Position\n")
    lines.append(f"| | |")
    lines.append(f"|---|---|")
    lines.append(f"| **Current Season** | {season_label} |")
    lines.append(f"| **Estimated Day** | Day {est_day} |")
    lines.append(f"| **Anchor Event** | {anchor_label} ({anchor_turn}) |")
    lines.append(f"| **Turn Span** | {summary.get('turn_span', 0)} turns |")
    conf = summary.get("confidence", 0.0)
    lines.append(f"| **Confidence** | {conf:.0%} |")
    lines.append("")

    # --- Narrative summary ---
    lines.append("## Narrative Summary\n")
    narrative = generate_narrative_timeline(timeline, anchor, latest_turn,
                                           entities=entities, events=events,
                                           relationships=relationships)
    lines.append(narrative)
    lines.append("")

    # --- Season progression table ---
    season_entries = [e for e in filtered if e.get("type") == "season_transition"]
    if season_entries:
        season_entries.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
        lines.append("## Season Progression\n")
        lines.append("| Turn | Season | Confidence | Signals |")
        lines.append("|---|---|---|---|")
        for entry in season_entries:
            turn = entry.get("source_turn", "")
            season = format_season_label(entry.get("season", ""))
            conf = entry.get("confidence", 0.0)
            signals = ", ".join(entry.get("signals", []))
            lines.append(f"| {turn} | {season} | {conf:.0%} | {signals} |")
        lines.append("")

    # --- Time skips table ---
    skip_entries = [e for e in filtered if e.get("type") == "time_skip"]
    if skip_entries:
        skip_entries.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
        lines.append("## Time Passages\n")
        lines.append("| Turn | Description | Confidence |")
        lines.append("|---|---|---|")
        for entry in skip_entries:
            turn = entry.get("source_turn", "")
            signals = entry.get("signals", [])
            desc = signals[0] if signals else entry.get("raw_text", "")
            conf = entry.get("confidence", 0.0)
            lines.append(f"| {turn} | {desc} | {conf:.0%} |")
        lines.append("")

    # --- Biological markers table ---
    bio_entries = [e for e in filtered if e.get("type") == "biological_marker"]
    if bio_entries:
        bio_entries.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
        lines.append("## Biological & Lifecycle Markers\n")
        lines.append("| Turn | Marker | Confidence |")
        lines.append("|---|---|---|")
        for entry in bio_entries:
            turn = entry.get("source_turn", "")
            signals = entry.get("signals", [])
            desc = signals[0] if signals else entry.get("raw_text", "")
            conf = entry.get("confidence", 0.0)
            lines.append(f"| {turn} | {desc} | {conf:.0%} |")
        lines.append("")

    # --- Other events table ---
    other_entries = [e for e in filtered
                     if e.get("type") in ("construction_milestone", "anchor_event")]
    if other_entries:
        other_entries.sort(key=lambda e: _parse_turn_number(e.get("source_turn")) or 0)
        lines.append("## Other Milestones\n")
        lines.append("| Turn | Type | Description | Confidence |")
        lines.append("|---|---|---|---|")
        for entry in other_entries:
            turn = entry.get("source_turn", "")
            etype = entry.get("type", "").replace("_", " ").title()
            desc = entry.get("description", "")
            conf = entry.get("confidence", 0.0)
            lines.append(f"| {turn} | {etype} | {desc} | {conf:.0%} |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append("*Generated from timeline catalog data — do not edit manually.*")
    return "\n".join(lines) + "\n"
