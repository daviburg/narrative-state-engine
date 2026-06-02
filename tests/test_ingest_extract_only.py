"""Tests for the --extract-only re-extraction mode of ingest_turn.py (#71).

Covers:
- --extract-only runs semantic extraction against an existing turn file
  WITHOUT creating a new turn file or modifying the raw transcript (Rule 1).
- Clear errors when the target turn file is missing, has a bad name, or
  --file is omitted.
- Regression: the normal new-turn ingest flow still creates the turn file
  and appends to the raw transcript.
- Pure helpers parse_turn_filename() and strip_turn_header().

All extraction is mocked — no real LLM/GPU is invoked.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import ingest_turn


def _make_session(tmp_path):
    """Create a minimal session dir with one existing DM turn."""
    session = tmp_path / "session-test"
    transcript = session / "transcript"
    raw = session / "raw"
    transcript.mkdir(parents=True)
    raw.mkdir(parents=True)
    turn_file = transcript / "turn-001-dm.md"
    turn_file.write_text(
        "# turn-001 — DM\n\nThe innkeeper looks up as you enter.\n",
        encoding="utf-8",
    )
    transcript_md = raw / "full-transcript.md"
    transcript_md.write_text(
        "\n---\n\n## turn-001 [dm]\n\nThe innkeeper looks up as you enter.\n",
        encoding="utf-8",
    )
    return session, turn_file, transcript_md


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_turn_filename_dm(self):
        assert ingest_turn.parse_turn_filename(
            "sessions/s/transcript/turn-022-dm.md"
        ) == ("turn-022", "dm")

    def test_parse_turn_filename_player(self):
        assert ingest_turn.parse_turn_filename("turn-007-player.md") == (
            "turn-007",
            "player",
        )

    def test_parse_turn_filename_rejects_non_turn(self):
        assert ingest_turn.parse_turn_filename("notes.md") is None

    def test_strip_turn_header_removes_header(self):
        text = "# turn-001 — DM\n\nThe innkeeper looks up.\n"
        assert ingest_turn.strip_turn_header(text) == "The innkeeper looks up."

    def test_strip_turn_header_noop_without_header(self):
        assert ingest_turn.strip_turn_header("Just narrative text.") == (
            "Just narrative text."
        )


# ---------------------------------------------------------------------------
# --extract-only behaviour
# ---------------------------------------------------------------------------


class TestExtractOnly:
    def test_runs_extraction_without_touching_files(self, tmp_path, monkeypatch):
        session, turn_file, transcript_md = _make_session(tmp_path)
        before_raw = transcript_md.read_text(encoding="utf-8")
        before_files = sorted(os.listdir(session / "transcript"))

        calls = []
        monkeypatch.setattr(
            ingest_turn,
            "run_semantic_extraction",
            lambda turn_id, speaker, text, session_dir, args: calls.append(
                (turn_id, speaker, text, session_dir)
            ),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--file", str(turn_file),
                "--extract-only",
                "--framework", str(tmp_path / "framework-local"),
            ],
        )

        ingest_turn.main()

        # Extraction ran against the existing turn, header stripped.
        assert len(calls) == 1
        turn_id, speaker, text, session_dir = calls[0]
        assert turn_id == "turn-001"
        assert speaker == "dm"
        assert text == "The innkeeper looks up as you enter."
        assert session_dir == str(session)

        # Rule 1: raw transcript and transcript dir are untouched.
        assert transcript_md.read_text(encoding="utf-8") == before_raw
        assert sorted(os.listdir(session / "transcript")) == before_files

    def test_missing_file_errors(self, tmp_path, monkeypatch):
        session, _turn_file, _transcript_md = _make_session(tmp_path)
        monkeypatch.setattr(
            ingest_turn, "run_semantic_extraction",
            lambda *a, **k: pytest.fail("extraction should not run"),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--file", str(session / "transcript" / "turn-999-dm.md"),
                "--extract-only",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1

    def test_bad_filename_errors(self, tmp_path, monkeypatch):
        session, _turn_file, _transcript_md = _make_session(tmp_path)
        bad = session / "transcript" / "notes.md"
        bad.write_text("not a turn", encoding="utf-8")
        monkeypatch.setattr(
            ingest_turn, "run_semantic_extraction",
            lambda *a, **k: pytest.fail("extraction should not run"),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--file", str(bad),
                "--extract-only",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1

    def test_requires_file_not_text(self, tmp_path, monkeypatch):
        session, _turn_file, _transcript_md = _make_session(tmp_path)
        monkeypatch.setattr(
            ingest_turn, "run_semantic_extraction",
            lambda *a, **k: pytest.fail("extraction should not run"),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--text", "inline text",
                "--extract-only",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1

    def test_conflicting_extract_flag_errors(self, tmp_path, monkeypatch):
        session, turn_file, _transcript_md = _make_session(tmp_path)
        monkeypatch.setattr(
            ingest_turn, "run_semantic_extraction",
            lambda *a, **k: pytest.fail("extraction should not run"),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--file", str(turn_file),
                "--extract-only",
                "--extract",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1

    def test_file_outside_session_transcript_errors(self, tmp_path, monkeypatch):
        session, _turn_file, _transcript_md = _make_session(tmp_path)
        # A correctly-named turn file but belonging to a DIFFERENT session.
        other = tmp_path / "session-other" / "transcript"
        other.mkdir(parents=True)
        foreign = other / "turn-001-dm.md"
        foreign.write_text(
            "# turn-001 — DM\n\nForeign session content.\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            ingest_turn, "run_semantic_extraction",
            lambda *a, **k: pytest.fail("extraction should not run"),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--file", str(foreign),
                "--extract-only",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Regression: normal new-turn ingest still works
# ---------------------------------------------------------------------------


class TestNormalIngestRegression:
    def test_creates_turn_and_appends_transcript(self, tmp_path, monkeypatch):
        session = tmp_path / "session-new"
        session.mkdir()

        # Stub the structured-data extraction the normal flow imports.
        stub = types.ModuleType("extract_structured_data")
        stub.extract_and_merge_single_turn = lambda *a, **k: None
        monkeypatch.setitem(sys.modules, "extract_structured_data", stub)

        monkeypatch.setattr(
            sys, "argv",
            [
                "ingest_turn.py",
                "--session", str(session),
                "--speaker", "dm",
                "--text", "A new turn appears.",
            ],
        )

        ingest_turn.main()

        turn_file = session / "transcript" / "turn-001-dm.md"
        assert turn_file.is_file()
        assert "A new turn appears." in turn_file.read_text(encoding="utf-8")

        raw = (session / "raw" / "full-transcript.md").read_text(encoding="utf-8")
        assert "turn-001 [dm]" in raw
        assert "A new turn appears." in raw
