"""Tests for fuzzy dedup matching in _dedup_catalogs()."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _dedup_catalogs


def _make_entity(id_, name, turn="turn-001"):
    return {
        "id": id_,
        "name": name,
        "first_seen_turn": turn,
        "attributes": {},
        "relationships": [],
    }


def _ids(catalogs, filename):
    return {e["id"] for e in catalogs[filename]}


def test_token_overlap_merge_spear():
    catalogs = {
        "items.json": [
            _make_entity("item-crude-woodhafted-spear", "Crude wood-hafted spear", "turn-003"),
            _make_entity("item-crude-spear", "Crude spear", "turn-007"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 1
    assert len(catalogs["items.json"]) == 1
    assert "item-crude-spear" in merge_map
    assert merge_map["item-crude-spear"] == "item-crude-woodhafted-spear"


def test_substring_merge_bowl_group():
    """With tighter dedup rules, only closely-related bowls merge."""
    catalogs = {
        "items.json": [
            _make_entity("item-bowl", "Bowl", "turn-002"),
            _make_entity("item-steaming-bowl", "Steaming bowl", "turn-005"),
            _make_entity("item-steaming-broth-bowl", "Steaming broth bowl", "turn-008"),
            _make_entity("item-warm-wooden-bowl", "Warm wooden bowl", "turn-010"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    # 'bowl' is a stopword → item-bowl has no tokens and 1-segment ID stem → stays separate.
    # 'steaming bowl' and 'steaming broth bowl' share enough overlap → merge.
    # 'warm wooden bowl' has no token/stem overlap with steaming variants → stays separate.
    assert count == 1
    assert len(catalogs["items.json"]) == 3
    surviving_ids = {e["id"] for e in catalogs["items.json"]}
    assert "item-bowl" in surviving_ids
    assert "item-steaming-bowl" in surviving_ids  # survivor of steaming pair
    assert "item-warm-wooden-bowl" in surviving_ids


def test_substring_merge_moonpetal():
    catalogs = {
        "items.json": [
            _make_entity("item-dried-moonpetal", "Dried moonpetal", "turn-004"),
            _make_entity("item-moonpetal", "Moonpetal", "turn-009"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 1
    assert len(catalogs["items.json"]) == 1
    assert "item-moonpetal" in merge_map


def test_no_cross_catalog_merge():
    catalogs = {
        "characters.json": [
            _make_entity("char-elder", "Elder", "turn-001"),
        ],
        "locations.json": [
            _make_entity("loc-forest", "Forest", "turn-001"),
        ],
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 0
    assert len(catalogs["characters.json"]) == 1
    assert len(catalogs["locations.json"]) == 1


def test_id_stem_merge():
    catalogs = {
        "items.json": [
            _make_entity("item-crude-spear", "Ashbrand", "turn-003"),
            _make_entity("item-crude-spear-broken", "Nightglass", "turn-010"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 1
    assert len(catalogs["items.json"]) == 1
    assert "item-crude-spear-broken" in merge_map
    assert merge_map["item-crude-spear-broken"] == "item-crude-spear"


def test_no_partial_word_substring_merge():
    """'ring' should NOT match 'spring' — only whole-token containment."""
    catalogs = {
        "items.json": [
            _make_entity("item-ring", "Ring", "turn-001"),
            _make_entity("item-spring", "Spring", "turn-002"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 0
    assert len(catalogs["items.json"]) == 2


def test_no_false_merge_crude_weapons_vs_buckets():
    """'crude weapons' and 'crude wooden buckets' should NOT merge."""
    catalogs = {
        "items.json": [
            _make_entity("item-crude-weapons", "crude weapons", "turn-001"),
            _make_entity("item-crude-wooden-buckets", "crude wooden buckets", "turn-002"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 0
    assert len(catalogs["items.json"]) == 2


def test_no_false_merge_herb_party_vs_hunting_party():
    """'herb gathering party' and 'hunting party' should NOT merge."""
    catalogs = {
        "factions.json": [
            _make_entity("faction-herb-gathering-party", "herb gathering party", "turn-001"),
            _make_entity("faction-hunting-party", "hunting party", "turn-002"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 0
    assert len(catalogs["factions.json"]) == 2


def test_no_false_merge_broad_figure_vs_lone_figure():
    """'broad figure' and 'a lone figure' should NOT merge."""
    catalogs = {
        "characters.json": [
            _make_entity("char-broad-figure", "broad figure", "turn-001"),
            _make_entity("char-lone-figure", "a lone figure", "turn-002"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    assert count == 0
    assert len(catalogs["characters.json"]) == 2


def test_correct_merge_snow_laden_pines_woods():
    """'snow-laden pines' and 'snow-laden woods' SHOULD merge (2-token overlap, 100%)."""
    catalogs = {
        "locations.json": [
            _make_entity("loc-snow-laden-pines", "snow-laden pines", "turn-001"),
            _make_entity("loc-snow-laden-woods", "snow-laden woods", "turn-005"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    # Both have tokens {snow, laden, pines/woods}; after stopword removal,
    # smaller has 3 tokens, overlap is {snow, laden} = 2/3 = 67% > 50% → merge
    # Wait — "snow-laden" splits to {snow, laden} and then + pines/woods = 3 tokens each.
    # overlap = {snow, laden} = 2; smaller = 3; 2/3 < 1.0 threshold for <=2
    # But smaller is 3, so threshold is 0.5, and 2/3 >= 0.5 → merge!
    assert count == 1
    assert len(catalogs["locations.json"]) == 1


def test_single_token_bowl_no_merge():
    """'bowl' should NOT merge with 'steaming bowl' — single-segment ID stem blocked."""
    catalogs = {
        "items.json": [
            _make_entity("item-bowl", "Bowl", "turn-002"),
            _make_entity("item-steaming-bowl", "Steaming bowl", "turn-005"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    # 'bowl' is a stopword so token rules won't fire,
    # and the ID-stem guard now requires 2+ segments in the smaller set.
    assert count == 0
    assert len(catalogs["items.json"]) == 2


# ---------------------------------------------------------------------------
# Batch dormancy skip (Issue #99)
# ---------------------------------------------------------------------------

def test_batch_extraction_does_not_call_dormancy():
    """extract_semantic_batch should NOT invoke mark_dormant_relationships."""
    with patch("semantic_extraction.mark_dormant_relationships") as mock_dormancy, \
         patch("semantic_extraction.LLMClient") as mock_llm_cls, \
         patch("semantic_extraction.load_catalogs", return_value={f: [] for f in ["characters.json","locations.json","factions.json","items.json"]}), \
         patch("semantic_extraction.load_events", return_value=[]), \
         patch("semantic_extraction.save_catalogs"), \
         patch("semantic_extraction.save_events"), \
         patch("semantic_extraction._save_progress"), \
         patch("semantic_extraction._ensure_player_character"):
        mock_llm_cls.return_value = MagicMock()
        from semantic_extraction import extract_semantic_batch
        extract_semantic_batch([], "session-test", "framework-test")
        mock_dormancy.assert_not_called()


def test_single_turn_extraction_calls_dormancy():
    """extract_semantic_single should invoke mark_dormant_relationships."""
    with patch("semantic_extraction.mark_dormant_relationships", return_value=0) as mock_dormancy, \
         patch("semantic_extraction.LLMClient") as mock_llm_cls, \
         patch("semantic_extraction.load_catalogs", return_value={f: [] for f in ["characters.json","locations.json","factions.json","items.json"]}), \
         patch("semantic_extraction.load_events", return_value=[]), \
         patch("semantic_extraction.save_catalogs"), \
         patch("semantic_extraction.save_events"), \
         patch("semantic_extraction._ensure_player_character"), \
         patch("semantic_extraction.extract_and_merge", return_value=({f: [] for f in ["characters.json","locations.json","factions.json","items.json"]}, [], False)):
        mock_llm_cls.return_value = MagicMock()
        from semantic_extraction import extract_semantic_single
        extract_semantic_single("turn-001", "dm", "Test text", "session-test", "framework-test")
        mock_dormancy.assert_called_once()


def test_dedup_normalize_map_in_merge_map():
    """Turn-tagged IDs normalized during dedup pre-pass appear in merge_map."""
    catalogs = {
        "characters.json": [
            _make_entity("char-shaman", "The Shaman", "turn-059"),
            _make_entity("char-shaman-turn-082", "The Shaman", "turn-082"),
        ]
    }
    count, merge_map = _dedup_catalogs(catalogs)
    # The turn-tagged entity gets normalized + deduped; its old ID is in merge_map
    assert "char-shaman-turn-082" in merge_map
    assert merge_map["char-shaman-turn-082"] == "char-shaman"
