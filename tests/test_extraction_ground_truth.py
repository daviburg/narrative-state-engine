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


# ---------------------------------------------------------------------------
# Functional end-to-end tests with synthetic catalog
# ---------------------------------------------------------------------------

def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.fixture
def mini_ground_truth(tmp_path):
    """Minimal ground truth covering key check paths."""
    gt = {
        "description": "mini fixture",
        "source_session": "test",
        "turn_range": [1, 50],
        "expected_pc_aliases": ["Hero Name"],
        "expected_independent_characters": [
            {
                "name": "Alice",
                "role": "ally",
                "id_patterns": ["char-alice"],
                "must_not_be_pc_alias": True,
                "expected_first_seen_range": [1, 5],
                "expected_last_updated_min": 40,
                "notes": "test char",
            },
            {
                "name": "MissingBob",
                "role": "missing",
                "id_patterns": ["char-bob"],
                "must_not_be_pc_alias": True,
                "expected_first_seen_range": [10, 12],
                "expected_last_updated_min": 30,
                "notes": "should be missing",
            },
        ],
        "must_not_merge": [
            {"pair": ["Alice", "char-player"], "reason": "Alice is an NPC"},
        ],
        "coreference_groups": [
            {
                "canonical_name": "Alice",
                "expected_id": "char-alice",
                "variants_to_merge": ["tall figure"],
                "notes": "test group",
            },
        ],
        "entity_staleness_targets": [
            {"id": "char-alice", "expected_last_updated_min": 40, "reason": "active"},
            {"id": "char-gone", "expected_last_updated_min": 30, "reason": "deleted"},
        ],
        "expected_locations_beyond_turn_100": [],
        "expected_factions_beyond_early_game": [],
    }
    gt_path = tmp_path / "gt.json"
    _write_json(gt_path, gt)
    return gt_path


@pytest.fixture
def synthetic_catalog(tmp_path):
    """Synthetic catalog directory with controlled entities."""
    cat_dir = tmp_path / "catalogs"
    chars = cat_dir / "characters"
    chars.mkdir(parents=True)

    # PC with one correct and one false alias
    _write_json(chars / "char-player.json", {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-045",
        "stable_attributes": {
            "aliases": {"value": ["Hero Name", "Alice"], "source_turn": "turn-005"},
        },
    })

    # Alice exists but is stale
    _write_json(chars / "char-alice.json", {
        "id": "char-alice",
        "name": "Alice",
        "type": "character",
        "first_seen_turn": "turn-003",
        "last_updated_turn": "turn-015",
        "stable_attributes": {},
    })

    # A coreference fragment that should have been merged
    _write_json(chars / "char-tall-figure.json", {
        "id": "char-tall-figure",
        "name": "tall figure",
        "type": "character",
        "first_seen_turn": "turn-003",
        "last_updated_turn": "turn-004",
        "stable_attributes": {},
    })

    return cat_dir


class TestFunctionalValidation:
    def test_independent_char_found_but_stale(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        gt = json.loads(mini_ground_truth.read_text(encoding="utf-8"))
        pc_aliases = validate_extraction._pc_aliases(synthetic_catalog)
        results = validate_extraction.check_independent_characters(
            gt, synthetic_catalog, pc_aliases,
        )
        alice = [r for r in results if r.label == "Alice"][0]
        # Alice exists but last_updated_turn=15 vs expected 40 → gap=25 → WARN
        assert alice.status == validate_extraction.Result.WARN

    def test_independent_char_missing(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        gt = json.loads(mini_ground_truth.read_text(encoding="utf-8"))
        pc_aliases = validate_extraction._pc_aliases(synthetic_catalog)
        results = validate_extraction.check_independent_characters(
            gt, synthetic_catalog, pc_aliases,
        )
        bob = [r for r in results if r.label == "MissingBob"][0]
        assert bob.status == validate_extraction.Result.FAIL

    def test_pc_alias_correct_and_false_positive(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        gt = json.loads(mini_ground_truth.read_text(encoding="utf-8"))
        pc_aliases = validate_extraction._pc_aliases(synthetic_catalog)
        results = validate_extraction.check_pc_aliases(gt, pc_aliases)
        statuses = {r.label: r.status for r in results}
        # "hero name" should be PASS, "alice" should be FAIL (false positive)
        assert statuses["hero name"] == validate_extraction.Result.PASS
        assert statuses["alice"] == validate_extraction.Result.FAIL

    def test_must_not_merge_detects_pc_alias(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        gt = json.loads(mini_ground_truth.read_text(encoding="utf-8"))
        pc_aliases = validate_extraction._pc_aliases(synthetic_catalog)
        results = validate_extraction.check_must_not_merge(
            gt, synthetic_catalog, pc_aliases,
        )
        assert len(results) == 1
        assert results[0].status == validate_extraction.Result.FAIL
        assert "MERGED" in results[0].detail

    def test_coreference_fragmentation_detected(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        gt = json.loads(mini_ground_truth.read_text(encoding="utf-8"))
        results = validate_extraction.check_coreference_groups(gt, synthetic_catalog)
        assert len(results) == 1
        assert results[0].status == validate_extraction.Result.WARN
        assert "FRAGMENT" in results[0].detail

    def test_staleness_missing_entity(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        gt = json.loads(mini_ground_truth.read_text(encoding="utf-8"))
        results = validate_extraction.check_staleness(gt, synthetic_catalog)
        gone = [r for r in results if r.label == "char-gone"][0]
        assert gone.status == validate_extraction.Result.FAIL

    def test_validate_returns_nonzero(self, mini_ground_truth, synthetic_catalog):
        import validate_extraction
        exit_code = validate_extraction.validate(synthetic_catalog, mini_ground_truth)
        assert exit_code == 1, "Expected failures in synthetic catalog"

    def test_normalize_aliases_string(self):
        import validate_extraction
        assert validate_extraction._normalize_aliases("foo, bar") == ["foo", "bar"]
        assert validate_extraction._normalize_aliases("single") == ["single"]
        assert validate_extraction._normalize_aliases(None) == []
        assert validate_extraction._normalize_aliases(["A", "B"]) == ["a", "b"]
