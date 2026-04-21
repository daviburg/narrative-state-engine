"""Tests for timeline tracking: schema validation, temporal signal extraction, day estimation."""

import json
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from temporal_extraction import (
    extract_temporal_signals,
    estimate_day_from_anchor,
    merge_temporal_signals,
    get_next_timeline_id,
    get_season_at_turn,
    get_current_timeline_summary,
    format_season_label,
    load_timeline,
    save_timeline,
    _detect_base_season,
    _detect_fine_season,
    _detect_biological_markers,
    _detect_time_skips,
    _detect_time_of_day,
    _parse_turn_number,
)
from validate import validate_file

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
TIMELINE_SCHEMA = os.path.join(REPO_ROOT, "schemas", "timeline.schema.json")


def _write_and_validate(data, schema_path):
    """Write data to temp file, validate, clean up."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "test.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return validate_file(path, schema_path)
    finally:
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestTimelineSchemaValidation:
    """Validate timeline entries against the expanded schema."""

    def test_minimal_valid_entry(self):
        entry = {
            "id": "time-001",
            "source_turn": "turn-001",
            "type": "season_transition",
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_full_entry_with_all_fields(self):
        entry = {
            "id": "time-042",
            "source_turn": "turn-113",
            "type": "biological_marker",
            "season": "early_spring",
            "estimated_day": 270,
            "confidence": 0.8,
            "anchor_ref": "Day 0",
            "signals": ["first signs of thaw", "pregnancy progression"],
            "year": 1,
            "description": "First signs of thaw with pregnancy marker",
            "raw_text": "The first true signs of thaw touched the land",
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_new_type_biological_marker(self):
        entry = {
            "id": "time-010",
            "source_turn": "turn-121",
            "type": "biological_marker",
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_new_type_construction_milestone(self):
        entry = {
            "id": "time-020",
            "source_turn": "turn-127",
            "type": "construction_milestone",
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_new_type_anchor_event(self):
        entry = {
            "id": "time-030",
            "source_turn": "turn-292",
            "type": "anchor_event",
            "description": "Foundation of the Quiet Weave",
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_fine_grained_seasons(self):
        for season in ["early_winter", "mid_winter", "late_winter",
                       "early_spring", "mid_spring", "late_spring",
                       "early_summer", "mid_summer", "late_summer",
                       "early_autumn", "mid_autumn", "late_autumn"]:
            entry = {
                "id": "time-001",
                "source_turn": "turn-001",
                "type": "season_transition",
                "season": season,
            }
            errors = _write_and_validate(entry, TIMELINE_SCHEMA)
            assert errors == [], f"Season '{season}' failed validation: {errors}"

    def test_negative_estimated_day(self):
        """Events before the anchor should get negative day values."""
        entry = {
            "id": "time-001",
            "source_turn": "turn-001",
            "type": "season_transition",
            "estimated_day": -30,
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert errors == [], f"Negative estimated_day failed: {errors}"

    def test_invalid_type_rejected(self):
        entry = {
            "id": "time-001",
            "source_turn": "turn-001",
            "type": "invalid_type",
        }
        errors = _write_and_validate(entry, TIMELINE_SCHEMA)
        assert len(errors) > 0, "Invalid type should be rejected"

    def test_old_season_values_rejected(self):
        """The old 4-season values should no longer be valid."""
        for old_season in ["spring", "summer", "fall", "winter"]:
            entry = {
                "id": "time-001",
                "source_turn": "turn-001",
                "type": "season_transition",
                "season": old_season,
            }
            errors = _write_and_validate(entry, TIMELINE_SCHEMA)
            assert len(errors) > 0, f"Old season '{old_season}' should be rejected"

    def test_confidence_bounds(self):
        """Confidence must be between 0.0 and 1.0."""
        for conf in [0.0, 0.5, 1.0]:
            entry = {
                "id": "time-001",
                "source_turn": "turn-001",
                "type": "season_transition",
                "confidence": conf,
            }
            errors = _write_and_validate(entry, TIMELINE_SCHEMA)
            assert errors == [], f"Confidence {conf} failed: {errors}"

        for bad_conf in [-0.1, 1.1]:
            entry = {
                "id": "time-001",
                "source_turn": "turn-001",
                "type": "season_transition",
                "confidence": bad_conf,
            }
            errors = _write_and_validate(entry, TIMELINE_SCHEMA)
            assert len(errors) > 0, f"Confidence {bad_conf} should be rejected"


# ---------------------------------------------------------------------------
# Temporal signal extraction tests
# ---------------------------------------------------------------------------

class TestExtractTemporalSignals:
    """Test pattern-based temporal signal extraction."""

    def test_season_snow_melted_spring(self):
        text = "The first true signs of thaw touched the land with tentative fingers."
        signals = extract_temporal_signals(text, "turn-100")
        season_signals = [s for s in signals if s["type"] == "season_transition"]
        assert len(season_signals) >= 1
        assert "spring" in season_signals[0]["season"]

    def test_season_deep_winter(self):
        text = "Deep winter clings to the land with an unyielding grip."
        signals = extract_temporal_signals(text, "turn-050")
        season_signals = [s for s in signals if s["type"] == "season_transition"]
        assert len(season_signals) >= 1
        assert season_signals[0]["season"] == "mid_winter"

    def test_season_autumn_does_not_linger(self):
        text = "Autumn does not linger forever. The chill deepens."
        signals = extract_temporal_signals(text, "turn-107")
        season_signals = [s for s in signals if s["type"] == "season_transition"]
        assert len(season_signals) >= 1
        assert "autumn" in season_signals[0]["season"]

    def test_biological_marker_belly_swell(self):
        text = "Your belly began to swell with new life."
        signals = extract_temporal_signals(text, "turn-129")
        bio_signals = [s for s in signals if s["type"] == "biological_marker"]
        assert len(bio_signals) >= 1
        assert any("pregnancy" in s["signals"][0] for s in bio_signals)

    def test_biological_marker_birth(self):
        text = "A girl named Lyrawyn is born, with fair skin and deep eyes."
        signals = extract_temporal_signals(text, "turn-141")
        bio_signals = [s for s in signals if s["type"] == "biological_marker"]
        assert len(bio_signals) >= 1

    def test_time_skip_weeks_pass(self):
        text = "The weeks continue to unfold, weaving you deeper into the fabric."
        signals = extract_temporal_signals(text, "turn-120")
        skip_signals = [s for s in signals if s["type"] == "time_skip"]
        assert len(skip_signals) >= 1

    def test_time_skip_months_pass(self):
        text = "The following months pass in a rhythm of quiet growth."
        signals = extract_temporal_signals(text, "turn-150")
        skip_signals = [s for s in signals if s["type"] == "time_skip"]
        assert len(skip_signals) >= 1

    def test_construction_event_detected(self):
        events = [{
            "id": "evt-073",
            "source_turns": ["turn-127"],
            "type": "construction",
            "description": "Player begins constructing longhouse",
        }]
        signals = extract_temporal_signals("building begins", "turn-127", events=events)
        construction = [s for s in signals if s["type"] == "construction_milestone"]
        assert len(construction) >= 1

    def test_birth_event_detected(self):
        events = [{
            "id": "evt-088",
            "source_turns": ["turn-141"],
            "type": "birth",
            "description": "Lyrawyn is born",
        }]
        signals = extract_temporal_signals("a child is born", "turn-141", events=events)
        bio = [s for s in signals if s["type"] == "biological_marker"]
        assert len(bio) >= 1

    def test_no_signals_in_plain_text(self):
        text = "You walk through the forest, noting the tall trees."
        signals = extract_temporal_signals(text, "turn-050")
        assert len(signals) == 0

    def test_source_turn_preserved(self):
        text = "The first true signs of thaw touched the land."
        signals = extract_temporal_signals(text, "turn-113")
        for s in signals:
            assert s["source_turn"] == "turn-113"


# ---------------------------------------------------------------------------
# Day estimation tests
# ---------------------------------------------------------------------------

class TestDayEstimation:
    """Test day offset estimation from anchor."""

    def test_estimated_day_from_default_anchor(self):
        result = estimate_day_from_anchor("turn-100")
        assert result["estimated_day"] > 0
        assert result["anchor_ref"] == "Day 0"
        assert 0 < result["confidence"] <= 1.0

    def test_day_zero_at_anchor(self):
        result = estimate_day_from_anchor("turn-001")
        assert result["estimated_day"] == 0

    def test_negative_day_for_pre_anchor(self):
        """Events before a named anchor get negative day values."""
        anchor = {"turn": "turn-100", "label": "Foundation", "day": 0}
        result = estimate_day_from_anchor("turn-050", anchor=anchor)
        assert result["estimated_day"] < 0

    def test_positive_day_for_post_anchor(self):
        anchor = {"turn": "turn-100", "label": "Foundation", "day": 0}
        result = estimate_day_from_anchor("turn-200", anchor=anchor)
        assert result["estimated_day"] > 0

    def test_custom_days_per_turn(self):
        result_slow = estimate_day_from_anchor("turn-100", days_per_turn=1.0)
        result_fast = estimate_day_from_anchor("turn-100", days_per_turn=10.0)
        assert result_fast["estimated_day"] > result_slow["estimated_day"]

    def test_confidence_decreases_with_distance(self):
        close = estimate_day_from_anchor("turn-010")
        far = estimate_day_from_anchor("turn-200")
        assert close["confidence"] >= far["confidence"]

    def test_pregnancy_calibration_rough(self):
        """Pregnancy discovery to birth spans ~270 days.

        With default 3.5 days/turn:
        turn-121 to turn-141 = 20 turns * 3.5 = 70 days
        This is shorter than real pregnancy, which shows turns are
        not uniform. But the ratio should be roughly consistent.
        """
        discovery = estimate_day_from_anchor("turn-121")
        birth = estimate_day_from_anchor("turn-141")
        span = birth["estimated_day"] - discovery["estimated_day"]
        # With 3.5 days/turn, 20 turns = 70 days
        assert span == 70  # 20 * 3.5
        assert span > 0

    def test_invalid_turn_id(self):
        result = estimate_day_from_anchor("invalid")
        assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Timeline catalog management tests
# ---------------------------------------------------------------------------

class TestTimelineCatalog:
    """Test timeline catalog load/save/merge operations."""

    def test_load_empty_timeline(self):
        tmpdir = tempfile.mkdtemp()
        try:
            # Write empty array
            path = os.path.join(tmpdir, "timeline.json")
            with open(path, "w") as f:
                json.dump([], f)
            result = load_timeline(tmpdir)
            assert result == []
        finally:
            shutil.rmtree(tmpdir)

    def test_load_nonexistent_timeline(self):
        tmpdir = tempfile.mkdtemp()
        try:
            result = load_timeline(tmpdir)
            assert result == []
        finally:
            shutil.rmtree(tmpdir)

    def test_save_and_load_roundtrip(self):
        tmpdir = tempfile.mkdtemp()
        try:
            entries = [{
                "id": "time-001",
                "source_turn": "turn-001",
                "type": "season_transition",
                "season": "mid_winter",
            }]
            save_timeline(tmpdir, entries)
            loaded = load_timeline(tmpdir)
            assert len(loaded) == 1
            assert loaded[0]["id"] == "time-001"
            assert loaded[0]["season"] == "mid_winter"
        finally:
            shutil.rmtree(tmpdir)

    def test_merge_avoids_duplicates(self):
        existing = [{
            "id": "time-001",
            "source_turn": "turn-001",
            "type": "season_transition",
            "season": "mid_winter",
        }]
        new_signals = [{
            "source_turn": "turn-001",
            "type": "season_transition",
            "season": "mid_winter",
        }]
        result = merge_temporal_signals(existing, new_signals)
        assert len(result) == 1  # No duplicate

    def test_merge_adds_new_entry(self):
        existing = [{
            "id": "time-001",
            "source_turn": "turn-001",
            "type": "season_transition",
        }]
        new_signals = [{
            "source_turn": "turn-050",
            "type": "time_skip",
            "signals": ["weeks pass"],
        }]
        result = merge_temporal_signals(existing, new_signals)
        assert len(result) == 2
        assert result[1]["id"] == "time-002"

    def test_get_next_timeline_id(self):
        timeline = [
            {"id": "time-001"},
            {"id": "time-005"},
            {"id": "time-003"},
        ]
        assert get_next_timeline_id(timeline) == 6

    def test_get_next_timeline_id_empty(self):
        assert get_next_timeline_id([]) == 1


# ---------------------------------------------------------------------------
# Season query tests
# ---------------------------------------------------------------------------

class TestSeasonQueries:
    """Test season lookup and timeline summary."""

    def test_get_season_at_turn(self):
        timeline = [
            {"id": "time-001", "source_turn": "turn-001", "type": "season_transition",
             "season": "mid_winter"},
            {"id": "time-002", "source_turn": "turn-113", "type": "season_transition",
             "season": "early_spring"},
            {"id": "time-003", "source_turn": "turn-155", "type": "season_transition",
             "season": "mid_summer"},
        ]
        assert get_season_at_turn(timeline, "turn-050") == "mid_winter"
        assert get_season_at_turn(timeline, "turn-113") == "early_spring"
        assert get_season_at_turn(timeline, "turn-130") == "early_spring"
        assert get_season_at_turn(timeline, "turn-200") == "mid_summer"

    def test_get_season_before_any_marker(self):
        timeline = [
            {"id": "time-001", "source_turn": "turn-050", "type": "season_transition",
             "season": "mid_winter"},
        ]
        # No season markers before turn-010
        assert get_season_at_turn(timeline, "turn-010") is None

    def test_format_season_label(self):
        assert format_season_label("mid_winter") == "Mid Winter"
        assert format_season_label("early_spring") == "Early Spring"
        assert format_season_label("late_autumn") == "Late Autumn"

    def test_current_timeline_summary(self):
        timeline = [
            {"id": "time-001", "source_turn": "turn-001", "type": "season_transition",
             "season": "mid_winter"},
            {"id": "time-002", "source_turn": "turn-345", "type": "season_transition",
             "season": "mid_winter"},
        ]
        summary = get_current_timeline_summary(timeline, latest_turn="turn-345")
        assert summary["estimated_day"] > 0
        assert summary["season"] == "mid_winter"
        assert summary["anchor_turn"] == "turn-001"

    def test_current_timeline_summary_empty(self):
        summary = get_current_timeline_summary([])
        assert summary["estimated_day"] == 0
        assert summary["season"] is None


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    """Test low-level extraction helpers."""

    def test_detect_base_season_winter(self):
        assert _detect_base_season("Snow covers the frozen ground") == "winter"

    def test_detect_base_season_spring(self):
        assert _detect_base_season("Flowers bloom and sprout in the thaw") == "spring"

    def test_detect_base_season_summer(self):
        assert _detect_base_season("The warm summer days bring growth") == "summer"

    def test_detect_base_season_autumn(self):
        assert _detect_base_season("Autumn leaves fall in the cooling air") == "autumn"

    def test_detect_base_season_none(self):
        assert _detect_base_season("You walk through the forest") is None

    def test_detect_fine_season_deep_winter(self):
        assert _detect_fine_season("Deep winter clings to the land") == "mid_winter"

    def test_detect_fine_season_first_thaw(self):
        assert _detect_fine_season("The first signs of thaw appeared") == "early_spring"

    def test_detect_fine_season_late_summer(self):
        assert _detect_fine_season("From late summer into autumn") == "late_summer"

    def test_detect_biological_pregnancy(self):
        markers = _detect_biological_markers("She is pregnant with her first child")
        assert len(markers) >= 1
        assert markers[0][0] == "pregnancy"

    def test_detect_biological_belly_swell(self):
        markers = _detect_biological_markers("Your belly began to swell with life")
        assert len(markers) >= 1
        assert "pregnancy" in markers[0][0]

    def test_detect_biological_birth(self):
        markers = _detect_biological_markers("A child is born to the settlement")
        assert len(markers) >= 1

    def test_detect_time_skip_weeks(self):
        skips = _detect_time_skips("The weeks continue to unfold")
        assert len(skips) >= 1

    def test_detect_time_skip_months(self):
        skips = _detect_time_skips("The following months pass in quiet growth")
        assert len(skips) >= 1

    def test_detect_time_of_day_dawn(self):
        markers = _detect_time_of_day("At first light, before the world woke")
        assert "dawn" in markers

    def test_parse_turn_number(self):
        assert _parse_turn_number("turn-001") == 1
        assert _parse_turn_number("turn-345") == 345
        assert _parse_turn_number("turn-042") == 42
        assert _parse_turn_number(None) is None
        assert _parse_turn_number("invalid") is None
