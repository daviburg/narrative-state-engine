#!/usr/bin/env python3
"""
dm_profile_analyzer.py — Populate and update the DM behavioral profile.

Two data sources:
1. Transcript analysis — LLM-based extraction of DM behavioral patterns from
   transcript turns (tone, structure, hints, adversariality, formatting).
2. User-provided documents — Off-game notes, session-zero agreements, house
   rules, and other materials the user supplies.

Usage:
    # Analyze DM turns from transcript (batch)
    python tools/dm_profile_analyzer.py --session sessions/session-001

    # Analyze a range of turns
    python tools/dm_profile_analyzer.py --session sessions/session-001 --start-turn 20 --max-turns 30

    # Ingest user-provided off-game document
    python tools/dm_profile_analyzer.py --user-input path/to/filled-template.md

    # Both: transcript analysis + user input
    python tools/dm_profile_analyzer.py --session sessions/session-001 --user-input notes.md

    # Dry run (don't write changes)
    python tools/dm_profile_analyzer.py --session sessions/session-001 --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

# Allow imports from the tools/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from llm_client import LLMClient, LLMExtractionError, QuotaExhaustedError
except ImportError:
    LLMClient = None
    LLMExtractionError = Exception
    QuotaExhaustedError = Exception

# Paths relative to repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATES_DIR = os.path.join(_REPO_ROOT, "templates", "extraction")
_FRAMEWORK_DIR = os.path.join(_REPO_ROOT, "framework")
_DM_PROFILE_PATH = os.path.join(_FRAMEWORK_DIR, "dm-profile", "dm-profile.json")
_DM_PROFILE_SCHEMA = os.path.join(_REPO_ROOT, "schemas", "dm-profile.schema.json")

_TURN_ID_RE = re.compile(r"^turn-(\d{3,})$")

# Adversarial level ordering for aggregation
_ADVERSARIAL_LEVELS = {"low": 0, "moderate": 1, "high": 2, "unknown": -1}
_ADVERSARIAL_BY_RANK = {v: k for k, v in _ADVERSARIAL_LEVELS.items() if v >= 0}


def load_dm_profile(path: str | None = None) -> dict:
    """Load the current DM profile from disk."""
    path = path or _DM_PROFILE_PATH
    if not os.path.exists(path):
        return _empty_profile()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_dm_profile(profile: dict, path: str | None = None, dry_run: bool = False) -> None:
    """Write the DM profile to disk."""
    path = path or _DM_PROFILE_PATH
    if dry_run:
        print(f"  DRY RUN: would write DM profile to {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")


def _empty_profile() -> dict:
    """Return a minimal empty DM profile."""
    return {
        "last_updated_turn": "turn-001",
        "structure_patterns": [],
        "hint_patterns": [],
        "adversarial_level": "unknown",
        "formatting_preferences": [],
        "notes": "Profile not yet established.",
        "confidence": 0.0,
    }


def load_template() -> str:
    """Load the DM profile analyzer prompt template."""
    filepath = os.path.join(_TEMPLATES_DIR, "dm-profile-analyzer.md")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def _format_analysis_prompt(turns: list[dict], current_profile: dict) -> str:
    """Format the user prompt for DM profile analysis."""
    parts = ["## DM Turns to Analyze\n"]
    for turn in turns:
        parts.append(
            f"### {turn['turn_id']} (speaker: {turn['speaker']})\n"
            f"{turn['text']}\n"
        )

    parts.append("\n## Current DM Profile\n")
    parts.append(json.dumps(current_profile, indent=2))

    return "\n".join(parts)


def _parse_turn_number(turn_id: str) -> int:
    """Extract numeric turn number from turn ID."""
    m = _TURN_ID_RE.match(turn_id)
    return int(m.group(1)) if m else 0


def list_dm_turns(session_dir: str, start_turn: int = 0, max_turns: int = 0) -> list[dict]:
    """List DM turns from a session transcript directory.

    Returns list of dicts with keys: turn_id, speaker, text.
    """
    transcript_dir = os.path.join(session_dir, "transcript")
    if not os.path.isdir(transcript_dir):
        print(f"  WARNING: No transcript directory at {transcript_dir}", file=sys.stderr)
        return []

    turn_files = sorted(
        f for f in os.listdir(transcript_dir)
        if f.startswith("turn-") and f.endswith("-dm.md")
    )

    turns = []
    for fname in turn_files:
        # Extract turn number from filename (e.g. turn-012-dm.md → 12)
        m = re.match(r"turn-(\d+)-dm\.md$", fname)
        if not m:
            continue
        turn_num = int(m.group(1))

        if start_turn and turn_num < start_turn:
            continue
        if max_turns and len(turns) >= max_turns:
            break

        turn_id = f"turn-{m.group(1)}"
        filepath = os.path.join(transcript_dir, fname)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read().strip()

        turns.append({"turn_id": turn_id, "speaker": "dm", "text": text})

    return turns


def analyze_dm_turns(
    turns: list[dict],
    profile: dict,
    llm: "LLMClient",
    batch_size: int = 5,
) -> list[dict]:
    """Analyze DM turns in batches and return observations.

    Sends batches of DM turns to the LLM for behavioral analysis.
    Returns a flat list of observation dicts.
    """
    template = load_template()
    all_observations = []

    for i in range(0, len(turns), batch_size):
        batch = turns[i : i + batch_size]
        batch_ids = [t["turn_id"] for t in batch]
        print(f"  Analyzing DM turns {batch_ids[0]}–{batch_ids[-1]}...")

        user_prompt = _format_analysis_prompt(batch, profile)

        try:
            result = llm.extract_json(
                system_prompt=template,
                user_prompt=user_prompt,
            )
        except QuotaExhaustedError:
            print("  ERROR: LLM quota exhausted, stopping analysis.", file=sys.stderr)
            break
        except LLMExtractionError as e:
            print(f"  WARNING: LLM extraction failed for batch: {e}", file=sys.stderr)
            continue

        if not isinstance(result, dict):
            print(f"  WARNING: Non-dict response for batch, skipping", file=sys.stderr)
            continue

        observations = result.get("observations", [])
        if not isinstance(observations, list):
            print(f"  WARNING: observations is not a list, skipping", file=sys.stderr)
            continue

        # Validate each observation
        valid = []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            if obs.get("field") not in (
                "tone", "structure_patterns", "hint_patterns",
                "adversarial_level", "formatting_preferences",
            ):
                print(f"  WARNING: Unknown field '{obs.get('field')}', skipping observation", file=sys.stderr)
                continue
            if not isinstance(obs.get("confidence", 0), (int, float)):
                continue
            if not obs.get("observation"):
                continue
            valid.append(obs)

        all_observations.extend(valid)
        print(f"    → {len(valid)} observations extracted")

        # Brief delay between batches
        if i + batch_size < len(turns):
            llm.delay()

    return all_observations


def merge_observations(profile: dict, observations: list[dict]) -> dict:
    """Merge LLM-extracted observations into the DM profile.

    Updates the profile in place and returns it.
    """
    if not observations:
        return profile

    # Track the latest turn seen
    latest_turn = profile.get("last_updated_turn", "turn-001")

    # Group observations by field
    by_field: dict[str, list[dict]] = {}
    for obs in observations:
        field = obs["field"]
        by_field.setdefault(field, []).append(obs)

        # Track latest turn
        source = obs.get("source_turn", "")
        if _parse_turn_number(source) > _parse_turn_number(latest_turn):
            latest_turn = source

    # Merge tone — pick highest-confidence observation
    if "tone" in by_field:
        tone_obs = sorted(by_field["tone"], key=lambda o: o.get("confidence", 0), reverse=True)
        profile["tone"] = tone_obs[0]["observation"]

    # Merge array fields — deduplicate by observation text
    for field in ("structure_patterns", "hint_patterns", "formatting_preferences"):
        if field not in by_field:
            continue
        existing = set(profile.get(field, []))
        for obs in by_field[field]:
            existing.add(obs["observation"])
        profile[field] = sorted(existing)

    # Merge adversarial_level — weighted average of observations
    if "adversarial_level" in by_field:
        profile["adversarial_level"] = _aggregate_adversarial_level(
            by_field["adversarial_level"],
            profile.get("adversarial_level", "unknown"),
        )

    # Update confidence — based on number of observations and turn coverage
    profile["confidence"] = _compute_confidence(profile, observations)
    profile["last_updated_turn"] = latest_turn

    return profile


def _aggregate_adversarial_level(observations: list[dict], current: str) -> str:
    """Determine adversarial level from observations.

    Extracts level keywords from observation text and returns the
    confidence-weighted consensus.
    """
    level_keywords = {
        "low": ["low", "permissive", "generous", "forgiving", "easy"],
        "moderate": ["moderate", "balanced", "fair", "medium"],
        "high": ["high", "adversarial", "punishing", "harsh", "strict", "challenging"],
    }

    weighted_scores: dict[str, float] = {}
    for obs in observations:
        text = obs.get("observation", "").lower()
        conf = obs.get("confidence", 0.3)
        for level, keywords in level_keywords.items():
            if any(kw in text for kw in keywords):
                weighted_scores[level] = weighted_scores.get(level, 0) + conf

    if not weighted_scores:
        return current

    return max(weighted_scores, key=weighted_scores.get)


def _compute_confidence(profile: dict, observations: list[dict]) -> float:
    """Compute overall profile confidence.

    Factors:
    - Number of distinct fields with observations
    - Average observation confidence
    - Number of source turns covered
    """
    if not observations:
        return profile.get("confidence", 0.0)

    fields_covered = len({obs["field"] for obs in observations})
    avg_confidence = sum(obs.get("confidence", 0) for obs in observations) / len(observations)
    source_turns = len({obs.get("source_turn", "") for obs in observations})

    # Fields coverage: 5 possible fields
    field_factor = min(fields_covered / 5, 1.0)

    # Turn coverage: more turns = more confidence, diminishing returns
    turn_factor = min(source_turns / 20, 1.0)

    # Composite: weighted blend, capped at 0.9 (1.0 reserved for user-confirmed)
    raw = (avg_confidence * 0.4) + (field_factor * 0.3) + (turn_factor * 0.3)
    # Blend with existing confidence (don't regress)
    existing = profile.get("confidence", 0.0)
    merged = max(existing, raw)
    return round(min(merged, 0.9), 2)


def parse_user_input(filepath: str) -> dict:
    """Parse a user-provided DM profile input document.

    Reads a Markdown file following the template in
    templates/content/dm-profile-user-input.md and extracts
    filled-in sections.

    Returns a dict with section headings as keys and content as values.
    """
    if not os.path.exists(filepath):
        print(f"  ERROR: User input file not found: {filepath}", file=sys.stderr)
        return {}

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    sections: dict[str, str] = {}
    current_section = None
    current_lines: list[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            # Save previous section
            if current_section and current_lines:
                text = "\n".join(current_lines).strip()
                # Filter out empty sections (only comments or whitespace)
                text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
                if text:
                    sections[current_section] = text
            current_section = line[3:].strip()
            current_lines = []
        elif current_section is not None:
            current_lines.append(line)

    # Save last section
    if current_section and current_lines:
        text = "\n".join(current_lines).strip()
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
        if text:
            sections[current_section] = text

    return sections


def merge_user_input(profile: dict, user_sections: dict) -> dict:
    """Merge user-provided information into the DM profile.

    User-provided information is treated as high-confidence (0.8) since it
    comes from direct human knowledge rather than inference.
    """
    if not user_sections:
        return profile

    # Map section names to profile fields
    section_map = {
        "Tone and Content Preferences": "tone",
        "Known DM Tendencies": "notes",
        "Hint and Clue Style": "hint_patterns",
        "Additional Notes": "notes",
    }

    for section, content in user_sections.items():
        if section == "Adversarial Level (Your Assessment)":
            level = content.strip().lower()
            if level in _ADVERSARIAL_LEVELS:
                profile["adversarial_level"] = level
            elif "low" in level:
                profile["adversarial_level"] = "low"
            elif "moderate" in level or "medium" in level:
                profile["adversarial_level"] = "moderate"
            elif "high" in level:
                profile["adversarial_level"] = "high"

        elif section == "Tone and Content Preferences":
            if content:
                existing_tone = profile.get("tone", "")
                if existing_tone:
                    profile["tone"] = f"{existing_tone}; User notes: {content}"
                else:
                    profile["tone"] = content

        elif section == "Known DM Tendencies":
            existing_notes = profile.get("notes", "")
            if existing_notes and existing_notes != "Profile not yet established.":
                profile["notes"] = f"{existing_notes}\n\nUser-provided: {content}"
            else:
                profile["notes"] = f"User-provided: {content}"

        elif section == "Hint and Clue Style":
            hints = profile.get("hint_patterns", [])
            hints.append(f"[user-provided] {content}")
            profile["hint_patterns"] = hints

        elif section in ("House Rules", "Session Zero Agreements", "Campaign Setting"):
            existing_notes = profile.get("notes", "")
            entry = f"\n\n{section}: {content}"
            if existing_notes and existing_notes != "Profile not yet established.":
                profile["notes"] = existing_notes + entry
            else:
                profile["notes"] = entry.strip()

        elif section == "DM Experience and Style":
            patterns = profile.get("structure_patterns", [])
            patterns.append(f"[user-provided] {content}")
            profile["structure_patterns"] = patterns

    # User input bumps confidence
    current_conf = profile.get("confidence", 0.0)
    profile["confidence"] = round(min(max(current_conf, 0.3), 0.9), 2)

    return profile


def analyze_single_turn(
    turn_id: str,
    speaker: str,
    text: str,
    framework_dir: str = "framework",
    config_path: str = "config/llm.json",
    dry_run: bool = False,
    overrides: dict | None = None,
) -> None:
    """Analyze a single DM turn and update the profile.

    Called from ingest_turn.py when --extract is used.
    Only processes DM turns (speaker == "dm").
    """
    if speaker != "dm":
        return

    if LLMClient is None:
        print("  WARNING: LLM client not available for DM profile analysis.", file=sys.stderr)
        return

    profile_path = os.path.join(framework_dir, "dm-profile", "dm-profile.json")
    profile = load_dm_profile(profile_path)

    try:
        llm = LLMClient(config_path, overrides=overrides)
    except (ImportError, LLMExtractionError, FileNotFoundError) as e:
        print(f"  WARNING: DM profile analysis not available: {e}", file=sys.stderr)
        return

    turn = {"turn_id": turn_id, "speaker": speaker, "text": text}
    observations = analyze_dm_turns([turn], profile, llm, batch_size=1)

    if observations:
        profile = merge_observations(profile, observations)
        save_dm_profile(profile, profile_path, dry_run=dry_run)
        print(f"  DM profile updated with {len(observations)} observation(s)")
    else:
        print(f"  No DM behavioral patterns observed in {turn_id}")


def analyze_batch(
    session_dir: str,
    framework_dir: str = "framework",
    config_path: str = "config/llm.json",
    start_turn: int = 0,
    max_turns: int = 0,
    batch_size: int = 5,
    dry_run: bool = False,
    overrides: dict | None = None,
) -> None:
    """Analyze all DM turns in a session and update the profile.

    Called from bootstrap_session.py or standalone CLI.
    """
    if LLMClient is None:
        print("  WARNING: LLM client not available for DM profile analysis.", file=sys.stderr)
        return

    profile_path = os.path.join(framework_dir, "dm-profile", "dm-profile.json")
    profile = load_dm_profile(profile_path)

    try:
        llm = LLMClient(config_path, overrides=overrides)
    except (ImportError, LLMExtractionError, FileNotFoundError) as e:
        print(f"  WARNING: DM profile analysis not available: {e}", file=sys.stderr)
        return

    turns = list_dm_turns(session_dir, start_turn=start_turn, max_turns=max_turns)
    if not turns:
        print("  No DM turns found for profile analysis.")
        return

    print(f"  Analyzing {len(turns)} DM turns for behavioral patterns...")
    t0 = time.monotonic()

    observations = analyze_dm_turns(turns, profile, llm, batch_size=batch_size)

    elapsed = time.monotonic() - t0
    print(f"  Extracted {len(observations)} observations in {elapsed:.1f}s")

    if observations:
        profile = merge_observations(profile, observations)
        save_dm_profile(profile, profile_path, dry_run=dry_run)
        print(f"  DM profile updated (confidence: {profile['confidence']})")
    else:
        print("  No DM behavioral patterns observed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze DM behavioral patterns and update the DM profile."
    )
    parser.add_argument(
        "--session", type=str, default=None,
        help="Path to session directory for transcript analysis",
    )
    parser.add_argument(
        "--user-input", type=str, default=None,
        help="Path to user-provided DM profile input document",
    )
    parser.add_argument(
        "--framework", type=str, default="framework",
        help="Path to framework directory (default: framework)",
    )
    parser.add_argument(
        "--config", type=str, default="config/llm.json",
        help="Path to LLM config file (default: config/llm.json)",
    )
    parser.add_argument(
        "--start-turn", type=int, default=0,
        help="Start analysis from this turn number",
    )
    parser.add_argument(
        "--max-turns", type=int, default=0,
        help="Maximum number of DM turns to analyze (0 = all)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=5,
        help="Number of turns to send per LLM call (default: 5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't write changes to disk",
    )

    args = parser.parse_args()

    if not args.session and not args.user_input:
        parser.error("At least one of --session or --user-input is required")

    profile_path = os.path.join(args.framework, "dm-profile", "dm-profile.json")

    # Process user-provided input first (no LLM needed)
    if args.user_input:
        print(f"Processing user-provided input: {args.user_input}")
        profile = load_dm_profile(profile_path)
        sections = parse_user_input(args.user_input)
        if sections:
            print(f"  Found {len(sections)} non-empty section(s): {', '.join(sections.keys())}")
            profile = merge_user_input(profile, sections)
            save_dm_profile(profile, profile_path, dry_run=args.dry_run)
            print(f"  DM profile updated from user input (confidence: {profile['confidence']})")
        else:
            print("  No filled-in sections found in user input.")

    # Then run transcript analysis (needs LLM)
    if args.session:
        analyze_batch(
            session_dir=args.session,
            framework_dir=args.framework,
            config_path=args.config,
            start_turn=args.start_turn,
            max_turns=args.max_turns,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
