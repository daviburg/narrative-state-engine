"""Tests for intra-turn parallel extraction path (#282)."""

import os
import sys
import threading
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from catalog_merger import CATALOG_KEYS


def _fresh_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_parallel_llm(discovery_entities=None, parallel_workers=4):
    """Build a stub LLM with parallel_workers > 1 and thread-tracking."""
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.parallel_workers = parallel_workers
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    if discovery_entities is None:
        discovery_entities = []

    # Track which threads called extract_json to verify concurrency
    call_threads = []
    call_lock = threading.Lock()

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None):
        with call_lock:
            call_threads.append(threading.current_thread().ident)

        prompt_lower = system_prompt.lower()
        if "discover" in prompt_lower or "discovery" in prompt_lower:
            return {"entities": discovery_entities}
        if "detail" in prompt_lower:
            # Return a valid entity based on what's in the user prompt
            if "char-player" in user_prompt.lower():
                return {"entity": {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-001",
                }}
            return {"entity": {
                "id": "char-elder",
                "name": "Elder",
                "type": "character",
                "identity": "The village elder.",
                "first_seen_turn": "turn-001",
                "last_updated_turn": "turn-001",
            }}
        if "relationship" in prompt_lower:
            return {"relationships": [
                {"source_id": "char-elder", "target_id": "char-player",
                 "type": "knows", "description": "met recently",
                 "source_turn": "turn-001"},
            ]}
        if "event" in prompt_lower:
            return {"events": [
                {"id": "evt-001", "type": "encounter",
                 "description": "Met the elder",
                 "source_turn": "turn-001",
                 "related_entities": ["char-elder", "char-player"]},
            ]}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    llm._call_threads = call_threads
    return llm


class TestParallelExtraction:
    """Tests for the parallel execution path in extract_and_merge()."""

    def test_parallel_produces_same_results_as_sequential(self, monkeypatch):
        """Parallel and sequential paths produce equivalent catalogs and events."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        entities = [
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
        ]
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "The elder greets you warmly."}

        # Run sequential
        se._reset_pc_failure_tracking()
        seq_llm = _make_parallel_llm(discovery_entities=entities, parallel_workers=1)
        seq_catalogs = _fresh_catalogs()
        seq_events = []
        seq_catalogs, seq_events, seq_failed, seq_log = se.extract_and_merge(
            turn, seq_catalogs, seq_events, seq_llm, min_confidence=0.6,
        )

        # Run parallel
        se._reset_pc_failure_tracking()
        par_llm = _make_parallel_llm(discovery_entities=entities, parallel_workers=4)
        par_catalogs = _fresh_catalogs()
        par_events = []
        par_catalogs, par_events, par_failed, par_log = se.extract_and_merge(
            turn, par_catalogs, par_events, par_llm, min_confidence=0.6,
        )

        # Both paths should produce same entity counts
        seq_entity_count = sum(len(v) for v in seq_catalogs.values())
        par_entity_count = sum(len(v) for v in par_catalogs.values())
        assert seq_entity_count == par_entity_count, (
            f"Sequential produced {seq_entity_count} entities, "
            f"parallel produced {par_entity_count}"
        )

        # Both paths should produce same event counts
        assert len(seq_events) == len(par_events), (
            f"Sequential produced {len(seq_events)} events, "
            f"parallel produced {len(par_events)}"
        )

        # Log records should have same phase outcomes
        for key in ("discovery_ok", "detail_ok", "events_ok"):
            assert seq_log[key] == par_log[key], f"Mismatch on {key}"

    def test_parallel_delay_called_once(self, monkeypatch):
        """In parallel mode, llm.delay() is called once per turn, not per-entity."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        entities = [
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
            {"name": "Smith", "is_new": True, "proposed_id": "char-smith",
             "type": "character", "confidence": 0.85, "source_turn": "turn-001"},
        ]
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "The elder and the smith greet you."}

        llm = _make_parallel_llm(discovery_entities=entities, parallel_workers=4)
        catalogs = _fresh_catalogs()
        se.extract_and_merge(turn, catalogs, [], llm, min_confidence=0.6)

        # Parallel path: 1 delay at end of turn
        assert llm.delay.call_count == 1

    def test_parallel_uses_multiple_threads(self, monkeypatch):
        """Parallel path submits work to multiple threads."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()

        entities = [
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
        ]
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "The elder greets you."}

        llm = _make_parallel_llm(discovery_entities=entities, parallel_workers=4)
        catalogs = _fresh_catalogs()
        se.extract_and_merge(turn, catalogs, [], llm, min_confidence=0.6)

        # Should have calls from worker threads (not just the main thread)
        unique_threads = set(llm._call_threads)
        # Discovery runs first in main thread, then parallel tasks in pool threads
        # At minimum we expect the main thread + at least 1 pool thread
        assert len(unique_threads) >= 2, (
            f"Expected multiple threads but got {len(unique_threads)}: {unique_threads}"
        )

    def test_parallel_handles_extraction_error(self, monkeypatch):
        """Parallel path handles LLM errors without crashing."""
        monkeypatch.setattr(se, "load_template", lambda name: f"{name} template")
        se._reset_pc_failure_tracking()
        from llm_client import LLMExtractionError

        entities = [
            {"name": "Elder", "is_new": True, "proposed_id": "char-elder",
             "type": "character", "confidence": 0.9, "source_turn": "turn-001"},
        ]
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "The elder greets you."}

        call_count = {"n": 0}

        def _failing_extract(system_prompt, user_prompt, timeout=None,
                             max_tokens=None, schema=None, temperature=None):
            call_count["n"] += 1
            prompt_lower = system_prompt.lower()
            if "discover" in prompt_lower:
                return {"entities": entities}
            if "detail" in prompt_lower:
                raise LLMExtractionError("test error")
            if "relationship" in prompt_lower:
                return {"relationships": []}
            if "event" in prompt_lower:
                return {"events": []}
            return {}

        llm = MagicMock()
        llm.default_timeout = 10
        llm.pc_max_tokens = 4096
        llm.parallel_workers = 4
        llm.delay = MagicMock()
        llm.config = {"checkpoint_interval": 100}
        llm.extract_json = MagicMock(side_effect=_failing_extract)

        catalogs = _fresh_catalogs()
        _, _, turn_failed, log = se.extract_and_merge(
            turn, catalogs, [], llm, min_confidence=0.6,
        )

        # Entity detail failed but extraction should still complete
        assert log["detail_ok"] is False
        assert log["detail_error"] == "test error"
        # Events should still have succeeded
        assert log["events_ok"] is True
