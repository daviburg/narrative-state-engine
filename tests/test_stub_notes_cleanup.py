"""Tests for stub note cleanup (#152)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from semantic_extraction import _clear_stub_notes, _has_real_identity


# Real stub identity strings produced by semantic_extraction.py
_EVENT_STUB_IDENTITY = "Entity referenced in events (stub — auto-created from event data)."
_POST_BATCH_STUB_IDENTITY = "Entity referenced in 3 events (stub — auto-created post-batch)."


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

    def test_clears_orphan_sweep_note_spaced(self):
        entity = {"notes": "Auto-created by post-batch orphan sweep.", "identity": "Some identity"}
        assert _clear_stub_notes(entity) is True
        assert entity["notes"] == ""

    def test_clears_orphan_sweep_note_hyphenated(self):
        """The actual production note uses hyphens: 'post-batch-orphan-sweep'."""
        entity = {"notes": "Auto-created by post-batch-orphan-sweep.", "identity": "Some identity"}
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


class TestHasRealIdentity:
    """Tests for _has_real_identity() — distinguishes real vs stub identity."""

    def test_real_identity(self):
        assert _has_real_identity({"identity": "A powerful wizard of the north."}) is True

    def test_stub_identity_event(self):
        assert _has_real_identity({"identity": _EVENT_STUB_IDENTITY}) is False

    def test_stub_identity_post_batch(self):
        assert _has_real_identity({"identity": _POST_BATCH_STUB_IDENTITY}) is False

    def test_empty_identity(self):
        assert _has_real_identity({"identity": ""}) is False

    def test_none_identity(self):
        assert _has_real_identity({"identity": None}) is False

    def test_missing_identity(self):
        assert _has_real_identity({}) is False


class TestBackfillSweepClearsStubNotes:
    """Integration-style test: the sweep after backfill clears enriched entities."""

    def test_sweep_clears_enriched_entities(self):
        """Enriched entities (non-stub identity) should have stub notes cleared."""
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
                    "identity": _EVENT_STUB_IDENTITY,
                    "notes": "Auto-created by event-stub.",
                },
            ]
        }

        # Simulate the sweep logic from backfill_stubs (uses _has_real_identity)
        for entities in catalogs.values():
            for entity in entities:
                if _has_real_identity(entity) and _clear_stub_notes(entity):
                    pass  # would print in real code

        # Enriched entity should have notes cleared
        assert catalogs["characters.json"][0]["notes"] == ""
        # Stub entity (placeholder stub identity) should keep its notes
        assert catalogs["characters.json"][1]["notes"] == "Auto-created by event-stub."

    def test_stub_identity_entities_retain_stub_notes(self):
        """Entities with stub placeholder identity keep their notes."""
        entity = {
            "id": "char-stub",
            "name": "Mystery Figure",
            "identity": _EVENT_STUB_IDENTITY,
            "notes": "Auto-created by event-stub.",
        }
        assert _has_real_identity(entity) is False
        assert entity["notes"] == "Auto-created by event-stub."

    def test_post_batch_stub_retains_notes(self):
        """Post-batch stubs with hyphenated notes and stub identity keep their notes."""
        entity = {
            "id": "char-stub2",
            "name": "Another Mystery",
            "identity": _POST_BATCH_STUB_IDENTITY,
            "notes": "Auto-created by post-batch-orphan-sweep.",
        }
        assert _has_real_identity(entity) is False
        assert entity["notes"] == "Auto-created by post-batch-orphan-sweep."

    def test_empty_identity_entities_retain_stub_notes(self):
        """Entities with empty identity are stubs and keep their notes."""
        entity = {
            "id": "char-stub3",
            "name": "Yet Another Mystery",
            "identity": "",
            "notes": "Auto-created by event-stub.",
        }
        assert _has_real_identity(entity) is False
        assert entity["notes"] == "Auto-created by event-stub."
