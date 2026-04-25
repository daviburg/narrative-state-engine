"""Tests for per-turn extraction failure tracking (#211)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from unittest.mock import MagicMock

import semantic_extraction as se


def _fresh_catalogs():
    return {fn: [] for fn in se.CATALOG_KEYS}


def _make_stub_llm(fail_on_turns=None):
    """Create a stub LLM that fails discovery on specific turn IDs."""
    fail_on_turns = set(fail_on_turns or [])
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    def _is_discovery_prompt(system_prompt):
        prompt = system_prompt.lower()
        return "entity-discovery" in prompt or "discovery" in prompt

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None, schema=None):
        # Detect which turn we're processing from the user prompt
        for tid in fail_on_turns:
            if tid in user_prompt and _is_discovery_prompt(system_prompt):
                raise se.LLMExtractionError(f"429 quota exhausted for {tid}")
        if _is_discovery_prompt(system_prompt):
            return {"entities": []}
        if "detail" in system_prompt.lower():
            return {"entity": {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-001",
            }}
        if "relationship" in system_prompt.lower():
            return {"relationships": []}
        if "event" in system_prompt.lower():
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


# --- extract_and_merge returns turn_failed ---

class TestExtractAndMergeTurnFailed:
    """extract_and_merge should return turn_failed=True on LLM failure."""

    def test_discovery_success_returns_false(self, monkeypatch):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm()
        catalogs = _fresh_catalogs()
        events = []
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Hello world."}
        catalogs, events, failed = se.extract_and_merge(
            turn, catalogs, events, llm, min_confidence=0.6,
        )
        assert failed is False

    def test_discovery_failure_returns_true(self, monkeypatch):
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(fail_on_turns={"turn-001"})
        catalogs = _fresh_catalogs()
        events = []
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "turn-001 Hello world."}
        catalogs, events, failed = se.extract_and_merge(
            turn, catalogs, events, llm, min_confidence=0.6,
        )
        assert failed is True


# --- extract_semantic_batch tracks failed turns ---

class TestBatchFailedTurnTracking:
    """extract_semantic_batch should record failed turns in progress file."""

    def test_failed_turns_recorded_in_progress(self, monkeypatch, tmp_path):
        """When LLM fails for specific turns, they appear in failed_turns."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        session_dir = str(tmp_path / "session")
        derived_dir = os.path.join(session_dir, "derived")
        os.makedirs(derived_dir, exist_ok=True)

        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)

        fail_turns = {"turn-003", "turn-005"}
        llm = _make_stub_llm(fail_on_turns=fail_turns)

        turns = []
        for i in range(1, 6):
            tid = f"turn-{i:03d}"
            turns.append({
                "turn_id": tid,
                "speaker": "dm",
                "text": f"{tid} The DM describes turn {i}.",
            })

        monkeypatch.setattr(se, "LLMClient", lambda *a, **kw: llm)
        monkeypatch.setattr(se, "_dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr(se, "cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr(se, "_post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr(se, "_name_mention_discovery", lambda cats, evts: 0)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
        )

        progress_file = os.path.join(derived_dir, "extraction-progress.json")
        assert os.path.exists(progress_file)
        with open(progress_file, "r", encoding="utf-8") as f:
            progress = json.load(f)

        assert "failed_turns" in progress
        assert set(progress["failed_turns"]) == fail_turns
        assert progress["completed"] is True

    def test_no_failed_turns_key_when_all_succeed(self, monkeypatch, tmp_path):
        """When all turns succeed, failed_turns should not appear in progress."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        session_dir = str(tmp_path / "session")
        derived_dir = os.path.join(session_dir, "derived")
        os.makedirs(derived_dir, exist_ok=True)

        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)

        llm = _make_stub_llm()

        turns = [
            {"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"turn-{i:03d} Turn {i}."}
            for i in range(1, 4)
        ]

        monkeypatch.setattr(se, "LLMClient", lambda *a, **kw: llm)
        monkeypatch.setattr(se, "_dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr(se, "cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr(se, "_post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr(se, "_name_mention_discovery", lambda cats, evts: 0)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
        )

        progress_file = os.path.join(derived_dir, "extraction-progress.json")
        assert os.path.exists(progress_file)
        with open(progress_file, "r", encoding="utf-8") as f:
            progress = json.load(f)

        assert "failed_turns" not in progress

    def test_failed_turn_summary_logged(self, monkeypatch, tmp_path, capsys):
        """Failed turn summary should be printed to stderr."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        session_dir = str(tmp_path / "session")
        derived_dir = os.path.join(session_dir, "derived")
        os.makedirs(derived_dir, exist_ok=True)

        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)

        llm = _make_stub_llm(fail_on_turns={"turn-002"})

        turns = [
            {"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"turn-{i:03d} Turn {i}."}
            for i in range(1, 4)
        ]

        monkeypatch.setattr(se, "LLMClient", lambda *a, **kw: llm)
        monkeypatch.setattr(se, "_dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr(se, "cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr(se, "_post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr(se, "_name_mention_discovery", lambda cats, evts: 0)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
        )

        captured = capsys.readouterr()
        assert "1 turn(s) had extraction failures" in captured.err
        assert "turn-002" in captured.err
        assert "re-extracted" in captured.err


# --- Resume re-attempts previously failed turns ---

class TestResumeRetry:
    """On resume, previously failed turns should be re-attempted."""

    def test_resume_retries_failed_turns(self, monkeypatch, tmp_path):
        """Resuming with failed_turns in progress should re-attempt them."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        session_dir = str(tmp_path / "session")
        derived_dir = os.path.join(session_dir, "derived")
        os.makedirs(derived_dir, exist_ok=True)

        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)

        # Write a progress file that indicates turn-002 failed but completed to turn-003
        progress_file = os.path.join(derived_dir, "extraction-progress.json")
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump({
                "last_completed_turn": "turn-003",
                "total_turns": 3,
                "entities_discovered": 1,
                "completed": False,
                "failed_turns": ["turn-002"],
            }, f)

        # LLM that succeeds on all turns now (quota recovered)
        llm = _make_stub_llm()

        turns = [
            {"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"turn-{i:03d} Turn {i}."}
            for i in range(1, 4)
        ]

        monkeypatch.setattr(se, "LLMClient", lambda *a, **kw: llm)
        monkeypatch.setattr(se, "_dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr(se, "cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr(se, "_post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr(se, "_name_mention_discovery", lambda cats, evts: 0)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
        )

        # After successful retry, failed_turns should not be in progress
        with open(progress_file, "r", encoding="utf-8") as f:
            progress = json.load(f)

        assert "failed_turns" not in progress
        assert progress["completed"] is True
