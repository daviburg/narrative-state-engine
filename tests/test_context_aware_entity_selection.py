"""Tests for context-aware entity selection (#233)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import (
    format_known_entities_bounded,
    _estimate_tokens,
    _find_mentioned_entities,
    _entity_names,
    _get_entity_location,
    _select_context_aware_entities,
)


def _make_entity(eid, name, etype="character", identity="", aliases=None,
                 last_updated_turn=None, location=None, relationships=None):
    """Build a minimal entity dict for testing."""
    e = {"id": eid, "name": name, "type": etype}
    if identity:
        e["identity"] = identity
    if aliases:
        e["stable_attributes"] = {"aliases": {"value": aliases}}
    if last_updated_turn:
        e["last_updated_turn"] = last_updated_turn
    if location:
        e["volatile_state"] = {"location": location}
    if relationships:
        e["relationships"] = relationships
    return e


def _make_catalogs(*entity_lists):
    """Wrap entity lists into a catalogs dict with appropriate keys."""
    result = {}
    keys = ["characters.json", "locations.json", "factions.json", "items.json"]
    for i, entities in enumerate(entity_lists):
        result[keys[i]] = entities
    return result


# ---------------------------------------------------------------------------
# _entity_names
# ---------------------------------------------------------------------------

class TestEntityNames:
    def test_name_only(self):
        e = _make_entity("char-a", "Alice")
        assert _entity_names(e) == ["Alice"]

    def test_with_aliases_list(self):
        e = _make_entity("char-a", "Alice", aliases=["Ali", "Al"])
        names = _entity_names(e)
        assert "Alice" in names
        assert "Ali" in names
        assert "Al" in names

    def test_with_alias_string(self):
        e = {"id": "char-a", "name": "Alice", "type": "character",
             "stable_attributes": {"aliases": {"value": "The Brave"}}}
        names = _entity_names(e)
        assert "Alice" in names
        assert "The Brave" in names


# ---------------------------------------------------------------------------
# _find_mentioned_entities
# ---------------------------------------------------------------------------

class TestFindMentionedEntities:
    def test_case_insensitive_match(self):
        entities = [_make_entity("char-a", "Alice")]
        result = _find_mentioned_entities(entities, "alice walked in")
        assert "char-a" in result

    def test_no_match(self):
        entities = [_make_entity("char-a", "Alice")]
        result = _find_mentioned_entities(entities, "Bob walked in")
        assert len(result) == 0

    def test_alias_match(self):
        entities = [_make_entity("char-a", "Alice", aliases=["Shortbow"])]
        result = _find_mentioned_entities(entities, "Shortbow fired an arrow")
        assert "char-a" in result

    def test_short_name_skipped(self):
        """Names shorter than 3 chars are skipped to avoid false positives."""
        entities = [_make_entity("char-a", "Al")]
        result = _find_mentioned_entities(entities, "Al walked in")
        assert len(result) == 0

    def test_word_boundary_prevents_substring_match(self):
        """Word-boundary matching prevents 'elder' matching inside 'beelder'."""
        entities = [_make_entity("char-e", "Elder")]
        result = _find_mentioned_entities(entities, "beelder walked in")
        assert len(result) == 0

    def test_word_boundary_prevents_compound_match(self):
        """Word-boundary matching prevents 'the camp' inside 'campfire'."""
        entities = [_make_entity("loc-c", "The Camp", etype="location")]
        result = _find_mentioned_entities(entities, "They sat by the campfire")
        assert len(result) == 0

    def test_word_boundary_allows_exact_match(self):
        """Word-boundary matching still matches exact word occurrences."""
        entities = [_make_entity("char-e", "Elder")]
        result = _find_mentioned_entities(entities, "The Elder spoke")
        assert "char-e" in result

    def test_empty_turn_text(self):
        entities = [_make_entity("char-a", "Alice")]
        result = _find_mentioned_entities(entities, "")
        assert len(result) == 0

    def test_multiple_matches(self):
        entities = [
            _make_entity("char-a", "Alice"),
            _make_entity("char-b", "Bob"),
        ]
        result = _find_mentioned_entities(entities, "Alice met Bob")
        assert "char-a" in result
        assert "char-b" in result

    def test_location_match(self):
        entities = [_make_entity("loc-t", "Thornhaven", etype="location")]
        result = _find_mentioned_entities(entities, "They arrived at Thornhaven")
        assert "loc-t" in result


# ---------------------------------------------------------------------------
# _get_entity_location
# ---------------------------------------------------------------------------

class TestGetEntityLocation:
    def test_has_location(self):
        e = _make_entity("char-a", "Alice", location="Thornhaven")
        assert _get_entity_location(e) == "Thornhaven"

    def test_no_volatile_state(self):
        e = _make_entity("char-a", "Alice")
        assert _get_entity_location(e) is None

    def test_empty_location(self):
        e = {"id": "char-a", "name": "Alice", "type": "character",
             "volatile_state": {"location": ""}}
        assert _get_entity_location(e) is None


# ---------------------------------------------------------------------------
# _select_context_aware_entities — ordering
# ---------------------------------------------------------------------------

class TestSelectContextAwareEntities:
    def test_mentioned_first(self):
        """Mentioned entities come before unmentioned ones."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-010"),
            _make_entity("char-b", "Bob", last_updated_turn="turn-100"),
        ]
        result, _ = _select_context_aware_entities(
            entities, "Alice walked in", current_turn=100, recency_window=10)
        assert result[0]["id"] == "char-a"

    def test_colocated_before_backfill(self):
        """Co-located entities come before recency backfill."""
        entities = [
            _make_entity("loc-t", "Thornhaven", etype="location",
                         last_updated_turn="turn-050"),
            _make_entity("char-a", "Alice", location="Thornhaven",
                         last_updated_turn="turn-020"),
            _make_entity("char-b", "Bob", last_updated_turn="turn-090"),
        ]
        result, _ = _select_context_aware_entities(
            entities, "They arrived at Thornhaven",
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        # Thornhaven is mentioned (tier 1), Alice is co-located (tier 2),
        # Bob is backfill (tier 4)
        assert ids.index("loc-t") < ids.index("char-a")
        assert ids.index("char-a") < ids.index("char-b")

    def test_one_hop_before_backfill(self):
        """Relationship targets come before recency backfill."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100",
                         relationships=[{"target_id": "char-b",
                                         "current_relationship": "mentor",
                                         "type": "mentorship",
                                         "first_seen_turn": "turn-010"}]),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),
            _make_entity("char-c", "Charlie", last_updated_turn="turn-090"),
        ]
        result, _ = _select_context_aware_entities(
            entities, "Alice spoke up",
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        # Alice mentioned (tier 1), Bob one-hop (tier 3), Charlie backfill (tier 4)
        assert ids.index("char-a") < ids.index("char-b")
        assert ids.index("char-b") < ids.index("char-c")

    def test_no_turn_text_falls_back_to_recency(self):
        """Without turn text, ordering is purely by recency."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-010"),
            _make_entity("char-b", "Bob", last_updated_turn="turn-090"),
        ]
        result, _ = _select_context_aware_entities(
            entities, None, current_turn=100, recency_window=10)
        # All are backfill, sorted by recency descending
        assert result[0]["id"] == "char-b"
        assert result[1]["id"] == "char-a"

    def test_entity_appears_only_once(self):
        """Each entity appears exactly once even if it matches multiple tiers."""
        entities = [
            _make_entity("char-a", "Alice", location="Thornhaven",
                         last_updated_turn="turn-100",
                         relationships=[{"target_id": "char-b",
                                         "current_relationship": "ally",
                                         "type": "social",
                                         "first_seen_turn": "turn-010"}]),
            _make_entity("char-b", "Bob", location="Thornhaven",
                         last_updated_turn="turn-010"),
            _make_entity("loc-t", "Thornhaven", etype="location",
                         last_updated_turn="turn-050"),
        ]
        result, _ = _select_context_aware_entities(
            entities, "Alice entered Thornhaven",
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        assert len(ids) == len(set(ids)), "Entities should not be duplicated"

    def test_location_pulls_in_colocated_entities(self):
        """When a location is mentioned, entities at that location are co-located."""
        innkeeper = _make_entity("char-inn", "Innkeeper",
                                 location="Thornhaven",
                                 last_updated_turn="turn-020")
        blacksmith = _make_entity("char-smith", "Blacksmith",
                                  location="Thornhaven",
                                  last_updated_turn="turn-015")
        loc = _make_entity("loc-t", "Thornhaven", etype="location",
                           last_updated_turn="turn-050")
        far_away = _make_entity("char-far", "Faraway",
                                location="Distant City",
                                last_updated_turn="turn-095")
        entities = [innkeeper, blacksmith, loc, far_away]
        result, _ = _select_context_aware_entities(
            entities, "They arrived at Thornhaven",
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        # Thornhaven mentioned (tier 1)
        # Innkeeper + Blacksmith co-located (tier 2)
        # Faraway backfill (tier 4)
        assert ids.index("loc-t") < ids.index("char-inn")
        assert ids.index("loc-t") < ids.index("char-smith")
        assert ids.index("char-inn") < ids.index("char-far")
        assert ids.index("char-smith") < ids.index("char-far")

    def test_dormant_relationship_excluded_from_one_hop(self):
        """Dormant relationships should not pull targets into one-hop tier."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100",
                         relationships=[{"target_id": "char-b",
                                         "current_relationship": "former ally",
                                         "type": "social",
                                         "status": "dormant",
                                         "first_seen_turn": "turn-010"}]),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),
            _make_entity("char-c", "Charlie", last_updated_turn="turn-090"),
        ]
        result, priority_ids = _select_context_aware_entities(
            entities, "Alice spoke up",
            current_turn=100, recency_window=10)
        # Bob should NOT be in priority (one-hop) because relationship is dormant
        assert "char-b" not in priority_ids
        ids = [e["id"] for e in result]
        # Bob is stale (turn-010 vs current 100, >50 turns) and not priority,
        # so it's excluded from backfill by staleness filtering
        assert "char-b" not in ids
        # Charlie (turn-090) is within staleness threshold, kept in backfill
        assert "char-c" in ids

    def test_resolved_relationship_excluded_from_one_hop(self):
        """Resolved relationships should not pull targets into one-hop tier."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100",
                         relationships=[{"target_id": "char-b",
                                         "current_relationship": "defeated",
                                         "type": "adversarial",
                                         "status": "resolved",
                                         "first_seen_turn": "turn-010"}]),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),
        ]
        _, priority_ids = _select_context_aware_entities(
            entities, "Alice spoke up",
            current_turn=100, recency_window=10)
        assert "char-b" not in priority_ids

    def test_active_relationship_included_in_one_hop(self):
        """Active relationships should pull targets into one-hop tier."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100",
                         relationships=[{"target_id": "char-b",
                                         "current_relationship": "ally",
                                         "type": "social",
                                         "status": "active",
                                         "first_seen_turn": "turn-010"}]),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),
        ]
        _, priority_ids = _select_context_aware_entities(
            entities, "Alice spoke up",
            current_turn=100, recency_window=10)
        assert "char-b" in priority_ids

    def test_location_id_matching_for_colocation(self):
        """Entities with volatile_state.location as a loc-* ID are co-located."""
        loc = _make_entity("loc-forge", "The Forge", etype="location",
                           last_updated_turn="turn-050")
        npc = _make_entity("char-smith", "Blacksmith",
                           location="loc-forge",
                           last_updated_turn="turn-020")
        entities = [loc, npc]
        result, priority_ids = _select_context_aware_entities(
            entities, "They entered The Forge",
            current_turn=100, recency_window=10)
        # Blacksmith's location is "loc-forge" (an ID), The Forge is mentioned
        assert "char-smith" in priority_ids
        ids = [e["id"] for e in result]
        assert ids.index("loc-forge") < ids.index("char-smith")

    def test_priority_ids_returned(self):
        """Priority IDs set is returned as second element of tuple."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100"),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),
        ]
        _, priority_ids = _select_context_aware_entities(
            entities, "Alice walked in",
            current_turn=100, recency_window=10)
        assert "char-a" in priority_ids
        assert "char-b" not in priority_ids

    def test_staleness_excludes_old_backfill(self):
        """Backfill entities older than 50 turns are excluded when turn_text present."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100"),
            _make_entity("char-b", "Bob", last_updated_turn="turn-040"),  # 60 turns stale
            _make_entity("char-c", "Charlie", last_updated_turn="turn-060"),  # 40 turns stale
        ]
        result, priority_ids = _select_context_aware_entities(
            entities, "Alice appeared",
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        # Alice is mentioned (priority)
        assert "char-a" in priority_ids
        assert "char-a" in ids
        # Charlie at turn-060 is 40 turns stale (< 50 threshold), kept
        assert "char-c" in ids
        # Bob at turn-040 is 60 turns stale (> 50 threshold), excluded
        assert "char-b" not in ids

    def test_staleness_does_not_exclude_priority(self):
        """Priority entities are kept regardless of staleness."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100",
                         relationships=[{"target_id": "char-b",
                                         "current_relationship": "ally",
                                         "type": "social",
                                         "status": "active",
                                         "first_seen_turn": "turn-001"}]),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),  # 90 turns stale
        ]
        result, priority_ids = _select_context_aware_entities(
            entities, "Alice appeared",
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        # Bob is one-hop (priority) despite being 90 turns stale
        assert "char-b" in priority_ids
        assert "char-b" in ids

    def test_staleness_not_applied_without_turn_text(self):
        """Without turn_text, all backfill entities are kept regardless of age."""
        entities = [
            _make_entity("char-a", "Alice", last_updated_turn="turn-100"),
            _make_entity("char-b", "Bob", last_updated_turn="turn-010"),  # 90 turns stale
        ]
        result, _ = _select_context_aware_entities(
            entities, None,
            current_turn=100, recency_window=10)
        ids = [e["id"] for e in result]
        # Both kept — no staleness filtering without turn_text
        assert "char-a" in ids
        assert "char-b" in ids


# ---------------------------------------------------------------------------
# format_known_entities_bounded with turn_text — integration
# ---------------------------------------------------------------------------

class TestBoundedWithTurnText:
    def test_mentioned_entities_prioritized(self):
        """Mentioned entities fill budget before recency backfill."""
        mentioned = _make_entity("char-m", "Mentioned",
                                 identity="Key NPC",
                                 last_updated_turn="turn-010")
        recent = _make_entity("char-r", "Recent",
                              identity="x" * 200,
                              last_updated_turn="turn-098")
        catalogs = _make_catalogs([mentioned, recent])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=30,
            recency_window=10,
            turn_text="Mentioned walked in")
        # Mentioned entity should be present with full detail
        assert "char-m" in result
        assert "Key NPC" in result

    def test_colocated_entities_in_context(self):
        """Co-located entities get included even if not recent."""
        loc = _make_entity("loc-t", "Thornhaven", etype="location",
                           last_updated_turn="turn-050")
        colocated = _make_entity("char-c", "Colocated",
                                 identity="NPC at Thornhaven",
                                 location="Thornhaven",
                                 last_updated_turn="turn-020")
        far = _make_entity("char-f", "FarEntity",
                           identity="x" * 200,
                           last_updated_turn="turn-095")
        catalogs = _make_catalogs([colocated, far], [loc])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=80,
            recency_window=10,
            turn_text="They arrived at Thornhaven")
        # Both Thornhaven and Colocated should appear
        assert "loc-t" in result
        assert "char-c" in result

    def test_budget_still_enforced_with_context(self):
        """Token budget is respected even with context-aware selection."""
        entities = []
        # 50 mentioned entities with big descriptions
        for i in range(50):
            entities.append(_make_entity(
                f"char-{i:03d}", f"Entity{i:03d}",
                identity="A long description " * 10,
                last_updated_turn=f"turn-{i:03d}"))
        catalogs = _make_catalogs(entities)
        turn_text = " ".join(f"Entity{i:03d}" for i in range(50))
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=200,
            recency_window=10,
            turn_text=turn_text)
        # Result should be within budget (may exceed slightly due to note)
        main_text = result.split("\n\n(Note:")[0] if "(Note:" in result else result
        assert _estimate_tokens(main_text) <= 200

    def test_no_turn_text_same_as_before(self):
        """Without turn_text, behavior matches pre-#233."""
        entities = [
            _make_entity("char-r", "Recent", identity="Active",
                         last_updated_turn="turn-098"),
            _make_entity("char-d", "Dormant", identity="Old",
                         last_updated_turn="turn-010"),
        ]
        catalogs = _make_catalogs(entities)
        result_no_text = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=20,
            recency_window=10)
        result_none = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=20,
            recency_window=10,
            turn_text=None)
        assert result_no_text == result_none

    def test_one_hop_relationship_in_context(self):
        """Relationship targets get included via one-hop traversal."""
        mentor = _make_entity("char-m", "Mentor",
                              identity="Wise teacher",
                              last_updated_turn="turn-100",
                              relationships=[{
                                  "target_id": "char-s",
                                  "current_relationship": "teaches",
                                  "type": "mentorship",
                                  "first_seen_turn": "turn-010",
                              }])
        student = _make_entity("char-s", "Student",
                               identity="Young learner",
                               last_updated_turn="turn-010")
        bystander = _make_entity("char-b", "Bystander",
                                 identity="x" * 200,
                                 last_updated_turn="turn-080")
        catalogs = _make_catalogs([mentor, student, bystander])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=80,
            recency_window=10,
            turn_text="Mentor started the lesson")
        # Mentor is mentioned, Student is one-hop
        assert "char-m" in result
        assert "char-s" in result

    def test_mentioned_via_character_location(self):
        """When a character is mentioned, entities at their location come in."""
        alice = _make_entity("char-a", "Alice",
                             location="The Tavern",
                             last_updated_turn="turn-100")
        bob = _make_entity("char-b", "Bob",
                           location="The Tavern",
                           last_updated_turn="turn-010")
        charlie = _make_entity("char-c", "Charlie",
                               location="The Market",
                               last_updated_turn="turn-090")
        catalogs = _make_catalogs([alice, bob, charlie])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=80,
            recency_window=10,
            turn_text="Alice spoke up")
        # Alice is mentioned, Bob is co-located at The Tavern
        assert "char-a" in result
        assert "char-b" in result

    def test_empty_catalog_with_turn_text(self):
        """Empty catalog still returns the expected message."""
        catalogs = {"characters.json": []}
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=500,
            turn_text="Some turn text")
        assert "none" in result.lower()

    def test_location_entity_alias_match(self):
        """A location's alias triggers co-location pull."""
        loc = _make_entity("loc-t", "Thornhaven", etype="location",
                           aliases=["The Haven"],
                           last_updated_turn="turn-050")
        npc = _make_entity("char-n", "NPC",
                           location="Thornhaven",
                           last_updated_turn="turn-020")
        catalogs = _make_catalogs([npc], [loc])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=100,
            recency_window=10,
            turn_text="They entered The Haven")
        assert "loc-t" in result
        assert "char-n" in result

    def test_priority_ordering_mentioned_over_colocated_over_backfill(self):
        """Priority: mentioned > co-located > backfill."""
        mentioned_char = _make_entity("char-mentioned", "Hero",
                                      identity="The main hero",
                                      location="Thornhaven",
                                      last_updated_turn="turn-050")
        colocated_char = _make_entity("char-coloc", "Shopkeeper",
                                      identity="Sells potions",
                                      location="Thornhaven",
                                      last_updated_turn="turn-030")
        backfill_char = _make_entity("char-back", "Distant",
                                     identity="From far away",
                                     location="Faraway City",
                                     last_updated_turn="turn-099")
        catalogs = _make_catalogs([mentioned_char, colocated_char, backfill_char])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=500,
            recency_window=10,
            turn_text="Hero entered the room")
        lines = result.strip().split("\n")
        # Find positions
        hero_pos = next(i for i, l in enumerate(lines) if "char-mentioned" in l)
        shop_pos = next(i for i, l in enumerate(lines) if "char-coloc" in l)
        back_pos = next(i for i, l in enumerate(lines) if "char-back" in l)
        assert hero_pos < shop_pos, "Mentioned should come before co-located"
        assert shop_pos < back_pos, "Co-located should come before backfill"

    def test_context_entities_get_full_detail(self):
        """Mentioned and co-located entities get full detail even if dormant."""
        loc = _make_entity("loc-t", "Thornhaven", etype="location",
                           identity="A quiet settlement",
                           last_updated_turn="turn-020")
        npc = _make_entity("char-n", "NPC",
                           identity="Local innkeeper",
                           location="Thornhaven",
                           last_updated_turn="turn-020")
        catalogs = _make_catalogs([npc], [loc])
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=200,
            recency_window=10,
            turn_text="They arrived at Thornhaven")
        # Both entities are dormant (turn 20, window 10, current 100)
        # but context-aware => they should have full detail
        assert "A quiet settlement" in result
        assert "Local innkeeper" in result

    def test_staleness_trims_even_under_budget(self):
        """With turn_text, stale backfill entities are excluded even if budget allows them."""
        mentioned = _make_entity("char-m", "Mentioned",
                                 identity="Active NPC",
                                 last_updated_turn="turn-100")
        recent_backfill = _make_entity("char-r", "RecentBackfill",
                                       identity="Recently active",
                                       last_updated_turn="turn-080")
        stale_backfill = _make_entity("char-s", "StaleBackfill",
                                      identity="Ancient entity from long ago",
                                      last_updated_turn="turn-010")
        catalogs = _make_catalogs([mentioned, recent_backfill, stale_backfill])
        # Budget is huge — everything would fit
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=5000,
            recency_window=10,
            turn_text="Mentioned spoke up")
        # Mentioned is priority, always in
        assert "char-m" in result
        # Recent backfill (20 turns old) is within 50-turn threshold
        assert "char-r" in result
        # Stale backfill (90 turns old) exceeds 50-turn threshold, excluded
        assert "char-s" not in result

    def test_no_staleness_without_turn_text(self):
        """Without turn_text, all entities kept regardless of age (fast-path)."""
        entities = [
            _make_entity("char-r", "Recent", identity="New",
                         last_updated_turn="turn-098"),
            _make_entity("char-s", "Stale", identity="Old but present",
                         last_updated_turn="turn-010"),
        ]
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=5000,
            recency_window=10)
        # Without turn_text, fast-path fires (or staleness not applied)
        assert "char-r" in result
        assert "char-s" in result
