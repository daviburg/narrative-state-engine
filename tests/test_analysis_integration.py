"""Tests for analysis agent integration with turn-context.json (#87)."""
import json
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from analyze_next_move import (
    format_nearby_summary,
    format_scene_entities,
    format_scene_locations,
    generate_analysis,
    load_turn_context,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_dir(tmp_path):
    """Create a minimal session directory with derived files."""
    session = tmp_path / "test-session"
    derived = session / "derived"
    derived.mkdir(parents=True)
    transcript = session / "transcript"
    transcript.mkdir()

    # Minimal state.json
    state = {
        "as_of_turn": "turn-010",
        "current_world_state": "The party rests at camp.",
        "player_state": {"location": "camp"},
        "active_threads": [],
    }
    (derived / "state.json").write_text(json.dumps(state), encoding="utf-8")

    # Empty evidence and objectives
    (derived / "evidence.json").write_text("[]", encoding="utf-8")
    (derived / "objectives.json").write_text("[]", encoding="utf-8")

    # Create transcript files so latest turn can be detected
    (transcript / "turn-010-dm.md").write_text(
        "# turn-010\nThe camp is quiet tonight.", encoding="utf-8"
    )

    return str(session)


@pytest.fixture
def session_with_context(session_dir):
    """Add a turn-context.json to the session."""
    derived = os.path.join(session_dir, "derived")
    context = {
        "as_of_turn": "turn-010",
        "scene_entities": [
            {
                "id": "char-player",
                "name": "Fenouille",
                "identity": "A forest druid",
                "current_status": "Resting at camp",
                "active_relationships": [
                    {
                        "target_id": "char-elder",
                        "target_name": "Elder Ashwood",
                        "relationship": "cautious ally",
                    }
                ],
            }
        ],
        "scene_locations": [
            {
                "id": "loc-camp",
                "name": "Forest Camp",
                "identity": "A temporary camp in the northern woods.",
                "current_status": "Quiet and secure.",
            }
        ],
        "nearby_entities_summary": [
            {
                "id": "char-merchant",
                "name": "Traveling Merchant",
                "status_summary": "Last seen heading north.",
            }
        ],
    }
    with open(os.path.join(derived, "turn-context.json"), "w", encoding="utf-8") as f:
        json.dump(context, f)
    return session_dir


@pytest.fixture
def session_stale_context(session_dir):
    """Add a stale turn-context.json (older than latest turn)."""
    derived = os.path.join(session_dir, "derived")
    transcript = os.path.join(session_dir, "transcript")

    # Add a newer turn
    with open(os.path.join(transcript, "turn-011-dm.md"), "w", encoding="utf-8") as f:
        f.write("# turn-011\nNew events unfold.")

    # Context is still at turn-010
    context = {
        "as_of_turn": "turn-010",
        "scene_entities": [],
    }
    with open(os.path.join(derived, "turn-context.json"), "w", encoding="utf-8") as f:
        json.dump(context, f)
    return session_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadTurnContext:
    def test_loads_turn_context(self, session_with_context):
        """analyze_next_move loads turn-context.json when present"""
        context, is_stale = load_turn_context(session_with_context)
        assert context is not None
        assert context["as_of_turn"] == "turn-010"
        assert len(context["scene_entities"]) == 1
        assert not is_stale

    def test_graceful_without_context(self, session_dir):
        """runs successfully when turn-context.json is missing"""
        context, is_stale = load_turn_context(session_dir)
        assert context is None
        assert not is_stale

    def test_stale_context_warning(self, session_stale_context):
        """warns when context is older than latest turn"""
        context, is_stale = load_turn_context(session_stale_context)
        assert context is not None
        assert is_stale


class TestEntityContextInTemplate:
    def test_entity_context_in_analysis(self, session_with_context):
        """scene entities appear in assembled analysis output"""
        generate_analysis(session_with_context, "desired_outcome")
        analysis_file = os.path.join(session_with_context, "derived", "next-move-analysis.md")
        assert os.path.isfile(analysis_file)
        with open(analysis_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Fenouille" in content
        assert "char-player" in content
        assert "Forest Camp" in content
        assert "Traveling Merchant" in content

    def test_no_context_fallback(self, session_dir):
        """analysis works without turn-context.json, shows fallback text"""
        generate_analysis(session_dir, "desired_outcome")
        analysis_file = os.path.join(session_dir, "derived", "next-move-analysis.md")
        with open(analysis_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "No turn-context.json available" in content

    def test_stale_context_generates_warning(self, session_stale_context):
        """stale context generates a warning but still produces analysis"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            generate_analysis(session_stale_context, "desired_outcome")
            stale_warnings = [x for x in w if "stale" in str(x.message).lower()]
            assert len(stale_warnings) >= 1


class TestFormatFunctions:
    def test_format_scene_entities_empty(self):
        assert "No entity context" in format_scene_entities([])

    def test_format_scene_entities_with_relationships(self):
        entities = [
            {
                "id": "char-1",
                "name": "Alice",
                "identity": "A rogue",
                "active_relationships": [
                    {"target_name": "Bob", "relationship": "rival"},
                ],
            }
        ]
        result = format_scene_entities(entities)
        assert "Alice" in result
        assert "char-1" in result
        assert "Bob" in result
        assert "rival" in result

    def test_format_scene_locations_empty(self):
        assert "No location context" in format_scene_locations([])

    def test_format_scene_locations(self):
        locations = [{"id": "loc-1", "name": "Tavern", "identity": "A cozy tavern"}]
        result = format_scene_locations(locations)
        assert "Tavern" in result
        assert "loc-1" in result

    def test_format_nearby_summary_empty(self):
        assert "No nearby entities" in format_nearby_summary([])

    def test_format_nearby_summary(self):
        nearby = [{"id": "char-2", "name": "Stranger", "status_summary": "Lurking"}]
        result = format_nearby_summary(nearby)
        assert "Stranger" in result
        assert "Lurking" in result
