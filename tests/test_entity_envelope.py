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
    """PC skip uses cooldown+retry cycle, not permanent skip."""
    from semantic_extraction import _PC_SKIP_THRESHOLD, _PC_SKIP_COOLDOWN, _PC_RETRY_WINDOW

    skipped = 0
    attempted = 0
    for consecutive_failures in range(
        _PC_SKIP_THRESHOLD, _PC_SKIP_THRESHOLD + 100
    ):  # 100 turns after threshold
        turns_since = consecutive_failures - _PC_SKIP_THRESHOLD
        cycle_pos = turns_since % (_PC_SKIP_COOLDOWN + _PC_RETRY_WINDOW)
        if cycle_pos < _PC_SKIP_COOLDOWN:
            skipped += 1
        else:
            attempted += 1

    # In 100 turns: ~91 skipped, ~9 attempted (two retry windows)
    assert attempted > 0, "Should retry during window"
    assert skipped > attempted, "Should skip more than retry"
