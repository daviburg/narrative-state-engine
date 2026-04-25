"""Tests for the pre-flight context window sufficiency check (#222)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import estimate_peak_context, preflight_context_check


class TestEstimatePeakContext:
    """Tests for estimate_peak_context()."""

    def test_insufficient_8k_300_turns(self):
        """8K context with 300+ turns should be flagged insufficient (Run 12 scenario)."""
        result = estimate_peak_context(
            turn_count=344,
            context_length=8192,
            max_tokens=4096,
            existing_entity_count=0,
        )
        assert not result["sufficient"]
        assert result["estimated_peak_tokens"] > 8192
        assert len(result["warnings"]) > 0
        assert any("exceeds" in w for w in result["warnings"])

    def test_sufficient_1m_300_turns(self):
        """1M context with 300 turns should be fine."""
        result = estimate_peak_context(
            turn_count=300,
            context_length=1_000_000,
            max_tokens=4096,
            existing_entity_count=0,
        )
        assert result["sufficient"]
        assert result["headroom_pct"] > 50
        assert len(result["warnings"]) == 0

    def test_sufficient_32k_moderate_session(self):
        """32K context with a moderate session (100 turns) should pass."""
        result = estimate_peak_context(
            turn_count=100,
            context_length=32768,
            max_tokens=4096,
            existing_entity_count=0,
        )
        assert result["sufficient"]
        assert len(result["warnings"]) == 0

    def test_existing_entities_increase_estimate(self):
        """Resuming with existing entities should increase estimated peak."""
        base = estimate_peak_context(
            turn_count=100,
            context_length=32768,
            max_tokens=4096,
            existing_entity_count=0,
        )
        resumed = estimate_peak_context(
            turn_count=100,
            context_length=32768,
            max_tokens=4096,
            existing_entity_count=80,
        )
        assert resumed["estimated_peak_tokens"] > base["estimated_peak_tokens"]
        assert resumed["projected_entity_count"] > base["projected_entity_count"]

    def test_existing_entities_trigger_warning(self):
        """Resuming with many existing entities in a tight window should warn."""
        result = estimate_peak_context(
            turn_count=100,
            context_length=8192,
            max_tokens=4096,
            existing_entity_count=50,
        )
        assert not result["sufficient"]
        assert len(result["warnings"]) > 0

    def test_segmentation_reduces_estimate(self):
        """Segmented extraction should reduce projected entity accumulation."""
        unsegmented = estimate_peak_context(
            turn_count=300,
            context_length=32768,
            max_tokens=4096,
            existing_entity_count=0,
            segment_size=0,
        )
        segmented = estimate_peak_context(
            turn_count=300,
            context_length=32768,
            max_tokens=4096,
            existing_entity_count=0,
            segment_size=100,
        )
        assert segmented["estimated_peak_tokens"] < unsegmented["estimated_peak_tokens"]
        assert segmented["projected_entity_count"] < unsegmented["projected_entity_count"]

    def test_none_context_length_skips_check(self):
        """When context_length is None, always report sufficient."""
        result = estimate_peak_context(
            turn_count=1000,
            context_length=None,
            max_tokens=4096,
            existing_entity_count=0,
        )
        assert result["sufficient"]
        assert result["context_length"] is None
        assert len(result["warnings"]) == 0

    def test_suggestions_include_segmentation(self):
        """Insufficient config with many turns should suggest segmentation."""
        result = estimate_peak_context(
            turn_count=300,
            context_length=8192,
            max_tokens=4096,
            existing_entity_count=0,
            segment_size=0,
        )
        assert any("segment" in s.lower() for s in result["suggestions"])

    def test_suggestions_include_increase_context(self):
        """Insufficient config with small context should suggest increasing it."""
        result = estimate_peak_context(
            turn_count=300,
            context_length=8192,
            max_tokens=4096,
            existing_entity_count=0,
        )
        assert any("context_length" in s for s in result["suggestions"])

    def test_tight_headroom_warns(self):
        """Headroom under 15% should produce a warning even if sufficient."""
        # With 50 turns, 0 entities: peak ~ 2000+100+600+300+4096 = 7096
        # context = 7800 → headroom = 700/7800 ≈ 9%
        result = estimate_peak_context(
            turn_count=50,
            context_length=7800,
            max_tokens=4096,
            existing_entity_count=0,
        )
        assert result["sufficient"]
        assert result["headroom_pct"] < 15
        assert len(result["warnings"]) > 0
        assert any(
            "tight" in w.lower() or "headroom" in w.lower()
            for w in result["warnings"]
        )


class TestPreflightContextCheck:
    """Tests for preflight_context_check() (the wrapper that prints)."""

    def test_returns_estimation_dict(self):
        """Should return the same dict as estimate_peak_context."""
        result = preflight_context_check(
            turn_count=100,
            context_length=32768,
            max_tokens=4096,
            model="test-model",
        )
        assert "sufficient" in result
        assert "estimated_peak_tokens" in result
        assert "warnings" in result
        assert "suggestions" in result

    def test_prints_warnings_to_stderr(self, capsys):
        """Should print warnings to stderr for insufficient configs."""
        preflight_context_check(
            turn_count=344,
            context_length=8192,
            max_tokens=4096,
            model="test-model",
        )
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "Pre-flight Context Check" in captured.err
        assert "test-model" in captured.err

    def test_no_output_when_sufficient(self, capsys):
        """Should not print anything for sufficient configs."""
        preflight_context_check(
            turn_count=50,
            context_length=1_000_000,
            max_tokens=4096,
        )
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_includes_suggestions_in_output(self, capsys):
        """Should print suggestions when config is insufficient."""
        preflight_context_check(
            turn_count=300,
            context_length=8192,
            max_tokens=4096,
            segment_size=0,
        )
        captured = capsys.readouterr()
        assert "Suggestions:" in captured.err

    def test_proceeds_warning_message(self, capsys):
        """Should tell user extraction will proceed despite warnings."""
        preflight_context_check(
            turn_count=344,
            context_length=8192,
            max_tokens=4096,
        )
        captured = capsys.readouterr()
        assert "will proceed" in captured.err
