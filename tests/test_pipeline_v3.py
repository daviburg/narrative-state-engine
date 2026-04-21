"""Integration tests for orphan entity feedback loop and PC extraction resilience."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import (
    _create_orphan_stubs,
    _pc_partial_merge,
    _format_prior_entity_context,
    _post_batch_orphan_sweep,
    format_detail_prompt,
    _extract_turn_number,
    _extract_themes,
)


# ---------------------------------------------------------------------------
# Orphan stub creation tests
# ---------------------------------------------------------------------------

class TestOrphanStubCreation:
    def test_creates_stub_for_orphan_id(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-player", "char-kael"], "turn_id": "turn-150"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-150")
        ids = {e["id"] for e in catalogs["characters.json"]}
        assert "char-kael" in ids
        stub = next(e for e in catalogs["characters.json"] if e["id"] == "char-kael")
        assert stub["name"] == "Kael"
        assert stub["type"] == "character"
        assert "Auto-created" in stub["notes"]

    def test_skips_char_player(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-player"], "turn_id": "turn-150"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-150")
        assert len(catalogs["characters.json"]) == 1

    def test_skips_generic_names(self):
        catalogs = {
            "characters.json": [],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-stranger", "char-figure"], "turn_id": "turn-100"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_skips_already_known(self):
        catalogs = {
            "characters.json": [
                {"id": "char-kael", "name": "Kael", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-kael"], "turn_id": "turn-200"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-200")
        assert len(catalogs["characters.json"]) == 1  # no duplicate


class TestLocationStub:
    def test_location_stub_goes_to_locations(self):
        catalogs = {
            "characters.json": [],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["loc-forest-camp"], "turn_id": "turn-050"},
        ]
        _create_orphan_stubs(catalogs, events, "turn-050")
        assert len(catalogs["locations.json"]) == 1
        stub = catalogs["locations.json"][0]
        assert stub["id"] == "loc-forest-camp"
        assert stub["type"] == "location"


# ---------------------------------------------------------------------------
# PC partial merge tests
# ---------------------------------------------------------------------------

class TestPCPartialMerge:
    def test_merges_current_status(self):
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "current_status": "Resting at camp.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }
        entity_data = {
            "id": "char-player",
            "current_status": "Preparing for battle.",
            # Missing required fields — would fail validation
        }
        _pc_partial_merge(catalogs, entity_data, "turn-100")
        pc = catalogs["characters.json"][0]
        assert pc["current_status"] == "Preparing for battle."
        assert pc["last_updated_turn"] == "turn-100"

    def test_merges_volatile_state(self):
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }
        entity_data = {
            "id": "char-player",
            "volatile_state": {"condition": "wounded", "equipment": ["sword", "shield"]},
        }
        _pc_partial_merge(catalogs, entity_data, "turn-075")
        pc = catalogs["characters.json"][0]
        assert pc["volatile_state"]["condition"] == "wounded"
        assert pc["last_updated_turn"] == "turn-075"

    def test_filters_disallowed_stable_attrs(self):
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }
        entity_data = {
            "id": "char-player",
            "stable_attributes": {
                "race": {"value": "Human", "inference": False},
                "backstory": {"value": "Born in a village", "inference": True},
            },
        }
        _pc_partial_merge(catalogs, entity_data, "turn-060")
        pc = catalogs["characters.json"][0]
        assert "race" in pc["stable_attributes"]
        assert "backstory" not in pc["stable_attributes"]

    def test_no_merge_when_no_valid_fields(self):
        catalogs = {
            "characters.json": [
                {
                    "id": "char-player",
                    "name": "Player Character",
                    "type": "character",
                    "identity": "The player character.",
                    "first_seen_turn": "turn-001",
                    "last_updated_turn": "turn-050",
                }
            ]
        }
        entity_data = {"id": "char-player"}
        _pc_partial_merge(catalogs, entity_data, "turn-060")
        pc = catalogs["characters.json"][0]
        assert pc["last_updated_turn"] == "turn-050"  # unchanged


# ---------------------------------------------------------------------------
# PC context trimming tests
# ---------------------------------------------------------------------------

class TestPCContextTrimming:
    def test_trims_stable_attributes_for_pc(self):
        entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "current_status": "In camp.",
            "stable_attributes": {
                "race": {"value": "Human"},
                "class": {"value": "Ranger"},
                "backstory": {"value": "Born in a village"},
                "motivation": {"value": "Find the artifact"},
                "aliases": {"value": ["PC", "Hero"]},
            },
            "volatile_state": {"condition": "healthy"},
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-345",
        }
        result = json.loads(_format_prior_entity_context(entry))
        # Only key attrs should be present
        sa = result.get("stable_attributes", {})
        assert "race" in sa
        assert "class" in sa
        assert "aliases" in sa
        assert "backstory" not in sa
        assert "motivation" not in sa

    def test_does_not_trim_non_pc(self):
        entry = {
            "id": "char-kael",
            "name": "Kael",
            "type": "character",
            "identity": "A young hunter.",
            "stable_attributes": {
                "race": {"value": "Elf"},
                "backstory": {"value": "Forest-born"},
                "motivation": {"value": "Protect the grove"},
            },
            "first_seen_turn": "turn-050",
            "last_updated_turn": "turn-200",
        }
        result = json.loads(_format_prior_entity_context(entry))
        sa = result.get("stable_attributes", {})
        assert "backstory" in sa
        assert "motivation" in sa

    def test_trims_volatile_state_lists(self):
        entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character.",
            "volatile_state": {
                "equipment": ["sword", "shield", "bow", "arrows", "cloak"],
            },
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-345",
        }
        result = json.loads(_format_prior_entity_context(entry))
        vs = result.get("volatile_state", {})
        # List longer than 3 should be trimmed to last 3
        assert len(vs["equipment"]) == 3
        assert vs["equipment"] == ["bow", "arrows", "cloak"]

    def test_pc_context_size_reasonable(self):
        """PC context with large attributes should still be under 4K tokens (~16KB)."""
        large_entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The player character in an epic RPG campaign.",
            "current_status": "Standing at the edge of the forest, preparing for the final battle.",
            "stable_attributes": {
                "race": {"value": "Human", "inference": False, "source_turn": "turn-001"},
                "class": {"value": "Ranger", "inference": False, "source_turn": "turn-001"},
                "aliases": {"value": ["PC", "Hero", "The Chosen"], "inference": False},
                "backstory": {"value": "A long backstory " * 50},
                "motivation": {"value": "Motivation text " * 30},
                "personality": {"value": "Personality text " * 20},
                "equipment_notes": {"value": "Equipment " * 40},
                "quest_history": {"value": "Quest " * 60},
            },
            "volatile_state": {
                "condition": "healthy",
                "equipment": [f"item-{i}" for i in range(20)],
                "location": "forest-edge",
            },
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-345",
        }
        result = _format_prior_entity_context(large_entry)
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(result) / 4
        assert estimated_tokens < 4000, f"PC context too large: ~{estimated_tokens:.0f} tokens"


# ---------------------------------------------------------------------------
# Post-batch orphan sweep tests
# ---------------------------------------------------------------------------

class TestPostBatchOrphanSweep:
    def test_creates_stubs_for_frequent_orphans(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": f"evt-{i}", "related_entities": ["char-player", "char-kael"],
             "turn_id": f"turn-{150 + i}"}
            for i in range(5)
        ]
        count = _post_batch_orphan_sweep(catalogs, events)
        assert count == 1
        ids = {e["id"] for e in catalogs["characters.json"]}
        assert "char-kael" in ids

    def test_skips_infrequent_orphans(self):
        catalogs = {
            "characters.json": [
                {"id": "char-player", "name": "Player Character", "type": "character"}
            ],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }
        events = [
            {"id": "evt-1", "related_entities": ["char-player", "char-random"],
             "turn_id": "turn-100"},
            {"id": "evt-2", "related_entities": ["char-player", "char-random"],
             "turn_id": "turn-101"},
        ]
        count = _post_batch_orphan_sweep(catalogs, events)
        assert count == 0


# ---------------------------------------------------------------------------
# Pronoun entity filter tests (#116)
# ---------------------------------------------------------------------------

class TestPronounFilter:
    """Pronouns in _GENERIC_STEMS are rejected by stub creation."""

    def _empty_catalogs(self):
        return {
            "characters.json": [],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }

    def test_skips_pronoun_char_she(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["char-she"], "turn_id": "turn-100"}]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_skips_pronoun_char_he(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["char-he"], "turn_id": "turn-100"}]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_skips_pronoun_char_they(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["char-they"], "turn_id": "turn-100"}]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_skips_pronoun_char_it(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["char-it"], "turn_id": "turn-100"}]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_existing_generic_stems_still_work(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["char-stranger"], "turn_id": "turn-100"}]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 0

    def test_real_entity_still_created(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["char-kael"], "turn_id": "turn-100"}]
        _create_orphan_stubs(catalogs, events, "turn-100")
        assert len(catalogs["characters.json"]) == 1
        assert catalogs["characters.json"][0]["id"] == "char-kael"


# ---------------------------------------------------------------------------
# Concept-prefix in stub creation tests (#117)
# ---------------------------------------------------------------------------

class TestConceptPrefixStubFilter:
    """Concept-prefix entities are rejected by stub creation."""

    def _empty_catalogs(self):
        return {
            "characters.json": [],
            "locations.json": [],
            "factions.json": [],
            "items.json": [],
        }

    def test_skips_concept_midwinter_celebration(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["concept-midwinter-celebration"], "turn_id": "turn-200"}]
        _create_orphan_stubs(catalogs, events, "turn-200")
        all_ids = {e["id"] for cat in catalogs.values() for e in cat}
        assert "concept-midwinter-celebration" not in all_ids

    def test_skips_concept_anything(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["concept-honor"], "turn_id": "turn-200"}]
        _create_orphan_stubs(catalogs, events, "turn-200")
        all_ids = {e["id"] for cat in catalogs.values() for e in cat}
        assert "concept-honor" not in all_ids

    def test_non_concept_still_creates_stub(self):
        catalogs = self._empty_catalogs()
        events = [{"id": "evt-1", "related_entities": ["item-magic-sword"], "turn_id": "turn-200"}]
        _create_orphan_stubs(catalogs, events, "turn-200")
        all_ids = {e["id"] for cat in catalogs.values() for e in cat}
        assert "item-magic-sword" in all_ids


# ---------------------------------------------------------------------------
# PC detail prompt double-context tests (#119)
# ---------------------------------------------------------------------------

class TestPCDetailPromptContext:
    """format_detail_prompt omits full entry_json for char-player."""

    def _make_turn(self):
        return {"turn_id": "turn-100", "speaker": "DM", "text": "The forest grows dark."}

    def _make_entry(self, entity_id="char-player"):
        return {
            "id": entity_id,
            "name": "Player Character" if entity_id == "char-player" else "Kael",
            "type": "character",
            "identity": "A brave adventurer.",
            "current_status": "Exploring the forest.",
            "stable_attributes": {"race": {"value": "Human"}},
            "volatile_state": {"condition": "healthy"},
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-099",
        }

    def test_pc_prompt_omits_current_catalog_entry(self):
        turn = self._make_turn()
        ref = {"name": "Player Character", "type": "character", "existing_id": "char-player"}
        entry = self._make_entry("char-player")
        prompt = format_detail_prompt(turn, ref, entry)
        assert "## Current Catalog Entry" not in prompt

    def test_non_pc_prompt_includes_current_catalog_entry(self):
        turn = self._make_turn()
        ref = {"name": "Kael", "type": "character", "existing_id": "char-kael"}
        entry = self._make_entry("char-kael")
        prompt = format_detail_prompt(turn, ref, entry)
        assert "## Current Catalog Entry" in prompt

    def test_pc_prompt_shorter_than_non_pc(self):
        turn = self._make_turn()
        pc_ref = {"name": "Player Character", "type": "character", "existing_id": "char-player"}
        pc_entry = self._make_entry("char-player")
        pc_prompt = format_detail_prompt(turn, pc_ref, pc_entry)

        npc_ref = {"name": "Player Character", "type": "character", "existing_id": "char-npc"}
        npc_entry = self._make_entry("char-player")
        npc_entry["id"] = "char-npc"
        npc_prompt = format_detail_prompt(turn, npc_ref, npc_entry)

        assert len(pc_prompt) < len(npc_prompt)


# ---------------------------------------------------------------------------
# num_ctx / context_length support tests (#118)
# ---------------------------------------------------------------------------

class TestContextLengthPassthrough:
    """LLMClient passes extra_body with num_ctx when context_length is set."""

    def _make_client(self, tmp_path, config):
        """Create an LLMClient with a mocked openai module."""
        import sys
        from unittest.mock import MagicMock
        from types import ModuleType

        config_path = tmp_path / "llm.json"
        config_path.write_text(json.dumps(config))

        # Inject a fake 'openai' module if the real one isn't installed
        mock_openai = ModuleType("openai")
        mock_openai_cls = MagicMock()
        mock_openai.OpenAI = mock_openai_cls
        orig = sys.modules.get("openai")
        sys.modules["openai"] = mock_openai

        # Force llm_client to re-import with the fake module
        if "llm_client" in sys.modules:
            del sys.modules["llm_client"]

        try:
            from llm_client import LLMClient

            mock_client_instance = MagicMock()
            mock_openai_cls.return_value = mock_client_instance

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = '{"result": "ok"}'
            mock_client_instance.chat.completions.create.return_value = mock_response

            client = LLMClient(str(config_path))
            return client, mock_client_instance
        finally:
            # Restore original module state
            if orig is not None:
                sys.modules["openai"] = orig
            else:
                sys.modules.pop("openai", None)
            sys.modules.pop("llm_client", None)

    def test_extract_json_includes_num_ctx(self, tmp_path):
        config = {
            "provider": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "test-model",
            "api_key_env": "",
            "temperature": 0.0,
            "max_tokens": 100,
            "timeout_seconds": 10,
            "retry_attempts": 1,
            "batch_delay_ms": 0,
            "context_length": 32768,
        }
        client, mock_inner = self._make_client(tmp_path, config)
        client.extract_json("system", "user")
        call_kwargs = mock_inner.chat.completions.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == {"num_ctx": 32768}

    def test_extract_json_no_extra_body_when_unset(self, tmp_path):
        config = {
            "provider": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "test-model",
            "api_key_env": "",
            "temperature": 0.0,
            "max_tokens": 100,
            "timeout_seconds": 10,
            "retry_attempts": 1,
            "batch_delay_ms": 0,
        }
        client, mock_inner = self._make_client(tmp_path, config)
        client.extract_json("system", "user")
        call_kwargs = mock_inner.chat.completions.create.call_args[1]
        assert "extra_body" not in call_kwargs

    def test_generate_text_includes_num_ctx(self, tmp_path):
        config = {
            "provider": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "test-model",
            "api_key_env": "",
            "temperature": 0.0,
            "max_tokens": 100,
            "timeout_seconds": 10,
            "retry_attempts": 1,
            "batch_delay_ms": 0,
            "context_length": 16384,
        }
        client, mock_inner = self._make_client(tmp_path, config)
        mock_inner.chat.completions.create.return_value.choices[0].message.content = "Hello world"
        client.generate_text("system", "user")
        call_kwargs = mock_inner.chat.completions.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == {"num_ctx": 16384}


# ---------------------------------------------------------------------------
# Relationship arc compaction tests (#120)
# ---------------------------------------------------------------------------

class TestRelationshipArcCompaction:
    """_compact_relationships_with_arcs replaces history with arc summaries."""

    def test_compacts_with_arc_data(self):
        from semantic_extraction import _compact_relationships_with_arcs

        relationships = [
            {
                "target_id": "char-kael",
                "type": "ally",
                "status": "active",
                "history": [
                    {"turn": "turn-010", "detail": "Met in forest"},
                    {"turn": "turn-050", "detail": "Fought together"},
                    {"turn": "turn-100", "detail": "Disagreement"},
                    {"turn": "turn-150", "detail": "Reconciled"},
                ],
            },
            {
                "target_id": "char-lyra",
                "type": "friend",
                "status": "active",
                "history": [
                    {"turn": "turn-020", "detail": "Traded goods"},
                ],
            },
        ]
        arcs_data = {
            "arcs": {
                "char-kael": {
                    "arc_summary": [
                        {"phase": "alliance"},
                        {"phase": "conflict"},
                        {"phase": "reconciliation"},
                    ],
                    "current_relationship": "trusted ally",
                },
            },
        }
        result = _compact_relationships_with_arcs(relationships, arcs_data)
        assert len(result) == 2
        # char-kael should be compacted
        kael = result[0]
        assert kael["target_id"] == "char-kael"
        assert "history" not in kael
        assert kael["arc_phases"] == 3
        assert kael["current"] == "trusted ally"
        assert "alliance" in kael["summary"]
        # char-lyra has no arc data — history trimmed to last 3
        lyra = result[1]
        assert lyra["target_id"] == "char-lyra"
        assert len(lyra["history"]) == 1  # only 1 entry, all kept

    def test_trims_history_without_arcs(self):
        from semantic_extraction import _compact_relationships_with_arcs

        relationships = [
            {
                "target_id": "char-unknown",
                "type": "rival",
                "status": "active",
                "history": [{"turn": f"turn-{i:03d}", "detail": f"event {i}"} for i in range(10)],
            },
        ]
        arcs_data = {"arcs": {}}
        result = _compact_relationships_with_arcs(relationships, arcs_data)
        assert len(result[0]["history"]) == 3

    def test_fallback_when_no_arcs_data(self):
        """_format_prior_entity_context falls back when arcs_data is None."""
        entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "The PC.",
            "relationships": [
                {"target_id": "char-kael", "type": "ally", "history": [{"turn": "turn-001"}]},
            ],
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-100",
        }
        result = json.loads(_format_prior_entity_context(entry, arcs_data=None))
        # Should still have relationships (trimmed, not compacted)
        rels = result.get("relationships", [])
        assert len(rels) == 1
        assert rels[0]["target_id"] == "char-kael"

    def test_token_savings_with_arcs(self):
        """Arc compaction should produce a shorter context than raw history."""
        relationships = [
            {
                "target_id": f"char-npc-{i}",
                "type": "ally",
                "status": "active",
                "history": [{"turn": f"turn-{j:03d}", "detail": f"interaction {j}"}
                            for j in range(10)],
            }
            for i in range(20)
        ]
        arcs_data = {
            "arcs": {
                f"char-npc-{i}": {
                    "arc_summary": [{"phase": "meeting"}, {"phase": "bonding"}],
                    "current_relationship": "friend",
                }
                for i in range(20)
            }
        }
        entry_with_arcs = {
            "id": "char-player", "name": "PC", "type": "character",
            "identity": "The PC.", "relationships": relationships,
            "first_seen_turn": "turn-001", "last_updated_turn": "turn-200",
        }
        with_arcs = _format_prior_entity_context(entry_with_arcs, arcs_data=arcs_data)
        without_arcs = _format_prior_entity_context(entry_with_arcs, arcs_data=None)
        assert len(with_arcs) < len(without_arcs)


# ---------------------------------------------------------------------------
# Volatile state digest tests (#121)
# ---------------------------------------------------------------------------

class TestVolatileStateDigest:
    """_build_volatile_digest compresses old entries."""

    def test_digests_old_entries(self):
        from semantic_extraction import _build_volatile_digest

        volatile = {
            "observations": [
                {"turn": "turn-010", "detail": "pregnancy signs noticed"},
                {"turn": "turn-020", "detail": "harvest preparations"},
                {"turn": "turn-030", "detail": "ritual performed"},
                {"turn": "turn-180", "detail": "council meeting held"},
                {"turn": "turn-190", "detail": "defense planned"},
            ],
        }
        result = _build_volatile_digest(volatile, current_turn_num=200)
        obs = result["observations"]
        # Old entries (turn-010..030) digested, recent (180, 190) kept
        assert isinstance(obs[0], str)
        assert "3 earlier entries" in obs[0]
        assert len(obs) == 3  # 1 summary + 2 recent

    def test_empty_volatile_state(self):
        from semantic_extraction import _build_volatile_digest

        assert _build_volatile_digest({}, 200) == {}

    def test_non_list_values_pass_through(self):
        from semantic_extraction import _build_volatile_digest

        volatile = {"condition": "healthy", "location": "forest"}
        result = _build_volatile_digest(volatile, 200)
        assert result["condition"] == "healthy"
        assert result["location"] == "forest"

    def test_all_recent_entries_no_digest(self):
        from semantic_extraction import _build_volatile_digest

        volatile = {
            "notes": [
                {"turn": "turn-180", "detail": "something"},
                {"turn": "turn-190", "detail": "something else"},
            ],
        }
        result = _build_volatile_digest(volatile, current_turn_num=200)
        assert len(result["notes"]) == 2
        assert isinstance(result["notes"][0], dict)

    def test_digest_includes_themes(self):
        from semantic_extraction import _build_volatile_digest

        volatile = {
            "events": [
                {"turn": "turn-010", "detail": "pregnancy confirmed"},
                {"turn": "turn-020", "detail": "construction of walls began"},
                {"turn": "turn-030", "detail": "healing ritual success"},
            ],
        }
        result = _build_volatile_digest(volatile, current_turn_num=200)
        summary = result["events"][0]
        assert "pregnancy" in summary or "construction" in summary or "healing" in summary

    def test_none_volatile_returns_none(self):
        from semantic_extraction import _build_volatile_digest

        assert _build_volatile_digest(None, 200) is None


class TestExtractTurnNumber:
    """_extract_turn_number handles various formats."""

    def test_dict_with_turn_key(self):
        from semantic_extraction import _extract_turn_number

        assert _extract_turn_number({"turn": "turn-042"}) == 42

    def test_dict_with_source_turn(self):
        from semantic_extraction import _extract_turn_number

        assert _extract_turn_number({"source_turn": "turn-100"}) == 100

    def test_string_with_turn_pattern(self):
        from semantic_extraction import _extract_turn_number

        assert _extract_turn_number("something at turn-055 happened") == 55

    def test_no_turn_info(self):
        from semantic_extraction import _extract_turn_number

        assert _extract_turn_number({"detail": "no turn"}) is None

    def test_integer_value(self):
        from semantic_extraction import _extract_turn_number

        assert _extract_turn_number(42) is None


class TestExtractThemes:
    """_extract_themes identifies keyword themes from entries."""

    def test_finds_keywords(self):
        from semantic_extraction import _extract_themes

        items = [
            {"detail": "pregnancy confirmed"},
            {"detail": "harvest completed"},
        ]
        themes = _extract_themes(items)
        assert "pregnancy" in themes
        assert "harvest" in themes

    def test_caps_at_five(self):
        from semantic_extraction import _extract_themes

        items = [
            "pregnancy text", "birth text", "construction text",
            "harvest text", "defense text", "ritual text",
        ]
        themes = _extract_themes(items)
        assert len(themes) <= 5

    def test_fallback_count(self):
        from semantic_extraction import _extract_themes

        items = [{"detail": "unrecognized content"}]
        themes = _extract_themes(items)
        assert any("1 observations" in t for t in themes)


# ---------------------------------------------------------------------------
# Integration: PC prompt with all compacting features active (#118, #120, #121)
# ---------------------------------------------------------------------------

class TestPCCompactingIntegration:
    """format_detail_prompt for char-player is shorter with all compacting."""

    def test_compact_prompt_shorter(self):
        turn = {"turn_id": "turn-300", "speaker": "DM", "text": "The storm rages on."}
        pc_ref = {"name": "Player Character", "type": "character",
                  "existing_id": "char-player", "is_new": False}

        # Large PC entry with relationships and volatile state
        relationships = [
            {
                "target_id": f"char-npc-{i}",
                "type": "ally",
                "status": "active",
                "history": [{"turn": f"turn-{j:03d}", "detail": f"met {j}"}
                            for j in range(1, 11)],
            }
            for i in range(15)
        ]
        volatile = {
            "observations": [
                {"turn": f"turn-{t:03d}", "detail": f"harvest event at turn {t}"}
                for t in range(10, 300, 5)
            ],
            "condition": "healthy",
        }
        pc_entry = {
            "id": "char-player",
            "name": "Player Character",
            "type": "character",
            "identity": "A brave adventurer in the northern wastes.",
            "current_status": "Weathering the storm.",
            "stable_attributes": {
                "race": {"value": "Human"},
                "class": {"value": "Ranger"},
                "aliases": {"value": ["PC"]},
                "backstory": {"value": "Long backstory " * 20},
            },
            "volatile_state": volatile,
            "relationships": relationships,
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-299",
        }
        arcs_data = {
            "arcs": {
                f"char-npc-{i}": {
                    "arc_summary": [{"phase": "meeting"}, {"phase": "trust"}],
                    "current_relationship": "ally",
                }
                for i in range(15)
            }
        }

        prompt_compact = format_detail_prompt(turn, pc_ref, pc_entry, arcs_data=arcs_data)
        prompt_raw = format_detail_prompt(turn, pc_ref, pc_entry, arcs_data=None)

        assert len(prompt_compact) < len(prompt_raw)
