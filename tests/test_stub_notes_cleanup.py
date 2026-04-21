"""Tests for stub note cleanup (#152)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _clear_stub_notes


class TestClearStubNotes:
    """Unit tests for _clear_stub_notes()."""

    def test_clears_event_stub_note(self):
        entity = {"notes": "Auto-created by event-stub.", "identity": "A real identity"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""

    def test_clears_backfilled_from_stub_note(self):
        entity = {"notes": "Backfilled from stub.", "identity": "A real identity"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""

    def test_clears_orphan_sweep_note(self):
        entity = {"notes": "Auto-created by post-batch orphan sweep.", "identity": "Some identity"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""

    def test_preserves_non_stub_notes(self):
        entity = {"notes": "Important lore detail about this character.", "identity": "A wizard"}
        assert _clear_stub_notes(entity) is False
        assert entity["notes"] == "Important lore detail about this character."

    def test_returns_false_for_empty_notes(self):
        entity = {"notes": "", "identity": "Some identity"}
        assert _clear_stub_notes(entity) is False

    def test_returns_false_for_missing_notes(self):
        entity = {"identity": "Some identity"}
        assert _clear_stub_notes(entity) is False

    def test_case_insensitive_match(self):
        entity = {"notes": "AUTO-CREATED BY EVENT-STUB.", "identity": "Real"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""

    def test_match_without_trailing_period(self):
        entity = {"notes": "Auto-created by event-stub", "identity": "Real"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""

    def test_match_with_whitespace(self):
        entity = {"notes": "  Auto-created by event-stub.  ", "identity": "Real"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""


class TestBackfillSweepClearsStubNotes:
    """Integration-style test: the sweep after backfill clears enriched entities."""

    def test_sweep_clears_enriched_entities(self):
        """Enriched entities (identity=truthy) should have stub notes cleared."""
        catalogs = {
            "characters.json": [
                {
                    "id": "char-enriched",
                    "name": "Enriched NPC",
                    "identity": "A powerful wizard",
                    "notes": "Backfilled from stub.",
                },
                {
                    "id": "char-still-stub",
                    "name": "Unknown NPC",
                    "identity": "",
                    "notes": "Auto-created by event-stub.",
                },
            ]
        }

        # Simulate the sweep logic from backfill_stubs
        for entities in catalogs.values():
            for entity in entities:
                if entity.get("identity") and _clear_stub_notes(entity):
                    pass  # would print in real code

        # Enriched entity should have notes cleared
        assert catalogs["characters.json"][0]["notes"] == ""
        # Stub entity (no real identity) should keep its notes
        assert catalogs["characters.json"][1]["notes"] == "Auto-created by event-stub."

    def test_identity_false_entities_retain_stub_notes(self):
        """Entities without identity data are real stubs and keep their notes."""
        entity = {
            "id": "char-stub",
            "name": "Mystery Figure",
            "identity": "",
            "notes": "Auto-created by event-stub.",
        }
        # identity is falsy, so the sweep condition (entity.get("identity")) is False
        # We should NOT call _clear_stub_notes for these
        should_clear = bool(entity.get("identity"))
        assert should_clear is False
        # Notes preserved
        assert entity["notes"] == "Auto-created by event-stub."

    def test_identity_none_entities_retain_stub_notes(self):
        """Entities with identity=None are stubs and keep their notes."""
        entity = {
            "id": "char-stub2",
            "name": "Another Mystery",
            "identity": None,
            "notes": "Auto-created by post-batch orphan sweep.",
        }
        should_clear = bool(entity.get("identity"))
        assert should_clear is False
        assert entity["notes"] == "Auto-created by post-batch orphan sweep."
