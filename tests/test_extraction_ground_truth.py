"""Tests for the extraction ground truth fixture and validation framework (#159)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "fixtures",
    "extraction-ground-truth-full-session.json",
)


@pytest.fixture
def ground_truth():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixture structure tests
# ---------------------------------------------------------------------------


class TestFixtureStructure:
    def test_fixture_loads_and_parses(self, ground_truth):
        assert isinstance(ground_truth, dict)
        assert "description" in ground_truth
        assert "source_session" in ground_truth

    def test_fixture_has_expected_characters(self, ground_truth):
        chars = ground_truth.get("expected_independent_characters", [])
        assert len(chars) >= 10, "Expected at least 10 independent characters"
        for char in chars:
            assert "name" in char
            assert "id_patterns" in char
            assert isinstance(char["id_patterns"], list)
            assert len(char["id_patterns"]) > 0

    def test_fixture_has_pc_aliases(self, ground_truth):
        aliases = ground_truth.get("expected_pc_aliases", [])
        assert isinstance(aliases, list)
        assert len(aliases) >= 1

    def test_fixture_has_must_not_merge(self, ground_truth):
        rules = ground_truth.get("must_not_merge", [])
        assert len(rules) >= 1
        for rule in rules:
            assert "pair" in rule
            assert len(rule["pair"]) == 2
            assert "reason" in rule

    def test_fixture_has_coreference_groups(self, ground_truth):
        groups = ground_truth.get("coreference_groups", [])
        assert len(groups) >= 1
        for group in groups:
            assert "canonical_name" in group
            assert "expected_id" in group
            assert "variants_to_merge" in group

    def test_fixture_turn_ranges_valid(self, ground_truth):
        turn_range = ground_truth.get("turn_range", [])
        assert len(turn_range) == 2
        assert turn_range[0] < turn_range[1]

        for char in ground_truth.get("expected_independent_characters", []):
            r = char.get("expected_first_seen_range", [])
            assert len(r) == 2, f"{char['name']}: missing first_seen_range"
            assert r[0] <= r[1], f"{char['name']}: invalid range {r}"
            assert r[0] >= turn_range[0], f"{char['name']}: range before session"
            assert r[1] <= turn_range[1], f"{char['name']}: range after session"

    def test_fixture_no_duplicate_names(self, ground_truth):
        chars = ground_truth.get("expected_independent_characters", [])
        names = [c["name"] for c in chars]
        assert len(names) == len(set(names)), (
            f"Duplicate character names: "
            f"{[n for n in names if names.count(n) > 1]}"
        )

    def test_fixture_has_staleness_targets(self, ground_truth):
        targets = ground_truth.get("entity_staleness_targets", [])
        assert len(targets) >= 1
        for t in targets:
            assert "id" in t
            assert "expected_last_updated_min" in t
            assert isinstance(t["expected_last_updated_min"], int)

    def test_fixture_has_location_and_faction_expectations(self, ground_truth):
        locations = ground_truth.get("expected_locations_beyond_turn_100", [])
        factions = ground_truth.get("expected_factions_beyond_early_game", [])
        assert len(locations) >= 1
        assert len(factions) >= 1


# ---------------------------------------------------------------------------
# Validation script import tests
# ---------------------------------------------------------------------------


class TestValidationScriptImportable:
    def test_validation_script_importable(self):
        import validate_extraction
        assert hasattr(validate_extraction, "validate")
        assert hasattr(validate_extraction, "main")
        assert callable(validate_extraction.validate)

    def test_check_functions_exist(self):
        import validate_extraction
        assert hasattr(validate_extraction, "check_independent_characters")
        assert hasattr(validate_extraction, "check_pc_aliases")
        assert hasattr(validate_extraction, "check_must_not_merge")
        assert hasattr(validate_extraction, "check_coreference_groups")
        assert hasattr(validate_extraction, "check_staleness")
        assert hasattr(validate_extraction, "check_locations")
        assert hasattr(validate_extraction, "check_factions")
