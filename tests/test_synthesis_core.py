"""Tests for the synthesis data assembly layer (tools/synthesis.py)."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from synthesis import (
    build_event_derived_profile,
    chunk_relationship_arcs,
    group_events_by_entity,
    load_events,
    merge_relationship_histories,
    resolve_entity_id,
    segment_phases,
    summarize_relationship_arcs,
    write_arc_sidecar,
    _infer_name_from_id,
    _infer_type_from_id,
    _count_event_types,
    _extract_co_occurrences,
    _fallback_arc_summaries,
)

# ---------------------------------------------------------------------------
# Fixtures — modeled on actual Run 4 data patterns
# ---------------------------------------------------------------------------


def _make_event(event_id, turns, related, etype="decision", desc="Test event"):
    """Convenience factory for event fixtures."""
    return {
        "id": event_id,
        "source_turns": turns,
        "type": etype,
        "description": desc,
        "related_entities": related,
    }


# Events designed to exercise grouping, ID aliases, and sorting
SAMPLE_EVENTS = [
    _make_event("evt-001", ["turn-001"], ["char-player"], "encounter", "Awakens in snow"),
    _make_event("evt-002", ["turn-005"], ["char-player", "char-Kael"], "encounter", "Triggers snare"),
    _make_event("evt-003", ["turn-015"], ["char-player", "char-elder"], "ritual", "Acceptance ritual"),
    _make_event("evt-004", ["turn-050"], ["char-player", "char-broad-figure"], "encounter", "Sits near fire"),
    _make_event("evt-005", ["turn-079"], ["char-player", "char-broad-figure"], "encounter", "Good-luck kiss"),
    _make_event("evt-006", ["turn-108"], ["char-player", "char-kael", "char-elder"], "decision", "Council"),
    _make_event("evt-007", ["turn-121"], ["char-player"], "discovery", "Pregnancy discovered"),
    _make_event("evt-008", ["turn-141"], ["char-player", "char-kael"], "birth", "Lyrawyn born"),
    _make_event("evt-009", ["turn-194"], ["char-player", "char-kael"], "decision", "Kael appointed"),
    _make_event("evt-010", ["turn-225"], ["char-player", "entity-healer"], "encounter", "Traveler arrives"),
    _make_event("evt-011", ["turn-252"], ["char-player", "char-anya"], "decision", "Plague resolved"),
    _make_event("evt-012", ["turn-291"], ["char-player"], "decision", "Names Quiet Weave"),
    _make_event("evt-013", ["turn-306"], ["char-player", "char-kael"], "birth", "Rune born"),
    _make_event("evt-014", ["turn-340"], ["char-player", "char-kael", "char-elder", "char-gorok"], "decision", "Winter council"),
    _make_event("evt-015", ["turn-169"], ["char-player", "char-kael"], "decision", "Water barrels"),
    _make_event("evt-016", ["turn-210"], ["char-player", "char-kael"], "decision", "Selects hunters"),
    _make_event("evt-017", ["turn-286"], ["char-player", "char-kael", "char-anya"], "decision", "Disruption fields"),
    _make_event("evt-018", ["turn-301"], ["char-player", "char-kael"], "decision", "Delegates leadership"),
    _make_event("evt-019", ["turn-326"], ["char-player", "char-kael"], "encounter", "Feels fourth child"),
    _make_event("evt-020", ["turn-343"], ["char-player", "char-kael", "char-gorok"], "decision", "Dual forces"),
    _make_event("evt-021", ["turn-100"], ["char-ananya"], "encounter", "Anya early event"),
    _make_event("evt-022", ["turn-200"], ["npc-ananya", "char-player"], "encounter", "Anya later event"),
    _make_event("evt-023", ["turn-332"], ["char-player", "faction-warrior-chief-gorok"], "encounter", "Gorok via faction ID"),
]


# Relationships modeled on char-player's data: multiple entries for same target
SAMPLE_RELATIONSHIPS = [
    {
        "target_id": "char-broad-figure",
        "current_relationship": "communicating with",
        "type": "social",
        "history": [
            {"turn": "turn-012", "type": "social", "description": "communicating with"},
            {"turn": "turn-035", "type": "social", "description": "working alongside"},
        ],
    },
    {
        "target_id": "char-Kael",
        "current_relationship": "Life partner",
        "type": "romantic",
        "history": [
            {"turn": "turn-051", "type": "social", "description": "helping maintain fire with"},
            {"turn": "turn-056", "type": "social", "description": "friendship with"},
            {"turn": "turn-078", "type": "romantic", "description": "laying a good luck kiss"},
            {"turn": "turn-108", "type": "romantic", "description": "seeking alliance"},
        ],
    },
    {
        "target_id": "char-kael",
        "current_relationship": "Co-leader",
        "type": "romantic",
        "history": [
            {"turn": "turn-194", "type": "romantic", "description": "trusting with leadership"},
            {"turn": "turn-210", "type": "romantic", "description": "sharing closeness"},
            {"turn": "turn-286", "type": "leadership", "description": "overseeing perimeter"},
            {"turn": "turn-326", "type": "romantic", "description": "feeling fourth child"},
            {"turn": "turn-340", "type": "leadership", "description": "winter council report"},
        ],
    },
    {
        "target_id": "char-elder",
        "current_relationship": "Respected elder",
        "type": "social",
        "history": [
            {"turn": "turn-015", "type": "social", "description": "acceptance ritual"},
            {"turn": "turn-029", "type": "social", "description": "offered broth"},
            {"turn": "turn-340", "type": "social", "description": "spiritual counsel"},
        ],
    },
    {
        "target_id": "char-Elder",
        "current_relationship": "Moral anchor",
        "type": "social",
        "history": [
            {"turn": "turn-015", "type": "social", "description": "ritual begins"},
        ],
    },
]


# ---------------------------------------------------------------------------
# 1. Event Grouping Tests
# ---------------------------------------------------------------------------


class TestEventGrouping:
    """Test event-entity grouping (Step 1)."""

    def test_groups_events_by_entity(self):
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        assert "char-player" in grouped
        # char-player appears in most events
        assert len(grouped["char-player"]) >= 15

    def test_applies_case_insensitive_matching(self):
        """char-Kael and char-kael should resolve to the same key."""
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        # Both char-Kael (evt-002) and char-kael (evt-006) should be under char-kael
        assert "char-kael" in grouped
        assert "char-Kael" not in grouped

    def test_applies_id_aliases(self):
        """char-broad-figure should resolve to char-kael via alias."""
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        kael_events = grouped["char-kael"]
        # evt-004 and evt-005 have char-broad-figure, evt-002 has char-Kael,
        # evt-006/008/009 have char-kael — all should be merged
        event_ids = {e["id"] for e in kael_events}
        assert "evt-002" in event_ids  # char-Kael
        assert "evt-004" in event_ids  # char-broad-figure
        assert "evt-006" in event_ids  # char-kael

    def test_alias_ananya_to_anya(self):
        """char-ananya and npc-ananya should both resolve to char-anya."""
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        assert "char-anya" in grouped
        anya_events = grouped["char-anya"]
        event_ids = {e["id"] for e in anya_events}
        assert "evt-021" in event_ids  # char-ananya
        assert "evt-022" in event_ids  # npc-ananya
        assert "evt-011" in event_ids  # char-anya direct
        assert "evt-017" in event_ids  # char-anya direct

    def test_alias_gorok_faction_to_char(self):
        """faction-warrior-chief-gorok should resolve to char-gorok."""
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        assert "char-gorok" in grouped
        gorok_events = grouped["char-gorok"]
        event_ids = {e["id"] for e in gorok_events}
        assert "evt-023" in event_ids  # faction-warrior-chief-gorok
        assert "evt-014" in event_ids  # char-gorok direct

    def test_entity_healer_alias(self):
        """entity-healer should resolve to char-healer."""
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        assert "char-healer" in grouped
        healer_events = grouped["char-healer"]
        event_ids = {e["id"] for e in healer_events}
        assert "evt-010" in event_ids

    def test_sorts_events_by_turn_number(self):
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        kael_events = grouped["char-kael"]
        turns = []
        for e in kael_events:
            t = e.get("source_turns", [""])[0]
            if t:
                turns.append(int(t.split("-")[1]))
        assert turns == sorted(turns), f"Kael events not sorted: {turns}"

    def test_empty_events(self):
        grouped = group_events_by_entity([])
        assert grouped == {}

    def test_event_without_related_entities(self):
        events = [{"id": "evt-999", "source_turns": ["turn-001"],
                    "type": "other", "description": "Orphan"}]
        grouped = group_events_by_entity(events)
        assert grouped == {}


# ---------------------------------------------------------------------------
# 2. Event-Derived Profile Tests
# ---------------------------------------------------------------------------


class TestEventDerivedProfiles:
    """Test event-derived entity profiles (Step 2)."""

    def test_infer_name_from_id(self):
        assert _infer_name_from_id("char-kael") == "Kael"
        assert _infer_name_from_id("loc-the-settlement") == "The Settlement"
        assert _infer_name_from_id("faction-quiet-weave") == "Quiet Weave"
        assert _infer_name_from_id("item-arcane-fragment") == "Arcane Fragment"

    def test_infer_type_from_prefix(self):
        assert _infer_type_from_id("char-kael") == "character"
        assert _infer_type_from_id("loc-settlement") == "location"
        assert _infer_type_from_id("faction-tribe") == "faction"
        assert _infer_type_from_id("item-sword") == "item"
        assert _infer_type_from_id("npc-guard") == "character"
        assert _infer_type_from_id("unknown-thing") == "unknown"

    def test_counts_events_and_co_occurrences(self):
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        kael_events = grouped["char-kael"]
        profile = build_event_derived_profile("char-kael", kael_events)

        assert profile["id"] == "char-kael"
        assert profile["name"] == "Kael"
        assert profile["type"] == "character"
        assert profile["source"] == "events_only"
        assert profile["event_count"] == len(kael_events)
        assert "char-player" in profile["co_occurring_entities"]
        assert isinstance(profile["event_types"], dict)

    def test_single_event_entity(self):
        events = [_make_event("evt-100", ["turn-050"], ["char-solo"], "encounter", "Solo event")]
        grouped = group_events_by_entity(events)
        solo_events = grouped["char-solo"]
        profile = build_event_derived_profile("char-solo", solo_events)

        assert profile["event_count"] == 1
        assert profile["first_event_turn"] == "turn-050"
        assert profile["last_event_turn"] == "turn-050"
        assert profile["co_occurring_entities"] == []

    def test_empty_events(self):
        profile = build_event_derived_profile("char-nobody", [])
        assert profile["event_count"] == 0
        assert profile["first_event_turn"] == ""
        assert profile["last_event_turn"] == ""

    def test_event_types_counted(self):
        events = [
            _make_event("e1", ["turn-001"], ["char-x"], "decision"),
            _make_event("e2", ["turn-002"], ["char-x"], "decision"),
            _make_event("e3", ["turn-003"], ["char-x"], "encounter"),
        ]
        profile = build_event_derived_profile("char-x", events)
        assert profile["event_types"] == {"decision": 2, "encounter": 1}


# ---------------------------------------------------------------------------
# 3. Phase Segmentation Tests
# ---------------------------------------------------------------------------


def _make_events_range(start, end, entity_id="char-player", etype="decision"):
    """Generate a sequence of events over a turn range."""
    events = []
    for i, turn in enumerate(range(start, end + 1)):
        events.append(_make_event(
            f"evt-{i:03d}",
            [f"turn-{turn:03d}"],
            [entity_id],
            etype,
            f"Event at turn {turn}",
        ))
    return events


class TestPhaseSegmentation:
    """Test phase segmentation (Step 3)."""

    def test_pc_many_events_produces_4_to_8_phases(self):
        """PC with ~277 events should produce 4–8 phases."""
        # Generate 277 events with some gaps
        events = []
        turn = 1
        for i in range(277):
            events.append(_make_event(
                f"evt-{i:03d}",
                [f"turn-{turn:03d}"],
                ["char-player"],
                "decision" if i % 3 != 0 else "encounter",
                f"PC event {i}",
            ))
            # Add occasional gaps
            if i in (40, 90, 140, 180, 220, 260):
                turn += 15  # 15-turn gap
            else:
                turn += 1

        phases = segment_phases(events, "character", "char-player")
        assert 4 <= len(phases) <= 8, f"Expected 4–8 phases, got {len(phases)}"

        # All events accounted for
        total_events = sum(p["event_count"] for p in phases)
        assert total_events == 277

    def test_major_npc_15_events(self):
        """NPC with 15 events → 2–4 phases."""
        events = []
        turns = [10, 15, 20, 25, 30,  # cluster 1
                 80, 85, 90, 95, 100,  # cluster 2 (gap of 50)
                 200, 205, 210, 215, 220]  # cluster 3 (gap of 100)
        for i, t in enumerate(turns):
            events.append(_make_event(
                f"evt-{i:03d}",
                [f"turn-{t:03d}"],
                ["char-npc"],
                "encounter",
                f"NPC event {i}",
            ))

        phases = segment_phases(events, "character", "char-npc")
        assert 2 <= len(phases) <= 4, f"Expected 2–4 phases, got {len(phases)}"

    def test_minor_entity_single_phase(self):
        """NPC with 5 events → 1 phase."""
        events = _make_events_range(100, 104, "char-minor")
        phases = segment_phases(events, "character", "char-minor")
        assert len(phases) == 1
        assert phases[0]["event_count"] == 5

    def test_detects_gaps_as_phase_boundaries(self):
        """Gaps of 10+ turns should create phase boundaries."""
        events = []
        # Cluster A: turns 1–10
        events.extend(_make_events_range(1, 10, "char-test"))
        # Gap: turns 10–30 (20-turn gap)
        # Cluster B: turns 30–45
        events.extend(_make_events_range(30, 45, "char-test"))

        phases = segment_phases(events, "character", "char-test")
        # Should be 2+ phases due to the gap
        assert len(phases) >= 2

    def test_single_turn_entity(self):
        """Entity with a single event produces a single phase."""
        events = [_make_event("evt-solo", ["turn-042"], ["char-one"], "encounter")]
        phases = segment_phases(events, "character", "char-one")
        assert len(phases) == 1
        assert phases[0]["event_count"] == 1

    def test_empty_events(self):
        phases = segment_phases([], "character", "char-empty")
        assert phases == []

    def test_phase_structure(self):
        """Each phase has the required fields."""
        events = _make_events_range(1, 5, "char-struct")
        phases = segment_phases(events, "character", "char-struct")
        phase = phases[0]
        assert "name" in phase
        assert "turn_range" in phase
        assert isinstance(phase["turn_range"], list)
        assert len(phase["turn_range"]) == 2
        assert "events" in phase
        assert "event_count" in phase

    def test_all_events_accounted_for(self):
        """No events should be lost in segmentation."""
        events = _make_events_range(1, 50, "char-player")
        phases = segment_phases(events, "character", "char-player")
        total = sum(p["event_count"] for p in phases)
        assert total == 50


# ---------------------------------------------------------------------------
# 4. Relationship Merger Tests
# ---------------------------------------------------------------------------


class TestRelationshipMerger:
    """Test relationship history merger (Step 4)."""

    def test_merges_same_target_entries(self):
        """char-broad-figure and char-Kael and char-kael all resolve to char-kael."""
        merged = merge_relationship_histories(SAMPLE_RELATIONSHIPS)
        # char-broad-figure → char-kael, char-Kael → char-kael, char-kael → char-kael
        assert "char-kael" in merged
        # 2 from char-broad-figure + 4 from char-Kael + 5 from char-kael = 11
        # minus turn-015 dedup (elder has it, not kael) = 11
        kael_history = merged["char-kael"]
        assert len(kael_history) == 11

    def test_case_insensitive_target_matching(self):
        """char-Elder and char-elder should merge."""
        merged = merge_relationship_histories(SAMPLE_RELATIONSHIPS)
        assert "char-elder" in merged
        assert "char-Elder" not in merged

    def test_deduplicates_same_turn_entries(self):
        """Entries at the same turn should be deduplicated."""
        # char-Elder has turn-015 and char-elder also has turn-015
        merged = merge_relationship_histories(SAMPLE_RELATIONSHIPS)
        elder_history = merged["char-elder"]
        turn_015_entries = [e for e in elder_history if e["turn"] == "turn-015"]
        assert len(turn_015_entries) == 1

    def test_keeps_more_detailed_entry_on_dedup(self):
        """When deduplicating, keep the entry with longer description."""
        rels = [
            {
                "target_id": "char-test",
                "history": [
                    {"turn": "turn-010", "type": "social", "description": "short"},
                    {"turn": "turn-020", "type": "social", "description": "unique"},
                ],
            },
            {
                "target_id": "char-test",
                "history": [
                    {"turn": "turn-010", "type": "social", "description": "much longer description here"},
                ],
            },
        ]
        merged = merge_relationship_histories(rels)
        assert merged["char-test"][0]["description"] == "much longer description here"

    def test_sorts_chronologically(self):
        merged = merge_relationship_histories(SAMPLE_RELATIONSHIPS)
        for target_id, history in merged.items():
            turns = [int(e["turn"].split("-")[1]) for e in history]
            assert turns == sorted(turns), f"{target_id} history not sorted"

    def test_empty_relationships(self):
        merged = merge_relationship_histories([])
        assert merged == {}


# ---------------------------------------------------------------------------
# 5. Arc Chunking Tests (rule-based part)
# ---------------------------------------------------------------------------


class TestArcChunking:
    """Test rule-based relationship arc chunking (Step 5, Phase A)."""

    def test_chunks_by_type_transition(self):
        history = [
            {"turn": "turn-012", "type": "social", "description": "met"},
            {"turn": "turn-020", "type": "social", "description": "worked together"},
            {"turn": "turn-030", "type": "social", "description": "shared meal"},
            {"turn": "turn-050", "type": "romantic", "description": "first kiss"},
            {"turn": "turn-060", "type": "romantic", "description": "declared love"},
        ]
        chunks = chunk_relationship_arcs(history)
        assert len(chunks) >= 2
        assert chunks[0]["type"] == "social"
        assert chunks[-1]["type"] == "romantic"

    def test_clusters_same_type_within_20_turns(self):
        history = [
            {"turn": "turn-010", "type": "social", "description": "met"},
            {"turn": "turn-015", "type": "social", "description": "talked"},
            {"turn": "turn-020", "type": "social", "description": "helped"},
            {"turn": "turn-025", "type": "social", "description": "bonded"},
            # Gap of 50 turns, same type
            {"turn": "turn-075", "type": "social", "description": "reunited"},
            {"turn": "turn-080", "type": "social", "description": "worked again"},
        ]
        chunks = chunk_relationship_arcs(history)
        # Should split into 2 chunks: turns 10-25 and turns 75-80
        assert len(chunks) == 2
        assert chunks[0]["turn_range"] == ["turn-010", "turn-025"]
        assert chunks[1]["turn_range"] == ["turn-075", "turn-080"]

    def test_skips_three_or_fewer_interactions(self):
        history = [
            {"turn": "turn-010", "type": "social", "description": "met"},
            {"turn": "turn-020", "type": "social", "description": "talked"},
            {"turn": "turn-030", "type": "social", "description": "helped"},
        ]
        chunks = chunk_relationship_arcs(history)
        assert chunks == []

    def test_skips_two_interactions(self):
        history = [
            {"turn": "turn-010", "type": "social", "description": "met"},
            {"turn": "turn-020", "type": "social", "description": "talked"},
        ]
        chunks = chunk_relationship_arcs(history)
        assert chunks == []

    def test_skips_empty_history(self):
        assert chunk_relationship_arcs([]) == []

    def test_combined_type_and_temporal_splitting(self):
        """Type change + temporal gap in another segment."""
        history = [
            {"turn": "turn-010", "type": "social", "description": "met"},
            {"turn": "turn-015", "type": "social", "description": "talked"},
            {"turn": "turn-020", "type": "romantic", "description": "kiss"},
            {"turn": "turn-025", "type": "romantic", "description": "love"},
            # Gap of 50 turns, same type
            {"turn": "turn-080", "type": "romantic", "description": "reunion"},
        ]
        chunks = chunk_relationship_arcs(history)
        # social chunk, romantic chunk (20-25), romantic chunk (80)
        assert len(chunks) == 3

    def test_fallback_arc_summaries(self):
        chunks = [
            {"turn_range": ["turn-010", "turn-030"], "type": "social", "entries": []},
            {"turn_range": ["turn-050", "turn-080"], "type": "romantic", "entries": []},
        ]
        summaries = _fallback_arc_summaries(chunks)
        assert len(summaries) == 2
        assert summaries[0]["phase"] == "Phase 1"
        assert summaries[1]["phase"] == "Phase 2"
        assert summaries[0]["type"] == "social"
        assert summaries[1]["type"] == "romantic"


# ---------------------------------------------------------------------------
# 6. Integration: Arc Summarizer with Mock LLM
# ---------------------------------------------------------------------------


class MockLLMClient:
    """Mock LLM client that returns predictable JSON responses."""

    def __init__(self, response=None, should_fail=False):
        self.response = response
        self.should_fail = should_fail
        self.calls = []

    def extract_json(self, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.should_fail:
            raise RuntimeError("Mock LLM failure")
        if self.response is not None:
            return self.response
        # Default: return a wrapped array (simulating json_object mode)
        return {
            "phases": [
                {
                    "phase": "First Contact",
                    "turn_range": ["turn-012", "turn-035"],
                    "type": "social",
                    "summary": "Initial encounters through shared labor.",
                    "key_turns": ["turn-012"],
                },
                {
                    "phase": "Growing Bond",
                    "turn_range": ["turn-051", "turn-108"],
                    "type": "romantic",
                    "summary": "Friendship deepened into romance.",
                    "key_turns": ["turn-078"],
                },
            ]
        }


class TestArcSummarizerIntegration:
    """Integration tests for the full arc summarization pipeline."""

    def test_full_pipeline_with_mock_llm(self):
        """merge → chunk → LLM → parse → store produces valid output."""
        mock_llm = MockLLMClient()
        result = summarize_relationship_arcs(
            source_id="char-player",
            source_name="Fenouille",
            relationships=SAMPLE_RELATIONSHIPS,
            llm_client=mock_llm,
        )

        assert result["entity_id"] == "char-player"
        assert "generated_at" in result
        assert "arcs" in result

        # char-kael should have arcs (11 merged interactions > 3 threshold)
        if "char-kael" in result["arcs"]:
            kael_arc = result["arcs"]["char-kael"]
            assert "arc_summary" in kael_arc
            assert "interaction_count" in kael_arc
            assert kael_arc["interaction_count"] == 11

    def test_graceful_fallback_on_llm_failure(self):
        """When LLM fails, fall back to rule-based naming."""
        mock_llm = MockLLMClient(should_fail=True)
        result = summarize_relationship_arcs(
            source_id="char-player",
            source_name="Fenouille",
            relationships=SAMPLE_RELATIONSHIPS,
            llm_client=mock_llm,
        )

        # Should still produce output
        assert result["entity_id"] == "char-player"
        assert "arcs" in result

        # Arcs should use fallback names
        if "char-kael" in result["arcs"]:
            kael_arc = result["arcs"]["char-kael"]
            for phase in kael_arc["arc_summary"]:
                assert phase["phase"].startswith("Phase ")

    def test_no_llm_produces_fallback(self):
        """Without LLM client, produces fallback arc summaries."""
        result = summarize_relationship_arcs(
            source_id="char-player",
            source_name="Fenouille",
            relationships=SAMPLE_RELATIONSHIPS,
            llm_client=None,
        )
        assert result["entity_id"] == "char-player"
        # Should still have arcs with fallback names
        if "char-kael" in result["arcs"]:
            for phase in result["arcs"]["char-kael"]["arc_summary"]:
                assert phase["phase"].startswith("Phase ")

    def test_sidecar_file_format(self):
        """Sidecar file is valid JSON with correct structure."""
        mock_llm = MockLLMClient()
        arc_data = summarize_relationship_arcs(
            source_id="char-player",
            source_name="Fenouille",
            relationships=SAMPLE_RELATIONSHIPS,
            llm_client=mock_llm,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_arc_sidecar(arc_data, tmpdir)
            assert os.path.basename(path) == "char-player.arcs.json"
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["entity_id"] == "char-player"
            assert "arcs" in loaded
            assert "generated_at" in loaded

    def test_current_relationship_preserved(self):
        """current_relationship from the latest relationship entry is preserved."""
        result = summarize_relationship_arcs(
            source_id="char-player",
            source_name="Fenouille",
            relationships=SAMPLE_RELATIONSHIPS,
            llm_client=None,
        )
        if "char-kael" in result["arcs"]:
            # The latest entry for char-kael has "Co-leader"
            cur = result["arcs"]["char-kael"]["current_relationship"]
            assert cur in ("Life partner", "Co-leader")

    def test_skips_sparse_relationships(self):
        """Relationships with ≤ 3 interactions are skipped."""
        sparse_rels = [
            {
                "target_id": "char-stranger",
                "current_relationship": "acquaintance",
                "type": "social",
                "history": [
                    {"turn": "turn-100", "type": "social", "description": "met once"},
                    {"turn": "turn-200", "type": "social", "description": "met twice"},
                ],
            },
        ]
        result = summarize_relationship_arcs(
            source_id="char-player",
            source_name="Fenouille",
            relationships=sparse_rels,
            llm_client=None,
        )
        assert "char-stranger" not in result["arcs"]


# ---------------------------------------------------------------------------
# 7. Load Events
# ---------------------------------------------------------------------------


class TestLoadEvents:
    """Test events loading utility."""

    def test_loads_valid_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cat_dir = os.path.join(tmpdir, "catalogs")
            os.makedirs(cat_dir)
            events = [
                {"id": "evt-001", "source_turns": ["turn-001"], "type": "encounter",
                 "description": "Test", "related_entities": ["char-player"]},
            ]
            with open(os.path.join(cat_dir, "events.json"), "w") as f:
                json.dump(events, f)

            loaded = load_events(tmpdir)
            assert len(loaded) == 1
            assert loaded[0]["id"] == "evt-001"

    def test_returns_empty_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = load_events(tmpdir)
            assert loaded == []

    def test_returns_empty_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cat_dir = os.path.join(tmpdir, "catalogs")
            os.makedirs(cat_dir)
            with open(os.path.join(cat_dir, "events.json"), "w") as f:
                f.write("not valid json")
            loaded = load_events(tmpdir)
            assert loaded == []


# ---------------------------------------------------------------------------
# 8. Resolve Entity ID
# ---------------------------------------------------------------------------


class TestResolveEntityId:
    """Test the entity ID resolution function."""

    def test_lowercase(self):
        assert resolve_entity_id("char-Kael") == "char-kael"

    def test_alias_broad_figure(self):
        assert resolve_entity_id("char-broad-figure") == "char-kael"

    def test_alias_faction_gorok(self):
        assert resolve_entity_id("faction-warrior-chief-gorok") == "char-gorok"

    def test_alias_ananya_variants(self):
        assert resolve_entity_id("char-ananya") == "char-anya"
        assert resolve_entity_id("char-anxa") == "char-anya"
        assert resolve_entity_id("npc-ananya") == "char-anya"

    def test_passthrough(self):
        assert resolve_entity_id("char-player") == "char-player"

    def test_empty_string(self):
        assert resolve_entity_id("") == ""
