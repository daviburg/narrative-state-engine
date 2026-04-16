"""Tests for entity ID normalization (normalize_entity_id)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import normalize_entity_id


class TestCaseNormalization:
    def test_lowercase_match(self):
        assert normalize_entity_id("char-Kael", {"char-kael"}) == "char-kael"

    def test_uppercase_player(self):
        assert normalize_entity_id("CHAR-PLAYER", {"char-player"}) == "char-player"

    def test_mixed_case(self):
        assert normalize_entity_id("char-Tala", {"char-tala"}) == "char-tala"


class TestPrefixCorrection:
    def test_faction_to_char(self):
        """faction-warrior-chief-gorok should match char-gorok via token overlap."""
        result = normalize_entity_id("faction-warrior-chief-gorok", {"char-gorok"})
        assert result == "char-gorok"

    def test_entity_to_char(self):
        assert normalize_entity_id("entity-healer", {"char-healer"}) == "char-healer"

    def test_npc_prefix(self):
        """npc-ananya should match char-ananya via stem match."""
        result = normalize_entity_id("npc-ananya", {"char-ananya"})
        assert result == "char-ananya"


class TestFuzzyMatch:
    def test_typo_anxa_to_anya(self):
        """char-anxa (edit distance 1 from anya) should match char-anya."""
        result = normalize_entity_id("char-anxa", {"char-anya"})
        assert result == "char-anya"

    def test_no_false_positive_distant_names(self):
        """Names with edit distance > 2 should not match."""
        result = normalize_entity_id("char-newname", {"char-kael", "char-anya"})
        assert result == "char-newname"


class TestPassthrough:
    def test_no_known_ids(self):
        assert normalize_entity_id("char-newname", set()) == "char-newname"

    def test_already_canonical(self):
        assert normalize_entity_id("char-kael", {"char-kael"}) == "char-kael"

    def test_empty_input(self):
        assert normalize_entity_id("", {"char-kael"}) == ""


class TestTokenOverlap:
    def test_chief_thorne_to_thorne(self):
        """char-chief-thorne should match char-thorne via token overlap."""
        result = normalize_entity_id("char-chief-thorne", {"char-thorne"})
        assert result == "char-thorne"

    def test_elder_lyra_to_lyra(self):
        """faction-elder-lyra should match char-lyra via token overlap."""
        result = normalize_entity_id("faction-elder-lyra", {"char-lyra"})
        assert result == "char-lyra"

    def test_maelis_full_title(self):
        """char-maelis-of-the-swift-arrows should match char-maelis via token overlap."""
        result = normalize_entity_id("char-maelis-of-the-swift-arrows", {"char-maelis"})
        assert result == "char-maelis"
