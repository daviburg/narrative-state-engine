"""Tests for structured per-turn extraction log (extraction-log.jsonl) (#217)."""

import json
import os
import sys

import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_stub_llm(fail_discovery=False, fail_events=False, fail_relationships=False):
    """Build a stub LLM that returns minimal valid responses."""
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None, schema=None):
        prompt_lower = system_prompt.lower()
        if "discover" in prompt_lower or "discovery" in prompt_lower:
            if fail_discovery:
                raise se.LLMExtractionError("429 quota exhausted")
            return {"entities": []}
        if "detail" in prompt_lower:
            return {"entity": {
                "id": "char-player",
                "name": "Player Character",
                "type": "character",
                "identity": "The player character.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-001",
            }}
        if "relationship" in prompt_lower:
            if fail_relationships:
                raise se.LLMExtractionError("429 quota exhausted")
            return {"relationships": []}
        if "event" in prompt_lower:
            if fail_events:
                raise se.LLMExtractionError("429 quota exhausted")
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


class TestExtractAndMergeLogRecord:
    """extract_and_merge returns a per-turn log record as 4th element."""

    def test_success_log_record(self, monkeypatch):
        """Successful extraction returns a log record with all phases ok."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm()
        catalogs = _fresh_catalogs()
        events = []
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "The DM speaks."}

        _, _, failed, log = se.extract_and_merge(
            turn, catalogs, events, llm, min_confidence=0.6,
        )

        assert failed is False
        assert log["turn_id"] == "turn-001"
        assert log["discovery_ok"] is True
        assert log["discovery_error"] is None
        assert log["events_ok"] is True
        assert log["events_error"] is None
        assert log["relationships_ok"] is True
        assert log["relationships_error"] is None
        assert isinstance(log["elapsed_ms"], int)
        assert log["elapsed_ms"] >= 0
        assert "timestamp" in log
        assert isinstance(log["new_entities"], int)
        assert isinstance(log["new_events"], int)

    def test_discovery_failure_log_record(self, monkeypatch):
        """Discovery failure is recorded in log record."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(fail_discovery=True)
        catalogs = _fresh_catalogs()
        events = []
        turn = {"turn_id": "turn-002", "speaker": "dm", "text": "Another turn."}

        _, _, failed, log = se.extract_and_merge(
            turn, catalogs, events, llm, min_confidence=0.6,
        )

        assert failed is True
        assert log["discovery_ok"] is False
        assert "429" in log["discovery_error"]

    def test_event_failure_log_record(self, monkeypatch):
        """Event extraction failure is recorded in log record."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        llm = _make_stub_llm(fail_events=True)
        catalogs = _fresh_catalogs()
        events = []
        turn = {"turn_id": "turn-003", "speaker": "dm", "text": "Yet another turn."}

        _, _, failed, log = se.extract_and_merge(
            turn, catalogs, events, llm, min_confidence=0.6,
        )

        assert failed is True
        assert log["events_ok"] is False
        assert "429" in log["events_error"]
        # Discovery should have succeeded
        assert log["discovery_ok"] is True


class TestExtractionLogFile:
    """extraction-log.jsonl is written during batch extraction."""

    def test_batch_writes_log_file(self, monkeypatch, tmp_path):
        """extract_semantic_batch writes extraction-log.jsonl."""
        session_dir = str(tmp_path / "sessions" / "test")
        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(os.path.join(session_dir, "derived"), exist_ok=True)
        os.makedirs(catalog_dir, exist_ok=True)

        call_count = [0]

        def mock_extract(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            call_count[0] += 1
            log = {
                "turn_id": turn["turn_id"],
                "timestamp": "2026-04-24T00:00:00+00:00",
                "discovery_ok": True, "discovery_error": None,
                "detail_ok": True, "detail_error": None,
                "pc_ok": True, "pc_error": None,
                "relationships_ok": True, "relationships_error": None,
                "events_ok": True, "events_error": None,
                "new_entities": 0, "new_events": 0, "elapsed_ms": 100,
            }
            return catalogs, events, False, log

        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"Turn {i}."}
                 for i in range(1, 4)]

        monkeypatch.setattr("semantic_extraction.LLMClient", lambda *a, **kw: MagicMock(
            config={"checkpoint_interval": 100}))
        monkeypatch.setattr("semantic_extraction.load_catalogs", lambda d: _fresh_catalogs())
        monkeypatch.setattr("semantic_extraction.load_events", lambda d: [])
        monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.extract_and_merge", mock_extract)
        monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr("semantic_extraction._ensure_player_character", lambda *a: None)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
            config_path="unused",
        )

        log_path = os.path.join(framework_dir, "extraction-log.jsonl")
        assert os.path.isfile(log_path), "extraction-log.jsonl not created"

        lines = open(log_path, "r", encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 3

        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["turn_id"] == f"turn-{i+1:03d}"
            assert record["discovery_ok"] is True

    def test_dry_run_skips_log(self, monkeypatch, tmp_path):
        """dry_run=True should NOT write the extraction log."""
        session_dir = str(tmp_path / "sessions" / "test")
        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(os.path.join(session_dir, "derived"), exist_ok=True)
        os.makedirs(catalog_dir, exist_ok=True)

        def mock_extract(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            return catalogs, events, False, {"turn_id": turn["turn_id"]}

        turns = [{"turn_id": "turn-001", "speaker": "dm", "text": "T1."}]

        monkeypatch.setattr("semantic_extraction.LLMClient", lambda *a, **kw: MagicMock(
            config={"checkpoint_interval": 100}))
        monkeypatch.setattr("semantic_extraction.load_catalogs", lambda d: _fresh_catalogs())
        monkeypatch.setattr("semantic_extraction.load_events", lambda d: [])
        monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.extract_and_merge", mock_extract)
        monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr("semantic_extraction._ensure_player_character", lambda *a: None)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
            config_path="unused", dry_run=True,
        )

        log_path = os.path.join(framework_dir, "extraction-log.jsonl")
        assert not os.path.isfile(log_path), "extraction-log.jsonl should not exist in dry_run"

    def test_failed_turn_logged(self, monkeypatch, tmp_path):
        """A turn that fails extraction still gets a log record."""
        session_dir = str(tmp_path / "sessions" / "test")
        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(os.path.join(session_dir, "derived"), exist_ok=True)
        os.makedirs(catalog_dir, exist_ok=True)

        call_count = [0]

        def mock_extract(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            call_count[0] += 1
            if call_count[0] == 2:
                # Simulate a turn with discovery failure
                log = {
                    "turn_id": turn["turn_id"],
                    "timestamp": "2026-04-24T00:00:00+00:00",
                    "discovery_ok": False, "discovery_error": "429 quota exhausted",
                    "detail_ok": True, "detail_error": None,
                    "pc_ok": True, "pc_error": None,
                    "relationships_ok": True, "relationships_error": None,
                    "events_ok": True, "events_error": None,
                    "new_entities": 0, "new_events": 0, "elapsed_ms": 50,
                }
                return catalogs, events, True, log
            log = {
                "turn_id": turn["turn_id"],
                "timestamp": "2026-04-24T00:00:00+00:00",
                "discovery_ok": True, "discovery_error": None,
                "detail_ok": True, "detail_error": None,
                "pc_ok": True, "pc_error": None,
                "relationships_ok": True, "relationships_error": None,
                "events_ok": True, "events_error": None,
                "new_entities": 1, "new_events": 0, "elapsed_ms": 100,
            }
            return catalogs, events, False, log

        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"Turn {i}."}
                 for i in range(1, 4)]

        monkeypatch.setattr("semantic_extraction.LLMClient", lambda *a, **kw: MagicMock(
            config={"checkpoint_interval": 100}))
        monkeypatch.setattr("semantic_extraction.load_catalogs", lambda d: _fresh_catalogs())
        monkeypatch.setattr("semantic_extraction.load_events", lambda d: [])
        monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.extract_and_merge", mock_extract)
        monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr("semantic_extraction._ensure_player_character", lambda *a: None)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
            config_path="unused",
        )

        log_path = os.path.join(framework_dir, "extraction-log.jsonl")
        lines = open(log_path, "r", encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 3

        # Second turn should record the failure
        record = json.loads(lines[1])
        assert record["turn_id"] == "turn-002"
        assert record["discovery_ok"] is False
        assert "429" in record["discovery_error"]

    def test_exception_in_extract_writes_log(self, monkeypatch, tmp_path):
        """When extract_and_merge raises an exception, a log record is still written."""
        session_dir = str(tmp_path / "sessions" / "test")
        framework_dir = str(tmp_path / "framework")
        catalog_dir = os.path.join(framework_dir, "catalogs")
        os.makedirs(os.path.join(session_dir, "derived"), exist_ok=True)
        os.makedirs(catalog_dir, exist_ok=True)

        call_count = [0]

        def mock_extract(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Simulated crash")
            log = {
                "turn_id": turn["turn_id"],
                "timestamp": "2026-04-24T00:00:00+00:00",
                "discovery_ok": True, "discovery_error": None,
                "detail_ok": True, "detail_error": None,
                "pc_ok": True, "pc_error": None,
                "relationships_ok": True, "relationships_error": None,
                "events_ok": True, "events_error": None,
                "new_entities": 0, "new_events": 0, "elapsed_ms": 100,
            }
            return catalogs, events, False, log

        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"Turn {i}."}
                 for i in range(1, 4)]

        monkeypatch.setattr("semantic_extraction.LLMClient", lambda *a, **kw: MagicMock(
            config={"checkpoint_interval": 100}))
        monkeypatch.setattr("semantic_extraction.load_catalogs", lambda d: _fresh_catalogs())
        monkeypatch.setattr("semantic_extraction.load_events", lambda d: [])
        monkeypatch.setattr("semantic_extraction.save_catalogs", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.save_events", lambda *a, **kw: None)
        monkeypatch.setattr("semantic_extraction.extract_and_merge", mock_extract)
        monkeypatch.setattr("semantic_extraction._dedup_catalogs", lambda cats: (0, {}))
        monkeypatch.setattr("semantic_extraction._post_batch_orphan_sweep", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction._name_mention_discovery", lambda cats, evts: 0)
        monkeypatch.setattr("semantic_extraction.cleanup_dangling_relationships", lambda cats: {})
        monkeypatch.setattr("semantic_extraction._ensure_player_character", lambda *a: None)

        se.extract_semantic_batch(
            turns, session_dir, framework_dir=framework_dir,
            config_path="unused",
        )

        log_path = os.path.join(framework_dir, "extraction-log.jsonl")
        lines = open(log_path, "r", encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 3

        # Second turn crashed — log record should indicate failure
        record = json.loads(lines[1])
        assert record["turn_id"] == "turn-002"
        assert record["discovery_ok"] is False
        assert "Simulated crash" in record["discovery_error"]


class TestWriteExtractionLogHelper:
    """Unit tests for _write_extraction_log."""

    def test_appends_to_file(self, tmp_path):
        log_path = str(tmp_path / "extraction-log.jsonl")
        record1 = {"turn_id": "turn-001", "ok": True}
        record2 = {"turn_id": "turn-002", "ok": False}

        se._write_extraction_log(log_path, record1)
        se._write_extraction_log(log_path, record2)

        lines = open(log_path, "r", encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["turn_id"] == "turn-001"
        assert json.loads(lines[1])["turn_id"] == "turn-002"

    def test_creates_directory(self, tmp_path):
        log_path = str(tmp_path / "nested" / "dir" / "extraction-log.jsonl")
        se._write_extraction_log(log_path, {"turn_id": "turn-001"})
        assert os.path.isfile(log_path)

    def test_silently_handles_write_error(self, monkeypatch, tmp_path):
        """Write errors must not propagate — extraction continues."""
        log_path = str(tmp_path / "extraction-log.jsonl")
        # Force an OSError by making the path a directory
        os.makedirs(log_path, exist_ok=True)
        # Should not raise
        se._write_extraction_log(log_path, {"turn_id": "turn-001"})
