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
    (r"\bbelly.*swell\w*\b", "pregnancy_progression"),
    (r"\bmorning sickness\b", "pregnancy_early"),
    (r"\blife.*taken root\b", "pregnancy_discovery"),
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

def _detect_base_season(text: str) -> str | None:
    """Detect the dominant base season from text using keyword counts."""
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for season, patterns in SEASON_PATTERNS.items():
        count = 0
        for pattern in patterns:
            count += len(re.findall(pattern, text_lower))
        if count > 0:
            scores[season] = count
    if not scores:
        return None
    return max(scores, key=scores.get)


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
            found.append((marker_type, match.group()))
    return found


def _detect_time_skips(text: str) -> list[tuple[str, str]]:
    """Detect explicit time-skip language."""
    text_lower = text.lower()
    found = []
    for pattern, skip_type in TIME_SKIP_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            found.append((skip_type, match.group()))
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
