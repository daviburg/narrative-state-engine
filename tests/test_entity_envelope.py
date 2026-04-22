"""Tests for entity envelope unwrapping and PC skip cooldown logic."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _unwrap_entity_response


def test_unwrap_entity_response_envelope():
    """Standard {"entity": {...}} format."""
    result = {"entity": {"id": "char-test", "type": "character", "name": "Test"}}
    assert _unwrap_entity_response(result) == {"id": "char-test", "type": "character", "name": "Test"}


def test_unwrap_entity_response_flat():
    """LLM returns entity directly without envelope."""
    result = {"id": "char-test", "type": "character", "name": "Test", "identity": "A test character"}
    unwrapped = _unwrap_entity_response(result)
    assert unwrapped is not None
    assert unwrapped["id"] == "char-test"
    assert unwrapped is not result  # should be a copy


def test_unwrap_entity_response_missing():
    """LLM returns something with no entity-like structure."""
    result = {"error": "something went wrong"}
    assert _unwrap_entity_response(result) is None


def test_unwrap_entity_response_entity_is_not_dict():
    """Edge case: {"entity": "some string"} should not be treated as valid."""
    result = {"entity": "not a dict"}
    assert _unwrap_entity_response(result) is None


def test_pc_skip_cooldown_cycle():
    """PC skip uses a repeating cooldown+retry progression, not a permanent skip.

    Simulates realistic state progression: _should_skip_pc is called each turn
    with a ``turns_since_cooldown`` counter that advances every turn once the
    threshold is reached.  Consecutive failures stay constant during skipped
    turns (no extraction attempted) and only increment during retry-window
    turns (when extraction is attempted but fails).
    """
    from semantic_extraction import (
        _should_skip_pc,
        _PC_SKIP_THRESHOLD,
        _PC_SKIP_COOLDOWN,
        _PC_RETRY_WINDOW,
    )

    cycle_len = _PC_SKIP_COOLDOWN + _PC_RETRY_WINDOW
    # Simulate 2 full cycles after threshold
    total_turns = cycle_len * 2
    consecutive_failures = _PC_SKIP_THRESHOLD
    turns_since_cooldown = 0
    sequence = []  # True = attempted, False = skipped

    for _ in range(total_turns):
        skip = _should_skip_pc(consecutive_failures, turns_since_cooldown)
        sequence.append(not skip)
        turns_since_cooldown += 1
        if not skip:
            # Attempt extraction — simulate failure
            consecutive_failures += 1

    # First cooldown window: all skipped
    assert sequence[:_PC_SKIP_COOLDOWN] == [False] * _PC_SKIP_COOLDOWN
    # First retry window: all attempted
    assert sequence[_PC_SKIP_COOLDOWN:cycle_len] == [True] * _PC_RETRY_WINDOW
    # Second cooldown window
    assert sequence[cycle_len:cycle_len + _PC_SKIP_COOLDOWN] == [False] * _PC_SKIP_COOLDOWN
    # Second retry window
    assert sequence[cycle_len + _PC_SKIP_COOLDOWN:cycle_len * 2] == [True] * _PC_RETRY_WINDOW

    attempted = sum(sequence)
    skipped = len(sequence) - attempted
    assert attempted == _PC_RETRY_WINDOW * 2
    assert skipped == _PC_SKIP_COOLDOWN * 2


def test_pc_skip_resets_on_success():
    """A successful extraction during the retry window resets cooldown."""
    from semantic_extraction import _should_skip_pc, _PC_SKIP_THRESHOLD

    # At threshold, first turn should be skipped (cooldown position 0)
    assert _should_skip_pc(_PC_SKIP_THRESHOLD, 0) is True
    # Below threshold, never skip
    assert _should_skip_pc(_PC_SKIP_THRESHOLD - 1, 0) is False
    assert _should_skip_pc(0, 0) is False
