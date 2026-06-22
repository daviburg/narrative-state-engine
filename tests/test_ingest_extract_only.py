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
        def _fake_extract(turn_id, speaker, text, session_dir, args):
            calls.append((turn_id, speaker, text, session_dir))
            return True
        monkeypatch.setattr(
            ingest_turn,
            "run_semantic_extraction",
            _fake_extract,
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


# ---------------------------------------------------------------------------
# --extract-only exit code is honest about extraction failure
# ---------------------------------------------------------------------------


import semantic_extraction  # noqa: E402


def _patch_extraction_internals(
    monkeypatch, *, llm_raises=False, merge_raises=False, turn_failed=False
):
    """Patch the module-level dependencies of extract_semantic_single so the
    REAL function runs end-to-end without any LLM/GPU or file side effects.

    The three failure modes mirror the production swallow sites:
    - llm_raises   -> (a) LLMClient unavailable.
    - merge_raises -> (b) extract_and_merge raises.
    - turn_failed  -> (c) extract_and_merge returns turn_failed=True.
    """
    if llm_raises:
        def _bad_client(*a, **k):
            raise semantic_extraction.LLMExtractionError("no LLM available")
        monkeypatch.setattr(semantic_extraction, "LLMClient", _bad_client)
    else:
        class _DummyClient:
            def __init__(self, *a, **k):
                self.config = {}

            def enable_raw_io_capture(self, *a, **k):
                pass
        monkeypatch.setattr(semantic_extraction, "LLMClient", _DummyClient)

    monkeypatch.setattr(semantic_extraction, "_reset_pc_failure_tracking", lambda: None)
    monkeypatch.setattr(semantic_extraction, "_raw_io_capture_enabled", lambda cfg: False)
    monkeypatch.setattr(semantic_extraction, "load_catalogs", lambda d: {})
    monkeypatch.setattr(semantic_extraction, "load_events", lambda d: [])
    monkeypatch.setattr(semantic_extraction, "load_timeline", lambda d: [])
    monkeypatch.setattr(semantic_extraction, "_ensure_player_character", lambda c, t: None)
    monkeypatch.setattr(semantic_extraction, "_write_extraction_log", lambda p, r: None)
    monkeypatch.setattr(semantic_extraction, "mark_dormant_relationships", lambda c, t: 0)
    monkeypatch.setattr(semantic_extraction, "save_catalogs", lambda d, c: None)
    monkeypatch.setattr(semantic_extraction, "save_events", lambda d, e: None)
    monkeypatch.setattr(semantic_extraction, "save_timeline", lambda d, t: None)

    if merge_raises:
        def _bad_merge(*a, **k):
            raise RuntimeError("merge boom")
        monkeypatch.setattr(semantic_extraction, "extract_and_merge", _bad_merge)
    else:
        def _ok_merge(turn, catalogs, events_list, llm, min_confidence, **k):
            return catalogs, events_list, turn_failed, {"turn_id": turn["turn_id"]}
        monkeypatch.setattr(semantic_extraction, "extract_and_merge", _ok_merge)


def _stub_dm_profile(monkeypatch):
    """Prevent DM-profile analysis from touching a real LLM."""
    stub = types.ModuleType("dm_profile_analyzer")
    stub.analyze_single_turn = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "dm_profile_analyzer", stub)


def _extract_only_argv(session, turn_file, tmp_path):
    return [
        "ingest_turn.py",
        "--session", str(session),
        "--speaker", "dm",
        "--file", str(turn_file),
        "--extract-only",
        "--framework", str(tmp_path / "framework-local"),
    ]


class TestExtractOnlyExitCode:
    """--extract-only must exit non-zero when extraction actually fails so a
    caller using subprocess check=True can detect it (companion to private
    advisor Fix A)."""

    def test_success_exits_zero(self, tmp_path, monkeypatch):
        session, turn_file, _md = _make_session(tmp_path)
        _patch_extraction_internals(monkeypatch)
        _stub_dm_profile(monkeypatch)
        monkeypatch.setattr(sys, "argv", _extract_only_argv(session, turn_file, tmp_path))
        # Returns normally (no SystemExit) on success.
        ingest_turn.main()

    def test_zero_entities_no_failure_exits_zero(self, tmp_path, monkeypatch):
        # A legitimately entity-free turn (empty catalogs, turn_failed=False)
        # must NOT be treated as a failure.
        session, turn_file, _md = _make_session(tmp_path)
        _patch_extraction_internals(monkeypatch, turn_failed=False)
        _stub_dm_profile(monkeypatch)
        monkeypatch.setattr(sys, "argv", _extract_only_argv(session, turn_file, tmp_path))
        ingest_turn.main()

    def test_llm_unavailable_exits_nonzero(self, tmp_path, monkeypatch):
        # Case (a): LLMClient init fails.
        session, turn_file, _md = _make_session(tmp_path)
        _patch_extraction_internals(monkeypatch, llm_raises=True)
        _stub_dm_profile(monkeypatch)
        monkeypatch.setattr(sys, "argv", _extract_only_argv(session, turn_file, tmp_path))
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1

    def test_merge_raises_exits_nonzero(self, tmp_path, monkeypatch):
        # Case (b): extract_and_merge raises.
        session, turn_file, _md = _make_session(tmp_path)
        _patch_extraction_internals(monkeypatch, merge_raises=True)
        _stub_dm_profile(monkeypatch)
        monkeypatch.setattr(sys, "argv", _extract_only_argv(session, turn_file, tmp_path))
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1

    def test_turn_failed_exits_nonzero(self, tmp_path, monkeypatch):
        # Case (c): per-phase failure (turn_failed=True) with NO exception.
        # This is the previously-undetectable common case.
        session, turn_file, _md = _make_session(tmp_path)
        _patch_extraction_internals(monkeypatch, turn_failed=True)
        _stub_dm_profile(monkeypatch)
        monkeypatch.setattr(sys, "argv", _extract_only_argv(session, turn_file, tmp_path))
        with pytest.raises(SystemExit) as exc:
            ingest_turn.main()
        assert exc.value.code == 1


class TestExtractSemanticSingleReturn:
    """Unit-level: extract_semantic_single returns an honest success bool."""

    def _run(self, tmp_path, monkeypatch, **kw):
        _patch_extraction_internals(monkeypatch, **kw)
        return semantic_extraction.extract_semantic_single(
            "turn-001", "dm", "The innkeeper looks up.",
            str(tmp_path / "session"),
            framework_dir=str(tmp_path / "framework-local"),
        )

    def test_returns_true_on_success(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch) is True

    def test_returns_false_when_llm_unavailable(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch, llm_raises=True) is False

    def test_returns_false_when_merge_raises(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch, merge_raises=True) is False

    def test_returns_false_when_turn_failed(self, tmp_path, monkeypatch):
        assert self._run(tmp_path, monkeypatch, turn_failed=True) is False


# ---------------------------------------------------------------------------
# extract_and_merge: which phase outcomes flip turn_failed (the exit-code
# source).  These exercise the REAL extract_and_merge (LLM stubbed, no GPU) and
# chain with TestExtractOnlyExitCode above: a phase that flips turn_failed=True
# propagates to a non-zero --extract-only exit, while an intentional best-effort
# drop keeps turn_failed=False (exit 0) yet stays auditable in the log.
# ---------------------------------------------------------------------------


def _phase_stub_llm(*, discovery_entities=None, detail_for=None):
    """Stub LLM driving the real extract_and_merge.

    discovery_entities: list of discovery proposal dicts (default empty).
    detail_for: optional ``{entity_marker: entity_dict}`` mapping; when a
        detail user-prompt mentions ``entity_marker`` the mapped entity is
        returned (used to feed a schema-invalid detail for a discovered
        entity).  The always-on char-player detail returns a valid PC.
    """
    from unittest.mock import MagicMock

    discovery_entities = discovery_entities or []
    detail_for = detail_for or {}
    llm = MagicMock()
    llm.default_timeout = 10
    llm.pc_max_tokens = 4096
    llm.delay = MagicMock()
    llm.config = {"checkpoint_interval": 100}

    _good_pc = {
        "id": "char-player",
        "name": "Player Character",
        "type": "character",
        "identity": "The player character.",
        "first_seen_turn": "turn-001",
        "last_updated_turn": "turn-001",
    }

    def _extract_json(system_prompt, user_prompt, timeout=None, max_tokens=None,
                      schema=None, temperature=None, capture=None):
        sp = system_prompt.lower()
        if "discover" in sp or "discovery" in sp:
            return {"entities": list(discovery_entities)}
        if "detail" in sp:
            for marker, entity in detail_for.items():
                if marker in user_prompt:
                    return {"entity": entity}
            return {"entity": _good_pc}
        if "relationship" in sp:
            return {"relationships": []}
        if "event" in sp:
            return {"events": []}
        return {}

    llm.extract_json = MagicMock(side_effect=_extract_json)
    return llm


class TestPhaseOutcomeDrivesExitCode:
    """extract_and_merge sets turn_failed for phase ERRORS (exceptions /
    unrecoverable phase failures) but NOT for intentional best-effort drops."""

    def _fresh_catalogs(self):
        return {fn: [] for fn in semantic_extraction.CATALOG_KEYS}

    def test_temporal_exception_sets_turn_failed(self, monkeypatch):
        """A RAISED temporal-extraction error -> turn_failed=True (so
        --extract-only exits non-zero and the turn is re-extractable)."""
        monkeypatch.setattr(semantic_extraction, "load_template",
                            lambda name: f"{name} template")
        semantic_extraction._reset_pc_failure_tracking()

        def _boom(*a, **k):
            raise RuntimeError("temporal boom")
        monkeypatch.setattr(semantic_extraction, "extract_temporal_signals", _boom)

        llm = _phase_stub_llm()
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Time passes."}
        _c, _e, failed, log = semantic_extraction.extract_and_merge(
            turn, self._fresh_catalogs(), [], llm, min_confidence=0.6, timeline=[],
        )
        assert failed is True
        assert log["temporal_ok"] is False
        assert "temporal boom" in (log["temporal_error"] or "")

    def test_temporal_no_signals_is_success(self, monkeypatch):
        """Legitimate 'no temporal signals' is NOT a failure (exit 0)."""
        monkeypatch.setattr(semantic_extraction, "load_template",
                            lambda name: f"{name} template")
        semantic_extraction._reset_pc_failure_tracking()
        monkeypatch.setattr(semantic_extraction, "extract_temporal_signals",
                            lambda *a, **k: [])

        llm = _phase_stub_llm()
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "Nothing temporal."}
        _c, _e, failed, log = semantic_extraction.extract_and_merge(
            turn, self._fresh_catalogs(), [], llm, min_confidence=0.6, timeline=[],
        )
        assert failed is False
        assert log["temporal_ok"] is True

    def test_zero_entity_turn_is_success(self, monkeypatch):
        """An empty/sparse turn (no discoveries, no signals) exits 0 — no false
        stall."""
        monkeypatch.setattr(semantic_extraction, "load_template",
                            lambda name: f"{name} template")
        semantic_extraction._reset_pc_failure_tracking()
        llm = _phase_stub_llm(discovery_entities=[])
        turn = {"turn_id": "turn-001", "speaker": "dm", "text": "A quiet moment."}
        _c, _e, failed, _log = semantic_extraction.extract_and_merge(
            turn, self._fresh_catalogs(), [], llm, min_confidence=0.6,
        )
        assert failed is False

    def test_entity_detail_validation_drop_is_best_effort(self, monkeypatch):
        """CONTRACT: an unrepairable entity_detail validation failure for a
        DISCOVERED entity is an intentional best-effort drop — turn_failed stays
        False (exit 0) so the deterministic extractor does not stall — but the
        drop is RECORDED in the extraction log's validation_failures (NOT
        silent)."""
        monkeypatch.setattr(semantic_extraction, "load_template",
                            lambda name: f"{name} template")
        semantic_extraction._reset_pc_failure_tracking()
        semantic_extraction._drain_validation_failures()  # clean buffer

        # identity must be a string; 123 is an unrepairable schema violation.
        bad_detail = {
            "id": "char-elder",
            "name": "Elder",
            "type": "character",
            "identity": 123,
            "first_seen_turn": "turn-001",
            "last_updated_turn": "turn-001",
        }
        discovery = [{
            "name": "Elder", "is_new": True, "proposed_id": "char-elder",
            "type": "character", "confidence": 0.9, "source_turn": "turn-001",
        }]
        llm = _phase_stub_llm(
            discovery_entities=discovery,
            detail_for={"char-elder": bad_detail, "Elder": bad_detail},
        )
        turn = {"turn_id": "turn-001", "speaker": "dm",
                "text": "The elder watches the road."}
        _c, _e, failed, log = semantic_extraction.extract_and_merge(
            turn, self._fresh_catalogs(), [], llm, min_confidence=0.6,
        )
        # Best-effort: the malformed detail is dropped but the turn is NOT failed.
        assert failed is False
        # NOT silent: the drop is recorded in the per-turn log.
        failures = log.get("validation_failures", [])
        assert any(
            f.get("entity_id") == "char-elder" and f.get("phase") == "entity_detail"
            for f in failures
        ), f"validation drop not recorded: {failures!r}"


