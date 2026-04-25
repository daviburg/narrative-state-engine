"""Test that entity catalogs are persisted at checkpoint intervals (#220)."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import CATALOG_KEYS, save_catalogs, load_catalogs


def _empty_catalogs():
    return {fn: [] for fn in CATALOG_KEYS}


def _make_entity(eid, name, etype="character"):
    return {
        "id": eid,
        "name": name,
        "type": etype,
        "identity": f"A test {etype}",
        "first_seen_turn": "turn-001-dm",
        "last_updated_turn": "turn-001-dm",
        "stable_attributes": {},
        "relationships": [],
    }


def _make_turn(n):
    return {"turn_id": f"turn-{n:03d}-dm", "speaker": "DM", "text": f"Turn {n} text."}


def _make_turns(start, count):
    return [_make_turn(i) for i in range(start, start + count)]


def _setup_session(tmp_path):
    """Create minimal session directory layout."""
    session_dir = tmp_path / "sessions" / "session-test"
    derived_dir = session_dir / "derived"
    derived_dir.mkdir(parents=True)
    framework_dir = tmp_path / "framework"
    catalog_dir = framework_dir / "catalogs"
    catalog_dir.mkdir(parents=True)
    return str(session_dir), str(framework_dir), str(catalog_dir)


# ---------------------------------------------------------------------------
# Segmented path: catalogs persisted after each completed segment
# ---------------------------------------------------------------------------

class TestSegmentedCheckpointPersistence:
    """Verify segmented extraction persists catalogs after each segment."""

    def test_catalogs_saved_after_each_segment(self, tmp_path):
        """After completing a segment, reconciled catalogs must exist on disk."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        turn_count = 0
        entity_counter = [0]

        def mock_extract_and_merge(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            nonlocal turn_count
            turn_count += 1
            entity_counter[0] += 1
            eid = f"char-entity-{entity_counter[0]}"
            catalogs["characters.json"].append(
                _make_entity(eid, f"Entity {entity_counter[0]}")
            )
            return catalogs, events, False

        turns = _make_turns(1, 6)  # 6 turns, segment_size=3 → 2 segments

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract_and_merge), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 25})

            from semantic_extraction import extract_semantic_batch
            extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused", segment_size=3,
            )

        # Catalogs must exist on disk
        cats = load_catalogs(catalog_dir)
        total_entities = sum(len(v) for v in cats.values())
        assert total_entities > 0, "No entities found on disk after segmented extraction"

    def test_catalogs_on_disk_after_first_segment_before_second(self, tmp_path):
        """Catalogs must be on disk after segment 1, BEFORE segment 2 finishes."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        segment_calls = [0]
        catalog_snapshots = []

        def mock_extract_and_merge(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            catalogs["characters.json"].append(
                _make_entity(f"char-seg-ent-{len(catalogs['characters.json']) + 1}",
                             f"Ent {len(catalogs['characters.json']) + 1}")
            )
            return catalogs, events, False

        def spying_ensure_pc(catalogs, turn_id):
            """Called at the start of each segment — snapshot disk state."""
            segment_calls[0] += 1
            if segment_calls[0] > 1:
                # This is the start of segment 2+; check disk from segment 1
                cats = load_catalogs(catalog_dir)
                catalog_snapshots.append(sum(len(v) for v in cats.values()))

        turns = _make_turns(1, 6)  # 2 segments of 3

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract_and_merge), \
             patch("semantic_extraction._ensure_player_character", side_effect=spying_ensure_pc), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 25})

            from semantic_extraction import extract_semantic_batch
            extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused", segment_size=3,
            )

        # Segment 2 must have seen entities from segment 1 on disk
        assert len(catalog_snapshots) >= 1, "Spy never saw segment 2 start"
        assert catalog_snapshots[0] > 0, (
            f"No entities on disk when segment 2 started; got {catalog_snapshots[0]}"
        )


# ---------------------------------------------------------------------------
# Segmented path: intra-segment checkpoint persists catalogs
# ---------------------------------------------------------------------------

class TestIntraSegmentCheckpoint:
    """Verify that intra-segment checkpoints also save catalogs."""

    def test_intra_segment_checkpoint_saves_catalogs(self, tmp_path):
        """With checkpoint_interval=2 and segment_size=5, catalogs must
        be written at turn 2 within the segment."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        checkpoint_disk_snapshots = []
        call_count = [0]

        def mock_extract_and_merge(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            call_count[0] += 1
            catalogs["characters.json"].append(
                _make_entity(f"char-intra-{call_count[0]}",
                             f"IntraEnt {call_count[0]}")
            )
            return catalogs, events, False

        turns = _make_turns(1, 5)  # 1 segment of 5 turns

        # Use a custom save_catalogs that also records snapshots
        real_save_catalogs = save_catalogs

        def tracking_save_catalogs(cdir, cats, **kw):
            real_save_catalogs(cdir, cats, **kw)
            total = sum(len(v) for v in cats.values())
            checkpoint_disk_snapshots.append(total)

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract_and_merge), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction.save_catalogs", side_effect=tracking_save_catalogs), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 2})

            from semantic_extraction import extract_semantic_batch
            extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused", segment_size=5,
            )

        # checkpoint_interval=2 with 5 turns → checkpoints at turn 2 and 4
        # plus final save → at least 3 save_catalogs calls
        assert len(checkpoint_disk_snapshots) >= 3, (
            f"Expected at least 3 save_catalogs calls, got {len(checkpoint_disk_snapshots)}"
        )
        # The first checkpoint (after 2 turns) should have entities
        assert checkpoint_disk_snapshots[0] > 0, "First checkpoint had 0 entities"


# ---------------------------------------------------------------------------
# Non-segmented path: error handling persists catalogs
# ---------------------------------------------------------------------------

class TestErrorPathPersistence:
    """Verify that catalogs are saved even on extraction errors."""

    def test_error_during_extraction_saves_catalogs(self, tmp_path):
        """If extract_and_merge raises, catalogs accumulated so far must be saved."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        call_count = [0]

        def mock_extract_and_merge(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            call_count[0] += 1
            if call_count[0] == 3:
                raise RuntimeError("Simulated extraction failure")
            catalogs["characters.json"].append(
                _make_entity(f"char-err-{call_count[0]}",
                             f"ErrEnt {call_count[0]}")
            )
            return catalogs, events, False

        turns = _make_turns(1, 5)

        save_calls = []
        real_save_catalogs = save_catalogs

        def tracking_save(cdir, cats, **kw):
            real_save_catalogs(cdir, cats, **kw)
            save_calls.append(sum(len(v) for v in cats.values()))

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract_and_merge), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction.save_catalogs", side_effect=tracking_save), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0), \
             patch("semantic_extraction.load_catalogs", return_value=_empty_catalogs()), \
             patch("semantic_extraction.load_events", return_value=[]):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 25})

            from semantic_extraction import extract_semantic_batch
            extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused",
            )

        # The error handler must have called save_catalogs with the 2 entities
        # accumulated before the error (turns 1 and 2 succeeded, turn 3 failed)
        error_save = save_calls[0]  # first save is from the error handler
        assert error_save == 2, f"Expected 2 entities at error save, got {error_save}"


# ---------------------------------------------------------------------------
# Non-segmented path: resume loads persisted entities
# ---------------------------------------------------------------------------

class TestResumeLoadsPersisted:
    """Verify that resuming from a checkpoint loads entities from disk."""

    def test_resume_loads_entities_from_prior_checkpoint(self, tmp_path):
        """Entities written at a checkpoint must be loaded when resuming."""
        session_dir, framework_dir, catalog_dir = _setup_session(tmp_path)

        # Pre-populate catalog dir with 2 entities (as if from a prior checkpoint)
        pre_existing = _empty_catalogs()
        pre_existing["characters.json"] = [
            _make_entity("char-alpha", "Alpha"),
            _make_entity("char-beta", "Beta"),
        ]
        save_catalogs(catalog_dir, pre_existing)

        # Write a progress file indicating turn-002-dm was the last completed
        progress_file = os.path.join(session_dir, "derived", "extraction-progress.json")
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump({
                "last_completed_turn": "turn-002-dm",
                "total_turns": 5,
                "entities_discovered": 2,
                "completed": False,
            }, f)

        loaded_catalogs = [None]

        def mock_extract_and_merge(turn, catalogs, events, llm, min_conf, catalog_dir=None):
            # On the first call (turn 3), record what catalogs were loaded
            if loaded_catalogs[0] is None:
                loaded_catalogs[0] = {k: list(v) for k, v in catalogs.items()}
            return catalogs, events, False

        turns = _make_turns(1, 5)

        with patch("semantic_extraction.LLMClient") as mock_llm_cls, \
             patch("semantic_extraction.extract_and_merge", side_effect=mock_extract_and_merge), \
             patch("semantic_extraction._ensure_player_character"), \
             patch("semantic_extraction.find_stale_entities", return_value=[]), \
             patch("semantic_extraction.save_catalogs"), \
             patch("semantic_extraction.save_events"), \
             patch("semantic_extraction.cleanup_dangling_relationships", return_value={}), \
             patch("semantic_extraction._post_batch_orphan_sweep", return_value=0), \
             patch("semantic_extraction._name_mention_discovery", return_value=0):
            mock_llm_cls.return_value = MagicMock(config={"checkpoint_interval": 25})

            from semantic_extraction import extract_semantic_batch
            extract_semantic_batch(
                turns, session_dir, framework_dir=framework_dir,
                config_path="unused",
            )

        # The first extract_and_merge call should have received catalogs
        # loaded from disk — containing our 2 pre-existing entities
        assert loaded_catalogs[0] is not None, "extract_and_merge was never called"
        chars = loaded_catalogs[0].get("characters.json", [])
        ids = {e["id"] for e in chars}
        assert "char-alpha" in ids, "Pre-existing entity char-alpha not loaded on resume"
        assert "char-beta" in ids, "Pre-existing entity char-beta not loaded on resume"
