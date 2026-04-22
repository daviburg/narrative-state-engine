"""Tests for PC max_tokens override and skip-after-failures logic (#148, #149)."""
import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure `import openai` succeeds even when the package is not installed.
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from llm_client import LLMClient, LLMExtractionError
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


def _make_stub_llm(pc_fail=True, pc_max_tokens=4096):
    """Create a stub LLMClient-like object for integration tests.

    If pc_fail is True, PC detail extraction raises LLMExtractionError.
    Other calls return minimal valid responses.
    """
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = pc_max_tokens
    llm.delay = MagicMock()

    _call_count = {"n": 0}

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None):
        _call_count["n"] += 1
        # Entity discovery always returns empty
        if "discover" in system_prompt.lower() or "discovery" in system_prompt.lower():
            return {"entities": []}
        # PC detail extraction — fail or succeed based on pc_fail
        if "detail" in system_prompt.lower():
            if pc_fail:
                raise LLMExtractionError("Simulated PC extraction failure")
            return {"entity": None}
        # Relationship mapper
        if "relationship" in system_prompt.lower():
            return {"relationships": []}
        # Event extractor
        if "event" in system_prompt.lower():
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def _fresh_catalogs():
    """Return minimal catalogs with a PC entry."""
    catalogs = {fn: [] for fn in se.CATALOG_KEYS}
    se._ensure_player_character(catalogs, "turn-001")
    return catalogs


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
# #149 — Skip PC extraction after N consecutive failures (integration)
# ---------------------------------------------------------------------------


class TestPCSkipThreshold:
    """Verify PC extraction is skipped after threshold failures."""

    def test_skip_threshold_constant(self):
        """Skip threshold should be 20."""
        assert se._PC_SKIP_THRESHOLD == 20

    def test_warn_threshold_less_than_skip(self):
        """Warn threshold should be less than skip threshold."""
        assert se._PC_FAILURE_WARN_THRESHOLD < se._PC_SKIP_THRESHOLD

    def test_reset_clears_all_counters(self):
        """_reset_pc_failure_tracking clears all counters."""
        original_f = se._pc_consecutive_failures
        original_s = se._pc_skipped_turns
        original_c = se._pc_turns_since_cooldown
        try:
            se._pc_consecutive_failures = 25
            se._pc_skipped_turns = 10
            se._pc_turns_since_cooldown = 30
            se._reset_pc_failure_tracking()
            assert se._pc_consecutive_failures == 0
            assert se._pc_skipped_turns == 0
            assert se._pc_turns_since_cooldown == 0
        finally:
            se._pc_consecutive_failures = original_f
            se._pc_skipped_turns = original_s
            se._pc_turns_since_cooldown = original_c


class TestPCSkipIntegration:
    """Integration tests: run extract_and_merge with a stub LLM."""

    def _run_turns(self, llm, num_turns, start=1):
        """Run extract_and_merge for num_turns with a failing PC extraction."""
        catalogs = _fresh_catalogs()
        events = []
        for i in range(num_turns):
            turn = {
                "turn_id": f"turn-{start + i:03d}",
                "speaker": "dm",
                "text": f"The DM describes turn {start + i}.",
            }
            catalogs, events = se.extract_and_merge(
                turn, catalogs, events, llm, min_confidence=0.6,
            )
        return catalogs, events

    def test_pc_extraction_called_before_threshold(self, monkeypatch):
        """PC extraction should be attempted for turns below the threshold."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(pc_fail=True)

        self._run_turns(llm, 5)

        # extract_json should be called for each turn (discovery + PC detail + events)
        assert se._pc_consecutive_failures == 5
        assert se._pc_skipped_turns == 0

    def test_pc_extraction_skipped_after_threshold(self, monkeypatch):
        """After _PC_SKIP_THRESHOLD failures, PC extraction should stop."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(pc_fail=True)

        total_turns = se._PC_SKIP_THRESHOLD + 10
        self._run_turns(llm, total_turns)

        assert se._pc_consecutive_failures == se._PC_SKIP_THRESHOLD
        assert se._pc_skipped_turns == 10

    def test_pc_max_tokens_passed_to_extract_json(self, monkeypatch):
        """PC extraction should pass llm.pc_max_tokens to extract_json."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(pc_fail=False, pc_max_tokens=8192)

        self._run_turns(llm, 1)

        # Find the PC detail call — it should have max_tokens=8192
        for call in llm.extract_json.call_args_list:
            kwargs = call[1] if call[1] else {}
            if kwargs.get("max_tokens") == 8192:
                return  # Found the PC extraction call with correct max_tokens
        assert False, "PC extraction call with max_tokens=8192 not found"

    def test_counter_resets_on_success(self, monkeypatch):
        """Failure counter should reset to 0 on a successful PC extraction."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        # First: fail a few times
        llm_fail = _make_stub_llm(pc_fail=True)
        catalogs = _fresh_catalogs()
        events = []
        for i in range(5):
            turn = {"turn_id": f"turn-{i+1:03d}", "speaker": "dm", "text": "text"}
            catalogs, events = se.extract_and_merge(
                turn, catalogs, events, llm_fail, min_confidence=0.6,
            )
        assert se._pc_consecutive_failures == 5

        # Now succeed — use a LLM that returns a valid PC entity
        llm_ok = MagicMock()
        llm_ok.default_timeout = 10
        llm_ok.pc_max_tokens = 4096
        llm_ok.delay = MagicMock()

        def _extract_ok(system_prompt, user_prompt, timeout=None, max_tokens=None,
                        schema=None):
            if "discover" in system_prompt.lower():
                return {"entities": []}
            if "detail" in system_prompt.lower():
                return {"entity": {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-006",
                }}
            if "relationship" in system_prompt.lower():
                return {"relationships": []}
            if "event" in system_prompt.lower():
                return {"events": []}
            return {}

        llm_ok.extract_json = MagicMock(side_effect=_extract_ok)
        turn = {"turn_id": "turn-006", "speaker": "dm", "text": "text"}
        se.extract_and_merge(
            turn, catalogs, events, llm_ok, min_confidence=0.6,
        )
        assert se._pc_consecutive_failures == 0


class TestPCSkipLogNoise:
    """Verify log noise is reduced (#153): warnings only at threshold crossings."""

    def _run_turns_capture_stderr(self, monkeypatch, capsys, num_turns):
        """Run num_turns with failing PC extraction and capture stderr."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(pc_fail=True)
        catalogs = _fresh_catalogs()
        events = []
        for i in range(num_turns):
            turn = {
                "turn_id": f"turn-{i+1:03d}",
                "speaker": "dm",
                "text": f"Turn {i+1} text.",
            }
            catalogs, events = se.extract_and_merge(
                turn, catalogs, events, llm, min_confidence=0.6,
            )
        return capsys.readouterr()

    def test_warning_at_warn_threshold(self, monkeypatch, capsys):
        """WARNING about consecutive failures fires at threshold 10."""
        captured = self._run_turns_capture_stderr(
            monkeypatch, capsys, se._PC_FAILURE_WARN_THRESHOLD,
        )
        assert "Context may be too large" in captured.err

    def test_no_repeated_warnings_past_threshold(self, monkeypatch, capsys):
        """Warning should fire once, not on every turn past threshold."""
        captured = self._run_turns_capture_stderr(
            monkeypatch, capsys, se._PC_FAILURE_WARN_THRESHOLD + 5,
        )
        count = captured.err.count("Context may be too large")
        assert count == 1, f"Expected 1 warning, got {count}"

    def test_skip_message_at_skip_threshold(self, monkeypatch, capsys):
        """Cooldown warning fires exactly at _PC_SKIP_THRESHOLD."""
        captured = self._run_turns_capture_stderr(
            monkeypatch, capsys, se._PC_SKIP_THRESHOLD,
        )
        assert "entering cooldown" in captured.err

    def test_no_warnings_while_skipping(self, monkeypatch, capsys):
        """No per-turn warnings should fire while PC extraction is skipped."""
        captured = self._run_turns_capture_stderr(
            monkeypatch, capsys, se._PC_SKIP_THRESHOLD + 10,
        )
        # Should only see the threshold-crossing warnings, not per-turn noise
        warn_count = captured.err.count("WARNING: PC extraction has failed")
        assert warn_count <= 1, f"Expected at most 1 consecutive-failure warning, got {warn_count}"
