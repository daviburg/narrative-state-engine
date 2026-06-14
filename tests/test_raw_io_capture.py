"""Tests for RAW-IO capture instrumentation (epic #477, step 1).

Measurement-only, default-OFF.  Covers:

- ``_raw_io_capture_enabled`` — default OFF, strict bool, defensive parsing.
- The flag-OFF byte-identity guarantee: when capture is not enabled, a call
  that passes a ``capture`` tag behaves identically (same request body, same
  parsed return) and writes NO artifact.
- Capture correctness: when enabled, a call tees a JSONL record carrying the
  verbatim prompt + completion, phase, turn, entity_id, and per-call
  input/output token counts (real ``usage`` field vs ``_estimate_tokens``
  fallback flagged ``*_tokens_estimated``).
- Per-entity isolation: entity_detail records carry the entity_id so the PC
  call is isolable.
"""

import json
import os
import sys

from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient, _estimate_tokens
from semantic_extraction import (
    _RAW_IO_CAPTURE_FILENAME,
    _raw_io_capture_enabled,
)


# ---------------------------------------------------------------------------
# Test doubles — a minimal OpenAI-style chat completion response.
# ---------------------------------------------------------------------------

class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content='{"ok": true}', usage=None, finish_reason="stop"):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = usage


def _write_config(tmp_dir, overrides=None):
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
        "skip_response_format": True,
    }
    if overrides:
        cfg.update(overrides)
    path = os.path.join(str(tmp_dir), "llm.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


def _make_client(tmp_path, response):
    client = LLMClient(config_path=_write_config(tmp_path))
    client.client.chat.completions.create = MagicMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Flag gate
# ---------------------------------------------------------------------------

class TestRawIoCaptureFlag:
    def test_default_off(self):
        assert _raw_io_capture_enabled(None) is False
        assert _raw_io_capture_enabled({}) is False
        assert _raw_io_capture_enabled({"context_optimizations": {}}) is False

    def test_strict_true_enables(self):
        cfg = {"context_optimizations": {"raw_io_capture": True}}
        assert _raw_io_capture_enabled(cfg) is True

    def test_truthy_non_bool_does_not_enable(self):
        for bad in ("true", "True", 1, [1], {"x": 1}):
            cfg = {"context_optimizations": {"raw_io_capture": bad}}
            assert _raw_io_capture_enabled(cfg) is False, bad

    def test_false_disables(self):
        cfg = {"context_optimizations": {"raw_io_capture": False}}
        assert _raw_io_capture_enabled(cfg) is False

    def test_defensive_against_malformed_config(self):
        assert _raw_io_capture_enabled({"context_optimizations": []}) is False
        assert _raw_io_capture_enabled({"context_optimizations": "x"}) is False
        assert _raw_io_capture_enabled("not-a-dict") is False
        assert _raw_io_capture_enabled(123) is False

    def test_filename_constant(self):
        assert _RAW_IO_CAPTURE_FILENAME == "raw-io-capture.jsonl"


# ---------------------------------------------------------------------------
# Flag-OFF byte-identity: no artifact, unchanged behaviour
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_no_artifact_when_capture_not_enabled(self, tmp_path):
        client = _make_client(tmp_path, _FakeResponse('{"ok": true}'))
        # Capture is NOT enabled (enable_raw_io_capture never called), but a
        # capture tag is still passed — it must be ignored and write nothing.
        result = client.extract_json(
            system_prompt="sys",
            user_prompt="usr",
            capture={"turn": "turn-001", "phase": "entity_detail",
                     "entity_id": "char-player"},
        )
        assert result == {"ok": True}
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        assert not os.path.exists(artifact)

    def test_request_body_identical_with_capture_tag(self, tmp_path):
        """The capture kwarg must never leak into the API request body."""
        client = _make_client(tmp_path, _FakeResponse('{"ok": true}'))
        client.extract_json(
            system_prompt="sys",
            user_prompt="usr",
            capture={"turn": "turn-001", "phase": "entity_detail",
                     "entity_id": "e1"},
        )
        call_kwargs = client.client.chat.completions.create.call_args[1]
        assert "capture" not in call_kwargs
        assert call_kwargs["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]


# ---------------------------------------------------------------------------
# Capture correctness
# ---------------------------------------------------------------------------

class TestCaptureRecord:
    def _read_records(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_captures_verbatim_prompt_completion_and_real_tokens(self, tmp_path):
        completion = '{"id": "char-player", "name": "Hero"}'
        resp = _FakeResponse(completion, usage=_FakeUsage(100, 42))
        client = _make_client(tmp_path, resp)
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        client.enable_raw_io_capture(artifact)

        client.extract_json(
            system_prompt="SYSTEM PROMPT TEXT",
            user_prompt="USER PROMPT TEXT",
            capture={"turn": "turn-042", "phase": "entity_detail",
                     "entity_id": "char-player"},
        )

        records = self._read_records(artifact)
        assert len(records) == 1
        rec = records[0]
        assert rec["turn"] == "turn-042"
        assert rec["phase"] == "entity_detail"
        assert rec["entity_id"] == "char-player"
        assert rec["raw_prompt"]["system"] == "SYSTEM PROMPT TEXT"
        assert rec["raw_prompt"]["user"] == "USER PROMPT TEXT"
        assert rec["raw_completion"] == completion
        # Real usage fields used, NOT estimated.
        assert rec["input_tokens"] == 100
        assert rec["output_tokens"] == 42
        assert rec["input_tokens_estimated"] is False
        assert rec["output_tokens_estimated"] is False

    def test_estimates_output_tokens_when_no_usage(self, tmp_path):
        completion = '{"events": []}'
        resp = _FakeResponse(completion, usage=None)
        client = _make_client(tmp_path, resp)
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        client.enable_raw_io_capture(artifact)

        client.extract_json(
            system_prompt="s",
            user_prompt="u",
            capture={"turn": "turn-007", "phase": "event", "entity_id": None},
        )

        rec = self._read_records(artifact)[0]
        assert rec["output_tokens"] == _estimate_tokens(completion)
        assert rec["output_tokens_estimated"] is True
        assert rec["input_tokens_estimated"] is True
        assert rec["output_tokens"] >= 1

    def test_per_entity_isolation(self, tmp_path):
        """Each entity_detail call records its own entity_id (PC isolable)."""
        client = LLMClient(config_path=_write_config(tmp_path))
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        client.enable_raw_io_capture(artifact)

        for eid in ("char-player", "char-npc-001", "loc-keep"):
            client.client.chat.completions.create = MagicMock(
                return_value=_FakeResponse('{"id": "%s"}' % eid,
                                           usage=_FakeUsage(50, 10)))
            client.extract_json(
                system_prompt="s",
                user_prompt="u for %s" % eid,
                capture={"turn": "turn-100", "phase": "entity_detail",
                         "entity_id": eid},
            )

        records = self._read_records(artifact)
        assert [r["entity_id"] for r in records] == [
            "char-player", "char-npc-001", "loc-keep",
        ]
        pc_records = [r for r in records if r["entity_id"] == "char-player"]
        assert len(pc_records) == 1
        assert pc_records[0]["input_tokens"] == 50

    def test_capture_meta_none_writes_nothing(self, tmp_path):
        client = _make_client(tmp_path, _FakeResponse('{"ok": true}'))
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        client.enable_raw_io_capture(artifact)
        client.extract_json(system_prompt="s", user_prompt="u", capture=None)
        assert not os.path.exists(artifact)

    def test_generate_text_captures(self, tmp_path):
        resp = _FakeResponse("free form text", usage=_FakeUsage(20, 5))
        client = _make_client(tmp_path, resp)
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        client.enable_raw_io_capture(artifact)
        out = client.generate_text(
            system_prompt="s", user_prompt="u",
            capture={"turn": "turn-001", "phase": "synthesis",
                     "entity_id": None},
        )
        assert out == "free form text"
        rec = self._read_records(artifact)[0]
        assert rec["raw_completion"] == "free form text"
        assert rec["output_tokens"] == 5
        assert rec["phase"] == "synthesis"


# ---------------------------------------------------------------------------
# Robustness — capture must never break extraction
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_enable_creates_directory(self, tmp_path):
        client = LLMClient(config_path=_write_config(tmp_path))
        nested = os.path.join(str(tmp_path), "a", "b", "raw-io.jsonl")
        client.enable_raw_io_capture(nested)
        assert os.path.isdir(os.path.dirname(nested))

    def test_write_record_never_raises(self, tmp_path):
        client = LLMClient(config_path=_write_config(tmp_path))
        client.enable_raw_io_capture(
            os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME))
        # Malformed messages / response must not raise.
        client._write_raw_io_record({}, "not-a-list", None, object())
        client._write_raw_io_record(
            {"turn": "t"}, [{"role": "system"}], None, None)

    def test_write_noop_when_path_unset(self, tmp_path):
        client = LLMClient(config_path=_write_config(tmp_path))
        # Path never set — writer returns immediately, creates nothing.
        client._write_raw_io_record(
            {"turn": "t"}, [{"role": "user", "content": "x"}], "y", None)
        artifact = os.path.join(str(tmp_path), _RAW_IO_CAPTURE_FILENAME)
        assert not os.path.exists(artifact)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
