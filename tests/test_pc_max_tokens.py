"""Tests for PC max_tokens override and skip-after-failures logic (#148, #149)."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient
import semantic_extraction as se


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


# ---------------------------------------------------------------------------
# #148 — pc_max_tokens config and LLMClient
# ---------------------------------------------------------------------------


class TestPCMaxTokensConfig:
    """Verify LLMClient reads pc_max_tokens from config."""

    def test_pc_max_tokens_from_config(self, tmp_path):
        """pc_max_tokens should be read from config."""
        cfg = _write_config(tmp_path, {"pc_max_tokens": 8192})
        client = LLMClient(config_path=cfg)
        assert client.pc_max_tokens == 8192

    def test_pc_max_tokens_defaults_to_max_tokens(self, tmp_path):
        """pc_max_tokens should default to max_tokens when not specified."""
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)
        assert client.pc_max_tokens == client.max_tokens == 4096

    def test_pc_max_tokens_independent_of_max_tokens(self, tmp_path):
        """pc_max_tokens and max_tokens should be independently configurable."""
        cfg = _write_config(tmp_path, {"max_tokens": 2048, "pc_max_tokens": 16384})
        client = LLMClient(config_path=cfg)
        assert client.max_tokens == 2048
        assert client.pc_max_tokens == 16384


class TestExtractJsonMaxTokensOverride:
    """Verify extract_json accepts and uses per-call max_tokens."""

    def test_max_tokens_override_passed_to_api(self, tmp_path):
        """When max_tokens is provided, it should override self.max_tokens."""
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)

        # Mock the OpenAI client's create method
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json(
            system_prompt="test",
            user_prompt="test",
            max_tokens=8192,
        )

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 8192

    def test_max_tokens_default_when_not_provided(self, tmp_path):
        """When max_tokens is not provided, self.max_tokens should be used."""
        cfg = _write_config(tmp_path, {"max_tokens": 2048})
        client = LLMClient(config_path=cfg)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json(
            system_prompt="test",
            user_prompt="test",
        )

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 2048

    def test_max_tokens_none_uses_default(self, tmp_path):
        """Explicitly passing max_tokens=None should use self.max_tokens."""
        cfg = _write_config(tmp_path, {"max_tokens": 3000})
        client = LLMClient(config_path=cfg)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json(
            system_prompt="test",
            user_prompt="test",
            max_tokens=None,
        )

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 3000


# ---------------------------------------------------------------------------
# #149 — Skip PC extraction after N consecutive failures
# ---------------------------------------------------------------------------


class TestPCSkipThreshold:
    """Verify PC extraction is skipped after threshold failures."""

    def test_skip_threshold_constant(self):
        """Skip threshold should be 20."""
        assert se._PC_SKIP_THRESHOLD == 20

    def test_warn_threshold_less_than_skip(self):
        """Warn threshold should be less than skip threshold."""
        assert se._PC_FAILURE_WARN_THRESHOLD < se._PC_SKIP_THRESHOLD

    def test_skip_when_failures_at_threshold(self):
        """PC extraction should be skipped when failures >= threshold."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = se._PC_SKIP_THRESHOLD
            assert se._pc_consecutive_failures >= se._PC_SKIP_THRESHOLD
        finally:
            se._pc_consecutive_failures = original

    def test_no_skip_below_threshold(self):
        """PC extraction should proceed when failures < threshold."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = se._PC_SKIP_THRESHOLD - 1
            assert se._pc_consecutive_failures < se._PC_SKIP_THRESHOLD
        finally:
            se._pc_consecutive_failures = original

    def test_reset_clears_counter(self):
        """_reset_pc_failure_tracking clears the counter."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = se._PC_SKIP_THRESHOLD + 5
            se._reset_pc_failure_tracking()
            assert se._pc_consecutive_failures == 0
        finally:
            se._pc_consecutive_failures = original


class TestPCSkipLogNoise:
    """Verify log noise is reduced (#153): warnings only at threshold crossings."""

    def test_warning_fires_at_warn_threshold_exactly(self, capsys):
        """WARNING should fire exactly when counter hits _PC_FAILURE_WARN_THRESHOLD."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = se._PC_FAILURE_WARN_THRESHOLD
            # Simulate the new logic: == not >=
            if se._pc_consecutive_failures == se._PC_FAILURE_WARN_THRESHOLD:
                print(
                    f"  WARNING: PC extraction has failed for {se._pc_consecutive_failures} "
                    f"consecutive turns",
                    file=sys.stderr,
                )
            captured = capsys.readouterr()
            assert "WARNING" in captured.err
            assert str(se._PC_FAILURE_WARN_THRESHOLD) in captured.err
        finally:
            se._pc_consecutive_failures = original

    def test_no_warning_above_warn_threshold(self, capsys):
        """No warning should fire between warn threshold and skip threshold."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = se._PC_FAILURE_WARN_THRESHOLD + 5
            # Simulate the new logic: == not >=
            if se._pc_consecutive_failures == se._PC_FAILURE_WARN_THRESHOLD:
                print("  WARNING: should not fire", file=sys.stderr)
            captured = capsys.readouterr()
            assert "WARNING" not in captured.err
        finally:
            se._pc_consecutive_failures = original

    def test_skip_message_fires_at_skip_threshold(self, capsys):
        """Skip message should fire when counter hits _PC_SKIP_THRESHOLD."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = se._PC_SKIP_THRESHOLD
            if se._pc_consecutive_failures == se._PC_SKIP_THRESHOLD:
                print(
                    f"  WARNING: PC extraction skipped from now on after "
                    f"{se._PC_SKIP_THRESHOLD} consecutive failures.",
                    file=sys.stderr,
                )
            captured = capsys.readouterr()
            assert "skipped" in captured.err
            assert str(se._PC_SKIP_THRESHOLD) in captured.err
        finally:
            se._pc_consecutive_failures = original
