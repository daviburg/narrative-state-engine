"""Tests for compound-term fragment rejection in entity discovery (#398)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _build_compound_word_index,
    _is_compound_term_fragment,
)


# ---------------------------------------------------------------------------
# _build_compound_word_index
# ---------------------------------------------------------------------------

class TestBuildCompoundWordIndex:
    def test_multi_word_entity_indexed(self):
        catalogs = {"items.json": [{"name": "Ice Shard", "type": "item"}]}
        index = _build_compound_word_index(catalogs)
        assert "ice" in index
        assert "shard" in index

    def test_single_word_entity_not_indexed(self):
        catalogs = {"characters.json": [{"name": "Kael", "type": "character"}]}
        index = _build_compound_word_index(catalogs)
        assert "kael" not in index

    def test_three_word_entity_all_words_indexed(self):
        catalogs = {"items.json": [{"name": "Triangular Pattern Disruption Field", "type": "item"}]}
        index = _build_compound_word_index(catalogs)
        assert "triangular" in index
        assert "pattern" in index
        assert "disruption" in index
        assert "field" in index

    def test_short_words_excluded(self):
        """Words shorter than 3 characters (e.g., 'of', 'a') are not indexed."""
        catalogs = {"items.json": [{"name": "Staff of Power", "type": "item"}]}
        index = _build_compound_word_index(catalogs)
        assert "of" not in index
        assert "staff" in index
        assert "power" in index

    def test_current_entities_also_indexed(self):
        """Words from current-turn entities (not yet in catalog) are indexed too."""
        catalogs: dict = {}
        current = [{"name": "Quiet Weave", "type": "location"}]
        index = _build_compound_word_index(catalogs, current_entities=current)
        assert "quiet" in index
        assert "weave" in index

    def test_index_value_is_original_compound_name(self):
        catalogs = {"items.json": [{"name": "Frost Precision", "type": "concept"}]}
        index = _build_compound_word_index(catalogs)
        assert index["frost"] == "Frost Precision"
        assert index["precision"] == "Frost Precision"

    def test_empty_catalogs(self):
        assert _build_compound_word_index({}) == {}

    def test_entity_without_name_skipped(self):
        catalogs = {"characters.json": [{"type": "character"}]}
        index = _build_compound_word_index(catalogs)
        assert len(index) == 0


# ---------------------------------------------------------------------------
# _is_compound_term_fragment
# ---------------------------------------------------------------------------

class TestIsCompoundTermFragment:
    def _make_index(self, *compound_names):
        catalogs = {
            "items.json": [{"name": n} for n in compound_names],
        }
        return _build_compound_word_index(catalogs)

    def test_single_word_fragment_rejected(self):
        index = self._make_index("Ice Shard")
        is_frag, compound = _is_compound_term_fragment({"name": "Ice"}, index)
        assert is_frag is True
        assert compound == "Ice Shard"

    def test_lowercase_fragment_rejected(self):
        index = self._make_index("Ice Shard")
        is_frag, compound = _is_compound_term_fragment({"name": "ice"}, index)
        assert is_frag is True

    def test_second_word_fragment_rejected(self):
        index = self._make_index("Ice Shard")
        is_frag, _ = _is_compound_term_fragment({"name": "shard"}, index)
        assert is_frag is True

    def test_multi_word_entity_not_rejected(self):
        """Multi-word entities are never fragments themselves."""
        index = self._make_index("Ice Shard")
        is_frag, _ = _is_compound_term_fragment({"name": "Ice Shard"}, index)
        assert is_frag is False

    def test_unrelated_single_word_not_rejected(self):
        """A single-word name that doesn't match any compound word is kept."""
        index = self._make_index("Ice Shard")
        is_frag, _ = _is_compound_term_fragment({"name": "Kael"}, index)
        assert is_frag is False

    def test_empty_index_never_rejects(self):
        is_frag, _ = _is_compound_term_fragment({"name": "ice"}, {})
        assert is_frag is False

    def test_entity_without_name_not_rejected(self):
        index = self._make_index("Ice Shard")
        is_frag, _ = _is_compound_term_fragment({}, index)
        assert is_frag is False

    def test_quiet_weave_fragments_rejected(self):
        """Reproduces the 'Quiet Weave' settlement fragmentation bug."""
        index = self._make_index("Quiet Weave")
        assert _is_compound_term_fragment({"name": "quiet"}, index)[0] is True
        assert _is_compound_term_fragment({"name": "weave"}, index)[0] is True
        assert _is_compound_term_fragment({"name": "Quiet Weave"}, index)[0] is False

    def test_triangular_pattern_disruption_field_fragments_rejected(self):
        """Reproduces the 'Triangular Pattern Disruption Field' fragmentation bug."""
        index = self._make_index("Triangular Pattern Disruption Field")
        for word in ("triangular", "pattern", "disruption", "field"):
            is_frag, compound = _is_compound_term_fragment({"name": word}, index)
            assert is_frag is True, f"Expected '{word}' to be rejected as fragment"
            assert "Triangular Pattern Disruption Field" in compound

    def test_frost_precision_fragment_rejected(self):
        """Reproduces the 'Frost Precision' ability fragmentation bug."""
        index = self._make_index("Frost Precision")
        is_frag, _ = _is_compound_term_fragment({"name": "precision"}, index)
        assert is_frag is True

    def test_fragment_from_current_turn_entity(self):
        """Fragments of compound terms extracted in the same turn are also rejected."""
        current = [{"name": "Quiet Weave"}]
        index = _build_compound_word_index({}, current_entities=current)
        is_frag, _ = _is_compound_term_fragment({"name": "quiet"}, index)
        assert is_frag is True
