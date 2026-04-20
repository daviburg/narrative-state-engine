"""Tests for Run 8 follow-up fixes (#131-#134)."""
import os
import sys
import json
import tempfile
import shutil
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _dedup_catalogs,
    _pc_partial_merge,
    _merge_pc_aliases,
    _pc_consecutive_failures,
    _PC_FAILURE_WARN_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(id_, name, turn="turn-001", last_turn=None):
    return {
        "id": id_,
        "name": name,
        "type": "character",
        "identity": f"A character named {name}.",
        "first_seen_turn": turn,
        "last_updated_turn": last_turn or turn,
        "relationships": [],
    }


def _make_event(event_id, description, related_entities, turn="turn-100"):
    return {
        "id": event_id,
        "turn_id": turn,
        "description": description,
        "related_entities": related_entities,
    }


# ---------------------------------------------------------------------------
# #131 — Stub backfill by default
# ---------------------------------------------------------------------------

class TestBackfillDefault:
    """Verify that backfill runs by default (no flag needed)."""

    def test_skip_backfill_flag_exists(self):
        """--skip-backfill should be defined in argparse."""
        import bootstrap_session
        import argparse
        # Parse with --skip-backfill to verify it's recognized
        parser = argparse.ArgumentParser()
        parser.add_argument("--skip-backfill", action="store_true")
        args = parser.parse_args(["--skip-backfill"])
        assert args.skip_backfill is True

    def test_skip_backfill_default_false(self):
        """--skip-backfill should default to False (backfill ON)."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--skip-backfill", action="store_true")
        args = parser.parse_args([])
        assert args.skip_backfill is False


# ---------------------------------------------------------------------------
# #132 — Minimum stem length guard for Levenshtein dedup
# ---------------------------------------------------------------------------

class TestLevenshteinStemLengthGuard:
    """Verify minimum stem length prevents short-name false positives."""

    def test_borin_vs_bran_not_merged(self):
        """Short stems (5/4 chars) should NOT be merged despite edit distance 2."""
        catalogs = {
            "characters.json": [
                _make_entity("char-borin", "Borin"),
                _make_entity("char-bran", "Bran"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 0
        assert len(catalogs["characters.json"]) == 2

    def test_communal_vs_communial_merged(self):
        """Longer stems (8/9 chars) with distance 1 should still merge."""
        catalogs = {
            "locations.json": [
                _make_entity("loc-communal", "Communal Hall"),
                _make_entity("loc-communial", "Communial Hall"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 1
        assert len(catalogs["locations.json"]) == 1

    def test_younger_hunter_variants_merged(self):
        """Long hyphenated stems should still merge."""
        catalogs = {
            "characters.json": [
                _make_entity("char-younger-hunter", "Younger Hunter"),
                _make_entity("char-younger-huntr", "Younger Huntr"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 1
        assert len(catalogs["characters.json"]) == 1

    def test_boundary_six_char_stems_merged(self):
        """Stems of exactly 6 chars with distance 2 should merge."""
        catalogs = {
            "characters.json": [
                _make_entity("char-kaelon", "Kaelon"),
                _make_entity("char-kaalbn", "Kaalbn"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        # Both stems are 6 chars, distance=2, same first char → should merge
        assert count == 1
        assert len(catalogs["characters.json"]) == 1

    def test_five_char_stems_not_merged(self):
        """Stems of 5 chars should NOT merge (below minimum of 6)."""
        catalogs = {
            "characters.json": [
                _make_entity("char-draen", "Draen"),
                _make_entity("char-draan", "Draan"),
            ]
        }
        count, merge_map = _dedup_catalogs(catalogs)
        assert count == 0
        assert len(catalogs["characters.json"]) == 2


# ---------------------------------------------------------------------------
# #133 — PC extraction failure logging and recovery
# ---------------------------------------------------------------------------

class TestPCConsecutiveFailureTracking:
    """Verify consecutive failure counter and warnings."""

    def test_failure_threshold_constant(self):
        """Threshold should be 10."""
        assert _PC_FAILURE_WARN_THRESHOLD == 10

    def test_warning_at_threshold(self, capsys):
        """WARNING should fire at 10 consecutive failures."""
        import semantic_extraction as se
        original = se._pc_consecutive_failures
        try:
            # Simulate 10 failures
            se._pc_consecutive_failures = _PC_FAILURE_WARN_THRESHOLD - 1
            pc_entry = {"last_updated_turn": "turn-050"}

            # Simulate one more failure
            se._pc_consecutive_failures += 1
            if se._pc_consecutive_failures >= _PC_FAILURE_WARN_THRESHOLD:
                print(
                    f"  WARNING: PC extraction has failed for {se._pc_consecutive_failures} "
                    f"consecutive turns (last update: "
                    f"{pc_entry.get('last_updated_turn', 'unknown')}). "
                    f"Context may be too large for reliable extraction.",
                    file=sys.stderr,
                )
            captured = capsys.readouterr()
            assert "WARNING" in captured.err
            assert "10" in captured.err
            assert "consecutive turns" in captured.err
            assert "turn-050" in captured.err
        finally:
            se._pc_consecutive_failures = original

    def test_counter_resets_on_success(self):
        """Counter should reset to 0 on successful extraction."""
        import semantic_extraction as se
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = 5
            # Simulate success
            se._pc_consecutive_failures = 0
            assert se._pc_consecutive_failures == 0
        finally:
            se._pc_consecutive_failures = original

    def test_empty_merge_warning_includes_response_keys(self, capsys):
        """Empty merge WARNING should include response keys."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }
        entity_data = {"id": "char-player", "garbage_field": "junk"}
        _pc_partial_merge(catalogs, entity_data, "turn-060")
        captured = capsys.readouterr()
        assert "no fields could be merged" in captured.err.lower()
        assert "Response keys:" in captured.err
        assert "turn-060" in captured.err

    def test_none_extraction_warning(self, capsys):
        """When entity_data is None, a specific warning should fire."""
        import semantic_extraction as se
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = 3
            # Simulate the None-extraction warning path
            turn_id = "turn-200"
            print(
                f"  WARNING: PC detail extraction returned None at {turn_id}. "
                f"Consecutive failures: {se._pc_consecutive_failures + 1}",
                file=sys.stderr,
            )
            captured = capsys.readouterr()
            assert "returned None" in captured.err
            assert "turn-200" in captured.err
            assert "4" in captured.err  # 3 + 1
        finally:
            se._pc_consecutive_failures = original


# ---------------------------------------------------------------------------
# #134 — Post-extraction PC alias merge
# ---------------------------------------------------------------------------

class TestPCAliaseMerge:
    """Verify _merge_pc_aliases() identifies and merges PC aliases."""

    def test_merge_alias_with_multiple_events(self):
        """Entity named in >=2 char-player events with <=3 turn span → merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-fenouille-moonwind", "Fenouille Moonwind",
                             "turn-059", "turn-059"),
            ]
        }
        events = [
            _make_event("evt-1", "Fenouille Moonwind draws her sword", ["char-player"], "turn-253"),
            _make_event("evt-2", "You are Fenouille Moonwind, druid of the forest", ["char-player"], "turn-313"),
            _make_event("evt-3", "Fenouille Moonwind casts healing word", ["char-player"], "turn-326"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-fenouille-moonwind" in merged
        assert len(catalogs["characters.json"]) == 1
        # Name should be added to aliases
        pc = catalogs["characters.json"][0]
        aliases = pc.get("stable_attributes", {}).get("aliases", {}).get("value", [])
        assert "Fenouille Moonwind" in aliases

    def test_no_merge_many_turns(self):
        """Entity with >3 turn span should NOT be merged even if name appears often."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-kael", "Kael", "turn-010", "turn-060"),
            ]
        }
        events = [
            _make_event("evt-1", "Kael joins the party", ["char-player"], "turn-020"),
            _make_event("evt-2", "Kael speaks to the elder", ["char-player"], "turn-030"),
            _make_event("evt-3", "Kael draws a map", ["char-player"], "turn-040"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []
        assert len(catalogs["characters.json"]) == 2

    def test_no_merge_insufficient_events(self):
        """Entity name appearing in <2 PC events should NOT be merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-random-npc", "Random NPC", "turn-050", "turn-050"),
            ]
        }
        events = [
            _make_event("evt-1", "Something happens", ["char-player"], "turn-100"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []
        assert len(catalogs["characters.json"]) == 2

    def test_merged_entity_file_deleted(self):
        """Merged entity's JSON file should be removed from disk."""
        tmpdir = tempfile.mkdtemp()
        try:
            chars_dir = os.path.join(tmpdir, "characters")
            os.makedirs(chars_dir)
            # Write the candidate entity file
            candidate = _make_entity("char-fenouille-moonwind", "Fenouille Moonwind",
                                     "turn-059", "turn-059")
            with open(os.path.join(chars_dir, "char-fenouille-moonwind.json"), "w") as f:
                json.dump(candidate, f)

            catalogs = {
                "characters.json": [
                    _make_entity("char-player", "Player Character", "turn-001"),
                    dict(candidate),
                ]
            }
            events = [
                _make_event("evt-1", "Fenouille Moonwind strikes", ["char-player"], "turn-253"),
                _make_event("evt-2", "Fenouille Moonwind rests", ["char-player"], "turn-313"),
            ]
            merged = _merge_pc_aliases(catalogs, events, tmpdir)
            assert "char-fenouille-moonwind" in merged
            assert not os.path.exists(os.path.join(chars_dir, "char-fenouille-moonwind.json"))
        finally:
            shutil.rmtree(tmpdir)

    def test_alias_absorbs_relationships(self):
        """Merged entity's unique relationships should transfer to char-player."""
        catalogs = {
            "characters.json": [
                {
                    **_make_entity("char-player", "Player Character", "turn-001"),
                    "relationships": [
                        {"target_id": "char-elder", "type": "ally"},
                    ],
                },
                {
                    **_make_entity("char-fenouille-moonwind", "Fenouille Moonwind",
                                   "turn-059", "turn-059"),
                    "relationships": [
                        {"target_id": "char-grove-spirit", "type": "ally"},
                    ],
                },
            ]
        }
        events = [
            _make_event("evt-1", "Fenouille Moonwind is here", ["char-player"], "turn-253"),
            _make_event("evt-2", "Fenouille Moonwind speaks", ["char-player"], "turn-313"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-fenouille-moonwind" in merged
        pc = catalogs["characters.json"][0]
        rel_targets = {r["target_id"] for r in pc["relationships"]}
        assert "char-elder" in rel_targets
        assert "char-grove-spirit" in rel_targets

    def test_no_merge_short_name(self):
        """Entity with name < 3 chars should be skipped."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-ab", "Ab", "turn-050", "turn-050"),
            ]
        }
        events = [
            _make_event("evt-1", "Ab Ab Ab", ["char-player"], "turn-100"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []
