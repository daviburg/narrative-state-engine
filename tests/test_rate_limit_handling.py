"""Tests for rate limit handling and retry statistics (#215)."""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import (
    LLMClient,
    LLMExtractionError,
    QuotaExhaustedError,
    RetryStats,
)


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
        "retry_attempts": 3,
        "batch_delay_ms": 0,
    }
    if overrides:
        cfg.update(overrides)
    path = os.path.join(tmp_dir, "llm.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


# ---------------------------------------------------------------------------
# RetryStats
# ---------------------------------------------------------------------------


class TestRetryStats:
    def test_initial_state(self):
        stats = RetryStats()
        s = stats.summary()
        assert s["total_requests"] == 0
        assert s["successful_requests"] == 0
        assert s["retried_requests"] == 0
        assert s["error_breakdown"] == {}
        assert not stats.has_errors()

    def test_record_success_resets_consecutive(self):
        stats = RetryStats()
        stats.record_error(429)
        stats.record_error(429)
        assert stats.consecutive_rate_limits == 2
        stats.record_success()
        assert stats.consecutive_rate_limits == 0

    def test_record_error_tracks_status(self):
        stats = RetryStats()
        stats.record_error(429)
        stats.record_error(503)
        stats.record_error(429)
        s = stats.summary()
        assert s["error_breakdown"] == {429: 2, 503: 1}
        assert s["total_requests"] == 3
        assert stats.has_errors()

    def test_consecutive_rate_limits_reset_on_other_error(self):
        stats = RetryStats()
        stats.record_error(429)
        stats.record_error(429)
        assert stats.consecutive_rate_limits == 2
        stats.record_error(503)
        assert stats.consecutive_rate_limits == 0
        assert stats._max_consecutive_rate_limits == 2

    def test_retry_after_tracking(self):
        stats = RetryStats()
        stats.record_error(429, retry_after_present=True)
        stats.record_error(429, retry_after_present=False)
        assert stats.summary()["retry_after_headers_seen"] == 1

    def test_unknown_status(self):
        stats = RetryStats()
        stats.record_error(None)
        assert stats.summary()["error_breakdown"] == {"unknown": 1}

    def test_record_retry(self):
        stats = RetryStats()
        stats.record_retry()
        stats.record_retry()
        assert stats.summary()["retried_requests"] == 2


# ---------------------------------------------------------------------------
# _classify_error
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_error_with_status_code(self):
        e = Exception("rate limited")
        e.status_code = 429
        status, retry_after = LLMClient._classify_error(e)
        assert status == 429
        assert retry_after is None

    def test_error_with_retry_after_header(self):
        e = Exception("rate limited")
        e.status_code = 429
        e.response = MagicMock()
        e.response.headers = {"retry-after": "5"}
        status, retry_after = LLMClient._classify_error(e)
        assert status == 429
        assert retry_after == 5.0

    def test_error_with_retry_after_ms_header(self):
        e = Exception("rate limited")
        e.status_code = 429
        e.response = MagicMock()
        e.response.headers = {"retry-after-ms": "3000"}
        status, retry_after = LLMClient._classify_error(e)
        assert status == 429
        assert retry_after == 3.0

    def test_retry_after_seconds_preferred_over_ms(self):
        e = Exception("rate limited")
        e.status_code = 429
        e.response = MagicMock()
        e.response.headers = {"retry-after": "10", "retry-after-ms": "3000"}
        _, retry_after = LLMClient._classify_error(e)
        assert retry_after == 10.0

    def test_resource_exhausted_in_message(self):
        e = Exception("RESOURCE_EXHAUSTED: quota exceeded")
        status, _ = LLMClient._classify_error(e)
        assert status == 429

    def test_generic_error_no_status(self):
        e = ValueError("parse error")
        status, retry_after = LLMClient._classify_error(e)
        assert status is None
        assert retry_after is None

    def test_invalid_retry_after_value(self):
        e = Exception("rate limited")
        e.status_code = 429
        e.response = MagicMock()
        e.response.headers = {"retry-after": "not-a-number"}
        _, retry_after = LLMClient._classify_error(e)
        assert retry_after is None


# ---------------------------------------------------------------------------
# _is_cloud_provider
# ---------------------------------------------------------------------------


class TestIsCloudProvider:
    def test_openai_is_cloud(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "https://api.openai.com/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_cloud_provider is True

    def test_gemini_is_cloud(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_cloud_provider is True

    def test_localhost_is_not_cloud(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:8080/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_cloud_provider is False

    def test_127_is_not_cloud(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://127.0.0.1:8080/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_cloud_provider is False

    def test_ollama_is_not_cloud(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
        })
        client = LLMClient(config_path=cfg)
        assert client._is_cloud_provider is False


# ---------------------------------------------------------------------------
# Cloud provider batch delay enforcement
# ---------------------------------------------------------------------------


class TestCloudBatchDelay:
    def test_cloud_enforces_min_delay(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "https://api.openai.com/v1",
            "batch_delay_ms": 200,
        })
        client = LLMClient(config_path=cfg)
        with patch("time.sleep") as mock_sleep:
            client.delay()
            mock_sleep.assert_called_once_with(2.0)  # 2000ms minimum

    def test_cloud_respects_higher_config(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "https://api.openai.com/v1",
            "batch_delay_ms": 5000,
        })
        client = LLMClient(config_path=cfg)
        with patch("time.sleep") as mock_sleep:
            client.delay()
            mock_sleep.assert_called_once_with(5.0)

    def test_local_uses_config_delay(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "base_url": "http://localhost:8080/v1",
            "batch_delay_ms": 200,
        })
        client = LLMClient(config_path=cfg)
        with patch("time.sleep") as mock_sleep:
            client.delay()
            mock_sleep.assert_called_once_with(0.2)


# ---------------------------------------------------------------------------
# QuotaExhaustedError hierarchy
# ---------------------------------------------------------------------------


class TestQuotaExhaustedError:
    def test_is_subclass_of_llm_extraction_error(self):
        assert issubclass(QuotaExhaustedError, LLMExtractionError)

    def test_can_be_caught_as_llm_extraction_error(self):
        with pytest.raises(LLMExtractionError):
            raise QuotaExhaustedError("quota gone")

    def test_can_be_caught_specifically(self):
        with pytest.raises(QuotaExhaustedError):
            raise QuotaExhaustedError("quota gone")


# ---------------------------------------------------------------------------
# _handle_retry raises QuotaExhaustedError on threshold
# ---------------------------------------------------------------------------


class TestHandleRetryQuota:
    def test_raises_quota_exhausted_after_threshold(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "retry_attempts": 1,
            "consecutive_rate_limit_threshold": 3,
        })
        client = LLMClient(config_path=cfg)

        # Simulate 429 errors
        e429 = Exception("rate limited")
        e429.status_code = 429

        # First two should not raise
        client._handle_retry(0, e429)
        client._handle_retry(0, e429)
        assert client.stats.consecutive_rate_limits == 2

        # Third should raise
        with pytest.raises(QuotaExhaustedError, match="consecutive 429"):
            client._handle_retry(0, e429)

    def test_success_resets_counter_between_calls(self, tmp_path):
        cfg = _write_config(tmp_path, {
            "retry_attempts": 1,
            "consecutive_rate_limit_threshold": 3,
        })
        client = LLMClient(config_path=cfg)

        e429 = Exception("rate limited")
        e429.status_code = 429

        client._handle_retry(0, e429)
        client._handle_retry(0, e429)
        client.stats.record_success()  # Simulates a successful call
        # Counter is reset, so next 429 starts fresh
        client._handle_retry(0, e429)
        assert client.stats.consecutive_rate_limits == 1

    @patch("time.sleep")
    def test_retry_after_header_used_for_backoff(self, mock_sleep, tmp_path):
        cfg = _write_config(tmp_path, {
            "retry_attempts": 3,
            "consecutive_rate_limit_threshold": 100,
            "base_url": "http://localhost:8080/v1",  # local, no jitter
        })
        client = LLMClient(config_path=cfg)

        e429 = Exception("rate limited")
        e429.status_code = 429
        e429.response = MagicMock()
        e429.response.headers = {"retry-after": "7"}

        # attempt=0, not last attempt (retry_attempts=3), should sleep with retry-after
        client._handle_retry(0, e429)
        mock_sleep.assert_called_once_with(7.0)

    @patch("time.sleep")
    def test_exponential_backoff_without_retry_after(self, mock_sleep, tmp_path):
        cfg = _write_config(tmp_path, {
            "retry_attempts": 3,
            "consecutive_rate_limit_threshold": 100,
            "base_url": "http://localhost:8080/v1",  # local, no jitter
        })
        client = LLMClient(config_path=cfg)

        e503 = Exception("server error")
        e503.status_code = 503

        client._handle_retry(0, e503)
        mock_sleep.assert_called_once_with(1.0)  # 2^0 * 1.0

        mock_sleep.reset_mock()
        client._handle_retry(1, e503)
        mock_sleep.assert_called_once_with(2.0)  # 2^1 * 1.0


# ---------------------------------------------------------------------------
# extract_json retry stats integration
# ---------------------------------------------------------------------------


class TestExtractJsonRetryStats:
    def _make_client(self, tmp_path, overrides=None):
        defaults = {
            "retry_attempts": 3,
            "base_url": "http://localhost:8080/v1",
        }
        if overrides:
            defaults.update(overrides)
        cfg = _write_config(tmp_path, defaults)
        return LLMClient(config_path=cfg)

    @patch("time.sleep")
    def test_success_records_stats(self, mock_sleep, tmp_path):
        client = self._make_client(tmp_path)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        result = client.extract_json("system", "user")
        assert result == {"result": "ok"}
        assert client.stats.successful_requests == 1
        assert client.stats.total_requests == 1

    @patch("time.sleep")
    def test_retry_then_success_records_stats(self, mock_sleep, tmp_path):
        client = self._make_client(tmp_path)

        e503 = Exception("server error")
        e503.status_code = 503

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok"}'

        client.client.chat.completions.create = MagicMock(
            side_effect=[e503, mock_response]
        )

        result = client.extract_json("system", "user")
        assert result == {"result": "ok"}
        assert client.stats.total_requests == 2
        assert client.stats.successful_requests == 1
        assert client.stats.summary()["error_breakdown"] == {503: 1}
        assert client.stats.summary()["retried_requests"] == 1

    @patch("time.sleep")
    def test_all_retries_fail(self, mock_sleep, tmp_path):
        client = self._make_client(tmp_path, {"retry_attempts": 2})

        e500 = Exception("internal error")
        e500.status_code = 500

        client.client.chat.completions.create = MagicMock(side_effect=e500)

        with pytest.raises(LLMExtractionError, match="Failed after 2 attempts"):
            client.extract_json("system", "user")

        assert client.stats.total_requests == 2
        assert client.stats.successful_requests == 0
        assert client.stats.summary()["error_breakdown"] == {500: 2}

    @patch("time.sleep")
    def test_quota_exhausted_during_extract(self, mock_sleep, tmp_path):
        client = self._make_client(tmp_path, {
            "retry_attempts": 5,
            "consecutive_rate_limit_threshold": 3,
        })

        e429 = Exception("rate limited")
        e429.status_code = 429

        client.client.chat.completions.create = MagicMock(side_effect=e429)

        with pytest.raises(QuotaExhaustedError, match="consecutive 429"):
            client.extract_json("system", "user")

        # Should stop at threshold, not exhaust all retries
        assert client.stats.consecutive_rate_limits >= 3


# ---------------------------------------------------------------------------
# generate_text retry stats
# ---------------------------------------------------------------------------


class TestGenerateTextRetryStats:
    @patch("time.sleep")
    def test_success_records_stats(self, mock_sleep, tmp_path):
        cfg = _write_config(tmp_path, {
            "retry_attempts": 1,
            "base_url": "http://localhost:8080/v1",
        })
        client = LLMClient(config_path=cfg)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello world"
        client.client.chat.completions.create = MagicMock(return_value=mock_response)

        result = client.generate_text("system", "user")
        assert result == "Hello world"
        assert client.stats.successful_requests == 1


# ---------------------------------------------------------------------------
# Semantic extraction QuotaExhaustedError propagation
# ---------------------------------------------------------------------------


class TestQuotaExhaustedPropagation:
    """Verify QuotaExhaustedError propagates through extract_and_merge."""

    def test_quota_error_not_swallowed(self):
        """QuotaExhaustedError should propagate past LLMExtractionError catches."""
        import semantic_extraction as se

        llm = MagicMock()
        llm.default_timeout = 10
        llm.pc_max_tokens = 4096
        llm.delay = MagicMock()
        llm.config = {"checkpoint_interval": 100}

        llm.extract_json.side_effect = QuotaExhaustedError("quota gone")

        catalogs = {fn: [] for fn in se.CATALOG_KEYS}
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "test"}

        with pytest.raises(QuotaExhaustedError, match="quota gone"):
            se.extract_and_merge(turn, catalogs, [], llm, 0.5)
