"""Tests for apply_coreference_hints() — deterministic entity merging via hints file (#162)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import apply_coreference_hints


def _make_entity(id_, name, turn="turn-001", identity=None, relationships=None,
                 stable_attributes=None):
    entity = {
        "id": id_,
        "name": name,
        "type": "character",
        "identity": identity or f"{name} is a character.",
        "first_seen_turn": turn,
        "last_updated_turn": turn,
        "relationships": relationships or [],
        "stable_attributes": stable_attributes or {},
    }
    return entity


def _make_event(event_id, turn_id, related_entities, description="Something happened"):
    return {
        "event_id": event_id,
        "turn_id": turn_id,
        "type": "interaction",
        "description": description,
        "related_entities": related_entities,
    }


def _write_hints(tmpdir, groups):
    path = os.path.join(tmpdir, "coreference-hints.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"character_groups": groups}, f)
    return path


class TestApplyCoreferenceMerge:
    """Test merging multiple variant entities into a canonical entity."""

    def test_merge_three_variants_into_canonical(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-broad-figure", "broad figure", "turn-009"),
                _make_entity("char-young-hunter", "young hunter", "turn-020"),
                _make_entity("char-brave-warrior", "brave warrior", "turn-050"),
            ]
        }
        events = [
            _make_event("evt-1", "turn-009", ["char-broad-figure"]),
            _make_event("evt-2", "turn-020", ["char-young-hunter", "char-elder"]),
            _make_event("evt-3", "turn-050", ["char-brave-warrior"]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure", "young hunter", "brave warrior"],
                "variant_id_patterns": ["char-broad-figure", "char-young-hunter", "char-brave-warrior"],
            }])

            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert len(merged) == 3
        assert "char-broad-figure" in merged
        assert "char-young-hunter" in merged
        assert "char-brave-warrior" in merged

        # Only canonical remains
        assert len(catalogs["characters.json"]) == 1
        assert catalogs["characters.json"][0]["id"] == "char-kael"

    def test_events_reassociated_after_merge(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-broad-figure", "broad figure", "turn-009"),
            ]
        }
        events = [
            _make_event("evt-1", "turn-009", ["char-broad-figure", "char-elder"]),
            _make_event("evt-2", "turn-010", ["char-broad-figure"]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
                "variant_id_patterns": ["char-broad-figure"],
            }])
            apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        # Events should now reference canonical ID
        assert events[0]["related_entities"] == ["char-kael", "char-elder"]
        assert events[1]["related_entities"] == ["char-kael"]

    def test_relationship_references_rewritten(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149", relationships=[
                    {"target_id": "char-elder", "source_id": "char-kael", "type": "ally_of"},
                ]),
                _make_entity("char-broad-figure", "broad figure", "turn-009"),
                _make_entity("char-elder", "Elder", "turn-001", relationships=[
                    {"target_id": "char-broad-figure", "source_id": "char-elder", "type": "captor_of"},
                ]),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
                "variant_id_patterns": ["char-broad-figure"],
            }])
            apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        # Elder's relationship should now point to char-kael
        elder = [e for e in catalogs["characters.json"] if e["id"] == "char-elder"][0]
        assert elder["relationships"][0]["target_id"] == "char-kael"

    def test_variant_files_deleted_from_disk(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-broad-figure", "broad figure", "turn-009"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog_dir = tmpdir
            char_dir = os.path.join(catalog_dir, "characters")
            os.makedirs(char_dir, exist_ok=True)
            # Create variant entity file on disk
            variant_file = os.path.join(char_dir, "char-broad-figure.json")
            with open(variant_file, "w") as f:
                json.dump(_make_entity("char-broad-figure", "broad figure"), f)

            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
                "variant_id_patterns": ["char-broad-figure"],
            }])
            apply_coreference_hints(catalogs, events, catalog_dir, hints_path)

            assert not os.path.exists(variant_file)

    def test_variant_names_added_as_aliases(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-broad-figure", "broad figure", "turn-009"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure", "young hunter"],
                "variant_id_patterns": ["char-broad-figure"],
            }])
            apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        kael = catalogs["characters.json"][0]
        aliases = kael["stable_attributes"]["aliases"]["value"]
        assert "broad figure" in aliases
        assert "young hunter" in aliases
        # canonical name should not be in aliases
        assert "Kael" not in aliases


class TestCoreferenceGracefulSkip:
    """Test that missing or empty hints are handled gracefully."""

    def test_skip_when_no_hints_file(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
            ]
        }
        events = []

        merged = apply_coreference_hints(catalogs, events, "/nonexistent", "/nonexistent/hints.json")
        assert merged == []
        assert len(catalogs["characters.json"]) == 1

    def test_skip_when_empty_groups(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [])
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert merged == []
        assert len(catalogs["characters.json"]) == 1

    def test_skip_when_canonical_not_found(self, capsys):
        catalogs = {
            "characters.json": [
                _make_entity("char-other", "Other", "turn-001"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
            }])
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert merged == []
        assert len(catalogs["characters.json"]) == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_no_match_leaves_catalogs_unchanged(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-elder", "Elder", "turn-001"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["nonexistent name"],
                "variant_id_patterns": ["char-nonexistent"],
            }])
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert merged == []
        assert len(catalogs["characters.json"]) == 2

    def test_skip_when_malformed_json(self, capsys):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = os.path.join(tmpdir, "coreference-hints.json")
            with open(hints_path, "w") as f:
                f.write("{invalid json content!!!")
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert merged == []
        assert len(catalogs["characters.json"]) == 1
        captured = capsys.readouterr()
        assert "WARNING" in captured.err


class TestCoreferenceMatchByName:
    """Test that variants can be matched by name alone (no ID pattern needed)."""

    def test_match_by_name_only(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-unknown-123", "young hunter", "turn-020"),
            ]
        }
        events = [
            _make_event("evt-1", "turn-020", ["char-unknown-123"]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["young hunter"],
            }])
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert "char-unknown-123" in merged
        assert len(catalogs["characters.json"]) == 1
        assert events[0]["related_entities"] == ["char-kael"]

    def test_match_by_id_pattern_only(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-broad-figure", "A tall broad figure", "turn-009"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": [],
                "variant_id_patterns": ["char-broad-figure"],
            }])
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)

        assert "char-broad-figure" in merged
        assert len(catalogs["characters.json"]) == 1


class TestCoreferenceDryRun:
    """Test that dry_run prevents file deletion but still modifies in-memory catalogs."""

    def test_dry_run_does_not_delete_files(self):
        catalogs = {
            "characters.json": [
                _make_entity("char-kael", "Kael", "turn-149"),
                _make_entity("char-broad-figure", "broad figure", "turn-009"),
            ]
        }
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            catalog_dir = tmpdir
            char_dir = os.path.join(catalog_dir, "characters")
            os.makedirs(char_dir, exist_ok=True)
            variant_file = os.path.join(char_dir, "char-broad-figure.json")
            with open(variant_file, "w") as f:
                json.dump(_make_entity("char-broad-figure", "broad figure"), f)

            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
                "variant_id_patterns": ["char-broad-figure"],
            }])
            merged = apply_coreference_hints(catalogs, events, catalog_dir, hints_path, dry_run=True)

            assert len(merged) == 1
            # File should still exist because dry_run=True
            assert os.path.exists(variant_file)


class TestCoreferenceHintsValidation:
    """Test loading and validating hints files."""

    def test_valid_hints_file_loads(self):
        catalogs = {"characters.json": [_make_entity("char-kael", "Kael")]}
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            hints_path = _write_hints(tmpdir, [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
                "variant_id_patterns": ["char-broad-figure"],
                "notes": "Test note",
            }])
            # Should not raise
            merged = apply_coreference_hints(catalogs, events, tmpdir, hints_path)
            assert isinstance(merged, list)

    def test_hints_schema_valid(self):
        """Validate the hints file against its schema."""
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "coreference-hints.schema.json"
        )
        hints_data = {
            "character_groups": [{
                "canonical_name": "Kael",
                "canonical_id": "char-kael",
                "variant_names": ["broad figure"],
                "variant_id_patterns": ["char-broad-figure"],
                "notes": "test",
            }]
        }

        try:
            import jsonschema
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            jsonschema.validate(hints_data, schema)
        except ImportError:
            pass  # jsonschema not required for basic tests

    def test_session_hints_file_valid(self):
        """Validate the session-import hints file against its schema."""
        hints_path = os.path.join(
            os.path.dirname(__file__), "..", "sessions", "session-import",
            "coreference-hints.json",
        )
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "coreference-hints.schema.json"
        )

        if not os.path.exists(hints_path):
            return  # Skip if session file doesn't exist

        with open(hints_path, "r", encoding="utf-8") as f:
            hints_data = json.load(f)

        try:
            import jsonschema
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            jsonschema.validate(hints_data, schema)
        except ImportError:
            pass  # jsonschema not required
