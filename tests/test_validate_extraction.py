"""Tests for validate_extraction relationship checks (#184, #183)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from validate_extraction import (
    Result,
    check_dangling_relationships,
    check_duplicate_relationships,
)


def _write_entity(catalog_dir, subdir, entity_id, entity_data):
    """Write a minimal entity JSON file under catalog_dir/subdir/."""
    d = catalog_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{entity_id}.json").write_text(
        json.dumps(entity_data), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# check_dangling_relationships
# ---------------------------------------------------------------------------

class TestCheckDanglingRelationships:
    def test_no_dangling_targets(self, tmp_path):
        _write_entity(tmp_path, "characters", "char-alice", {
            "id": "char-alice",
            "relationships": [{"target_id": "char-bob"}],
        })
        _write_entity(tmp_path, "characters", "char-bob", {
            "id": "char-bob",
            "relationships": [],
        })
        results = check_dangling_relationships(tmp_path)
        assert len(results) == 1
        assert results[0].status == Result.PASS

    def test_detects_dangling_target(self, tmp_path):
        _write_entity(tmp_path, "characters", "char-alice", {
            "id": "char-alice",
            "relationships": [{"target_id": "char-nonexistent"}],
        })
        results = check_dangling_relationships(tmp_path)
        assert any(r.status == Result.WARN and "char-nonexistent" in r.detail for r in results)

    def test_warns_on_unreadable_file(self, tmp_path):
        d = tmp_path / "characters"
        d.mkdir()
        (d / "char-broken.json").write_text("NOT VALID JSON", encoding="utf-8")
        results = check_dangling_relationships(tmp_path)
        assert any(r.status == Result.WARN and "could not load" in r.detail for r in results)


# ---------------------------------------------------------------------------
# check_duplicate_relationships
# ---------------------------------------------------------------------------

class TestCheckDuplicateRelationships:
    def test_no_duplicates(self, tmp_path):
        _write_entity(tmp_path, "characters", "char-alice", {
            "id": "char-alice",
            "relationships": [
                {"target_id": "char-bob"},
                {"target_id": "char-carol"},
            ],
        })
        results = check_duplicate_relationships(tmp_path)
        assert len(results) == 1
        assert results[0].status == Result.PASS

    def test_detects_duplicates(self, tmp_path):
        _write_entity(tmp_path, "characters", "char-alice", {
            "id": "char-alice",
            "relationships": [
                {"target_id": "char-bob", "type": "social"},
                {"target_id": "char-bob", "type": "adversarial"},
            ],
        })
        results = check_duplicate_relationships(tmp_path)
        assert any(
            r.status == Result.WARN and "char-bob" in r.detail and "2" in r.detail
            for r in results
        )
