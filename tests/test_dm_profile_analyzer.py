"""Tests for dm_profile_analyzer.py — DM behavioral profile population (#260)."""

import json
import os
import sys
import textwrap

import pytest

# Add tools/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from dm_profile_analyzer import (
    _aggregate_adversarial_level,
    _empty_profile,
    _format_analysis_prompt,
    _parse_turn_number,
    analyze_dm_turns,
    list_dm_turns,
    load_dm_profile,
    load_template,
    merge_observations,
    merge_user_input,
    parse_user_input,
    save_dm_profile,
)


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------


class TestParseTurnNumber:
    def test_basic(self):
        assert _parse_turn_number("turn-001") == 1
        assert _parse_turn_number("turn-050") == 50
        assert _parse_turn_number("turn-999") == 999

    def test_invalid(self):
        assert _parse_turn_number("invalid") == 0
        assert _parse_turn_number("") == 0


class TestEmptyProfile:
    def test_fields(self):
        p = _empty_profile()
        assert p["confidence"] == 0.0
        assert p["adversarial_level"] == "unknown"
        assert p["structure_patterns"] == []
        assert p["hint_patterns"] == []
        assert p["formatting_preferences"] == []
        assert "last_updated_turn" in p


# ---------------------------------------------------------------------------
# Unit tests — merge_observations
# ---------------------------------------------------------------------------

class TestMergeObservations:
    def test_empty_observations(self):
        profile = _empty_profile()
        result = merge_observations(profile, [])
        assert result["confidence"] == 0.0

    def test_tone_observation(self):
        profile = _empty_profile()
        obs = [
            {
                "field": "tone",
                "observation": "Dark and atmospheric",
                "evidence": "The shadows close in...",
                "confidence": 0.7,
                "source_turn": "turn-005",
            }
        ]
        result = merge_observations(profile, obs)
        assert result["tone"] == "Dark and atmospheric"
        assert result["last_updated_turn"] == "turn-005"

    def test_tone_picks_highest_confidence(self):
        profile = _empty_profile()
        obs = [
            {"field": "tone", "observation": "Low confidence tone", "evidence": "", "confidence": 0.3, "source_turn": "turn-001"},
            {"field": "tone", "observation": "High confidence tone", "evidence": "", "confidence": 0.8, "source_turn": "turn-005"},
        ]
        result = merge_observations(profile, obs)
        assert result["tone"] == "High confidence tone"

    def test_array_field_dedup(self):
        profile = _empty_profile()
        profile["structure_patterns"] = ["Existing pattern"]
        obs = [
            {"field": "structure_patterns", "observation": "Existing pattern", "evidence": "", "confidence": 0.5, "source_turn": "turn-003"},
            {"field": "structure_patterns", "observation": "New pattern", "evidence": "", "confidence": 0.6, "source_turn": "turn-005"},
        ]
        result = merge_observations(profile, obs)
        assert "Existing pattern" in result["structure_patterns"]
        assert "New pattern" in result["structure_patterns"]
        # No duplicates
        assert len(result["structure_patterns"]) == 2

    def test_hint_patterns_added(self):
        profile = _empty_profile()
        obs = [
            {"field": "hint_patterns", "observation": "Embeds clues in descriptions", "evidence": "...", "confidence": 0.6, "source_turn": "turn-010"},
        ]
        result = merge_observations(profile, obs)
        assert "Embeds clues in descriptions" in result["hint_patterns"]

    def test_formatting_preferences(self):
        profile = _empty_profile()
        obs = [
            {"field": "formatting_preferences", "observation": "Uses second-person narration", "evidence": "You see...", "confidence": 0.8, "source_turn": "turn-007"},
        ]
        result = merge_observations(profile, obs)
        assert "Uses second-person narration" in result["formatting_preferences"]

    def test_last_updated_turn_latest(self):
        profile = _empty_profile()
        obs = [
            {"field": "tone", "observation": "A", "evidence": "", "confidence": 0.5, "source_turn": "turn-003"},
            {"field": "tone", "observation": "B", "evidence": "", "confidence": 0.6, "source_turn": "turn-020"},
            {"field": "tone", "observation": "C", "evidence": "", "confidence": 0.4, "source_turn": "turn-010"},
        ]
        result = merge_observations(profile, obs)
        assert result["last_updated_turn"] == "turn-020"

    def test_confidence_does_not_regress(self):
        profile = _empty_profile()
        profile["confidence"] = 0.5
        # Single low-confidence observation shouldn't drop overall confidence
        obs = [
            {"field": "tone", "observation": "Terse", "evidence": "", "confidence": 0.2, "source_turn": "turn-001"},
        ]
        result = merge_observations(profile, obs)
        assert result["confidence"] >= 0.5

    def test_confidence_capped_at_09(self):
        profile = _empty_profile()
        obs = [
            {"field": "tone", "observation": "Rich", "evidence": "", "confidence": 0.95, "source_turn": f"turn-{i:03d}"}
            for i in range(1, 30)
        ] + [
            {"field": "structure_patterns", "observation": f"Pattern {i}", "evidence": "", "confidence": 0.9, "source_turn": f"turn-{i:03d}"}
            for i in range(1, 30)
        ]
        result = merge_observations(profile, obs)
        assert result["confidence"] <= 0.9

    def test_invalid_source_turn_not_used_as_last_updated(self):
        """LLM-returned source_turn that doesn't match schema pattern is ignored."""
        profile = _empty_profile()
        profile["last_updated_turn"] = "turn-005"
        obs = [
            {"field": "tone", "observation": "Dark", "evidence": "", "confidence": 0.6, "source_turn": "turn-1"},
        ]
        result = merge_observations(profile, obs)
        # Invalid "turn-1" should be rejected; last_updated_turn stays at turn-005
        assert result["last_updated_turn"] == "turn-005"

    def test_empty_source_turn_ignored(self):
        profile = _empty_profile()
        profile["last_updated_turn"] = "turn-010"
        obs = [
            {"field": "tone", "observation": "Dark", "evidence": "", "confidence": 0.6, "source_turn": ""},
        ]
        result = merge_observations(profile, obs)
        assert result["last_updated_turn"] == "turn-010"


# ---------------------------------------------------------------------------
# Unit tests — adversarial level aggregation
# ---------------------------------------------------------------------------

class TestAggregateAdversarialLevel:
    def test_low(self):
        obs = [
            {"observation": "Low adversariality; DM is forgiving", "confidence": 0.7},
        ]
        assert _aggregate_adversarial_level(obs, "unknown") == "low"

    def test_high(self):
        obs = [
            {"observation": "Highly adversarial; punishing consequences", "confidence": 0.8},
        ]
        assert _aggregate_adversarial_level(obs, "unknown") == "high"

    def test_moderate(self):
        obs = [
            {"observation": "Moderate challenge; balanced encounters", "confidence": 0.6},
        ]
        assert _aggregate_adversarial_level(obs, "unknown") == "moderate"

    def test_empty_returns_current(self):
        assert _aggregate_adversarial_level([], "low") == "low"
        assert _aggregate_adversarial_level([], "unknown") == "unknown"

    def test_weighted_consensus(self):
        obs = [
            {"observation": "Low difficulty, permissive DM", "confidence": 0.3},
            {"observation": "High challenge, strict rules", "confidence": 0.8},
        ]
        # High has more weight so should win
        assert _aggregate_adversarial_level(obs, "unknown") == "high"


# ---------------------------------------------------------------------------
# Unit tests — user input parsing
# ---------------------------------------------------------------------------

class TestParseUserInput:
    def test_basic_template(self, tmp_path):
        doc = tmp_path / "dm-input.md"
        doc.write_text(textwrap.dedent("""\
            # DM Profile — User-Provided Information

            ## Known DM Tendencies

            Loves social encounters and rarely kills PCs.

            ## Adversarial Level (Your Assessment)

            low

            ## Additional Notes

            <!-- No notes -->
        """), encoding="utf-8")

        sections = parse_user_input(str(doc))
        assert "Known DM Tendencies" in sections
        assert "Loves social encounters" in sections["Known DM Tendencies"]
        assert "Adversarial Level (Your Assessment)" in sections
        assert "low" in sections["Adversarial Level (Your Assessment)"]
        # "Additional Notes" should be empty (only comment)
        assert "Additional Notes" not in sections

    def test_empty_sections_filtered(self, tmp_path):
        doc = tmp_path / "empty.md"
        doc.write_text(textwrap.dedent("""\
            ## Section One

            <!-- only comments -->

            ## Section Two


        """), encoding="utf-8")
        sections = parse_user_input(str(doc))
        assert len(sections) == 0

    def test_missing_file(self):
        sections = parse_user_input("/nonexistent/file.md")
        assert sections == {}


class TestMergeUserInput:
    def test_adversarial_level_direct(self):
        profile = _empty_profile()
        sections = {"Adversarial Level (Your Assessment)": "moderate"}
        result = merge_user_input(profile, sections)
        assert result["adversarial_level"] == "moderate"

    def test_adversarial_level_keyword(self):
        profile = _empty_profile()
        sections = {"Adversarial Level (Your Assessment)": "Pretty high difficulty"}
        result = merge_user_input(profile, sections)
        assert result["adversarial_level"] == "high"

    def test_tone(self):
        profile = _empty_profile()
        sections = {"Tone and Content Preferences": "Gritty and dark"}
        result = merge_user_input(profile, sections)
        assert "Gritty and dark" in result["tone"]

    def test_tone_appended_to_existing(self):
        profile = _empty_profile()
        profile["tone"] = "Atmospheric"
        sections = {"Tone and Content Preferences": "Dark humor"}
        result = merge_user_input(profile, sections)
        assert "Atmospheric" in result["tone"]
        assert "Dark humor" in result["tone"]

    def test_known_tendencies(self):
        profile = _empty_profile()
        sections = {"Known DM Tendencies": "Rewards creative solutions"}
        result = merge_user_input(profile, sections)
        assert "Rewards creative solutions" in result["notes"]

    def test_hint_style(self):
        profile = _empty_profile()
        sections = {"Hint and Clue Style": "Subtle environmental clues"}
        result = merge_user_input(profile, sections)
        assert any("Subtle environmental clues" in h for h in result["hint_patterns"])

    def test_house_rules(self):
        profile = _empty_profile()
        sections = {"House Rules": "Critical hits are doubled damage"}
        result = merge_user_input(profile, sections)
        assert "Critical hits are doubled damage" in result["notes"]

    def test_confidence_bumped(self):
        profile = _empty_profile()
        sections = {"Known DM Tendencies": "Some info"}
        result = merge_user_input(profile, sections)
        assert result["confidence"] >= 0.3

    def test_empty_input(self):
        profile = _empty_profile()
        result = merge_user_input(profile, {})
        assert result["confidence"] == 0.0

    def test_confidence_preserved_above_09(self):
        """User input must not regress confidence above 0.9 (user-confirmed)."""
        profile = _empty_profile()
        profile["confidence"] = 0.95
        sections = {"Known DM Tendencies": "Some info"}
        result = merge_user_input(profile, sections)
        assert result["confidence"] == 0.95


# ---------------------------------------------------------------------------
# Unit tests — profile load/save
# ---------------------------------------------------------------------------

class TestProfileIO:
    def test_save_and_load(self, tmp_path):
        profile_path = str(tmp_path / "dm-profile" / "dm-profile.json")
        profile = _empty_profile()
        profile["tone"] = "Dark"
        profile["confidence"] = 0.5

        save_dm_profile(profile, profile_path)
        loaded = load_dm_profile(profile_path)
        assert loaded["tone"] == "Dark"
        assert loaded["confidence"] == 0.5

    def test_load_missing_returns_empty(self, tmp_path):
        profile = load_dm_profile(str(tmp_path / "nonexistent.json"))
        assert profile["confidence"] == 0.0

    def test_dry_run_does_not_write(self, tmp_path):
        profile_path = str(tmp_path / "dm-profile.json")
        save_dm_profile(_empty_profile(), profile_path, dry_run=True)
        assert not os.path.exists(profile_path)


# ---------------------------------------------------------------------------
# Unit tests — format_analysis_prompt shape
# ---------------------------------------------------------------------------

class TestFormatAnalysisPrompt:
    def test_includes_turns(self):
        turns = [
            {"turn_id": "turn-001", "speaker": "dm", "text": "The door creaks open."},
            {"turn_id": "turn-003", "speaker": "dm", "text": "A shadow moves."},
        ]
        profile = _empty_profile()
        prompt = _format_analysis_prompt(turns, profile)
        assert "turn-001" in prompt
        assert "turn-003" in prompt
        assert "The door creaks open." in prompt
        assert "Current DM Profile" in prompt


# ---------------------------------------------------------------------------
# Unit tests — list_dm_turns
# ---------------------------------------------------------------------------

class TestListDMTurns:
    def test_lists_dm_turns(self, tmp_path):
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        (transcript_dir / "turn-001-dm.md").write_text("# turn-001 — DM\n\nHello", encoding="utf-8")
        (transcript_dir / "turn-002-player.md").write_text("# turn-002 — PLAYER\n\nHi", encoding="utf-8")
        (transcript_dir / "turn-003-dm.md").write_text("# turn-003 — DM\n\nWorld", encoding="utf-8")

        turns = list_dm_turns(str(tmp_path))
        assert len(turns) == 2
        assert turns[0]["turn_id"] == "turn-001"
        assert turns[1]["turn_id"] == "turn-003"
        assert all(t["speaker"] == "dm" for t in turns)

    def test_start_turn_filter(self, tmp_path):
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        for i in range(1, 6):
            (transcript_dir / f"turn-{i:03d}-dm.md").write_text(f"Turn {i}", encoding="utf-8")

        turns = list_dm_turns(str(tmp_path), start_turn=3)
        assert len(turns) == 3
        assert turns[0]["turn_id"] == "turn-003"

    def test_max_turns_limit(self, tmp_path):
        transcript_dir = tmp_path / "transcript"
        transcript_dir.mkdir()
        for i in range(1, 11):
            (transcript_dir / f"turn-{i:03d}-dm.md").write_text(f"Turn {i}", encoding="utf-8")

        turns = list_dm_turns(str(tmp_path), max_turns=3)
        assert len(turns) == 3

    def test_missing_transcript_dir(self, tmp_path):
        turns = list_dm_turns(str(tmp_path / "nonexistent"))
        assert turns == []


# ---------------------------------------------------------------------------
# Unit tests — template loading
# ---------------------------------------------------------------------------

class TestLoadTemplate:
    def test_template_loads(self):
        template = load_template()
        assert "DM behavior analyst" in template
        assert "observations" in template


# ---------------------------------------------------------------------------
# Integration tests — analyze_dm_turns with mock LLM
# ---------------------------------------------------------------------------

class MockLLMClient:
    """Mock LLM client that returns pre-canned observations."""

    def __init__(self, responses=None):
        self._responses = responses or []
        self._call_count = 0

    def extract_json(self, system_prompt, user_prompt, **kwargs):
        if self._call_count < len(self._responses):
            result = self._responses[self._call_count]
            self._call_count += 1
            return result
        return {"observations": []}

    def delay(self):
        pass


class TestAnalyzeDMTurns:
    def test_basic_analysis(self):
        mock_response = {
            "observations": [
                {
                    "field": "tone",
                    "observation": "Dark and atmospheric",
                    "evidence": "The shadows press in...",
                    "confidence": 0.6,
                    "source_turn": "turn-001",
                },
                {
                    "field": "adversarial_level",
                    "observation": "Moderate; fair warnings",
                    "evidence": "You hear a click...",
                    "confidence": 0.5,
                    "source_turn": "turn-001",
                },
            ]
        }
        llm = MockLLMClient(responses=[mock_response])
        turns = [{"turn_id": "turn-001", "speaker": "dm", "text": "The shadows press in..."}]
        profile = _empty_profile()

        observations = analyze_dm_turns(turns, profile, llm, batch_size=5)
        assert len(observations) == 2
        assert observations[0]["field"] == "tone"
        assert observations[1]["field"] == "adversarial_level"

    def test_invalid_field_filtered(self):
        mock_response = {
            "observations": [
                {"field": "tone", "observation": "Good", "evidence": "...", "confidence": 0.5, "source_turn": "turn-001"},
                {"field": "invalid_field", "observation": "Bad", "evidence": "...", "confidence": 0.5, "source_turn": "turn-001"},
            ]
        }
        llm = MockLLMClient(responses=[mock_response])
        turns = [{"turn_id": "turn-001", "speaker": "dm", "text": "Test"}]

        observations = analyze_dm_turns(turns, _empty_profile(), llm, batch_size=5)
        assert len(observations) == 1
        assert observations[0]["field"] == "tone"

    def test_non_dict_response_skipped(self):
        llm = MockLLMClient(responses=["not a dict"])
        turns = [{"turn_id": "turn-001", "speaker": "dm", "text": "Test"}]

        observations = analyze_dm_turns(turns, _empty_profile(), llm, batch_size=5)
        assert observations == []

    def test_batching(self):
        responses = [
            {"observations": [{"field": "tone", "observation": f"Batch {i}", "evidence": "", "confidence": 0.5, "source_turn": f"turn-{i:03d}"}]}
            for i in range(1, 4)
        ]
        llm = MockLLMClient(responses=responses)
        turns = [{"turn_id": f"turn-{i:03d}", "speaker": "dm", "text": f"Turn {i}"} for i in range(1, 8)]

        observations = analyze_dm_turns(turns, _empty_profile(), llm, batch_size=3)
        # 7 turns / batch_size=3 = 3 batches
        assert llm._call_count == 3
        assert len(observations) == 3

    def test_empty_observation_filtered(self):
        mock_response = {
            "observations": [
                {"field": "tone", "observation": "", "evidence": "...", "confidence": 0.5, "source_turn": "turn-001"},
                {"field": "tone", "observation": "Valid", "evidence": "...", "confidence": 0.5, "source_turn": "turn-001"},
            ]
        }
        llm = MockLLMClient(responses=[mock_response])
        turns = [{"turn_id": "turn-001", "speaker": "dm", "text": "Test"}]

        observations = analyze_dm_turns(turns, _empty_profile(), llm, batch_size=5)
        assert len(observations) == 1
        assert observations[0]["observation"] == "Valid"


# ---------------------------------------------------------------------------
# Integration test — full round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_analyze_merge_save_load(self, tmp_path):
        """Full pipeline: analyze turns → merge observations → save → load → verify."""
        profile_path = str(tmp_path / "dm-profile" / "dm-profile.json")

        mock_response = {
            "observations": [
                {"field": "tone", "observation": "Tense and atmospheric", "evidence": "The air grows thick...", "confidence": 0.7, "source_turn": "turn-010"},
                {"field": "structure_patterns", "observation": "2-3 paragraph responses", "evidence": "...", "confidence": 0.6, "source_turn": "turn-010"},
                {"field": "hint_patterns", "observation": "Embeds clues in descriptions", "evidence": "A glint of metal...", "confidence": 0.5, "source_turn": "turn-010"},
                {"field": "adversarial_level", "observation": "Moderate challenge", "evidence": "Fair warning before traps", "confidence": 0.6, "source_turn": "turn-010"},
                {"field": "formatting_preferences", "observation": "Second-person narration", "evidence": "You see...", "confidence": 0.8, "source_turn": "turn-010"},
            ]
        }

        llm = MockLLMClient(responses=[mock_response])
        turns = [{"turn_id": "turn-010", "speaker": "dm", "text": "The air grows thick as you approach."}]

        profile = _empty_profile()
        observations = analyze_dm_turns(turns, profile, llm, batch_size=5)
        assert len(observations) == 5

        profile = merge_observations(profile, observations)
        assert profile["tone"] == "Tense and atmospheric"
        assert profile["adversarial_level"] == "moderate"
        assert profile["confidence"] > 0.0
        assert profile["last_updated_turn"] == "turn-010"

        save_dm_profile(profile, profile_path)
        loaded = load_dm_profile(profile_path)
        assert loaded["tone"] == "Tense and atmospheric"
        assert loaded["confidence"] == profile["confidence"]


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestSchemaCompliance:
    def test_profile_validates_against_schema(self, tmp_path):
        """Merged profile must pass the DM profile JSON schema."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")

        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "dm-profile.schema.json"
        )
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        profile = _empty_profile()
        observations = [
            {"field": "tone", "observation": "Dark", "evidence": "", "confidence": 0.6, "source_turn": "turn-005"},
            {"field": "structure_patterns", "observation": "Long responses", "evidence": "", "confidence": 0.5, "source_turn": "turn-005"},
            {"field": "hint_patterns", "observation": "Embedded clues", "evidence": "", "confidence": 0.5, "source_turn": "turn-005"},
            {"field": "adversarial_level", "observation": "Moderate difficulty", "evidence": "", "confidence": 0.6, "source_turn": "turn-005"},
            {"field": "formatting_preferences", "observation": "Second-person", "evidence": "", "confidence": 0.7, "source_turn": "turn-005"},
        ]

        profile = merge_observations(profile, observations)
        jsonschema.validate(instance=profile, schema=schema)

    def test_empty_profile_validates(self):
        """The empty profile must also validate."""
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")

        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "schemas", "dm-profile.schema.json"
        )
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        profile = _empty_profile()
        jsonschema.validate(instance=profile, schema=schema)
