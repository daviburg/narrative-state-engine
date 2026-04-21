#!/usr/bin/env python3
"""
extract_structured_data.py — Extract structured data from session turns.

Handles three types of extraction:
  1. Inline game markers (#21): HP changes, long rests, spell usage, item acquisitions
  2. Temporal markers (#27): Season transitions, time progression
  3. Season summary blocks (#28): Structured season summary data from DM turns
  4. Structured mechanical state (#86): HP, inventory, status effects for player_state

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


_SEASON_PREFIX_MAP = {
    "spring": "mid_spring",
    "summer": "mid_summer",
    "fall": "mid_autumn",
    "autumn": "mid_autumn",
    "winter": "mid_winter",
}


def _normalize_timeline_season(name: str) -> str:
    """Normalize season name to a timeline-schema-compliant enum value.

    Unprefixed names get a ``mid_`` prefix; already-prefixed names are
    passed through (with ``fall`` → ``autumn`` correction).
    """
    name = name.strip().lower()
    for prefix in ("early_", "mid_", "late_"):
        if name.startswith(prefix):
            base = name[len(prefix):]
            if base == "fall":
                return f"{prefix}autumn"
            return name
    return _SEASON_PREFIX_MAP.get(name, name)


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
            season = _normalize_timeline_season(m.group(1))
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
# Structured mechanical state extraction (#86)
# ---------------------------------------------------------------------------

# HP patterns
_HP_NUMERIC_RE = re.compile(
    r"HP\s*[:=]\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)
_HP_TAKES_DAMAGE_RE = re.compile(
    r"takes?\s+(\d+)\s+(?:points?\s+(?:of\s+)?)?damage",
    re.IGNORECASE,
)
_HP_HEALS_RE = re.compile(
    r"heals?\s+(\d+)\s*(?:HP|hit\s+points?)?",
    re.IGNORECASE,
)
_HP_LOSES_RE = re.compile(
    r"loses?\s+(\d+)\s*(?:HP|hit\s+points?)",
    re.IGNORECASE,
)
_HP_RESTORES_RE = re.compile(
    r"restores?\s+(\d+)\s*(?:HP|hit\s+points?)",
    re.IGNORECASE,
)
_HP_NARRATIVE_PATTERNS = [
    re.compile(r"\b(slight(?:ly)?\s+wounded)\b", re.IGNORECASE),
    re.compile(r"\b(badly\s+wounded|grievously\s+wounded)\b", re.IGNORECASE),
    re.compile(r"\b(barely\s+conscious|near\s+death)\b", re.IGNORECASE),
    re.compile(r"\b(unhurt|uninjured|healthy|fully?\s+healed?)\b", re.IGNORECASE),
    re.compile(r"\b(wounded|injured|hurt)\b", re.IGNORECASE),
]

# Status effect patterns
_STATUS_EFFECT_PATTERNS = [
    re.compile(r"\b(poisoned)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(exhausted|fatigued)\b(?:\s+from\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(stunned)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(frightened|afraid)\b(?:\s+(?:of|by)\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(charmed)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(blinded)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(paralyzed|paralysed)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(inspired)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(blessed)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(cursed)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
    re.compile(r"\b(invisible)\b", re.IGNORECASE),
    re.compile(r"\b(prone)\b", re.IGNORECASE),
    re.compile(r"\b(restrained)\b(?:\s+by\s+(.+?)(?:\.|,|$))?", re.IGNORECASE),
]

# Inventory patterns
_ITEM_ACQUIRED_RE = re.compile(
    r"(?:picks?\s+up|receives?|obtains?|finds?|gains?|acquires?|takes?|is\s+given)\s+(?:a\s+|an\s+|the\s+)?(.+?)(?:\.|,|$)",
    re.IGNORECASE | re.MULTILINE,
)
_ITEM_MARKER_RE = re.compile(
    r"New\s+item\s+acquired:\s*(.+?)$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_hp(text: str, turn_id: str) -> dict | None:
    """Extract structured HP information from turn text.

    Returns an hp dict matching state.schema.json player_state.hp, or None.
    """
    hp: dict = {}

    # Try numeric HP first (e.g., "HP: 15/20")
    m = _HP_NUMERIC_RE.search(text)
    if m:
        hp["numeric"] = int(m.group(1))
        hp["max_hp"] = int(m.group(2))
    else:
        hp["numeric"] = None
        hp["max_hp"] = None

    # Check for damage/healing to build last_change
    last_change = None
    damage_m = _HP_TAKES_DAMAGE_RE.search(text)
    heal_m = _HP_HEALS_RE.search(text)
    lose_m = _HP_LOSES_RE.search(text)
    restore_m = _HP_RESTORES_RE.search(text)

    if damage_m:
        # Try to extract source from surrounding context
        source = _extract_change_source(text, damage_m.start())
        last_change = {
            "delta": f"-{damage_m.group(1)}",
            "source": source,
            "turn": turn_id,
        }
    elif lose_m:
        source = _extract_change_source(text, lose_m.start())
        last_change = {
            "delta": f"-{lose_m.group(1)}",
            "source": source,
            "turn": turn_id,
        }
    elif heal_m:
        source = _extract_change_source(text, heal_m.start())
        last_change = {
            "delta": f"+{heal_m.group(1)}",
            "source": source,
            "turn": turn_id,
        }
    elif restore_m:
        source = _extract_change_source(text, restore_m.start())
        last_change = {
            "delta": f"+{restore_m.group(1)}",
            "source": source,
            "turn": turn_id,
        }

    # Also check marker-based HP changes
    for marker_m in MARKER_PATTERNS["hp_loss"].finditer(text):
        last_change = {
            "delta": f"-{marker_m.group(1)}",
            "source": "marker",
            "turn": turn_id,
        }
    for marker_m in MARKER_PATTERNS["hp_restore"].finditer(text):
        last_change = {
            "delta": f"+{marker_m.group(1)}",
            "source": "marker",
            "turn": turn_id,
        }

    if last_change:
        hp["last_change"] = last_change

    # Narrative HP description
    narrative = None
    for pattern in _HP_NARRATIVE_PATTERNS:
        nm = pattern.search(text)
        if nm:
            narrative = nm.group(1).strip().lower()
            break

    if narrative:
        hp["narrative"] = narrative
    elif hp.get("numeric") is not None:
        hp["narrative"] = f"HP {hp['numeric']}/{hp['max_hp']}"

    # Only return if we found something meaningful
    if hp.get("narrative") or hp.get("numeric") is not None or last_change:
        if "narrative" not in hp:
            hp["narrative"] = "unknown"
        return hp
    return None


def _extract_change_source(text: str, match_pos: int) -> str:
    """Try to extract the source/cause from nearby text around an HP change."""
    # Look at the sentence containing the match
    start = text.rfind(".", 0, match_pos)
    start = start + 1 if start >= 0 else 0
    end = text.find(".", match_pos)
    end = end if end >= 0 else len(text)
    sentence = text[start:end].strip()

    # Look for "from X", "by X" patterns
    source_m = re.search(r"\b(?:from|by)\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\.|,|$)", sentence, re.IGNORECASE)
    if source_m:
        return source_m.group(1).strip()[:50]
    return "unknown"


def extract_inventory_changes(
    text: str,
    items_catalog: list[dict] | None = None,
) -> list[dict]:
    """Extract inventory changes from turn text.

    Returns a list of inventory entry dicts matching state.schema.json player_state.inventory.
    """
    items: list[dict] = []
    seen_names: set[str] = set()

    # Build catalog name lookup
    catalog_lookup: dict[str, str] = {}
    if items_catalog:
        for item in items_catalog:
            name = item.get("name", "").lower()
            catalog_lookup[name] = item.get("id", "")

    # Check explicit markers first
    for m in _ITEM_MARKER_RE.finditer(text):
        name = m.group(1).strip()
        name_lower = name.lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)
        item_id = catalog_lookup.get(name_lower)
        items.append({
            "item_id": item_id,
            "name": name,
            "carried": True,
            "quantity": 1,
            "notes": None,
        })

    # Check narrative acquisition patterns
    for m in _ITEM_ACQUIRED_RE.finditer(text):
        name = m.group(1).strip()
        # Filter out overly long matches (likely not item names)
        if len(name.split()) > 5:
            continue
        name_lower = name.lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)
        item_id = catalog_lookup.get(name_lower)
        items.append({
            "item_id": item_id,
            "name": name,
            "carried": True,
            "quantity": 1,
            "notes": None,
        })

    return items


def extract_status_effects(text: str, turn_id: str) -> list[dict]:
    """Extract status effects from turn text.

    Returns a list of status effect dicts matching state.schema.json player_state.status_effects.
    """
    effects: list[dict] = []
    seen: set[str] = set()

    for pattern in _STATUS_EFFECT_PATTERNS:
        for m in pattern.finditer(text):
            effect = m.group(1).strip().lower()
            if effect in seen:
                continue
            seen.add(effect)
            entry: dict = {"effect": effect}
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                entry["source"] = m.group(2).strip()
            entry["since_turn"] = turn_id
            effects.append(entry)

    return effects


def extract_mechanical_state(
    text: str,
    turn_id: str,
    items_catalog: list[dict] | None = None,
) -> dict:
    """Extract all structured mechanical state fields from turn text.

    Returns a dict with keys hp, inventory, status_effects — only populated
    fields are included. Caller should merge into existing player_state.
    """
    result: dict = {}

    hp = extract_hp(text, turn_id)
    if hp is not None:
        result["hp"] = hp

    inventory = extract_inventory_changes(text, items_catalog)
    if inventory:
        result["inventory"] = inventory

    effects = extract_status_effects(text, turn_id)
    if effects:
        result["status_effects"] = effects

    return result


def merge_mechanical_state(
    existing_state: dict,
    new_mechanical: dict,
    turn_id: str,
) -> dict:
    """Merge newly extracted mechanical state into existing player_state.

    - HP: replaces existing hp entirely (latest turn wins)
    - Inventory: merges by name (updates existing, adds new)
    - Status effects: merges by effect name (adds new, updates since_turn)

    Returns updated player_state dict. Does not mutate *existing_state*.
    """
    player_state = dict(existing_state)

    if "hp" in new_mechanical:
        player_state["hp"] = new_mechanical["hp"]

    if "inventory" in new_mechanical:
        # Defensive copy so the original list/dicts are not mutated
        existing_inv: list[dict] = [dict(item) for item in player_state.get("inventory", [])]
        existing_by_name = {item["name"].lower(): item for item in existing_inv}
        for new_item in new_mechanical["inventory"]:
            key = new_item["name"].lower()
            if key in existing_by_name:
                existing_by_name[key].update(new_item)
            else:
                existing_inv.append(new_item)
        player_state["inventory"] = existing_inv

    if "status_effects" in new_mechanical:
        # Defensive copy so the original list/dicts are not mutated
        existing_effects: list[dict] = [dict(e) for e in player_state.get("status_effects", [])]
        existing_by_effect = {e["effect"].lower(): e for e in existing_effects}
        for new_effect in new_mechanical["status_effects"]:
            key = new_effect["effect"].lower()
            if key in existing_by_effect:
                # Preserve the original since_turn (earliest occurrence)
                original_since = existing_by_effect[key].get("since_turn")
                existing_by_effect[key].update(new_effect)
                if original_since:
                    existing_by_effect[key]["since_turn"] = original_since
            else:
                existing_effects.append(new_effect)
        player_state["status_effects"] = existing_effects

    return player_state


def update_state_mechanical(
    derived_dir: str,
    text: str,
    turn_id: str,
    items_catalog: list[dict] | None = None,
    dry_run: bool = False,
) -> dict:
    """Extract mechanical state from turn text and merge into state.json.

    Returns the extracted mechanical state dict.
    """
    mechanical = extract_mechanical_state(text, turn_id, items_catalog)
    if not mechanical:
        return {}

    state_file = os.path.join(derived_dir, "state.json")
    if not os.path.exists(state_file):
        return mechanical

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    player_state = state.get("player_state", {})
    updated = merge_mechanical_state(player_state, mechanical, turn_id)

    if dry_run:
        print(f"  [DRY]    would update mechanical state in {state_file}")
    else:
        state["player_state"] = updated
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        fields = list(mechanical.keys())
        print(f"  [UPDATE] {state_file} mechanical state: {fields}")

    return mechanical


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
    items_catalog: list[dict] | None = None,
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

    # Extract and merge mechanical state (#86)
    mechanical = update_state_mechanical(
        derived_dir, text, turn_id, items_catalog=items_catalog, dry_run=dry_run,
    )

    return {
        "session_events": new_events,
        "timeline": new_timeline,
        "season_summaries": new_summaries,
        "mechanical_state": mechanical,
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
