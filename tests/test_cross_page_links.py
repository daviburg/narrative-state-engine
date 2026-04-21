"""Tests for cross-page entity links in synthesis wiki pages (#139)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from narrative_synthesis import (
    _resolve_link,
    _safe_replace_first,
    _linkify_prose,
    _build_event_timeline,
    _build_relationship_table,
    assemble_character_page,
    assemble_location_page,
    assemble_faction_page,
    assemble_item_page,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

NAME_INDEX = {
    "char-kael": ("Kael", "../characters/char-kael.md"),
    "char-player": ("Player Character", "../characters/char-player.md"),
    "char-elder-lyra": ("Elder Lyra", "../characters/char-elder-lyra.md"),
    "char-lyra": ("Lyra", "../characters/char-lyra.md"),
    "loc-haven": ("Haven", "../locations/loc-haven.md"),
    "faction-guild": ("The Guild", "../factions/faction-guild.md"),
    "char-al": ("Al", "../characters/char-al.md"),  # short name, < 4 chars
}

CHARACTERS_DIR = "/fake/catalogs/characters"
LOCATIONS_DIR = "/fake/catalogs/locations"
FACTIONS_DIR = "/fake/catalogs/factions"
ITEMS_DIR = "/fake/catalogs/items"


def _evt(desc, related=None, turns=None, etype="decision"):
    return {
        "id": "evt-1",
        "source_turns": turns or ["turn-001"],
        "type": etype,
        "description": desc,
        "related_entities": related or [],
    }


# ---------------------------------------------------------------------------
# _resolve_link
# ---------------------------------------------------------------------------

class TestResolveLink:
    def test_cross_type_link(self):
        link = _resolve_link("loc-haven", NAME_INDEX, CHARACTERS_DIR)
        assert link == "[Haven](../locations/loc-haven.md)"

    def test_same_type_link(self):
        link = _resolve_link("char-kael", NAME_INDEX, CHARACTERS_DIR)
        assert link == "[Kael](char-kael.md)"

    def test_unknown_id_returns_none(self):
        assert _resolve_link("char-unknown", NAME_INDEX, CHARACTERS_DIR) is None

    def test_none_name_index(self):
        assert _resolve_link("char-kael", None, CHARACTERS_DIR) is None

    def test_none_source_dir(self):
        assert _resolve_link("char-kael", NAME_INDEX, None) is None


# ---------------------------------------------------------------------------
# _safe_replace_first
# ---------------------------------------------------------------------------

class TestSafeReplaceFirst:
    def test_replaces_first_occurrence(self):
        result = _safe_replace_first("Kael went to Kael", "Kael", "[Kael](k.md)")
        assert result == "[Kael](k.md) went to Kael"

    def test_skips_inside_existing_link(self):
        text = "[Elder Lyra](elder.md) met Kael"
        result = _safe_replace_first(text, "Elder Lyra", "[Elder Lyra](x.md)")
        # Already inside a link bracket — should skip and not find another
        assert result == text

    def test_no_match_returns_unchanged(self):
        result = _safe_replace_first("no match here", "Kael", "[Kael](k.md)")
        assert result == "no match here"

    def test_replaces_outside_link(self):
        text = "[Other](o.md) and Kael walked"
        result = _safe_replace_first(text, "Kael", "[Kael](k.md)")
        assert "[Kael](k.md)" in result


# ---------------------------------------------------------------------------
# _linkify_prose
# ---------------------------------------------------------------------------

class TestLinkifyProse:
    def test_links_first_mention(self):
        prose = "Kael arrived at Haven. Kael explored."
        linked = set()
        result = _linkify_prose(prose, NAME_INDEX, CHARACTERS_DIR,
                                "char-player", linked)
        assert "[Kael](char-kael.md)" in result
        # Second mention should NOT be linked
        assert result.count("[Kael]") == 1
        assert "char-kael" in linked

    def test_no_self_links(self):
        prose = "Player Character went to Haven."
        linked = set()
        result = _linkify_prose(prose, NAME_INDEX, CHARACTERS_DIR,
                                "char-player", linked)
        assert "[Player Character]" not in result

    def test_short_names_skipped(self):
        prose = "Al went to Haven."
        linked = set()
        result = _linkify_prose(prose, NAME_INDEX, CHARACTERS_DIR,
                                "char-player", linked)
        assert "[Al]" not in result
        assert "char-al" not in linked

    def test_longest_name_first(self):
        prose = "Elder Lyra spoke to Lyra."
        linked = set()
        result = _linkify_prose(prose, NAME_INDEX, CHARACTERS_DIR,
                                "char-player", linked)
        assert "[Elder Lyra]" in result
        # "Lyra" should still be linkable separately (different entity)
        assert "[Lyra]" in result

    def test_already_linked_skipped(self):
        prose = "Kael arrived."
        linked = {"char-kael"}
        result = _linkify_prose(prose, NAME_INDEX, CHARACTERS_DIR,
                                "char-player", linked)
        assert "[Kael]" not in result

    def test_none_name_index_passthrough(self):
        prose = "Kael arrived."
        result = _linkify_prose(prose, None, CHARACTERS_DIR,
                                "char-player", set())
        assert result == prose

    def test_cross_type_relative_path(self):
        prose = "Haven is a settlement."
        linked = set()
        result = _linkify_prose(prose, NAME_INDEX, CHARACTERS_DIR,
                                "char-player", linked)
        assert "[Haven](../locations/loc-haven.md)" in result


# ---------------------------------------------------------------------------
# _build_relationship_table with links
# ---------------------------------------------------------------------------

class TestRelationshipTableLinks:
    def test_has_links(self):
        arcs = {"arcs": {
            "char-kael": {"current_relationship": "ally", "interaction_count": 5},
        }}
        table = _build_relationship_table(
            arcs, name_index=NAME_INDEX,
            source_type_dir=CHARACTERS_DIR, self_id="char-player")
        assert "[Kael](char-kael.md)" in table
        assert "char-kael)" not in table.split("[Kael]")[0]  # old format gone

    def test_no_name_index_fallback(self):
        arcs = {"arcs": {
            "char-kael": {"current_relationship": "ally", "interaction_count": 5},
        }}
        table = _build_relationship_table(arcs)
        assert "Kael (char-kael)" in table


# ---------------------------------------------------------------------------
# _build_event_timeline with links
# ---------------------------------------------------------------------------

class TestEventTimelineLinks:
    def test_links_related_entities(self):
        events = [_evt("Kael arrived at Haven",
                       related=["char-kael", "loc-haven"])]
        table = _build_event_timeline(
            events, name_index=NAME_INDEX,
            source_type_dir=CHARACTERS_DIR, self_id="char-player")
        assert "[Kael]" in table

    def test_no_self_links_in_timeline(self):
        events = [_evt("Player Character spoke",
                       related=["char-player"])]
        table = _build_event_timeline(
            events, name_index=NAME_INDEX,
            source_type_dir=CHARACTERS_DIR, self_id="char-player")
        assert "[Player Character]" not in table


# ---------------------------------------------------------------------------
# Full page assembly with links
# ---------------------------------------------------------------------------

class TestCharacterPageLinks:
    def test_biography_prose_linked(self):
        page = assemble_character_page(
            "char-player", "Player Character", "",
            [("Phase 1", "Kael helped the Player Character at Haven.")],
            None, None, None, [],
            name_index=NAME_INDEX, source_type_dir=CHARACTERS_DIR)
        assert "[Kael](char-kael.md)" in page

    def test_no_self_links(self):
        page = assemble_character_page(
            "char-player", "Player Character", "",
            [("Phase 1", "Player Character walked.")],
            None, None, None, [],
            name_index=NAME_INDEX, source_type_dir=CHARACTERS_DIR)
        assert "[Player Character]" not in page

    def test_no_duplicate_links(self):
        page = assemble_character_page(
            "char-player", "Player Character", "",
            [("Phase 1", "Kael spoke."), ("Phase 2", "Kael returned.")],
            None, None, None, [],
            name_index=NAME_INDEX, source_type_dir=CHARACTERS_DIR)
        assert page.count("[Kael]") == 1

    def test_relationship_table_linked(self):
        arcs = {"arcs": {
            "loc-haven": {"current_relationship": "home", "interaction_count": 3},
        }}
        page = assemble_character_page(
            "char-player", "Player Character", "", [],
            None, None, arcs, [],
            name_index=NAME_INDEX, source_type_dir=CHARACTERS_DIR)
        assert "[Haven](../locations/loc-haven.md)" in page


class TestLocationPageLinks:
    def test_connected_entities_linked(self):
        catalog = {
            "relationships": [
                {"target_id": "char-kael", "current_relationship": "resident"},
            ],
        }
        page = assemble_location_page(
            "loc-haven", "Haven", "A settlement.",
            catalog, None, [],
            name_index=NAME_INDEX, source_type_dir=LOCATIONS_DIR)
        assert "[Kael](../characters/char-kael.md)" in page

    def test_significance_prose_linked(self):
        page = assemble_location_page(
            "loc-haven", "Haven", "Kael founded Haven.",
            None, None, [],
            name_index=NAME_INDEX, source_type_dir=LOCATIONS_DIR)
        assert "[Kael](../characters/char-kael.md)" in page


class TestFactionPageLinks:
    def test_member_list_linked(self):
        events = [_evt("Kael joined", related=["char-kael"])]
        page = assemble_faction_page(
            "faction-guild", "The Guild", "",
            None, None, events,
            name_index=NAME_INDEX, source_type_dir=FACTIONS_DIR)
        assert "[Kael](../characters/char-kael.md)" in page

    def test_history_prose_linked(self):
        page = assemble_faction_page(
            "faction-guild", "The Guild", "Kael led the founding.",
            None, None, [],
            name_index=NAME_INDEX, source_type_dir=FACTIONS_DIR)
        assert "[Kael](../characters/char-kael.md)" in page


class TestItemPageLinks:
    def test_significance_prose_linked(self):
        page = assemble_item_page(
            "item-sword", "Magic Sword", "Kael wielded the sword.",
            None, None, [],
            name_index=NAME_INDEX, source_type_dir=ITEMS_DIR)
        assert "[Kael](../characters/char-kael.md)" in page


# ---------------------------------------------------------------------------
# Template path still works (no name_index = no links, no crash)
# ---------------------------------------------------------------------------

class TestNoLinksFallback:
    def test_character_page_no_index(self):
        page = assemble_character_page(
            "char-player", "Player Character", "",
            [("Phase 1", "Kael helped.")],
            None, None, None, [])
        assert "# Player Character" in page
        assert "[Kael]" not in page  # no linkification without name_index

    def test_location_page_no_index(self):
        page = assemble_location_page(
            "loc-haven", "Haven", "Significance.", None, None, [])
        assert "# Haven" in page

    def test_faction_page_no_index(self):
        page = assemble_faction_page(
            "faction-guild", "The Guild", "History.", None, None, [])
        assert "# The Guild" in page

    def test_item_page_no_index(self):
        page = assemble_item_page(
            "item-sword", "Magic Sword", "Important.", None, None, [])
        assert "# Magic Sword" in page


# ---------------------------------------------------------------------------
# Cross-section deduplication
# ---------------------------------------------------------------------------

class TestCrossSectionDedup:
    def test_biography_links_prevent_relationship_relink(self):
        """Entity linked in biography should NOT be linked again in relationships."""
        arcs = {"arcs": {
            "char-kael": {"current_relationship": "ally", "interaction_count": 5},
        }}
        page = assemble_character_page(
            "char-player", "Player Character", "",
            [("Phase 1", "Kael helped build the settlement.")],
            None, None, arcs, [],
            name_index=NAME_INDEX, source_type_dir=CHARACTERS_DIR)
        # Only one link to Kael across the whole page
        assert page.count("[Kael]") == 1

    def test_biography_links_prevent_timeline_relink(self):
        """Entity linked in biography should NOT be linked again in timeline."""
        events = [_evt("Kael arrived at Haven",
                       related=["char-kael", "loc-haven"])]
        page = assemble_character_page(
            "char-player", "Player Character", "",
            [("Phase 1", "Kael helped build the settlement.")],
            None, None, None, events,
            name_index=NAME_INDEX, source_type_dir=CHARACTERS_DIR)
        assert page.count("[Kael]") == 1

    def test_faction_history_links_prevent_member_relink(self):
        """Entity linked in faction history should NOT relink in members list."""
        events = [_evt("Kael joined", related=["char-kael"])]
        page = assemble_faction_page(
            "faction-guild", "The Guild", "Kael founded the guild.",
            None, None, events,
            name_index=NAME_INDEX, source_type_dir=FACTIONS_DIR)
        assert page.count("[Kael]") == 1

    def test_location_significance_prevents_connected_relink(self):
        """Entity linked in significance should NOT relink in connected entities."""
        catalog = {
            "relationships": [
                {"target_id": "char-kael", "current_relationship": "resident"},
            ],
        }
        page = assemble_location_page(
            "loc-haven", "Haven", "Kael founded Haven.",
            catalog, None, [],
            name_index=NAME_INDEX, source_type_dir=LOCATIONS_DIR)
        assert page.count("[Kael]") == 1


# ---------------------------------------------------------------------------
# Alias ID resolution
# ---------------------------------------------------------------------------

class TestAliasIDResolution:
    def test_event_timeline_resolves_alias(self):
        """Alias IDs (e.g. char-Kael) should be canonicalized and still link."""
        events = [_evt("Kael arrived", related=["char-Kael"])]
        table = _build_event_timeline(
            events, name_index=NAME_INDEX,
            source_type_dir=CHARACTERS_DIR, self_id="char-player")
        assert "[Kael]" in table

    def test_connected_entities_resolves_alias(self):
        """Alias target_id in relationships should resolve and link."""
        from narrative_synthesis import assemble_location_page
        catalog = {
            "relationships": [
                {"target_id": "char-Kael", "current_relationship": "visitor"},
            ],
        }
        page = assemble_location_page(
            "loc-haven", "Haven", "A settlement.",
            catalog, None, [],
            name_index=NAME_INDEX, source_type_dir=LOCATIONS_DIR)
        assert "[Kael]" in page
