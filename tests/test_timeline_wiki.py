"""Tests for timeline wiki page generation."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from generate_wiki_pages import (
    generate_timeline_page,
    generate_wiki_pages,
    _group_season_ranges,
    _format_turn_range,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TIMELINE = [
    {
        "id": "time-001",
        "source_turn": "turn-003",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.9,
        "signals": ["snow on the ground"],
        "description": "Winter landscape described",
    },
    {
        "id": "time-002",
        "source_turn": "turn-010",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.85,
        "signals": ["cold wind"],
        "description": "Continued cold weather",
    },
    {
        "id": "time-003",
        "source_turn": "turn-025",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.8,
        "signals": ["frozen river"],
    },
    {
        "id": "time-004",
        "source_turn": "turn-051",
        "type": "season_transition",
        "season": "late_winter",
        "confidence": 0.75,
        "signals": ["thawing begins"],
        "description": "Signs of approaching spring",
    },
    {
        "id": "time-005",
        "source_turn": "turn-080",
        "type": "season_transition",
        "season": "early_spring",
        "confidence": 0.7,
        "signals": ["flowers blooming"],
        "description": "Spring arrives",
    },
    {
        "id": "time-006",
        "source_turn": "turn-030",
        "type": "time_skip",
        "confidence": 0.8,
        "description": "Several days pass while traveling",
        "raw_text": "After several days of travel...",
    },
    {
        "id": "time-007",
        "source_turn": "turn-060",
        "type": "time_skip",
        "confidence": 0.6,
        "description": "A week passes during recovery",
    },
    {
        "id": "time-008",
        "source_turn": "turn-015",
        "type": "biological_marker",
        "confidence": 0.9,
        "description": "Character wakes from sleep",
        "raw_text": "You open your eyes as dawn breaks.",
    },
    {
        "id": "time-009",
        "source_turn": "turn-045",
        "type": "biological_marker",
        "confidence": 0.7,
        "description": "Meal taken at midday",
    },
    {
        "id": "time-010",
        "source_turn": "turn-020",
        "type": "anchor_event",
        "confidence": 1.0,
        "description": "Foundation of the settlement",
        "estimated_day": 0,
        "season": "mid_winter",
    },
    {
        "id": "time-011",
        "source_turn": "turn-070",
        "type": "construction_milestone",
        "confidence": 0.85,
        "description": "Wall construction completed",
        "estimated_day": 45,
        "season": "late_winter",
    },
]


# ---------------------------------------------------------------------------
# _group_season_ranges tests
# ---------------------------------------------------------------------------

def test_group_season_ranges_basic():
    """Consecutive same-season entries grouped into one range."""
    ranges = _group_season_ranges(SAMPLE_TIMELINE)
    assert len(ranges) == 3
    assert ranges[0]["season"] == "mid_winter"
    assert ranges[0]["start_turn"] == "turn-003"
    assert ranges[0]["end_turn"] == "turn-025"
    assert ranges[0]["count"] == 3


def test_group_season_ranges_transitions():
    """Each season change creates a new range."""
    ranges = _group_season_ranges(SAMPLE_TIMELINE)
    assert ranges[1]["season"] == "late_winter"
    assert ranges[1]["start_turn"] == "turn-051"
    assert ranges[1]["count"] == 1
    assert ranges[2]["season"] == "early_spring"
    assert ranges[2]["start_turn"] == "turn-080"


def test_group_season_ranges_empty():
    """Empty timeline returns empty ranges."""
    assert _group_season_ranges([]) == []


def test_group_season_ranges_no_seasons():
    """Timeline with no season_transition entries returns empty."""
    entries = [{"type": "time_skip", "source_turn": "turn-005"}]
    assert _group_season_ranges(entries) == []


# ---------------------------------------------------------------------------
# _format_turn_range tests
# ---------------------------------------------------------------------------

def test_format_turn_range_single():
    """Single turn formatted without range."""
    assert _format_turn_range("turn-003", "turn-003") == "Turn 3"


def test_format_turn_range_multi():
    """Range of turns formatted with en-dash."""
    result = _format_turn_range("turn-003", "turn-025")
    assert "3" in result
    assert "25" in result
    assert "\u2013" in result  # en-dash


# ---------------------------------------------------------------------------
# generate_timeline_page tests
# ---------------------------------------------------------------------------

def test_timeline_page_empty():
    """Empty timeline produces placeholder message."""
    md = generate_timeline_page([])
    assert "# Timeline Overview" in md
    assert "No temporal data extracted yet" in md
    assert "timeline.json" in md


def test_timeline_page_has_season_progression():
    """Season progression section with grouped ranges."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "## Season Progression" in md
    assert "Mid Winter" in md
    assert "Late Winter" in md
    assert "Early Spring" in md


def test_timeline_page_has_time_skips():
    """Time skips section lists notable jumps."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "## Time Skips" in md
    assert "Several days pass while traveling" in md
    assert "A week passes during recovery" in md


def test_timeline_page_has_biological_markers():
    """Biological markers section present."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "## Biological Markers" in md
    assert "Character wakes from sleep" in md
    assert "Meal taken at midday" in md


def test_timeline_page_has_day_progression():
    """Day progression section shows estimated days."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "## Day Progression" in md
    assert "Day 0" in md
    assert "Day 45" in md


def test_timeline_page_has_other_markers():
    """Other temporal markers section for anchor events etc."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "## Other Temporal Markers" in md
    assert "Foundation of the settlement" in md
    assert "Wall construction completed" in md


def test_timeline_page_has_footer():
    """Footer with source file reference."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "do not edit manually" in md
    assert "timeline.json" in md


def test_timeline_page_has_summary_stats():
    """Summary line with total markers and turn range."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "11 temporal markers" in md
    # Per-type breakdown is included
    assert "biological marker" in md
    assert "time skip" in md


def test_timeline_page_confidence_format():
    """Confidence values formatted consistently."""
    md = generate_timeline_page(SAMPLE_TIMELINE)
    assert "0.80" in md or "0.60" in md  # time skip confidences


def test_timeline_page_escapes_pipes():
    """Pipe characters in descriptions are escaped."""
    entries = [
        {
            "id": "time-001",
            "source_turn": "turn-005",
            "type": "time_skip",
            "confidence": 0.5,
            "description": "Choice: left | right path",
        }
    ]
    md = generate_timeline_page(entries)
    assert "left \\| right path" in md


# ---------------------------------------------------------------------------
# Integration: generate_wiki_pages with timeline
# ---------------------------------------------------------------------------

def test_generate_wiki_pages_creates_timeline_md():
    """Full wiki generation creates timeline.md when timeline.json exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal catalog structure
        os.makedirs(os.path.join(tmpdir, "characters"))
        timeline_data = [
            {
                "id": "time-001",
                "source_turn": "turn-005",
                "type": "season_transition",
                "season": "mid_winter",
                "confidence": 0.9,
            }
        ]
        with open(os.path.join(tmpdir, "timeline.json"), "w") as f:
            json.dump(timeline_data, f)

        stats = generate_wiki_pages(tmpdir)
        assert "timeline" in stats
        assert stats["timeline"] == 1

        md_path = os.path.join(tmpdir, "timeline.md")
        assert os.path.isfile(md_path)
        with open(md_path) as f:
            content = f.read()
        assert "Mid Winter" in content


def test_generate_wiki_pages_no_timeline_json():
    """Wiki generation succeeds without timeline.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "characters"))
        stats = generate_wiki_pages(tmpdir)
        assert "timeline" not in stats


def test_generate_wiki_pages_type_filter_excludes_timeline():
    """When --type is set to an entity type, timeline is not generated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "characters"))
        with open(os.path.join(tmpdir, "timeline.json"), "w") as f:
            json.dump([{"id": "time-001", "source_turn": "turn-001",
                        "type": "season_transition", "season": "mid_winter"}], f)

        stats = generate_wiki_pages(tmpdir, entity_types=["characters"])
        assert "timeline" not in stats


def test_generate_wiki_pages_type_filter_timeline_only():
    """When --type timeline is set, only timeline is generated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "characters"))
        with open(os.path.join(tmpdir, "timeline.json"), "w") as f:
            json.dump([{"id": "time-001", "source_turn": "turn-001",
                        "type": "season_transition", "season": "mid_winter"}], f)

        stats = generate_wiki_pages(tmpdir, entity_types=["timeline"])
        assert "timeline" in stats
        assert "characters" not in stats
