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
        assert index["frost"] == [{"compound_name": "Frost Precision", "type": "concept"}]
        assert index["precision"] == [{"compound_name": "Frost Precision", "type": "concept"}]

    def test_index_value_records_entity_type(self):
        """The index also records the type of the compound entity (#539)."""
        catalogs = {"factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}]}
        index = _build_compound_word_index(catalogs)
        assert index["ledger"] == [{"compound_name": "Red Ledger Syndicate", "type": "faction"}]

    def test_word_shared_by_two_differently_typed_compounds_indexes_both(self):
        """A word contributed by two different-type multi-word entities in the
        same run must retain BOTH entries, not just the first-processed one
        (#539 follow-up — the reviewer's 'ledger' cross-type collision repro)."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
            "items.json": [{"name": "Ledger of Debts", "type": "item"}],
        }
        index = _build_compound_word_index(catalogs)
        assert len(index["ledger"]) == 2
        assert {"compound_name": "Red Ledger Syndicate", "type": "faction"} in index["ledger"]
        assert {"compound_name": "Ledger of Debts", "type": "item"} in index["ledger"]

    def test_duplicate_compound_name_type_pair_not_duplicated(self):
        """The same compound_name/type pair contributed twice (e.g. once from
        the catalog and once from current-turn entities) is stored only once."""
        catalogs = {"factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}]}
        current = [{"name": "Red Ledger Syndicate", "type": "faction"}]
        index = _build_compound_word_index(catalogs, current_entities=current)
        assert index["ledger"] == [{"compound_name": "Red Ledger Syndicate", "type": "faction"}]

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

    # ------------------------------------------------------------------
    # Known-reference guard (#524): a bare-name callback to a catalogued
    # entity is spared ONLY when its existing_id RESOLVES to a real catalog
    # id.  An unvalidated / unresolvable existing_id must NOT bypass the #398
    # fragment filter (iteration-3 HIGH regression fix).
    # ------------------------------------------------------------------

    def test_existing_id_reference_not_rejected(self):
        """A bare given-name callback whose existing_id resolves is kept."""
        index = self._make_index("Mara Veylin")
        entity = {
            "name": "Mara",
            "type": "character",
            "existing_id": "char-mara-veylin",
        }
        is_frag, _ = _is_compound_term_fragment(
            entity, index, {"char-mara-veylin"}
        )
        assert is_frag is False

    def test_resolvable_existing_id_with_is_new_false_not_rejected(self):
        """Both reference markers set AND the id resolves -> kept."""
        index = self._make_index("Mara Veylin")
        entity = {
            "name": "Mara",
            "type": "character",
            "is_new": False,
            "existing_id": "char-mara-veylin",
        }
        is_frag, _ = _is_compound_term_fragment(
            entity, index, {"char-mara-veylin"}
        )
        assert is_frag is False

    def test_unresolvable_existing_id_fragment_rejected(self):
        """A model-supplied existing_id that does NOT resolve must NOT spare the
        fragment (#524 iteration-3 HIGH regression).  Catalog has
        'item-frost-precision'; the model claims a bogus 'item-precision' for a
        bare 'Precision' -> still rejected as a compound fragment."""
        index = self._make_index("Frost Precision")
        entity = {
            "name": "Precision",
            "type": "item",
            "is_new": False,
            "existing_id": "item-precision",
        }
        is_frag, compound = _is_compound_term_fragment(
            entity, index, {"item-frost-precision"}
        )
        assert is_frag is True
        assert compound == "Frost Precision"

    def test_is_new_false_without_existing_id_fragment_rejected(self):
        """is_new=False with NO existing_id is an unverifiable reference claim —
        fail closed and let the #398 filter run (#524 iteration-3)."""
        index = self._make_index("Mara Veylin")
        entity = {"name": "Veylin", "type": "character", "is_new": False}
        is_frag, compound = _is_compound_term_fragment(
            entity, index, {"char-mara-veylin"}
        )
        assert is_frag is True
        assert compound == "Mara Veylin"

    def test_existing_id_not_spared_without_known_ids(self):
        """Without a known-id set the guard cannot validate the reference, so it
        fails closed rather than trusting a model-supplied existing_id."""
        index = self._make_index("Mara Veylin")
        entity = {
            "name": "Mara",
            "type": "character",
            "existing_id": "char-mara-veylin",
        }
        is_frag, _ = _is_compound_term_fragment(entity, index)
        assert is_frag is True

    def test_new_fragment_still_rejected_when_not_a_reference(self):
        """The guard only spares resolvable references; genuine new fragments drop."""
        index = self._make_index("Mara Veylin")
        # is_new defaults to True (no existing_id) -> still a fragment candidate.
        is_frag, compound = _is_compound_term_fragment({"name": "Mara"}, index)
        assert is_frag is True
        assert compound == "Mara Veylin"

    def test_new_explicit_fragment_rejected(self):
        """Explicit is_new=True with no existing_id is still subject to rejection."""
        index = self._make_index("Mara Veylin")
        entity = {"name": "Veylin", "type": "character", "is_new": True}
        is_frag, _ = _is_compound_term_fragment(
            entity, index, {"char-mara-veylin"}
        )
        assert is_frag is True


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

    # ------------------------------------------------------------------
    # Type-aware fragment check (#539): a candidate is only a fragment of a
    # compound entity when both share the same `type`.  A single-word item
    # proposal is NOT a fragment of an unrelated multi-word faction/character/
    # location name that merely happens to share a word.
    # ------------------------------------------------------------------

    def test_cross_type_word_collision_not_rejected(self):
        """Reproduces the 'ledger' bug (#539): an item is not a fragment of an
        unrelated faction whose name shares a word."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
        }
        index = _build_compound_word_index(catalogs)
        entity = {"name": "ledger", "type": "item"}
        is_frag, compound = _is_compound_term_fragment(entity, index)
        assert is_frag is False
        assert compound == ""

    def test_same_type_fragment_still_rejected(self):
        """Same-type fragment rejection must still work after the type-aware fix."""
        catalogs = {
            "items.json": [{"name": "Ice Shard", "type": "item"}],
        }
        index = _build_compound_word_index(catalogs)
        entity = {"name": "Ice", "type": "item"}
        is_frag, compound = _is_compound_term_fragment(entity, index)
        assert is_frag is True
        assert compound == "Ice Shard"

    def test_untyped_candidate_falls_back_to_type_agnostic_rejection(self):
        """When the candidate carries no type, the check falls back to the
        original type-agnostic behavior (fail toward rejection) so untyped
        callers are unaffected."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
        }
        index = _build_compound_word_index(catalogs)
        is_frag, _ = _is_compound_term_fragment({"name": "ledger"}, index)
        assert is_frag is True

    def test_untyped_compound_entity_falls_back_to_type_agnostic_rejection(self):
        """When the compound entity in the catalog carries no type, the check
        falls back to the original type-agnostic behavior."""
        index = self._make_index("Ice Shard")  # no type on the compound entry
        is_frag, _ = _is_compound_term_fragment({"name": "Ice", "type": "item"}, index)
        assert is_frag is True

    # ------------------------------------------------------------------
    # Multi-type-per-word regression (#539 follow-up, reviewer repro): a word
    # shared by TWO different-type multi-word entities in the same run must
    # be checked against BOTH entries, not just whichever one won a
    # single-slot index first. Regardless of catalog/dict iteration order,
    # each candidate must be matched against its OWN type's compound entry.
    # ------------------------------------------------------------------

    def test_ledger_word_shared_by_faction_and_item_both_same_type_matches_reject(self):
        """Index built from BOTH a faction 'Red Ledger Syndicate' and an item
        'Ledger of Debts' (same run, so both contribute the word 'ledger').
        A candidate item named 'ledger' must be rejected as a fragment of
        'Ledger of Debts', and a candidate faction named 'ledger' must be
        rejected as a fragment of 'Red Ledger Syndicate' -- each matched
        against the entry of its OWN type, not an arbitrary single slot."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
            "items.json": [{"name": "Ledger of Debts", "type": "item"}],
        }
        index = _build_compound_word_index(catalogs)

        is_frag_item, compound_item = _is_compound_term_fragment(
            {"name": "ledger", "type": "item"}, index
        )
        assert is_frag_item is True
        assert compound_item == "Ledger of Debts"

        is_frag_faction, compound_faction = _is_compound_term_fragment(
            {"name": "ledger", "type": "faction"}, index
        )
        assert is_frag_faction is True
        assert compound_faction == "Red Ledger Syndicate"

    def test_ledger_word_shared_by_faction_and_item_reverse_catalog_order(self):
        """Same repro as above but with the catalogs dict populated in the
        opposite order, to prove the result does not depend on which
        multi-word entity is processed (indexed) first."""
        catalogs = {
            "items.json": [{"name": "Ledger of Debts", "type": "item"}],
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
        }
        index = _build_compound_word_index(catalogs)

        is_frag_item, compound_item = _is_compound_term_fragment(
            {"name": "ledger", "type": "item"}, index
        )
        assert is_frag_item is True
        assert compound_item == "Ledger of Debts"

        is_frag_faction, compound_faction = _is_compound_term_fragment(
            {"name": "ledger", "type": "faction"}, index
        )
        assert is_frag_faction is True
        assert compound_faction == "Red Ledger Syndicate"

    def test_ledger_word_shared_by_faction_and_item_current_entities_order(self):
        """Same repro again, but with the two multi-word entities split across
        the catalog dict and the current-turn entities list (a third possible
        iteration order), to further confirm order-independence."""
        catalogs = {
            "items.json": [{"name": "Ledger of Debts", "type": "item"}],
        }
        current = [{"name": "Red Ledger Syndicate", "type": "faction"}]
        index = _build_compound_word_index(catalogs, current_entities=current)

        is_frag_item, compound_item = _is_compound_term_fragment(
            {"name": "ledger", "type": "item"}, index
        )
        assert is_frag_item is True
        assert compound_item == "Ledger of Debts"

        is_frag_faction, compound_faction = _is_compound_term_fragment(
            {"name": "ledger", "type": "faction"}, index
        )
        assert is_frag_faction is True
        assert compound_faction == "Red Ledger Syndicate"

    def test_ledger_word_third_unrelated_type_still_not_rejected(self):
        """With both the faction and item entries indexed for 'ledger', a
        candidate of a THIRD, unrelated type is still not rejected -- neither
        entry type-matches it."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
            "items.json": [{"name": "Ledger of Debts", "type": "item"}],
        }
        index = _build_compound_word_index(catalogs)
        is_frag, compound = _is_compound_term_fragment(
            {"name": "ledger", "type": "location"}, index
        )
        assert is_frag is False
        assert compound == ""

    # ------------------------------------------------------------------
    # Case-insensitive type comparison (#539 non-blocking note): the
    # discovery-phase `type` field is raw LLM output not yet schema-validated
    # at the point this filter runs, so type comparison must be
    # case-insensitive.
    # ------------------------------------------------------------------

    def test_type_comparison_is_case_insensitive(self):
        """A same-type match must be recognized even when the candidate's and
        the indexed compound entity's `type` differ only in case."""
        catalogs = {
            "items.json": [{"name": "Ice Shard", "type": "Item"}],
        }
        index = _build_compound_word_index(catalogs)
        is_frag, compound = _is_compound_term_fragment(
            {"name": "Ice", "type": "ITEM"}, index
        )
        assert is_frag is True
        assert compound == "Ice Shard"

    def test_cross_type_case_insensitive_still_not_rejected(self):
        """Differing types remain a non-match after case normalization."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "FACTION"}],
        }
        index = _build_compound_word_index(catalogs)
        is_frag, compound = _is_compound_term_fragment(
            {"name": "ledger", "type": "Item"}, index
        )
        assert is_frag is False
        assert compound == ""

    # ------------------------------------------------------------------
    # Empty-string `type` (as opposed to a missing key) must hit the same
    # type-agnostic fallback path as a missing key (#539 minor coverage gap).
    # ------------------------------------------------------------------

    def test_empty_string_candidate_type_falls_back_to_type_agnostic_rejection(self):
        """A candidate with `type=""` (present but blank) must be treated the
        same as a candidate with no `type` key at all -- fallback to
        type-agnostic rejection, not treated as a real (non-matching) type."""
        catalogs = {
            "factions.json": [{"name": "Red Ledger Syndicate", "type": "faction"}],
        }
        index = _build_compound_word_index(catalogs)
        is_frag, compound = _is_compound_term_fragment(
            {"name": "ledger", "type": ""}, index
        )
        assert is_frag is True
        assert compound == "Red Ledger Syndicate"

    def test_empty_string_compound_entity_type_falls_back_to_type_agnostic_rejection(self):
        """A compound entity indexed with `type=""` (present but blank) must
        be treated the same as one indexed with no `type` at all."""
        catalogs = {
            "items.json": [{"name": "Ice Shard", "type": ""}],
        }
        index = _build_compound_word_index(catalogs)
        is_frag, compound = _is_compound_term_fragment(
            {"name": "Ice", "type": "item"}, index
        )
        assert is_frag is True
        assert compound == "Ice Shard"
