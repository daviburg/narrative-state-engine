"""Tests for season summary extraction."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.extract_structured_data import extract_season_summaries


# Minimal DM season summary block matching the reported format
SAMPLE_SEASON_SUMMARY = """\
**Season 2 Summary: Autumn**

**Regional Changes:**
The northern passes have begun to freeze. River crossings are treacherous.

**Faction Actions:**
The Iron Covenant increased border patrols. The River Clans stockpile grain.

**Rumors Reaching the Tribe:**
Travelers speak of strange lights in the western mountains.

**Consequences of Prior Developments:**
The bridge repair from last season has improved trade flow.
"""


def test_detects_season_2_summary_header():
    """Season N Summary: <season> format is detected."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    assert len(results) == 1
    assert results[0]["source_turn"] == "turn-042"


def test_extracts_season_name_from_header():
    """Season name is extracted from the header line."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    assert results[0].get("season") == "fall"  # "autumn" normalized to "fall"


def test_extracts_standard_sections():
    """Regional Changes and Faction Actions are extracted."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    sections = results[0]["sections"]
    assert "regional_changes" in sections
    assert "faction_actions" in sections


def test_extracts_rumors_section():
    """Rumors Reaching the Tribe is captured."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    sections = results[0]["sections"]
    assert "rumors" in sections
    assert "strange lights" in sections["rumors"]


def test_extracts_consequences_section():
    """Consequences of Prior Developments is captured."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    sections = results[0]["sections"]
    assert "consequences" in sections
    assert "bridge repair" in sections["consequences"]


def test_captures_raw_text():
    """raw_text field is populated."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    assert results[0].get("raw_text")
    assert len(results[0]["raw_text"]) > 0


def test_id_format():
    """ID follows ss-NNN pattern."""
    results = extract_season_summaries(SAMPLE_SEASON_SUMMARY, "turn-042")
    assert results[0]["id"] == "ss-001"


def test_original_format_still_works():
    """Original 'Season Summary' header (no number, no colon) still detected."""
    text = """\
**Season Summary**

**Regional Changes:**
Snow covers the lowlands.

**Faction Actions:**
The guild suspends trade.
"""
    results = extract_season_summaries(text, "turn-010")
    assert len(results) == 1
    sections = results[0]["sections"]
    assert "regional_changes" in sections
    assert "faction_actions" in sections


def test_season_summary_with_just_season_name():
    """'Autumn Summary' format is detected."""
    text = """\
**Autumn Summary**

**Regional Changes:**
Leaves fall.

**Environmental Notes:**
First frost observed.
"""
    results = extract_season_summaries(text, "turn-020")
    assert len(results) == 1
    assert results[0].get("season") == "fall"


def test_economic_or_ecological_section():
    """'Economic or Ecological Shifts' variant is captured."""
    text = """\
**Season Summary**

**Regional Changes:**
Rivers are rising.

**Economic or Ecological Shifts:**
Trade caravans grow scarce as wildlife migrates south.
"""
    results = extract_season_summaries(text, "turn-030")
    assert len(results) == 1
    sections = results[0]["sections"]
    assert "economic_shifts" in sections
    assert "caravans" in sections["economic_shifts"]


def test_singular_header_forms():
    """Singular header forms (Rumor, Consequence) are mapped correctly."""
    text = """\
**Season Summary**

**Rumor Reaching the Tribe:**
A dragon was spotted.

**Consequence of Prior Actions:**
The village was rebuilt.
"""
    results = extract_season_summaries(text, "turn-035")
    assert len(results) == 1
    sections = results[0]["sections"]
    assert "rumors" in sections
    assert "consequences" in sections


def test_no_match_on_normal_text():
    """Normal narrative text does not trigger season summary detection."""
    text = "The party traveled through autumn forests. The consequences of their actions were unclear."
    results = extract_season_summaries(text, "turn-005")
    assert len(results) == 0
