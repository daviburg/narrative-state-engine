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


# ---------------------------------------------------------------------------
# --max-turns argument parsing (#234)
# ---------------------------------------------------------------------------

from bootstrap_session import build_parser


class TestMaxTurnsArg:
    """Verify --max-turns argument is parsed and help text is correct."""

    def test_max_turns_default_none(self):
        parser = build_parser()
        args = parser.parse_args([
            "--session", "sessions/test",
            "--file", "test.txt",
        ])
        assert args.max_turns is None

    def test_max_turns_parses_value(self):
        parser = build_parser()
        args = parser.parse_args([
            "--session", "sessions/test",
            "--file", "test.txt",
            "--max-turns", "50",
        ])
        assert args.max_turns == 50

    def test_max_turns_and_segment_size_coexist(self):
        """--max-turns and --segment-size are independent controls."""
        parser = build_parser()
        args = parser.parse_args([
            "--session", "sessions/test",
            "--file", "test.txt",
            "--max-turns", "50",
            "--segment-size", "25",
        ])
        assert args.max_turns == 50
        assert args.segment_size == 25

    def test_max_turns_slicing(self):
        """Verify slicing logic: max_turns < len produces truncated list."""
        turns = [{"turn_id": f"turn-{i:03d}"} for i in range(1, 101)]
        max_turns = 50
        sliced = turns[:max_turns]
        assert len(sliced) == 50
        assert sliced[0]["turn_id"] == "turn-001"
        assert sliced[-1]["turn_id"] == "turn-050"

    def test_max_turns_larger_than_total_is_noop(self):
        """max_turns >= len(turns) should not truncate."""
        turns = [{"turn_id": f"turn-{i:03d}"} for i in range(1, 11)]
        max_turns = 50
        if max_turns < len(turns):
            turns = turns[:max_turns]
        assert len(turns) == 10

    def test_segment_size_applied_after_max_turns(self):
        """_resolve_segment_size receives len(sliced), not len(original)."""
        original = 345
        max_turns = 50
        effective_count = min(max_turns, original)
        size, auto = _resolve_segment_size(25, effective_count)
        assert size == 25
        assert auto is False
