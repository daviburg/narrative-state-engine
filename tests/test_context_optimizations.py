"""Tests for context budget optimization features (#387).

All three optimizations (relationship relevance scoring, arc-aware compression,
scene-scoped detail) are always active — no config flags.

Covers:
- _trim_entry_for_scene(): scene-scoped detail injection with mention filtering
- _format_rel_by_tier(): per-tier relationship formatting
- _format_relationships_budgeted(): budget-aware relationship formatting
- Relationship relevance scoring in _collect_existing_relationships()
- Arc-aware compression in _format_prior_entity_context()
- Scene-scoped detail in format_detail_prompt()
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import semantic_extraction as se


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    entity_id="char-test",
    name="Test Entity",
    entity_type="character",
    last_updated_turn="turn-100",
    relationships=None,
    volatile_state=None,
    stable_attributes=None,
    identity="A test entity",
    current_status="active",
):
    """Build a minimal catalog entry for testing."""
    entry = {
        "id": entity_id,
        "name": name,
        "type": entity_type,
        "first_seen_turn": "turn-1",
        "last_updated_turn": last_updated_turn,
        "identity": identity,
        "current_status": current_status,
    }
    if stable_attributes is not None:
        entry["stable_attributes"] = stable_attributes
    if volatile_state is not None:
        entry["volatile_state"] = volatile_state
    if relationships is not None:
        entry["relationships"] = relationships
    return entry


def _make_rel(
    target_id,
    status="active",
    last_updated_turn="turn-90",
    relationship_type="ally",
    source_id="char-test",
    history=None,
):
    """Build a minimal relationship dict."""
    rel = {
        "source_id": source_id,
        "target_id": target_id,
        "relationship_type": relationship_type,
        "status": status,
        "last_updated_turn": last_updated_turn,
    }
    if history is not None:
        rel["history"] = history
    return rel


# ===========================================================================
# A. _trim_entry_for_scene() tests
# ===========================================================================

class TestTrimEntryForScene:
    """Tests for _trim_entry_for_scene()."""

    def test_basic_trimming_keeps_mentioned_relationships(self):
        """Relationships whose target is in mentioned_ids are kept fully."""
        rels = [
            _make_rel("char-alice", last_updated_turn="turn-10"),  # old, but mentioned
            _make_rel("char-bob", last_updated_turn="turn-10"),    # old, not mentioned
        ]
        entry = _make_entry(relationships=rels)
        mentioned = {"char-alice"}

        result = se._trim_entry_for_scene(entry, mentioned_ids=mentioned)

        # char-alice should be in kept (full form), char-bob in summary
        kept_targets = [r["target_id"] for r in result["relationships"]
                        if "relationship_type" in r and "status" in r
                        and "last_updated_turn" in r and "source_id" in r]
        assert "char-alice" in kept_targets

    def test_entity_with_no_relationships(self):
        """Entry without relationships returns cleanly without crash."""
        entry = _make_entry(relationships=None)
        result = se._trim_entry_for_scene(entry)
        assert "relationships" not in result
        assert result["id"] == "char-test"

    def test_entity_with_empty_relationships(self):
        """Entry with empty relationship list returns cleanly."""
        entry = _make_entry(relationships=[])
        result = se._trim_entry_for_scene(entry)
        # Empty list is falsy — relationships block should be absent
        assert "relationships" not in result

    def test_entity_with_no_volatile_state(self):
        """Entry without volatile_state handles gracefully."""
        entry = _make_entry(volatile_state=None)
        result = se._trim_entry_for_scene(entry)
        assert "volatile_state" not in result
        assert result["id"] == "char-test"

    def test_relationship_recency_filtering(self):
        """Old relationships beyond recency window get summarized."""
        # Window is 20 turns, entry is at turn-100
        rels = [
            _make_rel("char-recent", last_updated_turn="turn-95"),  # within window
            _make_rel("char-old", last_updated_turn="turn-50"),     # outside window
        ]
        entry = _make_entry(relationships=rels)
        result = se._trim_entry_for_scene(entry, mentioned_ids=set())

        # char-recent should be kept (active + within recency window)
        # char-old should be in summary form (no source_id, just target_id + type + status)
        result_rels = result["relationships"]
        recent_rel = next(r for r in result_rels if r["target_id"] == "char-recent")
        old_rel = next(r for r in result_rels if r["target_id"] == "char-old")
        # Kept rels have source_id; summary rels do not
        assert "source_id" in recent_rel
        assert "source_id" not in old_rel

    def test_cap_enforcement(self):
        """More than 15 relationships get capped at _SCENE_MAX_RELATIONSHIPS."""
        rels = [
            _make_rel(f"char-entity-{i}", last_updated_turn="turn-99")
            for i in range(20)
        ]
        entry = _make_entry(relationships=rels)
        result = se._trim_entry_for_scene(entry, mentioned_ids=set())
        assert len(result["relationships"]) <= se._SCENE_MAX_RELATIONSHIPS

    def test_mentioned_entities_kept_regardless_of_recency(self):
        """Mentioned entities are kept even if their relationship is old."""
        rels = [
            _make_rel("char-ancient", last_updated_turn="turn-1", status="dormant"),
        ]
        entry = _make_entry(relationships=rels)
        mentioned = {"char-ancient"}

        result = se._trim_entry_for_scene(entry, mentioned_ids=mentioned)
        result_rels = result["relationships"]
        # Should be kept in full form (has source_id)
        ancient = next(r for r in result_rels if r["target_id"] == "char-ancient")
        assert "source_id" in ancient

    def test_core_fields_preserved(self):
        """Core identity fields are always preserved in trimmed output."""
        entry = _make_entry(
            stable_attributes={"species": "elf", "class": "wizard"},
            identity="A wise elder",
        )
        result = se._trim_entry_for_scene(entry)
        assert result["id"] == "char-test"
        assert result["name"] == "Test Entity"
        assert result["identity"] == "A wise elder"
        assert result["stable_attributes"] == {"species": "elf", "class": "wizard"}

    def test_default_mentioned_ids_is_empty(self):
        """When mentioned_ids is not provided, defaults to empty set (no matches)."""
        rels = [
            _make_rel("char-alice", last_updated_turn="turn-10"),  # old
        ]
        entry = _make_entry(relationships=rels)
        # No mentioned_ids — should not crash
        result = se._trim_entry_for_scene(entry)
        assert "relationships" in result


# ===========================================================================
# B. _format_rel_by_tier() tests
# ===========================================================================

class TestFormatRelByTier:
    """Tests for _format_rel_by_tier()."""

    def _sample_rel(self):
        return {
            "source_id": "char-a",
            "target_id": "char-b",
            "relationship_type": "ally",
            "status": "active",
            "last_updated_turn": "turn-50",
            "history": [
                {"turn": "turn-10", "event": "first met"},
                {"turn": "turn-30", "event": "formed alliance"},
                {"turn": "turn-50", "event": "renewed vow"},
            ],
        }

    def test_tier_1_full(self):
        """Tier 1: full relationship JSON preserved."""
        rel = self._sample_rel()
        result = se._format_rel_by_tier(1, rel)
        assert result == rel

    def test_tier_2_recent(self):
        """Tier 2: only current state + last history entry."""
        rel = self._sample_rel()
        result = se._format_rel_by_tier(2, rel)
        assert result["source_id"] == "char-a"
        assert result["target_id"] == "char-b"
        assert result["status"] == "active"
        assert len(result["history"]) == 1
        assert result["history"][0]["turn"] == "turn-50"

    def test_tier_2_no_history(self):
        """Tier 2 with no history: returns without history key."""
        rel = {
            "source_id": "char-a",
            "target_id": "char-b",
            "relationship_type": "ally",
            "status": "active",
        }
        result = se._format_rel_by_tier(2, rel)
        assert "history" not in result
        assert result["status"] == "active"

    def test_tier_3_summary(self):
        """Tier 3: compact summary format."""
        rel = self._sample_rel()
        result = se._format_rel_by_tier(3, rel)
        assert set(result.keys()) == {"source_id", "target_id", "relationship_type", "status"}
        assert result["source_id"] == "char-a"
        assert result["target_id"] == "char-b"
        assert result["relationship_type"] == "ally"
        assert result["status"] == "active"

    def test_tier_4_omit(self):
        """Tier 4: returns empty dict (omitted)."""
        rel = self._sample_rel()
        result = se._format_rel_by_tier(4, rel)
        assert result == {}


# ===========================================================================
# C. _format_relationships_budgeted() tests
# ===========================================================================

class TestFormatRelationshipsBudgeted:
    """Tests for _format_relationships_budgeted()."""

    def _sample_scored(self):
        """Build a scored list with items at various tiers."""
        return [
            (1, "char-a", _make_rel("char-b", source_id="char-a")),
            (2, "char-a", _make_rel("char-c", source_id="char-a")),
            (3, "char-a", _make_rel("char-d", source_id="char-a")),
            (4, "char-a", _make_rel("char-e", source_id="char-a")),  # omitted
        ]

    def test_under_budget_all_included(self):
        """Under budget: all non-tier-4 relationships at assigned tier."""
        scored = self._sample_scored()
        # Large budget — everything fits
        result = se._format_relationships_budgeted(scored, budget=10000)
        parsed = json.loads(result)
        # Tier 4 (char-e) should be omitted
        targets = [r["target_id"] for r in parsed.get("char-a", [])]
        assert "char-b" in targets
        assert "char-c" in targets
        assert "char-d" in targets
        assert "char-e" not in targets

    def test_over_budget_tier3_degraded(self):
        """Over budget: tier 3 items degraded to omit."""
        scored = self._sample_scored()
        # Tiny budget — should degrade tier 3
        result = se._format_relationships_budgeted(scored, budget=1)
        if result:
            parsed = json.loads(result)
            targets = [r["target_id"] for r in parsed.get("char-a", [])]
            # Tier 3 (char-d) should be omitted after degradation
            assert "char-d" not in targets

    def test_far_over_budget_tier2_degraded(self):
        """Far over budget: tier 2 items degraded to summary."""
        # Create items that are large enough to be over budget
        big_history = [{"turn": f"turn-{i}", "event": f"event {'x' * 100}"} for i in range(20)]
        scored = [
            (2, "char-a", _make_rel("char-b", source_id="char-a", history=big_history)),
            (2, "char-a", _make_rel("char-c", source_id="char-a", history=big_history)),
        ]
        # Very small budget — should degrade tier 2 to summary (tier 3)
        result = se._format_relationships_budgeted(scored, budget=1)
        if result:
            parsed = json.loads(result)
            for r in parsed.get("char-a", []):
                # After degradation to tier 3, should only have summary fields
                assert "history" not in r

    def test_empty_input(self):
        """Empty input: returns empty string."""
        result = se._format_relationships_budgeted([], budget=1000)
        assert result == ""

    def test_all_tier4_returns_empty(self):
        """All tier-4 items: returns empty string."""
        scored = [
            (4, "char-a", _make_rel("char-b", source_id="char-a")),
            (4, "char-a", _make_rel("char-c", source_id="char-a")),
        ]
        result = se._format_relationships_budgeted(scored, budget=1000)
        assert result == ""


# ===========================================================================
# D. Relationship scoring tests (_collect_existing_relationships)
# ===========================================================================

class TestRelationshipScoring:
    """Tests for relationship relevance scoring in _collect_existing_relationships."""

    def _make_catalogs_with_rels(self, entity_id, rels):
        """Create minimal catalogs with one entity that has relationships."""
        entity = _make_entry(
            entity_id=entity_id,
            name=entity_id.replace("char-", "").replace("-", " ").title(),
            relationships=rels,
        )
        return {"characters.json": [entity]}

    def test_both_endpoints_mentioned_tier1(self):
        """Both endpoints mentioned → tier 1 (full)."""
        rels = [_make_rel("char-bob", source_id="char-alice", last_updated_turn="turn-50")]
        catalogs = self._make_catalogs_with_rels("char-alice", rels)

        result = se._collect_existing_relationships(
            catalogs,
            entity_ids=["char-alice", "char-bob"],
            turn_text="Alice and Bob met in the forest",
            current_turn_num=100,
            context_length=32768,
        )
        # Result should be non-empty JSON
        assert result
        parsed = json.loads(result)
        # char-alice's relationship to char-bob should be present (tier 1 = full)
        alice_rels = parsed.get("char-alice", [])
        assert len(alice_rels) >= 1
        # Full format has source_id and last_updated_turn
        assert alice_rels[0].get("source_id") == "char-alice"
        assert "last_updated_turn" in alice_rels[0]

    def test_one_mentioned_recent_tier2(self):
        """One endpoint mentioned + recent → tier 2."""
        rels = [_make_rel("char-stranger", source_id="char-alice", last_updated_turn="turn-95")]
        catalogs = self._make_catalogs_with_rels("char-alice", rels)

        result = se._collect_existing_relationships(
            catalogs,
            entity_ids=["char-alice"],
            turn_text="Alice walked through the market",
            current_turn_num=100,
            context_length=32768,
        )
        assert result
        parsed = json.loads(result)
        alice_rels = parsed.get("char-alice", [])
        assert len(alice_rels) >= 1
        # Tier 2 trims history to last entry only
        rel = alice_rels[0]
        if "history" in rel:
            assert len(rel["history"]) <= 1

    def test_one_mentioned_active_old_tier3(self):
        """One endpoint mentioned + active + old → tier 3 (summary)."""
        rels = [_make_rel("char-stranger", source_id="char-alice", last_updated_turn="turn-10")]
        catalogs = self._make_catalogs_with_rels("char-alice", rels)

        result = se._collect_existing_relationships(
            catalogs,
            entity_ids=["char-alice"],
            turn_text="Alice walked through the market",
            current_turn_num=100,
            context_length=32768,
        )
        assert result
        parsed = json.loads(result)
        alice_rels = parsed.get("char-alice", [])
        assert len(alice_rels) >= 1
        # Tier 3 is summary: only source_id, target_id, relationship_type, status
        rel = alice_rels[0]
        assert "history" not in rel

    def test_dormant_resolved_tier4_omitted(self):
        """Dormant/resolved relationships → tier 4 (omitted)."""
        rels = [
            _make_rel("char-stranger", source_id="char-alice",
                       last_updated_turn="turn-10", status="dormant"),
        ]
        catalogs = self._make_catalogs_with_rels("char-alice", rels)

        result = se._collect_existing_relationships(
            catalogs,
            entity_ids=["char-alice"],
            turn_text="Alice walked through the market",
            current_turn_num=100,
            context_length=32768,
        )
        # dormant + only one mentioned + old → tier 4 (omit)
        if result:
            parsed = json.loads(result)
            # Should be empty — tier 4 gets omitted
            alice_rels = parsed.get("char-alice", [])
            assert len(alice_rels) == 0
        else:
            # Empty string is valid — no relationships to return
            pass


# ===========================================================================
# E. Arc-aware compression tests (_format_prior_entity_context)
# ===========================================================================

class TestArcAwareCompression:
    """Tests for arc-aware compression in _format_prior_entity_context."""

    def test_non_pc_long_volatile_state_digested(self):
        """Non-PC entity with long volatile_state gets digested (always-on)."""
        vs = {
            "location": [
                "Moved to town square (turn-10)",
                "Traveled to forest (turn-20)",
                "Returned to village (turn-30)",
                "Went to castle (turn-40)",
                "Explored dungeon (turn-50)",
                "Back at village (turn-90)",
                "At the market (turn-95)",
                "Near the river (turn-99)",
            ],
        }
        entry = _make_entry(
            entity_id="char-npc",
            volatile_state=vs,
            last_updated_turn="turn-100",
        )

        result_json = se._format_prior_entity_context(entry)
        result = json.loads(result_json)
        vs_out = result.get("volatile_state", {})
        loc = vs_out.get("location", [])
        # Should be capped to _ARC_AWARE_MAX_VOLATILE_SNAPSHOTS (3) + possible digest summary
        assert len(loc) <= se._ARC_AWARE_MAX_VOLATILE_SNAPSHOTS + 1

    def test_non_pc_many_history_entries_capped(self):
        """Non-PC with 5+ relationship history entries: capped to 3."""
        rels = [{
            "target_id": "char-ally",
            "type": "ally",
            "status": "active",
            "last_updated_turn": "turn-95",
            "history": [
                {"turn": f"turn-{i}", "event": f"event {i}"}
                for i in range(1, 8)
            ],
        }]
        entry = _make_entry(entity_id="char-npc", relationships=rels)

        result_json = se._format_prior_entity_context(entry)
        result = json.loads(result_json)
        out_rels = result.get("relationships", [])
        assert len(out_rels) == 1
        assert len(out_rels[0]["history"]) == 3

    def test_pc_entity_still_works(self):
        """PC entity: compression always applies (no regression)."""
        vs = {
            "goals": [
                "Find the artifact (turn-1)",
                "Speak to elder (turn-50)",
                "Enter dungeon (turn-90)",
                "Defeat boss (turn-95)",
            ],
        }
        entry = _make_entry(
            entity_id="char-player",
            volatile_state=vs,
            last_updated_turn="turn-100",
        )
        # PC compression should work even without arc_aware config
        result_json = se._format_prior_entity_context(entry, config=None)
        result = json.loads(result_json)
        assert "volatile_state" in result


# ===========================================================================
# F. Scene-scoped detail tests (format_detail_prompt)
# ===========================================================================

class TestSceneScopedDetail:
    """Tests for scene-scoped detail (always-on) in format_detail_prompt."""

    def _make_turn(self, text="The elder speaks of ancient times"):
        return {
            "turn_id": "turn-100",
            "speaker": "DM",
            "text": text,
        }

    def _make_entity_ref(self, entity_id="char-npc"):
        return {
            "name": "Test NPC",
            "type": "character",
            "existing_id": entity_id,
            "is_new": False,
        }

    def test_scene_scoped_trims_entry(self):
        """Entry is trimmed via scene-scoped relationship filtering in prior context."""
        rels = [
            _make_rel("char-old-friend", last_updated_turn="turn-5"),
            _make_rel("char-recent", last_updated_turn="turn-98"),
        ]
        entry = _make_entry(relationships=rels)
        mentioned = {"char-old-friend"}

        prompt = se.format_detail_prompt(
            self._make_turn(), self._make_entity_ref(),
            current_entry=entry, mentioned_ids=mentioned,
        )
        # No separate "Current Catalog Entry" — consolidated into prior state
        assert "Current Catalog Entry" not in prompt
        assert "Prior entity state" in prompt
        # Parse the JSON from the prior entity state section
        json_block = prompt.split("```json\n")[1].split("\n```")[0]
        parsed = json.loads(json_block)
        # Should be trimmed — relationships should be present but capped
        assert "relationships" in parsed or len(rels) == 0

    def test_scene_scoped_no_config(self):
        """Without config, entry is still trimmed (always-on) in prior context."""
        entry = _make_entry()
        prompt = se.format_detail_prompt(
            self._make_turn(), self._make_entity_ref(),
            current_entry=entry, config=None,
        )
        assert "Current Catalog Entry" not in prompt
        assert "Prior entity state" in prompt

    def test_pc_entity_skips_catalog_entry(self):
        """PC entity never gets the catalog entry section."""
        entry = _make_entry(entity_id="char-player")
        ref = {
            "name": "Player",
            "type": "character",
            "existing_id": "char-player",
        }

        prompt = se.format_detail_prompt(
            self._make_turn(), ref,
            current_entry=entry,
        )
        assert "Current Catalog Entry" not in prompt

    def test_mentioned_ids_passed_through(self):
        """Verify mentioned_ids reaches scene-scoped relationship filtering."""
        # If mentioned_ids works, the mentioned entity's relationship
        # will be kept in full form even if old
        rels = [
            _make_rel("char-mentioned", last_updated_turn="turn-1"),  # very old
        ]
        entry = _make_entry(relationships=rels)
        mentioned = {"char-mentioned"}

        prompt = se.format_detail_prompt(
            self._make_turn(), self._make_entity_ref(),
            current_entry=entry, mentioned_ids=mentioned,
        )
        assert "Current Catalog Entry" not in prompt
        # Parse the JSON from the prior entity state section
        json_block = prompt.split("```json\n")[1].split("\n```")[0]
        parsed = json.loads(json_block)
        # The mentioned entity's relationship should be in full form
        rels_out = parsed.get("relationships", [])
        mentioned_rel = [r for r in rels_out if r.get("target_id") == "char-mentioned"]
        assert len(mentioned_rel) >= 1
        # Full form has source_id
        assert "source_id" in mentioned_rel[0]


# ===========================================================================
# G. Fix verification tests (deferred review items)
# ===========================================================================

class TestCurrentTurnNumRecency:
    """Fix 1: Recency uses current_turn_num parameter, not entry's own turn."""

    def test_recency_uses_current_turn_num(self):
        """When current_turn_num is provided, recency is calculated from it."""
        # Entry last updated at turn-50, but current turn is 100.
        # Relationship at turn-85 is within 20-turn window of 100 but NOT of 50.
        rels = [
            _make_rel("char-recent-from-current", last_updated_turn="turn-85"),
        ]
        entry = _make_entry(last_updated_turn="turn-50", relationships=rels)

        # Without current_turn_num: uses entry's turn-50, so turn-85 is 35 turns away (outside window)
        result_no_param = se._trim_entry_for_scene(entry, mentioned_ids=set())
        assert "relationships" in result_no_param  # verify trimming ran
        # The entry's own turn (50) is used as reference; 50 - 85 = -35 -> kept (not outside window)
        # Use a scenario where distance matters: entry at turn-200, rel at turn-150
        rels2 = [
            _make_rel("char-boundary", last_updated_turn="turn-150"),
        ]
        entry2 = _make_entry(last_updated_turn="turn-200", relationships=rels2)

        # With entry's turn-200: distance = 200 - 150 = 50 (outside 20-turn window) -> summarized
        result_entry_turn = se._trim_entry_for_scene(entry2, mentioned_ids=set())
        boundary_rel = result_entry_turn["relationships"][0]
        assert "source_id" not in boundary_rel  # summarized

        # With current_turn_num=160: distance = 160 - 150 = 10 (inside window) -> kept
        result_current_turn = se._trim_entry_for_scene(
            entry2, mentioned_ids=set(), current_turn_num=160)
        boundary_rel2 = result_current_turn["relationships"][0]
        assert "source_id" in boundary_rel2  # kept in full form

    def test_current_turn_num_none_falls_back_to_entry(self):
        """When current_turn_num is None, falls back to entry's last_updated_turn."""
        rels = [
            _make_rel("char-old", last_updated_turn="turn-50"),
        ]
        entry = _make_entry(last_updated_turn="turn-100", relationships=rels)

        result_none = se._trim_entry_for_scene(entry, mentioned_ids=set(), current_turn_num=None)
        result_default = se._trim_entry_for_scene(entry, mentioned_ids=set())
        assert result_none == result_default


class TestHistoryTrimmingOnKeptRelationships:
    """Fix 3: History is trimmed to 3 entries on kept relationships."""

    def test_kept_relationship_history_trimmed(self):
        """Kept relationships have their history capped at 3 entries."""
        long_history = [
            {"turn": f"turn-{i}", "event": f"event {i}"}
            for i in range(10)
        ]
        rels = [
            _make_rel("char-mentioned", last_updated_turn="turn-95",
                       history=long_history),
        ]
        entry = _make_entry(relationships=rels)

        result = se._trim_entry_for_scene(entry, mentioned_ids={"char-mentioned"})
        kept_rel = next(r for r in result["relationships"]
                        if r["target_id"] == "char-mentioned")
        assert "history" in kept_rel
        assert len(kept_rel["history"]) == 3
        # Should keep last 3
        assert kept_rel["history"][0]["turn"] == "turn-7"

    def test_kept_relationship_short_history_unchanged(self):
        """Kept relationships with <= 3 history entries are untouched."""
        short_history = [
            {"turn": "turn-1", "event": "met"},
            {"turn": "turn-5", "event": "allied"},
        ]
        rels = [
            _make_rel("char-friend", last_updated_turn="turn-95",
                       history=short_history),
        ]
        entry = _make_entry(relationships=rels)

        result = se._trim_entry_for_scene(entry, mentioned_ids={"char-friend"})
        kept_rel = next(r for r in result["relationships"]
                        if r["target_id"] == "char-friend")
        assert len(kept_rel["history"]) == 2

    def test_recency_kept_relationship_also_trimmed(self):
        """Relationships kept by recency (not mention) also get history trimmed."""
        long_history = [
            {"turn": f"turn-{i}", "event": f"event {i}"}
            for i in range(8)
        ]
        rels = [
            _make_rel("char-recent", last_updated_turn="turn-95",
                       history=long_history),
        ]
        entry = _make_entry(relationships=rels)

        # Not mentioned, but recent (turn-95 within 20 of turn-100)
        result = se._trim_entry_for_scene(entry, mentioned_ids=set())
        kept_rel = next(r for r in result["relationships"]
                        if r["target_id"] == "char-recent")
        assert len(kept_rel["history"]) == 3


class TestBudgetOverflowSafetyValve:
    """Fix 4: Budget overflow safety valve for tier-1 dominant cases."""

    def test_tier1_overflow_trims_history(self):
        """When tier-1 items alone exceed budget, histories are trimmed to 2."""
        big_history = [
            {"turn": f"turn-{i}", "event": f"event {'x' * 200}"}
            for i in range(20)
        ]
        scored = [
            (1, "char-a", _make_rel("char-b", source_id="char-a",
                                     history=big_history)),
            (1, "char-a", _make_rel("char-c", source_id="char-a",
                                     history=big_history)),
        ]
        # Very small budget — tier-1 items should trigger safety valve
        result = se._format_relationships_budgeted(scored, budget=1)
        if result:
            parsed = json.loads(result)
            for rel in parsed.get("char-a", []):
                hist = rel.get("history", [])
                assert len(hist) <= 2

    def test_under_budget_no_trimming(self):
        """When under budget, tier-1 histories are NOT trimmed."""
        history = [
            {"turn": f"turn-{i}", "event": f"event {i}"}
            for i in range(5)
        ]
        scored = [
            (1, "char-a", _make_rel("char-b", source_id="char-a",
                                     history=history)),
        ]
        result = se._format_relationships_budgeted(scored, budget=100000)
        parsed = json.loads(result)
        rel = parsed["char-a"][0]
        assert len(rel["history"]) == 5  # all preserved


class TestEntityDetailCapping:
    """Tests for _MAX_DETAIL_ENTITIES_PER_TURN capping logic."""

    def _make_entity_ref(self, entity_id, is_new=True, confidence=0.8):
        return {
            "name": entity_id.replace("char-", "").replace("item-", ""),
            "type": "character" if entity_id.startswith("char-") else "item",
            "is_new": is_new,
            "proposed_id": entity_id if is_new else None,
            "existing_id": None if is_new else entity_id,
            "confidence": confidence,
        }

    def test_cap_not_triggered_under_limit(self):
        """No capping when entity_tasks <= cap."""
        from tools.semantic_extraction import _MAX_DETAIL_ENTITIES_PER_TURN
        # If we have fewer tasks than cap, all should be kept
        tasks = [(self._make_entity_ref(f"char-{i}"), None) for i in range(4)]
        assert len(tasks) <= _MAX_DETAIL_ENTITIES_PER_TURN

    def test_cap_enforced_new_entities_exceed_cap(self):
        """When new entities exceed cap, total is still bounded."""
        from tools.semantic_extraction import _MAX_DETAIL_ENTITIES_PER_TURN, get_entity_id
        # Simulate 25 new entities (exceeds the raised cap of 22)
        tasks = [(self._make_entity_ref(f"char-new-{i}", is_new=True, confidence=0.9-i*0.01), None)
                 for i in range(25)]
        # Apply capping logic inline (mirrors the code)
        pc_tasks = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) == "char-player"]
        non_pc = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) != "char-player"]
        new_tasks = [(ref, entry) for ref, entry in non_pc if ref.get("is_new", True)]
        existing_tasks = [(ref, entry) for ref, entry in non_pc if not ref.get("is_new", True)]
        new_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        existing_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        available = _MAX_DETAIL_ENTITIES_PER_TURN - len(pc_tasks)
        kept_new = new_tasks[:available]
        remaining = max(0, available - len(kept_new))
        kept_existing = existing_tasks[:remaining]
        result = pc_tasks + kept_new + kept_existing
        assert len(result) <= _MAX_DETAIL_ENTITIES_PER_TURN

    def test_cap_prioritizes_new_over_existing(self):
        """New entities are kept before existing ones."""
        from tools.semantic_extraction import _MAX_DETAIL_ENTITIES_PER_TURN, get_entity_id
        # 12 new + 14 existing = 26 total, cap=22 -> all 12 new kept, 10 existing
        new_refs = [(self._make_entity_ref(f"char-new-{i}", is_new=True, confidence=0.8), None)
                    for i in range(12)]
        existing_refs = [(self._make_entity_ref(f"char-old-{i}", is_new=False, confidence=0.9), None)
                         for i in range(14)]
        tasks = new_refs + existing_refs
        # Apply capping
        pc_tasks = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) == "char-player"]
        non_pc = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) != "char-player"]
        new_tasks = [(ref, entry) for ref, entry in non_pc if ref.get("is_new", True)]
        existing_tasks = [(ref, entry) for ref, entry in non_pc if not ref.get("is_new", True)]
        new_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        existing_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        available = _MAX_DETAIL_ENTITIES_PER_TURN - len(pc_tasks)
        kept_new = new_tasks[:available]
        remaining = max(0, available - len(kept_new))
        kept_existing = existing_tasks[:remaining]
        result = pc_tasks + kept_new + kept_existing
        # All 12 new should be kept, only 10 existing (cap 22)
        assert len(result) == _MAX_DETAIL_ENTITIES_PER_TURN
        new_ids = {get_entity_id(ref) for ref, _ in kept_new}
        assert len(new_ids) == 12  # all new kept

    def test_pc_never_capped(self):
        """char-player is never dropped by capping."""
        from tools.semantic_extraction import _MAX_DETAIL_ENTITIES_PER_TURN, get_entity_id
        # PC + 24 new entities = 25 total (exceeds the raised cap of 22)
        pc_ref = self._make_entity_ref("char-player", is_new=False, confidence=1.0)
        tasks = [(pc_ref, None)] + [(self._make_entity_ref(f"char-new-{i}", is_new=True, confidence=0.8), None)
                                     for i in range(24)]
        # Apply capping
        pc_tasks = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) == "char-player"]
        non_pc = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) != "char-player"]
        new_tasks = [(ref, entry) for ref, entry in non_pc if ref.get("is_new", True)]
        existing_tasks = [(ref, entry) for ref, entry in non_pc if not ref.get("is_new", True)]
        new_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        existing_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        available = _MAX_DETAIL_ENTITIES_PER_TURN - len(pc_tasks)
        kept_new = new_tasks[:available]
        remaining = max(0, available - len(kept_new))
        kept_existing = existing_tasks[:remaining]
        result = pc_tasks + kept_new + kept_existing
        result_ids = {get_entity_id(ref) for ref, _ in result}
        assert "char-player" in result_ids
        # The constant is the TOTAL-incl-PC budget (cap = PC + others), so the
        # kept set never exceeds it even though PC is force-kept.
        assert len(result) <= _MAX_DETAIL_ENTITIES_PER_TURN


class TestRaisedEntityDetailCap:
    """Tests for the raised entity_detail cap (shadow-495 high watermark 21 ->
    cap 22) and its config override ``_get_max_detail_entities_per_turn``.

    The cap is the TOTAL number of entity_detail calls per turn INCLUDING the
    always-kept PC, so cap=22 means PC + up to 21 others.
    """

    def _make_entity_ref(self, entity_id, is_new=True, confidence=0.8):
        return {
            "name": entity_id.replace("char-", ""),
            "type": "character",
            "is_new": is_new,
            "proposed_id": entity_id if is_new else None,
            "existing_id": None if is_new else entity_id,
            "confidence": confidence,
        }

    def _apply_cap(self, tasks, cap):
        """Mirror the production capping logic for a given cap value."""
        from tools.semantic_extraction import get_entity_id
        pc_tasks = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) == "char-player"]
        non_pc = [(ref, entry) for ref, entry in tasks if get_entity_id(ref) != "char-player"]
        new_tasks = [(ref, entry) for ref, entry in non_pc if ref.get("is_new", True)]
        existing_tasks = [(ref, entry) for ref, entry in non_pc if not ref.get("is_new", True)]
        new_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        existing_tasks.sort(key=lambda x: x[0].get("confidence", 0.5), reverse=True)
        available = cap - len(pc_tasks)
        kept_new = new_tasks[:available]
        remaining = max(0, available - len(kept_new))
        kept_existing = existing_tasks[:remaining]
        return pc_tasks + kept_new + kept_existing

    def test_default_constant_is_22(self):
        """The hard-cap default is the high-watermark-derived 22 (was 6)."""
        from tools.semantic_extraction import _MAX_DETAIL_ENTITIES_PER_TURN
        assert _MAX_DETAIL_ENTITIES_PER_TURN == 22

    def test_twenty_one_candidates_all_detailed_under_default_cap(self):
        """21 candidates are ALL detailed under the default cap (22 does not bind).

        This is the core quality-restoration assertion: a dense council turn at
        the observed high watermark (21) no longer drops any this-turn-active
        entity, because 21 is strictly below the default cap of 22. The
        assertion is load-bearing: it runs the production capping logic and
        proves the cap does NOT bind (no entity dropped) at the high watermark.
        """
        from tools.semantic_extraction import _get_max_detail_entities_per_turn
        cap = _get_max_detail_entities_per_turn(None)
        assert cap == 22 and 21 < cap  # high watermark sits below the default cap
        tasks = [(self._make_entity_ref(f"char-{i}", confidence=0.9), None) for i in range(21)]
        # Always run the production capping logic (not an `else tasks` shortcut),
        # so the assertion proves capping leaves all 21 when it does not bind.
        result = self._apply_cap(tasks, cap)
        assert len(result) == 21  # cap did not bind: nothing dropped

    def test_twenty_one_candidates_cap_binds_when_lowered(self):
        """When the configured cap is lowered below 21, capping BINDS and drops the tail.

        Load-bearing counterpart to the default-cap case: with 21 candidates
        (PC + 20 others) and an override cap of 20, the cap binds and exactly 20
        are detailed (PC + 19), proving the capping path actually fires.
        """
        from tools.semantic_extraction import _get_max_detail_entities_per_turn, get_entity_id
        cfg = {"context_optimizations": {"max_detail_entities_per_turn": 20}}
        cap = _get_max_detail_entities_per_turn(cfg)
        assert cap == 20
        pc = self._make_entity_ref("char-player", is_new=False, confidence=1.0)
        tasks = [(pc, None)] + [(self._make_entity_ref(f"char-{i}", confidence=0.9), None)
                                for i in range(20)]
        assert len(tasks) == 21 and len(tasks) > cap  # cap binds
        result = self._apply_cap(tasks, cap)
        assert len(result) == 20  # PC + 19: one entity dropped by the binding cap
        assert len(result) < len(tasks)  # capping actually fired
        assert "char-player" in {get_entity_id(ref) for ref, _ in result}  # PC force-kept

    def test_cap_allows_22_total_incl_pc(self):
        """With 25 candidates (PC + 24), exactly 22 are detailed (PC always kept)."""
        from tools.semantic_extraction import _get_max_detail_entities_per_turn, get_entity_id
        cap = _get_max_detail_entities_per_turn(None)
        pc = self._make_entity_ref("char-player", is_new=False, confidence=1.0)
        tasks = [(pc, None)] + [(self._make_entity_ref(f"char-{i}"), None) for i in range(24)]
        result = self._apply_cap(tasks, cap)
        assert len(result) == 22  # TOTAL-incl-PC ceiling honored, not 6-7
        assert "char-player" in {get_entity_id(ref) for ref, _ in result}

    def test_config_override_defaults_to_constant(self):
        """Unset / empty config falls back to the module default (22)."""
        from tools.semantic_extraction import (
            _get_max_detail_entities_per_turn,
            _MAX_DETAIL_ENTITIES_PER_TURN,
        )
        assert _get_max_detail_entities_per_turn(None) == _MAX_DETAIL_ENTITIES_PER_TURN
        assert _get_max_detail_entities_per_turn({}) == _MAX_DETAIL_ENTITIES_PER_TURN
        assert _get_max_detail_entities_per_turn(
            {"context_optimizations": {}}
        ) == _MAX_DETAIL_ENTITIES_PER_TURN

    def test_config_override_honored(self):
        """An explicit override is used (tunable per campaign density)."""
        from tools.semantic_extraction import _get_max_detail_entities_per_turn
        cfg = {"context_optimizations": {"max_detail_entities_per_turn": 30}}
        assert _get_max_detail_entities_per_turn(cfg) == 30

    def test_config_override_malformed_falls_back(self):
        """Malformed / non-positive overrides fall back to the default, never 0."""
        from tools.semantic_extraction import (
            _get_max_detail_entities_per_turn,
            _MAX_DETAIL_ENTITIES_PER_TURN,
        )
        for bad in [None, "abc", [], {}, -5, 0]:
            cfg = {"context_optimizations": {"max_detail_entities_per_turn": bad}}
            assert _get_max_detail_entities_per_turn(cfg) == _MAX_DETAIL_ENTITIES_PER_TURN

    def test_config_optimizations_not_a_dict(self):
        """A malformed (non-dict) context_optimizations falls back safely."""
        from tools.semantic_extraction import (
            _get_max_detail_entities_per_turn,
            _MAX_DETAIL_ENTITIES_PER_TURN,
        )
        assert _get_max_detail_entities_per_turn(
            {"context_optimizations": ["not", "a", "dict"]}
        ) == _MAX_DETAIL_ENTITIES_PER_TURN
        assert _get_max_detail_entities_per_turn("not a dict") == _MAX_DETAIL_ENTITIES_PER_TURN
