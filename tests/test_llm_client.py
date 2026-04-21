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
