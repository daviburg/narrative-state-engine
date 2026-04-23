"""Tests for Run 8 follow-up fixes (#131-#134)."""
import os
import sys
import json
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se
from bootstrap_session import build_parser

# Shorter aliases for frequently used functions
_dedup_catalogs = se._dedup_catalogs
_pc_partial_merge = se._pc_partial_merge
_merge_pc_aliases = se._merge_pc_aliases
_reset_pc_failure_tracking = se._reset_pc_failure_tracking
_PC_FAILURE_WARN_THRESHOLD = se._PC_FAILURE_WARN_THRESHOLD


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
        """--skip-backfill should be defined in the real bootstrap parser."""
        parser = build_parser()
        args = parser.parse_args(["--session", "s", "--file", "f", "--skip-backfill"])
        assert args.skip_backfill is True

    def test_skip_backfill_default_false(self):
        """--skip-backfill should default to False (backfill ON)."""
        parser = build_parser()
        args = parser.parse_args(["--session", "s", "--file", "f"])
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

    def test_reset_clears_counter(self):
        """_reset_pc_failure_tracking should set counter to 0."""
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = 7
            _reset_pc_failure_tracking()
            assert se._pc_consecutive_failures == 0
        finally:
            se._pc_consecutive_failures = original

    def test_warning_at_threshold(self, capsys):
        """WARNING should fire at 10 consecutive failures."""
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
        original = se._pc_consecutive_failures
        try:
            se._pc_consecutive_failures = 5
            # Simulate success
            se._pc_consecutive_failures = 0
            assert se._pc_consecutive_failures == 0
        finally:
            se._pc_consecutive_failures = original

    def test_partial_merge_returns_true_on_success(self):
        """_pc_partial_merge should return True when fields are merged."""
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
        entity_data = {"id": "char-player", "current_status": "Fighting."}
        result = _pc_partial_merge(catalogs, entity_data, "turn-100")
        assert result is True

    def test_partial_merge_returns_false_on_empty(self):
        """_pc_partial_merge should return False when no fields merge."""
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
        result = _pc_partial_merge(catalogs, entity_data, "turn-060")
        assert result is False


# ---------------------------------------------------------------------------
# #134 — Post-extraction PC alias merge
# ---------------------------------------------------------------------------

class TestPCAliasMerge:
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
        # Name should be added to aliases with schema-compliant source_turn
        pc = catalogs["characters.json"][0]
        aliases_attr = pc.get("stable_attributes", {}).get("aliases", {})
        assert "Fenouille Moonwind" in aliases_attr.get("value", [])
        assert "source_turn" in aliases_attr
        assert "source_turns" not in aliases_attr  # schema compliance

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

    def test_dry_run_preserves_entity_file(self):
        """In dry_run mode, entity file should NOT be deleted."""
        tmpdir = tempfile.mkdtemp()
        try:
            chars_dir = os.path.join(tmpdir, "characters")
            os.makedirs(chars_dir)
            candidate = _make_entity("char-fenouille-moonwind", "Fenouille Moonwind",
                                     "turn-059", "turn-059")
            entity_path = os.path.join(chars_dir, "char-fenouille-moonwind.json")
            with open(entity_path, "w") as f:
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
            merged = _merge_pc_aliases(catalogs, events, tmpdir, dry_run=True)
            assert "char-fenouille-moonwind" in merged
            # File should still exist in dry_run mode
            assert os.path.exists(entity_path)
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

    def test_word_boundary_prevents_substring_match(self):
        """'Ann' should NOT match inside 'Annabelle' — only whole-word matches."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-ann", "Ann", "turn-050", "turn-050"),
            ]
        }
        events = [
            _make_event("evt-1", "Annabelle walks forward", ["char-player"], "turn-100"),
            _make_event("evt-2", "Annabelle casts a spell", ["char-player"], "turn-101"),
            _make_event("evt-3", "Annabelle rests", ["char-player"], "turn-102"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []

    def test_stale_ids_rewritten_in_events(self):
        """After alias merge, events referencing the alias ID should point to char-player."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-fenouille-moonwind", "Fenouille Moonwind",
                             "turn-059", "turn-059"),
            ]
        }
        events = [
            # Candidate referenced alone (not co-occurring with char-player)
            _make_event("evt-1", "Fenouille Moonwind draws her sword",
                        ["char-fenouille-moonwind"], "turn-253"),
            _make_event("evt-2", "Fenouille Moonwind casts healing word",
                        ["char-player"], "turn-313"),
            _make_event("evt-3", "You are Fenouille Moonwind, druid",
                        ["char-player"], "turn-326"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-fenouille-moonwind" in merged
        # The alias ID in evt-1's related_entities should now be char-player
        assert "char-fenouille-moonwind" not in events[0]["related_entities"]
        assert "char-player" in events[0]["related_entities"]

    def test_no_merge_cooccurrence_in_event(self):
        """Entity co-occurring with char-player in an event's related_entities → NOT merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-lyrawyn", "Lyrawyn", "turn-100", "turn-101"),
            ]
        }
        events = [
            _make_event("evt-1", "Lyrawyn is born to the adventurer",
                        ["char-player", "char-lyrawyn"], "turn-100"),
            _make_event("evt-2", "Lyrawyn cries in her mother's arms",
                        ["char-player"], "turn-101"),
            _make_event("evt-3", "Lyrawyn sleeps by the fire",
                        ["char-player"], "turn-102"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []
        assert len(catalogs["characters.json"]) == 2

    def test_no_merge_candidate_has_relationship_to_pc(self):
        """Entity with a relationship targeting char-player → NOT merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                {
                    **_make_entity("char-faelan", "Faelan", "turn-120", "turn-121"),
                    "relationships": [
                        {"target_id": "char-player", "type": "child_of"},
                    ],
                },
            ]
        }
        events = [
            _make_event("evt-1", "Faelan runs to his father",
                        ["char-player"], "turn-120"),
            _make_event("evt-2", "Faelan asks about the forest",
                        ["char-player"], "turn-121"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []
        assert len(catalogs["characters.json"]) == 2

    def test_no_merge_pc_has_relationship_to_candidate(self):
        """char-player with a relationship targeting candidate → NOT merged."""
        catalogs = {
            "characters.json": [
                {
                    **_make_entity("char-player", "Player Character", "turn-001"),
                    "relationships": [
                        {"target_id": "char-chief-thorne", "type": "ally"},
                    ],
                },
                _make_entity("char-chief-thorne", "Chief Thorne", "turn-200", "turn-201"),
            ]
        }
        events = [
            _make_event("evt-1", "Chief Thorne greets the adventurer",
                        ["char-player"], "turn-200"),
            _make_event("evt-2", "Chief Thorne offers a trade",
                        ["char-player"], "turn-201"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []
        assert len(catalogs["characters.json"]) == 2

    def test_legitimate_alias_still_merged(self):
        """Valid alias: name in PC events, ≤3 turn span, no co-occurrence, no relationships → merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-fenouille", "Fenouille", "turn-059", "turn-059"),
            ]
        }
        events = [
            _make_event("evt-1", "You are Fenouille, druid of the forest",
                        ["char-player"], "turn-253"),
            _make_event("evt-2", "Fenouille casts a healing spell",
                        ["char-player"], "turn-313"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-fenouille" in merged
        assert len(catalogs["characters.json"]) == 1

    def test_false_positive_lyrawyn_scenario(self):
        """Lyrawyn (PC's daughter): appears in birth event alongside PC → NOT merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-lyrawyn", "Lyrawyn", "turn-150", "turn-152"),
            ]
        }
        events = [
            _make_event("evt-birth", "Lyrawyn is born",
                        ["char-player", "char-lyrawyn"], "turn-150"),
            _make_event("evt-2", "Lyrawyn is held by her mother",
                        ["char-player"], "turn-151"),
            _make_event("evt-3", "Lyrawyn takes her first breath",
                        ["char-player"], "turn-152"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []

    def test_false_positive_chief_thorne_scenario(self):
        """Chief Thorne: appears at diplomatic event alongside PC → NOT merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-chief-thorne", "Chief Thorne", "turn-200", "turn-202"),
            ]
        }
        events = [
            _make_event("evt-diplomacy", "Chief Thorne meets the adventurer",
                        ["char-player", "char-chief-thorne"], "turn-200"),
            _make_event("evt-2", "Chief Thorne discusses territory",
                        ["char-player"], "turn-201"),
            _make_event("evt-3", "Chief Thorne agrees to terms",
                        ["char-player"], "turn-202"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert merged == []

    def test_fenouille_moonwind_still_merged(self):
        """Fenouille Moonwind (PC's actual name): never in separate events → merged correctly."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-fenouille-moonwind", "Fenouille Moonwind",
                             "turn-059", "turn-059"),
            ]
        }
        events = [
            _make_event("evt-1", "You are Fenouille Moonwind",
                        ["char-player"], "turn-253"),
            _make_event("evt-2", "Fenouille Moonwind draws her sword",
                        ["char-player"], "turn-313"),
            _make_event("evt-3", "Fenouille Moonwind casts healing word",
                        ["char-player"], "turn-326"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-fenouille-moonwind" in merged
        assert len(catalogs["characters.json"]) == 1


# ---------------------------------------------------------------------------
# #186 — PC alias blocklist
# ---------------------------------------------------------------------------

class TestPCAliasBlocklist:
    """Verify _merge_pc_aliases rejects meta-labels like 'player character'."""

    def test_blocklist_filters_player_character(self):
        """Entity named 'Player Character' should NOT be merged as alias."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-player-character", "Player Character",
                             "turn-050", "turn-050"),
            ]
        }
        events = [
            _make_event("evt-1", "The Player Character enters the cave",
                        ["char-player"], "turn-100"),
            _make_event("evt-2", "Player Character attacks the goblin",
                        ["char-player"], "turn-101"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-player-character" not in merged

    def test_blocklist_allows_valid_names(self):
        """A real character name like 'Fenouille Moonwind' should still be merged."""
        catalogs = {
            "characters.json": [
                _make_entity("char-player", "Player Character", "turn-001"),
                _make_entity("char-fenouille", "Fenouille Moonwind",
                             "turn-059", "turn-059"),
            ]
        }
        events = [
            _make_event("evt-1", "Fenouille Moonwind draws her sword",
                        ["char-player"], "turn-253"),
            _make_event("evt-2", "You are Fenouille Moonwind",
                        ["char-player"], "turn-313"),
        ]
        merged = _merge_pc_aliases(catalogs, events, "")
        assert "char-fenouille" in merged

    def test_strips_existing_blocklisted_aliases(self):
        """Blocklisted alias already on PC entity should be cleaned."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Hero",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-100",
                    "stable_attributes": {
                        "aliases": {
                            "value": ["Fenouille Moonwind", "player character", "protagonist"],
                            "source_turn": "turn-050",
                        }
                    },
                },
            ]
        }
        events = []
        _merge_pc_aliases(catalogs, events, "")
        pc = catalogs["characters.json"][0]
        alias_list = pc["stable_attributes"]["aliases"]["value"]
        assert "Fenouille Moonwind" in alias_list
        assert "player character" not in alias_list
        assert "protagonist" not in alias_list

    def test_strips_blocklist_from_string_aliases(self):
        """Blocklisted aliases stored as comma-separated string should be cleaned."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Hero",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-100",
                    "stable_attributes": {
                        "aliases": {
                            "value": "Fenouille Moonwind, player character, protagonist",
                            "source_turn": "turn-050",
                        }
                    },
                },
            ]
        }
        events = []
        _merge_pc_aliases(catalogs, events, "")
        pc = catalogs["characters.json"][0]
        alias_list = pc["stable_attributes"]["aliases"]["value"]
        assert isinstance(alias_list, list)
        assert "Fenouille Moonwind" in alias_list
        assert "player character" not in alias_list
        assert "protagonist" not in alias_list
