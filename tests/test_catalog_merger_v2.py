"""Tests for V2 catalog_merger: per-entity I/O, relationship consolidation,
dormancy marking, and index generation."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import (
    _consolidate_relationship,
    _dedup_relationships,
    _generate_index,
    _merge_entity_relationships,
    _parse_turn_number,
    _read_v2_entities,
    _write_v2_entity,
    cleanup_dangling_relationships,
    load_catalogs,
    mark_dormant_relationships,
    merge_entity,
    merge_relationships,
    save_catalogs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_v2_entity(id_, name, etype="character", turn="turn-001", **kwargs):
    """Create a minimal V2-shaped entity dict."""
    entity = {
        "id": id_,
        "name": name,
        "type": etype,
        "identity": f"{name} identity.",
        "first_seen_turn": turn,
        "last_updated_turn": turn,
    }
    entity.update(kwargs)
    return entity


# ---------------------------------------------------------------------------
# V2 per-entity file I/O
# ---------------------------------------------------------------------------

class TestV2ReadPerEntityFiles:
    def test_reads_all_entity_files(self, tmp_path):
        edir = tmp_path / "characters"
        edir.mkdir()
        e1 = _make_v2_entity("char-alpha", "Alpha")
        e2 = _make_v2_entity("char-beta", "Beta")
        (edir / "char-alpha.json").write_text(json.dumps(e1), encoding="utf-8")
        (edir / "char-beta.json").write_text(json.dumps(e2), encoding="utf-8")
        result = _read_v2_entities(str(edir))
        assert len(result) == 2
        ids = {e["id"] for e in result}
        assert ids == {"char-alpha", "char-beta"}

    def test_skips_index_json(self, tmp_path):
        edir = tmp_path / "characters"
        edir.mkdir()
        e1 = _make_v2_entity("char-alpha", "Alpha")
        (edir / "char-alpha.json").write_text(json.dumps(e1), encoding="utf-8")
        (edir / "index.json").write_text("[]", encoding="utf-8")
        result = _read_v2_entities(str(edir))
        assert len(result) == 1

    def test_returns_empty_for_missing_dir(self, tmp_path):
        result = _read_v2_entities(str(tmp_path / "nonexistent"))
        assert result == []

    def test_handles_bom(self, tmp_path):
        edir = tmp_path / "characters"
        edir.mkdir()
        e1 = _make_v2_entity("char-alpha", "Alpha")
        # Write with BOM
        (edir / "char-alpha.json").write_bytes(
            b"\xef\xbb\xbf" + json.dumps(e1).encode("utf-8")
        )
        result = _read_v2_entities(str(edir))
        assert len(result) == 1
        assert result[0]["id"] == "char-alpha"


class TestV2WritePerEntityFiles:
    def test_writes_entity_file(self, tmp_path):
        edir = tmp_path / "characters"
        e1 = _make_v2_entity("char-alpha", "Alpha")
        _write_v2_entity(str(edir), e1)
        fpath = edir / "char-alpha.json"
        assert fpath.exists()
        data = json.loads(fpath.read_text(encoding="utf-8"))
        assert data["id"] == "char-alpha"
        assert data["name"] == "Alpha"

    def test_creates_directory_if_missing(self, tmp_path):
        edir = tmp_path / "new_type"
        e1 = _make_v2_entity("char-alpha", "Alpha")
        _write_v2_entity(str(edir), e1)
        assert (edir / "char-alpha.json").exists()

    def test_pretty_prints_with_two_space_indent(self, tmp_path):
        edir = tmp_path / "characters"
        e1 = _make_v2_entity("char-alpha", "Alpha")
        _write_v2_entity(str(edir), e1)
        text = (edir / "char-alpha.json").read_text(encoding="utf-8")
        # Check 2-space indent (not 4)
        assert '  "id"' in text
        assert '    "id"' not in text


# ---------------------------------------------------------------------------
# Full load/save round-trip (V2)
# ---------------------------------------------------------------------------

class TestV2LoadSaveRoundTrip:
    def test_round_trip(self, tmp_path):
        # Set up V2 directory structure
        cdir = tmp_path / "characters"
        cdir.mkdir()
        e1 = _make_v2_entity("char-alpha", "Alpha")
        (cdir / "char-alpha.json").write_text(json.dumps(e1), encoding="utf-8")
        # Also create other type dirs (empty)
        for d in ["locations", "factions", "items"]:
            (tmp_path / d).mkdir()

        catalogs = load_catalogs(str(tmp_path))
        assert len(catalogs["characters.json"]) == 1

        # Add a new entity and save
        catalogs["characters.json"].append(
            _make_v2_entity("char-beta", "Beta")
        )
        save_catalogs(str(tmp_path), catalogs)

        # Verify individual files exist
        assert (cdir / "char-alpha.json").exists()
        assert (cdir / "char-beta.json").exists()
        assert (cdir / "index.json").exists()

        # Reload and verify
        catalogs2 = load_catalogs(str(tmp_path))
        assert len(catalogs2["characters.json"]) == 2

    def test_stale_file_removed_on_save(self, tmp_path):
        """After dedup removes an entity, its file should be deleted on save."""
        cdir = tmp_path / "characters"
        cdir.mkdir()
        for d in ["locations", "factions", "items"]:
            (tmp_path / d).mkdir()

        e1 = _make_v2_entity("char-alpha", "Alpha")
        e2 = _make_v2_entity("char-beta", "Beta")
        (cdir / "char-alpha.json").write_text(json.dumps(e1), encoding="utf-8")
        (cdir / "char-beta.json").write_text(json.dumps(e2), encoding="utf-8")

        # Load, then remove one entity (simulating dedup)
        catalogs = load_catalogs(str(tmp_path))
        catalogs["characters.json"] = [
            e for e in catalogs["characters.json"] if e["id"] != "char-beta"
        ]
        save_catalogs(str(tmp_path), catalogs)

        assert (cdir / "char-alpha.json").exists()
        assert not (cdir / "char-beta.json").exists()  # stale file removed
        # Reload confirms only one entity
        catalogs2 = load_catalogs(str(tmp_path))
        assert len(catalogs2["characters.json"]) == 1


# ---------------------------------------------------------------------------
# Relationship consolidation
# ---------------------------------------------------------------------------

class TestRelationshipConsolidation:
    def test_existing_pair_updates_current_and_adds_history(self):
        existing = {
            "target_id": "char-beta",
            "current_relationship": "ally",
            "type": "partnership",
            "status": "active",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-010",
        }
        update = {
            "current_relationship": "close friend and ally",
            "type": "partnership",
            "last_updated_turn": "turn-020",
        }
        _consolidate_relationship(existing, update)
        assert existing["current_relationship"] == "close friend and ally"
        assert existing["last_updated_turn"] == "turn-020"
        assert len(existing["history"]) == 1
        assert existing["history"][0]["turn"] == "turn-010"
        assert existing["history"][0]["description"] == "ally"

    def test_same_description_does_not_add_history(self):
        existing = {
            "target_id": "char-beta",
            "current_relationship": "ally",
            "type": "partnership",
            "status": "active",
            "first_seen_turn": "turn-010",
            "last_updated_turn": "turn-010",
        }
        update = {
            "current_relationship": "ally",
            "type": "partnership",
            "last_updated_turn": "turn-020",
        }
        _consolidate_relationship(existing, update)
        assert existing["current_relationship"] == "ally"
        assert existing["last_updated_turn"] == "turn-020"
        assert "history" not in existing

    def test_history_accumulates(self):
        existing = {
            "target_id": "char-beta",
            "current_relationship": "acquaintance",
            "type": "social",
            "status": "active",
            "first_seen_turn": "turn-005",
            "last_updated_turn": "turn-005",
            "history": [
                {"turn": "turn-003", "description": "stranger"},
            ],
        }
        update = {
            "current_relationship": "friend",
            "type": "partnership",
            "last_updated_turn": "turn-015",
        }
        _consolidate_relationship(existing, update)
        assert existing["current_relationship"] == "friend"
        assert len(existing["history"]) == 2
        assert existing["history"][1]["description"] == "acquaintance"


class TestRelationshipNewPair:
    def test_new_pair_creates_fresh_entry(self):
        existing_rels = []
        new_rels = [
            {
                "target_id": "char-gamma",
                "current_relationship": "mentor",
                "type": "mentorship",
                "first_seen_turn": "turn-005",
                "last_updated_turn": "turn-005",
            }
        ]
        _merge_entity_relationships(existing_rels, new_rels)
        assert len(existing_rels) == 1
        assert existing_rels[0]["target_id"] == "char-gamma"
        assert existing_rels[0]["status"] == "active"

    def test_relationship_field_becomes_current_relationship(self):
        existing_rels = []
        new_rels = [
            {
                "target_id": "char-gamma",
                "relationship": "mentor",
                "type": "mentorship",
                "first_seen_turn": "turn-005",
            }
        ]
        _merge_entity_relationships(existing_rels, new_rels)
        assert existing_rels[0]["current_relationship"] == "mentor"

    def test_no_duplicate_pairs(self):
        existing_rels = [
            {
                "target_id": "char-beta",
                "current_relationship": "ally",
                "type": "partnership",
                "status": "active",
                "first_seen_turn": "turn-010",
                "last_updated_turn": "turn-010",
            }
        ]
        new_rels = [
            {
                "target_id": "char-beta",
                "current_relationship": "close friend",
                "type": "partnership",
                "last_updated_turn": "turn-020",
            }
        ]
        _merge_entity_relationships(existing_rels, new_rels)
        assert len(existing_rels) == 1  # no duplicate
        assert existing_rels[0]["current_relationship"] == "close friend"


class TestMergeRelationshipsTopLevel:
    def test_consolidates_per_pair(self):
        """merge_relationships() should consolidate by (source, target) pair."""
        catalogs = {
            "characters.json": [
                _make_v2_entity("char-alpha", "Alpha", relationships=[
                    {
                        "target_id": "char-beta",
                        "current_relationship": "acquaintance",
                        "type": "social",
                        "status": "active",
                        "first_seen_turn": "turn-005",
                        "last_updated_turn": "turn-005",
                    }
                ]),
            ]
        }
        new_rels = [
            {
                "source_id": "char-alpha",
                "target_id": "char-beta",
                "relationship": "close friend",
                "type": "partnership",
            }
        ]
        merge_relationships(catalogs, new_rels, "turn-020")
        rels = catalogs["characters.json"][0]["relationships"]
        assert len(rels) == 1
        assert rels[0]["current_relationship"] == "close friend"
        assert len(rels[0].get("history", [])) == 1


# ---------------------------------------------------------------------------
# Dormancy marking
# ---------------------------------------------------------------------------

class TestDormancyMarking:
    def test_marks_dormant_after_threshold(self):
        catalogs = {
            "characters.json": [
                _make_v2_entity("char-alpha", "Alpha", turn="turn-001",
                                last_updated_turn="turn-001",
                                relationships=[
                                    {
                                        "target_id": "char-beta",
                                        "current_relationship": "ally",
                                        "type": "partnership",
                                        "status": "active",
                                        "first_seen_turn": "turn-001",
                                        "last_updated_turn": "turn-001",
                                    }
                                ]),
                _make_v2_entity("char-beta", "Beta", turn="turn-001",
                                last_updated_turn="turn-001"),
            ]
        }
        marked = mark_dormant_relationships(catalogs, "turn-015", dormancy_threshold=10)
        assert marked == 1
        rel = catalogs["characters.json"][0]["relationships"][0]
        assert rel["status"] == "dormant"

    def test_does_not_mark_recently_active(self):
        catalogs = {
            "characters.json": [
                _make_v2_entity("char-alpha", "Alpha", turn="turn-010",
                                last_updated_turn="turn-010",
                                relationships=[
                                    {
                                        "target_id": "char-beta",
                                        "current_relationship": "ally",
                                        "type": "partnership",
                                        "status": "active",
                                        "first_seen_turn": "turn-005",
                                        "last_updated_turn": "turn-010",
                                    }
                                ]),
                _make_v2_entity("char-beta", "Beta", turn="turn-003",
                                last_updated_turn="turn-003"),
            ]
        }
        marked = mark_dormant_relationships(catalogs, "turn-015", dormancy_threshold=10)
        # source (turn-010) is only 5 turns ago → not stale → relationship stays active
        assert marked == 0

    def test_does_not_mark_resolved(self):
        catalogs = {
            "characters.json": [
                _make_v2_entity("char-alpha", "Alpha", turn="turn-001",
                                last_updated_turn="turn-001",
                                relationships=[
                                    {
                                        "target_id": "char-beta",
                                        "current_relationship": "captured by",
                                        "type": "adversarial",
                                        "status": "resolved",
                                        "first_seen_turn": "turn-001",
                                        "last_updated_turn": "turn-001",
                                    }
                                ]),
                _make_v2_entity("char-beta", "Beta", turn="turn-001",
                                last_updated_turn="turn-001"),
            ]
        }
        marked = mark_dormant_relationships(catalogs, "turn-050", dormancy_threshold=10)
        assert marked == 0  # already resolved, not touched

    def test_threshold_configurable(self):
        catalogs = {
            "characters.json": [
                _make_v2_entity("char-alpha", "Alpha", turn="turn-005",
                                last_updated_turn="turn-005",
                                relationships=[
                                    {
                                        "target_id": "char-beta",
                                        "current_relationship": "ally",
                                        "type": "partnership",
                                        "status": "active",
                                        "first_seen_turn": "turn-005",
                                        "last_updated_turn": "turn-005",
                                    }
                                ]),
                _make_v2_entity("char-beta", "Beta", turn="turn-005",
                                last_updated_turn="turn-005"),
            ]
        }
        # With threshold 20, turn-015 is only 10 turns → not dormant
        marked = mark_dormant_relationships(catalogs, "turn-015", dormancy_threshold=20)
        assert marked == 0

        # With threshold 5, turn-015 is 10 turns → dormant
        # Reset status
        catalogs["characters.json"][0]["relationships"][0]["status"] = "active"
        marked = mark_dormant_relationships(catalogs, "turn-015", dormancy_threshold=5)
        assert marked == 1


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------

class TestIndexGeneration:
    def test_index_generated_correctly(self, tmp_path):
        edir = tmp_path / "characters"
        edir.mkdir()
        entities = [
            _make_v2_entity("char-alpha", "Alpha", turn="turn-001",
                            current_status="Active warrior."),
            _make_v2_entity("char-beta", "Beta", turn="turn-003",
                            current_status="Village healer.",
                            relationships=[
                                {"target_id": "char-alpha", "current_relationship": "ally",
                                 "type": "partnership", "status": "active",
                                 "first_seen_turn": "turn-003"},
                                {"target_id": "char-gamma", "current_relationship": "enemy",
                                 "type": "adversarial", "status": "dormant",
                                 "first_seen_turn": "turn-003"},
                            ]),
        ]
        _generate_index(str(edir), entities)
        index_path = edir / "index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(index) == 2

        alpha_idx = next(e for e in index if e["id"] == "char-alpha")
        assert alpha_idx["name"] == "Alpha"
        assert alpha_idx["active_relationship_count"] == 0
        assert alpha_idx["status_summary"] == "Active warrior."

        beta_idx = next(e for e in index if e["id"] == "char-beta")
        assert beta_idx["active_relationship_count"] == 1  # only 'active' status counted


# ---------------------------------------------------------------------------
# Clean-start V2 defaulting (Issue #96)
# ---------------------------------------------------------------------------

class TestSaveCatalogsCleanStart:
    def test_clean_start_uses_v2(self, tmp_path):
        """On a completely clean catalog dir, save_catalogs should default to V2."""
        catalog_dir = str(tmp_path / "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)
        # Write empty flat files (simulating clean extraction start)
        for name in ["characters.json", "locations.json", "factions.json", "items.json"]:
            with open(os.path.join(catalog_dir, name), "w") as f:
                json.dump([], f)

        catalogs = {
            "characters.json": [{"id": "char-test", "name": "Test", "type": "character",
                                 "identity": "A test character", "first_seen_turn": "turn-001"}],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        save_catalogs(catalog_dir, catalogs)

        # Should have created V2 per-entity directory
        assert os.path.isdir(os.path.join(catalog_dir, "characters"))
        assert os.path.isfile(os.path.join(catalog_dir, "characters", "char-test.json"))
        assert os.path.isfile(os.path.join(catalog_dir, "characters", "index.json"))

    def test_empty_dir_uses_v2(self, tmp_path):
        """On a completely empty catalog dir (no files at all), save_catalogs defaults to V2."""
        catalog_dir = str(tmp_path / "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)

        catalogs = {
            "characters.json": [{"id": "char-test", "name": "Test", "type": "character",
                                 "identity": "A test character", "first_seen_turn": "turn-001"}],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        save_catalogs(catalog_dir, catalogs)

        assert os.path.isdir(os.path.join(catalog_dir, "characters"))
        assert os.path.isfile(os.path.join(catalog_dir, "characters", "char-test.json"))

    def test_index_status_summary_truncated(self, tmp_path):
        edir = tmp_path / "characters"
        edir.mkdir()
        long_status = "A" * 200
        entities = [_make_v2_entity("char-alpha", "Alpha", current_status=long_status)]
        _generate_index(str(edir), entities)
        index = json.loads((edir / "index.json").read_text(encoding="utf-8"))
        assert len(index[0]["status_summary"]) == 80

    def test_index_falls_back_to_identity(self, tmp_path):
        edir = tmp_path / "characters"
        edir.mkdir()
        entities = [_make_v2_entity("char-alpha", "Alpha")]  # no current_status
        _generate_index(str(edir), entities)
        index = json.loads((edir / "index.json").read_text(encoding="utf-8"))
        assert "identity" in index[0]["status_summary"].lower() or len(index[0]["status_summary"]) > 0


# ---------------------------------------------------------------------------
# Merge entity (V2)
# ---------------------------------------------------------------------------

class TestMergeEntityV2:
    def test_new_v2_entity_appended(self):
        catalogs = {"characters.json": []}
        entity = _make_v2_entity("char-alpha", "Alpha", turn="turn-001")
        merge_entity(catalogs, entity)
        assert len(catalogs["characters.json"]) == 1

    def test_existing_v2_entity_updated(self):
        catalogs = {
            "characters.json": [
                _make_v2_entity("char-alpha", "Alpha", turn="turn-001")
            ]
        }
        update = _make_v2_entity("char-alpha", "Alpha", turn="turn-010")
        update["current_status"] = "Now a leader."
        update["last_updated_turn"] = "turn-010"
        merge_entity(catalogs, update)
        assert len(catalogs["characters.json"]) == 1
        assert catalogs["characters.json"][0]["current_status"] == "Now a leader."
        assert catalogs["characters.json"][0]["last_updated_turn"] == "turn-010"


# ---------------------------------------------------------------------------
# parse_turn_number
# ---------------------------------------------------------------------------

class TestParseTurnNumber:
    def test_valid_turn(self):
        assert _parse_turn_number("turn-078") == 78

    def test_none(self):
        assert _parse_turn_number(None) is None

    def test_invalid(self):
        assert _parse_turn_number("invalid") is None

    def test_large_number(self):
        assert _parse_turn_number("turn-345") == 345


# ---------------------------------------------------------------------------
# _dedup_relationships (#183)
# ---------------------------------------------------------------------------

class TestDedupRelationships:
    def test_consolidates_duplicates(self):
        """Two entries for the same target_id should be collapsed to one."""
        rels = [
            {"target_id": "char-bob", "current_relationship": "ally",
             "type": "social", "last_updated_turn": "turn-010"},
            {"target_id": "char-bob", "current_relationship": "friend",
             "type": "social", "last_updated_turn": "turn-020"},
        ]
        result = _dedup_relationships(rels)
        assert len(result) == 1
        assert result[0]["target_id"] == "char-bob"
        # The later turn should win
        assert result[0]["last_updated_turn"] == "turn-020"

    def test_preserves_history(self):
        """History arrays from both entries should be merged."""
        rels = [
            {"target_id": "char-bob", "current_relationship": "ally",
             "type": "social", "last_updated_turn": "turn-010",
             "history": [{"turn": "turn-005", "description": "acquaintance"}]},
            {"target_id": "char-bob", "current_relationship": "friend",
             "type": "social", "last_updated_turn": "turn-020",
             "history": [{"turn": "turn-015", "description": "ally"}]},
        ]
        result = _dedup_relationships(rels)
        assert len(result) == 1
        history = result[0].get("history", [])
        assert len(history) == 2  # both histories merged
        # Should be chronologically sorted
        turns = [h.get("turn") for h in history]
        assert turns == ["turn-005", "turn-015"]

    def test_deduplicates_identical_history(self):
        """Identical history entries should not be duplicated."""
        rels = [
            {"target_id": "char-bob", "current_relationship": "ally",
             "type": "social", "last_updated_turn": "turn-010",
             "history": [{"turn": "turn-005", "description": "acquaintance"}]},
            {"target_id": "char-bob", "current_relationship": "friend",
             "type": "social", "last_updated_turn": "turn-020",
             "history": [{"turn": "turn-005", "description": "acquaintance"}]},
        ]
        result = _dedup_relationships(rels)
        assert len(result) == 1
        history = result[0].get("history", [])
        assert len(history) == 1  # duplicates removed

    def test_preserves_earliest_first_seen_turn(self):
        """The earliest first_seen_turn should be kept."""
        rels = [
            {"target_id": "char-bob", "current_relationship": "ally",
             "type": "social", "first_seen_turn": "turn-005",
             "last_updated_turn": "turn-010"},
            {"target_id": "char-bob", "current_relationship": "friend",
             "type": "social", "first_seen_turn": "turn-020",
             "last_updated_turn": "turn-030"},
        ]
        result = _dedup_relationships(rels)
        assert len(result) == 1
        # Winner (turn-030) should adopt the earlier first_seen_turn
        assert result[0]["first_seen_turn"] == "turn-005"
        assert result[0]["last_updated_turn"] == "turn-030"

    def test_preserves_distinct_targets(self):
        """Relationships with different target_ids should not be merged."""
        rels = [
            {"target_id": "char-bob", "current_relationship": "ally",
             "type": "social", "last_updated_turn": "turn-010"},
            {"target_id": "char-alice", "current_relationship": "rival",
             "type": "adversarial", "last_updated_turn": "turn-020"},
        ]
        result = _dedup_relationships(rels)
        assert len(result) == 2

    def test_empty_list(self):
        assert _dedup_relationships([]) == []


# ---------------------------------------------------------------------------
# cleanup_dangling_relationships (#184)
# ---------------------------------------------------------------------------

class TestCleanupDanglingRelationships:
    def test_removes_missing_targets(self):
        """Relationships targeting non-existent entities should be removed."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-alice",
                    "name": "Alice",
                    "relationships": [
                        {"target_id": "char-bob", "type": "social"},
                        {"target_id": "char-nonexistent", "type": "social"},
                    ],
                },
                {"id": "char-bob", "name": "Bob", "relationships": []},
            ]
        }
        removed = cleanup_dangling_relationships(catalogs)
        assert "char-alice" in removed
        assert "char-nonexistent" in removed["char-alice"]
        # Only valid relationship kept
        assert len(catalogs["characters.json"][0]["relationships"]) == 1
        assert catalogs["characters.json"][0]["relationships"][0]["target_id"] == "char-bob"

    def test_keeps_valid_targets(self):
        """All relationships targeting existing entities should be preserved."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-alice",
                    "name": "Alice",
                    "relationships": [
                        {"target_id": "char-bob", "type": "social"},
                    ],
                },
                {"id": "char-bob", "name": "Bob", "relationships": []},
            ]
        }
        removed = cleanup_dangling_relationships(catalogs)
        assert removed == {}
        assert len(catalogs["characters.json"][0]["relationships"]) == 1

    def test_cross_catalog_targets(self):
        """Targets in a different catalog file should be considered valid."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-alice",
                    "name": "Alice",
                    "relationships": [
                        {"target_id": "loc-town", "type": "other"},
                    ],
                },
            ],
            "locations.json": [
                {"id": "loc-town", "name": "Town", "relationships": []},
            ],
        }
        removed = cleanup_dangling_relationships(catalogs)
        assert removed == {}
        assert len(catalogs["characters.json"][0]["relationships"]) == 1
