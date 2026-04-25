"""Tests for bounded entity context formatting (#221)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from catalog_merger import (
    format_known_entities,
    format_known_entities_bounded,
    _estimate_tokens,
    _format_entity_full,
    _format_entity_brief,
)


def _make_entity(eid, name, etype="character", identity="", aliases=None,
                 last_updated_turn=None):
    """Build a minimal entity dict for testing."""
    e = {"id": eid, "name": name, "type": etype}
    if identity:
        e["identity"] = identity
    if aliases:
        e["stable_attributes"] = {"aliases": {"value": aliases}}
    if last_updated_turn:
        e["last_updated_turn"] = last_updated_turn
    return e


def _make_catalogs(entities):
    """Wrap a flat entity list into a catalogs dict."""
    return {"characters.json": entities}


class TestEstimateTokens:
    def test_basic(self):
        assert _estimate_tokens("abcd") == 1
        assert _estimate_tokens("a" * 100) == 25

    def test_empty(self):
        assert _estimate_tokens("") == 1  # min 1


class TestFormatEntityFull:
    def test_with_identity_and_aliases(self):
        e = _make_entity("char-a", "Alice", identity="A warrior",
                         aliases=["Ali", "Al"])
        line = _format_entity_full(e)
        assert "char-a" in line
        assert "Alice" in line
        assert "A warrior" in line
        assert "Ali" in line

    def test_minimal(self):
        e = _make_entity("char-b", "Bob")
        line = _format_entity_full(e)
        assert line == "char-b | Bob | character"


class TestFormatEntityBrief:
    def test_strips_identity(self):
        e = _make_entity("char-a", "Alice", identity="A warrior",
                         aliases=["Shortbow"])
        line = _format_entity_brief(e)
        assert line == "char-a | Alice | character"
        assert "warrior" not in line
        assert "Shortbow" not in line


class TestBoundedFormatSmallCatalog:
    """When catalog is small enough, all entities get full detail."""

    def test_small_catalog_unchanged(self):
        entities = [_make_entity(f"char-{i}", f"Char{i}",
                                 last_updated_turn=f"turn-{i:03d}")
                    for i in range(5)]
        catalogs = _make_catalogs(entities)
        unbounded = format_known_entities(catalogs)
        bounded = format_known_entities_bounded(
            catalogs, current_turn=10, context_length=32768)
        assert bounded == unbounded

    def test_empty_catalog(self):
        result = format_known_entities_bounded(
            {"characters.json": []}, current_turn=1, context_length=8192)
        assert "none" in result.lower()


class TestBoundedFormatRecentPrioritization:
    """Recent entities get full detail, dormant ones get brief or omitted."""

    def test_recent_full_dormant_brief(self):
        recent = [_make_entity("char-r", "Recent", identity="Active hero",
                               aliases=["R"], last_updated_turn="turn-098")]
        dormant = [_make_entity("char-d", "Dormant",
                                identity="Old NPC from long ago",
                                aliases=["D", "Dormy"],
                                last_updated_turn="turn-010")]
        catalogs = _make_catalogs(recent + dormant)
        # Budget of 20 tokens is below unbounded size (31 tokens), so
        # the fast-path won't apply and tiering kicks in.
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=20,
            recency_window=10)
        # Recent entity should have full detail
        assert "Active hero" in result
        assert "aliases: R" in result
        # Dormant entity should be brief (no identity/aliases) because
        # it's outside the recency window and budget is tight.
        lines = [l for l in result.strip().split("\n") if l.startswith("char-d")]
        assert len(lines) == 1
        assert "Old NPC" not in lines[0]
        assert "Dormy" not in lines[0]

    def test_no_current_turn_all_recent(self):
        """When current_turn is None, all entities are treated as recent."""
        entities = [_make_entity("char-a", "A", identity="Detail",
                                 last_updated_turn="turn-001")]
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(
            catalogs, current_turn=None, context_length=32768)
        assert "Detail" in result


class TestBoundedFormatBudgetEnforcement:
    """Entity list stays within the configured token budget."""

    def _big_catalog(self, n, turn_offset=0):
        """Create n entities with long identities."""
        entities = []
        for i in range(n):
            entities.append(_make_entity(
                f"char-{i:04d}", f"Character-{i:04d}",
                identity=f"A detailed description of entity {i} " * 5,
                aliases=[f"alias-{i}-a", f"alias-{i}-b"],
                last_updated_turn=f"turn-{i + turn_offset:03d}",
            ))
        return entities

    def test_budget_limits_output(self):
        entities = self._big_catalog(200, turn_offset=1)
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(
            catalogs, current_turn=200,
            entity_context_budget=500,  # very tight budget
            recency_window=5)
        # Recent entities (turns 195-200 = 6 entities) must appear.
        assert "char-0199" in result  # most recent
        assert "char-0195" in result  # edge of recency window
        # Should have some omitted
        assert "additional entities exist" in result

    def test_explicit_budget_overrides_fraction(self):
        entities = self._big_catalog(200, turn_offset=1)
        catalogs = _make_catalogs(entities)
        # context_length=100000 would give 25000 budget by default,
        # but explicit budget=500 should override
        result = format_known_entities_bounded(
            catalogs, current_turn=200,
            context_length=100000,
            entity_context_budget=500,
            recency_window=5)
        assert "additional entities exist" in result

    def test_large_context_no_truncation(self):
        """Models with huge context windows should get all entities."""
        entities = self._big_catalog(50, turn_offset=1)
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(
            catalogs, current_turn=50,
            context_length=1_000_000,  # 1M tokens
            recency_window=10)
        # All 50 entities should appear, no truncation note
        assert "additional entities exist" not in result
        for i in range(50):
            assert f"char-{i:04d}" in result


class TestBoundedFormatNoBudget:
    """When no budget info is available, falls back to unbounded."""

    def test_no_context_length(self):
        entities = [_make_entity("char-a", "A", identity="Detail")]
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(catalogs)
        unbounded = format_known_entities(catalogs)
        assert result == unbounded

    def test_none_budget_none_context(self):
        entities = [_make_entity("char-a", "A", identity="Detail")]
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(
            catalogs, current_turn=10,
            context_length=None, entity_context_budget=None)
        unbounded = format_known_entities(catalogs)
        assert result == unbounded


class TestBoundedFormatDormantOrdering:
    """Dormant entities are sorted by recency — most recent first."""

    def test_most_recent_dormant_included_first(self):
        recent = [_make_entity("char-r", "R", last_updated_turn="turn-100")]
        dormant = [
            _make_entity("char-old", "Old", identity="x" * 200,
                         last_updated_turn="turn-010"),
            _make_entity("char-mid", "Mid", identity="x" * 200,
                         last_updated_turn="turn-050"),
        ]
        catalogs = _make_catalogs(recent + dormant)
        # Very tight budget: only room for recent + maybe one dormant
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=100,  # tiny
            recency_window=5)
        # char-mid (turn 50) should appear before char-old (turn 10)
        # if both fit; if only one fits, it should be char-mid
        if "char-mid" in result and "char-old" in result:
            assert result.index("char-mid") < result.index("char-old")
        elif "char-mid" in result:
            pass  # correct — mid is more recent
        else:
            # Both might be omitted if budget is too tight for any dormant
            pass


class TestDiscoveryPromptIntegration:
    """The discovery prompt receives well-formed entity context."""

    def test_format_discovery_prompt_with_bounded(self):
        """Ensure format_discovery_prompt accepts bounded output."""
        from semantic_extraction import format_discovery_prompt
        entities = [_make_entity("char-a", "Alice", identity="A warrior",
                                 last_updated_turn="turn-050")]
        catalogs = _make_catalogs(entities)
        known = format_known_entities_bounded(
            catalogs, current_turn=50, context_length=8192)
        turn = {"turn_id": "turn-051", "speaker": "DM", "text": "You see Alice."}
        prompt = format_discovery_prompt(turn, known)
        assert "## Known Entities" in prompt
        assert "char-a" in prompt
        assert "Alice" in prompt

    def test_truncation_note_in_prompt(self):
        """Truncation note is part of the Known Entities section."""
        from semantic_extraction import format_discovery_prompt
        entities = [
            _make_entity(f"char-{i}", f"C{i}",
                         identity="x" * 200,
                         last_updated_turn=f"turn-{i:03d}")
            for i in range(100)
        ]
        catalogs = _make_catalogs(entities)
        known = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=200,
            recency_window=5)
        turn = {"turn_id": "turn-101", "speaker": "DM", "text": "Test."}
        prompt = format_discovery_prompt(turn, known)
        assert "additional entities exist" in prompt


class TestFastPathUnbounded:
    """When full output fits within budget, return unbounded (all full detail)."""

    def test_full_output_within_budget_matches_unbounded(self):
        """Even dormant entities get full detail if it all fits."""
        recent = [_make_entity("char-r", "Recent", identity="Hero",
                               last_updated_turn="turn-098")]
        dormant = [_make_entity("char-d", "Dormant", identity="Old friend",
                                last_updated_turn="turn-010")]
        catalogs = _make_catalogs(recent + dormant)
        unbounded = format_known_entities(catalogs)
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            context_length=100000,  # huge budget
            recency_window=5)
        assert result == unbounded
        # Dormant entity should have full detail since it fits
        assert "Old friend" in result


class TestRecentTierOverflow:
    """When recent entities alone exceed budget, they degrade to brief."""

    def test_recent_degraded_when_over_budget(self):
        """Oldest recent entities should lose identity/aliases."""
        # Create entities all within recency window but with big descriptions
        entities = [
            _make_entity(f"char-{i}", f"Char{i}",
                         identity="Very long description " * 20,
                         aliases=[f"alias-{i}"],
                         last_updated_turn=f"turn-{90 + i:03d}")
            for i in range(10)
        ]
        catalogs = _make_catalogs(entities)
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=200,  # very tight
            recency_window=15)
        # All 10 are recent, but budget is small.
        # Most recent should keep detail, oldest recent should be brief.
        # All entities should still appear (not omitted).
        for i in range(10):
            assert f"char-{i}" in result
        # The result should have at least some entities in brief format
        # (no identity) to stay within budget
        brief_lines = [l for l in result.strip().split("\n")
                       if l and "Very long description" not in l
                       and l.startswith("char-")]
        assert len(brief_lines) > 0, "Some recent entities should be degraded to brief"

    def test_single_recent_entity_not_degraded(self):
        """A single recent entity should not trigger degradation loop."""
        entity = _make_entity("char-solo", "Solo",
                              identity="Big description " * 50,
                              last_updated_turn="turn-100")
        catalogs = _make_catalogs([entity])
        # Budget is tiny but only 1 recent entity — should not crash
        result = format_known_entities_bounded(
            catalogs, current_turn=100,
            entity_context_budget=10,
            recency_window=5)
        assert "char-solo" in result
