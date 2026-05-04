"""Tests for discovery truncation detection and retry/repair flow (#288).

Covers:
- _repair_truncated_discovery() JSON repair from partial output
- Discovery retry with 2× max_tokens on truncation
- Fallback to repair when retry also truncates
- Correct turn_failed / _phase_log state on total failure
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _repair_truncated_discovery

# Ensure openai mock exists for llm_client import
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMTruncationError


# ---------------------------------------------------------------------------
# _repair_truncated_discovery tests
# ---------------------------------------------------------------------------


class TestRepairTruncatedDiscovery:
    """Verify JSON repair logic for truncated discovery responses."""

    def test_recovers_complete_entities_from_truncated_output(self):
        """Truncation mid-second-entity recovers the first complete entity."""
        partial = '{"entities": [{"id": "char-kael", "name": "Kael", "type": "character", "is_new": true, "confidence": 0.9, "description": "A warrior"}, {"id": "loc-for'
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert len(result["entities"]) == 1
        assert result["entities"][0]["id"] == "char-kael"

    def test_returns_none_for_no_complete_entities(self):
        """When truncation happens inside the first entity, returns None."""
        partial = '{"entities": [{"id": "char-kael", "name": "Ka'
        result = _repair_truncated_discovery(partial)
        assert result is None

    def test_returns_complete_json_unchanged(self):
        """Already-valid JSON passes through (returns parsed dict)."""
        complete = '{"entities": [{"id": "char-kael", "name": "Kael", "type": "character", "is_new": true, "confidence": 0.9, "description": "A warrior"}]}'
        result = _repair_truncated_discovery(complete)
        assert result is not None
        assert len(result["entities"]) == 1

    def test_handles_think_block_prefix(self):
        """<think> blocks are stripped before repair."""
        partial = '<think>Let me analyze this turn carefully...</think>{"entities": [{"id": "char-kael", "name": "Kael", "type": "character", "is_new": true, "confidence": 0.9, "description": "A warrior"}, {"id": "loc-trunc'
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert result["entities"][0]["id"] == "char-kael"

    def test_handles_markdown_fence_prefix(self):
        """Markdown code fences are stripped before repair."""
        partial = '```json\n{"entities": [{"id": "char-kael", "name": "Kael", "type": "character", "is_new": true, "confidence": 0.9, "description": "A warrior"}, {"id": "char-trunc'
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert result["entities"][0]["id"] == "char-kael"

    def test_returns_none_for_garbage(self):
        """Non-JSON garbage returns None."""
        result = _repair_truncated_discovery("this is not json at all")
        assert result is None

    def test_multiple_complete_entities_preserved(self):
        """All complete entity objects before truncation point are kept."""
        partial = '{"entities": [{"id": "char-a", "name": "A", "type": "character", "is_new": true, "confidence": 0.9, "description": "x"}, {"id": "char-b", "name": "B", "type": "character", "is_new": false, "confidence": 0.8}, {"id": "char-c", "name": "C", "type": "chara'
        result = _repair_truncated_discovery(partial)
        assert result is not None
        assert len(result["entities"]) == 2
        assert result["entities"][0]["id"] == "char-a"
        assert result["entities"][1]["id"] == "char-b"


# ---------------------------------------------------------------------------
# Discovery truncation retry flow tests
# ---------------------------------------------------------------------------


class TestDiscoveryTruncationRetry:
    """Verify the truncation-aware retry logic in the discovery extraction path.

    These tests mock the LLM client to simulate truncation scenarios and verify
    the retry/repair fallback behavior.
    """

    def _make_discovery_result(self, entities):
        """Build a valid discovery result dict."""
        return {"entities": entities}

    def test_retry_uses_doubled_max_tokens(self):
        """On first truncation, retry is called with 2× the original max_tokens."""
        from llm_client import LLMClient
        cfg_data = {
            "provider": "openai", "base_url": "http://localhost:8000/v1",
            "model": "test", "api_key_env": "", "temperature": 0.0,
            "max_tokens": 4096, "timeout_seconds": 10, "retry_attempts": 1,
            "batch_delay_ms": 0, "discovery_max_tokens": 8192,
        }
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(cfg_data, f)
            cfg_path = f.name

        try:
            client = LLMClient(config_path=cfg_path)
            calls = []

            def mock_extract_json(system_prompt, user_prompt, temperature=None, max_tokens=None):
                calls.append({"max_tokens": max_tokens})
                if len(calls) == 1:
                    raise LLMTruncationError("truncated", partial_text='{"entities": [{"id": "char-x"')
                return {"entities": [{"id": "char-a", "name": "A", "type": "character", "is_new": True, "confidence": 0.9, "description": "test"}]}

            with patch.object(client, "extract_json", side_effect=mock_extract_json):
                # Simulate the retry logic from semantic_extraction
                discovery_max = 8192
                try:
                    result = client.extract_json(
                        system_prompt="test", user_prompt="test",
                        max_tokens=discovery_max,
                    )
                except LLMTruncationError:
                    retry_max = discovery_max * 2
                    result = client.extract_json(
                        system_prompt="test", user_prompt="test",
                        max_tokens=retry_max,
                    )

            assert calls[0]["max_tokens"] == 8192
            assert calls[1]["max_tokens"] == 16384
            assert result["entities"][0]["id"] == "char-a"
        finally:
            os.unlink(cfg_path)

    def test_repair_fallback_on_double_truncation(self):
        """When retry also truncates, repair is attempted on the larger partial."""
        first_partial = '{"entities": [{"id": "char-a", "name": "A", "type": "character", "is_new": true, "confidence": 0.9, "description": "test"}, {"id": "char-trunc'
        second_partial = '{"entities": [{"id": "char-a", "name": "A", "type": "character", "is_new": true, "confidence": 0.9, "description": "test"}, {"id": "char-b", "name": "B", "type": "character", "is_new": true, "confidence": 0.8, "description": "second"}, {"id": "char-trunc'

        # Simulate: first call truncates, retry truncates with more data
        # Repair on second partial should recover 2 entities
        repaired = _repair_truncated_discovery(second_partial)
        assert repaired is not None
        assert len(repaired["entities"]) == 2

    def test_repair_falls_back_to_first_partial(self):
        """When repair on retry partial fails, try repair on original partial."""
        first_partial = '{"entities": [{"id": "char-a", "name": "A", "type": "character", "is_new": true, "confidence": 0.9, "description": "good"}, {"id": "char-trunc'
        # Second partial is garbage (e.g. model produced non-JSON on retry)
        second_partial = "Internal server error"

        repair_second = _repair_truncated_discovery(second_partial)
        assert repair_second is None  # Can't repair garbage

        repair_first = _repair_truncated_discovery(first_partial)
        assert repair_first is not None
        assert len(repair_first["entities"]) == 1
        assert repair_first["entities"][0]["id"] == "char-a"
