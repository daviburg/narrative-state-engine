"""Tests for Gemini Flash provider configuration and client behavior (#190)."""
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


def _write_config(tmp_dir, overrides=None):
    """Write a minimal llm.json and return its path."""
    cfg = {
        "provider": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
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


class TestGeminiProviderConfig:
    """Verify Gemini config loads correctly and is not treated as Ollama."""

    def test_gemini_not_detected_as_ollama(self, tmp_path):
        path = _write_config(str(tmp_path))
        client = LLMClient(path)
        assert not client._is_ollama

    def test_gemini_model_name(self, tmp_path):
        path = _write_config(str(tmp_path))
        client = LLMClient(path)
        assert client.model == "gemini-2.5-flash"

    def test_gemini_base_url_preserved(self, tmp_path):
        path = _write_config(str(tmp_path))
        client = LLMClient(path)
        assert "generativelanguage.googleapis.com" in client.config["base_url"]

    def test_gemini_api_key_env_loading(self, tmp_path):
        path = _write_config(str(tmp_path), {"api_key_env": "GEMINI_API_KEY"})
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-12345"}):
            client = LLMClient(path)
            assert client.model == "gemini-2.5-flash"

    def test_gemini_api_key_missing_raises(self, tmp_path):
        path = _write_config(str(tmp_path), {"api_key_env": "GEMINI_API_KEY"})
        # Ensure the var is NOT set
        env = os.environ.copy()
        env.pop("GEMINI_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            try:
                LLMClient(path)
                assert False, "Should have raised"
            except Exception as e:
                assert "GEMINI_API_KEY" in str(e)

    def test_gemini_context_length_not_injected(self, tmp_path):
        """context_length should be stored but not cause Ollama extra_body."""
        path = _write_config(str(tmp_path), {"context_length": 1048576})
        client = LLMClient(path)
        assert client.context_length == 1048576
        assert not client._is_ollama

    def test_gemini_with_overrides(self, tmp_path):
        path = _write_config(str(tmp_path))
        client = LLMClient(path, overrides={"model": "gemini-2.5-flash-lite"})
        assert client.model == "gemini-2.5-flash-lite"

    def test_gemini_pc_max_tokens_default(self, tmp_path):
        path = _write_config(str(tmp_path))
        client = LLMClient(path)
        assert client.pc_max_tokens == 4096  # Falls back to max_tokens

    def test_gemini_pc_max_tokens_explicit(self, tmp_path):
        path = _write_config(str(tmp_path), {"pc_max_tokens": 8192})
        client = LLMClient(path)
        assert client.pc_max_tokens == 8192


class TestGeminiExtractJsonNoOllamaBody:
    """Ensure extract_json does NOT inject Ollama extra_body for Gemini."""

    def test_no_extra_body_for_gemini(self, tmp_path):
        path = _write_config(str(tmp_path), {"context_length": 1048576})
        client = LLMClient(path)

        # Mock the underlying OpenAI client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"entities": []}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        result = client.extract_json("system", "user")

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert "extra_body" not in call_kwargs
        assert result == {"entities": []}

    def test_response_format_json_object_sent(self, tmp_path):
        path = _write_config(str(tmp_path))
        client = LLMClient(path)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"test": true}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        client.extract_json("system", "user")

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["model"] == "gemini-2.5-flash"


class TestGeminiGenerateTextNoOllamaBody:
    """Ensure generate_text does NOT inject Ollama extra_body for Gemini."""

    def test_no_extra_body_for_gemini_text(self, tmp_path):
        path = _write_config(str(tmp_path), {"context_length": 1048576})
        client = LLMClient(path)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Some text response"
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        result = client.generate_text("system", "user")

        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert "extra_body" not in call_kwargs
        assert result == "Some text response"
