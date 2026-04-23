"""Tests for bootstrap_session segment-size auto-default behavior (#197)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from bootstrap_session import _resolve_segment_size


def test_auto_default_enabled_for_large_sessions_when_unset():
    """Unset --segment-size should auto-default to 100 for >150 turns."""
    size, auto_enabled = _resolve_segment_size(None, 151)
    assert size == 100
    assert auto_enabled is True


def test_auto_default_not_enabled_at_threshold():
    """Threshold is exclusive: 150 turns should keep segmentation disabled."""
    size, auto_enabled = _resolve_segment_size(None, 150)
    assert size == 0
    assert auto_enabled is False


def test_explicit_zero_disables_segmentation():
    """Explicit --segment-size 0 should remain disabled and skip auto-defaulting."""
    size, auto_enabled = _resolve_segment_size(0, 400)
    assert size == 0
    assert auto_enabled is False


def test_explicit_segment_size_is_preserved():
    """Explicit non-zero segment size should always be respected."""
    size, auto_enabled = _resolve_segment_size(120, 500)
    assert size == 120
    assert auto_enabled is False
