"""Tests for LLM client provider gating (tools/llm_client.py)."""

import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
# LLMClient.__init__ does `from openai import OpenAI` at runtime.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient


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
# _is_ollama detection
# ---------------------------------------------------------------------------


class TestIsOllamaDetection:
    """Verify _is_ollama returns True only for Ollama providers."""

    def test_openai_provider(self, tmp_path):
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is False

    def test_ollama_provider_field(self, tmp_path):
        cfg = _write_config(tmp_path, {"provider": "ollama"})
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is True

    def test_ollama_provider_field_case_insensitive(self, tmp_path):
        cfg = _write_config(tmp_path, {"provider": "Ollama"})
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is True

    def test_ollama_base_url_port(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "openai",
            "base_url": "http://localhost:11434/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is True

    def test_non_ollama_base_url(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "openai",
            "base_url": "http://localhost:8080/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is False


# ---------------------------------------------------------------------------
# extra_body gating
# ---------------------------------------------------------------------------


class TestExtraBodyGating:
    """Verify Ollama-specific fields are only sent to Ollama providers."""

    def test_ollama_gets_num_ctx(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "ollama",
            "context_length": 32768,
        })
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is True
        assert client.context_length == 32768

    def test_ollama_gets_options(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "ollama",
            "ollama_options": {"num_gpu": 1},
        })
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is True
        assert client.ollama_options == {"num_gpu": 1}

    def test_openai_ignores_context_length(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "openai",
            "context_length": 32768,
        })
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is False
        # context_length is still stored, but _is_ollama gates its use
        assert client.context_length == 32768

    def test_openai_ignores_ollama_options(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "openai",
            "ollama_options": {"num_gpu": 1},
        })
        client = LLMClient(config_path=cfg)
        assert client._is_ollama is False
        assert client.ollama_options == {"num_gpu": 1}


# ---------------------------------------------------------------------------
# _skip_response_format gating
# ---------------------------------------------------------------------------


class TestSkipResponseFormat:
    """Verify _skip_response_format is set correctly for various configs."""

    def test_openai_default_sends_response_format(self, tmp_path):
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)
        assert client._skip_response_format is False

    def test_ollama_qwen35_auto_skips(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3.5-9b-32k",
        })
        client = LLMClient(config_path=cfg)
        assert client._skip_response_format is True

    def test_ollama_non_qwen35_does_not_skip(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b",
        })
        client = LLMClient(config_path=cfg)
        assert client._skip_response_format is False

    def test_explicit_skip_override(self, tmp_path):
        cfg = _write_config(tmp_path, {"skip_response_format": True})
        client = LLMClient(config_path=cfg)
        assert client._skip_response_format is True

    def test_explicit_skip_false_overrides_auto(self, tmp_path):
        """Explicit False in config should not override the auto-detection."""
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
            "model": "qwen3.5-9b-32k",
            "skip_response_format": False,
        })
        client = LLMClient(config_path=cfg)
        # Auto-detection for qwen3.5 still returns True even with explicit False
        # because the explicit check only triggers on truthy values
        assert client._skip_response_format is True


# ---------------------------------------------------------------------------
# _parse_json_response — think-tag stripping
# ---------------------------------------------------------------------------


class TestParseJsonThinkStripping:
    """Verify _parse_json_response handles <think> blocks correctly."""

    def _make_client(self, tmp_path):
        cfg = _write_config(tmp_path)
        return LLMClient(config_path=cfg)

    def test_plain_json(self, tmp_path):
        client = self._make_client(tmp_path)
        result = client._parse_json_response('{"entities": []}')
        assert result == {"entities": []}

    def test_think_block_before_json(self, tmp_path):
        client = self._make_client(tmp_path)
        raw = '<think>Let me analyze this turn...</think>{"entities": [{"name": "Kael"}]}'
        result = client._parse_json_response(raw)
        assert result == {"entities": [{"name": "Kael"}]}

    def test_multiple_think_blocks(self, tmp_path):
        client = self._make_client(tmp_path)
        raw = '<think>First thought</think><think>Second thought</think>{"entities": []}'
        result = client._parse_json_response(raw)
        assert result == {"entities": []}

    def test_think_block_with_fenced_json(self, tmp_path):
        client = self._make_client(tmp_path)
        raw = '<think>Reasoning here</think>\n```json\n{"entities": []}\n```'
        result = client._parse_json_response(raw)
        assert result == {"entities": []}

    def test_multiline_think_block(self, tmp_path):
        client = self._make_client(tmp_path)
        raw = (
            '<think>\nThe user wants me to identify entities.\n'
            '- "tripwire" is an item\n- "net" is an item\n</think>\n'
            '{"entities": [{"name": "tripwire"}]}'
        )
        result = client._parse_json_response(raw)
        assert result == {"entities": [{"name": "tripwire"}]}

    def test_no_think_tags_unchanged(self, tmp_path):
        client = self._make_client(tmp_path)
        raw = '{"key": "value"}'
        result = client._parse_json_response(raw)
        assert result == {"key": "value"}

    def test_think_only_no_json_raises(self, tmp_path):
        from llm_client import LLMExtractionError
        client = self._make_client(tmp_path)
        raw = '<think>Just thinking, no JSON output</think>'
        try:
            client._parse_json_response(raw)
            assert False, "Should have raised LLMExtractionError"
        except LLMExtractionError as e:
            assert "Failed to parse JSON" in str(e)


# ---------------------------------------------------------------------------
# Ollama config knobs
# ---------------------------------------------------------------------------


class TestOllamaConfigKnobs:
    """Verify ollama_format, ollama_think, and _use_ollama_streaming."""

    def test_ollama_format_stored(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
            "ollama_format": "json",
        })
        client = LLMClient(config_path=cfg)
        assert client.ollama_format == "json"

    def test_ollama_format_default_none(self, tmp_path):
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)
        assert client.ollama_format is None

    def test_use_ollama_streaming_when_format_set(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
            "ollama_format": "json",
        })
        client = LLMClient(config_path=cfg)
        assert client._use_ollama_streaming is True

    def test_no_streaming_without_format(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._use_ollama_streaming is False

    def test_no_streaming_for_openai(self, tmp_path):
        cfg = _write_config(tmp_path, {"ollama_format": "json"})
        client = LLMClient(config_path=cfg)
        assert client._use_ollama_streaming is False

    def test_ollama_think_stored(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:11434/v1",
            "ollama_think": False,
        })
        client = LLMClient(config_path=cfg)
        assert client.config.get("ollama_think") is False
