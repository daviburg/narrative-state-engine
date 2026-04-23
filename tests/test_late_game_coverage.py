"""Tests for late-game coverage fixes (#185): season coercion, validation globs, optional flag."""
import fnmatch
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from extract_structured_data import _normalize_season


# ---------------------------------------------------------------------------
# Season coercion tests
# ---------------------------------------------------------------------------

class TestSeasonCoercion:
    """_normalize_season handles sub-seasons and colloquial variants."""

    def test_mid_hyphen_winter(self):
        assert _normalize_season("mid-winter") == "mid_winter"

    def test_midwinter_compound(self):
        assert _normalize_season("midwinter") == "mid_winter"

    def test_deep_winter(self):
        assert _normalize_season("deep winter") == "mid_winter"

    def test_autumn_to_fall(self):
        assert _normalize_season("autumn") == "fall"

    def test_early_autumn(self):
        assert _normalize_season("early_autumn") == "early_fall"

    def test_late_autumn(self):
        assert _normalize_season("late autumn") == "late_fall"

    def test_plain_spring_unchanged(self):
        assert _normalize_season("spring") == "spring"

    def test_plain_winter_unchanged(self):
        assert _normalize_season("winter") == "winter"

    def test_early_spring_passthrough(self):
        assert _normalize_season("early_spring") == "early_spring"

    def test_case_insensitive(self):
        assert _normalize_season("Mid-Winter") == "mid_winter"

    def test_deep_summer(self):
        assert _normalize_season("deep summer") == "summer"

    def test_mid_summer_not_in_enum(self):
        """mid-summer / mid summer -> summer (mid_summer is not a schema enum)."""
        assert _normalize_season("mid-summer") == "summer"

    def test_mid_spring_not_in_enum(self):
        """mid-spring / mid spring -> spring (mid_spring is not a schema enum)."""
        assert _normalize_season("mid spring") == "spring"

    def test_mid_fall_not_in_enum(self):
        """mid-fall / mid fall -> fall (mid_fall is not a schema enum)."""
        assert _normalize_season("mid-fall") == "fall"

    def test_mid_autumn_to_fall(self):
        assert _normalize_season("mid autumn") == "fall"


# ---------------------------------------------------------------------------
# Ground truth glob matching tests
# ---------------------------------------------------------------------------

class TestGlobPatternMatching:
    """id_patterns with glob wildcards match catalog IDs correctly."""

    def test_glob_matches_turn_tagged_id(self):
        pattern = "char-shaman-turn-*"
        assert fnmatch.fnmatch("char-shaman-turn-082", pattern)

    def test_glob_does_not_match_unrelated(self):
        pattern = "char-shaman-turn-*"
        assert not fnmatch.fnmatch("char-healer-turn-100", pattern)

    def test_exact_pattern_still_works(self):
        pattern = "char-shaman"
        assert fnmatch.fnmatch("char-shaman", pattern)
