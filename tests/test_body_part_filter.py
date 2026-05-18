"""Tests for body part and abstract concept entity rejection (#338)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _is_misclassified_character,
    _is_misclassified_location,
    _STUB_REJECT_STEMS,
    _strip_any_prefix,
)


class TestBodyPartCharacterRejection:
    def test_shoulders_rejected_as_character(self):
        entity = {"name": "his shoulders", "type": "character"}
        assert _is_misclassified_character(entity)

    def test_two_rejected_as_character(self):
        entity = {"name": "Two", "type": "character"}
        assert _is_misclassified_character(entity)

    def test_four_rejected_as_character(self):
        entity = {"name": "Four", "type": "character"}
        assert _is_misclassified_character(entity)

    def test_ten_rejected_as_character(self):
        entity = {"name": "ten", "type": "character"}
        assert _is_misclassified_character(entity)

    def test_men_rejected_as_character(self):
        entity = {"name": "men", "type": "character"}
        assert _is_misclassified_character(entity)

    def test_valid_character_not_rejected(self):
        entity = {"name": "Kael", "type": "character"}
        assert not _is_misclassified_character(entity)

    def test_elder_not_rejected(self):
        """'Elder' is in the role allowlist and should not be rejected."""
        entity = {"name": "Elder", "type": "character"}
        assert not _is_misclassified_character(entity)


class TestBodyPartLocationRejection:
    def test_body_rejected_as_location(self):
        entity = {"name": "the body", "type": "location"}
        assert _is_misclassified_location(entity)

    def test_lips_rejected_as_location(self):
        entity = {"name": "his lips", "type": "location"}
        assert _is_misclassified_location(entity)

    def test_shoulders_rejected_as_location(self):
        entity = {"name": "shoulders", "type": "location"}
        assert _is_misclassified_location(entity)

    def test_ground_rejected_as_location(self):
        entity = {"name": "the ground", "type": "location"}
        assert _is_misclassified_location(entity)

    def test_edge_rejected_as_location(self):
        entity = {"name": "the edge", "type": "location"}
        assert _is_misclassified_location(entity)

    def test_reaction_rejected_as_location(self):
        entity = {"name": "the river's reaction", "type": "location"}
        assert _is_misclassified_location(entity)

    def test_valid_location_not_rejected(self):
        entity = {"name": "the longhouse", "type": "location"}
        assert not _is_misclassified_location(entity)

    def test_forest_not_rejected(self):
        entity = {"name": "the forest", "type": "location"}
        assert not _is_misclassified_location(entity)

    def test_non_location_type_ignored(self):
        entity = {"name": "the body", "type": "character"}
        assert not _is_misclassified_location(entity)


class TestStubRejectionStems:
    """Verify _STUB_REJECT_STEMS prevents body parts / abstract concepts re-entering via stubs."""

    def _stem(self, eid: str) -> str:
        return _strip_any_prefix(eid)

    def test_loc_body_stem_rejected(self):
        assert self._stem("loc-body") in _STUB_REJECT_STEMS

    def test_loc_edge_stem_rejected(self):
        assert self._stem("loc-edge") in _STUB_REJECT_STEMS

    def test_loc_shoulders_stem_rejected(self):
        assert self._stem("loc-shoulders") in _STUB_REJECT_STEMS

    def test_char_shoulders_stem_rejected(self):
        assert self._stem("char-shoulders") in _STUB_REJECT_STEMS

    def test_char_two_stem_rejected(self):
        assert self._stem("char-two") in _STUB_REJECT_STEMS

    def test_char_men_stem_rejected(self):
        assert self._stem("char-men") in _STUB_REJECT_STEMS

    def test_loc_reaction_stem_rejected(self):
        assert self._stem("loc-reaction") in _STUB_REJECT_STEMS

    def test_valid_location_stem_not_rejected(self):
        assert self._stem("loc-longhouse") not in _STUB_REJECT_STEMS

    def test_valid_character_stem_not_rejected(self):
        assert self._stem("char-kael") not in _STUB_REJECT_STEMS

