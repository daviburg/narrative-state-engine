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
    _cap_signal_text,
    _detect_base_season,
    _detect_biological_markers,
    extract_temporal_signals,
    DEFAULT_ANCHOR,
    MAX_SIGNAL_TEXT_LENGTH,
)
from generate_wiki_pages import generate_timeline_page, generate_wiki_pages


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
        assert "biological" in narrative.lower() or "birth" in narrative.lower() or "pregnan" in narrative.lower() or "lifecycle" in narrative.lower() or "passage" in narrative.lower()

    def test_custom_anchor(self):
        """Custom anchor is used in narrative."""
        anchor = {"turn": "turn-003", "label": "Captured by the tribe", "day": 0}
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE, anchor=anchor)
        assert "Captured by the tribe" in narrative

    def test_with_anchor_event_in_timeline(self):
        """Anchor event in timeline is auto-detected."""
        narrative = generate_narrative_timeline(TIMELINE_WITH_ANCHOR)
        assert "Captured" in narrative

    def test_backward_compat_no_kwargs(self):
        """Old signature (no catalog data) still works."""
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE, None, "turn-060")
        assert len(narrative) > 30
        assert narrative != "*No temporal data available yet.*"

    def test_with_events_produces_structured_output(self):
        """Passing events produces structured markdown with Story Progression."""
        events = [
            {
                "id": "evt-001",
                "source_turns": ["turn-005"],
                "type": "decision",
                "description": "The player regains consciousness after a fall.",
                "related_entities": ["char-player"],
            },
            {
                "id": "evt-002",
                "source_turns": ["turn-015"],
                "type": "discovery",
                "description": "A hidden cave is found beneath the cliff.",
                "related_entities": ["loc-cave"],
            },
            {
                "id": "evt-003",
                "source_turns": ["turn-035"],
                "type": "conflict",
                "description": "Wolves attack the camp at night.",
                "related_entities": ["char-player"],
            },
        ]
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE, events=events)
        assert "**Story Progression**" in narrative
        assert "**Temporal Arc**" in narrative

    def test_bio_markers_grouped_not_listed(self):
        """Bio markers are grouped into cycles, not listed 1:1."""
        # Create many bio markers to ensure grouping kicks in
        big_bio_timeline = SAMPLE_TIMELINE + [
            {
                "id": f"time-bio-{i}",
                "source_turn": f"turn-{31+i:03d}",
                "type": "biological_marker",
                "signals": ["pregnancy_progression: belly grows"],
                "confidence": 0.7,
            }
            for i in range(10)
        ]
        events = [
            {"id": "evt-001", "source_turns": ["turn-005"], "type": "decision",
             "description": "The player wakes up."},
        ]
        narrative = generate_narrative_timeline(big_bio_timeline, events=events)
        # Should NOT list each marker individually
        assert narrative.count("belly grows") <= 1
        assert "**Lifecycle Events**" in narrative

    def test_output_length_with_events(self):
        """Output should stay under 2000 chars for a reasonable timeline."""
        events = [
            {"id": f"evt-{i:03d}", "source_turns": [f"turn-{i*5:03d}"],
             "type": "discovery", "description": f"Event {i} happened here."}
            for i in range(10)
        ]
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE, events=events)
        assert len(narrative) < 2000

    def test_fallback_no_catalog_concise(self):
        """Without catalog data, fallback is concise (not a data dump)."""
        narrative = generate_narrative_timeline(SAMPLE_TIMELINE)
        # Fallback should be short — under 500 chars
        assert len(narrative) < 500


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
        # Create minimal catalog structure expected by generate_wiki_pages
        os.makedirs(os.path.join(self.tmpdir, "characters"))

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_generates_file(self):
        """Should write timeline.md to the catalog directory."""
        timeline_path = os.path.join(self.tmpdir, "timeline.json")
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump(SAMPLE_TIMELINE, f)

        stats = generate_wiki_pages(self.tmpdir)
        assert "timeline" in stats
        assert stats["timeline"] == 1

        md_path = os.path.join(self.tmpdir, "timeline.md")
        assert os.path.isfile(md_path)

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "# Timeline" in content
        assert "## Narrative Summary" in content

    def test_no_timeline_data(self):
        """Timeline not generated when no timeline.json exists."""
        stats = generate_wiki_pages(self.tmpdir)
        assert "timeline" not in stats

    def test_empty_timeline_json(self):
        """Empty timeline array produces placeholder page."""
        timeline_path = os.path.join(self.tmpdir, "timeline.json")
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump([], f)

        # generate_timeline_page with empty list still produces a page
        md = generate_timeline_page([])
        assert "No temporal data extracted yet" in md


# ---------------------------------------------------------------------------
# Regression tests for issue #277
# ---------------------------------------------------------------------------

class TestGreedyRegexFix:
    """Regression tests for greedy regex patterns in biological markers."""

    def test_belly_swell_non_greedy(self):
        """belly.*?swell should stop at the first valid ending token."""
        long_text = (
            "Her belly begins to swell. Days later, after more travel and worry, "
            "her belly continues to swell again."
        )
        markers = _detect_biological_markers(long_text)
        pregnancy_prog = [m for m in markers if m[0] == "pregnancy_progression"]
        assert len(pregnancy_prog) == 1
        assert pregnancy_prog[0][1] == "belly begins to swell"
        assert len(pregnancy_prog[0][1]) <= MAX_SIGNAL_TEXT_LENGTH

    def test_life_taken_root_non_greedy(self):
        """life.*?taken root should stop at the first valid ending token."""
        long_text = (
            "A new life has taken root. Later, after hardship and waiting, "
            "everyone fears that life has taken root more deeply still."
        )
        markers = _detect_biological_markers(long_text)
        discovery = [m for m in markers if m[0] == "pregnancy_discovery"]
        assert len(discovery) == 1
        assert discovery[0][1] == "life has taken root"


class TestSignalTextCap:
    """Regression tests for signal text length cap."""

    def test_cap_short_text_unchanged(self):
        """Short text passes through unchanged."""
        assert _cap_signal_text("hello") == "hello"

    def test_cap_at_boundary(self):
        """Text exactly at limit is unchanged."""
        text = "x" * MAX_SIGNAL_TEXT_LENGTH
        assert _cap_signal_text(text) == text

    def test_cap_over_limit_truncated(self):
        """Text over limit is truncated with ellipsis."""
        text = "x" * (MAX_SIGNAL_TEXT_LENGTH + 50)
        result = _cap_signal_text(text)
        assert len(result) == MAX_SIGNAL_TEXT_LENGTH
        assert result.endswith("...")

    def test_no_signal_over_limit_in_extraction(self):
        """extract_temporal_signals never produces signals with text > limit."""
        long_text = (
            "Her belly " + "stretches and " * 50 + "swells with new life. "
            "The snow falls heavily and frost covers the ground. "
            "The cold biting cold of deep winter settles. "
            "Days pass slowly in the frozen wilderness."
        )
        signals = extract_temporal_signals(long_text, "turn-099")
        for sig in signals:
            raw = sig.get("raw_text", "")
            assert len(raw) <= MAX_SIGNAL_TEXT_LENGTH, (
                f"Signal raw_text too long ({len(raw)} chars): {raw[:50]}..."
            )


class TestSeasonDetectionThresholds:
    """Regression tests for season detection requiring distinct keywords and margin."""

    def test_single_keyword_not_enough(self):
        """A single 'cold' mention should NOT trigger winter detection."""
        text = "The cold is harsh but her warmth sustains them."
        result = _detect_base_season(text)
        assert result is None

    def test_ambiguous_text_returns_none(self):
        """Text with equal winter/summer signals returns None."""
        text = "The frost melts in the warm sun, ice gives way to heat."
        result = _detect_base_season(text)
        # Even if both score similarly, margin requirement prevents a pick
        assert result is None

    def test_strong_winter_signal_detected(self):
        """Multiple distinct winter keywords are detected correctly."""
        text = (
            "The snow falls thick, frost covers the ground, "
            "and the frozen river cracks under the weight of ice."
        )
        result = _detect_base_season(text)
        assert result == "winter"

    def test_winter_dominant_no_spurious_summer(self):
        """A winter story with incidental 'warm' should not detect summer."""
        text = (
            "Deep winter has settled. Snow blankets everything. "
            "The frozen lake creaks. Inside, a warm fire burns. "
            "The cold biting cold seeps through the walls."
        )
        result = _detect_base_season(text)
        # Should be winter or None, never summer
        assert result != "summer"


class TestFlickerSlidingWindow:
    """Regression tests for sliding-window season flicker filtering."""

    def test_isolated_summer_in_winter_sequence_removed(self):
        """A single summer entry surrounded by winter entries is filtered out."""
        timeline = []
        for i in range(1, 21):
            season = "mid_winter" if i != 10 else "mid_summer"
            conf = 0.8 if i != 10 else 0.4
            timeline.append({
                "id": f"time-{i:03d}",
                "source_turn": f"turn-{i:03d}",
                "type": "season_transition",
                "season": season,
                "confidence": conf,
                "signals": [f"base season: {'winter' if i != 10 else 'summer'}"],
            })
        result = filter_season_flicker(timeline)
        seasons = [e.get("season") for e in result if e.get("type") == "season_transition"]
        assert "mid_summer" not in seasons

    def test_legitimate_transition_preserved(self):
        """A real season change (multiple consecutive entries) is kept."""
        timeline = []
        # 10 winter entries, then 10 spring entries
        for i in range(1, 11):
            timeline.append({
                "id": f"time-{i:03d}",
                "source_turn": f"turn-{i:03d}",
                "type": "season_transition",
                "season": "mid_winter",
                "confidence": 0.8,
                "signals": ["base season: winter"],
            })
        for i in range(11, 21):
            timeline.append({
                "id": f"time-{i:03d}",
                "source_turn": f"turn-{i:03d}",
                "type": "season_transition",
                "season": "early_spring",
                "confidence": 0.5,  # Low confidence but supported by neighbors
                "signals": ["base season: spring"],
            })
        result = filter_season_flicker(timeline)
        spring = [e for e in result if "spring" in e.get("season", "")]
        assert len(spring) == 10  # All spring entries kept


class TestAnchorLabelFormatting:
    """Regression tests for human-readable anchor labels."""

    def test_no_type_colon_type_pattern(self):
        """Anchor label should never look like 'type: type'."""
        timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-005",
                "type": "biological_marker",
                "signals": ["labor: labor"],
                "confidence": 0.7,
                "raw_text": "labor",
            }
        ]
        anchor = detect_anchor_event(timeline)
        assert "labor: labor" not in anchor["label"]
        assert anchor["label"][0].isupper()  # Should be capitalized

    def test_uses_raw_text_when_short(self):
        """Should use raw_text as label when it's short and readable."""
        timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-010",
                "type": "time_skip",
                "signals": ["days_pass: days pass"],
                "confidence": 0.6,
                "raw_text": "days pass",
            }
        ]
        anchor = detect_anchor_event(timeline)
        assert anchor["label"] == "Days pass"

    def test_fallback_to_type_label(self):
        """Without raw_text, uses human-readable type description."""
        timeline = [
            {
                "id": "time-001",
                "source_turn": "turn-010",
                "type": "biological_marker",
                "signals": ["pregnancy: pregnant"],
                "confidence": 0.7,
            }
        ]
        anchor = detect_anchor_event(timeline)
        assert "biological marker" in anchor["label"].lower()

