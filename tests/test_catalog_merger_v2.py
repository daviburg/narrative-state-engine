"""Tests for V2 catalog_merger: per-entity I/O, relationship consolidation,
dormancy marking, and index generation."""
import json
import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import (
    _consolidate_relationship,
    _generate_index,
    _merge_entity_relationships,
    _parse_turn_number,
    _read_v2_entities,
    _write_v2_entity,
    detect_format,
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


def _make_v1_entity(id_, name, etype="character", turn="turn-001"):
    """Create a V1-shaped entity dict."""
    return {
        "id": id_,
        "name": name,
        "type": etype,
        "description": f"{name} description.",
        "attributes": {},
        "first_seen_turn": turn,
        "last_updated_turn": turn,
        "relationships": [],
    }


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

class TestFormatDetection:
    def test_v2_detected_when_all_directories_exist(self, tmp_path):
        for d in ["characters", "locations", "factions", "items"]:
            (tmp_path / d).mkdir()
        assert detect_format(str(tmp_path)) == "v2"

    def test_v1_detected_when_no_directories(self, tmp_path):
        (tmp_path / "characters.json").write_text("[]")
        assert detect_format(str(tmp_path)) == "v1"

    def test_v2_detected_with_partial_dirs_no_v1_files(self, tmp_path):
        (tmp_path / "locations").mkdir()
        assert detect_format(str(tmp_path)) == "v2"

    def test_v1_on_empty_dir(self, tmp_path):
        assert detect_format(str(tmp_path)) == "v1"

    def test_mixed_layout_falls_back_to_v1(self, tmp_path):
        """If some V2 dirs exist but V1 flat files also remain, use V1."""
        (tmp_path / "characters").mkdir()
        (tmp_path / "locations.json").write_text("[]")
        assert detect_format(str(tmp_path)) == "v1"

    def test_all_dirs_overrides_v1_files(self, tmp_path):
        """If all V2 dirs exist, use V2 even if stale flat files remain."""
        for d in ["characters", "locations", "factions", "items"]:
            (tmp_path / d).mkdir()
        (tmp_path / "characters.json").write_text("[]")  # stale leftover
        assert detect_format(str(tmp_path)) == "v2"


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
# V1 fallback read
# ---------------------------------------------------------------------------

class TestV1FallbackRead:
    def test_reads_flat_file(self, tmp_path):
        entities = [_make_v1_entity("char-alpha", "Alpha")]
        (tmp_path / "characters.json").write_text(json.dumps(entities), encoding="utf-8")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            catalogs = load_catalogs(str(tmp_path))
            assert len(w) == 1
            assert "V1 flat catalog" in str(w[0].message)
        assert len(catalogs["characters.json"]) == 1
        assert catalogs["characters.json"][0]["id"] == "char-alpha"

    def test_v1_returns_empty_for_missing_files(self, tmp_path):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            catalogs = load_catalogs(str(tmp_path))
        for filename in ["characters.json", "locations.json", "factions.json", "items.json"]:
            assert catalogs[filename] == []


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

    def test_v1_relationship_field_becomes_current_relationship(self):
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

    def test_real_v1_data_stays_v1(self, tmp_path):
        """When real V1 data exists, save_catalogs should stay V1 for backward compat."""
        catalog_dir = str(tmp_path / "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)
        # Write V1 flat file with real data
        with open(os.path.join(catalog_dir, "characters.json"), "w") as f:
            json.dump([{"id": "char-old", "name": "Old", "type": "character",
                        "description": "An old format entity"}], f)
        for name in ["locations.json", "factions.json", "items.json"]:
            with open(os.path.join(catalog_dir, name), "w") as f:
                json.dump([], f)

        catalogs = {"characters.json": [{"id": "char-old", "name": "Old", "type": "character",
                                         "description": "An old format entity"}],
                    "locations.json": [], "factions.json": [], "items.json": []}
        save_catalogs(catalog_dir, catalogs)

        # Should NOT have created V2 directory — V1 data exists
        assert not os.path.isdir(os.path.join(catalog_dir, "characters"))

    def test_prefer_v2_false_stays_v1(self, tmp_path):
        """When prefer_v2=False, clean start stays V1."""
        catalog_dir = str(tmp_path / "catalogs")
        os.makedirs(catalog_dir, exist_ok=True)
        for name in ["characters.json", "locations.json", "factions.json", "items.json"]:
            with open(os.path.join(catalog_dir, name), "w") as f:
                json.dump([], f)

        catalogs = {
            "characters.json": [{"id": "char-test", "name": "Test", "type": "character",
                                 "identity": "Test", "first_seen_turn": "turn-001"}],
            "locations.json": [], "factions.json": [], "items.json": [],
        }
        save_catalogs(catalog_dir, catalogs, prefer_v2=False)

        # Should NOT have created V2 directory
        assert not os.path.isdir(os.path.join(catalog_dir, "characters"))

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
