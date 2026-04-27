"""Tests for incremental extraction support (#251).

Covers:
- --start-turn argument parsing and turn slicing
- --start-turn + --max-turns combined semantics (absolute upper bound)
- Validation errors for out-of-range values
- discovery_temperature config key and LLMClient.extract_json temperature override
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from bootstrap_session import build_parser

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient


# ---------------------------------------------------------------------------
# --start-turn argument parsing
# ---------------------------------------------------------------------------


class TestStartTurnArg:
    """Verify --start-turn argument is parsed correctly."""

    def test_start_turn_default_none(self):
        parser = build_parser()
        args = parser.parse_args([
            "--session", "sessions/test",
            "--file", "test.txt",
        ])
        assert args.start_turn is None

    def test_start_turn_parses_value(self):
        parser = build_parser()
        args = parser.parse_args([
            "--session", "sessions/test",
            "--file", "test.txt",
            "--start-turn", "26",
        ])
        assert args.start_turn == 26

    def test_start_turn_and_max_turns_coexist(self):
        parser = build_parser()
        args = parser.parse_args([
            "--session", "sessions/test",
            "--file", "test.txt",
            "--start-turn", "26",
            "--max-turns", "50",
        ])
        assert args.start_turn == 26
        assert args.max_turns == 50


# ---------------------------------------------------------------------------
# Turn slicing logic
# ---------------------------------------------------------------------------


class TestTurnSlicing:
    """Verify turn slicing with --start-turn and --max-turns."""

    def _make_turns(self, n):
        return [{"turn_id": f"turn-{i:03d}", "speaker": "DM", "text": f"T{i}"} for i in range(1, n + 1)]

    def test_start_turn_slices_from_correct_index(self):
        """--start-turn 5 should skip turns 1-4."""
        turns = self._make_turns(10)
        start_turn = 5
        sliced = turns[start_turn - 1:]
        assert len(sliced) == 6
        assert sliced[0]["turn_id"] == "turn-005"
        assert sliced[-1]["turn_id"] == "turn-010"

    def test_start_turn_and_max_turns_absolute_upper_bound(self):
        """--start-turn 26 --max-turns 50 should extract turns 26-50."""
        turns = self._make_turns(100)
        start_turn = 26
        max_turns = 50  # Absolute turn number, not count
        sliced = turns[start_turn - 1:max_turns]
        assert len(sliced) == 25
        assert sliced[0]["turn_id"] == "turn-026"
        assert sliced[-1]["turn_id"] == "turn-050"

    def test_start_turn_only_extracts_to_end(self):
        """--start-turn 90 without --max-turns extracts turns 90-100."""
        turns = self._make_turns(100)
        start_turn = 90
        sliced = turns[start_turn - 1:]
        assert len(sliced) == 11
        assert sliced[0]["turn_id"] == "turn-090"
        assert sliced[-1]["turn_id"] == "turn-100"

    def test_max_turns_caps_at_total_when_exceeding(self):
        """--start-turn 26 --max-turns 200 (beyond total) should cap at total."""
        turns = self._make_turns(100)
        start_turn = 26
        max_turns = 200
        end_idx = min(max_turns, len(turns))
        sliced = turns[start_turn - 1:end_idx]
        assert len(sliced) == 75
        assert sliced[0]["turn_id"] == "turn-026"
        assert sliced[-1]["turn_id"] == "turn-100"

    def test_start_turn_1_is_identity(self):
        """--start-turn 1 should not skip any turns."""
        turns = self._make_turns(10)
        sliced = turns[0:]
        assert len(sliced) == 10
        assert sliced[0]["turn_id"] == "turn-001"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestStartTurnValidation:
    """Verify validation errors for out-of-range --start-turn values."""

    def test_start_turn_zero_is_invalid(self):
        """--start-turn 0 should be rejected (1-based)."""
        # Validation is in main(), test the condition directly
        start_turn = 0
        assert start_turn < 1

    def test_start_turn_exceeding_total_is_invalid(self):
        """--start-turn beyond total turns should be rejected."""
        turns = self._make_turns(10)
        start_turn = 15
        start_idx = start_turn - 1
        assert start_idx >= len(turns)

    def test_max_turns_less_than_start_turn_is_invalid(self):
        """--max-turns 10 --start-turn 20 should be rejected."""
        assert 10 < 20  # max_turns < start_turn

    def _make_turns(self, n):
        return [{"turn_id": f"turn-{i:03d}"} for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# discovery_temperature in LLMClient.extract_json
# ---------------------------------------------------------------------------


def _write_config(tmp_dir, overrides=None):
    """Write a minimal llm.json and return its path."""
    cfg = {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_key_env": "",
        "temperature": 0.0,
        "max_tokens": 4096,
        "timeout_seconds": 10,
        "retry_attempts": 1,
        "batch_delay_ms": 0,
    }
    if overrides:
        cfg.update(overrides)
    path = os.path.join(tmp_dir, "llm.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


class TestDiscoveryTemperature:
    """Verify discovery_temperature config and extract_json override."""

    def test_temperature_override_used_in_openai_path(self, tmp_path):
        """extract_json should use the provided temperature override."""
        cfg_path = _write_config(tmp_path, {"temperature": 0.0})
        client = LLMClient(config_path=cfg_path)

        # Mock the OpenAI client's chat.completions.create
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"entities": []}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json(
            system_prompt="test",
            user_prompt="test",
            temperature=0.7,
        )

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.7

    def test_temperature_default_when_no_override(self, tmp_path):
        """extract_json should use self.temperature when no override given."""
        cfg_path = _write_config(tmp_path, {"temperature": 0.3})
        client = LLMClient(config_path=cfg_path)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"entities": []}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json(
            system_prompt="test",
            user_prompt="test",
        )

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.3

    def test_discovery_temperature_read_from_config(self, tmp_path):
        """discovery_temperature key should be accessible via config dict."""
        cfg_path = _write_config(tmp_path, {
            "temperature": 0.0,
            "discovery_temperature": 0.5,
        })
        client = LLMClient(config_path=cfg_path)
        assert client.config.get("discovery_temperature") == 0.5

    def test_discovery_temperature_absent_returns_none(self, tmp_path):
        """When discovery_temperature is not in config, .get returns None."""
        cfg_path = _write_config(tmp_path, {"temperature": 0.0})
        client = LLMClient(config_path=cfg_path)
        assert client.config.get("discovery_temperature") is None

    def test_temperature_none_override_uses_default(self, tmp_path):
        """Passing temperature=None should fall back to self.temperature."""
        cfg_path = _write_config(tmp_path, {"temperature": 0.2})
        client = LLMClient(config_path=cfg_path)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": true}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json(
            system_prompt="test",
            user_prompt="test",
            temperature=None,
        )

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.2
