"""Tests for the relationship_mapper output-token cap override (#477).

The relationship_mapper phase has its own `relationship_max_tokens` config key
(default 8192 in the shipped config) so a single oversized council-turn output
no longer hits the 4096 fallback cap and forces a truncation + full restart.
The value is a ceiling, not a target: greedy decoding stops at the natural end
of the JSON, so turns whose rel output fits under 4096 are byte-identical.
"""
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

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


def _make_stub_llm(config=None, parallel_workers=1):
    """Build a stub LLM that returns minimal valid responses for each phase."""
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.max_tokens = 4096
    llm.parallel_workers = parallel_workers
    llm.delay = MagicMock()
    llm.config = config if config is not None else {}

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        prompt_lower = system_prompt.lower()
        if "discover" in prompt_lower or "discovery" in prompt_lower:
            return {"entities": [
                {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
                 "type": "character", "confidence": 0.9,
                 "source_turn": "turn-001"},
            ]}
        if "detail" in prompt_lower:
            if "char-player" in user_prompt.lower():
                return {"entity": None}
            return {"entity": {
                "id": "char-elder", "name": "Elder", "type": "character",
                "identity": "The village elder.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-001",
            }}
        if "relationship" in prompt_lower:
            return {"relationships": []}
        if "event" in prompt_lower:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


def _fresh_catalogs():
    catalogs = {fn: [] for fn in CATALOG_KEYS}
    se._ensure_player_character(catalogs, "turn-001")
    return catalogs


def _rel_call_max_tokens(llm):
    """Return the max_tokens passed on the relationship_mapper extract_json call."""
    for call in llm.extract_json.call_args_list:
        capture = call.kwargs.get("capture") or {}
        if capture.get("phase") == "relationship_mapper":
            return call.kwargs.get("max_tokens")
    return "NO_REL_CALL"


def _run_turn(llm, monkeypatch):
    monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
    se._reset_pc_failure_tracking()
    turn = {"turn_id": "turn-001", "speaker": "dm",
            "text": "The elder greets you warmly."}
    catalogs = _fresh_catalogs()
    se.extract_and_merge(turn, catalogs, [], llm, min_confidence=0.6)


class TestRelationshipMaxTokens:
    """The relationship_mapper call honours the relationship_max_tokens config."""

    def test_uses_relationship_max_tokens_when_set(self, monkeypatch):
        """With relationship_max_tokens=8192, the rel call uses 8192 (not 4096)."""
        llm = _make_stub_llm(config={"relationship_max_tokens": 8192})
        _run_turn(llm, monkeypatch)
        assert _rel_call_max_tokens(llm) == 8192

    def test_uses_relationship_max_tokens_in_parallel_path(self, monkeypatch):
        """The parallel extraction path also threads relationship_max_tokens."""
        llm = _make_stub_llm(config={"relationship_max_tokens": 8192},
                             parallel_workers=4)
        _run_turn(llm, monkeypatch)
        assert _rel_call_max_tokens(llm) == 8192

    def test_falls_back_to_none_when_unset(self, monkeypatch):
        """Without the key, max_tokens is None (LLM falls back to its default)."""
        llm = _make_stub_llm(config={})
        _run_turn(llm, monkeypatch)
        assert _rel_call_max_tokens(llm) is None


class TestShippedConfig:
    """The shipped config/llm.json ships the 8192 ceiling."""

    def test_shipped_config_has_relationship_max_tokens(self):
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "llm.json",
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg.get("relationship_max_tokens") == 8192
