"""Tests for LLM client provider gating (tools/llm_client.py)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
# LLMClient.__init__ does `from openai import OpenAI` at runtime.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient, LLMTruncationError, LLMExtractionError, QuotaExhaustedError


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
# Fallback JSON extraction (inline preamble handling)
# ---------------------------------------------------------------------------

class TestFallbackJsonExtraction:
    """Verify _parse_json_response fallback handles inline preamble text."""

    def _make_client(self, tmp_path):
        cfg = _write_config(tmp_path)
        return LLMClient(config_path=cfg)

    def test_preamble_before_json(self, tmp_path):
        """Model emits reasoning text before JSON object."""
        client = self._make_client(tmp_path)
        raw = 'Okay, let\'s analyze this turn.\n\n{"entities": [{"name": "Elf"}]}'
        result = client._parse_json_response(raw)
        assert result == {"entities": [{"name": "Elf"}]}

    def test_preamble_with_braces_before_json(self, tmp_path):
        """Preamble contains non-JSON braces (e.g. {foo}) before real JSON."""
        client = self._make_client(tmp_path)
        raw = 'The structure {unclear} needs analysis.\n{"entities": []}'
        result = client._parse_json_response(raw)
        assert result == {"entities": []}

    def test_braces_in_json_strings(self, tmp_path):
        """JSON containing brace characters inside string values."""
        client = self._make_client(tmp_path)
        raw = 'Preamble text\n{"desc": "a {curly} thing", "count": 1}'
        result = client._parse_json_response(raw)
        assert result == {"desc": "a {curly} thing", "count": 1}

    def test_trailing_text_after_json(self, tmp_path):
        """Model emits text after JSON — should still parse the object."""
        client = self._make_client(tmp_path)
        raw = '{"entities": []}\n\nHope that helps!'
        result = client._parse_json_response(raw)
        assert result == {"entities": []}

    def test_no_json_at_all_raises_with_details(self, tmp_path):
        """No JSON anywhere — error includes original parse details."""
        from llm_client import LLMExtractionError
        client = self._make_client(tmp_path)
        raw = 'This is just plain text with no JSON at all.'
        try:
            client._parse_json_response(raw)
            assert False, "Should have raised"
        except LLMExtractionError as e:
            assert "Initial parse error" in str(e)
            assert "no valid JSON object found" in str(e)

    def test_invalid_json_after_preamble_raises_with_details(self, tmp_path):
        """Malformed JSON after preamble — error includes candidate error."""
        from llm_client import LLMExtractionError
        client = self._make_client(tmp_path)
        raw = 'Here is the result:\n{"entities": [INVALID]}'
        try:
            client._parse_json_response(raw)
            assert False, "Should have raised"
        except LLMExtractionError as e:
            assert "Initial parse error" in str(e)


# ---------------------------------------------------------------------------
# Malformed confidence repair (#290)
# ---------------------------------------------------------------------------

class TestMalformedConfidenceRepair:
    """Verify _parse_json_response fixes malformed confidence values."""

    def _make_client(self, tmp_path):
        cfg = _write_config(tmp_path)
        return LLMClient(config_path=cfg)

    def test_confidence_range_0_1_0(self, tmp_path):
        """'confidence': 0-1.0 → 1.0 (right value ≤1, use it directly)."""
        client = self._make_client(tmp_path)
        raw = '{"entities": [{"name": "A", "confidence": 0-1.0}]}'
        result = client._parse_json_response(raw)
        assert result["entities"][0]["confidence"] == 1.0

    def test_confidence_range_0_0_95(self, tmp_path):
        """'confidence': 0-0.95 → 0.95."""
        client = self._make_client(tmp_path)
        raw = '{"entities": [{"name": "A", "confidence": 0-0.95}]}'
        result = client._parse_json_response(raw)
        assert result["entities"][0]["confidence"] == 0.95

    def test_confidence_0_9_mistyped_decimal(self, tmp_path):
        """'confidence': 0-9 → 0.9 (right > 1, interpret as 0.X)."""
        client = self._make_client(tmp_path)
        raw = '{"entities": [{"name": "A", "confidence": 0-9}]}'
        result = client._parse_json_response(raw)
        assert result["entities"][0]["confidence"] == 0.9

    def test_confidence_0_85_mistyped(self, tmp_path):
        """'confidence': 0-85 → 0.85."""
        client = self._make_client(tmp_path)
        raw = '{"entities": [{"name": "A", "confidence": 0-85}]}'
        result = client._parse_json_response(raw)
        assert result["entities"][0]["confidence"] == 0.85

    def test_valid_confidence_not_modified(self, tmp_path):
        """Normal confidence values are not touched."""
        client = self._make_client(tmp_path)
        raw = '{"entities": [{"name": "A", "confidence": 0.95}]}'
        result = client._parse_json_response(raw)
        assert result["entities"][0]["confidence"] == 0.95

    def test_multiple_malformed_in_one_response(self, tmp_path):
        """Multiple malformed confidences in one response all get fixed."""
        client = self._make_client(tmp_path)
        raw = '{"entities": [{"name": "A", "confidence": 0-1.0}, {"name": "B", "confidence": 0-9}]}'
        result = client._parse_json_response(raw)
        assert result["entities"][0]["confidence"] == 1.0
        assert result["entities"][1]["confidence"] == 0.9

    def test_non_zero_left_side_not_repaired(self, tmp_path):
        """Patterns like 1-2 are not repaired — only 0-X is a known defect."""
        client = self._make_client(tmp_path)
        # 1-2 is not valid JSON either, but the regex should leave it alone
        # so the original parse error surfaces instead of silent corruption
        raw = '{"entities": [{"name": "A", "confidence": 1-2}]}'
        import pytest
        with pytest.raises(Exception):
            client._parse_json_response(raw)


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


# ---------------------------------------------------------------------------
# LLMTruncationError detection
# ---------------------------------------------------------------------------


class TestTruncationDetection:
    """Verify extract_json raises LLMTruncationError on finish_reason=length."""

    def test_finish_reason_length_raises_truncation_error(self, tmp_path):
        cfg = _write_config(tmp_path, {"retry_attempts": 1})
        client = LLMClient(config_path=cfg)

        # Mock the OpenAI response with finish_reason="length"
        mock_choice = MagicMock()
        mock_choice.message.content = '{"entities": [{"id": "char-foo"'
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch.object(client.client.chat.completions, "create",
                          return_value=mock_response):
            with pytest.raises(LLMTruncationError) as exc_info:
                client.extract_json(
                    system_prompt="test",
                    user_prompt="test",
                )
            assert exc_info.value.partial_text == '{"entities": [{"id": "char-foo"'
            assert "finish_reason=length" in str(exc_info.value)

    def test_finish_reason_stop_does_not_raise(self, tmp_path):
        cfg = _write_config(tmp_path, {"retry_attempts": 1})
        client = LLMClient(config_path=cfg)

        mock_choice = MagicMock()
        mock_choice.message.content = '{"entities": []}'
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch.object(client.client.chat.completions, "create",
                          return_value=mock_response):
            result = client.extract_json(
                system_prompt="test",
                user_prompt="test",
            )
            assert result == {"entities": []}

    def test_truncation_error_not_retried(self, tmp_path):
        """LLMTruncationError should propagate immediately, not retry."""
        cfg = _write_config(tmp_path, {"retry_attempts": 3})
        client = LLMClient(config_path=cfg)

        mock_choice = MagicMock()
        mock_choice.message.content = '{"partial": true'
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        call_count = 0

        def counting_create(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch.object(client.client.chat.completions, "create",
                          side_effect=counting_create):
            with pytest.raises(LLMTruncationError):
                client.extract_json(
                    system_prompt="test",
                    user_prompt="test",
                )
            # Should only be called once — no retries
            assert call_count == 1

    def test_truncation_error_records_stats(self, tmp_path):
        """LLMTruncationError should be recorded in RetryStats before re-raising."""
        cfg = _write_config(tmp_path, {"retry_attempts": 3})
        client = LLMClient(config_path=cfg)

        mock_choice = MagicMock()
        mock_choice.message.content = '{"partial": true'
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch.object(client.client.chat.completions, "create",
                          return_value=mock_response):
            with pytest.raises(LLMTruncationError):
                client.extract_json(
                    system_prompt="test",
                    user_prompt="test",
                )
            assert "truncation" in client.stats.errors_by_status

    def test_quota_error_records_stats(self, tmp_path):
        """QuotaExhaustedError should be recorded in RetryStats before re-raising."""
        cfg = _write_config(tmp_path, {
            "retry_attempts": 3,
            "consecutive_rate_limit_threshold": 2,
        })
        client = LLMClient(config_path=cfg)

        # Simulate 429 errors that trigger QuotaExhaustedError
        err = Exception("rate limited")
        err.status_code = 429
        err.response = MagicMock()
        err.response.status_code = 429
        err.response.headers = {}

        with patch.object(client.client.chat.completions, "create",
                          side_effect=err):
            with pytest.raises(QuotaExhaustedError):
                client.extract_json(
                    system_prompt="test",
                    user_prompt="test",
                )
            assert "quota_exhausted" in client.stats.errors_by_status


# ---------------------------------------------------------------------------
# Fallback provider
# ---------------------------------------------------------------------------


class TestFallbackProvider:
    """Verify fallback LLM provider is used when primary exhausts retries."""

    def test_fallback_client_initialized(self, tmp_path):
        """Fallback client is created when config has a fallback block."""
        cfg = _write_config(tmp_path, overrides={
            "fallback": {
                "base_url": "http://localhost:8081/v1",
                "model": "fallback-model",
                "timeout_seconds": 300,
            }
        })
        client = LLMClient(config_path=cfg)
        assert client._fallback_client is not None
        assert client._fallback_client.model == "fallback-model"

    def test_no_fallback_without_config(self, tmp_path):
        """No fallback client when config lacks a fallback block."""
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)
        assert client._fallback_client is None

    def test_fallback_called_on_primary_failure(self, tmp_path):
        """Fallback extract_json is called when primary exhausts retries."""
        cfg = _write_config(tmp_path, overrides={
            "retry_attempts": 1,
            "fallback": {
                "base_url": "http://localhost:8081/v1",
                "model": "fallback-model",
            }
        })
        client = LLMClient(config_path=cfg)

        # Primary always fails
        with patch.object(client.client.chat.completions, "create",
                          side_effect=Exception("primary failed")):
            # Fallback succeeds
            with patch.object(client._fallback_client, "extract_json",
                              return_value={"entities": []}) as mock_fb:
                result = client.extract_json(
                    system_prompt="sys", user_prompt="user"
                )
                assert result == {"entities": []}
                mock_fb.assert_called_once()

    def test_fallback_failure_raises_combined_error(self, tmp_path):
        """If both primary and fallback fail, error includes both messages."""
        cfg = _write_config(tmp_path, overrides={
            "retry_attempts": 1,
            "fallback": {
                "base_url": "http://localhost:8081/v1",
                "model": "fallback-model",
            }
        })
        client = LLMClient(config_path=cfg)

        with patch.object(client.client.chat.completions, "create",
                          side_effect=Exception("primary failed")):
            with patch.object(client._fallback_client, "extract_json",
                              side_effect=LLMExtractionError("fallback failed")):
                with pytest.raises(LLMExtractionError, match="Fallback"):
                    client.extract_json(
                        system_prompt="sys", user_prompt="user"
                    )


# ---------------------------------------------------------------------------
# base_url override vs base_urls suppression
# ---------------------------------------------------------------------------


class TestBaseUrlOverrideSuppression:
    """Verify base_url in overrides only suppresses base_urls when appropriate."""

    def test_base_url_override_suppresses_base_urls(self, tmp_path):
        """base_url override without base_urls drops config base_urls."""
        cfg = _write_config(tmp_path, overrides={
            "base_urls": [
                "http://localhost:8080/v1",
                "http://localhost:8081/v1",
            ],
        })
        # Override with single base_url — should suppress base_urls
        client = LLMClient(config_path=cfg, overrides={
            "base_url": "http://localhost:9999/v1",
        })
        # Only one client (the override), not the base_urls list
        assert len(client._clients) == 1
        assert client._base_urls == ["http://localhost:9999/v1"]

    def test_base_url_with_base_urls_in_overrides_preserves_list(self, tmp_path):
        """base_url + base_urls in overrides preserves base_urls list."""
        cfg = _write_config(tmp_path)
        # Overrides contain both base_url and base_urls (like fallback init)
        client = LLMClient(config_path=cfg, overrides={
            "base_url": "http://localhost:8080/v1",
            "base_urls": [
                "http://localhost:8080/v1",
                "http://localhost:8081/v1",
            ],
        })
        # base_urls should be preserved — two clients
        assert len(client._clients) == 2
        assert client._base_urls == [
            "http://localhost:8080/v1",
            "http://localhost:8081/v1",
        ]

    def test_fallback_with_base_urls_preserves_multi_endpoint(self, tmp_path):
        """Fallback block with base_urls gets multi-endpoint support."""
        cfg = _write_config(tmp_path, overrides={
            "fallback": {
                "base_url": "http://localhost:8081/v1",
                "base_urls": [
                    "http://localhost:8081/v1",
                    "http://localhost:8082/v1",
                ],
                "model": "fallback-model",
            }
        })
        client = LLMClient(config_path=cfg)
        fb = client._fallback_client
        assert fb is not None
        assert len(fb._clients) == 2
        assert fb._base_urls == [
            "http://localhost:8081/v1",
            "http://localhost:8082/v1",
        ]


# ---------------------------------------------------------------------------
# Sampler params threaded into the request body (#471)
# ---------------------------------------------------------------------------


def _mock_ok_response(content='{"entities": []}'):
    """Build a MagicMock chat-completion response with the given content."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_choice.finish_reason = "stop"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


def _capture_request_kwargs(client):
    """Patch the client's create() to capture and return the request kwargs.

    Returns the dict the kwargs are recorded into; run extract_json after.
    """
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _mock_ok_response()

    return captured, _create


class TestSamplerParamsInRequestBody:
    """Verify explicit sampler params (#471) are threaded into the request."""

    def test_sampler_params_present_when_configured(self, tmp_path):
        """top_k/top_p/min_p/seed appear in the request when set in config."""
        cfg = _write_config(tmp_path, {
            "retry_attempts": 1,
            "temperature": 0,
            "top_k": 1,
            "top_p": 1.0,
            "min_p": 0.0,
            "seed": 42,
        })
        client = LLMClient(config_path=cfg)
        captured, fake_create = _capture_request_kwargs(client)

        with patch.object(client.client.chat.completions, "create",
                          side_effect=fake_create):
            client.extract_json(system_prompt="sys", user_prompt="user")

        # top_p and seed are native OpenAI params
        assert captured["top_p"] == 1.0
        assert captured["seed"] == 42
        # top_k and min_p ride in extra_body
        assert captured["extra_body"]["top_k"] == 1
        assert captured["extra_body"]["min_p"] == 0.0

    def test_temperature_zero_is_sent(self, tmp_path):
        """temperature 0 is sent in the request body (not dropped/defaulted)."""
        cfg = _write_config(tmp_path, {"retry_attempts": 1, "temperature": 0})
        client = LLMClient(config_path=cfg)
        captured, fake_create = _capture_request_kwargs(client)

        with patch.object(client.client.chat.completions, "create",
                          side_effect=fake_create):
            client.extract_json(system_prompt="sys", user_prompt="user")

        assert captured["temperature"] == 0

    def test_sampler_params_absent_when_not_configured(self, tmp_path):
        """Backward compat: no sampler keys in body when absent from config."""
        cfg = _write_config(tmp_path, {"retry_attempts": 1})
        client = LLMClient(config_path=cfg)
        captured, fake_create = _capture_request_kwargs(client)

        with patch.object(client.client.chat.completions, "create",
                          side_effect=fake_create):
            client.extract_json(system_prompt="sys", user_prompt="user")

        assert "top_p" not in captured
        assert "seed" not in captured
        # No extra_body at all for a non-Ollama provider without samplers
        assert "extra_body" not in captured

    def test_partial_sampler_config(self, tmp_path):
        """Only configured sampler keys are sent; unset ones stay absent."""
        cfg = _write_config(tmp_path, {
            "retry_attempts": 1,
            "seed": 7,
        })
        client = LLMClient(config_path=cfg)
        captured, fake_create = _capture_request_kwargs(client)

        with patch.object(client.client.chat.completions, "create",
                          side_effect=fake_create):
            client.extract_json(system_prompt="sys", user_prompt="user")

        assert captured["seed"] == 7
        assert "top_p" not in captured
        assert "extra_body" not in captured


# ---------------------------------------------------------------------------
# Sampler observability — /props probe (#471)
# ---------------------------------------------------------------------------


class TestSamplerObservability:
    """Verify the startup sampler log and best-effort /props probe."""

    def test_props_probe_404_is_swallowed(self, tmp_path):
        """A 404 from /props must not raise when the probe runs."""
        cfg = _write_config(tmp_path, {"base_url": "http://localhost:8000/v1"})
        client = LLMClient(config_path=cfg)

        httpx = pytest.importorskip("httpx")
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch.object(httpx, "get", return_value=mock_resp) as mock_get:
            # Explicit probe — exercises the network path directly.
            client._log_sampler_config(probe_backend=True)
        mock_get.assert_called_once()
        # Probe targets the server ROOT, not /v1
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://localhost:8000/props"

    def test_props_probe_exception_is_swallowed(self, tmp_path):
        """A connection error from /props must not raise when the probe runs."""
        cfg = _write_config(tmp_path, {"base_url": "http://localhost:8000/v1"})
        client = LLMClient(config_path=cfg)

        httpx = pytest.importorskip("httpx")
        with patch.object(httpx, "get",
                          side_effect=httpx.ConnectError("refused")):
            # Must not raise.
            client._log_sampler_config(probe_backend=True)

    def test_props_probe_skipped_under_pytest(self, tmp_path):
        """Auto-probe stays offline under pytest (no spurious network calls)."""
        cfg = _write_config(tmp_path, {"base_url": "http://localhost:8000/v1"})

        httpx = pytest.importorskip("httpx")
        # Construction triggers the auto path (probe_backend=None), which must
        # be suppressed under pytest so the suite never hits the network.
        with patch.object(httpx, "get") as mock_get:
            client = LLMClient(config_path=cfg)
            client._log_sampler_config()  # auto path
        mock_get.assert_not_called()

    def test_props_probe_skipped_for_cloud(self, tmp_path):
        """Cloud providers are never probed even with probing forced on."""
        cfg = _write_config(tmp_path)  # default base_url is api.openai.com
        client = LLMClient(config_path=cfg)

        httpx = pytest.importorskip("httpx")
        # Force probing on: the cloud gate must suppress the probe regardless
        # of the forced flag (and regardless of pytest).
        with patch.object(httpx, "get") as mock_get:
            client._log_sampler_config(probe_backend=True)
        mock_get.assert_not_called()

