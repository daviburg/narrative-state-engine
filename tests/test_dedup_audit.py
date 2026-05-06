"""Tests for dedup_audit.py — post-extraction duplicate entity detection."""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from dedup_audit import (
    AUTO_MERGE_THRESHOLD,
    REVIEW_THRESHOLD,
    generate_candidates,
    process_results,
    score_pair,
    _normalize_name,
    _edit_distance,
)


def _make_entity(id_, name, turn="turn-001", relationships=None):
    """Helper to build a minimal entity dict."""
    entity = {
        "id": id_,
        "name": name,
        "type": id_.split("-")[0].replace("loc", "location").replace("char", "character"),
        "identity": f"{name} entity",
        "first_seen_turn": turn,
    }
    if relationships:
        entity["relationships"] = relationships
    return entity


def _make_catalogs(entities):
    """Build a catalogs dict from a flat list of entities."""
    catalogs = {
        "characters.json": [],
        "locations.json": [],
        "factions.json": [],
        "items.json": [],
    }
    for e in entities:
        eid = e["id"]
        if eid.startswith("char-"):
            catalogs["characters.json"].append(e)
        elif eid.startswith("loc-"):
            catalogs["locations.json"].append(e)
        elif eid.startswith("faction-"):
            catalogs["factions.json"].append(e)
        elif eid.startswith("item-"):
            catalogs["items.json"].append(e)
    return catalogs


class TestCandidateGeneration:
    def test_candidate_generation_typo(self):
        """loc-communal-longhouse and loc-communial-home found as candidates (edit distance)."""
        entities = [
            _make_entity("loc-communal-longhouse", "Communal Longhouse"),
            _make_entity("loc-communial-home", "Communial Home"),
        ]
        catalogs = _make_catalogs(entities)
        candidates = generate_candidates(catalogs)
        pair_ids = [(a, b) for a, b, _ in candidates]
        assert ("loc-communal-longhouse", "loc-communial-home") in pair_ids or \
               ("loc-communial-home", "loc-communal-longhouse") in pair_ids

    def test_candidate_generation_substring(self):
        """loc-camp and loc-campsite found as candidates (substring)."""
        entities = [
            _make_entity("loc-camp", "Camp"),
            _make_entity("loc-campsite", "Campsite"),
        ]
        catalogs = _make_catalogs(entities)
        candidates = generate_candidates(catalogs)
        pair_ids = [(a, b) for a, b, _ in candidates]
        assert ("loc-camp", "loc-campsite") in pair_ids or \
               ("loc-campsite", "loc-camp") in pair_ids

    def test_candidate_generation_ignores_different_types(self):
        """char-camp and loc-camp are NOT paired (different type prefixes)."""
        entities = [
            _make_entity("char-camp", "Camp"),
            _make_entity("loc-camp", "Camp"),
        ]
        catalogs = _make_catalogs(entities)
        candidates = generate_candidates(catalogs)
        pair_ids = [(a, b) for a, b, _ in candidates]
        assert ("char-camp", "loc-camp") not in pair_ids
        assert ("loc-camp", "char-camp") not in pair_ids

    def test_candidate_no_false_positive_distinct_names(self):
        """char-borin and char-boruk NOT paired when both have rich independent data."""
        entities = [
            _make_entity("char-borin", "Borin", relationships=[
                {"target_id": "loc-mine", "current_relationship": "works at", "type": "location"},
                {"target_id": "char-dwarf-king", "current_relationship": "serves", "type": "social"},
                {"target_id": "item-pickaxe", "current_relationship": "owns", "type": "possession"},
            ]),
            _make_entity("char-boruk", "Boruk", relationships=[
                {"target_id": "loc-tavern", "current_relationship": "frequents", "type": "location"},
                {"target_id": "char-elf-queen", "current_relationship": "serves", "type": "social"},
                {"target_id": "item-sword", "current_relationship": "owns", "type": "possession"},
            ]),
        ]
        catalogs = _make_catalogs(entities)
        candidates = generate_candidates(catalogs)
        pair_ids = [(a, b) for a, b, _ in candidates]
        assert ("char-borin", "char-boruk") not in pair_ids
        assert ("char-boruk", "char-borin") not in pair_ids


class TestScoring:
    def test_score_auto_merge_threshold(self):
        """Confidence 0.95 -> action = auto-merge."""
        scored_pairs = [{
            "entity_a_id": "loc-camp",
            "entity_b_id": "loc-campsite",
            "same_entity": True,
            "confidence": 0.95,
            "canonical_id": "loc-camp",
            "rationale": "Same location, different names",
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            review_file = os.path.join(tmpdir, "review.json")
            result = process_results(
                scored_pairs, tmpdir, auto_merge=False,
                review_file=review_file, dry_run=True,
            )
        assert result["auto_merged"] == 1
        assert result["flagged_for_review"] == 0
        assert result["discarded"] == 0

    def test_score_review_threshold(self):
        """Confidence 0.75 -> action = flag for review."""
        scored_pairs = [{
            "entity_a_id": "char-apprentice",
            "entity_b_id": "char-elara",
            "same_entity": True,
            "confidence": 0.75,
            "canonical_id": "char-elara",
            "rationale": "Possibly same person",
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            review_file = os.path.join(tmpdir, "review.json")
            result = process_results(
                scored_pairs, tmpdir, auto_merge=False,
                review_file=review_file, dry_run=True,
            )
        assert result["auto_merged"] == 0
        assert result["flagged_for_review"] == 1
        assert result["discarded"] == 0

    def test_score_discard_threshold(self):
        """Confidence 0.4 -> action = discard."""
        scored_pairs = [{
            "entity_a_id": "char-borin",
            "entity_b_id": "char-boruk",
            "same_entity": False,
            "confidence": 0.4,
            "canonical_id": "char-borin",
            "rationale": "Different characters",
        }]
        with tempfile.TemporaryDirectory() as tmpdir:
            review_file = os.path.join(tmpdir, "review.json")
            result = process_results(
                scored_pairs, tmpdir, auto_merge=False,
                review_file=review_file, dry_run=True,
            )
        assert result["auto_merged"] == 0
        assert result["flagged_for_review"] == 0
        assert result["discarded"] == 1

    def test_review_file_format(self):
        """Output review JSON matches expected schema."""
        scored_pairs = [
            {
                "entity_a_id": "char-apprentice",
                "entity_b_id": "char-elara",
                "same_entity": True,
                "confidence": 0.75,
                "canonical_id": "char-elara",
                "rationale": "Possibly same person",
            },
            {
                "entity_a_id": "loc-camp",
                "entity_b_id": "loc-campsite",
                "same_entity": True,
                "confidence": 0.8,
                "canonical_id": "loc-camp",
                "rationale": "Same location",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            review_file = os.path.join(tmpdir, "review.json")
            process_results(
                scored_pairs, tmpdir, auto_merge=False,
                review_file=review_file, dry_run=False,
            )
            assert os.path.isfile(review_file)
            with open(review_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            assert isinstance(data, list)
            assert len(data) == 2
            for entry in data:
                assert "entity_a" in entry
                assert "entity_b" in entry
                assert "confidence" in entry
                assert "rationale" in entry
                assert "action" in entry
                assert entry["action"] is None
                assert 0.0 <= entry["confidence"] <= 1.0
