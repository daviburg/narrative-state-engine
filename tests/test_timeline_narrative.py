"""Tests for timeline narrative summary, season flicker filtering, and wiki page generation."""

import json
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from temporal_extraction import (
    filter_season_flicker,
    detect_anchor_event,
    generate_narrative_timeline,
    generate_timeline_wiki_page,
    _base_season,
    DEFAULT_ANCHOR,
)
from generate_wiki_pages import generate_timeline_page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TIMELINE = [
    {
        "id": "time-001",
        "source_turn": "turn-001",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.8,
        "signals": ["base season: winter"],
        "year": 1,
    },
    {
        "id": "time-002",
        "source_turn": "turn-010",
        "type": "time_skip",
        "signals": ["days_pass: days pass"],
        "confidence": 0.6,
        "raw_text": "days pass",
    },
    {
        "id": "time-003",
        "source_turn": "turn-020",
        "type": "season_transition",
        "season": "late_winter",
        "confidence": 0.7,
        "signals": ["fine season: late_winter"],
        "year": 1,
    },
    {
        "id": "time-004",
        "source_turn": "turn-030",
        "type": "biological_marker",
        "signals": ["pregnancy: pregnant"],
        "confidence": 0.7,
        "raw_text": "pregnant",
    },
    {
        "id": "time-005",
        "source_turn": "turn-040",
        "type": "season_transition",
        "season": "early_spring",
        "confidence": 0.8,
        "signals": ["fine season: early_spring"],
        "year": 1,
    },
    {
        "id": "time-006",
        "source_turn": "turn-050",
        "type": "time_skip",
        "signals": ["weeks_pass: weeks pass"],
        "confidence": 0.6,
        "raw_text": "weeks pass",
    },
    {
        "id": "time-007",
        "source_turn": "turn-060",
        "type": "biological_marker",
        "signals": ["birth: born"],
        "confidence": 0.9,
        "raw_text": "born",
    },
]

# Timeline with season flicker (low-confidence noise)
FLICKERING_TIMELINE = [
    {
        "id": "time-001",
        "source_turn": "turn-001",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.8,
        "signals": ["base season: winter"],
    },
    {
        "id": "time-002",
        "source_turn": "turn-005",
        "type": "season_transition",
        "season": "mid_summer",
        "confidence": 0.4,  # Low confidence — flicker
        "signals": ["base season: summer"],
    },
    {
        "id": "time-003",
        "source_turn": "turn-010",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.5,  # Medium — should be kept (consecutive winter)
        "signals": ["base season: winter"],
    },
    {
        "id": "time-004",
        "source_turn": "turn-020",
        "type": "season_transition",
        "season": "mid_spring",
        "confidence": 0.3,  # Low, isolated — flicker
        "signals": ["base season: spring"],
    },
    {
        "id": "time-005",
        "source_turn": "turn-030",
        "type": "season_transition",
        "season": "early_spring",
        "confidence": 0.8,
        "signals": ["fine season: early_spring"],
    },
    {
        "id": "time-006",
        "source_turn": "turn-015",
        "type": "time_skip",
        "signals": ["days_pass: days pass"],
        "confidence": 0.6,
    },
]


TIMELINE_WITH_ANCHOR = [
    {
        "id": "time-001",
        "source_turn": "turn-003",
        "type": "anchor_event",
        "description": "Captured by the tribe",
        "confidence": 1.0,
        "estimated_day": 0,
    },
    {
        "id": "time-002",
        "source_turn": "turn-010",
        "type": "season_transition",
        "season": "mid_winter",
        "confidence": 0.8,
        "signals": ["base season: winter"],
    },
    {
        "id": "time-003",
        "source_turn": "turn-040",
        "type": "time_skip",
        "signals": ["weeks_pass: weeks pass"],
        "confidence": 0.6,
    },
]


# ---------------------------------------------------------------------------
# filter_season_flicker tests
# ---------------------------------------------------------------------------

class TestFilterSeasonFlicker:
    """Tests for season noise filtering."""

    def test_high_confidence_signals_always_kept(self):
        """High-confidence season transitions should never be filtered."""
        result = filter_season_flicker(SAMPLE_TIMELINE)
        seasons = [e for e in result if e.get("type") == "season_transition"]
        # All entries in SAMPLE_TIMELINE have confidence >= 0.7
        assert len(seasons) == 3

    def test_low_confidence_isolated_signal_removed(self):
        """A lone low-confidence signal with no consecutive support is removed."""
        result = filter_season_flicker(FLICKERING_TIMELINE)
        seasons = [e for e in result if e.get("type") == "season_transition"]
        # mid_summer (conf 0.4, isolated — only summer entry) should be removed
        season_names = [e.get("season") for e in seasons]
        assert "mid_summer" not in season_names

    def test_low_confidence_consecutive_kept(self):
        """Low-confidence signals with same-base-season support are kept."""
        result = filter_season_flicker(FLICKERING_TIMELINE)
        seasons = [e for e in result if e.get("type") == "season_transition"]
        # mid_winter at turn-010 (conf 0.5) should be kept because
        # mid_winter at turn-001 (conf 0.8) provides base-season support
        winter_entries = [e for e in seasons if "winter" in e.get("season", "")]
        assert len(winter_entries) == 2
        # mid_spring at turn-020 (conf 0.3) kept because early_spring provides support
        spring_entries = [e for e in seasons if "spring" in e.get("season", "")]
        assert len(spring_entries) == 2

    def test_non_season_entries_always_preserved(self):
        """Time skips and other entries are never filtered."""
        result = filter_season_flicker(FLICKERING_TIMELINE)
        skips = [e for e in result if e.get("type") == "time_skip"]
        assert len(skips) == 1

    def test_empty_timeline(self):
        """Empty timeline returns empty."""
        result = filter_season_flicker([])
        assert result == []

    def test_custom_thresholds(self):
        """Custom min_confidence and min_support work."""
        # With min_confidence=0.3, everything except below 0.3 passes
        result = filter_season_flicker(FLICKERING_TIMELINE, min_confidence=0.3)
        seasons = [e for e in result if e.get("type") == "season_transition"]
        assert len(seasons) >= 4  # Only things below 0.3 filtered

    def test_result_sorted_by_turn(self):
        """Result entries are sorted by turn number."""
        import re
        result = filter_season_flicker(FLICKERING_TIMELINE)
        turns = []
        for e in result:
            src = e.get("source_turn", "")
            m = re.match(r"turn-0*(\d+)", src)
            if m:
                turns.append(int(m.group(1)))
        assert turns == sorted(turns)


# ---------------------------------------------------------------------------
# _base_season tests
# ---------------------------------------------------------------------------

class TestBaseSeason:
    """Tests for base season extraction."""

    def test_early_winter(self):
        assert _base_season("early_winter") == "winter"

    def test_mid_spring(self):
        assert _base_season("mid_spring") == "spring"

    def test_late_summer(self):
        assert _base_season("late_summer") == "summer"

    def test_early_autumn(self):
        assert _base_season("early_autumn") == "autumn"

    def test_plain_season(self):
        assert _base_season("winter") == "winter"


# ---------------------------------------------------------------------------
# detect_anchor_event tests
# ---------------------------------------------------------------------------

class TestDetectAnchorEvent:
    """Tests for anchor event detection."""

    def test_prefers_explicit_anchor(self):
        """Should prefer anchor_event type entries."""
        anchor = detect_anchor_event(TIMELINE_WITH_ANCHOR)
        assert anchor["turn"] == "turn-003"
        assert "Captured" in anchor["label"]
        assert anchor["day"] == 0

    def test_falls_back_to_significant_event(self):
        """Without anchor_event, falls back to first time_skip or bio marker."""
        timeline_no_anchor = [e for e in SAMPLE_TIMELINE if e.get("type") != "anchor_event"]
        anchor = detect_anchor_event(timeline_no_anchor)
        # First significant event is time-002 (time_skip at turn-010)
        assert anchor["turn"] == "turn-010"

    def test_default_for_empty(self):
        """Empty timeline returns DEFAULT_ANCHOR."""
        anchor = detect_anchor_event([])
        assert anchor == DEFAULT_ANCHOR

    def test_default_for_season_only(self):
        """Timeline with only season transitions uses DEFAULT_ANCHOR."""
        season_only = [e for e in SAMPLE_TIMELINE if e.get("type") == "season_transition"]
        anchor = detect_anchor_event(season_only)
        assert anchor == DEFAULT_ANCHOR


# ---------------------------------------------------------------------------
# generate_narrative_timeline tests
# ---------------------------------------------------------------------------

class TestGenerateNarrativeTimeline:
    """Tests for narrative summary generation."""

    def test_produces_text(self):
        """Should produce non-empty narrative text."""
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE)
        assert len(narrative) > 50
        assert narrative != "*No temporal data available yet.*"

    def test_empty_timeline_returns_placeholder(self):
        """Empty timeline produces placeholder text."""
        narrative = generate_narrative_timeline([])
        assert narrative == "*No temporal data available yet.*"

    def test_mentions_time_elapsed(self):
        """Narrative should mention time elapsed."""
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE)
        assert "elapsed" in narrative.lower() or "day" in narrative.lower()

    def test_mentions_seasons(self):
        """Narrative should reference season progression."""
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE)
        assert "winter" in narrative.lower() or "spring" in narrative.lower()

    def test_mentions_biological_markers(self):
        """Narrative should reference biological markers."""
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE)
        assert "biological" in narrative.lower() or "birth" in narrative.lower() or "pregnan" in narrative.lower()

    def test_custom_anchor(self):
        """Custom anchor is used in narrative."""
        anchor = {"turn": "turn-003", "label": "Captured by the tribe", "day": 0}
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE, anchor=anchor)
        assert "Captured by the tribe" in narrative

    def test_with_anchor_event_in_timeline(self):
        """Anchor event in timeline is auto-detected."""
        narrative = generate_narrative_timeline(TIMELINE_WITH_ANCHOR)
        assert "Captured" in narrative


# ---------------------------------------------------------------------------
# generate_timeline_wiki_page tests
# ---------------------------------------------------------------------------

class TestGenerateTimelineWikiPage:
    """Tests for full timeline wiki page generation."""

    def test_produces_markdown(self):
        """Should produce valid markdown page content."""
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE)
        assert page.startswith("# Timeline\n")
        assert "## Current Position" in page
        assert "## Narrative Summary" in page

    def test_contains_current_position_table(self):
        """Page should have a current position infobox."""
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE)
        assert "**Current Season**" in page
        assert "**Estimated Day**" in page
        assert "**Anchor Event**" in page

    def test_contains_season_table(self):
        """Page should have season progression table."""
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE)
        assert "## Season Progression" in page
        assert "| Turn | Season | Confidence | Signals |" in page

    def test_contains_time_passages_table(self):
        """Page should have time passages table."""
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE)
        assert "## Time Passages" in page

    def test_contains_biological_table(self):
        """Page should have biological markers table."""
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE)
        assert "## Biological & Lifecycle Markers" in page

    def test_contains_footer(self):
        """Page should have auto-generated footer."""
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE)
        assert "do not edit manually" in page

    def test_empty_timeline(self):
        """Empty timeline produces minimal page with no data message."""
        page = generate_timeline_wiki_page([])
        assert "# Timeline" in page
        assert "No temporal data available" in page

    def test_flicker_filtered_in_page(self):
        """Flicker should be filtered in generated page."""
        page = generate_timeline_wiki_page(FLICKERING_TIMELINE)
        # mid_summer flicker (isolated, low confidence) should not appear
        assert "Mid Summer" not in page

    def test_anchor_event_table(self):
        """Anchor events should appear in Other Milestones section."""
        page = generate_timeline_wiki_page(TIMELINE_WITH_ANCHOR)
        assert "## Other Milestones" in page
        assert "Captured by the tribe" in page

    def test_custom_anchor(self):
        """Custom anchor appears in current position."""
        anchor = {"turn": "turn-003", "label": "Captured by the tribe", "day": 0}
        page = generate_timeline_wiki_page(SAMPLE_TIMELINE, anchor=anchor)
        assert "Captured by the tribe" in page


# ---------------------------------------------------------------------------
# generate_timeline_page integration test
# ---------------------------------------------------------------------------

class TestGenerateTimelinePageIntegration:
    """Integration tests for the wiki page file generation."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_generates_file(self):
        """Should write timeline.md to the catalog directory."""
        timeline_path = os.path.join(self.tmpdir, "timeline.json")
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump(SAMPLE_TIMELINE, f)

        count = generate_timeline_page(self.tmpdir)
        assert count == 1

        md_path = os.path.join(self.tmpdir, "timeline.md")
        assert os.path.isfile(md_path)

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "# Timeline" in content
        assert "## Narrative Summary" in content

    def test_no_timeline_data(self):
        """Should return 0 when no timeline.json exists."""
        count = generate_timeline_page(self.tmpdir)
        assert count == 0

    def test_empty_timeline_json(self):
        """Should return 0 when timeline.json contains empty array."""
        timeline_path = os.path.join(self.tmpdir, "timeline.json")
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump([], f)

        count = generate_timeline_page(self.tmpdir)
        assert count == 0
