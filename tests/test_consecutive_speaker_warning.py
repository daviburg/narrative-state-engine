"""Tests for consecutive same-speaker turn detection."""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.bootstrap_session import Turn


# Helper to capture stderr
def _capture_warnings(turns):
    from tools.bootstrap_session import _warn_consecutive_speakers
    buf = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = buf
    try:
        _warn_consecutive_speakers(turns)
    finally:
        sys.stderr = old_stderr
    return buf.getvalue()


def test_no_warning_on_alternating_speakers():
    turns = [
        Turn(1, "player", "I look around."),
        Turn(2, "dm", "You see a door."),
        Turn(3, "player", "I open it."),
    ]
    output = _capture_warnings(turns)
    assert output == ""


def test_warns_consecutive_player_turns():
    turns = [
        Turn(1, "player", "I look around."),
        Turn(2, "player", "I also check the ceiling."),
        Turn(3, "dm", "You see a door."),
    ]
    output = _capture_warnings(turns)
    assert "turns 1 and 2" in output
    assert "player" in output


def test_warns_consecutive_dm_turns():
    turns = [
        Turn(1, "dm", "The room is dark."),
        Turn(2, "dm", "A sound echoes."),
        Turn(3, "player", "I listen."),
    ]
    output = _capture_warnings(turns)
    assert "turns 1 and 2" in output
    assert "dm" in output


def test_warns_multiple_consecutive_runs():
    turns = [
        Turn(1, "player", "A"),
        Turn(2, "player", "B"),
        Turn(3, "player", "C"),
        Turn(4, "dm", "D"),
    ]
    output = _capture_warnings(turns)
    assert "turns 1 and 2" in output
    assert "turns 2 and 3" in output
