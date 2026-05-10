"""Tests for wall-clock watchdog timers in LLM client (#195, #281)."""

import json
import os
import sys
import time
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient, LLMExtractionError


def _write_config(tmp_dir, overrides=None):
    """Write a minimal llm.json and return its path."""
    cfg = {
        "provider": "openai",
        "base_url": "http://localhost:8000/v1",
        "model": "test-model",
        "api_key_env": "",
        "temperature": 0.0,
        "max_tokens": 4096,
        "timeout_seconds": 2,
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
# _call_with_deadline tests (OpenAI-compat watchdog, #195)
# ---------------------------------------------------------------------------


class TestCallWithDeadline:
    """Verify _call_with_deadline enforces wall-clock timeout."""

    def test_stalled_call_raises_within_deadline(self, tmp_path):
        """A function that blocks is interrupted by deadline."""
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)

        # Use an event so the thread can be signalled to stop after test
        stop_event = threading.Event()

        def stalling_fn():
            stop_event.wait(timeout=30)

        start = time.time()
        with pytest.raises(LLMExtractionError, match="WATCHDOG"):
            client._call_with_deadline(stalling_fn, deadline_seconds=1.0)
        elapsed = time.time() - start
        # Should complete within ~1s + small overhead
        assert elapsed < 3.0
        # Signal thread to exit so it doesn't linger
        stop_event.set()

    def test_fast_call_returns_normally(self, tmp_path):
        """A fast function completes without watchdog interference."""
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)

        result = client._call_with_deadline(lambda: 42, deadline_seconds=5.0)
        assert result == 42

    def test_exception_in_fn_propagates(self, tmp_path):
        """Exceptions from the wrapped function propagate normally."""
        cfg = _write_config(tmp_path)
        client = LLMClient(config_path=cfg)

        def failing_fn():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            client._call_with_deadline(failing_fn, deadline_seconds=5.0)


# ---------------------------------------------------------------------------
# Ollama streaming watchdog tests (#281)
# ---------------------------------------------------------------------------


class TestOllamaStreamingWatchdog:
    """Verify watchdog aborts stalled Ollama streaming connections."""

    def test_stalled_stream_raises_within_hard_limit(self, tmp_path):
        """When iter_lines() blocks forever, watchdog aborts the stream."""
        cfg = _write_config(tmp_path, {
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "timeout_seconds": 1,  # hard_limit = 3s
        })
        client = LLMClient(config_path=cfg)

        # Mock httpx.stream to return a response that blocks on iter_lines
        block_event = threading.Event()

        class StallResponse:
            def __init__(self):
                self._closed = False

            def iter_lines(self):
                # Block until watchdog calls close()
                block_event.wait(timeout=10)
                # After unblocking, yield nothing (empty iteration)
                return
                yield  # make this a generator

            def close(self):
                self._closed = True
                block_event.set()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        stall_resp = StallResponse()

        with patch("httpx.stream", return_value=stall_resp):
            start = time.time()
            # The empty response after watchdog abort should raise
            with pytest.raises(LLMExtractionError):
                client._ollama_streaming_chat(
                    [{"role": "user", "content": "test"}],
                    timeout=1,
                )
            elapsed = time.time() - start
            # hard_limit = 1 * 3 = 3s; should complete within ~4s
            assert elapsed < 5.0
            # Watchdog should have closed the connection
            assert stall_resp._closed

    def test_normal_stream_completes_without_interference(self, tmp_path):
        """Normal streaming responses work fine with watchdog present."""
        cfg = _write_config(tmp_path, {
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "timeout_seconds": 10,
        })
        client = LLMClient(config_path=cfg)

        # Simulate a normal Ollama streaming response
        lines = [
            json.dumps({"message": {"content": "hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": False}),
            json.dumps({"done": True, "eval_count": 2, "prompt_eval_count": 5,
                        "done_reason": "stop"}),
        ]

        class NormalResponse:
            def iter_lines(self):
                for line in lines:
                    yield line

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch("httpx.stream", return_value=NormalResponse()):
            result = client._ollama_streaming_chat(
                [{"role": "user", "content": "test"}],
                timeout=10,
            )
            assert result == "hello world"

    def test_partial_content_aborted_raises_error(self, tmp_path):
        """Watchdog abort with partial content must NOT return truncated data."""
        cfg = _write_config(tmp_path, {
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "timeout_seconds": 1,  # hard_limit = 3s
        })
        client = LLMClient(config_path=cfg)

        block_event = threading.Event()

        class PartialThenStallResponse:
            """Yields some content chunks then stalls (no done frame)."""

            def __init__(self):
                self._closed = False

            def iter_lines(self):
                # Yield partial content
                yield json.dumps({"message": {"content": "partial"}, "done": False})
                yield json.dumps({"message": {"content": " data"}, "done": False})
                # Then stall until watchdog fires
                block_event.wait(timeout=10)

            def close(self):
                self._closed = True
                block_event.set()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        stall_resp = PartialThenStallResponse()

        with patch("httpx.stream", return_value=stall_resp):
            with pytest.raises(LLMExtractionError, match="WATCHDOG"):
                client._ollama_streaming_chat(
                    [{"role": "user", "content": "test"}],
                    timeout=1,
                )
            # Watchdog should have closed the connection
            assert stall_resp._closed


# ---------------------------------------------------------------------------
# Integration: watchdog exception is retryable (#195, #281)
# ---------------------------------------------------------------------------


class TestWatchdogRetryable:
    """Verify watchdog errors are caught by extract_json retry loop."""

    def test_watchdog_error_triggers_retry(self, tmp_path):
        """When watchdog fires, extract_json retries and can succeed."""
        cfg = _write_config(tmp_path, {
            "timeout_seconds": 1,
            "retry_attempts": 2,
        })
        client = LLMClient(config_path=cfg)

        call_count = [0]
        stop_event = threading.Event()

        def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call stalls until signalled
                stop_event.wait(timeout=30)
                raise Exception("interrupted")
            # Second call succeeds
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = '{"result": "ok"}'
            mock_resp.choices[0].finish_reason = "stop"
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        with patch.object(client, '_next_client', return_value=mock_client):
            with patch.object(type(client), '_use_ollama_streaming',
                             new_callable=PropertyMock, return_value=False):
                result = client.extract_json(
                    system_prompt="test",
                    user_prompt="test",
                    timeout=1,
                )
                assert result == {"result": "ok"}
                assert call_count[0] == 2
        # Signal stalled thread to exit
        stop_event.set()
