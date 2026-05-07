"""Unit tests for the context budget pre-flight check in LLMClient.extract_json.

Tests the WARNING/NOTICE messages emitted to stderr when estimated
input + output approaches or exceeds the context window.
"""
import contextlib
import io
import json
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

# Mock openai so tests don't depend on optional LLM dependency
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient


def _make_llm_client(context_length=32768, max_tokens=4096):
    """Construct a real LLMClient from a temp config file."""
    import tempfile
    config = {
        "provider": "openai",
        "base_url": "http://localhost:59999/v1",
        "model": "test-model",
        "api_key_env": "",
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "context_length": context_length,
        "retry_attempts": 1,
        "timeout_seconds": 1,
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(config, f)
        client = LLMClient(path)
    finally:
        os.unlink(path)
    return client


def _capture_preflight(client, sys_prompt, user_prompt, max_tokens=None):
    """Call extract_json and capture stderr. Expects the LLM call to fail
    (no server) — we only care about the pre-flight output."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        try:
            client.extract_json(sys_prompt, user_prompt, max_tokens=max_tokens)
        except Exception:
            pass  # Expected — no server running on port 59999
    return stderr.getvalue()


class TestPreFlightBudgetCheck(unittest.TestCase):

    def test_no_warning_small_prompt(self):
        """Small prompt should produce no WARNING or NOTICE."""
        client = _make_llm_client(context_length=32768, max_tokens=4096)
        output = _capture_preflight(client, "Short system.", "Short user.", max_tokens=100)
        self.assertNotIn("WARNING", output)
        self.assertNotIn("NOTICE", output)

    def test_warning_on_overflow(self):
        """Prompt that exceeds context window should emit WARNING."""
        client = _make_llm_client(context_length=32768, max_tokens=6144)
        # ~33,333 tokens at //3, + 6144 output = ~39k > 32k
        big_prompt = "x" * 100000
        output = _capture_preflight(client, "sys", big_prompt, max_tokens=6144)
        self.assertIn("WARNING", output)
        self.assertIn("exceeds context window", output)

    def test_notice_on_tight_fit(self):
        """Prompt that barely fits should emit NOTICE."""
        client = _make_llm_client(context_length=32768, max_tokens=4096)
        # Target: input ~27.3k tokens, output 4096 → ~31.4k, headroom ~1.3k < 5% of 32k (1638)
        tight_prompt = "y" * 82000
        output = _capture_preflight(client, "s", tight_prompt, max_tokens=4096)
        self.assertIn("NOTICE", output)
        self.assertIn("Tight context budget", output)

    def test_no_check_without_context_length(self):
        """When context_length is None, no check is performed."""
        client = _make_llm_client(context_length=32768, max_tokens=4096)
        client.context_length = None  # Override after construction
        big_prompt = "z" * 100000
        output = _capture_preflight(client, "sys", big_prompt, max_tokens=6144)
        self.assertNotIn("WARNING", output)
        self.assertNotIn("NOTICE", output)

    def test_no_warning_at_comfortable_margin(self):
        """Prompt with >5% headroom should produce no warning."""
        client = _make_llm_client(context_length=32768, max_tokens=4096)
        # ~20k tokens input + 4096 output = ~24k, headroom ~8.7k > 5% (1638)
        prompt = "a" * 60000
        output = _capture_preflight(client, "sys", prompt, max_tokens=4096)
        self.assertNotIn("WARNING", output)
        self.assertNotIn("NOTICE", output)


if __name__ == "__main__":
    unittest.main()
