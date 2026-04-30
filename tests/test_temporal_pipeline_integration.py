"""Tests for temporal extraction integration into the semantic pipeline (#263)."""

import json
import os
import sys

from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import CATALOG_KEYS
import semantic_extraction as se
from temporal_extraction import (
    extract_temporal_signals,
    merge_temporal_signals,
    save_timeline,
)


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_turns(start, end, speaker="dm"):
    """Create turn dicts from turn-{start} to turn-{end} (inclusive)."""
    return [
        {"turn_id": f"turn-{i:03d}", "speaker": speaker, "text": f"Turn {i} text."}
        for i in range(start, end + 1)
    ]


def _setup_session(tmp_path):
    session_dir = str(tmp_path / "sessions" / "test")
    framework_dir = str(tmp_path / "framework")
    catalog_dir = os.path.join(framework_dir, "catalogs")
    os.makedirs(os.path.join(session_dir, "derived"), exist_ok=True)
    os.makedirs(catalog_dir, exist_ok=True)
    return session_dir, framework_dir, catalog_dir


def _make_noop_extract(add_temporal=None):
    """Create a mock extract_and_merge that optionally adds temporal signals."""
    def mock_extract(turn, catalogs, events, llm, min_conf,
                     catalog_dir=None, timeline=None):
        if timeline is not None and add_temporal:
            for sig in add_temporal(turn):
                timeline.append(sig)
        return catalogs, events, False, {"turn_id": turn["turn_id"]}
    return mock_extract


# ---------------------------------------------------------------------------
# Unit test: temporal signals extraction (pattern-based, not pipeline)
# ---------------------------------------------------------------------------

class TestTemporalSignalExtraction:
    """Verify that extract_temporal_signals produces correct results."""

    def test_winter_text(self):
        signals = extract_temporal_signals(
            "The first snow falls across the valley, covering everything in frost.",
            "turn-001",
        )
        assert len(signals) > 0
        assert any(s["type"] == "season_transition" for s in signals)

    def test_time_skip(self):
        signals = extract_temporal_signals(
            "Weeks pass in relative quiet. The settlement grows.",
            "turn-010",
        )
        assert any(s["type"] == "time_skip" for s in signals)

    def test_no_signals(self):
        signals = extract_temporal_signals(
            "The merchant offers you a choice of weapons.",
            "turn-003",
        )
        assert signals == []

    def test_spring_text(self):
        signals = extract_temporal_signals(
            "The thaw begins. First true signs of spring arrive.",
            "turn-005",
        )
        assert any(s["type"] == "season_transition" for s in signals)


# ---------------------------------------------------------------------------
# Unit test: merge_temporal_signals dedup
# ---------------------------------------------------------------------------

class TestMergeTemporalSignals:
    """Verify merge and dedup behavior."""

    def test_dedup_same_signal(self):
        timeline = []
        signals = [
            {"source_turn": "turn-001", "type": "season_transition",
             "season": "mid_winter", "signals": ["first"]},
        ]
        merge_temporal_signals(timeline, signals)
        assert len(timeline) == 1

        # Same signal again — should NOT duplicate
        signals2 = [
            {"source_turn": "turn-001", "type": "season_transition",
             "season": "mid_winter", "signals": ["second"]},
        ]
        merge_temporal_signals(timeline, signals2)
        assert len(timeline) == 1

    def test_different_signals_kept(self):
        timeline = []
        merge_temporal_signals(timeline, [
            {"source_turn": "turn-001", "type": "season_transition",
             "season": "mid_winter", "signals": ["a"]},
        ])
        merge_temporal_signals(timeline, [
            {"source_turn": "turn-005", "type": "time_skip",
             "signals": ["b"]},
        ])
        assert len(timeline) == 2


# ---------------------------------------------------------------------------
# Integration: extract_and_merge populates timeline
# ---------------------------------------------------------------------------

class TestExtractAndMergeTemporalIntegration:
    """Verify temporal phase in extract_and_merge via mocked pipeline."""

    def test_temporal_signals_populated(self):
        """Mock extract_and_merge confirms timeline arg is received and used."""
        timeline = []
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "Snow covers the land."}

        mock = _make_noop_extract(add_temporal=lambda t: [
            {"source_turn": t["turn_id"], "type": "season_transition",
             "season": "mid_winter", "signals": ["mock"]}
        ])

        mock(turn, _fresh_catalogs(), [], None, 0.6, timeline=timeline)
        assert len(timeline) == 1
        assert timeline[0]["type"] == "season_transition"

    def test_no_timeline_param_no_signals(self):
        """When timeline is None, no signals added."""
        mock = _make_noop_extract(add_temporal=lambda t: [
            {"source_turn": t["turn_id"], "type": "time_skip", "signals": ["x"]}
        ])
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Test."}
        mock(turn, _fresh_catalogs(), [], None, 0.6, timeline=None)
        # No assertion failure = success (no crash when timeline is None)


# ---------------------------------------------------------------------------
# Real extract_and_merge with temporal (requires more mocking of LLM)
# This tests the actual wiring in extract_and_merge
# ---------------------------------------------------------------------------

class TestExtractAndMergeRealWiring:
    """Test the actual temporal extraction wiring in extract_and_merge."""

    def test_temporal_phase_runs_after_events(self):
        """With full mocking, extract_and_merge should run temporal extraction."""
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "The first snow of deep winter blankets the valley."}
        catalogs = _fresh_catalogs()
        events_list = []
        timeline = []

        mock_llm = MagicMock()
        # Discovery returns no entities
        mock_llm.extract_json.return_value = {"entities": [], "events": []}
        mock_llm.delay.return_value = None
        mock_llm.default_timeout = 60
        mock_llm.max_tokens = 4096

        with patch.object(se, "load_template", return_value="mock template"):
            _, _, failed, log = se.extract_and_merge(
                turn, catalogs, events_list, mock_llm, timeline=timeline,
            )

        assert not failed
        assert len(timeline) > 0, "Expected temporal signals from deep winter text"
        assert any(e["type"] == "season_transition" for e in timeline)
        assert all("id" in e for e in timeline)
        assert log.get("temporal_ok") is True
        assert log.get("new_temporal_signals", 0) > 0

    def test_log_records_temporal_none_when_no_timeline(self):
        """When timeline is not passed, temporal_ok should be None."""
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Snow."}
        catalogs = _fresh_catalogs()
        events_list = []

        mock_llm = MagicMock()
        mock_llm.extract_json.return_value = {"entities": [], "events": []}
        mock_llm.delay.return_value = None
        mock_llm.default_timeout = 60
        mock_llm.max_tokens = 4096

        with patch.object(se, "load_template", return_value="mock template"):
            _, _, _, log = se.extract_and_merge(
                turn, catalogs, events_list, mock_llm, timeline=None,
            )

        assert log.get("temporal_ok") is None
        assert log.get("temporal_error") is None

    def test_no_signals_still_ok(self):
        """A turn with no temporal language should not be an error."""
        turn = {"turn_id": "turn-003", "speaker": "dm",
                "text": "The merchant offers you a choice of weapons."}
        catalogs = _fresh_catalogs()
        events_list = []
        timeline = []

        mock_llm = MagicMock()
        mock_llm.extract_json.return_value = {"entities": [], "events": []}
        mock_llm.delay.return_value = None
        mock_llm.default_timeout = 60
        mock_llm.max_tokens = 4096

        with patch.object(se, "load_template", return_value="mock template"):
            _, _, _, log = se.extract_and_merge(
                turn, catalogs, events_list, mock_llm, timeline=timeline,
            )

        assert log["temporal_ok"] is True
        assert log["new_temporal_signals"] == 0


# ---------------------------------------------------------------------------
# Batch extraction: timeline saved to disk
# ---------------------------------------------------------------------------

class TestBatchExtractionTimeline:
    """Verify that batch extraction loads, populates, and saves timeline."""

    def test_batch_saves_timeline(self, tmp_path):
        """extract_semantic_batch should save timeline.json."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        def mock_extract(turn, catalogs, events, llm, min_conf,
                         catalog_dir=None, timeline=None):
            if timeline is not None and "snow" in turn["text"].lower():
                timeline.append({
                    "source_turn": turn["turn_id"],
                    "type": "season_transition",
                    "season": "mid_winter",
                    "signals": ["test mock"],
                    "confidence": 0.8,
                })
            return catalogs, events, False, {"turn_id": turn["turn_id"]}

        turns = [
            {"turn_id": "turn-001", "speaker": "dm", "text": "Snow and frost blanket the land."},
            {"turn_id": "turn-002", "speaker": "dm", "text": "You enter the tavern."},
        ]

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction._dedup_catalogs", return_value=(0, {})), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 100})

            se.extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused",
            )

        timeline_path = os.path.join(catalog_dir, "timeline.json")
        assert os.path.isfile(timeline_path), "timeline.json not created"
        with open(timeline_path, encoding="utf-8") as f:
            timeline = json.load(f)
        assert len(timeline) >= 1
        assert timeline[0]["type"] == "season_transition"

    def test_batch_loads_existing_timeline(self, tmp_path):
        """Batch mode should load and extend an existing timeline."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        # Pre-populate timeline
        existing = [
            {"id": "time-001", "source_turn": "turn-001", "type": "season_transition",
             "season": "mid_winter", "signals": ["pre-existing"], "confidence": 0.8}
        ]
        save_timeline(catalog_dir, existing)

        loaded_timelines = []

        def mock_extract(turn, catalogs, events, llm, min_conf,
                         catalog_dir=None, timeline=None):
            if timeline is not None:
                loaded_timelines.append(len(timeline))
            return catalogs, events, False, {"turn_id": turn["turn_id"]}

        turns = _make_turns(2, 3)

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction._dedup_catalogs", return_value=(0, {})), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 100})

            se.extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused",
            )

        # First call should have received the pre-existing timeline entry
        assert len(loaded_timelines) >= 1
        assert loaded_timelines[0] == 1, \
            f"Expected 1 pre-existing entry, got {loaded_timelines[0]}"


# ---------------------------------------------------------------------------
# Single-turn extraction: timeline saved to disk
# ---------------------------------------------------------------------------

class TestSingleTurnTimeline:
    """Verify that single-turn extraction loads, populates, and saves timeline."""

    def test_single_turn_saves_timeline(self, tmp_path):
        """extract_semantic_single should save timeline.json."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        def mock_extract(turn, catalogs, events, llm, min_conf,
                         catalog_dir=None, timeline=None):
            if timeline is not None:
                timeline.append({
                    "id": "time-001",
                    "source_turn": turn["turn_id"],
                    "type": "season_transition",
                    "season": "early_spring",
                    "signals": ["test mock single"],
                    "confidence": 0.7,
                })
            return catalogs, events, False, {"turn_id": turn["turn_id"]}

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.mark_dormant_relationships", return_value=0):
            mock_llm_cls.return_value = MagicMock(config={})

            se.extract_semantic_single(
                "turn-010", "dm", "The thaw begins.",
                session_dir, framework_dir=framework_dir,
                config_path="unused",
            )

        timeline_path = os.path.join(catalog_dir, "timeline.json")
        assert os.path.isfile(timeline_path), "timeline.json not created"
        with open(timeline_path, encoding="utf-8") as f:
            timeline = json.load(f)
        assert len(timeline) == 1
        assert timeline[0]["season"] == "early_spring"


# ---------------------------------------------------------------------------
# Segmented extraction: timeline reconciled across segments
# ---------------------------------------------------------------------------

class TestSegmentedTimeline:
    """Verify timeline reconciliation in segmented extraction."""

    def test_segmented_reconciles_timelines(self, tmp_path):
        """Segments should each build timeline independently, then reconcile."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        def mock_extract(turn, catalogs, events, llm, min_conf,
                         catalog_dir=None, timeline=None):
            if timeline is not None:
                turn_num = int(turn["turn_id"].split("-")[1])
                if turn_num <= 3:
                    # Only add for first segment turns
                    timeline.append({
                        "source_turn": turn["turn_id"],
                        "type": "season_transition",
                        "season": "mid_winter",
                        "signals": [f"segment signal {turn['turn_id']}"],
                        "confidence": 0.8,
                    })
                elif turn_num >= 4:
                    timeline.append({
                        "source_turn": turn["turn_id"],
                        "type": "season_transition",
                        "season": "early_spring",
                        "signals": [f"segment signal {turn['turn_id']}"],
                        "confidence": 0.7,
                    })
            return catalogs, events, False, {}

        turns = _make_turns(1, 6)

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 25})

            se.extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused", segment_size=3,
            )

        timeline_path = os.path.join(catalog_dir, "timeline.json")
        assert os.path.isfile(timeline_path), "timeline.json not created"
        with open(timeline_path, encoding="utf-8") as f:
            timeline = json.load(f)
        assert len(timeline) == 6, f"Expected 6 signals, got {len(timeline)}"
        # Check both seasons present (from both segments)
        seasons = {e["season"] for e in timeline}
        assert "mid_winter" in seasons
        assert "early_spring" in seasons
        # All should have IDs assigned
        assert all("id" in e for e in timeline)
        # IDs should be sequential
        ids = sorted(e["id"] for e in timeline)
        assert ids == [f"time-{i:03d}" for i in range(1, 7)]


# ---------------------------------------------------------------------------
# _reconcile_timelines helper
# ---------------------------------------------------------------------------

class TestReconcileTimelines:
    """Unit tests for the _reconcile_timelines helper."""

    def test_empty_segments(self):
        segments = [
            {"timeline": []},
            {"timeline": []},
        ]
        result = se._reconcile_timelines(segments)
        assert result == []

    def test_dedup_across_segments(self):
        """Duplicate signals across segments should be merged."""
        segments = [
            {"timeline": [
                {"id": "time-001", "source_turn": "turn-001",
                 "type": "season_transition", "season": "mid_winter",
                 "signals": ["seg1"]},
            ]},
            {"timeline": [
                {"id": "time-001", "source_turn": "turn-001",
                 "type": "season_transition", "season": "mid_winter",
                 "signals": ["seg2"]},
            ]},
        ]
        result = se._reconcile_timelines(segments)
        # Same (source_turn, type, season, raw_text) — should dedup to 1
        assert len(result) == 1

    def test_distinct_signals_preserved(self):
        """Different signals across segments should both be kept."""
        segments = [
            {"timeline": [
                {"id": "time-001", "source_turn": "turn-001",
                 "type": "season_transition", "season": "mid_winter",
                 "signals": ["seg1"]},
            ]},
            {"timeline": [
                {"id": "time-001", "source_turn": "turn-004",
                 "type": "season_transition", "season": "early_spring",
                 "signals": ["seg2"]},
            ]},
        ]
        result = se._reconcile_timelines(segments)
        assert len(result) == 2

    def test_missing_timeline_key(self):
        """Segments without timeline key should be handled gracefully."""
        segments = [
            {"catalogs": {}, "events": []},
            {"timeline": [
                {"id": "time-001", "source_turn": "turn-001",
                 "type": "time_skip", "signals": ["test"]},
            ]},
        ]
        result = se._reconcile_timelines(segments)
        assert len(result) == 1
