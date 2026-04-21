"""Tests for timeline season normalization (#151)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from extract_structured_data import _normalize_timeline_season, extract_temporal_markers


class TestNormalizeTimelineSeason:
    def test_fall_maps_to_mid_autumn(self):
        assert _normalize_timeline_season("fall") == "mid_autumn"

    def test_winter_maps_to_mid_winter(self):
        assert _normalize_timeline_season("winter") == "mid_winter"

    def test_autumn_maps_to_mid_autumn(self):
        assert _normalize_timeline_season("autumn") == "mid_autumn"

    def test_spring_maps_to_mid_spring(self):
        assert _normalize_timeline_season("spring") == "mid_spring"

    def test_summer_maps_to_mid_summer(self):
        assert _normalize_timeline_season("summer") == "mid_summer"

    def test_early_fall_maps_to_early_autumn(self):
        assert _normalize_timeline_season("early_fall") == "early_autumn"

    def test_mid_winter_passthrough(self):
        assert _normalize_timeline_season("mid_winter") == "mid_winter"

    def test_late_spring_passthrough(self):
        assert _normalize_timeline_season("late_spring") == "late_spring"

    def test_case_insensitive(self):
        assert _normalize_timeline_season("Spring") == "mid_spring"

    def test_whitespace_stripped(self):
        assert _normalize_timeline_season("  winter  ") == "mid_winter"


class TestExtractTemporalMarkersSeasonEnum:
    def test_fall_transition_produces_mid_autumn(self):
        text = "As fall arrives, the leaves change color."
        entries = extract_temporal_markers(text, "turn-100")
        assert len(entries) == 1
        assert entries[0]["season"] == "mid_autumn"

    def test_winter_transition_produces_mid_winter(self):
        text = "Winter sets in across the land."
        entries = extract_temporal_markers(text, "turn-101")
        assert len(entries) == 1
        assert entries[0]["season"] == "mid_winter"

    def test_autumn_transition_produces_mid_autumn(self):
        text = "Autumn arrives with cool winds."
        entries = extract_temporal_markers(text, "turn-102")
        assert len(entries) == 1
        assert entries[0]["season"] == "mid_autumn"
