"""Tests for _is_plausible_character_name stub filter and its integration
with _post_batch_orphan_sweep and _name_mention_discovery (#384).

The filter uses wordfreq word-frequency data and morphological suffix patterns
rather than hardcoded domain word lists, so it generalises across campaigns.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _is_plausible_character_name,
    _create_orphan_stubs,
    _post_batch_orphan_sweep,
    _name_mention_discovery,
)


# ---------------------------------------------------------------------------
# _is_plausible_character_name unit tests
# ---------------------------------------------------------------------------

class TestIsPlausibleCharacterName:
    """Tests for _is_plausible_character_name()."""

    # Words that are clearly not character names ─────────────────────────────

    def test_rejects_abstract_noun_echo(self):
        assert _is_plausible_character_name("echo") is False

    def test_rejects_abstract_noun_pattern(self):
        assert _is_plausible_character_name("pattern") is False

    def test_rejects_abstract_noun_weave(self):
        assert _is_plausible_character_name("weave") is False

    def test_rejects_abstract_noun_song(self):
        assert _is_plausible_character_name("song") is False

    def test_rejects_abstract_noun_precision(self):
        assert _is_plausible_character_name("precision") is False

    def test_rejects_abstract_noun_disruption(self):
        assert _is_plausible_character_name("disruption") is False

    def test_rejects_adjective_quiet(self):
        assert _is_plausible_character_name("quiet") is False

    def test_rejects_adjective_broken(self):
        assert _is_plausible_character_name("broken") is False

    def test_rejects_adjective_triangular(self):
        assert _is_plausible_character_name("triangular") is False

    def test_rejects_noun_field(self):
        assert _is_plausible_character_name("field") is False

    def test_rejects_directional_southern(self):
        assert _is_plausible_character_name("southern") is False

    def test_rejects_directional_northern(self):
        assert _is_plausible_character_name("northern") is False

    def test_rejects_directional_eastern(self):
        assert _is_plausible_character_name("eastern") is False

    def test_rejects_directional_western(self):
        assert _is_plausible_character_name("western") is False

    def test_rejects_number_word_two(self):
        assert _is_plausible_character_name("two") is False

    def test_rejects_generic_stem_shadow(self):
        """Words in _GENERIC_STEMS are rejected."""
        assert _is_plausible_character_name("shadow") is False

    def test_rejects_generic_stem_figure(self):
        assert _is_plausible_character_name("figure") is False

    def test_rejects_group_noun_scouts(self):
        assert _is_plausible_character_name("scouts") is False

    # Case-insensitive handling ───────────────────────────────────────────────

    def test_rejects_mixed_case_abstract(self):
        assert _is_plausible_character_name("Echo") is False

    def test_rejects_uppercase_abstract(self):
        assert _is_plausible_character_name("PATTERN") is False

    # Words that ARE plausible character names ────────────────────────────────

    def test_accepts_proper_name_kael(self):
        assert _is_plausible_character_name("kael") is True

    def test_accepts_proper_name_maelis(self):
        assert _is_plausible_character_name("maelis") is True

    def test_accepts_proper_name_thorne(self):
        assert _is_plausible_character_name("thorne") is True

    def test_accepts_proper_name_lyra(self):
        assert _is_plausible_character_name("lyra") is True

    def test_accepts_multi_word_name(self):
        """Multi-word stems (e.g. from hyphenated IDs) pass through."""
        assert _is_plausible_character_name("iron wolf") is True

    def test_accepts_proper_name_laurence(self):
        """Capitalized proper name with -ence suffix bypasses suffix filter."""
        assert _is_plausible_character_name("Laurence") is True

    def test_accepts_proper_name_constance(self):
        """Capitalized proper name with -ance suffix bypasses suffix filter."""
        assert _is_plausible_character_name("Constance") is True

    def test_accepts_proper_name_clement(self):
        """Capitalized proper name with -ment suffix bypasses suffix filter."""
        assert _is_plausible_character_name("Clement") is True



# ---------------------------------------------------------------------------
# Integration: _post_batch_orphan_sweep rejects abstract character stubs
# ---------------------------------------------------------------------------

def _make_events(entity_ids: list[str], count: int = 4) -> list[dict]:
    """Return a minimal events list where each id appears *count* times."""
    events = []
    for i in range(count):
        events.append({
            "id": f"event-{i}",
            "turn_id": f"turn-{i+1:03d}",
            "related_entities": entity_ids,
        })
    return events


class TestPostBatchOrphanSweepFilter:
    """_post_batch_orphan_sweep should not create stubs for abstract names."""

    def test_rejects_char_echo(self):
        catalogs: dict = {}
        events = _make_events(["char-echo"])
        _post_batch_orphan_sweep(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-echo" for e in chars)

    def test_rejects_char_pattern(self):
        catalogs: dict = {}
        events = _make_events(["char-pattern"])
        _post_batch_orphan_sweep(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-pattern" for e in chars)

    def test_rejects_char_southern(self):
        catalogs: dict = {}
        events = _make_events(["char-southern"])
        _post_batch_orphan_sweep(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-southern" for e in chars)

    def test_rejects_char_scouts(self):
        catalogs: dict = {}
        events = _make_events(["char-scouts"])
        _post_batch_orphan_sweep(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-scouts" for e in chars)

    def test_accepts_valid_character_id(self):
        """A legitimate character ID like char-kael should still be stubbed."""
        catalogs: dict = {}
        events = _make_events(["char-kael"])
        _post_batch_orphan_sweep(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert any(e["id"] == "char-kael" for e in chars)

    def test_non_character_abstract_id_not_filtered(self):
        """Abstract ID with non-character prefix (loc-) is unaffected by char filter."""
        catalogs: dict = {}
        events = _make_events(["loc-southern"], count=2)
        _post_batch_orphan_sweep(catalogs, events)
        locs = catalogs.get("locations.json", [])
        assert any(e["id"] == "loc-southern" for e in locs)


# ---------------------------------------------------------------------------
# Integration: _name_mention_discovery rejects abstract capitalized words
# ---------------------------------------------------------------------------

def _make_desc_events(words: list[str], count: int = 3) -> list[dict]:
    """Return events whose descriptions each contain the given capitalized words."""
    events = []
    for i in range(count):
        desc = " ".join(words) + f" did something in event {i}."
        events.append({
            "id": f"event-{i}",
            "turn_id": f"turn-{i+1:03d}",
            "description": desc,
            "related_entities": [],
        })
    return events


class TestNameMentionDiscoveryFilter:
    """_name_mention_discovery should not create stubs for abstract words."""

    def test_rejects_capitalized_echo(self):
        catalogs: dict = {}
        events = _make_desc_events(["Echo"])
        _name_mention_discovery(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-echo" for e in chars)

    def test_rejects_capitalized_pattern(self):
        catalogs: dict = {}
        events = _make_desc_events(["Pattern"])
        _name_mention_discovery(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-pattern" for e in chars)

    def test_rejects_capitalized_southern(self):
        catalogs: dict = {}
        events = _make_desc_events(["Southern"])
        _name_mention_discovery(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-southern" for e in chars)

    def test_rejects_generic_stem_shadow(self):
        catalogs: dict = {}
        events = _make_desc_events(["Shadow"])
        _name_mention_discovery(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-shadow" for e in chars)

    def test_accepts_proper_character_name(self):
        """A capitalized proper name that isn't blocked should create a stub."""
        catalogs: dict = {}
        events = _make_desc_events(["Maelis"])
        _name_mention_discovery(catalogs, events)
        chars = catalogs.get("characters.json", [])
        assert any(e["id"] == "char-maelis" for e in chars)


# ---------------------------------------------------------------------------
# Integration: _create_orphan_stubs rejects abstract character stubs
# ---------------------------------------------------------------------------

class TestCreateOrphanStubsFilter:
    """_create_orphan_stubs should not create stubs for implausible char names."""

    def test_rejects_char_disruption_stub(self):
        """Abstract noun 'disruption' should not get a character stub."""
        catalogs: dict = {}
        events = [{
            "id": "event-1",
            "turn_id": "turn-001",
            "related_entities": ["char-disruption"],
        }]
        _create_orphan_stubs(catalogs, events, "turn-001")
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-disruption" for e in chars)

    def test_rejects_char_precision_stub(self):
        """Abstract noun 'precision' should not get a character stub."""
        catalogs: dict = {}
        events = [{
            "id": "event-1",
            "turn_id": "turn-001",
            "related_entities": ["char-precision"],
        }]
        _create_orphan_stubs(catalogs, events, "turn-001")
        chars = catalogs.get("characters.json", [])
        assert not any(e["id"] == "char-precision" for e in chars)

    def test_accepts_valid_character_stub(self):
        """A legitimate character ID like char-fenouille should be stubbed."""
        catalogs: dict = {}
        events = [{
            "id": "event-1",
            "turn_id": "turn-001",
            "related_entities": ["char-fenouille"],
        }]
        _create_orphan_stubs(catalogs, events, "turn-001")
        chars = catalogs.get("characters.json", [])
        assert any(e["id"] == "char-fenouille" for e in chars)

    def test_non_character_type_unaffected(self):
        """Location stubs are not subject to the character name filter."""
        catalogs: dict = {}
        events = [{
            "id": "event-1",
            "turn_id": "turn-001",
            "related_entities": ["loc-disruption"],
        }]
        _create_orphan_stubs(catalogs, events, "turn-001")
        locs = catalogs.get("locations.json", [])
        assert any(e["id"] == "loc-disruption" for e in locs)
