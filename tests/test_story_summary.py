"""Tests for the story summary generator (tools/generate_story_summary.py)."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from generate_story_summary import (
    assemble_story_summary_input,
    assemble_summary_page,
    generate_story_summary,
    generate_story_summary_data_only,
    generate_story_summary_llm,
    load_entity_catalog,
    load_plot_threads,
    load_timeline,
    _critical_events,
    _format_turn_range,
    _get_pc_data,
    _top_entities_by_events,
)
from synthesis import group_events_by_entity

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

SAMPLE_CHARACTERS = [
    {
        "id": "char-player",
        "name": "Fenouille Moonwind",
        "type": "character",
        "identity": "Elven warlock who awakened in the snow",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-141",
        "current_status": "Leading the tribe",
        "status_updated_turn": "turn-141",
        "relationships": [
            {
                "target_id": "char-kael",
                "type": "romantic",
                "current_relationship": "Partner",
                "status": "active",
                "last_updated_turn": "turn-141",
                "history": [],
            },
            {
                "target_id": "char-elder",
                "type": "social",
                "current_relationship": "Respected elder",
                "status": "active",
                "last_updated_turn": "turn-108",
                "history": [],
            },
        ],
    },
]

SAMPLE_PLOT_THREADS = [
    {
        "id": "plot-tribal-leadership",
        "title": "Tribal Leadership",
        "description": "Fenouille's rise to leadership of the tribe.",
        "status": "active",
        "related_entities": ["char-player", "char-kael"],
        "open_questions": ["Will the tribe accept joint leadership?"],
        "key_turns": ["turn-108", "turn-141"],
        "first_seen_turn": "turn-015",
        "last_updated_turn": "turn-141",
    },
    {
        "id": "plot-sealed-tower",
        "title": "The Sealed Tower",
        "description": "A mysterious sealed tower seen in the distance.",
        "status": "dormant",
        "related_entities": ["char-player"],
        "open_questions": ["What is inside the tower?"],
        "key_turns": ["turn-050"],
        "first_seen_turn": "turn-050",
        "last_updated_turn": "turn-050",
    },
    {
        "id": "plot-winter-plague",
        "title": "Winter Plague",
        "description": "A plague that swept through during winter.",
        "status": "resolved",
        "related_entities": ["char-player", "char-elder"],
        "open_questions": [],
        "key_turns": ["turn-079"],
        "first_seen_turn": "turn-060",
        "last_updated_turn": "turn-090",
    },
]

SAMPLE_TIMELINE = [
    {
        "id": "tm-001",
        "source_turn": "turn-001",
        "type": "season",
        "description": "Late winter",
        "season": "winter",
    },
    {
        "id": "tm-002",
        "source_turn": "turn-050",
        "type": "season",
        "description": "Early spring",
        "season": "spring",
    },
]


class MockLLMClient:
    """Mock LLM client that returns predictable summary text."""

    def __init__(self, response_text=None):
        self.response_text = response_text or (
            "Fenouille Moonwind's journey began when she awakened in the snow "
            "[turn-001]. Through a series of trials and an acceptance ritual "
            "[turn-015], she integrated into the tribe. Her bond with Kael "
            "deepened [turn-050] and culminated in the birth of Lyrawyn "
            "[turn-141]. She now leads the tribe alongside her partner."
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
    """Mock LLM client that always raises."""

    model = "failing-model"

    def generate_text(self, system_prompt, user_prompt, timeout=None):
        raise RuntimeError("LLM unavailable")

    def delay(self):
        pass


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for internal helper functions."""

    def test_get_pc_data_found(self):
        pc = _get_pc_data(SAMPLE_CHARACTERS)
        assert pc is not None
        assert pc["id"] == "char-player"

    def test_get_pc_data_missing(self):
        assert _get_pc_data([]) is None
        assert _get_pc_data([{"id": "char-kael"}]) is None

    def test_format_turn_range(self):
        first, last = _format_turn_range(SAMPLE_EVENTS)
        assert first == "turn-001"
        assert last == "turn-141"

    def test_format_turn_range_empty(self):
        first, last = _format_turn_range([])
        assert first == "?"
        assert last == "?"

    def test_critical_events_filters_types(self):
        critical = _critical_events(SAMPLE_EVENTS)
        types = {e["type"] for e in critical}
        # Only critical types should appear
        assert "encounter" not in types
        assert "ritual" in types
        assert "birth" in types
        assert "discovery" in types
        assert "decision" in types

    def test_critical_events_sorted(self):
        critical = _critical_events(SAMPLE_EVENTS)
        turns = []
        for e in critical:
            t = e.get("source_turns", [""])[0]
            if t:
                turns.append(int(t.split("-")[1]))
        assert turns == sorted(turns)

    def test_critical_events_limit(self):
        critical = _critical_events(SAMPLE_EVENTS, limit=2)
        assert len(critical) <= 2

    def test_top_entities_by_events(self):
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        top = _top_entities_by_events(grouped, exclude={"char-player"}, limit=3)
        assert len(top) <= 3
        # char-kael should be first (most events after player)
        assert top[0][0] == "char-kael"

    def test_top_entities_excludes(self):
        grouped = group_events_by_entity(SAMPLE_EVENTS)
        top = _top_entities_by_events(grouped, exclude={"char-player"})
        ids = {eid for eid, _ in top}
        assert "char-player" not in ids


# ---------------------------------------------------------------------------
# Input assembly tests
# ---------------------------------------------------------------------------


class TestInputAssembly:
    """Tests for assemble_story_summary_input."""

    def test_produces_system_and_user_prompts(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "system_prompt" in result
        assert "user_prompt" in result

    def test_includes_campaign_scope(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        assert "turn-001" in prompt
        assert "turn-141" in prompt
        assert "8" in prompt  # total events

    def test_includes_player_character(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        assert "Fenouille Moonwind" in prompt
        assert "Elven warlock" in prompt
        assert "Leading the tribe" in prompt

    def test_includes_plot_threads(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        assert "Tribal Leadership" in prompt
        assert "ACTIVE" in prompt
        assert "DORMANT" in prompt
        assert "RESOLVED" in prompt

    def test_includes_key_events(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        assert "ritual" in prompt.lower()
        assert "birth" in prompt.lower()

    def test_includes_relationships(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        assert "Kael" in prompt
        assert "Partner" in prompt

    def test_includes_timeline(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        assert "winter" in prompt.lower()

    def test_handles_empty_plot_threads(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, [], SAMPLE_CHARACTERS, [])
        prompt = result["user_prompt"]
        # Should still contain PC data and events
        assert "Fenouille Moonwind" in prompt
        assert "Task" in prompt

    def test_handles_no_pc(self):
        result = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS, [], SAMPLE_TIMELINE)
        prompt = result["user_prompt"]
        # Should still work without PC
        assert "Task" in prompt
        assert "Campaign Scope" in prompt


# ---------------------------------------------------------------------------
# LLM generation tests
# ---------------------------------------------------------------------------


class TestLLMGeneration:
    """Tests for LLM-based summary generation."""

    def test_calls_llm_with_prompts(self):
        client = MockLLMClient()
        summary_input = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        result = generate_story_summary_llm(client, summary_input)
        assert len(client.calls) == 1
        assert "Fenouille" in result

    def test_returns_empty_on_failure(self):
        client = FailingLLMClient()
        summary_input = assemble_story_summary_input(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        result = generate_story_summary_llm(client, summary_input)
        assert result == ""


# ---------------------------------------------------------------------------
# Data-only summary tests
# ---------------------------------------------------------------------------


class TestDataOnlySummary:
    """Tests for generate_story_summary_data_only."""

    def test_produces_markdown(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_campaign_range(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "turn-001" in result
        assert "turn-141" in result

    def test_includes_pc_info(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "Fenouille Moonwind" in result
        assert "Leading the tribe" in result

    def test_includes_active_threads(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "Tribal Leadership" in result

    def test_includes_dormant_threads(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "Sealed Tower" in result

    def test_includes_resolved_threads(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "Winter Plague" in result

    def test_includes_key_events(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "Acceptance ritual" in result
        assert "Lyrawyn born" in result

    def test_includes_open_questions(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
            SAMPLE_CHARACTERS, SAMPLE_TIMELINE)
        assert "joint leadership" in result

    def test_handles_empty_events(self):
        result = generate_story_summary_data_only([], [], [], [])
        assert isinstance(result, str)
        # Minimal output with no data
        assert "?" in result  # turn range fallback

    def test_handles_no_threads(self):
        result = generate_story_summary_data_only(
            SAMPLE_EVENTS, [], SAMPLE_CHARACTERS, [])
        assert "Fenouille" in result
        # No thread sections
        assert "Active plot threads" not in result


# ---------------------------------------------------------------------------
# Page assembly tests
# ---------------------------------------------------------------------------


class TestPageAssembly:
    """Tests for assemble_summary_page."""

    def test_produces_valid_markdown(self):
        prose = "This is the campaign summary."
        page = assemble_summary_page(prose, SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
                                     generated_by="test")
        assert page.startswith("# Story Summary")
        assert "## Arc Overview" in page
        assert "This is the campaign summary." in page

    def test_includes_metadata(self):
        page = assemble_summary_page("Test", SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
                                     generated_by="mock-model")
        assert "turn-001" in page
        assert "turn-141" in page
        assert "8 events" in page
        assert "mock-model" in page

    def test_includes_open_questions(self):
        page = assemble_summary_page("Test", SAMPLE_EVENTS, SAMPLE_PLOT_THREADS,
                                     generated_by="test")
        assert "## Open Questions" in page
        assert "joint leadership" in page
        # The sealed tower also has open questions
        assert "inside the tower" in page

    def test_no_open_questions_when_none(self):
        threads = [{"id": "t", "title": "T", "status": "resolved",
                    "description": "", "first_seen_turn": "turn-001"}]
        page = assemble_summary_page("Test", SAMPLE_EVENTS, threads,
                                     generated_by="test")
        assert "## Open Questions" not in page

    def test_generated_by_label(self):
        page = assemble_summary_page("Test", SAMPLE_EVENTS, [],
                                     generated_by="llm (qwen2.5:14b)")
        assert "llm (qwen2.5:14b)" in page


# ---------------------------------------------------------------------------
# Integration: generate_story_summary with temp framework dir
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests for generate_story_summary with filesystem."""

    def _build_framework(self, tmpdir):
        """Create a minimal framework directory with catalog files."""
        catalogs = os.path.join(tmpdir, "catalogs")
        story = os.path.join(tmpdir, "story")
        os.makedirs(catalogs)
        os.makedirs(story)

        with open(os.path.join(catalogs, "events.json"), "w") as f:
            json.dump(SAMPLE_EVENTS, f)
        with open(os.path.join(catalogs, "characters.json"), "w") as f:
            json.dump(SAMPLE_CHARACTERS, f)
        with open(os.path.join(catalogs, "plot-threads.json"), "w") as f:
            json.dump(SAMPLE_PLOT_THREADS, f)
        with open(os.path.join(catalogs, "timeline.json"), "w") as f:
            json.dump(SAMPLE_TIMELINE, f)

        return tmpdir

    def test_no_llm_generates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fw = self._build_framework(tmpdir)
            page = generate_story_summary(fw, no_llm=True)

            output_path = os.path.join(fw, "story", "summary.md")
            assert os.path.isfile(output_path)
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert content == page
            assert "# Story Summary" in content
            assert "data-only" in content

    def test_llm_generates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fw = self._build_framework(tmpdir)
            client = MockLLMClient()
            page = generate_story_summary(fw, llm_client=client)

            output_path = os.path.join(fw, "story", "summary.md")
            assert os.path.isfile(output_path)
            assert "Fenouille" in page
            assert "mock-model" in page

    def test_llm_fallback_on_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fw = self._build_framework(tmpdir)
            client = FailingLLMClient()
            page = generate_story_summary(fw, llm_client=client)

            assert "data-only" in page
            assert "# Story Summary" in page

    def test_empty_framework(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalogs = os.path.join(tmpdir, "catalogs")
            os.makedirs(catalogs)
            with open(os.path.join(catalogs, "events.json"), "w") as f:
                json.dump([], f)

            page = generate_story_summary(tmpdir, no_llm=True)
            assert "No events extracted yet" in page

    def test_missing_catalogs_graceful(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # No catalogs directory at all
            page = generate_story_summary(tmpdir, no_llm=True)
            assert "No events extracted yet" in page

    def test_creates_story_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fw = self._build_framework(tmpdir)
            # Remove story dir to test creation
            story_dir = os.path.join(fw, "story")
            os.rmdir(story_dir)
            assert not os.path.isdir(story_dir)

            generate_story_summary(fw, no_llm=True)
            assert os.path.isfile(os.path.join(fw, "story", "summary.md"))


# ---------------------------------------------------------------------------
# File loader tests
# ---------------------------------------------------------------------------


class TestLoaders:
    """Tests for JSON file loaders."""

    def test_load_plot_threads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalogs = os.path.join(tmpdir, "catalogs")
            os.makedirs(catalogs)
            with open(os.path.join(catalogs, "plot-threads.json"), "w") as f:
                json.dump(SAMPLE_PLOT_THREADS, f)
            result = load_plot_threads(tmpdir)
            assert len(result) == 3

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert load_plot_threads(tmpdir) == []
            assert load_timeline(tmpdir) == []
            assert load_entity_catalog(tmpdir, "characters") == []

    def test_load_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalogs = os.path.join(tmpdir, "catalogs")
            os.makedirs(catalogs)
            with open(os.path.join(catalogs, "plot-threads.json"), "w") as f:
                f.write("{invalid json")
            assert load_plot_threads(tmpdir) == []
