"""Tests for tools/retry_failed_turns.py (#287).

Covers:
- load_failed_turns() extraction log parsing
- load_turn_dicts() transcript header stripping
- merge_parallel_results() entity/event/timeline merging
"""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Ensure openai mock exists
if "openai" not in sys.modules:
    _mock_openai = MagicMock()
    _mock_openai.OpenAI = MagicMock
    sys.modules["openai"] = _mock_openai

from retry_failed_turns import load_failed_turns, load_turn_dicts, merge_parallel_results


# ---------------------------------------------------------------------------
# load_failed_turns tests
# ---------------------------------------------------------------------------


class TestLoadFailedTurns:
    """Verify extraction log parsing for failed turn identification."""

    def test_identifies_failed_turns(self, tmp_path):
        log_path = str(tmp_path / "extraction-log.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({"turn_id": "turn-001", "discovery_ok": True}) + "\n")
            f.write(json.dumps({"turn_id": "turn-002", "discovery_ok": False}) + "\n")
            f.write(json.dumps({"turn_id": "turn-003", "discovery_ok": True}) + "\n")

        result = load_failed_turns(log_path)
        assert result == ["turn-002"]

    def test_success_after_failure_clears_failed(self, tmp_path):
        """If a turn fails then later succeeds, it's not in the failed list."""
        log_path = str(tmp_path / "extraction-log.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({"turn_id": "turn-005", "discovery_ok": False}) + "\n")
            f.write(json.dumps({"turn_id": "turn-005", "discovery_ok": True}) + "\n")

        result = load_failed_turns(log_path)
        assert result == []

    def test_missing_discovery_ok_not_treated_as_failure(self, tmp_path):
        """Records without discovery_ok field are skipped (not treated as failure)."""
        log_path = str(tmp_path / "extraction-log.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({"turn_id": "turn-010", "some_other_field": True}) + "\n")

        result = load_failed_turns(log_path)
        assert result == []

    def test_empty_turn_id_skipped(self, tmp_path):
        """Records with empty turn_id are ignored."""
        log_path = str(tmp_path / "extraction-log.jsonl")
        with open(log_path, "w") as f:
            f.write(json.dumps({"turn_id": "", "discovery_ok": False}) + "\n")

        result = load_failed_turns(log_path)
        assert result == []

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_failed_turns(str(tmp_path / "nonexistent.jsonl"))
        assert result == []

    def test_malformed_json_lines_skipped(self, tmp_path):
        log_path = str(tmp_path / "extraction-log.jsonl")
        with open(log_path, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"turn_id": "turn-020", "discovery_ok": False}) + "\n")

        result = load_failed_turns(log_path)
        assert result == ["turn-020"]


# ---------------------------------------------------------------------------
# load_turn_dicts tests
# ---------------------------------------------------------------------------


class TestLoadTurnDicts:
    """Verify transcript parsing and header stripping."""

    def test_strips_h2_header(self, tmp_path):
        """## header lines are stripped."""
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        (transcript_dir / "turn-001-dm.md").write_text(
            "## turn-001 — DM\n\nThe forest grows dark.\n", encoding="utf-8"
        )
        result = load_turn_dicts(str(tmp_path))
        assert len(result) == 1
        assert result[0]["text"] == "The forest grows dark."
        assert result[0]["turn_id"] == "turn-001"
        assert result[0]["speaker"] == "dm"

    def test_strips_h1_header(self, tmp_path):
        """# header lines are also stripped."""
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        (transcript_dir / "turn-050-player.md").write_text(
            "# turn-050 — PLAYER\n\nI attack the goblin.\n", encoding="utf-8"
        )
        result = load_turn_dicts(str(tmp_path))
        assert len(result) == 1
        assert result[0]["text"] == "I attack the goblin."
        assert result[0]["speaker"] == "player"

    def test_strips_blank_line_after_header(self, tmp_path):
        """Blank line following header is also stripped."""
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        (transcript_dir / "turn-002-dm.md").write_text(
            "## turn-002 — DM\n\nLine 1\nLine 2\n", encoding="utf-8"
        )
        result = load_turn_dicts(str(tmp_path))
        assert result[0]["text"] == "Line 1\nLine 2"

    def test_no_header_preserves_content(self, tmp_path):
        """Text without a header line is returned as-is."""
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        (transcript_dir / "turn-003-dm.md").write_text(
            "Just some content here.\n", encoding="utf-8"
        )
        result = load_turn_dicts(str(tmp_path))
        assert result[0]["text"] == "Just some content here."


# ---------------------------------------------------------------------------
# merge_parallel_results tests
# ---------------------------------------------------------------------------


class TestMergeParallelResults:
    """Verify merge logic for parallel extraction results."""

    def test_skips_failed_results(self):
        base_catalogs = {"characters.json": [], "locations.json": []}
        base_events = []
        base_timeline = []
        results = [
            ("turn-001", {"characters.json": [{"id": "char-x", "name": "X", "type": "character"}]},
             [], [], True, {}),  # failed=True
        ]
        cats, evts, tl = merge_parallel_results(base_catalogs, base_events, base_timeline, results)
        assert len(cats["characters.json"]) == 0

    def test_merges_new_entities(self):
        base_catalogs = {"characters.json": [], "locations.json": []}
        base_events = []
        base_timeline = []
        results = [
            ("turn-001", {"characters.json": [{"id": "char-a", "name": "A", "type": "character", "first_seen_turn": "turn-001", "identity": "A the warrior"}]},
             [], [], False, {}),
        ]
        cats, evts, tl = merge_parallel_results(base_catalogs, base_events, base_timeline, results)
        assert any(e["id"] == "char-a" for e in cats["characters.json"])

    def test_deduplicates_events_by_id(self):
        base_events = [{"id": "evt-001", "description": "existing"}]
        results = [
            ("turn-001", {"characters.json": []},
             [{"id": "evt-001", "description": "dupe"}, {"id": "evt-002", "description": "new"}],
             [], False, {}),
        ]
        _, evts, _ = merge_parallel_results({"characters.json": []}, base_events, [], results)
        ids = [e["id"] for e in evts]
        assert ids.count("evt-001") == 1
        assert "evt-002" in ids

    def test_merges_timeline_with_proper_dedup(self):
        base_timeline = [
            {"id": "time-001", "source_turn": "turn-001", "type": "biological",
             "season": "summer", "raw_text": "flowers bloom"},
        ]
        new_signals = [
            # Duplicate — same key
            {"source_turn": "turn-001", "type": "biological",
             "season": "summer", "raw_text": "flowers bloom"},
            # New — different raw_text
            {"source_turn": "turn-001", "type": "weather",
             "season": "summer", "raw_text": "rain falls"},
        ]
        results = [
            ("turn-001", {"characters.json": []}, [], new_signals, False, {}),
        ]
        _, _, tl = merge_parallel_results({"characters.json": []}, [], base_timeline, results)
        # Should have original + 1 new (the dupe should not be added)
        assert len(tl) == 2
        raw_texts = [e.get("raw_text") for e in tl]
        assert "flowers bloom" in raw_texts
        assert "rain falls" in raw_texts
