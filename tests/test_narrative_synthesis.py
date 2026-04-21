"""Tests for the LLM narrative synthesis pipeline (tools/narrative_synthesis.py)."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from narrative_synthesis import (
    assemble_synthesis_input,
    assemble_lede_input,
    assemble_location_input,
    assemble_faction_input,
    assemble_item_input,
    assemble_character_page,
    assemble_location_page,
    assemble_faction_page,
    assemble_item_page,
    extract_cited_turns,
    validate_provenance,
    add_provenance_warning,
    build_synthesis_sidecar,
    write_synthesis_sidecar,
    load_synthesis_sidecar,
    should_synthesize,
    generate_phase_biography,
    generate_lede,
    synthesize_entity,
    needs_regeneration,
    _format_events_for_prompt,
    _format_catalog_section,
    _format_arc_section,
    _build_infobox,
    _build_event_timeline,
    _build_current_status,
    _parse_biography_response,
    _normalize_subheadings,
)

from narrative_synthesis import _collect_critical_turns

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(event_id, turns, related, etype="decision", desc="Test event"):
    return {
        "id": event_id,
        "source_turns": turns,
        "type": etype,
        "description": desc,
        "related_entities": related,
    }


SAMPLE_EVENTS = [
    _make_event("evt-001", ["turn-001"], ["char-player"], "encounter", "Awakens in snow"),
    _make_event("evt-002", ["turn-005"], ["char-player", "char-kael"], "encounter", "Triggers snare"),
    _make_event("evt-003", ["turn-015"], ["char-player", "char-elder"], "ritual", "Acceptance ritual"),
    _make_event("evt-004", ["turn-050"], ["char-player", "char-kael"], "encounter", "Sits near fire"),
    _make_event("evt-005", ["turn-079"], ["char-player", "char-kael"], "encounter", "Good-luck kiss"),
    _make_event("evt-006", ["turn-108"], ["char-player", "char-kael", "char-elder"], "decision", "Council"),
    _make_event("evt-007", ["turn-121"], ["char-player"], "discovery", "Pregnancy discovered"),
    _make_event("evt-008", ["turn-141"], ["char-player", "char-kael"], "birth", "Lyrawyn born"),
]

SAMPLE_CATALOG = {
    "id": "char-player",
    "name": "Fenouille Moonwind",
    "type": "character",
    "identity": "Elven warlock who awakened in the snow",
    "first_seen_turn": "turn-001",
    "last_updated_turn": "turn-141",
    "current_status": "Leading the tribe",
    "status_updated_turn": "turn-141",
    "stable_attributes": {
        "race": {"value": "Elf", "source_turn": "turn-001", "confidence": 1.0},
        "class": {"value": "Warlock", "source_turn": "turn-001", "confidence": 0.9},
    },
    "volatile_state": {
        "condition": "healthy",
        "location": "the encampment",
    },
    "relationships": [
        {
            "target_id": "char-kael",
            "type": "romantic",
            "current_relationship": "Partner",
            "status": "active",
            "last_updated_turn": "turn-141",
            "history": [
                {"turn": "turn-050", "description": "Shared fire"},
                {"turn": "turn-079", "description": "Good-luck kiss"},
                {"turn": "turn-141", "description": "Birth of child"},
            ],
        }
    ],
}

SAMPLE_ARC_SUMMARIES = {
    "entity_id": "char-player",
    "generated_at": "2026-04-15T00:00:00Z",
    "arcs": {
        "char-kael": {
            "arc_summary": [
                {
                    "phase": "Early Bond",
                    "turn_range": ["turn-050", "turn-079"],
                    "type": "romantic",
                    "summary": "Bond deepened from shared fire to affection.",
                    "key_turns": ["turn-050"],
                },
                {
                    "phase": "Family",
                    "turn_range": ["turn-108", "turn-141"],
                    "type": "romantic",
                    "summary": "Partnership solidified through council and birth.",
                    "key_turns": ["turn-141"],
                },
            ],
            "current_relationship": "Partner",
            "interaction_count": 5,
        }
    },
}


class MockLLMClient:
    """Mock LLM client that returns predictable text with turn citations."""

    def __init__(self, response_text=None):
        self.response_text = response_text or (
            "The character awakened in the snow [turn-001] and triggered a "
            "snare [turn-005]. Through acceptance rituals [turn-015], they "
            "joined the tribe."
        )
        self.model = "mock-model"
        self.calls = []

    def generate_text(self, system_prompt, user_prompt, timeout=None):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        })
        return self.response_text

    def delay(self):
        pass


class FailingLLMClient:
    """Mock LLM client that always raises an exception."""

    model = "failing-model"

    def generate_text(self, system_prompt, user_prompt, timeout=None):
        raise RuntimeError("LLM unavailable")

    def delay(self):
        pass


# ---------------------------------------------------------------------------
# Test input assembly
# ---------------------------------------------------------------------------


class TestInputAssembly:
    """Tests for assemble_synthesis_input and related formatting."""

    def test_full_data_entity(self):
        """Entity with catalog + events + arcs produces complete prompt."""
        phase = {
            "name": "The Awakening",
            "turn_range": ["turn-001", "turn-015"],
            "events": SAMPLE_EVENTS[:3],
            "event_count": 3,
        }

        result = assemble_synthesis_input(
            "char-player", phase, SAMPLE_CATALOG,
            SAMPLE_ARC_SUMMARIES)

        assert "system_prompt" in result
        assert "user_prompt" in result
        assert "events_used" in result
        assert result["events_used"] == ["evt-001", "evt-002", "evt-003"]
        assert result["phase_name"] == "The Awakening"
        assert result["turn_range"] == ["turn-001", "turn-015"]

        prompt = result["user_prompt"]
        assert "Fenouille Moonwind" in prompt
        assert "char-player" in prompt
        assert "Elven warlock" in prompt
        assert "Awakens in snow" in prompt
        assert "Triggers snare" in prompt
        assert "Acceptance ritual" in prompt
        assert "Kael" in prompt  # from arc summaries
        assert "The Awakening" in prompt

    def test_events_only_entity(self):
        """Entity with events only (no catalog) notes absence."""
        phase = {
            "name": None,
            "turn_range": ["turn-050", "turn-108"],
            "events": SAMPLE_EVENTS[3:6],
            "event_count": 3,
        }

        result = assemble_synthesis_input(
            "char-kael", phase, None, None)

        prompt = result["user_prompt"]
        assert "No catalog entry exists" in prompt
        assert "Kael" in prompt
        assert "No relationship arc summaries" in prompt

    def test_single_call_small_entity(self):
        """Entity with < 40 events gets all events in one call."""
        events = SAMPLE_EVENTS[:5]
        phase = {
            "name": "Full Timeline",
            "turn_range": ["turn-001", "turn-079"],
            "events": events,
            "event_count": 5,
        }

        result = assemble_synthesis_input(
            "char-elder", phase, None, None)

        assert len(result["events_used"]) == 5

    def test_available_turns_collected(self):
        """Available turns are collected from all events."""
        phase = {
            "name": "Test",
            "turn_range": ["turn-001", "turn-015"],
            "events": SAMPLE_EVENTS[:3],
            "event_count": 3,
        }

        result = assemble_synthesis_input(
            "char-player", phase, SAMPLE_CATALOG, None)

        assert "turn-001" in result["available_turns"]
        assert "turn-005" in result["available_turns"]
        assert "turn-015" in result["available_turns"]

    def test_lede_input_assembly(self):
        """Lede input combines biography sections."""
        sections = ["Phase 1 text [turn-001].", "Phase 2 text [turn-050]."]
        result = assemble_lede_input("char-player", sections, SAMPLE_CATALOG)

        assert "Fenouille Moonwind" in result["user_prompt"]
        assert "Phase 1 text" in result["user_prompt"]
        assert "Phase 2 text" in result["user_prompt"]
        assert "2–3 sentence summary" in result["user_prompt"]

    def test_location_input_assembly(self):
        events = SAMPLE_EVENTS[:2]
        result = assemble_location_input("loc-camp", events, None)
        assert "loc-camp" in result["user_prompt"]
        assert "Awakens in snow" in result["user_prompt"]

    def test_faction_input_assembly(self):
        events = SAMPLE_EVENTS[:2]
        result = assemble_faction_input("faction-tribe", events, None)
        assert "faction-tribe" in result["user_prompt"]

    def test_item_input_assembly(self):
        events = SAMPLE_EVENTS[:2]
        result = assemble_item_input("item-fragment", events, None)
        assert "item-fragment" in result["user_prompt"]


class TestFormatHelpers:
    """Tests for prompt formatting helper functions."""

    def test_format_events_for_prompt(self):
        text = _format_events_for_prompt(SAMPLE_EVENTS[:2])
        assert "[turn-001]" in text
        assert "(encounter)" in text
        assert "Awakens in snow" in text

    def test_format_catalog_section_present(self):
        text = _format_catalog_section(SAMPLE_CATALOG)
        assert "Elven warlock" in text
        assert "race" in text
        assert "Leading the tribe" in text

    def test_format_catalog_section_absent(self):
        text = _format_catalog_section(None)
        assert "No catalog entry exists" in text

    def test_format_arc_section_present(self):
        text = _format_arc_section(SAMPLE_ARC_SUMMARIES)
        assert "Kael" in text
        assert "Early Bond" in text
        assert "Partner" in text

    def test_format_arc_section_absent(self):
        text = _format_arc_section(None)
        assert "No relationship arc summaries" in text

    def test_format_arc_section_turn_range_non_list(self):
        """Malformed turn_range (string instead of list) should not crash."""
        arc = {
            "entity_id": "char-player",
            "generated_at": "2026-04-15T00:00:00Z",
            "arcs": {
                "char-kael": {
                    "arc_summary": [
                        {"phase": "Bond", "turn_range": "bad", "summary": "ok"},
                    ],
                    "current_relationship": "Ally",
                    "interaction_count": 1,
                },
            },
        }
        text = _format_arc_section(arc)
        assert "Bond" in text
        assert "Kael" in text

    def test_format_arc_section_turn_range_empty_list(self):
        """Empty turn_range list should not crash."""
        arc = {
            "entity_id": "char-player",
            "generated_at": "2026-04-15T00:00:00Z",
            "arcs": {
                "char-kael": {
                    "arc_summary": [
                        {"phase": "Bond", "turn_range": [], "summary": "ok"},
                    ],
                    "current_relationship": "Ally",
                    "interaction_count": 1,
                },
            },
        }
        text = _format_arc_section(arc)
        assert "Bond" in text
        assert "?" in text  # fallback placeholder

    def test_format_arc_section_turn_range_single_element(self):
        """Single-element turn_range list should not crash."""
        arc = {
            "entity_id": "char-player",
            "generated_at": "2026-04-15T00:00:00Z",
            "arcs": {
                "char-kael": {
                    "arc_summary": [
                        {"phase": "Bond", "turn_range": ["turn-001"], "summary": "ok"},
                    ],
                    "current_relationship": "Ally",
                    "interaction_count": 1,
                },
            },
        }
        text = _format_arc_section(arc)
        assert "Bond" in text
        assert "turn-001" in text


# ---------------------------------------------------------------------------
# Test provenance validation
# ---------------------------------------------------------------------------


class TestProvenanceValidation:

    def test_detect_hallucinated_turns(self):
        """Turns cited but not in input are flagged as hallucinations."""
        markdown = "Something happened [turn-001] and then [turn-999]."
        result = validate_provenance(markdown, ["turn-001", "turn-005"])

        assert "turn-999" in result["hallucination_flags"]
        assert "turn-001" not in result["hallucination_flags"]

    def test_detect_omitted_critical_events(self):
        """Critical events not cited are flagged."""
        markdown = "Something happened [turn-001]."
        result = validate_provenance(
            markdown, ["turn-001", "turn-005"],
            critical_event_turns=["turn-001", "turn-005"])

        assert "turn-005" in result["uncited_critical_events"]
        assert "turn-001" not in result["uncited_critical_events"]

    def test_correct_provenance(self):
        """No flags when all citations match available turns."""
        markdown = "Event A [turn-001] and event B [turn-005]."
        result = validate_provenance(markdown, ["turn-001", "turn-005"])

        assert result["hallucination_flags"] == []
        assert result["uncited_critical_events"] == []

    def test_extract_cited_turns(self):
        """Extracts turn references from markdown."""
        md = "Text [turn-001] more [turn-050] end [turn-001]."
        turns = extract_cited_turns(md)
        assert "[turn-001]" in turns
        assert "[turn-050]" in turns
        assert len(turns) == 2  # deduplicated

    def test_provenance_warning_added(self):
        """Warning banner is prepended when hallucinations exist."""
        md = "# Test Page\n\nContent."
        result = add_provenance_warning(md, "char-test")
        assert "⚠️" in result
        assert "char-test.synthesis.json" in result
        assert result.endswith("Content.")

    def test_collect_critical_turns(self):
        """Critical turns extracted from birth/decision/ritual events."""
        events = [
            _make_event("e1", ["turn-001"], ["char-player"], "encounter", "Walk"),
            _make_event("e2", ["turn-015"], ["char-player"], "ritual", "Accepted"),
            _make_event("e3", ["turn-050"], ["char-player"], "encounter", "Chat"),
            _make_event("e4", ["turn-141"], ["char-player"], "birth", "Baby born"),
        ]
        turns = _collect_critical_turns(events)
        assert "turn-015" in turns  # ritual is critical
        assert "turn-141" in turns  # birth is critical
        assert "turn-001" not in turns  # encounter is not critical
        assert "turn-050" not in turns  # encounter is not critical

    def test_collect_critical_turns_empty(self):
        """No critical turns when all events are non-critical type."""
        events = [
            _make_event("e1", ["turn-001"], ["char-player"], "encounter", "Walk"),
        ]
        assert _collect_critical_turns(events) == []


# ---------------------------------------------------------------------------
# Test page assembly
# ---------------------------------------------------------------------------


class TestPageAssembly:

    def test_character_page_structure(self):
        """Character page has all required sections."""
        phase_texts = [
            ("The Awakening", "Fenouille awoke in the snow [turn-001]."),
            ("Finding the Tribe", "She was accepted into the tribe [turn-015]."),
        ]
        page = assemble_character_page(
            "char-player", "Fenouille Moonwind",
            "Elven warlock who built a civilization.",
            phase_texts, SAMPLE_CATALOG, None,
            SAMPLE_ARC_SUMMARIES, SAMPLE_EVENTS)

        assert "# Fenouille Moonwind" in page
        assert "> Elven warlock who built a civilization." in page
        assert "## Overview" in page
        assert "## Biography" in page
        assert "### The Awakening" in page
        assert "### Finding the Tribe" in page
        assert "## Relationships" in page
        assert "## Current Status" in page
        assert "## Event Timeline" in page
        assert "turn-001" in page

    def test_character_page_event_derived(self):
        """Character page works with event-derived profile (no catalog)."""
        derived = {
            "id": "char-kael",
            "name": "Kael",
            "type": "character",
            "source": "events_only",
            "first_event_turn": "turn-050",
            "last_event_turn": "turn-141",
            "event_count": 5,
        }
        page = assemble_character_page(
            "char-kael", "Kael", "A mysterious warrior.",
            [("Full Timeline", "Kael appeared [turn-050].")],
            None, derived, None, SAMPLE_EVENTS[3:])

        assert "# Kael" in page
        assert "Events only" in page
        assert "## Biography" in page

    def test_location_page_structure(self):
        """Location page uses event table instead of biography."""
        page = assemble_location_page(
            "loc-camp", "The Encampment",
            "Central tribal settlement.",
            None, None, SAMPLE_EVENTS[:3])

        assert "# The Encampment" in page
        assert "## Significance" in page
        assert "Central tribal settlement." in page
        assert "## Key Events" in page
        assert "| Turn | Type | Description |" in page
        assert "Biography" not in page

    def test_faction_page_structure(self):
        """Faction page has history and members sections."""
        events = [
            _make_event("evt-f1", ["turn-100"], ["faction-tribe", "char-player"],
                        "encounter", "Meeting"),
        ]
        page = assemble_faction_page(
            "faction-tribe", "The Tribe",
            "The tribe grew from nomads to settlers.",
            None, None, events)

        assert "# The Tribe" in page
        assert "## History" in page
        assert "## Known Members" in page
        assert "Player" in page  # From char-player co-occurrence

    def test_item_page_structure(self):
        """Item page has significance and key events."""
        page = assemble_item_page(
            "item-fragment", "The Fragment",
            "A mysterious arcane object.",
            None, None, SAMPLE_EVENTS[:2])

        assert "# The Fragment" in page
        assert "## Significance" in page
        assert "## Key Events" in page

    def test_hallucination_warning_in_page(self):
        """Page gets warning banner when hallucinations detected."""
        md = "# Test\n\nContent [turn-999]."
        result = add_provenance_warning(md, "char-test")
        assert "⚠️ **Provenance warning**" in result

    def test_infobox_from_catalog(self):
        """Infobox built from catalog data."""
        text = _build_infobox("char-player", SAMPLE_CATALOG, None)
        assert "Character" in text
        assert "turn-001" in text
        assert "Elf" in text

    def test_infobox_from_derived_profile(self):
        """Infobox built from event-derived profile."""
        derived = {
            "type": "character",
            "source": "events_only",
            "first_event_turn": "turn-050",
            "last_event_turn": "turn-141",
            "event_count": 5,
        }
        text = _build_infobox("char-kael", None, derived)
        assert "Events only" in text
        assert "turn-050" in text

    def test_event_timeline(self):
        """Event timeline table is generated."""
        text = _build_event_timeline(SAMPLE_EVENTS[:2])
        assert "| Turn | Type | Description |" in text
        assert "turn-001" in text
        assert "Awakens in snow" in text

    def test_current_status_from_catalog(self):
        """Current status from catalog data."""
        text = _build_current_status(SAMPLE_CATALOG, SAMPLE_EVENTS)
        assert "Leading the tribe" in text
        assert "turn-141" in text

    def test_current_status_from_events(self):
        """Current status derived from latest event."""
        text = _build_current_status(None, SAMPLE_EVENTS)
        assert "Lyrawyn born" in text


# ---------------------------------------------------------------------------
# Test should_synthesize
# ---------------------------------------------------------------------------


class TestShouldSynthesize:

    def test_character_3_events(self):
        assert should_synthesize("char-test", 3, "character") is True

    def test_character_1_event(self):
        assert should_synthesize("char-test", 1, "character") is False

    def test_character_0_events(self):
        assert should_synthesize("char-test", 0, "character") is False

    def test_location_3_events(self):
        assert should_synthesize("loc-test", 3, "location") is True

    def test_location_2_events(self):
        assert should_synthesize("loc-test", 2, "location") is False

    def test_faction_5_events(self):
        assert should_synthesize("faction-test", 5, "faction") is True

    def test_item_2_events(self):
        assert should_synthesize("item-test", 2, "item") is False

    def test_item_5_events(self):
        assert should_synthesize("item-test", 5, "item") is True

    def test_unknown_type(self):
        assert should_synthesize("x-test", 10, "unknown") is False


# ---------------------------------------------------------------------------
# Test LLM generation with mock
# ---------------------------------------------------------------------------


class TestBiographyGeneration:

    def test_generate_phase_biography_success(self):
        """Phase biography returns LLM text and metadata."""
        llm = MockLLMClient()
        phase = {
            "name": "Test Phase",
            "turn_range": ["turn-001", "turn-015"],
            "events": SAMPLE_EVENTS[:3],
            "event_count": 3,
        }
        synth_input = assemble_synthesis_input(
            "char-player", phase, SAMPLE_CATALOG, None)

        text, meta = generate_phase_biography(llm, synth_input)

        assert "[turn-001]" in text
        assert meta["name"] == "Test Phase"
        # No TITLE: prefix in mock response → title falls back to phase_name
        assert meta["title"] == "Test Phase"
        assert meta["llm_model"] == "mock-model"
        assert meta["tokens_used"] > 0
        assert len(llm.calls) == 1

    def test_generate_phase_biography_failure(self):
        """Phase biography returns fallback on LLM failure."""
        llm = FailingLLMClient()
        synth_input = {
            "system_prompt": "test",
            "user_prompt": "test",
            "events_used": [],
            "turn_range": ["turn-001", "turn-015"],
            "phase_name": "Test",
            "available_turns": [],
        }

        text, meta = generate_phase_biography(llm, synth_input)

        assert "Generation failed" in text
        assert "error" in meta
        assert meta["title"] == "Test"

    def test_generate_lede(self):
        """Lede generation returns text."""
        llm = MockLLMClient("A concise summary of the story arc.")
        lede_input = assemble_lede_input(
            "char-player", ["Phase 1 text.", "Phase 2 text."], SAMPLE_CATALOG)

        text = generate_lede(llm, lede_input)
        assert text == "A concise summary of the story arc."

    def test_generate_lede_failure(self):
        """Lede returns empty string on failure."""
        llm = FailingLLMClient()
        lede_input = {"system_prompt": "test", "user_prompt": "test"}
        text = generate_lede(llm, lede_input)
        assert text == ""


# ---------------------------------------------------------------------------
# Test sidecar generation
# ---------------------------------------------------------------------------


class TestSidecarGeneration:

    def test_build_synthesis_sidecar(self):
        """Sidecar has all required fields."""
        provenance = {
            "turns_cited": ["turn-001", "turn-005"],
            "turns_available": ["turn-001", "turn-005", "turn-015"],
            "hallucination_flags": [],
            "uncited_critical_events": [],
        }
        phase_meta = [{
            "name": "Test Phase",
            "turn_range": ["turn-001", "turn-015"],
            "events_used": ["evt-001", "evt-002"],
            "llm_model": "mock",
            "tokens_used": 500,
        }]
        sidecar = build_synthesis_sidecar(
            "char-player", SAMPLE_EVENTS[:3],
            True, "turn-141", 1, phase_meta, provenance)

        assert sidecar["entity_id"] == "char-player"
        assert "generated_at" in sidecar
        assert sidecar["source_data"]["events_count"] == 3
        assert sidecar["source_data"]["catalog_available"] is True
        assert sidecar["source_data"]["catalog_last_updated"] == "turn-141"
        assert sidecar["source_data"]["relationship_arcs_count"] == 1
        assert len(sidecar["phases"]) == 1
        assert sidecar["provenance_check"]["hallucination_flags"] == []

    def test_write_and_load_sidecar(self):
        """Sidecar round-trips through write and load."""
        sidecar = {
            "entity_id": "char-test",
            "generated_at": "2026-04-15T00:00:00Z",
            "source_data": {"events_count": 5},
            "phases": [],
            "provenance_check": {},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "char-test.synthesis.json")
            write_synthesis_sidecar(sidecar, path)

            loaded = load_synthesis_sidecar(path)
            assert loaded is not None
            assert loaded["entity_id"] == "char-test"
            assert loaded["source_data"]["events_count"] == 5

    def test_load_missing_sidecar(self):
        """Loading non-existent sidecar returns None."""
        result = load_synthesis_sidecar("/nonexistent/path.json")
        assert result is None


# ---------------------------------------------------------------------------
# Test incremental awareness
# ---------------------------------------------------------------------------


class TestIncrementalAwareness:

    def test_needs_regeneration_no_sidecar(self):
        """Regeneration needed when no sidecar exists."""
        assert needs_regeneration("char-test", 10, "/nonexistent.json") is True

    def test_needs_regeneration_force(self):
        """Force always triggers regeneration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.synthesis.json")
            sidecar = {"source_data": {"events_count": 10}}
            with open(path, "w") as f:
                json.dump(sidecar, f)

            assert needs_regeneration("char-test", 10, path, force=True) is True

    def test_no_regeneration_same_count(self):
        """No regeneration when event count unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.synthesis.json")
            sidecar = {"source_data": {"events_count": 10}}
            with open(path, "w") as f:
                json.dump(sidecar, f)

            assert needs_regeneration("char-test", 10, path) is False

    def test_regeneration_new_events(self):
        """Regeneration needed when event count changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.synthesis.json")
            sidecar = {"source_data": {"events_count": 10}}
            with open(path, "w") as f:
                json.dump(sidecar, f)

            assert needs_regeneration("char-test", 15, path) is True


# ---------------------------------------------------------------------------
# Integration test: full pipeline with mock LLM
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:

    def test_character_synthesis_full_data(self):
        """Full character pipeline with catalog + events + arcs."""
        llm = MockLLMClient(
            "The character awakened in the snow [turn-001] and triggered "
            "a snare [turn-005]. They were accepted into the tribe [turn-015]."
        )

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS, SAMPLE_CATALOG,
            SAMPLE_ARC_SUMMARIES, llm, entity_type="character")

        # Page structure
        assert "# Fenouille Moonwind" in page
        assert "## Biography" in page
        assert "## Event Timeline" in page
        assert "[turn-001]" in page

        # Sidecar
        assert sidecar["entity_id"] == "char-player"
        assert sidecar["source_data"]["catalog_available"] is True
        assert len(sidecar["phases"]) >= 1
        assert "provenance_check" in sidecar

    def test_character_synthesis_events_only(self):
        """Character pipeline with events only (no catalog)."""
        llm = MockLLMClient(
            "Kael appeared near the fire [turn-050] and attended "
            "the council [turn-108]."
        )
        kael_events = [e for e in SAMPLE_EVENTS
                       if "char-kael" in e.get("related_entities", [])]

        page, sidecar = synthesize_entity(
            "char-kael", kael_events, None, None,
            llm, entity_type="character")

        assert "# Kael" in page
        assert "Events only" in page
        assert sidecar["source_data"]["catalog_available"] is False

    def test_location_synthesis(self):
        """Location synthesis produces event table."""
        llm = MockLLMClient(
            "The encampment served as the tribal center [turn-001].")

        page, sidecar = synthesize_entity(
            "loc-camp", SAMPLE_EVENTS[:3], None, None,
            llm, entity_type="location")

        assert "# Camp" in page
        assert "## Significance" in page
        assert "## Key Events" in page
        assert "Biography" not in page

    def test_faction_synthesis(self):
        """Faction synthesis produces history and members."""
        events = [
            _make_event("evt-f1", ["turn-100"], ["faction-tribe", "char-player"],
                        "encounter", "Tribe meeting"),
            _make_event("evt-f2", ["turn-110"], ["faction-tribe", "char-elder"],
                        "decision", "Tribal decision"),
            _make_event("evt-f3", ["turn-120"], ["faction-tribe", "char-kael"],
                        "decision", "Alliance formed"),
        ]
        llm = MockLLMClient(
            "The tribe evolved through meetings [turn-100] "
            "and alliances [turn-120].")

        page, sidecar = synthesize_entity(
            "faction-tribe", events, None, None,
            llm, entity_type="faction")

        assert "# Tribe" in page
        assert "## History" in page
        assert "## Known Members" in page

    def test_item_synthesis(self):
        """Item synthesis produces significance summary."""
        events = [
            _make_event("evt-i1", ["turn-108"], ["item-fragment"],
                        "discovery", "Fragment studied"),
            _make_event("evt-i2", ["turn-306"], ["item-fragment"],
                        "discovery", "Fragment flares"),
            _make_event("evt-i3", ["turn-340"], ["item-fragment"],
                        "discovery", "Deepened thrum"),
        ]
        llm = MockLLMClient(
            "The fragment is a mysterious artifact [turn-108] "
            "that flares with power [turn-306].")

        page, sidecar = synthesize_entity(
            "item-fragment", events, None, None,
            llm, entity_type="item")

        assert "# Fragment" in page
        assert "## Significance" in page

    def test_provenance_pass(self):
        """Provenance validation passes when citations match."""
        llm = MockLLMClient(
            "Something happened [turn-001] and [turn-005].")

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS[:3], SAMPLE_CATALOG,
            None, llm, entity_type="character")

        assert sidecar["provenance_check"]["hallucination_flags"] == []
        assert "⚠️" not in page

    def test_provenance_hallucination_flagged(self):
        """Hallucinated turns trigger warning banner."""
        llm = MockLLMClient(
            "Something happened [turn-001] and also [turn-999].")

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS[:3], SAMPLE_CATALOG,
            None, llm, entity_type="character")

        assert "turn-999" in sidecar["provenance_check"]["hallucination_flags"]
        assert "⚠️" in page

    def test_incremental_skip_on_second_run(self):
        """Entity is skipped on second run when no new events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sidecar_path = os.path.join(tmpdir, "char-test.synthesis.json")

            # First run: needs regeneration
            assert needs_regeneration("char-test", 5, sidecar_path) is True

            # Write sidecar
            sidecar = {"source_data": {"events_count": 5}}
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f)

            # Second run: skip
            assert needs_regeneration("char-test", 5, sidecar_path) is False

            # New events: regenerate
            assert needs_regeneration("char-test", 7, sidecar_path) is True

    def test_output_files_written(self):
        """Full pipeline writes .md and .synthesis.json files."""
        from narrative_synthesis import write_synthesis_sidecar

        llm = MockLLMClient(
            "The character awakened [turn-001].")

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS[:3], SAMPLE_CATALOG,
            None, llm, entity_type="character")

        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "char-player.md")
            sidecar_path = os.path.join(tmpdir, "char-player.synthesis.json")

            with open(md_path, "w", encoding="utf-8") as f:
                f.write(page)
            write_synthesis_sidecar(sidecar, sidecar_path)

            assert os.path.isfile(md_path)
            assert os.path.isfile(sidecar_path)

            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "# Fenouille Moonwind" in content

            loaded = load_synthesis_sidecar(sidecar_path)
            assert loaded["entity_id"] == "char-player"


# ---------------------------------------------------------------------------
# Test biography title parsing and subheading normalization
# ---------------------------------------------------------------------------


class TestBiographyTitleParsing:

    def test_parse_biography_response_with_title(self):
        """TITLE: prefix is extracted as the section title."""
        raw = "TITLE: Capture and Integration\n\nProse about the capture."
        title, prose = _parse_biography_response(raw, "Fallback Name")
        assert title == "Capture and Integration"
        assert prose == "Prose about the capture."

    def test_parse_biography_response_without_title(self):
        """Missing TITLE: prefix uses fallback name."""
        raw = "Prose without a title prefix."
        title, prose = _parse_biography_response(raw, "Fallback Name")
        assert title == "Fallback Name"
        assert prose == "Prose without a title prefix."

    def test_parse_biography_response_title_case_insensitive(self):
        """TITLE: matching is case-insensitive."""
        raw = "title: Awakening in the Snow\n\nSome text here."
        title, prose = _parse_biography_response(raw, "Fallback")
        assert title == "Awakening in the Snow"
        assert prose == "Some text here."

    def test_parse_biography_response_title_only(self):
        """TITLE: line with no following prose returns empty prose."""
        raw = "TITLE: Solo Title"
        title, prose = _parse_biography_response(raw, "Fallback")
        assert title == "Solo Title"
        assert prose == ""

    def test_normalize_subheadings_downgrades(self):
        """### headings become #### headings."""
        text = "### Sub-section A\nContent.\n### Sub-section B\nMore."
        result = _normalize_subheadings(text)
        assert "#### Sub-section A" in result
        assert "#### Sub-section B" in result
        # No lines start with exactly "### " (only "#### ")
        for line in result.splitlines():
            assert not line.startswith("### ")

    def test_normalize_subheadings_keeps_h4(self):
        """#### headings stay unchanged."""
        text = "#### Already level 4\nContent."
        result = _normalize_subheadings(text)
        assert "#### Already level 4" in result
        # No spurious extra # added
        assert "##### Already level 4" not in result

    def test_sidecar_caches_title(self):
        """Phase metadata in sidecar includes the title field."""
        llm = MockLLMClient(
            "TITLE: Awakening in the Snow\n\n"
            "The character awakened [turn-001] and was found [turn-005]."
        )

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS[:3], SAMPLE_CATALOG,
            None, llm, entity_type="character")

        assert len(sidecar["phases"]) >= 1
        phase = sidecar["phases"][0]
        assert phase["title"] == "Awakening in the Snow"

    def test_no_generic_phase_titles(self):
        """When LLM returns TITLE:, no generic 'Phase (turns' headings appear."""
        llm = MockLLMClient(
            "TITLE: Early Days\n\n"
            "The character awakened [turn-001] and was found [turn-005]."
        )

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS[:3], SAMPLE_CATALOG,
            None, llm, entity_type="character")

        import re
        assert not re.search(r"^### Phase \(turns", page, re.MULTILINE)
        assert "### Early Days (turns" in page

    def test_descriptive_title_in_full_pipeline(self):
        """Full pipeline uses LLM title with turn range in heading."""
        llm = MockLLMClient(
            "TITLE: Capture and Integration\n\n"
            "Events unfolded [turn-001] and [turn-005]."
        )

        page, sidecar = synthesize_entity(
            "char-player", SAMPLE_EVENTS[:3], SAMPLE_CATALOG,
            None, llm, entity_type="character")

        # The heading should include the descriptive title and turn range
        assert "### Capture and Integration (turns" in page
        # The generic Phase label should NOT appear
        assert "Phase (turns" not in page
